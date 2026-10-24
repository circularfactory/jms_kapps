"""The screwing resource of use case 1: one attempt for each unscrewing operation, then the call
of the anomaly detector.

**It takes its work as the planner dispatches it.** The planner creates an unscrewing operation,
`queued`, which names one Screw and implements the screwing resource's `:UnscrewingCapability`, and
notifies the screwing resource, both with `mw.issue_operation()`. The middleware's intake reads the
operation once and runs the unscrewing workflow with it, one operation at a time. Nothing calls the
workflow over REST. A notification of any other operation is logged and left alone.

**One attempt is one commit of the middleware**, `mw.commit()`:

- **Entry:** the operation is committed `running`. If the store refuses that, nothing runs, and
  the operation stays as it was.
- **Body:** the attempt on the operation's Screw, and the write of its frame into the time-series
  store. It holds nothing of the graph. Only `transition.update(...)` sets the operation's
  time-series reference, for the exit.
- **Clean exit:** the reference, and nothing else. There is no exit transition, so the operation
  stays `running`: the anomaly detector ends it.
- **Error in the body:** the reference is dropped, and the operation is committed `failed`, with
  why as its `svc:hasExecutionResult`. The detector is not called.

The commit reads nothing: the intake has read the operation already.

**A Screw type without its seven values ends the operation before the attempt.** When the Screw
type of the operation's Screw holds one of the seven values (the six thresholds and the
preparation turning angle) zero times, more than once, or not as a finite number, the screw cannot
be unscrewed: in the real cell, the robot and the screwdriver need them. Then the screwing resource
makes no attempt, writes no frame and calls no detector. It commits the operation from `queued`
straight to `failed`, in one `mw.commit()`, with why as its `svc:hasExecutionResult`: it begins with
`NOT_ATTEMPTED` and names each missing or unusable value. It commits no `running` first, because
no work begins. The planner is notified of the end as of any other.

**Then it calls the anomaly detector's classify workflow** with the operation's IRI, found in the
graph by the detector's Classify capability (`classify_call`). That is a call inside the cell, and
the screwing resource acts on no result of it: the detector ends the operation. It keeps the
detector's answer, how the detector left the operation, for its dashboard only.

**A detector that is not reached** leaves nobody to end the operation: no live detector is found,
or the call fails or times out. Then the screwing resource ends the operation `failed` itself, in
one `mw.commit()`, with why as its `svc:hasExecutionResult` (it begins with `NOT_REACHED`). The
attempt's curves and the operation's reference stay. It reads the operation's status first: an
operation that is no longer `running` has been ended meanwhile, by a detector that took the call
after all, and keeps its end: the record holds the status, result and outcome of that one read,
not the call's failure. The read and the commit are two steps, so an end that falls between
them is still overwritten.

**One attempt for each operation.** An operation that is not `queued` when it reaches the
workflow has had its attempt, or has ended, so it is logged and left alone.

**The attempt** is a function the screwing resource is given: the Screw and the values of its
Screw type in, the frame of the attempt out. The intake reads the values with the operation, in the
same fetch, from the Screw type that the Screw is linked to: the six thresholds and the
preparation turning angle. The learner changes them on the type, so the next attempt on a Screw of
that type is given the refined value. The attempt is always given all seven: without one, it is
not made (above). The log line of each attempt and the dashboard show the
values it was given. In `jms-uc1` the attempt drives the robot over MQTT (`robot_attempt`): the
middleware instance of the screwing resource is built with the robot's wiring (`build`, with
`robot`), so it holds a connector for each of the robot's twelve parameters, built from the
knowledge graph's description of the robot and nothing else; the attempt writes the seven values
and the start command through the write connectors, and collects every sample the robot publishes
through the read connectors into the frame. The shipped demonstrator replaces only the robot
controller: a stand-in process (`robot_controller`) that replays the example recording the demo
names for the Screw (`RECORDING_OF`) over the same broker and topics. A robot that reports a
failure, or none within the attempt's timeout, raises, and the operation is `failed` with the
reason. The replay attempts (`replay_attempt`, `replay_by_screw`) stay for a build without the
wiring, as the offline tests and the detector's live test have it.

Its activity feed is on, and so is the middleware's state dashboard: the library's own boxes, and
the box "Attempts" in the tab "Screwing resource", which the dashboard opens on
(`screwing_resource_dashboard`). The box shows each attempt, newest first, from what the screwing
resource keeps in its memory as it works (`screwing_resource_memory`): an open dashboard reads no
store.

Run it as `python -m jms_kapps.uc1.screwing_resource --dataset <name>`. It starts and serves as
every participant's process does, through `common.process`, with the local InfluxDB unless
`INFLUXDB_URL` names another. `jms-uc1` starts it (`run_core`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from typing import Any

from kapps_ogm import OGM, ClassScope, Node, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import process_parser, run_process
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.classify_call import Called, DetectorNotReached, call_detector
from jms_kapps.uc1.effective_values import type_value_problems, usable_values
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.mqtt_broker import ensure_transport
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.robot_attempt import mqtt_attempt
from jms_kapps.uc1.robot_binding import (
    ROBOT_SCOPE,
    RobotWiring,
    Setpoints,
    robot_wiring,
)
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.screwing_resource_dashboard import TAB as DASHBOARD_TAB
from jms_kapps.uc1.screwing_resource_dashboard import register_box
from jms_kapps.uc1.screwing_resource_memory import (
    AttemptRecord,
    ScrewingMemory,
    values_text,
)
from jms_kapps.uc1.seed import RECORDING_OF
from jms_kapps.uc1.timeseries import FrameWriter
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, TYPE_LEVEL

# Named, not `__name__`: the process runs this module as `python -m`, where `__name__` is
# `__main__`. A logger of that name is not below `jms_kapps`, so its INFO lines would be
# dropped at the root's WARNING (`common.process` lets `jms_kapps` log from INFO up).
logger = logging.getLogger("jms_kapps.uc1.screwing_resource")

# The screwing resource of the demo, which `jms-uc1` starts.
RESOURCE = IRI("ScrewingResource1", base=JMS_NS)

TypeValues = Mapping[IRI, float]
"""The values of the Screw type that the Screw is linked to, by property: the six thresholds and
the preparation turning angle, each held once as a finite number. The attempt is given all seven."""

Attempt = Callable[[IRI, TypeValues], Frame]
"""The unscrewing attempt on one Screw, given the values of its Screw type: the physical work,
which gives back the attempt's frame.

It raises if the attempt fails, and the operation is then `failed`.
"""

Commit = Callable[..., AbstractContextManager[Any]]
"""The middleware's `commit`."""

# What the intake reads of an unscrewing operation, besides the Capability it implements: the
# Screw it names, its Screw type, the seven values the type holds, and the operation's status, its
# result and its outcome. One projection, so the attempt's parameters arrive with the operation and
# no second read follows; the result and outcome are what a detector that ended it left.
_OPERATION_CHAINS: list[list[str | IRI]] = []
for _prop in TYPE_LEVEL:
    _OPERATION_CHAINS.append([JMS.concernsScrew, JMS.hasScrewType, _prop])
_OPERATION_CHAINS.append([SVC.hasOperationStatus])
_OPERATION_CHAINS.append([SVC.hasExecutionResult])
_OPERATION_CHAINS.append([JMS.hasDetectionResult])
OPERATION_SCOPE = ClassScope.from_property_chains(_OPERATION_CHAINS)


@dataclass(frozen=True)
class UnscrewingOperation:
    """One unscrewing operation, as the intake read it: its status, the Screws it names, their
    Screw types, and the values those types hold.

    `type_values` holds, for the one Screw type that the one Screw names, each of the seven
    type-level values that the type holds once as a finite number. It is empty unless the
    operation names one Screw of one Screw type. `type_problems` names, for an operation that
    names one Screw, what keeps its Screw type from giving all seven: a value held zero times,
    more than once, or not as a finite number, or no Screw type, or more than one. The screwing
    resource makes no attempt when it names any. `results` and `outcome` are the operation's
    `svc:hasExecutionResult` and Detection result (the IRI), which an operation that was ended holds.
    """

    operation: IRI
    status: str | None
    screws: tuple[IRI, ...]
    screw_types: tuple[IRI, ...] = ()
    type_values: Mapping[IRI, float] = field(default_factory=dict)
    type_problems: tuple[str, ...] = ()
    results: tuple[str, ...] = ()
    outcome: str | None = None


def unscrewing_operation_from(node: Node) -> UnscrewingOperation:
    """The unscrewing operation that the intake hands the workflow, as the domain code takes it.

    The intake reads it under `OPERATION_SCOPE` into a node: a linked value is a node with an
    `id`, and a literal is the value itself.
    """
    data = node.data or {}
    statuses = data.get(SVC.hasOperationStatus, [])
    outcomes = data.get(JMS.hasDetectionResult, [])
    screws = data.get(JMS.concernsScrew, [])
    screw_types = [
        screw_type
        for screw in screws
        for screw_type in (getattr(screw, "data", None) or {}).get(JMS.hasScrewType, [])
    ]
    type_values: Mapping[IRI, float] = {}
    type_problems: list[str] = []
    if len(screws) == 1 and len(screw_types) == 1:
        held = getattr(screw_types[0], "data", None) or {}
        type_values = usable_values(held)
        type_problems = type_value_problems(_id(screw_types[0]), held)
    elif len(screws) == 1:
        named = _id(screws[0]).fragment
        type_problems = [
            f"the Screw {named} names {len(screw_types)} Screw types, not one"
            if screw_types
            else f"the Screw {named} names no Screw type"
        ]
    return UnscrewingOperation(
        operation=IRI(str(node.id)),
        status=str(statuses[0]) if statuses else None,
        screws=tuple(_id(screw) for screw in screws),
        screw_types=tuple(_id(screw_type) for screw_type in screw_types),
        type_values=type_values,
        type_problems=tuple(type_problems),
        results=tuple(str(result) for result in data.get(SVC.hasExecutionResult, [])),
        outcome=str(_id(outcomes[0])) if outcomes else None,
    )


def _id(value: object) -> IRI:
    """The IRI of a linked value, which the intake gives as a node with an `id`."""
    return IRI(str(getattr(value, "id", value)))


def replay_attempt(recording: str = "success") -> Attempt:
    """The attempt of the demonstrator: the example recording `recording`, replayed as a
    frame stamped now, whatever the Screw. `replay.EXAMPLE_RECORDINGS` names the recordings. It
    ignores the type values it is given: in the real cell, the robot and the screwdriver would use
    them."""

    def attempt(screw: IRI, values: TypeValues) -> Frame:
        return example_frame(recording)

    return attempt


def replay_by_screw(recordings: Mapping[IRI, str]) -> Attempt:
    """The attempt of the demonstrator: the recording that `recordings` names for the Screw,
    replayed as a frame stamped now. It ignores the type values it is given: in the real cell, the
    robot and the screwdriver would use them.

    A Screw that `recordings` names no recording for raises `ValueError`, and the operation then
    ends `failed` with that reason.

    Args:
        recordings: Which recording to replay for each Screw, a demo's stand-in for the robot.
    """

    def attempt(screw: IRI, values: TypeValues) -> Frame:
        recording = recordings.get(screw)
        if recording is None:
            raise ValueError(
                f"no recording for {screw.fragment}: the demo replays one for "
                f"{' and '.join(sorted(s.fragment for s in recordings))}"
            )
        return example_frame(recording)

    return attempt


# What the screwing resource logs. The tests match each record whole, so it is built here.


def left_alone_record(resource: IRI, operation: IRI, status: str | None) -> str:
    """What `resource` logs when it leaves alone `operation`, which is not `queued`."""
    return (
        f"{resource.fragment}: notified of {operation.fragment}, which is {status}, so it is "
        "left alone: an operation gets one attempt"
    )


def attempt_given_record(
    resource: IRI,
    operation: IRI,
    screw: IRI,
    screw_type: IRI | None,
    values: TypeValues,
) -> str:
    """What `resource` logs when the attempt of `operation` on `screw` begins: the values of
    `screw_type` that the attempt is given."""
    return (
        f"{resource.fragment}: the attempt of {operation.fragment} on {screw.fragment} is given "
        f"the values of {screw_type.fragment if screw_type else 'no Screw type'}: "
        f"{values_text(values)}"
    )


def attempt_failed_record(resource: IRI, operation: IRI) -> str:
    """What `resource` logs once the attempt of `operation` failed and `failed` was committed."""
    return (
        f"{resource.fragment}: the attempt of {operation.fragment} failed, so it is failed, "
        "and the anomaly detector is not called"
    )


def not_attempted_record(resource: IRI, operation: IRI, why: str) -> str:
    """What `resource` logs once it ended `operation` `failed` before the attempt, because the
    Screw type does not give the seven values: `why` is the operation's `svc:hasExecutionResult`."""
    return (
        f"{resource.fragment}: no attempt is made for {operation.fragment}, so it is failed, "
        f"and the anomaly detector is not called: {why}"
    )


def ended_meanwhile_record(resource: IRI, operation: IRI, status: str | None) -> str:
    """What `resource` logs when the call of the anomaly detector for `operation` failed, but
    the operation had ended already: it keeps its end."""
    return (
        f"{resource.fragment}: the call of the anomaly detector for {operation.fragment} did "
        f"not succeed, but {operation.fragment} is {status} already, so it stays {status}"
    )


# How the reason of an operation that the detector did not take begins, in its
# `svc:hasExecutionResult`.
NOT_REACHED = "the detector was not reached: "

# How the reason of an operation that the screwing resource ended before the attempt begins, in
# its `svc:hasExecutionResult`: its Screw type does not give the seven values.
NOT_ATTEMPTED = "no attempt was made: "


class ScrewingResource:
    """One screwing resource, given the middleware's commit, where its frames go, its attempt,
    the call of the anomaly detector, and its memory.

    Args:
        resource: The screwing resource.
        commit: The middleware's `commit`.
        writer: Writes each attempt's frame into the time-series store.
        attempt: The attempt on one Screw.
        classify: Calls the anomaly detector's classify workflow with an operation.
        read: Reads an unscrewing operation from the graph, as the intake does.
        memory: Where it keeps each attempt that it made, for its dashboard.
    """

    def __init__(
        self,
        resource: IRI,
        *,
        commit: Commit,
        writer: FrameWriter,
        attempt: Attempt,
        classify: Callable[[IRI], Called],
        read: Callable[[IRI], UnscrewingOperation],
        memory: ScrewingMemory | None = None,
    ) -> None:
        self.resource = IRI(resource)
        self.commit = commit
        self.writer = writer
        self.attempt = attempt
        self.classify = classify
        self.read = read
        self.memory = memory if memory is not None else ScrewingMemory()

    def unscrew(self, unscrewing: UnscrewingOperation) -> None:
        """The unscrewing workflow: one attempt for an unscrewing operation, then the call of the
        anomaly detector."""
        operation = unscrewing.operation
        log_scope(
            logger,
            "Screwing resource",
            "receives",
            operation,
            "OPERATION_SCOPE",
            OPERATION_SCOPE,
            "the middleware's intake read it for the workflow: the Screw it names (concernsScrew), "
            "its Screw type (hasScrewType) and the seven values the type holds, which the "
            "attempt is given, and its status (hasOperationStatus), its result (hasExecutionResult) and "
            "its outcome (hasDetectionResult), which an operation the detector ended holds",
        )
        if unscrewing.status != OperationStatus.QUEUED:
            logger.warning(
                "%s", left_alone_record(self.resource, operation, unscrewing.status)
            )
            return
        record = AttemptRecord(
            at=self.memory.now(),
            operation=operation,
            screw=unscrewing.screws[0] if len(unscrewing.screws) == 1 else None,
            screw_type=(
                unscrewing.screw_types[0] if len(unscrewing.screw_types) == 1 else None
            ),
            type_values=unscrewing.type_values,
        )
        if unscrewing.type_problems:
            self.memory.attempted(
                self._end_not_attempted(record, unscrewing.type_problems)
            )
            return
        attempted = False
        try:
            with self.commit(
                entry=[_status(operation, OperationStatus.RUNNING)],
                error=lambda error: [
                    _failed(operation, f"the unscrewing attempt failed: {error}")
                ],
            ) as transition:
                attempted = True
                screw = _the_screw(unscrewing)
                logger.info(
                    "%s",
                    attempt_given_record(
                        self.resource,
                        operation,
                        screw,
                        record.screw_type,
                        unscrewing.type_values,
                    ),
                )
                frame = self.attempt(screw, unscrewing.type_values)
                reference = self.writer.write(frame)
                transition.update(
                    operation, {str(JMS.hasTimeSeriesReference): [str(reference)]}
                )
        except Exception as error:
            if attempted:
                logger.exception("%s", attempt_failed_record(self.resource, operation))
                self.memory.attempted(
                    replace(
                        record,
                        status=OperationStatus.FAILED,
                        why=f"the unscrewing attempt failed: {error}",
                    )
                )
            else:
                logger.exception(
                    "%s: the store refused %s running, so no attempt was made",
                    self.resource.fragment,
                    operation.fragment,
                )
            return
        logger.info(
            "%s: %s is running, with its curves at %s",
            self.resource.fragment,
            operation.fragment,
            reference,
        )
        self.memory.attempted(
            self._call_detector(replace(record, reference=str(reference)))
        )

    def _call_detector(self, record: AttemptRecord) -> AttemptRecord:
        """Call the anomaly detector's classify workflow with the operation of `record`. A
        detector that is not found, or a call that fails or times out, ends the operation
        `failed`. Gives back `record` with the detector called and how the operation ended."""
        operation = record.operation
        try:
            called = self.classify(operation)
        except DetectorNotReached as error:
            return self._end_not_reached(
                replace(record, detector=error.endpoint), str(error)
            )
        except Exception as error:
            # Not one of the call's known failures, such as a store that does not answer the
            # lookup: the detector was not reached all the same.
            logger.exception(
                "%s: the call of the anomaly detector for %s raised",
                self.resource.fragment,
                operation.fragment,
            )
            return self._end_not_reached(
                record, f"the call of its classify workflow failed: {error!r}"
            )
        logger.info(
            "%s: the anomaly detector took %s",
            self.resource.fragment,
            operation.fragment,
        )
        answer = called.answer
        if answer is None:
            return replace(record, detector=called.endpoint)
        if answer.ended is None:
            # The detector left the operation as it was, with the status it had then.
            return replace(
                record,
                detector=called.endpoint,
                why=f"the detector left it as it was: {answer.why}",
            )
        return replace(
            record,
            detector=called.endpoint,
            status=answer.ended,
            outcome=answer.outcome,
            why=answer.why,
        )

    def _end_not_attempted(
        self, record: AttemptRecord, problems: tuple[str, ...]
    ) -> AttemptRecord:
        """Commit the operation of `record` `failed`, from `queued`, with each of `problems`: no
        attempt is made, so the record keeps no type values. Gives back `record` with the operation's status as this left it, or
        None when the commit did not succeed."""
        operation = record.operation
        result = NOT_ATTEMPTED + "; ".join(problems)
        try:
            with self.commit(entry=[_failed(operation, result)]):
                pass
        except Exception:
            logger.exception(
                "%s: no attempt is made for %s (%s), and the commit of failed did not succeed",
                self.resource.fragment,
                operation.fragment,
                result,
            )
            return replace(
                record,
                status=None,
                type_values={},
                why=f"{result}; the commit of failed did not reach the store",
            )
        logger.warning("%s", not_attempted_record(self.resource, operation, result))
        return replace(
            record, status=OperationStatus.FAILED, type_values={}, why=result
        )

    def _end_not_reached(self, record: AttemptRecord, why: str) -> AttemptRecord:
        """Commit the operation of `record` `failed`, with why the detector was not reached,
        unless it is no longer `running`. Its reference stays. Gives back `record` with the
        operation's status as this left it, or None when the commit did not succeed."""
        operation = record.operation
        result = NOT_REACHED + why
        try:
            current = self.read(operation)
            status = current.status
            if status != OperationStatus.RUNNING:
                logger.warning(
                    "%s", ended_meanwhile_record(self.resource, operation, status)
                )
                # The end is the detector's: its result and outcome, not the call's failure.
                return replace(
                    record,
                    status=status,
                    outcome=current.outcome,
                    # An operation can hold several results; the box shows them joined, as the
                    # planner's does, and none when it holds none.
                    why="; ".join(current.results) or None,
                )
            with self.commit(entry=[_failed(operation, result)]):
                pass
        except Exception:
            logger.exception(
                "%s: the anomaly detector was not reached for %s (%s), and the commit of "
                "failed did not succeed",
                self.resource.fragment,
                operation.fragment,
                why,
            )
            return replace(
                record,
                status=None,
                why=f"{result}; the commit of failed did not reach the store",
            )
        logger.warning(
            "%s: the anomaly detector was not reached for %s, so it is failed: %s",
            self.resource.fragment,
            operation.fragment,
            why,
        )
        return replace(record, status=OperationStatus.FAILED, why=result)


def build(
    resource_iri: str,
    ogm: OGM,
    host: str,
    port: int,
    *,
    writer: FrameWriter | None = None,
    attempt: Attempt | None = None,
    robot: RobotWiring | None = None,
) -> SemanticMiddleware:
    """The middleware instance of the screwing resource `resource_iri`, served at `host:port`,
    with its unscrewing workflow, its notification handler and its dashboard: the middleware's
    boxes, and its own box in the tab "Screwing resource", which it opens on.

    With `robot`, the middleware is built to drive the screwing resource's robot over MQTT: it
    reads the robot's parameters from the graph under `ROBOT_SCOPE`, builds a connector for each
    from that description through use case 1's binding descriptor, in a registry of its own, and
    brings up the broker through the wiring's transport hook. The attempt then defaults to the
    MQTT attempt (`robot_attempt.mqtt_attempt`), whose setpoints are persistence writes through
    the robot's write connectors (`robot_binding.Setpoints`). Nothing here names a broker, a port
    or a topic. The build raises if the graph does not describe every parameter of the robot.

    Args:
        writer: Where each attempt's frame goes. Defaults to the InfluxDB that `INFLUXDB_URL`
            names, in the database `jms-kapps`.
        attempt: The attempt on one Screw. Defaults to the MQTT attempt with `robot`, else to
            the replay of the recording `success`.
        robot: The wiring that drives the robot over MQTT. None builds the middleware as before
            this wiring existed, with no connector: for a dataset without a seeded robot, as the
            offline tests and the detector's live test have.
    """
    if robot is None:
        middleware = SemanticMiddleware(
            mode=Mode.RESOURCE,
            resource_iri=resource_iri,
            service_class=str(JMS.ScrewingResourceService),
            ogm=ogm,
            host=host,
            port=port,
            activity_feed=True,
        )
    else:
        # The same instance, built to drive the robot: the constructor wires the connectors.
        middleware = SemanticMiddleware(
            mode=Mode.RESOURCE,
            resource_iri=resource_iri,
            service_class=str(JMS.ScrewingResourceService),
            ogm=ogm,
            host=host,
            port=port,
            activity_feed=True,
            class_scope=ROBOT_SCOPE,
            autoregister_connectors=True,
            connector_registry=robot.registry,
            ensure_transport=robot.ensure_transport,
        )
        robot.check()
        logger.info(
            "%s: %d connectors to its robot, built from the graph description",
            IRI(resource_iri).fragment,
            len(middleware.connections),
        )
        if attempt is None:
            setpoints = Setpoints(middleware, IRI(resource_iri), robot)
            attempt = mqtt_attempt(setpoints.write, robot.sink)
    # The middleware's state dashboard at `/dashboard`: the library's boxes, and the screwing
    # resource's own, which renders its memory.
    memory = ScrewingMemory()
    register_box(enable_dashboard(middleware, first_tab=DASHBOARD_TAB), memory)
    resource = ScrewingResource(
        IRI(resource_iri),
        commit=middleware.commit,
        writer=writer if writer is not None else FrameWriter(connect_influxdb()),
        attempt=attempt if attempt is not None else replay_attempt(),
        classify=lambda operation: call_detector(ogm.db, operation),
        read=lambda operation: _read_operation(middleware, operation),
        memory=memory,
    )

    # The intake runs this workflow with an operation that implements the resource's
    # UnscrewingCapability, read once under the scope given below. Nothing calls it over REST.
    @middleware.workflow(
        capability_class=JMS.UnscrewingCapability,
        workflow_class=JMS.UnscrewingWorkflow,
    )
    def unscrew(operation: Node) -> None:
        """Make one attempt for an unscrewing operation, then call the anomaly detector."""
        resource.unscrew(unscrewing_operation_from(operation))

    # Every other notification: the unscrewing workflow does not take it.
    def unmatched(operation: Node) -> None:
        logger.warning(
            "%s: notified of %s, which its unscrewing workflow does not take",
            IRI(resource_iri).fragment,
            IRI(str(operation.id)).fragment,
        )

    middleware.on_notification(unmatched, class_scope=OPERATION_SCOPE)
    return middleware


def _read_operation(
    middleware: SemanticMiddleware, operation: IRI
) -> UnscrewingOperation:
    """Read an unscrewing operation as the intake did, logging the scope that reads it."""
    log_scope(
        logger,
        "Screwing resource",
        "reads",
        operation,
        "OPERATION_SCOPE",
        OPERATION_SCOPE,
        "it wants its status (hasOperationStatus), its result (hasExecutionResult) and its outcome "
        "(hasDetectionResult), for an operation the detector ended; the scope is the intake's, so it reads "
        "the Screw (concernsScrew), its Screw type (hasScrewType) and the type's values too",
    )
    return unscrewing_operation_from(
        middleware.fetch(operation, class_scope=OPERATION_SCOPE)
    )


def _the_screw(unscrewing: UnscrewingOperation) -> IRI:
    """The one Screw that the operation names.

    Raises:
        ValueError: If it names none, or more than one.
    """
    if len(unscrewing.screws) != 1:
        raise ValueError(
            f"{unscrewing.operation.fragment} names {len(unscrewing.screws)} Screws; an "
            "unscrewing operation names exactly one"
        )
    return unscrewing.screws[0]


def _status(operation: IRI, status: str) -> Update:
    return Update(instance_iri=operation, data={str(SVC.hasOperationStatus): [status]})


def _failed(operation: IRI, why: str) -> Update:
    """The operation `failed`, with why as its result."""
    return Update(
        instance_iri=operation,
        data={
            str(SVC.hasOperationStatus): [OperationStatus.FAILED],
            str(SVC.hasExecutionResult): [why],
        },
    )


def main() -> None:
    parser = process_parser("The screwing resource of use case 1")
    parser.add_argument(
        "--resource",
        default=str(RESOURCE),
        help="The IRI of the screwing resource this process serves",
    )
    args = parser.parse_args()
    # The robot is driven over MQTT through the connectors the middleware builds from the graph
    # description; the broker comes up through the transport hook, at the address the graph names.
    with connect_influxdb() as influxdb:
        run_process(
            args.host,
            lambda port: build(
                args.resource,
                OGM(db=connect(args.dataset)),
                args.host,
                port,
                writer=FrameWriter(influxdb),
                robot=robot_wiring(ensure_transport),
            ),
            lambda address: (
                f"The screwing resource runs on {address}. It drives its robot over MQTT "
                "through the connectors its middleware built from the knowledge graph; the "
                "robot controller replays "
                + _recording_sentence()
                + f" Its dashboard: {address}dashboard"
            ),
        )


def _recording_sentence() -> str:
    """The announcement's middle sentence: which recording the robot controller replays for each
    demo Screw, built from `RECORDING_OF` (e.g. "success for Screw1 and missing_screw for
    Screw2.")."""
    parts = [
        f"{recording} for {screw.fragment}" for screw, recording in RECORDING_OF.items()
    ]
    return " and ".join(parts) + "."


if __name__ == "__main__":
    main()
