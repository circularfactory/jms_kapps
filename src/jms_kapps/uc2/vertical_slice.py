"""`jms-uc2 slice`: the smallest use case 2 that proves every seam: two modules, one box, one handover.

The slice seeds the graph, starts each module as its own process, and waits until both have
registered their workflows. It checks that each module registered its own service, with an
address and a heartbeat. Then it plays two acts and checks the graph after each.

The slice plays the part of the WarehouseManagementSystem with a middleware of its own, which it
never serves. Through it, it places each box on module 1 with `mw.create()`, together with the box's
transport, `queued`, toward module 2 and assigned to module 1, and notifies module 1 of the
transport, as the WarehouseManagementSystem notifies a box's origin. The slice runs no
WarehouseManagementSystem, and the one in the graph has no handover ability, so the handover that
would give a box back is refused before any write: module 2, the destination, keeps the box and
records its transport `failed`. That is what keeps module 2 occupied for the fault.

1. **A clean handover.** Box 1 goes from module 1 to module 2: module 1 asks module 2's Reserve,
   commits the handover, conveys the box, its own move, and calls module 2's Receive, from which
   module 2 carries the box on. Module 2 logs each step it accepts, and the slice reads those
   records: they show that Reserve and Receive ran on module 2, not only that module 1 called
   them. (Changed on 2026-10-06, jms_kapps 40, decided by Etienne. Until then: "Box 1 goes from
   module 1 to module 2 through Reserve, Convey and Receive.")
2. **The fault.** Box 2 is placed on module 1, the fault `skip-reserve` is armed there, and
   module 1 hands box 2 to module 2 without asking. Module 2 already holds box 1, so the triple
   store refuses the write before anything moves. Module 1 logs the report, and emits the refusal
   with it on its handover event stream. Nothing in the graph changes but box 2's transport, which
   module 1 set running: the middleware commits the change of possession in one write, before the
   box moves, so a refusal leaves nothing behind. Module 1 then adapts: it asks module 2's Reserve
   first, which refuses because module 2 is occupied, and asks again after the retry time. Module 2
   never gives box 1 back in the slice, so module 1 waits with box 2, its transport running, until
   the slice stops.

The report is checked as RDF, not as text: one of its validation results must name module 2 as
the focus node, the possession shape's whole path, and that shape as its source. A search of the
text would not do. The store puts the source shape's own description into the report, path
included, so every name can appear in the text while no result names it.

The slice reaches each module the way the parties do: through the graph, which also says where
module 1's handover event stream lives. It exits 0 when both acts behave as described, and 1 when
one does not.

Ctrl+C, SIGTERM, which `kill` sends, and SIGHUP, which a closing terminal sends, stop the slice
the same way, as they stop the Launcher, through the same helper (`one_stop`): the slice stops
both modules, each deregisters its service, and it exits with 128 plus the signal's number, as a
shell reports a process that the signal ended. A signal during the seed ends the slice there,
before any module starts. A second signal does not cut the stop short. A slice started with SIGHUP
ignored, as under `nohup`, keeps ignoring it. (Changed on 2026-10-11, jms_kapps 71, decided by
Etienne: until then only Ctrl+C stopped the slice and its modules together, and SIGTERM or SIGHUP
to the slice alone ended it at once while its modules ran on. The exit code of a stopped slice is
the build's reading of "non-zero, as on Ctrl+C". Until then the paragraph above ended "It exits 0
when both acts behave as described, and 1 otherwise.")
"""

from __future__ import annotations

import contextlib
import re
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime

from kapps_ogm import OGM
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import endpoint_for
from kapps_semantic_middleware.validation_report import (
    ValidationResult,
    validation_results,
)
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI, SPARQLQuery, TripleStore

from jms_kapps.common.process import one_stop
from jms_kapps.common.services import Service, reader, services
from jms_kapps.common.supervision import (
    LaunchFailed,
    LiveInstanceError,
    NotReady,
    Supervisor,
    refuse_if_live,
)
from jms_kapps.common.triplestore import Queryable, bindings, connect
from jms_kapps.uc2.discovery import (
    boxes_held_by,
    capability,
    handover_event_stream,
    service_address,
)
from jms_kapps.uc2.handover_events import HandoverEvent, read_sse
from jms_kapps.uc2.handshake import acceptance_record
from jms_kapps.uc2.module import DEFAULT_RETRY_SECONDS, Fault
from jms_kapps.uc2.module import logger as module_logger
from jms_kapps.uc2.process import MODULE_WORKFLOWS, module_command, set_fault_switch
from jms_kapps.uc2.seed import DEFAULT_TRANSIT_SECONDS, seed_two_modules
from jms_kapps.uc2.transport import TransportRecords, operation_status
from jms_kapps.uc2.vocabulary import FC, WMS, box_iri

REGISTRATION_TIMEOUT_SECONDS = 60.0

# How long a module's log may take to pass through the supervisor after the call that wrote it.
LOG_TIMEOUT_SECONDS = 5.0

# How long the modules may take to act on a notification or a Receive, beyond the moves: each act
# is one hop at most.
ACT_TIMEOUT_SECONDS = 30.0

# The path of the shape that allows a module one box at a time: from the module to its one
# possession state, then back from the state to each box that points at it. A handover changes
# only the box's link, so a second box handed to the module shows up here.
POSSESSION_PATH = f"(<{CFC.hasPossessor}>/^<{CFC.hasPossessedWorkpiece}>)"


class Failure(Exception):
    """An act did not behave as the slice claims."""


# What the slice prints once its stop has begun, before it asks either module to stop.
STOPPING_MESSAGE = "Stopping both modules..."


class Stopped(BaseException):
    """A stop signal reached the slice, and cuts short what its main thread does, as Ctrl+C does.

    A BaseException, as KeyboardInterrupt is, so that no `except Exception` on the way, such as
    the readiness gate's around each read of the graph, takes it for an error and goes on.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def stopped_code(signum: int) -> int:
    """The exit code of a slice that the stop signal `signum` stopped: 128 plus its number, as a
    shell reports a process that the signal ended. So a shell shows the code it showed before the
    slice took these signals: 130 on Ctrl+C, 143 on SIGTERM, 129 on SIGHUP."""
    return 128 + signum


class _StopSignals:
    """What the first stop signal does to the slice, which takes every stop signal as one stop
    (`one_stop`), as the Launcher does.

    It begins the supervisor's stop first, so that a module that the same signal ended meanwhile,
    as a terminal's Ctrl+C ends both, has stopped, not failed. Inside `cut_short` it then raises
    `Stopped` on the main thread, as Ctrl+C raises KeyboardInterrupt, and the slice goes on to stop
    both modules. Outside it only marks the stop: while a module starts, since a module whose
    process has started before the supervisor holds it would escape the stop, and while the modules
    stop, which a signal must not cut short.
    """

    def __init__(self, supervisor: Supervisor) -> None:
        self._supervisor = supervisor
        self._cut_short = False
        self._signum: int | None = None

    def begin(self, signum: int) -> None:
        """`one_stop` calls this for the first stop signal alone."""
        self._signum = signum
        self._supervisor.begin_stop()
        if self._cut_short:
            raise Stopped(signum)

    @contextlib.contextmanager
    def cut_short(self) -> Iterator[None]:
        """While in this block, a stop signal cuts short what the main thread does. One that came
        before the block cuts it short at its start."""
        self._cut_short = True
        try:
            if self._signum is not None:
                raise Stopped(self._signum)
            yield
        finally:
            self._cut_short = False


# -- Reading what the modules say ------------------------------------------------------------ #

# A line that is one N-Quads statement: a line of a logged report.
_STATEMENT = re.compile(r"(?:_:\S+|<[^<>\s]*>)\s+<[^<>\s]*>\s+.*\.\s*$")


def lines_of(echoed: Iterable[str], child: str) -> list[str]:
    """The lines `child` wrote, out of what a supervisor echoed: it puts the name in front."""
    prefix = f"{child}  "
    return [line.removeprefix(prefix) for line in echoed if line.startswith(prefix)]


def logged_report(lines: Iterable[str]) -> str:
    """The report a module logged, out of its process's output lines.

    A record of the module's logger starts on the line that names the logger, and its report
    follows on the next lines, one statement each. Another logger's record does not count,
    although the client logs the store's error, report included, before the module does.
    """
    report: list[str] = []
    inside = False
    for line in lines:
        if module_logger.name in line:
            inside = True
        elif inside and _STATEMENT.match(line):
            report.append(line)
        else:
            inside = False
    return "\n".join(report)


def logs_acceptance(
    lines: Iterable[str], module: IRI, step: IRI, box: IRI, giver: IRI
) -> bool:
    """Whether `module`'s output lines hold its own record that it accepted `step` for `box` from
    `giver`.

    The record is matched whole, as the module builds it, right after the name of the module's
    logger: Python's default format, which the module's process keeps, puts the name just before
    the message. So a record with another verb, such as a refusal of the same step, does not
    count, and neither does one from another giver or another logger.
    """
    record = f"{module_logger.name}:{acceptance_record(module, step, box, giver)}"
    return any(line.endswith(record) for line in lines)


_OUTPUT = threading.Lock()


def say(text: str) -> None:
    """Print `text` on stdout, each line whole.

    The supervisor's drain threads print while the acts do. `print` writes the text and the line
    end in two calls, and on an unbuffered stdout another thread's line can come between them.

    A terminal that has closed takes no line: the write fails, and the line is dropped, as the
    supervisor drops it. So the stop that the terminal's SIGHUP began goes on, and a drain thread
    goes on reading its module's output, whose pipe would otherwise fill and block the module.
    (Added on 2026-10-11, jms_kapps 71: the slice takes SIGHUP as a stop signal now.)
    """
    with _OUTPUT, contextlib.suppress(OSError):
        print(text, flush=True)


class Transcript:
    """The supervisor's echo: prints every line as it comes, and keeps it for the checks."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, line: str) -> None:
        say(line)
        with self._lock:
            self._lines.append(line)

    def lines_of(self, child: str) -> list[str]:
        with self._lock:
            return lines_of(self._lines, child)


def logged_in_time(
    condition: Callable[[], bool], timeout: float = LOG_TIMEOUT_SECONDS
) -> bool:
    """Whether `condition`, a check of what a module logged, holds within `timeout` seconds.

    A module logs before it answers, but its lines reach this process through the supervisor, on
    a thread of their own.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


# -- Registration ---------------------------------------------------------------------------- #


def services_of(db: Queryable, resource: IRI) -> list[Service]:
    """Every service of `resource`, live or not, each with its address and heartbeat where it has
    one: the one reader's answer, which a check reads after the modules have stopped too."""
    return services(db, resource=resource, live_only=False)


def set_fault(db: TripleStore, module: IRI, name: Fault, armed: bool) -> None:
    address = service_address(db, module)
    assert address is not None
    set_fault_switch(address, name, armed)


def refusals_emitted(db: TripleStore, module: IRI) -> list[HandoverEvent]:
    """The `commit_refused` events on `module`'s handover event stream, found through the graph.

    One connection reads every event kept, and ends after the stream's wait.
    """
    url = handover_event_stream(db, module)
    if url is None:
        return []
    events = [
        HandoverEvent.model_validate_json(data) for _, data in read_sse(url, None)
    ]
    return [event for event in events if event.kind == "commit_refused"]


def asked_again(lines: Iterable[str], refused: str) -> int:
    """How often a module logged that it will ask again after the refusal `refused`."""
    waiting = f"{refused}; asking again in "
    return sum(1 for line in lines if waiting in line)


def holdings(db: TripleStore, modules: tuple[IRI, ...]) -> dict[str, list[str]]:
    return {
        m.fragment: sorted(b.fragment for b in boxes_held_by(db, m)) for m in modules
    }


def explicit_statements(
    db: TripleStore, outcome_of: IRI | None = None
) -> set[tuple[str, str, str]]:
    """Every explicit statement of the default graph, except heartbeats, which move on their own,
    and the status and result of the transport `outcome_of`, if given, which records an act's
    outcome."""
    clauses = ["?s ?p ?o", f"FILTER (?p != <{SVC.lastHeartbeat}>)"]
    if outcome_of is not None:
        clauses.append(
            f"""FILTER (?s != <{outcome_of}> ||
                    ?p NOT IN (<{SVC.operationStatus}>, <{SVC.executionResult}>))"""
        )
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["?s", "?p", "?o"],
            where_clauses=clauses,
            include_implicit=False,
        ),
        # Each term's lexical value, compared as it is written, with no conversion in between.
        convert=False,
    )
    return {(row["s"]["value"], row["p"]["value"], row["o"]["value"]) for row in rows}


def check(
    description: str, condition: bool, detail: Callable[[], str] = lambda: ""
) -> None:
    say(f"  {'ok  ' if condition else 'FAIL'} {description}")
    if not condition:
        raise Failure(f"{description}\n{detail()}")


def check_services(db: TripleStore, modules: tuple[IRI, ...], since: datetime) -> None:
    """Each module registered its own service, with an address and a heartbeat since `since`."""
    seen: set[IRI] = set()
    for module in modules:
        found = services_of(db, module)
        _check_service(module, found, seen, since)
        # The check held, so `found` holds exactly one service.
        seen.add(found[0].iri)


def _check_service(
    module: IRI, found: list[Service], others: set[IRI], since: datetime
) -> None:
    """Check that `found` holds one service, not in `others`, and live since `since`."""
    service = found[0] if len(found) == 1 else None
    heartbeat = service.heartbeat if service else None
    say(
        f"  {module.fragment}: {service.iri if service else None} at "
        f"{service.address if service else None}, heartbeat {heartbeat}"
    )
    check(
        f"{module.fragment} registered its own service, with an address and a heartbeat",
        service is not None
        and service.iri not in others
        and service.address is not None
        and heartbeat is not None
        and heartbeat >= since,
        lambda: f"services: {found}\nother modules' services: {sorted(others)}",
    )


def check_accepted(
    transcript: Transcript, peer: IRI, step: IRI, box: IRI, giver: IRI
) -> None:
    """Check that `peer` ran `step` for `box` itself: its own log says that it accepted it from
    `giver`."""
    check(
        f"{peer.fragment} logs that it accepted {step.fragment} for {box.fragment} "
        f"from {giver.fragment}",
        logged_in_time(
            lambda: logs_acceptance(
                transcript.lines_of(peer.fragment), peer, step, box, giver
            )
        ),
        lambda: "\n".join(transcript.lines_of(peer.fragment)),
    )


def holds_within(condition: Callable[[], bool], timeout: float) -> bool:
    """Whether `condition`, a check of the graph, holds within `timeout` seconds.

    The modules act on a notification or a Receive in the background, so what an act changes
    holds a little later.
    The slice is no module, so it may ask the graph until then.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def place(
    records: TransportRecords,
    box: IRI,
    modules: tuple[IRI, IRI],
    transport_capability: IRI,
) -> IRI:
    """Place `box` on the first module, with its transport `queued` toward the second, in one
    `mw.create()`, and return the transport: the slice knows it from the opening, never from the
    box's name."""
    m1, m2 = modules
    return records.open(
        box, origin=m1, destination=m2, capability=transport_capability, possessor=m1
    )


def notify(mw: SemanticMiddleware, operation: IRI, module: IRI) -> None:
    say(f"  notify {module.fragment} of {operation.fragment}")
    mw.notify(operation=operation, resource=module)


def play(
    db: TripleStore,
    mw: SemanticMiddleware,
    modules: tuple[IRI, IRI],
    transcript: Transcript,
) -> None:
    """The two acts. Each raises `Failure` at the first check that does not hold."""
    m1, m2 = modules
    b1, b2 = box_iri(1), box_iri(2)
    occupied = f"{m2.fragment} refused to reserve: occupied by {b1.fragment}"
    records = TransportRecords(mw)
    transport_capability = capability(db, m1, FC.BoxTransportCapability)
    if transport_capability is None:
        raise Failure(f"{m1.fragment} registered no BoxTransportCapability")

    def failed(operation: IRI) -> Callable[[], bool]:
        return lambda: operation_status(db, operation) == OperationStatus.FAILED

    say("\n1. A clean handover")
    t1 = place(records, b1, modules, transport_capability)
    say(f"  before: {holdings(db, modules)}")
    notify(mw, t1, m1)
    check(
        f"{m2.fragment} keeps {b1.fragment}, and its transport fails: the WMS in the slice's "
        "graph has no handover ability, so it takes no box back",
        holds_within(failed(t1), ACT_TIMEOUT_SECONDS),
        lambda: f"{t1.fragment} is {operation_status(db, t1)}",
    )
    check_accepted(transcript, m2, FC.ReserveWorkflow, b1, giver=m1)
    check_accepted(transcript, m2, FC.ReceiveWorkflow, b1, giver=m1)
    after = holdings(db, modules)
    say(f"  after:  {after}")
    check(
        f"the graph shows {m2.fragment} holding {b1.fragment}",
        after == {m1.fragment: [], m2.fragment: [b1.fragment]},
    )

    say(f"\n2. The fault: {m1.fragment} hands {b2.fragment} over without asking")
    t2 = place(records, b2, modules, transport_capability)
    before = holdings(db, modules)
    say(f"  before: {before}")
    set_fault(db, m1, "skip-reserve", armed=True)
    graph_before = explicit_statements(db, outcome_of=t2)
    notify(mw, t2, m1)
    emitted: list[HandoverEvent] = []

    def refused_b2() -> bool:
        emitted[:] = [e for e in refusals_emitted(db, m1) if e.box == str(b2)]
        return bool(emitted)

    # An event carries a report only when the store refused: that, not the reason's prose, says so.
    check(
        f"the triple store refuses it, and {m1.fragment} emits the refusal with its report",
        holds_within(refused_b2, ACT_TIMEOUT_SECONDS),
        lambda: f"{t2.fragment} is {operation_status(db, t2)}",
    )
    event = emitted[0]
    report = event.refusal.report if event.refusal else None
    say(f"  {m1.fragment} emitted: {event.reason}, peer {event.peer}")
    check(
        f"the event names {b2.fragment}'s transport and {m2.fragment}",
        (event.transport, event.peer) == (str(t2), str(m2)),
        lambda: event.model_dump_json(indent=2),
    )
    results = validation_results(report or "")
    for found in sorted(results):
        say(f"  {found}")
    refused = ValidationResult(
        focus=str(m2),
        path=POSSESSION_PATH,
        shape=str(FC.FlexConveyorModulePossessionShape),
    )
    at_focus = {found for found in results if found.focus == refused.focus}
    check("the report names the focus node", bool(at_focus), lambda: report or "")
    check(
        "the report names the whole constraint path",
        any(found.path == refused.path for found in at_focus),
        lambda: report or "",
    )
    check("the report names the source shape", refused in results, lambda: report or "")
    check(
        f"{m1.fragment} logs the report",
        logged_in_time(
            lambda: (
                refused
                in validation_results(logged_report(transcript.lines_of(m1.fragment)))
            )
        ),
        lambda: "\n".join(transcript.lines_of(m1.fragment)),
    )
    check(
        "the event names the rule the report names",
        event.refusal is not None
        and any(
            (rule.shape, rule.focus_node, rule.path)
            == (refused.shape, refused.focus, refused.path)
            for rule in event.refusal.results
        ),
        lambda: event.model_dump_json(indent=2),
    )
    check(
        f"{m1.fragment} adapts: it asks first, and asks again after the retry time while "
        f"{m2.fragment} is occupied",
        logged_in_time(
            lambda: asked_again(transcript.lines_of(m1.fragment), occupied) >= 2,
            timeout=LOG_TIMEOUT_SECONDS + 2 * DEFAULT_RETRY_SECONDS,
        ),
        lambda: "\n".join(transcript.lines_of(m1.fragment)),
    )
    check(
        f"{b2.fragment} waits on {m1.fragment}, and its transport runs: it has not failed",
        holdings(db, modules)
        == {m1.fragment: [b2.fragment], m2.fragment: [b1.fragment]}
        and operation_status(db, t2) == OperationStatus.RUNNING,
        lambda: f"{holdings(db, modules)}; {t2.fragment} is {operation_status(db, t2)}",
    )
    graph_after = explicit_statements(db, outcome_of=t2)
    check(
        "the graph is unchanged, but for the transport's outcome",
        graph_after == graph_before,
        lambda: (
            f"added: {graph_after - graph_before}\nremoved: {graph_before - graph_after}"
        ),
    )


def run(
    dataset: str,
    *,
    force: bool,
    transit_seconds: float = DEFAULT_TRANSIT_SECONDS,
) -> int:
    """Play the two acts against `dataset`, which it clears, with both modules at the transit
    time `transit_seconds`, which its seed writes into the graph, until a stop signal stops it.

    Returns the exit code: 0 when both acts behave as described, 1 when one does not, and
    `stopped_code` of the signal that stopped it.
    """
    db = connect(dataset)
    try:
        refuse_if_live(reader(db), dataset=dataset, force=force)
    except LiveInstanceError as error:
        print(
            f"jms-uc2 slice: {error} Or pass --force to clear it anyway.",
            file=sys.stderr,
        )
        return 1
    except LaunchFailed as error:
        print(f"jms-uc2 slice: {error}", file=sys.stderr)
        return 1
    started = datetime.now(UTC)
    transcript = Transcript()
    supervisor = Supervisor(reader(db), echo=transcript)
    signals = _StopSignals(supervisor)
    with one_stop(signals.begin):
        try:
            with signals.cut_short():
                say(f"Seeding {dataset} on {endpoint_for(dataset).url}")
                modules = seed_two_modules(db, transit_seconds=transit_seconds)
            # Not cut short: a signal meanwhile cuts short what follows, at its start.
            for module in modules:
                supervisor.spawn(
                    module,
                    module.fragment,
                    module_command(module, dataset),
                    workflows=MODULE_WORKFLOWS,
                )
            with signals.cut_short():
                try:
                    supervisor.wait_until_ready(REGISTRATION_TIMEOUT_SECONDS)
                except NotReady as not_ready:
                    raise Failure(f"not registered in time: {not_ready}") from not_ready
                say("Both modules registered their workflows.")
                check_services(db, modules, since=started)
                # The WMS's part, played by the slice: never served, so no notification reaches
                # it.
                wms = SemanticMiddleware(
                    mode=Mode.RESOURCE,
                    resource_iri=str(WMS),
                    service_class=str(FC.WarehouseManagementSystemService),
                    ogm=OGM(db=db),
                )
                play(db, wms, modules, transcript)
            code = 0
        except Stopped as stopped:
            say(f"\nStopped by {signal.Signals(stopped.signum).name}.")
            code = stopped_code(stopped.signum)
        except Failure as failure:
            print(f"\nThe slice does not hold: {failure}", file=sys.stderr)
            code = 1
        finally:
            say(f"\n{STOPPING_MESSAGE}")
            supervisor.stop_all()
    if code == 0:
        say("\nBoth acts behaved as claimed.")
    return code
