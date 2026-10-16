"""What passes between two parties in a handover, and the handover itself.

Inside the FlexConveyor station, one hop works like the real FlexConveyor. The giving module asks
the receiving module to **reserve** itself for the box, and the receiving module consents in its
memory and answers at once. Once it has accepted, the giving module opens the middleware's
handover: its entry commits the change of possession, so the store admits or refuses it before
anything moves, and the same commit assigns the box's transport to the receiving module. Only then
does the giving module **convey** the box, its own move and no workflow, and call the receiving
module's **receive**, the receiving module's light barrier, which never refuses: the possession
has already changed. The receiving module answers, then reads the box with its transport and
carries the box on. Reserve and Receive are each one blocking call to the peer's workflow, and each
answers with a step result. A peer that the graph names no live workflow of, or whose call is
refused, fails or times out, gives no answer: `ask` raises `PeerUnreachable`, whose message says
why. A module leaves such a peer out for the box, with that message in its warning, and chooses
again; the reason a failed transport records then names the destination and each module left out
(`no_live_route`). The WarehouseManagementSystem's reasons keep the message. (Changed on
2026-10-11, jms_kapps 77, decided by Etienne: a crash is routed around. Until then the sentence
ended "whose message is the reason that a failed transport records.")

(Changed on 2026-10-06, jms_kapps 40, decided by Etienne: the hop is matched to the real
conveyor, whose receiving module's light barrier the Receive abstracts. Until then the receiving
module called the giving module's Convey once it had accepted, the paragraph said "Convey opens
the middleware's handover", and it ended: "Each step is one blocking call to the peer's workflow,
and each answers with a step result. Then the giving module notifies the receiving module of the
transport, and the receiving module carries the box on.")

The WarehouseManagementSystem's handover onto the station is its spawn, without the middleware's
handover: it asks the origin's Reserve, creates the box directly on the origin, and notifies the
origin, whose intake carries the box on as after a Receive. At the end, the destination hands the box back without the questions:
the WarehouseManagementSystem answers no Reserve, and the store's admission is the only check
there. `Giver.hand_over` is the handover without the questions: every hop opens it, and the last
handover, back to the WarehouseManagementSystem, is nothing more. (Changed on 2026-10-06, jms_kapps
41, decided by Etienne. Until then the WarehouseManagementSystem's handover to the origin "keeps
the handshake it had, with the WarehouseManagementSystem as the giver: it asks the origin's
Reserve, the origin calls its Convey, and its Convey calls the origin's Receive", and its Convey
opened `Giver.hand_over` too.)

(Changed on 2026-10-05, KSM 413, decided by Etienne. Until then this paragraph read: "The
WarehouseManagementSystem hands a box over too, onto the station and, at the end, a module hands it
back. It calls no workflow of a module, and no module calls one of it: the store's admission is
the only check at the level boundary. So `Giver.hand_over` is the handover without the questions,
and both use it.")

The step result is this use case's own, not the middleware's: the middleware's workflow contract
is arguments in, return value out, with no envelope. Status, time and progress are not in it,
because the graph carries them.

The store's refusal of a write is a type: the triplestore interface raises `ShaclValidationError`,
which carries the store's validation report verbatim, and the middleware and the object-graph
mapper pass it on unchanged. Every write a party makes catches that type, and only that type. Any
other error of the store, such as a dataset that does not exist, is raised.
"""

from __future__ import annotations

import logging
import typing
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

import httpx
from kapps_semantic_middleware.errors import HandoverPreconditionError
from kapps_semantic_middleware.vocabulary import MES
from kapps_triplestore_interface import IRI, ShaclValidationError
from pydantic import BaseModel

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.validation_report import rules_clause
from jms_kapps.uc2.vocabulary import FC, FCI_NS


class StepResult(BaseModel):
    """A party's answer to one handshake step, or what became of one handover.

    `report` is set only when the triple store refused a write, and is then its validation report,
    verbatim. So a caller tells the store's refusal from every other refusal by
    `result.report is not None`, never by `reason`, which is prose for a person to read.
    """

    accepted: bool
    reason: str = ""
    report: str | None = None


class ReserveRequest(BaseModel):
    """A party's ask that a module reserve itself for `box`, which `source_module` asks.

    `destination` is the box's destination, which the module looks ahead by: it accepts only if it
    is free and either it is the destination, or its own next module accepts a plain Reserve for
    the box at once. `plain` marks that inner ask, which looks no further ahead and is answered
    at once. `box_at` names where the box is when the asker does not hold it: the giving module of
    the look-ahead that asks, or the WarehouseManagementSystem for the box of a spawn. A Reserve
    that names no destination is plain. (Added on 2026-10-06, jms_kapps 43, decided by Etienne:
    "look-ahead acceptance". Until then a Reserve named the box and the asker only.)
    """

    box: str
    source_module: str
    destination: str | None = None
    plain: bool = False
    box_at: str | None = None


class ReceiveRequest(BaseModel):
    box: str
    source_module: str


Refusal = typing.Literal["occupied", "reserved", "behind", "blocked"]
"""Why a module refused to reserve itself, when the refusal passes: it holds a box (`occupied`), it
has reserved itself for another box (`reserved`), a box that has waited longer comes first
(`behind`), or its own next module refused the box (`blocked`). A giver tries its detour, and then
asks such a module again. (Changed on 2026-10-06, jms_kapps 43, decided by Etienne: longest-waiting
first, and look-ahead acceptance. Until then the refusals were `occupied` and `reserved`, and "A
giver asks such a module again.")"""

NoLiveRouteOn = typing.Literal["no live route on"]
"""The one named refusal of Reserve that does not pass: the module's look-ahead left out every next
module its strategy named, since none could be reached. A giver treats the module like one that
is not reachable: it leaves it out for the box and chooses again. (Added on 2026-10-11, jms_kapps
77, decided by Etienne: "it refuses with a new refusal, "no live route on"".)"""

NO_LIVE_ROUTE_ON: NoLiveRouteOn = "no live route on"


def passes(refusal: str | None) -> typing.TypeGuard[Refusal]:
    """Whether `refusal` is one that passes, after which a giver asks again: `occupied`,
    `reserved`, `behind` or `blocked`. "no live route on" does not pass, and neither does a
    refusal that names none."""
    return refusal in typing.get_args(Refusal)


class ReserveResult(StepResult):
    """A module's answer to Reserve. `refusal` says why it refused, when the refusal passes or is
    "no live route on", and is None for an acceptance and for any other refusal. (Changed on
    2026-10-11, jms_kapps 77, decided by Etienne: the refusal "no live route on". Until then
    "`refusal` says why it refused, when the refusal passes".)"""

    refusal: Refusal | NoLiveRouteOn | None = None


class ReceiveResult(StepResult):
    pass


def acceptance_record(resource: IRI, step: IRI, box: IRI, source: IRI) -> str:
    """What `resource` logs when it accepts `step` for `box` from `source`.

    The record is the party's own evidence that the step ran on it. The vertical slice and the
    tests match it whole, so every party builds it here: a record with another verb or from
    another giver is no evidence of this step.
    """
    return f"{resource.fragment}: {step.fragment} accepted {box.fragment} from {source.fragment}"


def reserved_record(resource: IRI, receiver: IRI, box: IRI) -> str:
    """What the giver `resource` logs when `receiver` has accepted its Reserve for `box`: the
    giving side of the step, as `acceptance_record` is the receiving side."""
    return (
        f"{resource.fragment}: {receiver.fragment}'s {FC.ReserveWorkflow.fragment} accepted "
        f"{box.fragment}"
    )


def conveyed_record(resource: IRI, box: IRI, receiver: IRI) -> str:
    """What the giver `resource` logs once it has conveyed `box` to `receiver`, which had reserved
    itself for it: the store admitted the handover, the box has moved, and the giver has called
    `receiver`'s Receive. (Changed on 2026-10-06, jms_kapps 40, decided by Etienne: a module's
    Convey is its own move, no workflow. Until then the record read "Module1: ConveyWorkflow
    handed Box1 to Module2".)"""
    return f"{resource.fragment}: conveyed {box.fragment} to {receiver.fragment}"


# Prose for a person: a caller tells the store's refusal by the step result's report.
_STORE_REFUSAL = "the triple store refused the handover"

# Why a party refuses a step, a spawn among them, or ends a transport, once the stop of its process
# has begun.
STOPPING = "the system is stopping"

MiddlewareHandover = Callable[..., AbstractContextManager]
"""The middleware's `handover(mode=, workpiece=, counterpart=, operation=, entry=)`: the part of a
handover from the commit on. Its entry commits the change of possession; its body, which
`Giver.hand_over` runs in it, is the move and the arrival. A consent, a Reserve, comes before it.
(Renamed on 2026-10-10, jms_kapps 16, from `Handover`, which names the whole handover here.)"""

Call = Callable[[str, BaseModel], dict]
"""A blocking call to a peer's workflow endpoint, returning the decoded answer.

It raises an `httpx.HTTPError` when the call fails: the peer refuses the connection, does not
answer in time, or answers with an error status."""

Move = Callable[[IRI, IRI], None]
"""The move of a box onto a receiver: `move(box, receiver)` returns once the box is there.

The simulated one waits the transit time. A real FlexConveyor passes one that drives its motor.
"""

Notify = Callable[[IRI, IRI], None]
"""The middleware's notification: `notify(operation, resource)` tells `resource` to look at
`operation` in the graph. Raises `NotificationUndelivered` when it does not arrive."""


class Endpoints(Protocol):
    """Where a party finds a peer's workflow. `discovery.GraphView` is the real one."""

    def workflow_endpoint(self, resource: IRI, workflow_class: IRI) -> str | None: ...


def not_reachable(peer: IRI, error: Exception) -> str:
    """The reason a failed transport records when a party could not reach `peer`, because of
    `error`: "Module3 is not reachable: ..." and why, on one line, as `readable_reason` says it."""
    return _not_reachable(peer, readable_reason(error))


def lost_giver(giver: IRI, box: IRI, bound: float) -> str:
    """The reason a receiving module records when `giver` passed it `box` and did not call its
    Receive within `bound` seconds of its answer to Reserve: one line that names the giver."""
    return (
        f"{giver.fragment} is lost: it did not call {FC.ReceiveWorkflow.fragment} for "
        f"{box.fragment} within {bound:g} s"
    )


def _not_reachable(peer: IRI, why: str) -> str:
    return f"{peer.fragment} is not reachable: {why}"


def listed(names: Sequence[str]) -> str:
    """`names` as a person lists them: "Module3", "Module3 and Module1", "Module3, Module1 and
    Module5"."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def no_live_route(destination: IRI, left_out: Sequence[IRI]) -> str:
    """Why a module cannot carry a box on toward `destination`, nor look ahead for it, once it has
    left out each module of `left_out`, in the order it left them out, and no neighbour is left to
    ask: "no live route to Module9: Module3 and Module1 are not reachable". A module that answered
    "no live route on" is named as one that is not reachable, since the module treats it so.
    (Added on 2026-10-11, jms_kapps 77, decided by Etienne.)"""
    verb = "is" if len(left_out) == 1 else "are"
    names = listed([module.fragment for module in left_out])
    return f"no live route to {destination.fragment}: {names} {verb} not reachable"


class PeerUnreachable(Exception):
    """A party could not reach `peer`. The message says why, on one line: "Module3 is not
    reachable: ..." and why. A module puts it in the warning that leaves the peer out; the
    WarehouseManagementSystem's reasons keep it. (Changed on 2026-10-11, jms_kapps 77, decided by
    Etienne. Until then "The message is the reason a failed transport records".)"""

    def __init__(self, peer: IRI, why: str) -> None:
        super().__init__(_not_reachable(peer, why))
        self.peer = peer


def readable_reason(error: Exception) -> str:
    """Why `error` happened, as a person reads it, on one line.

    It is the error's cause when it has one, as the middleware's failed notification has the
    failed post, and the first line of its message: a failed transport's result keeps the
    store's report on the lines after its reason, and an error status's message has a second
    line with a link. The use case's own IRIs read as their names.
    """
    cause = error.__cause__ if isinstance(error.__cause__, Exception) else error
    lines = str(cause).replace(FCI_NS, "").splitlines()
    return lines[0] if lines else type(cause).__name__


def ask[Answer: StepResult](
    endpoints: Endpoints,
    call: Call,
    peer: IRI,
    workflow_class: IRI,
    request: BaseModel,
    result_type: typing.Type[Answer],  # noqa: UP006 - the docs resolve `type` to two attributes
) -> Answer:
    """Call `peer`'s workflow of `workflow_class`, found in the graph, and wait for its answer.

    Raises:
        PeerUnreachable: If the graph names no live workflow of that class of `peer`, or the
            call failed.
    """
    endpoint = endpoints.workflow_endpoint(peer, workflow_class)
    if endpoint is None:
        raise PeerUnreachable(
            peer, f"the graph names no live {workflow_class.fragment} of it"
        )
    try:
        answer = call(endpoint, request)
    except httpx.HTTPError as error:
        raise PeerUnreachable(peer, readable_reason(error)) from error
    return result_type.model_validate(answer)


class CapabilityReads(Protocol):
    """Where a party finds a module's capability. `discovery.GraphView` is the real one."""

    def capability(self, resource: IRI, capability_class: IRI) -> IRI | None: ...


class BoxTransportCapabilities:
    """Each module's BoxTransportCapability, read from the graph once: it does not change.

    The middleware writes it when the module registers its BoxTransportWorkflow, and keeps it
    when the module stops. A module that has none yet is asked again next time.
    """

    def __init__(self, graph: CapabilityReads) -> None:
        self._graph = graph
        self._known: dict[IRI, IRI] = {}

    def of(self, module: IRI) -> IRI | None:
        """The BoxTransportCapability of `module`, or None if it has never registered one."""
        if module not in self._known:
            found = self._graph.capability(module, FC.BoxTransportCapability)
            if found is None:
                return None
            self._known[module] = found
        return self._known[module]


def no_box_transport_capability(module: IRI) -> str:
    """Why nothing can be carried on by `module`: it offers no BoxTransportCapability."""
    return (
        f"{module.fragment} offers no BoxTransportCapability: it has never registered its "
        "BoxTransportWorkflow"
    )


@dataclass(frozen=True)
class Giver:
    """The giving side of every handover that the party `resource` makes: its middleware's
    handover, its move, the logger its refusals go to, and its handover event stream, if it has
    one."""

    handover: MiddlewareHandover
    resource: IRI
    move: Move
    logger: logging.Logger
    events: HandoverEvents | None = None

    def hand_over(
        self,
        box: IRI,
        receiver: IRI,
        *,
        on_refusal: str,
        operation: IRI | None = None,
        transport: IRI | None = None,
        entry: Sequence[object] = (),
        arrived: Callable[[], object] = lambda: None,
    ) -> StepResult:
        """Hand `box` to `receiver`: the possession changes first, then the box moves.

        The middleware's handover commits the change of possession in its entry, together with
        the changes of `entry`, and `operation` becomes `done` in the same commit when it is
        given. If the store refuses the commit, nothing is written and nothing moves: the refusal
        is logged with its report, emitted as `commit_refused` on the party's handover event
        stream, and the answer carries both. A failed precondition of the handover is refused the
        same way, before any write, and emits nothing: the store refused nothing. Otherwise the
        body runs: the move, then `arrived`, which calls the receiver's Receive.

        An error of the move, or of `arrived`, is raised as it is: the possession has changed by
        then, so the caller says what happened. A module writes nothing when `arrived` cannot
        reach the receiver, since the transport is the receiver's by then; what the station
        records after the move itself failed is not decided yet. (Changed on 2026-10-06,
        jms_kapps 42, decided by Etienne. Until then "the caller records what happened. A module
        records the transport `failed` when `arrived` cannot reach the receiver".) (Changed on
        2026-10-06, jms_kapps 41, decided by Etienne. Until then "A module, or the
        WarehouseManagementSystem in its Convey, records".)

        Only a `ShaclValidationError` is the store's refusal. Any other error of the store, such as
        a dataset that does not exist, is raised.

        Args:
            on_refusal: What the giver does next if the store refuses. The log line of the
                refusal says it.
            transport: The transport of `box`, which an event of the refusal names.
        """
        try:
            with self.handover(
                mode=MES.Pass,
                workpiece=box,
                counterpart=receiver,
                operation=operation,
                entry=list(entry),
            ):
                self.move(box, receiver)
                arrived()
        except HandoverPreconditionError as error:
            return StepResult(accepted=False, reason=str(error))
        except ShaclValidationError as refused:
            self.logger.warning(
                "%s: the triple store refused handing %s to %s%s; %s:\n%s",
                self.resource.fragment,
                box.fragment,
                receiver.fragment,
                rules_clause(refused.report),
                on_refusal,
                refused.report,
            )
            if self.events is not None:
                self.events.commit_refused(
                    box=box,
                    peer=receiver,
                    transport=transport,
                    reason=_STORE_REFUSAL,
                    report=refused.report,
                )
            return StepResult(
                accepted=False, reason=_STORE_REFUSAL, report=refused.report
            )
        return StepResult(accepted=True)
