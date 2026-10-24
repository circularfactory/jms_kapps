"""What the monitor knows, read in the background: the graph, the handovers it sees in it, and the
participants' refusals.

A page poll reads only this memory, so an open tab causes no load: the reads below run on their
own cadence whether a tab is open or not.

It has two modes, which a page switches: **"graph + streams"**, and **"graph only"**, in which the
monitor process starts unless the Launcher is told to collect the refusals (the memory itself
starts in "graph + streams" unless it is given a mode). In "graph only" it sees what a dashboard
sees that sees only the knowledge graph. Both read the graph; only "graph + streams" finds and
follows the streams. A page also sets the interval of the graph read, from 0.5 s to 10 s. A
change applies at once.

- **Every second**, or at the interval a page set, the graph: the modules and their connections,
  which participants are live (an address and a heartbeat younger than 90 s), every box with its
  transport and possessor, and the WarehouseManagementSystem by its class. A read that fails keeps
  what the last one read, and says why.
- **At each read of the graph**, the handovers: each box whose possessor changed since the last
  read that did not fail, and each new box on its origin (`handovers.py`). The first read has
  nothing to compare with, so it lists none. It keeps the 50 newest, newest first.
- **Every 5 s in "graph + streams"**, the streams: where each participant serves its handover
  event stream, through `fc:hasHandoverEventStream`. The monitor follows the stream of every live
  participant, each on a thread of its own, and lets go of a participant that is no longer live. A
  follower reads a connection to its end, which the participant makes after at most 2 s of
  waiting, and connects again at once with the id of the last event it read. A stream that cannot
  be read is tried again after a second. A switch to "graph only" lets go of every stream at once,
  and drops what a follower reads after it.
- It keeps the 50 newest `commit_refused` events, newest first by their time, each with the
  rules its report names and each of its results as a person reads it, read once when it
  arrives, with the middleware's reader of a validation report. A participant that is followed
  again, after it was offline or after "graph only", is read on after the last event read of it,
  so no refusal comes twice. In "graph only" a snapshot holds none: a refused write leaves no
  trace in the graph. The refusals kept show again in "graph + streams".
- It keeps each `deadlock_alarm` event, once, while the graph shows its box on the module that
  raised it: a read of the graph that shows the box elsewhere, or not at all, drops the alarm for
  good. In "graph only" a snapshot holds none, since an alarm is no fact of the graph, and the
  alarms kept show again in "graph + streams". The page says why none shows, as for refusals.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

from kapps_semantic_middleware import validation_report
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.handover_events import (
    DeadlockAlarm,
    HandoverEvent,
    event_from_json,
    read_sse,
)
from jms_kapps.uc2.monitor.handovers import SeenHandover, handovers_between
from jms_kapps.uc2.monitor.reads import STREAMS_READ, BoxRow, ModuleRow, SentQuery

logger = logging.getLogger(__name__)

GRAPH_SECONDS = 1.0
DISCOVER_SECONDS = 5.0
# The intervals of the graph read a page may set, in seconds, both included.
SHORTEST_GRAPH_SECONDS = 0.5
LONGEST_GRAPH_SECONDS = 10.0
KEPT_REFUSALS = 50
KEPT_HANDOVERS = 50
KEPT_ALARMS = 50

# How long a follower waits before it reads a stream again that could not be read.
RETRY_SECONDS = 1.0

# The monitor's two modes. In "graph only" it sees what a dashboard sees that sees only the
# knowledge graph: it follows no handover event stream. In "graph + streams" it follows them too,
# for the refusals, the one view that does not come from the graph.
Mode = Literal["graph + streams", "graph only"]
GRAPH_AND_STREAMS: Mode = "graph + streams"
GRAPH_ONLY: Mode = "graph only"


class MonitorReads(Protocol):
    """What the monitor reads from the graph. `reads.GraphReads` is the real one."""

    def modules(self) -> list[ModuleRow]: ...

    def live_resources(self) -> set[IRI]: ...

    def boxes(self) -> list[BoxRow]: ...

    def wms(self) -> set[IRI]: ...

    def streams(self) -> dict[IRI, str]: ...

    def sent(self) -> list[SentQuery]: ...


ReadStream = Callable[[str, str | None], Iterator[tuple[str, str]]]
"""Reads one connection of the stream at a URL, resuming after the event id given: the `(id,
data)` of each event, until the participant ends the connection."""


@dataclass(frozen=True)
class KeptRefusal:
    """A `commit_refused` event the monitor keeps, with what a person reads of its report: the
    rules it names, each shape at its focus node, and each of its results in full. `results` is
    None when the report is no RDF."""

    event: HandoverEvent
    rules: tuple[str, ...]
    results: tuple[validation_report.ReadableResult, ...] | None


@dataclass(frozen=True)
class Snapshot:
    """What the monitor knows now, and how it reads. `refusals` and `handovers` are newest
    first; `error` says why the last read of the graph failed, if it did. In "graph only"
    `refusals` is empty: a refused write leaves no trace in the graph. `queries` is the query each
    read sent last. `alarms` are the deadlock alarms whose box the last read of the graph shows on
    the module that raised them, oldest first, and none in "graph only"."""

    modules: tuple[ModuleRow, ...] = ()
    live: frozenset[IRI] = frozenset()
    boxes: tuple[BoxRow, ...] = ()
    refusals: tuple[KeptRefusal, ...] = ()
    handovers: tuple[SeenHandover, ...] = ()
    alarms: tuple[DeadlockAlarm, ...] = ()
    error: str | None = None
    mode: Mode = GRAPH_AND_STREAMS
    graph_seconds: float = GRAPH_SECONDS
    discover_seconds: float = DISCOVER_SECONDS
    queries: tuple[SentQuery, ...] = ()

    def every(self, query: SentQuery) -> float | None:
        """How often the monitor sends `query` now, in seconds. The query of the read `streams`
        goes at each discovery, which "graph only" does not make, so then it is not sent (None);
        every other query goes at each read of the graph."""
        if query.read == STREAMS_READ:
            return self.discover_seconds if self.mode == GRAPH_AND_STREAMS else None
        return self.graph_seconds


@dataclass
class _Follower:
    url: str
    stop: threading.Event = field(default_factory=threading.Event)


class MonitorMemory:
    """The monitor's memory, and the background task that fills it from `reads` and from the
    participants' streams, read with `read_stream`."""

    def __init__(
        self,
        reads: MonitorReads,
        *,
        read_stream: ReadStream = read_sse,
        mode: Mode = GRAPH_AND_STREAMS,
        graph_seconds: float = GRAPH_SECONDS,
        discover_seconds: float = DISCOVER_SECONDS,
    ) -> None:
        self.reads = reads
        self.read_stream = read_stream
        self._graph_seconds = graph_seconds
        self.discover_seconds = discover_seconds
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        # Oldest first by their time; at most KEPT_REFUSALS.
        self._refusals: list[KeptRefusal] = []
        # Oldest first by their time; at most KEPT_ALARMS. Only those whose box the last read of
        # the graph that did not fail shows on the module that raised them.
        self._alarms: list[DeadlockAlarm] = []
        # Oldest first, as the reads saw them; at most KEPT_HANDOVERS. None until the first read
        # that did not fail, which has nothing to compare with.
        self._handovers: list[SeenHandover] | None = None
        self._followers: dict[IRI, _Follower] = {}
        # The id of the last event read of each participant, kept when it is no longer followed.
        self._last_read: dict[IRI, str] = {}
        self._mode: Mode = mode
        # When the background task reads the graph next, and finds the streams next, in
        # `time.monotonic()`: 0 is at once.
        self._next_read = 0.0
        self._next_discovery = 0.0
        self._stop = threading.Event()
        # Wakes the background task from its wait between two reads: at a stop, and when the mode
        # or the interval changes, so that the change applies at once.
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- What a page reads ------------------------------------------------------------------- #

    def snapshot(self) -> Snapshot:
        """What the monitor knows now. Reads only memory: the reads' own, for the queries.

        In "graph only" it holds no refusal and no alarm. The monitor keeps those it read, and
        shows them again in "graph + streams".
        """
        queries = tuple(self.reads.sent())
        with self._lock:
            streams = self._mode == GRAPH_AND_STREAMS
            held = {row.box: row.holder for row in self._snapshot.boxes}
            return replace(
                self._snapshot,
                refusals=tuple(reversed(self._refusals)) if streams else (),
                alarms=tuple(
                    alarm
                    for alarm in self._alarms
                    if streams and held.get(IRI(alarm.box)) == IRI(alarm.resource)
                ),
                handovers=tuple(reversed(self._handovers or ())),
                mode=self._mode,
                graph_seconds=self._graph_seconds,
                discover_seconds=self.discover_seconds,
                queries=queries,
            )

    @property
    def mode(self) -> Mode:
        """Whether the monitor follows the participants' streams beside the graph, or not."""
        with self._lock:
            return self._mode

    @property
    def graph_seconds(self) -> float:
        """How long the background task waits between two reads of the graph."""
        with self._lock:
            return self._graph_seconds

    def following(self) -> dict[IRI, str]:
        """The participants whose streams are followed, with where."""
        with self._lock:
            return {p: f.url for p, f in self._followers.items()}

    # -- What a page sets -------------------------------------------------------------------- #

    def set_mode(self, mode: Mode) -> None:
        """Switch to `mode`. "graph only" lets go of every stream at once; "graph + streams"
        has the background task find them again at once."""
        with self._lock:
            self._mode = mode
            if mode == GRAPH_ONLY:
                self._let_go_of(set(self._followers))
            else:
                self._next_discovery = 0.0
        self._wake.set()

    def set_graph_seconds(self, seconds: float) -> None:
        """Read the graph every `seconds` from now on: at once, and then at that interval.

        Raises:
            ValueError: If `seconds` is not from 0.5 s to 10 s. The interval stays as it was.
        """
        if not SHORTEST_GRAPH_SECONDS <= seconds <= LONGEST_GRAPH_SECONDS:
            raise ValueError(
                f"The interval of the knowledge graph read is from {SHORTEST_GRAPH_SECONDS:g} s to "
                f"{LONGEST_GRAPH_SECONDS:g} s, not {seconds:g} s."
            )
        with self._lock:
            self._graph_seconds = seconds
            self._next_read = 0.0
        self._wake.set()

    # -- The background task ----------------------------------------------------------------- #

    def start(self) -> None:
        """Start the background task, on a daemon thread."""
        self._spawn("monitor-memory", self._run)

    def stop(self) -> None:
        """Stop the background task and every follower, and wait for them."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            self._let_go_of(set(self._followers))
        for thread in self._threads:
            thread.join(timeout=15)

    def _run(self) -> None:
        """Read the graph when its read is due, find the streams when a discovery is due in
        "graph + streams", each on its own interval, and wait for the next that is due or for a
        change, until the task stops."""
        while not self._stop.is_set():
            self._wake.clear()
            with self._lock:
                read_due = time.monotonic() >= self._next_read
            if read_due:
                self.read_graph()
                with self._lock:
                    self._next_read = time.monotonic() + self._graph_seconds
            with self._lock:
                discovery_due = (
                    self._mode == GRAPH_AND_STREAMS
                    and time.monotonic() >= self._next_discovery
                )
                # Set before the discovery, so that a switch back during it still counts.
                if discovery_due:
                    self._next_discovery = time.monotonic() + self.discover_seconds
            if discovery_due:
                self.discover()
            with self._lock:
                next_due = self._next_read
                if self._mode == GRAPH_AND_STREAMS:
                    next_due = min(next_due, self._next_discovery)
            self._wake.wait(max(0.0, next_due - time.monotonic()))

    def read_graph(self) -> None:
        """Read the modules, the live participants, the boxes and the WarehouseManagementSystem
        into memory, and keep the handovers since the last read. A read that fails keeps what the
        last one read, and says why."""
        try:
            modules = tuple(self.reads.modules())
            live = frozenset(self.reads.live_resources())
            boxes = tuple(self.reads.boxes())
            wms = self.reads.wms()
        except Exception as error:  # noqa: BLE001 - shown on the page, and tried again
            logger.warning("The monitor could not read the graph: %s", error)
            with self._lock:
                self._snapshot = replace(self._snapshot, error=str(error))
            return
        seen = datetime.now(UTC).isoformat()
        held = {row.box: row.holder for row in boxes}
        with self._lock:
            # An alarm whose box the graph no longer shows on its module is dropped for good.
            self._alarms = [
                alarm
                for alarm in self._alarms
                if held.get(IRI(alarm.box)) == IRI(alarm.resource)
            ]
            if self._handovers is None:
                self._handovers = []
            else:
                self._handovers.extend(
                    handovers_between(self._snapshot.boxes, boxes, seen, wms)
                )
                del self._handovers[:-KEPT_HANDOVERS]
            self._snapshot = Snapshot(modules=modules, live=live, boxes=boxes)

    def discover(self) -> None:
        """Follow the stream of every live participant, and let go of the others. In "graph only"
        it does nothing, and reads nothing."""
        if self.mode == GRAPH_ONLY:
            return
        try:
            located = self.reads.streams()
        except Exception as error:  # noqa: BLE001 - tried again at the next discovery
            logger.warning("The monitor could not find the streams: %s", error)
            return
        with self._lock:
            # A switch to "graph only" while the streams were read wants none of them.
            live = (
                self._snapshot.live if self._mode == GRAPH_AND_STREAMS else frozenset()
            )
            wanted = {p: url for p, url in located.items() if p in live}
            self._let_go_of(
                {
                    participant
                    for participant, follower in self._followers.items()
                    if wanted.get(participant) != follower.url
                }
            )
            started = {
                p: _Follower(url)
                for p, url in wanted.items()
                if p not in self._followers
            }
            self._followers.update(started)
        for participant, follower in started.items():
            self._spawn(
                f"monitor-follow-{participant.fragment or participant}",
                functools.partial(self._follow, participant, follower),
            )

    def _let_go_of(self, participants: set[IRI]) -> None:
        """Stop following `participants`. The caller holds the lock."""
        for participant in participants:
            self._followers.pop(participant).stop.set()

    def take(self, event: HandoverEvent) -> None:
        """Keep `event` if it is a refusal not kept yet, in the order of the events' times, and
        only the newest `KEPT_REFUSALS`; or if it is a deadlock alarm not kept yet, in the same
        order, and only the newest `KEPT_ALARMS`."""
        if isinstance(event, DeadlockAlarm):
            with self._lock:
                if any(other.id == event.id for other in self._alarms):
                    return
                self._alarms.append(event)
                self._alarms.sort(key=lambda other: other.time)
                del self._alarms[:-KEPT_ALARMS]
            return
        if event.kind != "commit_refused":
            return
        report = event.refusal.report if event.refusal else ""
        # Read here, when the refusal arrives, so that a page poll parses no report.
        results = validation_report.readable_results(report)
        kept = KeptRefusal(
            event,
            tuple(validation_report.rule_names(report)),
            None if results is None else tuple(results),
        )
        with self._lock:
            if any(other.event.id == event.id for other in self._refusals):
                return
            self._refusals.append(kept)
            self._refusals.sort(key=lambda other: other.event.time)
            del self._refusals[:-KEPT_REFUSALS]

    def _follow(self, participant: IRI, follower: _Follower) -> None:
        """Read the stream of `participant` until it is let go of, resuming after the last event
        read of it. Whatever a read raises, the follower waits a second and reads again."""
        while not (follower.stop.is_set() or self._stop.is_set()):
            with self._lock:
                last = self._last_read.get(participant)
            try:
                for event_id, data in self.read_stream(follower.url, last):
                    # A follower let go of in the middle of a read keeps nothing it reads.
                    if follower.stop.is_set():
                        return
                    self.take(event_from_json(data))
                    if event_id:
                        with self._lock:
                            self._last_read[participant] = last = event_id
            except Exception as error:  # noqa: BLE001 - logged, and read again
                logger.info("The monitor could not read %s: %s", follower.url, error)
                follower.stop.wait(RETRY_SECONDS)

    def _spawn(self, name: str, target: Callable[[], None]) -> None:
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()
