"""The anomaly detector of use case 1: it classifies one recorded unscrewing attempt, and ends its
operation.

**It is called, not notified.** After each attempt, the screwing resource calls the detector's
classify workflow with the IRI of the unscrewing operation (`classify_call.ClassifyRequest`),
which is `running` and holds its time-series reference. The screwing resource finds the workflow
in the graph by the detector's `:ClassifyCapability`, and acts on nothing of the answer: the
detector ends the operation. The screwing resource keeps the answer for its dashboard.

**One classification is one commit of the middleware**, `mw.commit()`:

- **Body:** the effective values of the operation's Screw (`effective_values`), read afresh for
  every attempt, so a value that the learner changed counts from the next attempt; the frame that
  the operation's time-series reference names (`timeseries.FrameReader`); and the decision tree
  (`decision_tree.classify`). Then the classification as a `prov:Activity`, with the detector's
  resource as its agent (`prov:wasAssociatedWith`) and its end time (`prov:endedAtTime`), and the
  parameter copy, a new instance of the ten values that names that activity
  (`prov:wasGeneratedBy`), each with `mw.create()`, which joins the commit; and through `transition.update(...)` the operation's
  outcome, its link to the parameter copy, and after a failure outcome the measured end position of its
  approach.
- **Clean exit:** all of that and how the operation ends, as one write. The outcome ScrewIntact
  ends it `done`. Every other outcome is a failure outcome, and ends it `failed`, with a
  `svc:hasExecutionResult` that names the outcome (it begins with `ANOMALY`), for example
  `ANOMALY: MissingScrew`.
- **Error in the body:** nothing of the body is written, and the operation is committed `failed`,
  with why as its `svc:hasExecutionResult` (it begins with `NOT_CLASSIFIED`): a Screw without its
  placement values, a frame that the store lacks or that cannot be read, or curves that the tree
  refuses.

**It ends an operation only while the operation is `running`.** From `running` on, the screwing
resource that took the operation is responsible for it. It may end the operation `failed` itself,
when the detector does not answer in time. An operation that is not `running` when the call comes
is left as it is: nothing else is read, and nothing is written. When the detector ends an
operation, at the clean exit or on an error, it reads the status again. An operation that is no
longer `running` is left as it is too, and nothing of the classification is written. Each time,
the detector logs one line, `left_as_it_is_record`.

The store's check closes part of the gap between that read and the write: the write removes the
status that the object-graph mapper read just before it, and the store refuses the write when
that status is gone (`PreconditionFailedError`). The detector then reads the operation again, and
leaves it as it is when it has ended. The mapper reads the status at the write itself, though, so
a status that changed between the detector's read and the mapper's read is still overwritten. The
libraries give a write no precondition on a value that the writer read earlier.

**The detector sends no notification of its own.** The planner, which issued the operation,
learns of the end from the middleware: after the store admits the commit that ends the operation
`done` or `failed`, after a failure outcome as well, `mw.commit()` notifies the operation's
issuer (`svc:issuedBy`), in the
background. A commit that writes nothing, because the operation ended meanwhile, notifies nobody.

The answer of the classify workflow, `classify_call.ClassifyAnswer`, says what the call did
with the operation, for a person who calls the workflow. The detector refuses a call, with an error, in two cases
only: it cannot read the operation, or its `failed` did not reach the store and the operation is
still `running`. Then the screwing resource ends the operation `failed` itself.

Its activity feed is on, and so is the middleware's state dashboard: the library's own boxes, and
the boxes "Classifications" and "Screw type values" in the tab "Anomaly detector", which the
dashboard opens on (`anomaly_detector_dashboard`). They show each classification with its outcome,
the rule of its exit and the ten values, and what each Screw type held at the
last read, from what the detector keeps in its memory as it classifies
(`anomaly_detector_memory`): an open dashboard reads no store. A classification goes into the
memory once its commit has reached the store.

With the mode "Save measurements as uncertainty quantifications" on (interim name; off by
default, on from the start with `--save-measurements-as-uq`), the same commit also writes a random
variable beside each measured position: see `AnomalyDetector`. The detector serves the mode on
one route of its own, `MODE_ROUTE` (interim name): a read gives it, a write sets it, and the next
classification reads it. Use case 1's page reaches the route through its passthrough, for its
switch.

Run it as `python -m jms_kapps.uc1.anomaly_detector --dataset <name>`. It starts and serves as
every participant's process does, through `common.process`, with the local InfluxDB unless
`INFLUXDB_URL` names another. `jms-uc1` starts it (`run_core`).
"""

from __future__ import annotations

import argparse
import logging
import math
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from kapps_ogm import OGM, ClassScope, Node, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from pydantic import BaseModel, StrictBool

from jms_kapps.common.process import process_parser, run_process
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.anomaly_detector_dashboard import TAB as DASHBOARD_TAB
from jms_kapps.uc1.anomaly_detector_dashboard import register_boxes
from jms_kapps.uc1.anomaly_detector_memory import (
    APPROACH_END,
    SCREW_HEAD,
    DetectorMemory,
    MeasuredPosition,
)
from jms_kapps.uc1.classify_call import ClassifyAnswer, ClassifyRequest
from jms_kapps.uc1.decision_tree import (
    AttemptSignals,
    DetectionResult,
    PhaseSignals,
    classify,
    contact_position,
)
from jms_kapps.uc1.effective_values import (
    EffectiveValuesWithOrigin,
    UnresolvedValues,
    as_properties,
    read_effective_values_with_origin,
)
from jms_kapps.uc1.frame import (
    AXIAL_FORCE,
    PHASES,
    ROBOT_POSITION,
    SIGNALS,
    UNSCREWING_TORQUE,
    Frame,
)
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.timeseries import FrameReader
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.uncertainty import measurement_literal
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PROV, UQ

# Named, not `__name__`: the process runs this module as `python -m`, where `__name__` is
# `__main__`. A logger of that name is not below `jms_kapps`, so its INFO lines would be
# dropped at the root's WARNING (`common.process` lets `jms_kapps` log from INFO up).
logger = logging.getLogger("jms_kapps.uc1.anomaly_detector")

# The anomaly detector of the demo, which `jms-uc1` starts.
RESOURCE = IRI("AnomalyDetector1", base=JMS_NS)

Commit = Callable[..., AbstractContextManager[Any]]
"""The middleware's `commit`."""

Create = Callable[..., AbstractContextManager[Any]]
"""The middleware's `create`."""

# What the detector reads of an unscrewing operation: its status and its result, the Screw it
# names, and where its curves are.
OPERATION_SCOPE = ClassScope.from_property_chains(
    [
        [SVC.hasOperationStatus],
        [SVC.hasExecutionResult],
        [JMS.concernsScrew],
        [JMS.hasTimeSeriesReference],
    ]
)

# What it reads of the operation with the mode "Save measurements as uncertainty
# quantifications" on: also the resource whose capability the operation implements, whose robot
# took the measurements, and the head-position variable that its Screw holds already. The resource
# is reached through the capability's workflow and that workflow's service, the links the
# middleware writes when the resource registers. The operation's `svc:executedByWorkflow` is no
# way to it: only the middleware's `claim_next()` writes that link, and the screwing resource
# commits its attempt with `mw.commit()`, so the store never holds it. One resource has the
# capability (the capability shape), so every chain ends at that one resource.
UQ_OPERATION_SCOPE = ClassScope.from_property_chains(
    [
        [SVC.hasOperationStatus],
        [SVC.hasExecutionResult],
        [JMS.concernsScrew],
        [JMS.hasTimeSeriesReference],
        [
            CFC.implementsCapability,
            SVC.realizedByWorkflow,
            SVC.isWorkflowOf,
            SVC.isServiceOf,
        ],
        [JMS.concernsScrew, JMS.hasScrewHeadPositionVariable],
    ]
)

# What it reads of that resource, under the resource's own class: the standard deviation of its
# robot's positions. The operation's read cannot reach it, because the mapper specifies the
# nested resource by the range of `svc:isServiceOf`, `cfc:Resource`, which has no `:hasRobot`.
ROBOT_SCOPE = ClassScope.from_property_chains(
    [[JMS.hasRobot, JMS.hasPositionStandardDeviation]]
)

# The option that turns the mode "Save measurements as uncertainty quantifications" on at the
# start (interim name), on the detector's process and on `jms-uc1`, which passes it on. Off
# without it.
SAVE_UQ_OPTION = "--save-measurements-as-uq"
SAVE_UQ_ARGUMENT: dict[str, Any] = {
    "action": "store_true",
    "help": (
        "Save measurements as uncertainty quantifications from the start: beside each measured "
        "position, a random variable with its Gaussian-mixture literal (off by default)"
    ),
}

# The route on which the detector serves the mode "Save measurements as uncertainty
# quantifications" (interim name): `GET` gives it, `PUT` sets it, as `SavesUq`.
MODE_ROUTE = "/save-measurements-as-uq"


class SavesUq(BaseModel):
    """The mode "Save measurements as uncertainty quantifications", as its route reads and sets
    it: on or off."""

    on: StrictBool


def mount_mode(app: FastAPI, memory: DetectorMemory) -> None:
    """Serve the mode that `memory` holds on `app` at `MODE_ROUTE`: `GET` gives it, `PUT` sets
    it and gives it as it now is. The next classification reads it."""

    @app.get(MODE_ROUTE, response_model=SavesUq)
    def mode() -> SavesUq:
        """Whether the detector saves measurements as uncertainty quantifications."""
        return SavesUq(on=memory.saves_uq)

    @app.put(MODE_ROUTE, response_model=SavesUq)
    def set_mode(wanted: SavesUq) -> SavesUq:
        """Turn the saving of measurements as uncertainty quantifications on or off, from the
        next classification."""
        memory.set_saves_uq(wanted.on)
        return SavesUq(on=memory.saves_uq)


# The exit of the tree that is no failure: it ends the operation `done`. Every other outcome is
# a failure outcome, and ends it `failed`.
NO_FAILURE: DetectionResult = "ScrewIntact"

# How the reason of an operation that the detector could not classify begins, in its
# `svc:hasExecutionResult`.
NOT_CLASSIFIED = "the anomaly detector could not classify the attempt: "

# How the reason of an operation that ended with a failure outcome begins, in its `svc:hasExecutionResult`,
# followed by the outcome's local name. Interim text, so that it is told apart from an
# attempt that was not classified, a detector that was not reached, and an attempt that failed.
ANOMALY = "ANOMALY: "


@dataclass(frozen=True)
class OperationToClassify:
    """One unscrewing operation, as the detector reads it: its status, its results, the Screws it
    names, and its time-series references. Read under `UQ_OPERATION_SCOPE`, also the resources
    whose capability it implements, each once, and the head-position variables its Screws hold;
    else none."""

    operation: IRI
    status: str | None
    results: tuple[str, ...]
    screws: tuple[IRI, ...]
    references: tuple[str, ...]
    resources: tuple[IRI, ...] = ()
    head_position_variables: tuple[IRI, ...] = ()


def operation_to_classify_from(node: Node) -> OperationToClassify:
    """The operation that a read under `OPERATION_SCOPE` or `UQ_OPERATION_SCOPE` gives, as the
    detector takes it."""
    data = node.data or {}
    statuses = data.get(SVC.hasOperationStatus, [])
    screws = data.get(JMS.concernsScrew, [])
    return OperationToClassify(
        operation=IRI(str(node.id)),
        status=str(statuses[0]) if statuses else None,
        results=tuple(str(r) for r in data.get(SVC.hasExecutionResult, [])),
        screws=tuple(IRI(_id(screw)) for screw in screws),
        references=tuple(str(r) for r in data.get(JMS.hasTimeSeriesReference, [])),
        # A capability's workflow and service are written again at each registration, so a
        # resource that registered more than once is reached by more than one chain.
        resources=tuple(
            dict.fromkeys(
                IRI(_id(resource))
                for capability in data.get(CFC.implementsCapability, [])
                for workflow in _linked(capability, SVC.realizedByWorkflow)
                for service in _linked(workflow, SVC.isWorkflowOf)
                for resource in _linked(service, SVC.isServiceOf)
            )
        ),
        head_position_variables=tuple(
            IRI(_id(variable))
            for screw in screws
            for variable in _linked(screw, JMS.hasScrewHeadPositionVariable)
        ),
    )


def _linked(value: object, prop: IRI) -> list[Any]:
    """What a nested node of a read holds of `prop`; nothing for a bare link."""
    return list((getattr(value, "data", None) or {}).get(prop, []))


def _id(value: object) -> str:
    """The IRI of a linked value, which the mapper gives as a node with an `id`."""
    return str(getattr(value, "id", value))


class NotClassified(Exception):
    """The attempt of an operation cannot be classified. The message says why, for a person to
    read: it becomes the operation's result."""


class EndedMeanwhile(Exception):
    """The operation is no longer `running` when the detector would end it."""

    def __init__(self, status: str | None) -> None:
        self.status = status
        super().__init__(f"the operation is {status}, not running")


@dataclass
class _Ending:
    """How one classification ends: the outcome that it reached, and how its error path left the
    operation."""

    outcome: DetectionResult | None = None
    # Whether it was left as it is, because it was `status`, not `running`.
    left: bool = False
    status: str | None = None
    # Why it was committed `failed`.
    why: str | None = None
    # What it read of the Screw's effective values, once it has read them.
    with_origin: EffectiveValuesWithOrigin | None = None
    # The mode it is made in, and the measured positions it writes with their random variables.
    saves_uq: bool = False
    positions: tuple[MeasuredPosition, ...] = ()

    def leave(self, status: str | None) -> None:
        self.left, self.status = True, status


def attempt_signals(frame: Frame) -> AttemptSignals:
    """The signals of the decision tree, by phase, from a frame: each phase's samples, in time
    order. A sample with no phase belongs to none.

    Raises:
        NotClassified: If the frame lacks a signal that the tree reads, or has no phase at all.
    """
    missing = [signal for signal in SIGNALS if signal not in frame.signals]
    if missing:
        raise NotClassified(f"its frame has no {', '.join(missing)}")
    if not frame.phased:
        raise NotClassified("its frame was recorded without phases")
    approach, form_fit, preparation, disassembly = (
        _phase_signals(frame, phase) for phase in PHASES
    )
    return AttemptSignals(approach, form_fit, preparation, disassembly)


def _phase_signals(frame: Frame, phase: str) -> PhaseSignals:
    samples = [
        i for i, sample_phase in enumerate(frame.phases) if sample_phase == phase
    ]
    return PhaseSignals(
        axial_force=[frame.signals[AXIAL_FORCE][i] for i in samples],
        position=[frame.signals[ROBOT_POSITION][i] for i in samples],
        torque=[frame.signals[UNSCREWING_TORQUE][i] for i in samples],
    )


def approach_end_position(signals: AttemptSignals) -> float:
    """The measured position at which the approach ended: that of its last sample."""
    return signals.approach.position[-1]


# What the anomaly detector logs. The tests match each record whole, so it is built here.


def left_as_it_is_record(detector: IRI, operation: IRI, status: str | None) -> str:
    """What `detector` logs when it leaves `operation` as it is, because it is `status`, not
    `running`."""
    return (
        f"{detector.fragment}: {operation.fragment} is {status}, not running, so it is left as "
        "it is"
    )


class AnomalyDetector:
    """The anomaly detector, given the middleware's commit and create, its reads, and its
    memory.

    With the mode "Save measurements as uncertainty quantifications" on (`memory.saves_uq`, read
    at each classification), the classification's commit also writes beside each measured
    position a random variable (`uq:RandomVariable`) whose Gaussian-mixture literal holds that one
    measurement: the measured value as the mean, the squared standard deviation of the robot's
    positions as the variance. After a failure outcome, that is the end position of the approach,
    linked from the operation (`:hasApproachEndPositionVariable`). After every outcome that reached
    contact (all but MissingScrew), it is the position at contact, the Screw's
    `:hasMeasuredScrewHeadPosition`, linked from the Screw (`:hasScrewHeadPositionVariable`). A
    Screw that holds a head-position variable keeps it, and the new measurement replaces its
    literal and the best estimate. Nothing is fused, and no held literal is read. With the mode on,
    a robot without its standard deviation is an error of the body. With the mode off, it
    reads only under `OPERATION_SCOPE`, and writes no random variable and no head position.

    Args:
        detector: The anomaly detector's resource.
        commit: The middleware's `commit`.
        create: The middleware's `create`.
        read: Reads an unscrewing operation from the graph, under `OPERATION_SCOPE`; with
            `saves_uq=True`, under `UQ_OPERATION_SCOPE`.
        values: Reads the effective values of a Screw from the graph, afresh.
        frame: Reads the frame that a time-series reference names.
        clock: The time at which a classification ends, its `prov:endedAtTime`. Timezone-aware;
            UTC unless a test gives another.
        memory: Where it keeps what its dashboard shows: each classification once its commit
            has reached the store, and what each Screw type held at its last read; and its mode.
        standard_deviations: Reads the position standard deviations that the robot of a resource
            holds, with the mode on.
    """

    def __init__(
        self,
        detector: IRI,
        *,
        commit: Commit,
        create: Create,
        read: Callable[..., OperationToClassify],
        values: Callable[[IRI], EffectiveValuesWithOrigin],
        frame: Callable[[str], Frame],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        memory: DetectorMemory | None = None,
        standard_deviations: Callable[[IRI], Sequence[object]] = lambda resource: (),
    ) -> None:
        self.detector = IRI(detector)
        self.commit = commit
        self.create = create
        self.read = read
        self.values = values
        self.frame = frame
        self.clock = clock
        self.memory = memory if memory is not None else DetectorMemory()
        self.standard_deviations = standard_deviations

    def classify(self, operation: IRI) -> ClassifyAnswer:
        """The classify workflow: classify the attempt of `operation`, and end it, while it is
        `running`.

        Raises:
            Exception: Only when the operation cannot be read, or when the detector's `failed`
                did not reach the store and the operation is still `running`. Then the caller is
                told that the call failed.
        """
        saves_uq = self.memory.saves_uq
        found = (
            self.read(operation, saves_uq=True) if saves_uq else self.read(operation)
        )
        if found.status != OperationStatus.RUNNING:
            return self._left_as_it_is(operation, found.status)
        ending = _Ending(saves_uq=saves_uq)
        try:
            with self.commit(
                exit=lambda: self._ended_while_running(operation, ending),
                error=lambda error: self._failed_while_running(
                    operation, error, ending
                ),
            ) as transition:
                values, signals, ending.outcome = self._classify(found, ending)
                deviation = self._standard_deviation(found) if saves_uq else None
                log_scope(
                    logger,
                    "Anomaly detector",
                    "writes",
                    operation,
                    "an empty ClassScope",
                    ClassScope(),
                    "it creates a new Activity and a new ParameterCopy, and reads nothing "
                    "of them",
                )
                # The classification, the PROV witness on the decision. Its IRI
                # is given, because the middleware mints `<class>_<hex>`, which for a class of
                # PROV-O would be an IRI in the W3C's namespace.
                with self.create(
                    str(PROV.Activity),
                    ClassScope(),
                    instance_iri=str(_classification_iri()),
                ) as classification:
                    classification.update(
                        {
                            str(PROV.wasAssociatedWith): [_link(self.detector)],
                            str(PROV.endedAtTime): [self.clock()],
                        }
                    )
                with self.create(str(JMS.ParameterCopy), ClassScope()) as snapshot:
                    snapshot.update(
                        {
                            str(prop): [value]
                            for prop, value in as_properties(values.values).items()
                        }
                        | {
                            str(PROV.wasGeneratedBy): [
                                _link(IRI(str(classification.iri)))
                            ]
                        }
                    )
                classified: dict[str, list[Any]] = {
                    str(JMS.hasDetectionResult): [_link(outcome_iri(ending.outcome))],
                    str(JMS.classifiedWith): [_link(IRI(str(snapshot.iri)))],
                }
                positions: list[MeasuredPosition] = []
                if anomaly_of(ending.outcome) is not None:
                    end = approach_end_position(signals)
                    classified[str(JMS.hasMeasuredApproachEndPosition)] = [end]
                    if deviation is not None:
                        classified[str(JMS.hasApproachEndPositionVariable)] = [
                            _link(self._new_variable(end, deviation))
                        ]
                        positions.append(MeasuredPosition(APPROACH_END, end, deviation))
                if deviation is not None:
                    at_contact = contact_position(signals, values.values)
                    if at_contact is not None:
                        self._head_position(transition, found, at_contact, deviation)
                        positions.append(
                            MeasuredPosition(SCREW_HEAD, at_contact, deviation)
                        )
                ending.positions = tuple(positions)
                transition.update(operation, classified)
        except Exception as error:
            if ending.left:
                return self._left_as_it_is(operation, ending.status)
            if ending.why is None:
                raise
            answer = self._after_failed(operation, ending.why, error)
            if answer.ended == OperationStatus.FAILED:
                self.memory.not_classified(
                    operation,
                    found.screws[0] if len(found.screws) == 1 else None,
                    ending.with_origin,
                    ending.why,
                    saves_uq=saves_uq,
                )
            return answer
        assert ending.outcome is not None
        anomaly = anomaly_of(ending.outcome)
        self.memory.classified(
            operation,
            values,
            ending.outcome,
            anomaly,
            saves_uq=saves_uq,
            positions=ending.positions,
        )
        if anomaly is None:
            logger.info(
                "%s: %s is done, with the outcome %s",
                self.detector.fragment,
                operation.fragment,
                ending.outcome,
            )
        else:
            logger.warning(
                "%s: %s is failed, with the outcome %s: %s",
                self.detector.fragment,
                operation.fragment,
                ending.outcome,
                anomaly,
            )
        return ClassifyAnswer(
            operation=str(operation),
            ended=OperationStatus.DONE if anomaly is None else OperationStatus.FAILED,
            outcome=str(outcome_iri(ending.outcome)),
            why=anomaly,
        )

    def _standard_deviation(self, found: OperationToClassify) -> float:
        """The standard deviation of the positions of the robot that measured the attempt: the
        robot of the one resource whose capability the operation implements.

        Raises:
            NotClassified: If the operation names no such resource, or more than one, or its robot
                holds no standard deviation, more than one, or one that is not a finite number.
        """
        if len(found.resources) != 1:
            raise NotClassified(
                f"{found.operation.fragment} names {len(found.resources)} resources whose "
                "capability it implements; saving measurements as uncertainty quantifications "
                "needs the one whose robot measured it"
            )
        (resource,) = found.resources
        held = list(self.standard_deviations(resource))
        if len(held) != 1 or not _is_finite_number(held[0]):
            raise NotClassified(
                f"the robot of {resource.fragment} holds {held or 'no'} position standard "
                "deviation; saving measurements as uncertainty quantifications needs one, a "
                "finite number"
            )
        return float(held[0])  # type: ignore[arg-type]

    def _new_variable(self, value: float, deviation: float) -> IRI:
        """Create, in the commit, the random variable of one measured `value`."""
        with self.create(str(UQ.RandomVariable), ClassScope()) as variable:
            variable.update(
                {
                    str(UQ.hasDistribution): [measurement_literal(value, deviation)],
                    str(UQ.hasDomain): [_link(UQ.double)],
                }
            )
        return IRI(str(variable.iri))

    def _head_position(
        self,
        transition: Any,
        found: OperationToClassify,
        at_contact: float,
        deviation: float,
    ) -> None:
        """Write, in the commit, the measured head position of the operation's Screw and its
        random variable: a new one, or the one the Screw holds, with its literal replaced.

        Raises:
            NotClassified: If the Screw holds more than one head-position variable.
        """
        (screw,) = found.screws
        held = found.head_position_variables
        if len(held) > 1:
            raise NotClassified(
                f"{screw.fragment} holds {len(held)} head-position variables, not one"
            )
        estimate: dict[str, list[Any]] = {
            str(JMS.hasMeasuredScrewHeadPosition): [at_contact]
        }
        if held:
            transition.update(
                held[0],
                {str(UQ.hasDistribution): [measurement_literal(at_contact, deviation)]},
            )
        else:
            estimate[str(JMS.hasScrewHeadPositionVariable)] = [
                _link(self._new_variable(at_contact, deviation))
            ]
        transition.update(screw, estimate)

    def _after_failed(
        self, operation: IRI, why: str, error: Exception
    ) -> ClassifyAnswer:
        """The answer after the error path asked for `failed` with `why`.

        When the error transition is not committed, the middleware logs that and raises the
        error that ended the commit, with a note. So the error alone does not say whether
        `failed` reached the store, and the detector reads the operation once more. It holds the
        `failed` with `why`; or it has ended otherwise, and is left as it is; or it is still
        `running`.

        Raises:
            Exception: `error` again, if the operation is still `running`. The call is then
                refused, and the screwing resource ends the operation `failed` itself.
        """
        found = self.read(operation)
        if found.status == OperationStatus.FAILED and why in found.results:
            logger.warning(
                "%s: %s is failed: %s", self.detector.fragment, operation.fragment, why
            )
            return ClassifyAnswer(
                operation=str(operation), ended=OperationStatus.FAILED, why=why
            )
        if found.status != OperationStatus.RUNNING:
            return self._left_as_it_is(operation, found.status)
        raise error

    def _ended_while_running(self, operation: IRI, ending: _Ending) -> list[Update]:
        """The exit transition, if the operation is still `running`: `done` after the outcome
        ScrewIntact, and `failed`, naming it, after any other outcome that `ending` holds.

        That reason is not kept in `ending.why`, which says that the error path asked for
        `failed`: the `failed` of a failure outcome is the clean exit.

        Raises:
            EndedMeanwhile: If it is no longer `running`. Then nothing of the body is written.
        """
        status = self.read(operation).status
        if status != OperationStatus.RUNNING:
            raise EndedMeanwhile(status)
        assert ending.outcome is not None
        anomaly = anomaly_of(ending.outcome)
        if anomaly is None:
            return [_status(operation, OperationStatus.DONE)]
        return [_failed(operation, anomaly)]

    def _failed_while_running(
        self, operation: IRI, error: BaseException, ending: _Ending
    ) -> list[Update]:
        """The error transition: `failed` with why, if the operation is still `running`, and
        nothing otherwise. `ending` keeps which of the two it was."""
        if isinstance(error, EndedMeanwhile):
            ending.leave(error.status)
            return []
        status = self.read(operation).status
        if status != OperationStatus.RUNNING:
            ending.leave(status)
            return []
        ending.why = NOT_CLASSIFIED + _text_of(error)
        return [_failed(operation, ending.why)]

    def _left_as_it_is(self, operation: IRI, status: str | None) -> ClassifyAnswer:
        """Log that `operation`, which is `status`, is left as it is, and answer so."""
        logger.warning("%s", left_as_it_is_record(self.detector, operation, status))
        return ClassifyAnswer(
            operation=str(operation), why=f"it is {status}, not running"
        )

    def _classify(
        self, found: OperationToClassify, ending: _Ending
    ) -> tuple[EffectiveValuesWithOrigin, AttemptSignals, DetectionResult]:
        """The effective values of the operation's Screw, the signals of its frame, and the exit
        of the tree they reach. Nothing is written to the store. The values go into `ending`
        and, with what the Screw type holds, into the memory, as soon as they are read.

        Raises:
            NotClassified: With why, when the attempt cannot be classified.
        """
        screw = _the_one(found.screws, found.operation, "Screws")
        reference = _the_one(
            found.references, found.operation, "time-series references"
        )
        try:
            values = self.values(screw)
        except UnresolvedValues as error:
            raise NotClassified(str(error)) from error
        except Exception as error:
            raise NotClassified(
                f"the effective values of {screw.fragment} could not be read: {_text_of(error)}"
            ) from error
        ending.with_origin = values
        self.memory.read(values)
        try:
            frame = self.frame(reference)
        except Exception as error:
            raise NotClassified(
                f"its frame could not be read from {reference}: {_text_of(error)}"
            ) from error
        signals = attempt_signals(frame)
        try:
            return values, signals, classify(signals, values.values)
        except ValueError as error:
            raise NotClassified(
                f"the decision tree refused its curves: {error}"
            ) from error


def read_operation(
    middleware: SemanticMiddleware, operation: IRI, *, saves_uq: bool = False
) -> OperationToClassify:
    """Read the operation from the graph under `OPERATION_SCOPE`, or with `saves_uq` under
    `UQ_OPERATION_SCOPE`, logging what the detector wants. The detector's own read."""
    wants = (
        "it wants the status (hasOperationStatus), the reason of a failure "
        "(hasExecutionResult), the Screw (concernsScrew) and where its curves are "
        "(hasTimeSeriesReference)"
    )
    if saves_uq:
        name, scope = "UQ_OPERATION_SCOPE", UQ_OPERATION_SCOPE
        wants += (
            ", and to save measurements as uncertainty quantifications the resource "
            "whose capability it implements (implementsCapability, realizedByWorkflow, "
            "isWorkflowOf, isServiceOf) and the Screw's head-position variable "
            "(hasScrewHeadPositionVariable)"
        )
    else:
        name, scope = "OPERATION_SCOPE", OPERATION_SCOPE
    log_scope(logger, "Anomaly detector", "reads", operation, name, scope, wants)
    return operation_to_classify_from(middleware.fetch(operation, class_scope=scope))


def build(
    resource_iri: str,
    ogm: OGM,
    host: str,
    port: int,
    *,
    reader: FrameReader | None = None,
    saves_uq: bool = False,
) -> SemanticMiddleware:
    """The middleware instance of the anomaly detector `resource_iri`, served at `host:port`,
    with its classify workflow, its notification handler and its dashboard: the middleware's
    boxes, and its own two in the tab "Anomaly detector", which it opens on; and its mode route
    (`MODE_ROUTE`).

    Args:
        reader: Where the frames are read from. Defaults to the InfluxDB that `INFLUXDB_URL`
            names.
        saves_uq: The start value of the mode "Save measurements as uncertainty
            quantifications". The mode route changes it while the detector runs.
    """
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=resource_iri,
        service_class=str(JMS.AnomalyDetectorService),
        ogm=ogm,
        host=host,
        port=port,
        activity_feed=True,
    )
    # The middleware's state dashboard at `/dashboard`: the library's boxes, and the detector's
    # own, which render its memory.
    memory = DetectorMemory(saves_uq=saves_uq)
    register_boxes(enable_dashboard(middleware, first_tab=DASHBOARD_TAB), memory)
    frames = reader if reader is not None else FrameReader(connect_influxdb())

    def read(operation: IRI, *, saves_uq: bool = False) -> OperationToClassify:
        return read_operation(middleware, operation, saves_uq=saves_uq)

    def standard_deviations(resource: IRI) -> list[object]:
        """Read the position standard deviations of the robot of `resource`."""
        log_scope(
            logger,
            "Anomaly detector",
            "reads",
            resource,
            "ROBOT_SCOPE",
            ROBOT_SCOPE,
            "it wants the standard deviation of the robot's positions (hasRobot, "
            "hasPositionStandardDeviation), to save measurements as uncertainty "
            "quantifications",
        )
        data = middleware.fetch(resource, class_scope=ROBOT_SCOPE).data or {}
        return [
            value
            for robot in data.get(JMS.hasRobot, [])
            for value in _linked(robot, JMS.hasPositionStandardDeviation)
        ]

    detector = AnomalyDetector(
        IRI(resource_iri),
        commit=middleware.commit,
        create=middleware.create,
        read=read,
        standard_deviations=standard_deviations,
        values=lambda screw: read_effective_values_with_origin(ogm, screw),
        frame=frames.read,
        memory=memory,
    )

    @middleware.workflow(
        capability_class=JMS.ClassifyCapability,
        workflow_class=JMS.ClassifyWorkflow,
    )
    def classify(request: ClassifyRequest) -> ClassifyAnswer:
        """Classify the recorded attempt of one running unscrewing operation, and end the
        operation with its outcome and its parameter copy: `done` for an intact screw, or
        `failed` naming its failure outcome; or `failed` with why it could not be classified. An
        operation that is not running is left as it is."""
        return detector.classify(IRI(request.operation))

    # A notification that reaches the detector all the same: it takes no Operation from a
    # notification, only from a call. The handler also keeps the middleware from warning at each
    # start that the instance has neither a notification handler nor a callback.
    def not_taken(operation: Node) -> None:
        logger.warning(
            "%s: notified of %s; the detector takes an operation only from a call of its "
            "classify workflow, so nothing is done",
            IRI(resource_iri).fragment,
            IRI(str(operation.id)).fragment,
        )

    middleware.on_notification(not_taken)
    mount_mode(middleware.app, memory)
    return middleware


def _is_finite_number(value: object) -> bool:
    """Whether `value` is an int, a float or a Decimal, and finite. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    return math.isfinite(value)


def _the_one(items: Sequence[Any], operation: IRI, what: str) -> Any:
    """The one item that the operation names.

    Raises:
        NotClassified: If it names none, or more than one.
    """
    if len(items) != 1:
        raise NotClassified(
            f"{operation.fragment} names {len(items)} {what}; an unscrewing operation names one"
        )
    return items[0]


def anomaly_of(outcome: DetectionResult) -> str | None:
    """The reason that names the failure outcome `outcome`, for example
    `ANOMALY: MissingScrew`, or None for the outcome ScrewIntact, which is no failure."""
    if outcome == NO_FAILURE:
        return None
    return ANOMALY + outcome


def outcome_iri(outcome: DetectionResult) -> IRI:
    """The individual of the use case's ontology that names the tree's exit `outcome`."""
    return IRI(outcome, base=JMS_NS)


def _classification_iri() -> IRI:
    """A new IRI for one classification, in use case 1's namespace."""
    return IRI(f"Classification_{uuid.uuid4().hex[:12]}", base=JMS_NS)


def _link(iri: IRI) -> dict[str, str]:
    """A link to `iri`, as the object-graph mapper takes it."""
    return {"id": str(iri)}


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


def _text_of(error: BaseException) -> str:
    """What `error` says, or its type when it says nothing."""
    return str(error) or repr(error)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The options of the detector's process."""
    parser = process_parser("The anomaly detector of use case 1")
    parser.add_argument(
        "--resource",
        default=str(RESOURCE),
        help="The IRI of the anomaly detector this process serves",
    )
    parser.add_argument(SAVE_UQ_OPTION, **SAVE_UQ_ARGUMENT)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    with connect_influxdb() as influxdb:
        run_process(
            args.host,
            lambda port: build(
                args.resource,
                OGM(db=connect(args.dataset)),
                args.host,
                port,
                reader=FrameReader(influxdb),
                saves_uq=args.save_measurements_as_uq,
            ),
            lambda address: (
                f"The anomaly detector runs on {address}. Its dashboard: {address}dashboard"
            ),
        )


if __name__ == "__main__":
    main()
