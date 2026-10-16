"""One FlexConveyorModule's part in a box's journey: what it answers, how it carries a box on, and
how it gives the box back.

**A transport is assigned to one module at a time**, the one that carries the box on now, and
the assigned module also possesses the box. The WarehouseManagementSystem creates the box on the
origin with its transport, assigned to the origin, and notifies the origin. Every later module
gets the box and the transport in the handover of the module before it, and goes on once that
module calls its Receive: then it reads the box with its transport, through the box's link to it
(`fc:hasTransport`). (Changed on 2026-10-06, jms_kapps 41, decided by Etienne. Until then the
WarehouseManagementSystem "opens the transport assigned to the origin, hands the box to it and
notifies it".)

**The BoxTransportWorkflow** carries a transport on while the transport is assigned to the module.
At the origin, the WarehouseManagementSystem's notification of the transport starts it: the
middleware's intake hands the module the transport, read once. At every later module, the
module's Receive starts it, with the transport it read through the box. It reads the station's
connections at every decision, in one read, the WarehouseManagementSystem and the next module's
BoxTransportCapability once, and a peer's workflow endpoint at every call. It acts only when its
module is the assigned module, which also holds the box: the two move in one commit, and each
handover checks the possession again before it commits. At the origin it commits the `queued`
transport `running`; the store refuses that while the box is not on the module, and then the
transport stays `queued` (changed on 2026-10-06, jms_kapps 41, decided by Etienne: the
WarehouseManagementSystem creates the box on the origin before it notifies the origin, so this
happens only to a box that is elsewhere; until then "not on the module yet"). At a later module it goes on with the `running` one. A transport that
is done or has failed is left alone: a failed transport stays failed. Where the module is the
destination, it gives the box back to the WarehouseManagementSystem: one handover whose entry
makes the WarehouseManagementSystem the possessor and the transport `done` in one commit, then
the move, then a notification of the WarehouseManagementSystem. Otherwise it hops to the first
neighbour that its module strategy names (`strategies`). With `real`, the default, that is the
next module on a shortest path to the destination, by hop count over the connections, a tie going
to the lowest IRI, and never straight back to the module the box came from (the build's reading),
unless a crash has left a neighbour out for the box (below); a box goes on to the module that its
module reserved ahead for it, while a connection still leads there. No module
routes by a table of routes or of neighbours, and nothing routes centrally; the connections it
read last it keeps for its dashboard only. (Changed on 2026-10-11, jms_kapps 77, decided by
Etienne: "unless a crash has left a neighbour out for the box" was added.) (Changed on 2026-10-06,
jms_kapps 43, decided by
Etienne: "the box then goes to the module reserved ahead". Until then the hop's sentence ended
"a tie going to the lowest IRI.") (Changed on 2026-10-10, jms_kapps 52, decided by Etienne on
2026-10-07: a strategy chooses, `real` by default. Until then: "Otherwise it hops to the next
module on a shortest path to the destination, by hop count over the connections, a tie going to
the lowest IRI, and never straight back to the module the box came from (the build's reading); a
box goes on to the module that its module reserved ahead for it, while a connection still leads
there.")

**One hop works like the real FlexConveyor.** The giving module asks the receiving module's Reserve.
A module reserves itself only while it holds nothing, has promised itself to no other box, and no
box that has waited longer still asks for it, in its memory, and answers at once. Its read of what
it holds and its reservation are one step, which a Receive waits for: otherwise a Reserve that read
an empty module could be accepted just after the box it was reserved for arrived. The graph still
refuses a second box on a module, whatever the modules believed. A module that refuses with a
refusal that passes says so in its answer (`occupied`, `reserved`, `behind` or `blocked`), and the
giving module asks its detour once, and then waits the retry time and asks again, with no bound,
holding the box meanwhile. Any other refusal ends the transport, once the detour has refused too,
but "no live route on", the refusal of a module whose look-ahead found no live module left: the
giving module leaves that module out, as one it cannot reach (below). (Changed on 2026-10-11,
jms_kapps 77, decided by Etienne: "no live route on". Until then the sentence ended "once the
detour has refused too.") Once the receiving module has accepted, the giving module opens the
middleware's handover: its entry commits the change of possession and assigns the transport to
the receiving module, and the store admits or refuses both together, before anything moves. Only
once the store has admitted it does the giving module convey the box: its own move, a function the module is given, and no workflow that a
module exposes. Then it calls the receiving module's Receive, the receiving module's light barrier,
which never refuses, because the possession has already changed. The receiving module answers, and
then, as its own work, reads the box with its transport and carries the box on. So a hop is two
calls between the modules, Reserve and Receive, and one commit, and no notification passes between
them, and no module polls the graph for a box it is about to receive. The receiving module logs each
step it accepts, so its own log shows that the step ran there. The giving module logs its side, at
INFO too: the receiving module's answer to its Reserve, and its convey once the box has gone. So
each module's activity page shows the hop from both ends. (Changed on 2026-10-06, jms_kapps 43,
decided by Etienne. Until then: "A module reserves itself only while it holds nothing and has
promised itself to no other box" and "A module that refuses as occupied or reserved says so in its
answer, and the giving module waits the retry time and asks again, with no bound, holding the box
meanwhile.")

**Routing keeps boxes flowing** (decided by Etienne on 2026-10-06, jms_kapps 43):

- **Look-ahead acceptance.** A module accepts a Reserve only if it is free and either it is the
  box's destination, or its own next module accepts a plain Reserve for the box at once; that
  inner ask looks no further ahead. Otherwise it refuses at once, as `blocked`, and keeps no
  reservation. The box then goes on to the module reserved ahead, whose reservation is bound by
  `ARRIVAL_BOUND_SECONDS` and two times T (`ahead_bound`), T the longest transit time of any
  module. So a box moves only onto a module from which it can move on at once, and no module fills
  with a box that cannot. **It routes around a crash too:** a next module it cannot reach it leaves
  out for the box, and it looks ahead to the next one its strategy names, the module that holds
  the box still excluded; where no live module is left, it refuses "no live route on", which does
  not pass, and the asker leaves it out as one it cannot reach. (Added on 2026-10-11, jms_kapps
  77, decided by Etienne.)
- **One detour before waiting.** When the next module declines, the giving module asks one other
  neighbour, the second its module strategy names; with `real` (`routing.detour`), not the module
  the box came from, and one from which the destination is reached without coming back, so the
  module the next module ties with (changed on 2026-10-10, jms_kapps 52, decided by Etienne on
  2026-10-07: a strategy chooses, `real` by default; until then "asks one other neighbour
  (`routing.detour`): not the module the box came from, and one from which the destination is
  reached without coming back. The module the next module ties with is the detour."). Only if it declines too does the module wait and ask both again, or end the transport
  where the next module's refusal does not pass. Once a crash has left a neighbour out, the
  strategy is asked with no module the box came from, so the detour, or the first, may name the
  module the box came from (added on 2026-10-11, jms_kapps 77, decided by Etienne: "The module
  the box came from is now a valid choice."). The module reserved ahead looks ahead again
  when the box asks it, and may refuse then, as `blocked` (the build's reading).
- **Longest-waiting first.** A module that is free serves the box that has waited longest
  (`WaitingOrder`): any other box is refused as `behind`. A box keeps its place, the inner ask of a
  look-ahead included, and the WarehouseManagementSystem's Reserve waits behind every box. A box
  leaves the order once it arrives, moves elsewhere or its transport ends, which the module reads
  in the graph when it would otherwise refuse a box; a box that has stopped asking for a while
  comes first no more, but keeps its place.
- **The stop.** Once the stop of its process has begun, the module asks no Reserve, neither at
  its look-ahead nor of its detour, as the stop rules say.
- **`skip-reserve`** makes the module skip the look-ahead and the detour for its own hop: it
  accepts a box from a module without looking ahead, and hands it on without asking, so on a busy
  station it meets an occupied module, and the store refuses it. The WarehouseManagementSystem's
  Reserve is always looked ahead for.

**A reservation lapses at its bound.** A box the module reserved itself for that has not arrived
within `ARRIVAL_BOUND_SECONDS` and T, the longest transit time of any module in the graph (read at
the process's start), or two T for a box reserved ahead, counted from the module's answer to
Reserve, makes the module read the graph, and the box leaves its waiting order (both added on
2026-10-06, jms_kapps 43, decided by Etienne). If the box
is its own already and its transport has not ended, the giving module was lost after its commit
and before its Receive: the module ends the transport `failed`, with a reason that names the
giving module. Otherwise it frees its reservation. A transport that has ended is left alone. So
after a commit the store refused, the giving module keeps the box and ends the transport `failed`
with the store's reason, and the receiving module's reservation lapses at its bound. A box handed
over with `skip-reserve` armed comes with no reservation, so no bound: if its giving module is
lost after the commit, the transport stays `running`.

**The box of a spawn is created on the origin.** The WarehouseManagementSystem asks the origin's
Reserve, creates the box on the origin with its transport, and notifies the origin. The
notification takes the place of a Receive: the middleware's intake reads the transport, and the
BoxTransportWorkflow carries the box on, which uses the reservation up. A reservation for the
WarehouseManagementSystem's box lapses `ARRIVAL_BOUND_SECONDS` after the answer to Reserve, with
no transit time, since no move comes before the notification. At that bound the module frees the
reservation and writes nothing: the WarehouseManagementSystem owns the station's entry, and ends
the transport itself when it cannot notify the origin. So the module leaves a transport that has
ended alone, and one that has not to the WarehouseManagementSystem; the second is the build's
reading, where the ticket names only the first. (Changed on 2026-10-06, jms_kapps 41, decided by
Etienne. Until then the paragraph read: "**Until the spawn changes**, the
WarehouseManagementSystem's handover onto the origin keeps its handshake: the origin that has
reserved itself for the WarehouseManagementSystem's box calls the WarehouseManagementSystem's
Convey, which opens the transport, hands the box over and calls the origin's Receive. That Receive
starts nothing: the WarehouseManagementSystem's notification of the transport starts the origin.
Such a reservation needs no bound of its own, since the call of Convey has a timeout.")

(Changed on 2026-10-06, jms_kapps 40, decided by Etienne. Until then the receiving module called
the giving module's Convey once it had accepted Reserve, Convey opened the handover, moved the box
and called Receive, and the paragraph on the hop went on: "Its body is the move, a function the
module is given, and then the receiving module's Receive, which only confirms the arrival: it
never refuses, because the possession has already changed, and it starts nothing. After the
handover the giving module notifies the receiving module of the transport, and that notification
starts the receiving module's BoxTransportWorkflow.")

**A module that cannot go on** commits the transport `failed`, with the reason and the store's
report where there is one, and notifies the WarehouseManagementSystem. Only the assigned module
writes its transport.

**A next module that cannot be reached** before the handover, because the graph names no live
workflow of it, or the call of one is refused, fails or times out, is left out for the box: the
module logs one warning that names it ("Module9: leaves Module15 out for Box1: Module15 is not
reachable: ..."), raises nothing, keeps the box, and asks its strategy again, with the
connections to each module left out removed and with no module the box came from, which is then
a valid choice. Then the hop goes on as any hop: the first, then the detour, and the wait while
both refuse with a refusal that passes, with no time limit. A module left out stays out while the
box is here, and so does a detour that cannot be reached. A next module that refuses "no live
route on" is left out the same way. Only where no neighbour is left to ask does the module end
the transport the same way, with a reason that names the destination and each module left out
("no live route to Module9: Module3 and Module1 are not reachable"). With `skip-reserve` armed, a
hop the store refused asks first, and leaves no module out: `skip-reserve` onto a crashed module
stays as it was (the build's reading of jms_kapps 77, whose ticket leaves it out of scope).
(Changed on 2026-10-11, jms_kapps 77, decided by Etienne: "When the next module is not reachable,
the giving module leaves it out for this box and chooses again", and "The transport ends
`failed` only when no neighbour is left to ask". Until then: "A next module that cannot be
reached before the handover ends the transport the same way, with "Module3 is not reachable:
..." and why as its reason: the graph names no live workflow of it, or the call of one is
refused, fails or times out. The module logs one warning, raises nothing, and keeps the box.")
After the handover, when the next module's Receive cannot reach that
module, the box is on it and the transport is assigned to it: the module that passed both on
writes nothing, and says why in one warning. The receiving module ends the transport: at the
bound of its reservation, or at its stop. A receiving module that crashed after the commit ends
it at its next start, when the middleware's crash recovery ends every `running` Operation
assigned to it. A module finds the transport of a box it holds through the box, which links to it
(`fc:hasTransport`): it reads the box with its transport in one fetch, and no rule names a
transport after its box. (Changed on 2026-10-06, jms_kapps 42, decided by Etienne: only the
assigned module writes the transport. Until then the module that passed both on ended the
transport `failed`, "the one case where a module writes a transport not assigned to it. This is
what a stop does to a transport in flight, and what a peer that crashes during a run does".)
(Changed on 2026-10-06, jms_kapps 41, decided by Etienne. Until then the paragraph went on: "An
origin that cannot reach the WarehouseManagementSystem's Convey releases its reservation and says
why [...] Unless the WarehouseManagementSystem committed the handover before it was lost: then the
box and the transport are the origin's, and no notification of the transport will come, so the
origin ends it `failed`.")

**The deadlock alarm.** The module measures how long each box it holds waits for a Reserve,
from its first ask of one. Past `deadlock_alarm_seconds`, the launch's deadlock alarm time, 300 s
unless a run sets another (`--deadlock-alarm-seconds`), it raises the alarm once for the box, before
its next wait: one WARNING line that starts with "possible deadlock:" and names the module, the
box, the time waited and the modules it asks, and one `deadlock_alarm` event on its handover event
stream. The box keeps waiting, and nothing fails: the alarm is the visible backstop for boxes on a
cycle of connections that wait for each other. It writes nothing to the graph. (Added on
2026-10-11, jms_kapps 77, decided by Etienne.)

**A stop.** From the stop signal on, the module waits at most the graceful-shutdown bound for the
box it reserved itself for and for its own work: a move under way finishes, and the giving module
calls Receive. Its server takes no new connection by then, so only a Receive already under way
still comes. If no Receive came, and the box is its own with its transport not ended, its giving
module committed the handover: the module ends the transport `failed`, "the system is stopping".
The origin of a spawn waits for the WarehouseManagementSystem's notification instead, and leaves
the transport of the WarehouseManagementSystem's box to it, which owns the station's entry. (Added
on 2026-10-06, jms_kapps 42, decided by Etienne.) Once the stop has begun, the module also refuses
every Reserve, asks none, and hands no box to another module: the module a box's transport is
assigned to keeps the box and ends the transport `failed`, "the system is stopping". A destination
still gives its box back to the WarehouseManagementSystem, with the transport `done`. (Added on
2026-10-06, jms_kapps 42: the build's reading of Etienne's decision that only the assigned module
writes the transport, where the ticket names no rule for a hop not committed yet.)

**Two faults**, §5.2's faults of a module's own logic, are armed at run time and off when the
module starts. What stops either is the graph alone, and each refusal is logged with its report
and emitted on the module's handover event stream.

- `skip-reserve` makes the giving module skip Reserve and commit the handover regardless. Onto a
  module that holds a box, the store refuses that, nothing moves, and the module adapts: it asks
  Reserve first, the proper way, and waits its turn while the receiving module is occupied or
  reserved, so the box still arrives. Onto a free module, it conveys the box and calls Receive,
  and the receiving module carries the box on.
- `complete-without-releasing` makes the destination commit the transport `done` before it gives
  the box back. The store refuses that while the module holds the box, and the module then gives
  the box back properly, with the last handover.

This is the adaptation the paper describes, hardcoded. A module that reads the report and decides
for itself is future work.

Only a `ShaclValidationError` is the graph's refusal, and its report travels verbatim. Any other
error of the store, such as a dataset that does not exist or a read that failed, is not caught.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Literal, Protocol, get_args

from kapps_semantic_middleware.errors import NotificationUndelivered
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI, ShaclValidationError

from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import (
    NO_LIVE_ROUTE_ON,
    STOPPING,
    BoxTransportCapabilities,
    Call,
    Giver,
    MiddlewareHandover,
    Move,
    Notify,
    PeerUnreachable,
    ReceiveRequest,
    ReceiveResult,
    ReserveRequest,
    ReserveResult,
    StepResult,
    acceptance_record,
    ask,
    conveyed_record,
    listed,
    lost_giver,
    no_box_transport_capability,
    no_live_route,
    passes,
    readable_reason,
    reserved_record,
)
from jms_kapps.uc2.module_memory import ModuleMemory
from jms_kapps.uc2.routing import next_module_choices
from jms_kapps.uc2.strategies import REAL, REAL_MODULE_STRATEGY, ModuleStrategy
from jms_kapps.uc2.transport import Transport, reassignment, status_record
from jms_kapps.uc2.validation_report import rules_clause
from jms_kapps.uc2.vocabulary import FC, local_name
from jms_kapps.uc2.waiting_order import Asker, WaitingOrder

logger = logging.getLogger(__name__)

# How long a module waits before it asks again the Reserve of a module that refused to reserve
# itself, once its detour has refused too (changed on 2026-10-06, jms_kapps 43: until then "an
# occupied or reserved module's Reserve"), unless a run sets it: long enough that a person watching
# the demo can follow the log. The tests set 0.1 s.
DEFAULT_RETRY_SECONDS = 1.0

# How long a box that asked a module's Reserve still counts as asking, beyond twice the retry time
# that passes between two of its asks: long enough for its two asks of a round, the next module and
# the detour, each of which may look ahead, on a shared store. A box that has asked no more for
# that long comes first no more in the module's waiting order, and keeps its place there.
STILL_ASKING_SECONDS = 10.0

# How long after its last ask a box is forgotten by a module's waiting order.
FORGOTTEN_SECONDS = 300.0

# How long a module that reserved itself for a box waits for the box's Receive, beyond the move's
# transit time, counted from its answer to Reserve. The process adds T, the longest transit time of
# any module in the graph, which it reads at its start (changed on 2026-10-07, jms_kapps 8, decided
# by Etienne: until then "a run adds its transit time, one value for every module"). For the box of a spawn,
# the WarehouseManagementSystem's notification takes the place of the Receive, and no move comes
# before it, so the bound is this alone.
ARRIVAL_BOUND_SECONDS = 30.0

# How long a box that a module holds may wait for a Reserve, counted from the module's first ask of
# one for it, before the module raises the deadlock alarm for it, unless a run sets another time:
# `--deadlock-alarm-seconds`. (Added on 2026-10-11, jms_kapps 77, decided by Etienne.)
DEFAULT_DEADLOCK_ALARM_SECONDS = 300.0

# How the WARNING line of a deadlock alarm starts, so that a reader of a run's output finds it.
POSSIBLE_DEADLOCK = "possible deadlock:"

Fault = Literal["skip-reserve", "complete-without-releasing"]
"""A fault an operator arms on a module at run time: §5.2's two faults of a module's own logic.

`skip-reserve` hands a box on without asking the receiving module's Reserve.
`complete-without-releasing` makes the destination commit the transport `done` before it gives the
box back. Both are off when a module starts.
"""

FAULTS: tuple[Fault, ...] = get_args(Fault)


def faults_off() -> dict[Fault, bool]:
    """Every fault, off: how a module starts."""
    return dict.fromkeys(FAULTS, False)


# Prose for a person: a caller tells the store's refusal by the event's report.
_COMPLETION_REFUSED = (
    "the triple store refused the transport done while this module holds the box"
)


class GraphReads(Protocol):
    """What a module reads from the graph. `discovery.GraphView` is the real one."""

    def boxes_held_by(self, resource: IRI) -> list[IRI]: ...

    def connections(self) -> list[DirectedConnection]: ...

    def workflow_endpoint(self, resource: IRI, workflow_class: IRI) -> str | None: ...

    def capability(self, resource: IRI, capability_class: IRI) -> IRI | None: ...

    def wms(self) -> IRI | None: ...

    def transport_of(self, box: IRI) -> Transport | None: ...


class TransportWrites(Protocol):
    """What a module writes of a transport. `transport.TransportRecords` is the real one."""

    def start(self, operation: IRI) -> None: ...

    def complete(self, operation: IRI) -> None: ...

    def fail(self, operation: IRI, reason: str, report: str | None = None) -> None: ...


Start = Callable[[Callable[[], object]], None]
"""Starts a piece of this module's own work, and returns before it has run."""

Later = Callable[[float, Callable[[], object]], None]
"""Runs a piece of this module's own work once the seconds it is given have passed, and returns at
once."""


def _logged(work: Callable[[], object]) -> Callable[[], None]:
    """`work`, which logs what it raises: nobody else would see it."""

    def run() -> None:
        try:
            work()
        except Exception:
            logger.exception("Work in the background failed")

    return run


def in_background(work: Callable[[], object]) -> None:
    """Run `work` on a thread of its own, and log what it raises."""
    threading.Thread(target=_logged(work), daemon=True).start()


def in_background_after(seconds: float, work: Callable[[], object]) -> None:
    """Run `work` on a thread of its own once `seconds` have passed, and log what it raises."""
    timer = threading.Timer(seconds, _logged(work))
    timer.daemon = True
    timer.start()


def _has_ended(transport: Transport) -> bool:
    """Whether `transport` is `done` or `failed`: a transport that has ended is left alone."""
    return transport.status not in (OperationStatus.QUEUED, OperationStatus.RUNNING)


class _NotReachable(ReserveResult):
    """The answer in place of a module's own answer to Reserve where the module could not be
    reached: the graph names no live Reserve of it, or the call was refused, failed or timed out.
    Its reason says why. (Added on 2026-10-11, jms_kapps 77, decided by Etienne: a module that
    cannot be reached is left out for the box, so the module now tells this answer from a
    refusal.)"""


def _not_reachable(answer: ReserveResult) -> bool:
    """Whether the module that gave `answer` is one to leave out for the box: it could not be
    reached, or it refused "no live route on", which a giver treats the same way."""
    return isinstance(answer, _NotReachable) or answer.refusal == NO_LIVE_ROUTE_ON


def _without(
    connections: Sequence[DirectedConnection], left_out: Sequence[IRI]
) -> list[DirectedConnection]:
    """`connections` without those to a module of `left_out`: what a strategy is given once the
    module has left those modules out for a box."""
    return [c for c in connections if c.target not in left_out]


ChooseAgain = Callable[[Sequence[IRI]], list[IRI]]
"""The neighbours a module asks for a box once it has left out the modules it is given, in order:
its strategy's answer without the connections to them, and with no module the box came from."""


# How long the origin of a spawn waits for the WarehouseManagementSystem's notification of the box
# it reserved itself for, counted from its answer to Reserve: no move comes before the
# notification, so no transit time is added.
SPAWN_BOUND_SECONDS = ARRIVAL_BOUND_SECONDS


@dataclass(eq=False)
class _Reservation:
    """This module's promise to take `box` from `giver`, which lapses `bound` seconds after the
    module's answer to Reserve. `for_a_spawn` says whether the giver is the
    WarehouseManagementSystem, whose notification takes the place of the Receive. Each Reserve it
    accepts makes a new one, so that the bound of an older one leaves a newer one alone.

    `ahead` is the next module that reserved itself for the box at this module's look-ahead,
    where the box goes on to; `deciding` holds the module reserved while its look-ahead asks that
    next module, so that no other box takes it meanwhile. (Added on 2026-10-06, jms_kapps 43,
    decided by Etienne.)"""

    box: IRI
    giver: IRI
    bound: float
    for_a_spawn: bool
    ahead: IRI | None = None
    deciding: bool = False


class FlexConveyorModule:
    """One module, given what it reads from the graph, what it writes, and how it reaches peers.

    Args:
        resource: The module.
        graph: The graph's answers.
        handover: The middleware's handover.
        call: A blocking call to a peer's workflow.
        records: The module's writes of a transport.
        notify: The middleware's notification of a resource.
        move: The move of a box off this module onto a receiver: its convey.
        start: Starts this module's own work: carrying a box on after Receive has answered.
            (Changed on 2026-10-06, jms_kapps 41, decided by Etienne. Until then also "at the
            origin of a spawn, calling the WarehouseManagementSystem's Convey after Reserve has
            answered".)
        retry_seconds: How long the module waits before it asks again the Reserve of a module
            that refused to reserve itself, once its detour has refused too (changed on
            2026-10-06, jms_kapps 43: until then "an occupied or reserved module's Reserve").
        wait: Waits the seconds it is given.
        later: Runs this module's own work after some seconds: the bound of a reservation.
        arrival_bound: How long a box the module reserved itself for may take to arrive, counted
            from its answer to Reserve: `ARRIVAL_BOUND_SECONDS` and T, the longest transit time of any module. The
            WarehouseManagementSystem's notification of a box that the module, as the origin of a
            spawn, reserved itself for may take `SPAWN_BOUND_SECONDS`.
        ahead_bound: How long a box the module reserved itself for ahead, at the plain Reserve of
            a look-ahead, may take to arrive: `ARRIVAL_BOUND_SECONDS` and two T,
            since the box first moves onto the module that asked.
        clock: The time, in seconds, by which the waiting order counts how long a box waited.
        events: The module's handover event stream, where a refused handover is emitted.
        memory: What the module keeps of its own work, for its dashboard.
        stopping: Whether the stop of the module's process has begun.
        strategy: The module strategy that answers which neighbours the module asks for a box,
            `real` unless a run chooses another (`strategies`).
        strategy_name: Its name, which a warning about one of its answers names.
        deadlock_alarm_seconds: How long a box the module holds may wait for a Reserve, counted
            from its first ask of one, before the module raises the deadlock alarm for it, by
            the clock `clock`. (Added on 2026-10-11, jms_kapps 77, decided by Etienne.)
    """

    def __init__(
        self,
        resource: IRI,
        *,
        graph: GraphReads,
        handover: MiddlewareHandover,
        call: Call,
        records: TransportWrites,
        notify: Notify,
        move: Move,
        start: Start = in_background,
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        wait: Callable[[float], object] = time.sleep,
        later: Later = in_background_after,
        arrival_bound: float = ARRIVAL_BOUND_SECONDS,
        ahead_bound: float = ARRIVAL_BOUND_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        events: HandoverEvents | None = None,
        memory: ModuleMemory | None = None,
        stopping: Callable[[], bool] = lambda: False,
        strategy: ModuleStrategy = REAL_MODULE_STRATEGY,
        strategy_name: str = REAL,
        deadlock_alarm_seconds: float = DEFAULT_DEADLOCK_ALARM_SECONDS,
    ) -> None:
        self.resource = IRI(resource)
        self.graph = graph
        self.handover = handover
        self.call = call
        self.records = records
        self.notify = notify
        self.start = start
        self.retry_seconds = retry_seconds
        self.wait = wait
        self.later = later
        self.arrival_bound = arrival_bound
        self.ahead_bound = ahead_bound
        self.clock = clock
        self.strategy = strategy
        self.strategy_name = strategy_name
        self.deadlock_alarm_seconds = deadlock_alarm_seconds
        # The boxes that asked its Reserve, by their place: held under `_reserving` too.
        self._waiting = WaitingOrder(
            asking_seconds=2 * retry_seconds + STILL_ASKING_SECONDS,
            forgotten_seconds=FORGOTTEN_SECONDS,
        )
        # Which fault is armed, by name: all off when the module starts.
        self.faults: dict[Fault, bool] = faults_off()
        self._stopping = stopping
        self._reservation: _Reservation | None = None
        # Held while a Reserve reads and reserves, and while a reservation is released. A
        # stopping module waits on it until its reservation is used up.
        self._reserving = threading.Condition()
        # How many of this module's own pieces of work run: carrying a box on, from the answer to
        # Receive or the WarehouseManagementSystem's notification until the box has gone or the
        # transport has ended. A stopping module waits on `_idle` until none runs.
        self._working = 0
        self._idle = threading.Condition()
        # Held while a reservation's bound reads the graph and ends a transport, and while an
        # arrival reads the box's transport: the arrival finds the transport as the bound left it.
        self._arriving = threading.Lock()
        self._capabilities = BoxTransportCapabilities(graph)
        self.events = events
        self.memory = memory if memory is not None else ModuleMemory()

        def move_off(box: IRI, receiver: IRI) -> None:
            move(box, receiver)
            self.memory.gone(box)

        self._giver = Giver(handover, self.resource, move_off, logger, events)
        # The WMS is seeded before any module starts, so it is read once, when it is there.
        self._wms: IRI | None = None

    # -- The BoxTransportWorkflow ------------------------------------------------------------ #

    def box_transport(
        self,
        transport: Transport,
        *,
        came_from: IRI | None = None,
        ahead: IRI | None = None,
    ) -> None:
        """The BoxTransportWorkflow: carry the box of `transport` on, while the transport is
        assigned to this module.

        `came_from` is the module the box came from, which it does not go straight back to, and
        `ahead` the next module that reserved itself for the box at this module's look-ahead,
        where the box goes on to; a reservation this module made for the box names it too. (Added
        on 2026-10-06, jms_kapps 43, decided by Etienne.)

        It acts on the transport it is handed: at the origin, as the middleware's intake read it
        for the WarehouseManagementSystem's notification; at a later module, as its Receive read
        it through the box. Either way it carries the box on the same way. A transport that is
        done or has failed, or is assigned to another module, is logged and left alone. Once the
        module acts on a transport assigned to it, a reservation it made for the box is used up.
        The transport alone does not tell the module that it possesses the box, so its memory
        does not hold the box for it.

        At the origin, the queued transport is committed `running` first. The store refuses that
        while the box is not on the module: then the transport stays `queued`, and the module
        writes nothing more and notifies no one. The WarehouseManagementSystem creates the box on
        the origin before it notifies the origin, so that happens only to a box that is elsewhere.
        (Changed on 2026-10-06, jms_kapps 41, decided by Etienne. Until then: "while the box is
        not on the module yet: then the transport stays `queued`, because the
        WarehouseManagementSystem holds the box and writes its transport".) A `running` transport whose box is held elsewhere fails at the next handover,
        which checks the possession before it commits.

        It counts as the module's own work, which a stop of its process waits for.
        """
        with self._at_work():
            self._box_transport(transport, came_from=came_from, ahead=ahead)

    def _box_transport(
        self, transport: Transport, *, came_from: IRI | None, ahead: IRI | None
    ) -> None:
        if _has_ended(transport):
            self._leave(transport, str(transport.status))
            return
        if transport.assigned != self.resource:
            assigned = transport.assigned.fragment if transport.assigned else "nobody"
            self._leave(transport, f"assigned to {assigned}")
            return
        used = self._take(transport.box)
        if ahead is None and used is not None:
            ahead = used.ahead
        if transport.status == OperationStatus.QUEUED:
            try:
                self.records.start(transport.operation)
            except ShaclValidationError as refused:
                logger.warning(
                    "%s: the triple store refused running %s%s, so it stays queued: %s is not "
                    "on this module:\n%s",
                    self.resource.fragment,
                    transport.operation.fragment,
                    rules_clause(refused.report),
                    transport.box.fragment,
                    refused.report,
                )
                return
            logger.info(
                "%s",
                status_record(
                    self.resource, transport.operation, OperationStatus.RUNNING
                ),
            )
            transport = replace(transport, status=OperationStatus.RUNNING)
            # The store admits `running` only while a FlexConveyorModule possesses the box, and
            # the WMS creates the box on the origin before it notifies the origin: from this
            # commit on, the origin knows it possesses the box. A transport assigned to a module
            # is no such knowledge, so a `running` one changes nothing here.
            self.memory.holding(transport.box)
        self.carry_on(transport, came_from=came_from, ahead=ahead)

    def _leave(self, transport: Transport, why: str) -> None:
        logger.warning(
            "%s: %s is %s, so it is left alone",
            self.resource.fragment,
            transport.operation.fragment,
            why,
        )

    def carry_on(
        self,
        transport: Transport,
        *,
        came_from: IRI | None = None,
        ahead: IRI | None = None,
    ) -> None:
        """Carry the box of `transport` toward its destination: give it back there, or start the
        hop to the first neighbour that the module's strategy names, with `came_from`, the module
        the box came from, and `ahead`, the module reserved ahead for it at this module's
        look-ahead (`strategies`); the second it names is the detour, which the module tries when
        the first declines. A module that cannot go on says why in the transport. (Changed on
        2026-10-06, jms_kapps 43, decided by Etienne: the box goes on to the module reserved
        ahead, and a declined Reserve leads to one detour. Until then: "or start the hop to the
        next module on a shortest path to it.") (Changed on 2026-10-10, jms_kapps 52, decided by
        Etienne on 2026-10-07: a strategy chooses, `real` by default, which is the choice made
        here until then. Until then: "or start the hop to the next module on a shortest path to
        it, which never runs straight back to `came_from`. The module reserved `ahead` for the
        box comes first while a connection still leads there. The detour is the one other module
        it tries when the next module declines (`routing.detour`).")

        **A crash is routed around.** A neighbour it asks that cannot be reached, or that refuses
        "no live route on", it leaves out for the box, with one warning that names it, and it
        chooses again: its strategy is asked again with the connections to each module left out
        removed, and with no module the box came from, which is then a valid choice. A module left
        out stays out while the box is here. Only where no neighbour is left to ask does the
        transport end `failed`, with a reason that names the destination and each module left out.
        The choice again decides on the connections read for this decision, and reads none.
        (Added on 2026-10-11, jms_kapps 77, decided by Etienne: "When the next module is not
        reachable, the giving module leaves it out for this box and chooses again. The module the
        box came from is now a valid choice.")"""
        destination = transport.destination
        if destination == self.resource:
            self.memory.decided(transport, ())
            self.give_back(transport)
            return
        # Read at every decision, not kept: the path is found on the graph as it is now, because a
        # table of connections filled once is a race against the modules' start.
        connections = self.graph.connections()
        asked = self._decide(transport, came_from, ahead, connections, read=True)
        if not asked:
            self.cannot_go_on(transport, self._no_route(destination))
            return

        def choose_again(left_out: Sequence[IRI]) -> list[IRI]:
            return self._decide(
                transport, None, ahead, _without(connections, left_out), read=False
            )

        self.hop(
            transport,
            asked[0],
            asked[1] if len(asked) > 1 else None,
            choose_again=choose_again,
        )

    def _decide(
        self,
        transport: Transport,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
        *,
        read: bool,
    ) -> list[IRI]:
        """The neighbours the module asks for the box of `transport`, in order, at most two, as its
        strategy names them on `connections`, kept as a decision in its memory: with
        `connections`, where `read` says they are what it read for this decision."""
        asked = self._neighbours_to_ask(
            transport.destination, came_from, ahead, connections
        )
        kept = connections if read else None
        if not asked:
            self.memory.decided(transport, (), kept)
            return []
        receiver = asked[0]
        # The dashboard's ties stay what they were: the other modules first on a shortest path.
        avoid = [] if came_from is None else [came_from]
        shortest = next_module_choices(
            [(c.source, c.target) for c in connections],
            self.resource,
            transport.destination,
            avoid=avoid,
        )
        ties = [c for c in shortest if c != receiver]
        self.memory.decided(
            transport,
            [receiver, *ties],
            kept,
            detour=asked[1] if len(asked) > 1 else None,
        )
        return asked

    def _neighbours_to_ask(
        self,
        destination: IRI,
        came_from: IRI | None,
        ahead: IRI | None,
        connections: Sequence[DirectedConnection],
    ) -> list[IRI]:
        """The neighbours the module's strategy names for a box bound for `destination`, in
        order, at most two: the first two of its answer, without any to which no connection of
        this module leads. Each one dropped so is logged in one warning that names the strategy.
        (Added on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07.)"""
        answer = self.strategy.neighbours_to_ask(
            self.resource, destination, came_from, ahead, connections
        )
        leads_to = {c.target for c in connections if c.source == self.resource}
        kept: list[IRI] = []
        for neighbour in list(answer)[:2]:
            if neighbour in leads_to:
                kept.append(IRI(neighbour))
                continue
            logger.warning(
                "%s: the module strategy %s named %s, to which no connection of %s leads, "
                "so it is not asked",
                self.resource.fragment,
                self.strategy_name,
                local_name(str(neighbour)),
                self.resource.fragment,
            )
        return kept

    def hop(
        self,
        transport: Transport,
        receiver: IRI,
        detour_to: IRI | None = None,
        *,
        choose_again: ChooseAgain | None = None,
    ) -> None:
        """The hop of the box of `transport` to `receiver`: ask its Reserve, and once it has
        accepted, commit the handover, convey the box and call its Receive. While `receiver`
        refuses, ask `detour_to` once, and take the hop there if it accepts; otherwise wait and ask
        both again, or, where the refusal of `receiver` does not pass, end the transport.

        With `choose_again`, a module asked that cannot be reached, or that refuses "no live
        route on", is left out for the box, and `choose_again` names the neighbours to ask then
        (added on 2026-10-11, jms_kapps 77, decided by Etienne). Without it, as before, such a
        `receiver` ends the transport, and such a detour is asked no more.

        With `skip-reserve` armed, commit the handover onto `receiver` without asking, and so
        without a detour. If the store refuses that, ask first, as the hop should have, and wait
        its turn the same way, with no module left out: `skip-reserve` onto a crashed module
        stays as it was (the build's reading of jms_kapps 77, whose ticket leaves it out of
        scope). (Changed on 2026-10-06, jms_kapps 43, decided by Etienne: one detour before
        waiting. Until then: "While `receiver` refuses as occupied or reserved, wait and ask
        again.")
        """
        first: StepResult | None = None
        if self.faults["skip-reserve"]:
            first = self._pass(transport, receiver, asked=False)
            if first.accepted:
                return
            if first.report is None:
                self.cannot_go_on(transport, first.reason)
                return
            choose_again = None
        accepted_by, reserved = self._reserve_when_free(
            transport, receiver, detour_to, choose_again
        )
        if not reserved.accepted:
            if first is None:
                self.cannot_go_on(transport, reserved.reason, reserved.report)
            else:
                self.cannot_go_on(
                    transport,
                    f"{first.reason}; asked again: {reserved.reason}",
                    reserved.report or first.report,
                )
            return
        self._convey(transport, accepted_by)

    def _convey(self, transport: Transport, receiver: IRI) -> None:
        """Hand the box of `transport` to `receiver`, which has reserved itself for it.

        If the store refuses the commit, this module keeps the box and cannot go on. It tells
        `receiver` nothing: the reservation lapses at its bound.
        """
        handed = self._pass(transport, receiver, asked=True)
        if not handed.accepted:
            self.cannot_go_on(transport, handed.reason, handed.report)

    def give_back(self, transport: Transport) -> None:
        """Give the box of `transport` back to the WarehouseManagementSystem, and notify it.

        The handover's entry makes the WarehouseManagementSystem the possessor and the transport
        `done` in one commit, so no moment has a box on a module with its transport done, or a
        box back in the WMS with its transport running.
        """
        box, operation = transport.box, transport.operation
        wms = self._the_wms()
        if wms is None:
            self.cannot_go_on(transport, "the graph names no WarehouseManagementSystem")
            return
        if self.faults["complete-without-releasing"]:
            self._complete_without_releasing(transport)
        handed = self._giver.hand_over(
            box,
            wms,
            on_refusal="it keeps the box",
            operation=operation,
            transport=operation,
        )
        if not handed.accepted:
            self.cannot_go_on(transport, handed.reason, handed.report)
            return
        logger.info("%s", status_record(self.resource, operation, OperationStatus.DONE))
        self._notify(operation, wms)

    def _complete_without_releasing(self, transport: Transport) -> None:
        """The fault `complete-without-releasing`: commit `transport` `done` while this module
        still holds its box. The store refuses that; the refusal is logged with its report and
        emitted, and the module then gives the box back properly."""
        try:
            self.records.complete(transport.operation)
        except ShaclValidationError as refused:
            logger.warning(
                "%s: the triple store refused completing %s without releasing %s%s; it gives "
                "the box back properly:\n%s",
                self.resource.fragment,
                transport.operation.fragment,
                transport.box.fragment,
                rules_clause(refused.report),
                refused.report,
            )
            if self.events is not None:
                self.events.commit_refused(
                    box=transport.box,
                    peer=None,
                    transport=transport.operation,
                    reason=_COMPLETION_REFUSED,
                    report=refused.report,
                )
            return
        logger.warning(
            "%s: the triple store admitted %s done while this module holds %s: its rules are "
            "not loaded",
            self.resource.fragment,
            transport.operation.fragment,
            transport.box.fragment,
        )

    def cannot_go_on(
        self, transport: Transport, reason: str, report: str | None = None
    ) -> None:
        """End `transport` as failed with `reason`, and notify the WarehouseManagementSystem.

        This module is the one the transport is assigned to: it is handed the transport only
        then, and it keeps it until the box has gone. The box stays here.
        """
        self._end_failed(
            transport.box, transport.operation, reason, report, "stays here"
        )

    def _receiver_not_reached(
        self, transport: Transport, receiver: IRI, reason: str
    ) -> None:
        """Say why in one warning, and write nothing: this module passed `transport` and its box
        to `receiver`, and then could not reach `receiver`'s Receive. The box is on `receiver`
        and the transport is assigned to it, so only `receiver` writes the transport: at the
        bound of its reservation, or at its stop. (Changed on 2026-10-06, jms_kapps 42, decided
        by Etienne. Until then this module ended the transport `failed`: "The one case where a
        module writes a transport that is not assigned to it: nobody else would end it.")"""
        logger.warning(
            "%s: %s is on %s, so %s is %s's to end: %s",
            self.resource.fragment,
            transport.box.fragment,
            receiver.fragment,
            transport.operation.fragment,
            receiver.fragment,
            reason,
        )

    def _giver_lost(self, box: IRI, reason: str) -> None:
        """End the transport of `box` as failed, unless it has ended: its giver passed the box
        and the transport to this module, and was lost before it told this module, so nothing
        would carry the box on. This module holds the box, so it reads the box with its
        transport, through the box's link to it: no naming rule tells a transport from its box.
        (Changed on 2026-10-06, jms_kapps 39, decided by Etienne. Until then: "The transport is
        named after its box, as the WarehouseManagementSystem opened it, so this module knows it
        without a read.")"""
        self.memory.holding(box)
        transport = self.graph.transport_of(box)
        if transport is None:
            logger.warning(
                "%s: %s is here, and the graph names no transport of it: %s",
                self.resource.fragment,
                box.fragment,
                reason,
            )
            return
        if _has_ended(transport):
            self._leave(transport, str(transport.status))
            return
        self._end_failed(box, transport.operation, reason, None, "is here")

    def _end_failed(
        self,
        box: IRI,
        operation: IRI,
        reason: str,
        report: str | None,
        where_the_box_is: str,
    ) -> None:
        """Commit `operation` failed with `reason`, notify the WMS, and say why in one warning,
        which also says when the WMS could not be told, as at a stop.

        If the store refuses the record, the refusal is logged with its report, and the
        WarehouseManagementSystem is not notified: in the graph, the transport has not ended.
        """
        why = f"{self.resource.fragment}: {box.fragment} {where_the_box_is}: {reason}"
        try:
            self.records.fail(operation, reason, report)
        except ShaclValidationError as refused:
            logger.warning("%s", why)
            logger.warning(
                "%s: the triple store refused recording %s failed%s:\n%s",
                self.resource.fragment,
                operation.fragment,
                rules_clause(refused.report),
                refused.report,
            )
            return
        logger.info(
            "%s", status_record(self.resource, operation, OperationStatus.FAILED)
        )
        untold = self._tell_the_wms(operation)
        logger.warning("%s%s", why, "" if untold is None else f"; {untold}")

    def _tell_the_wms(self, operation: IRI) -> str | None:
        """Notify the WMS that `operation` has ended. Returns why it was not told, or None."""
        wms = self._the_wms()
        if wms is None:
            return None
        try:
            self.notify(operation, wms)
        except NotificationUndelivered as error:
            return f"{wms.fragment} was not told: {readable_reason(error)}"
        return None

    # -- The steps this module answers ------------------------------------------------------- #

    def reserve(self, request: ReserveRequest) -> ReserveResult:
        """Reserve this module for a box, in its memory, and set the reservation's bound: for a
        box of the WarehouseManagementSystem, whose notification comes with no move before it,
        `SPAWN_BOUND_SECONDS`, for a box reserved ahead, at the plain Reserve of a look-ahead,
        `ahead_bound`, and for a box of a module `arrival_bound`. (Changed on 2026-10-06,
        jms_kapps 41, decided by Etienne. Until then "The origin of a spawn, reserved for the
        WarehouseManagementSystem's box, calls the WarehouseManagementSystem's Convey instead".)

        **Look-ahead acceptance.** The module accepts only while it holds nothing, has promised
        itself to no other box, and no box that has waited longer still asks (`WaitingOrder`);
        and then only where it is the box's destination, or its own next module, which it asks a
        plain Reserve for the box, accepts at once. Otherwise it refuses at once and keeps no
        reservation. A plain Reserve, and a Reserve that names no destination, looks no further
        ahead. Every box but the WarehouseManagementSystem's takes its place in the waiting
        order at its first ask, whatever the answer. With `skip-reserve` armed, the module looks
        ahead for the WarehouseManagementSystem's Reserve alone. (Added on 2026-10-06, jms_kapps
        43, decided by Etienne.)

        Once the stop of the module's process has begun, it refuses, with the reason "the system
        is stopping" and no refusal that passes, and sets no bound: a reservation then would
        outlast the module. (Added on 2026-10-06, jms_kapps 42: the build's reading of Etienne's
        decision that only the assigned module writes the transport.)
        """
        if self._stopping():
            # Its server takes no new connection once the stop has begun: only a Reserve already
            # under way comes, and a reservation now would outlast the module.
            return ReserveResult(accepted=False, reason=STOPPING)
        box, asker = IRI(request.box), IRI(request.source_module)
        at = IRI(request.box_at) if request.box_at else asker
        destination = IRI(request.destination) if request.destination else None
        wms = self._the_wms()
        for_a_spawn = asker == wms
        if for_a_spawn:
            bound = SPAWN_BOUND_SECONDS
        elif request.plain:
            bound = self.ahead_bound
        else:
            bound = self.arrival_bound
        looks_ahead = (
            not request.plain
            and destination is not None
            and destination != self.resource
            and (for_a_spawn or not self.faults["skip-reserve"])
        )
        with self._reserving:
            now = self.clock()
            if at != wms:
                self._waiting.asked(box, at=at, via=asker, now=now)
            refused = self._refusal(box, spawn=at == wms, now=now)
            if refused is not None:
                return refused
            reservation = self._reservation = _Reservation(
                box, asker, bound, for_a_spawn, deciding=looks_ahead
            )
        if looks_ahead:
            assert destination is not None
            try:
                ahead, answer = self._look_ahead(
                    box, destination, came_from=asker, at=at
                )
            except BaseException:
                # A read or a call that raised: the module keeps no reservation it decided on.
                with self._reserving:
                    if self._reservation is reservation:
                        self._reservation = None
                        self._reserving.notify_all()
                raise
            with self._reserving:
                if self._reservation is not reservation:
                    return ReserveResult(
                        accepted=False,
                        reason=f"reserved for {box.fragment}",
                        refusal="reserved",
                    )
                if not answer.accepted:
                    self._reservation = None
                    # A stop that waits for the reservation to be gone sees it gone.
                    self._reserving.notify_all()
                    return answer
                reservation = self._reservation = replace(
                    reservation, ahead=ahead, deciding=False
                )
        self._log_accepted(FC.ReserveWorkflow, box, asker)
        self.later(bound, lambda: self._bound_passed(reservation))
        return ReserveResult(accepted=True)

    def _refusal(self, box: IRI, *, spawn: bool, now: float) -> ReserveResult | None:
        """Why this module cannot reserve itself for `box` now, or None if it can: it holds a box,
        it has promised itself to another box or decides on one, or a box that has waited longer
        still asks for it. Called under `_reserving`."""
        held = self.graph.boxes_held_by(self.resource)
        if held:
            return ReserveResult(
                accepted=False,
                reason=f"occupied by {held[0].fragment}",
                refusal="occupied",
            )
        promised = self._reservation
        if promised is not None and (promised.box != box or promised.deciding):
            return ReserveResult(
                accepted=False,
                reason=f"reserved for {promised.box.fragment}",
                refusal="reserved",
            )
        if promised is None:
            first = self._waited_longer(None if spawn else box, now)
            if first is not None:
                return ReserveResult(
                    accepted=False,
                    reason=f"{first.fragment} has waited longer",
                    refusal="behind",
                )
        return None

    def _waited_longer(self, box: IRI | None, now: float) -> IRI | None:
        """The first box ahead of `box` in the waiting order that still asks and still waits for
        this module, or None; any such box when `box` is None. A box ahead that has moved
        elsewhere, or whose transport ended, leaves the order: the graph says where its transport
        is assigned, which is where the box is."""
        for asker in self._waiting.ahead_of(box, now):
            if self._still_waits(asker):
                return asker.box
            self._waiting.leave(asker.box)
        return None

    def _still_waits(self, asker: Asker) -> bool:
        """Whether the box of `asker` still waits for this module: its transport has not ended,
        and the box is where it asked from, on the module that asked for it, or arriving here."""
        try:
            transport = self.graph.transport_of(asker.box)
        except ValueError:
            return False
        if transport is None or _has_ended(transport) or transport.assigned is None:
            return False
        return transport.assigned == self.resource or not asker.moved_from(
            transport.assigned
        )

    def _look_ahead(
        self, box: IRI, destination: IRI, *, came_from: IRI, at: IRI
    ) -> tuple[IRI | None, ReserveResult]:
        """This module's next module for `box`, which comes from `came_from` and is at `at`, and
        that module's answer to a plain Reserve for it, as this module answers the asker: a
        refusal that passes is `blocked`.

        A next module that cannot be reached is left out for the box, with one warning that names
        it, and the module looks ahead to the next one its strategy names, given the connections
        without those to each module left out; `came_from`, the module that holds the box, stays
        excluded. Where none is left, it refuses "no live route on", with a reason that names the
        destination and each module left out. (Added on 2026-10-11, jms_kapps 77, decided by
        Etienne: "The look-ahead routes around too.")"""
        connections = self.graph.connections()
        left_out: list[IRI] = []
        while True:
            # The first neighbour the strategy names, with no module reserved ahead. (Changed on
            # 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07: a strategy chooses,
            # `real` by default. Until then the first module on a shortest path, which never
            # runs straight back to `came_from`.)
            asked = self._neighbours_to_ask(
                destination, came_from, None, _without(connections, left_out)
            )
            if not asked and left_out:
                return None, ReserveResult(
                    accepted=False,
                    reason=no_live_route(destination, left_out),
                    refusal=NO_LIVE_ROUTE_ON,
                )
            if not asked:
                return None, ReserveResult(
                    accepted=False, reason=self._no_route(destination)
                )
            ahead = asked[0]
            if self._stopping():
                # A module whose stop has begun asks no Reserve.
                return ahead, ReserveResult(accepted=False, reason=STOPPING)
            try:
                answer = ask(
                    self.graph,
                    self.call,
                    ahead,
                    FC.ReserveWorkflow,
                    ReserveRequest(
                        box=str(box),
                        source_module=str(self.resource),
                        destination=str(destination),
                        plain=True,
                        box_at=str(at),
                    ),
                    ReserveResult,
                )
            except PeerUnreachable as lost:
                self._leave_out(box, ahead, str(lost), left_out)
                continue
            if answer.accepted:
                return ahead, answer
            return ahead, ReserveResult(
                accepted=False,
                reason=f"{ahead.fragment}, its next module, refused to reserve: {answer.reason}",
                report=answer.report,
                refusal="blocked" if passes(answer.refusal) else None,
            )

    def _leave_out(self, box: IRI, module: IRI, why: str, left_out: list[IRI]) -> None:
        """Leave `module` out for `box`, adding it to `left_out`, and say why in one warning that
        names it, which a reader of a run's output finds by "leaves Module15 out". (Added on
        2026-10-11, jms_kapps 77, decided by Etienne: "The module logs one warning that names
        each module it leaves out.")"""
        left_out.append(module)
        logger.warning(
            "%s: leaves %s out for %s: %s",
            self.resource.fragment,
            module.fragment,
            box.fragment,
            why,
        )

    def receive(self, request: ReceiveRequest) -> ReceiveResult:
        """The light barrier: a box has arrived. The possession has changed already, so it never
        refuses. Once it has answered, it reads the box with its transport and carries the box
        on, as its own work. (Changed on 2026-10-06, jms_kapps 41, decided by Etienne: the
        WarehouseManagementSystem calls no Receive. Until then "A Receive from the
        WarehouseManagementSystem starts nothing".)"""
        box, giver = IRI(request.box), IRI(request.source_module)
        # Before the reservation is used up: a stop that sees it gone sees the work to wait for.
        # The work that carries the box on ends it.
        self._begin_work()
        try:
            used = self._take(box)
            ahead = used.ahead if used is not None else None
            self.memory.holding(box)
            self._log_accepted(FC.ReceiveWorkflow, box, giver)
            # Not inside this call: the giver waits for this answer.
            self.start(lambda: self._carry_on_arrived(box, giver, ahead))
        except BaseException:
            self._end_work()
            raise
        return ReceiveResult(accepted=True)

    def _log_accepted(self, step: IRI, box: IRI, source: IRI) -> None:
        """Log a step this module accepted: its own evidence that the step ran here."""
        logger.info("%s", acceptance_record(self.resource, step, box, source))

    # -- The stop ---------------------------------------------------------------------------- #

    def stop(self, within: float) -> None:
        """This module's part in the stop of its process, which has begun: wait at most
        `within` seconds for the box it reserved itself for and for its own work, then decide
        on what is left of its reservation. It returns when it has.

        - **A box from a module**: once the stop has begun, the module's server takes no new
          connection, so only a Receive already under way still comes, and uses the reservation
          up. If none has come by then, the module reads the graph: if the box is its own, the
          giving module committed the handover, and the module, now the assigned module, ends
          the transport `failed`, "the system is stopping", unless it has ended. A box that is
          not here it frees, and writes nothing.
        - **The box of a spawn**, at the origin: the WarehouseManagementSystem's notification,
          not a Receive, uses the reservation up. If none has come, the module frees the
          reservation and writes nothing: the WarehouseManagementSystem owns the station's
          entry, and ends the transport itself when it cannot notify the origin.
        - **Its own work**: a hop in its move finishes the move and calls Receive, and work that
          has not handed its box on yet ends its transport `failed`, "the system is stopping", at
          its next step, since the module asks no Reserve and hands no box to another module once
          the stop has begun (the build's reading); a destination gives its box back.

        (Added on 2026-10-06, jms_kapps 42, decided by Etienne: inside the station only the
        assigned module writes the transport.)
        """
        deadline = time.monotonic() + within
        with self._reserving:
            self._reserving.wait_for(lambda: self._reservation is None, timeout=within)
            reservation = self._reservation
        if reservation is not None:
            self._lapse(reservation, STOPPING, "before the stop")
        with self._idle:
            self._idle.wait_for(
                lambda: self._working == 0,
                timeout=max(0.0, deadline - time.monotonic()),
            )

    # -- Helpers ----------------------------------------------------------------------------- #

    def _pass(self, transport: Transport, receiver: IRI, *, asked: bool) -> StepResult:
        """The handover of the box and of `transport` to `receiver`.

        The entry commits the change of possession and assigns the transport to `receiver`. The
        body conveys the box, this module's own move, and calls the receiver's Receive, which
        carries the box on. Once the box has gone, the module logs it. If the receiver's Receive
        cannot reach it, this module writes nothing, and the handover still counts as accepted:
        the box has gone, and the transport is the receiver's. (Changed on 2026-10-06, jms_kapps
        42, decided by Etienne. Until then "this module ends the transport `failed`".) Once the
        stop of this module's process has begun, it commits no handover to `receiver` (added on
        2026-10-06, jms_kapps 42: the build's reading).

        Args:
            asked: Whether `receiver` accepted this module's Reserve first, so that a refusal of
                the store ends the transport; or this module hands the box over without asking,
                with `skip-reserve` armed, and asks first if the store refuses.
        """
        box = transport.box
        capability = self._capabilities.of(receiver)
        if capability is None:
            return StepResult(
                accepted=False, reason=no_box_transport_capability(receiver)
            )
        if self._stopping():
            # As late as it can be asked: once committed, the box is the receiver's.
            return StepResult(accepted=False, reason=STOPPING)

        def arrived() -> None:
            received = ask(
                self.graph,
                self.call,
                receiver,
                FC.ReceiveWorkflow,
                ReceiveRequest(box=str(box), source_module=str(self.resource)),
                ReceiveResult,
            )
            # The possession has changed already, so nothing is undone: the receiver carries the
            # box on from its own read, or its reservation's bound ends the transport.
            if not received.accepted:
                logger.warning(
                    "%s: %s did not confirm the arrival of %s: %s",
                    self.resource.fragment,
                    receiver.fragment,
                    box.fragment,
                    received.reason,
                )

        try:
            handed = self._giver.hand_over(
                box,
                receiver,
                on_refusal=(
                    "it asked first, so it does not try again"
                    if asked
                    else "asking before trying again"
                ),
                transport=transport.operation,
                entry=reassignment(transport.operation, receiver, capability),
                arrived=arrived,
            )
        except PeerUnreachable as lost:
            # The store admitted the handover and the box has moved: the box and the transport
            # are the receiver's, and only the receiver writes the transport now.
            self._log_handed(box, receiver, asked)
            self._receiver_not_reached(transport, receiver, str(lost))
            return StepResult(accepted=True)
        if handed.accepted:
            self._log_handed(box, receiver, asked)
        return handed

    def _log_handed(self, box: IRI, receiver: IRI, asked: bool) -> None:
        """Log that this module has handed `box` to `receiver`: conveyed once `receiver` had
        reserved itself, or without asking, with `skip-reserve` armed."""
        if asked:
            logger.info("%s", conveyed_record(self.resource, box, receiver))
            return
        logger.info(
            "%s: handed %s to %s without asking its %s, as skip-reserve is armed",
            self.resource.fragment,
            box.fragment,
            receiver.fragment,
            FC.ReserveWorkflow.fragment,
        )

    def _no_route(self, destination: IRI) -> str:
        """Why this module cannot carry a box on toward `destination`: no path leads there."""
        return f"{self.resource.fragment} has no route to {destination.fragment}"

    def _the_wms(self) -> IRI | None:
        """The WarehouseManagementSystem, read from the graph until it is found, then kept."""
        if self._wms is None:
            self._wms = self.graph.wms()
        return self._wms

    def _ask_reserve(self, box: IRI, receiver: IRI, destination: IRI) -> ReserveResult:
        """`receiver`'s answer to Reserve for `box`, bound for `destination`, by which `receiver`
        looks ahead. A receiver that cannot be reached gives no answer: the result is then not
        accepted, says why, names no refusal, and is a `_NotReachable`, so the hop leaves the
        receiver out or asks it no more (changed on 2026-10-11, jms_kapps 77: until then "so the
        hop asks no more"). Nor does a module whose stop has begun: it asks nothing, and its
        answer is "the system is stopping"."""
        if self._stopping():
            return ReserveResult(accepted=False, reason=STOPPING)
        try:
            reserved = ask(
                self.graph,
                self.call,
                receiver,
                FC.ReserveWorkflow,
                ReserveRequest(
                    box=str(box),
                    source_module=str(self.resource),
                    destination=str(destination),
                ),
                ReserveResult,
            )
        except PeerUnreachable as lost:
            return _NotReachable(accepted=False, reason=str(lost))
        if reserved.accepted:
            return reserved
        return ReserveResult(
            accepted=False,
            reason=f"{receiver.fragment} refused to reserve: {reserved.reason}",
            report=reserved.report,
            refusal=reserved.refusal,
        )

    def _reserve_when_free(
        self,
        transport: Transport,
        receiver: IRI,
        detour_to: IRI | None,
        choose_again: ChooseAgain | None = None,
    ) -> tuple[IRI, ReserveResult]:
        """Ask `receiver`'s Reserve for the box of `transport` until it or `detour_to` accepts, and
        the module that accepted: after each refusal of `receiver`, ask `detour_to` once, and if it
        refuses too, wait `retry_seconds` and ask both again, with no bound. A refusal of
        `receiver` that does not pass is the answer once the detour has refused too; one of
        `detour_to` ends its detour.

        The module holds the box while it waits, and writes nothing. On a station where every
        path leads to a module with the exit role, which frees itself by giving back each box
        whose destination it is, the wait ends. (Changed on 2026-10-06, jms_kapps 43, decided by
        Etienne: "if we reserve something and it gets declined we should try a detour before
        asking again". Until then it asked `receiver` alone, "and wait `retry_seconds` after each
        refusal as occupied or reserved".)

        **Routing around a crash**, with `choose_again`: a `receiver` that cannot be reached, or
        refuses "no live route on", is left out for the box, and the module asks at once the
        neighbours that `choose_again` names without each module left out; where it names none,
        the answer is that no live route is left, naming the destination and each module left
        out. Such a detour is left out too, and asked no more. (Added on 2026-10-11, jms_kapps 77,
        decided by Etienne.)

        **The deadlock alarm.** A box that has waited `deadlock_alarm_seconds` since the first ask
        here raises the alarm once, before the next wait: one WARNING line that starts with
        "possible deadlock:", and one `deadlock_alarm` event on the module's handover event
        stream, each naming the module, the box, the time waited and the modules it asks. The box
        keeps waiting. (Added on 2026-10-11, jms_kapps 77, decided by Etienne.)
        """
        box, destination = transport.box, transport.destination
        first_asked = self.clock()
        alarmed = False
        left_out: list[IRI] = []
        while True:
            reserved = self._ask_reserve(box, receiver, destination)
            if reserved.accepted:
                logger.info("%s", reserved_record(self.resource, receiver, box))
                return receiver, reserved
            if not passes(reserved.refusal) and self._stopping():
                # This module asks no Reserve once its stop has begun: no detour either.
                return receiver, reserved
            if choose_again is not None and _not_reachable(reserved):
                self._leave_out(box, receiver, reserved.reason, left_out)
                asked = choose_again(left_out)
                if not asked:
                    return receiver, ReserveResult(
                        accepted=False, reason=no_live_route(destination, left_out)
                    )
                receiver, detour_to = asked[0], asked[1] if len(asked) > 1 else None
                continue
            if passes(reserved.refusal):
                self.memory.refused(box, reserved.refusal)
            last = reserved
            asking = [receiver]
            if detour_to is not None:
                logger.info(
                    "%s: %s; asking %s instead",
                    self.resource.fragment,
                    reserved.reason,
                    detour_to.fragment,
                )
                answer = self._ask_reserve(box, detour_to, destination)
                if answer.accepted:
                    logger.info("%s", reserved_record(self.resource, detour_to, box))
                    self.memory.detoured(box)
                    return detour_to, answer
                if choose_again is not None and _not_reachable(answer):
                    self._leave_out(box, detour_to, answer.reason, left_out)
                    detour_to = None
                elif not passes(answer.refusal):
                    logger.info(
                        "%s: %s; it asks %s no more for %s",
                        self.resource.fragment,
                        answer.reason,
                        detour_to.fragment,
                        box.fragment,
                    )
                    detour_to = None
                else:
                    self.memory.detour_refused(box, answer.refusal)
                    last = answer
                    asking.append(detour_to)
            if not passes(reserved.refusal):
                return receiver, reserved
            if not alarmed:
                alarmed = self._alarm_if_waited_long(transport, first_asked, asking)
            logger.info(
                "%s: %s; asking again in %g s",
                self.resource.fragment,
                last.reason,
                self.retry_seconds,
            )
            self.memory.waited(box)
            self.wait(self.retry_seconds)

    def _alarm_if_waited_long(
        self, transport: Transport, first_asked: float, asking: Sequence[IRI]
    ) -> bool:
        """Raise the deadlock alarm for the box of `transport`, which this module holds, if it has
        waited `deadlock_alarm_seconds` since `first_asked`, the module's first ask of a Reserve
        for it, and asks `asking` now. Returns whether it raised it."""
        waited = self.clock() - first_asked
        if waited < self.deadlock_alarm_seconds:
            return False
        what = (
            f"{self.resource.fragment} has waited {round(waited, 1):g} s to hand "
            f"{transport.box.fragment} on, asking "
            f"{listed([module.fragment for module in asking])}"
        )
        logger.warning("%s %s", POSSIBLE_DEADLOCK, what)
        if self.events is not None:
            self.events.deadlock_alarm(
                box=transport.box,
                transport=transport.operation,
                asked=asking,
                reason=what,
            )
        return True

    def _notify(self, operation: IRI, resource: IRI) -> None:
        try:
            self.notify(operation, resource)
        except NotificationUndelivered as error:
            logger.warning(
                "%s: %s was not notified of %s: %s",
                self.resource.fragment,
                resource.fragment,
                operation.fragment,
                readable_reason(error),
            )

    def _take(self, box: IRI) -> _Reservation | None:
        """`box` has arrived: it leaves the waiting order, and the reservation for it, if there
        is one, is used up and returned."""
        with self._reserving:
            self._waiting.leave(box)
            used = self._reservation
            if used is not None and used.box == box:
                self._reservation = None
                self._reserving.notify_all()
                return used
            return None

    @contextmanager
    def _at_work(self) -> Iterator[None]:
        """Count this module's own work while the block runs, so that its stop waits for it."""
        self._begin_work()
        try:
            yield
        finally:
            self._end_work()

    def _begin_work(self) -> None:
        with self._idle:
            self._working += 1

    def _end_work(self) -> None:
        with self._idle:
            self._working -= 1
            self._idle.notify_all()

    def _carry_on_arrived(self, box: IRI, giver: IRI, ahead: IRI | None) -> None:
        """Carry on `box`, which `giver` has passed to this module and whose Receive it has
        called: read the box with its transport, through the box's link to it, and run the
        BoxTransportWorkflow with that transport, toward `ahead`, if this module reserved that
        module ahead for the box. It ends the work that the Receive began."""
        try:
            with self._arriving:
                transport = self.graph.transport_of(box)
            if transport is None:
                logger.warning(
                    "%s: %s arrived from %s, and the graph names no transport of it",
                    self.resource.fragment,
                    box.fragment,
                    giver.fragment,
                )
                return
            self.box_transport(transport, came_from=giver, ahead=ahead)
        finally:
            self._end_work()

    def _bound_passed(self, reservation: _Reservation) -> None:
        """The bound of `reservation` has passed, and its box may not have arrived: free the
        reservation and read the graph. If the box is this module's and its transport has not
        ended, its giving module was lost after its commit: end the transport `failed`, with a
        reason that names the giving module. A reservation that its Receive or the
        WarehouseManagementSystem's notification used up, or that a newer one replaced, is left
        alone.

        The WarehouseManagementSystem owns the station's entry: it ends the transport of its box
        itself when it cannot notify the origin, and this module leaves such a transport alone
        (added on 2026-10-06, jms_kapps 41, decided by Etienne). A transport of its box that has
        not ended this module leaves to it too, and writes nothing: the build's reading of that
        decision, where the ticket names only the transport that has ended."""
        self._lapse(
            reservation,
            lost_giver(reservation.giver, reservation.box, reservation.bound),
            f"within {reservation.bound:g} s",
        )

    def _lapse(self, reservation: _Reservation, reason: str, when: str) -> None:
        """Free `reservation`, unless it was used up or replaced, and read the graph: end the
        transport of its box `failed` with `reason` if the box is this module's, came from a
        module and its transport has not ended; leave a box of the WarehouseManagementSystem to
        it. `when` says, in the log, by when the box did not come."""
        with self._arriving:
            with self._reserving:
                if self._reservation is not reservation:
                    return
                self._reservation = None
                self._reserving.notify_all()
                # Its giver is lost, or ended the transport: the box asks no more.
                self._waiting.leave(reservation.box)
            box, giver = reservation.box, reservation.giver
            here = box in self.graph.boxes_held_by(self.resource)
            if here and reservation.for_a_spawn:
                self._not_notified(reservation, when)
                return
            if here:
                self._giver_lost(box, reason)
                return
        logger.warning(
            "%s: %s did not come from %s %s, so it frees its reservation",
            self.resource.fragment,
            box.fragment,
            giver.fragment,
            when,
        )

    def _not_notified(self, reservation: _Reservation, when: str) -> None:
        """The WarehouseManagementSystem created the box of `reservation` on this module, the
        origin, and its notification has not come `when` the log says: leave the transport to
        the WarehouseManagementSystem, and say why. A transport it ended is left alone as any
        other that has ended."""
        box, wms = reservation.box, reservation.giver
        self.memory.holding(box)
        transport = self.graph.transport_of(box)
        if transport is None:
            logger.warning(
                "%s: %s is here, and the graph names no transport of it",
                self.resource.fragment,
                box.fragment,
            )
            return
        if _has_ended(transport):
            self._leave(transport, str(transport.status))
            return
        logger.warning(
            "%s: %s did not notify it of %s %s, so it frees its reservation and leaves the "
            "transport to %s",
            self.resource.fragment,
            wms.fragment,
            transport.operation.fragment,
            when,
            wms.fragment,
        )
