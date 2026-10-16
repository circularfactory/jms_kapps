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
  why as its `svc:executionResult`. The detector is not called.

The commit reads nothing: the intake has read the operation already.

**Then it calls the anomaly detector's classify workflow** with the operation's IRI, found in the
graph by the detector's Classify capability (`classify_call`). That is a call inside the cell, and
the screwing resource reads no result from it: the detector ends the operation.

**A detector that is not reached** leaves nobody to end the operation: no live detector is found,
or the call fails or times out. Then the screwing resource ends the operation `failed` itself, in
one `mw.commit()`, with why as its `svc:executionResult` (it begins with `NOT_REACHED`). The
attempt's curves and the operation's reference stay. It reads the operation's status first: an
operation that is no longer `running` has been ended meanwhile, by a detector that took the call
after all, and keeps its end. The read and the commit are two steps, so an end that falls between
them is still overwritten.

**One attempt for each operation.** An operation that is not `queued` when it reaches the
workflow has had its attempt, or has ended, so it is logged and left alone.

**The attempt** is a function the screwing resource is given: the Screw in, the frame of the
attempt out. In the demonstrator it replays a recording as a frame, the legacy mock recording that
the demo names for the Screw (`replay_by_screw`, from `RECORDING_OF`), stamped as if recorded now.
A Screw the demo names no recording for raises, and the operation is `failed`. In the real cell,
the robot and the screwdriver would run here through connectors.

Its activity feed is on, and so is the middleware's state dashboard, with the library's own boxes
only, as for every party of use case 2.

Run it as `python -m jms_kapps.uc1.screwing_resource --dataset <name>`. It starts and serves as
every participant's process does, through `common.process`, with the local InfluxDB unless
`INFLUXDB_URL` names another. `jms-uc1` starts it (`run_core`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from kapps_ogm import OGM, ClassScope, Node, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import process_parser, run_process
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.classify_call import DetectorNotReached, call_detector
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.replay import mock_frame
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.seed import RECORDING_OF
from jms_kapps.uc1.timeseries import FrameWriter
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

# Named, not `__name__`: the process runs this module as `python -m`, where `__name__` is
# `__main__`. A logger of that name is not below `jms_kapps`, so its INFO lines would be
# dropped at the root's WARNING (`common.process` lets `jms_kapps` log from INFO up).
logger = logging.getLogger("jms_kapps.uc1.screwing_resource")

# The screwing resource of the demo, which `jms-uc1` starts.
RESOURCE = IRI("ScrewingResource1", base=JMS_NS)

Attempt = Callable[[IRI], Frame]
"""The unscrewing attempt on one Screw: the physical work, which gives back the attempt's frame.

It raises if the attempt fails, and the operation is then `failed`.
"""

Commit = Callable[..., AbstractContextManager[Any]]
"""The middleware's `commit`."""

# What the intake reads of an unscrewing operation, besides the Capability it implements: the
# Screw it names, and its status.
OPERATION_SCOPE = ClassScope.from_property_chains(
    [[JMS.hasScrew], [SVC.operationStatus]]
)


@dataclass(frozen=True)
class UnscrewingOperation:
    """One unscrewing operation, as the intake read it: its status, and the Screws it names."""

    operation: IRI
    status: str | None
    screws: tuple[IRI, ...]


def unscrewing_operation_from(node: Node) -> UnscrewingOperation:
    """The unscrewing operation that the intake hands the workflow, as the domain code takes it.

    The intake reads it under `OPERATION_SCOPE` into a node: a linked value is a node with an
    `id`, and a literal is the value itself.
    """
    data = node.data or {}
    statuses = data.get(SVC.operationStatus, [])
    return UnscrewingOperation(
        operation=IRI(str(node.id)),
        status=str(statuses[0]) if statuses else None,
        screws=tuple(
            IRI(str(getattr(screw, "id", screw)))
            for screw in data.get(JMS.hasScrew, [])
        ),
    )


def replay_attempt(recording: str = "success") -> Attempt:
    """The attempt of the demonstrator: the legacy mock recording `recording`, replayed as a
    frame stamped now, whatever the Screw. `replay.MOCK_RECORDINGS` names the recordings."""

    def attempt(screw: IRI) -> Frame:
        return mock_frame(recording)

    return attempt


def replay_by_screw(recordings: Mapping[IRI, str]) -> Attempt:
    """The attempt of the demonstrator: the recording that `recordings` names for the Screw,
    replayed as a frame stamped now.

    A Screw that `recordings` names no recording for raises `ValueError`, and the operation then
    ends `failed` with that reason.

    Args:
        recordings: Which recording to replay for each Screw, a demo's stand-in for the robot.
    """

    def attempt(screw: IRI) -> Frame:
        recording = recordings.get(screw)
        if recording is None:
            raise ValueError(
                f"no recording for {screw.fragment}: the demo replays one for "
                f"{' and '.join(sorted(s.fragment for s in recordings))}"
            )
        return mock_frame(recording)

    return attempt


# What the screwing resource logs. The tests match each record whole, so it is built here.


def left_alone_record(resource: IRI, operation: IRI, status: str | None) -> str:
    """What `resource` logs when it leaves alone `operation`, which is not `queued`."""
    return (
        f"{resource.fragment}: notified of {operation.fragment}, which is {status}, so it is "
        "left alone: an operation gets one attempt"
    )


def attempt_failed_record(resource: IRI, operation: IRI) -> str:
    """What `resource` logs once the attempt of `operation` failed and `failed` was committed."""
    return (
        f"{resource.fragment}: the attempt of {operation.fragment} failed, so it is failed, "
        "and the anomaly detector is not called"
    )


def ended_meanwhile_record(resource: IRI, operation: IRI, status: str | None) -> str:
    """What `resource` logs when the call of the anomaly detector for `operation` failed, but
    the operation had ended already: it keeps its end."""
    return (
        f"{resource.fragment}: the call of the anomaly detector for {operation.fragment} did "
        f"not succeed, but {operation.fragment} is {status} already, so it stays {status}"
    )


# How the reason of an operation that the detector did not take begins, in its
# `svc:executionResult`.
NOT_REACHED = "the detector was not reached: "


class ScrewingResource:
    """One screwing resource, given the middleware's commit, where its frames go, its attempt,
    and the call of the anomaly detector.

    Args:
        resource: The screwing resource.
        commit: The middleware's `commit`.
        writer: Writes each attempt's frame into the time-series store.
        attempt: The attempt on one Screw.
        classify: Calls the anomaly detector's classify workflow with an operation.
        read: Reads an unscrewing operation from the graph, as the intake does.
    """

    def __init__(
        self,
        resource: IRI,
        *,
        commit: Commit,
        writer: FrameWriter,
        attempt: Attempt,
        classify: Callable[[IRI], None],
        read: Callable[[IRI], UnscrewingOperation],
    ) -> None:
        self.resource = IRI(resource)
        self.commit = commit
        self.writer = writer
        self.attempt = attempt
        self.classify = classify
        self.read = read

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
            "the middleware's intake read it for the workflow: the Screw it names (hasScrew) "
            "and its status (operationStatus)",
        )
        if unscrewing.status != OperationStatus.QUEUED:
            logger.warning(
                "%s", left_alone_record(self.resource, operation, unscrewing.status)
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
                frame = self.attempt(_the_screw(unscrewing))
                reference = self.writer.write(frame)
                transition.update(
                    operation, {str(JMS.hasTimeSeriesReference): [str(reference)]}
                )
        except Exception:
            if attempted:
                logger.exception("%s", attempt_failed_record(self.resource, operation))
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
        self._call_detector(operation)

    def _call_detector(self, operation: IRI) -> None:
        """Call the anomaly detector's classify workflow with `operation`. A detector that is not
        found, or a call that fails or times out, ends the operation `failed`."""
        try:
            self.classify(operation)
        except DetectorNotReached as error:
            self._end_not_reached(operation, str(error))
            return
        except Exception as error:
            # Not one of the call's known failures, such as a store that does not answer the
            # lookup: the detector was not reached all the same.
            logger.exception(
                "%s: the call of the anomaly detector for %s raised",
                self.resource.fragment,
                operation.fragment,
            )
            self._end_not_reached(
                operation, f"the call of its classify workflow failed: {error!r}"
            )
            return
        logger.info(
            "%s: the anomaly detector took %s",
            self.resource.fragment,
            operation.fragment,
        )

    def _end_not_reached(self, operation: IRI, why: str) -> None:
        """Commit `operation` `failed`, with why the detector was not reached, unless it is no
        longer `running`. Its reference stays."""
        try:
            status = self.read(operation).status
            if status != OperationStatus.RUNNING:
                logger.warning(
                    "%s", ended_meanwhile_record(self.resource, operation, status)
                )
                return
            with self.commit(entry=[_failed(operation, NOT_REACHED + why)]):
                pass
        except Exception:
            logger.exception(
                "%s: the anomaly detector was not reached for %s (%s), and the commit of "
                "failed did not succeed",
                self.resource.fragment,
                operation.fragment,
                why,
            )
            return
        logger.warning(
            "%s: the anomaly detector was not reached for %s, so it is failed: %s",
            self.resource.fragment,
            operation.fragment,
            why,
        )


def build(
    resource_iri: str,
    ogm: OGM,
    host: str,
    port: int,
    *,
    writer: FrameWriter | None = None,
    attempt: Attempt | None = None,
) -> SemanticMiddleware:
    """The middleware instance of the screwing resource `resource_iri`, served at `host:port`,
    with its unscrewing workflow, its notification handler and its state dashboard.

    Args:
        writer: Where each attempt's frame goes. Defaults to the InfluxDB that `INFLUXDB_URL`
            names, in the database `jms-kapps`.
        attempt: The attempt on one Screw. Defaults to the replay of the recording `success`.
    """
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=resource_iri,
        service_class=str(JMS.ScrewingResourceService),
        ogm=ogm,
        host=host,
        port=port,
        activity_feed=True,
    )
    # The middleware's state dashboard at `/dashboard`, with the library's own boxes.
    enable_dashboard(middleware)
    resource = ScrewingResource(
        IRI(resource_iri),
        commit=middleware.commit,
        writer=writer if writer is not None else FrameWriter(connect_influxdb()),
        attempt=attempt if attempt is not None else replay_attempt(),
        classify=lambda operation: call_detector(ogm.db, operation),
        read=lambda operation: _read_operation(middleware, operation),
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
        "it wants the Screw it names (hasScrew) and its status (operationStatus)",
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
    return Update(instance_iri=operation, data={str(SVC.operationStatus): [status]})


def _failed(operation: IRI, why: str) -> Update:
    """The operation `failed`, with why as its result."""
    return Update(
        instance_iri=operation,
        data={
            str(SVC.operationStatus): [OperationStatus.FAILED],
            str(SVC.executionResult): [why],
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
    with connect_influxdb() as influxdb:
        run_process(
            args.host,
            lambda port: build(
                args.resource,
                OGM(db=connect(args.dataset)),
                args.host,
                port,
                writer=FrameWriter(influxdb),
                attempt=replay_by_screw(RECORDING_OF),
            ),
            lambda address: (
                f"The screwing resource runs on {address}. It replays "
                + _recording_sentence()
                + f" Its dashboard: {address}dashboard"
            ),
        )


def _recording_sentence() -> str:
    """The announcement's middle sentence: which recording each demo Screw replays, built from
    `RECORDING_OF` (e.g. "success for Screw1 and missing_screw for Screw2.")."""
    parts = [
        f"{recording} for {screw.fragment}" for screw, recording in RECORDING_OF.items()
    ]
    return " and ".join(parts) + "."


if __name__ == "__main__":
    main()
