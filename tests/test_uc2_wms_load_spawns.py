"""The WarehouseManagementSystem's spawns for the load tests (jms_kapps 51, decided by Etienne on
2026-10-07): the spawn timer starts each spawn when it is due, on a thread of its own, whether the
spawn before it has ended or not; at most as many spawns are under way as the topology has
modules that may be an origin; and concurrent spawns pick apart.

The WMS runs on `Station`, a fake of the graph, the origins' Reserve and the store that is safe
to use from many threads at once: every box leaves its origin at once, so an origin is free
again as soon as its spawn has ended. Time is real, as in the spawn timer's other tests: one
interval is a tenth of a second.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections import Counter
from collections.abc import Callable

import pytest
from _station import store_refusal
from _wait import eventually
from kapps_semantic_middleware.errors import NotificationUndelivered
from kapps_triplestore_interface import IRI, ShaclValidationError
from rdflib.namespace import RDF, SH

from jms_kapps.uc2.discovery import ModulesAndConnections
from jms_kapps.uc2.handshake import ReserveResult
from jms_kapps.uc2.spawn_rate import SpawnTimer
from jms_kapps.uc2.vocabulary import WMS, box_iri, module_iri
from jms_kapps.uc2.wms import (
    SpawnBoxRequest,
    SpawnBoxResult,
    WarehouseManagementSystem,
)
from jms_kapps.uc2.wms import logger as wms_logger

# 600 boxes a minute, the rate of the live run: one interval is 0.1 s.
RATE = 600
INTERVAL = 60 / RATE
EVERY_ORIGIN_BUSY = "every origin has a spawn under way"
REPORT = f"_:report <{RDF.type}> <{SH.ValidationReport}> .\n"


class Station:
    """`entries` entry modules, each connected to the one exit module after them, unless a test
    sets `connections`. An origin's Reserve accepts once `gate` lets it, `answer_after` seconds
    after it was asked or at once.

    It keeps the origin of each Reserve under way, how many were under way at most, and whether
    two were ever under way on one origin at once. A test sets what stands in a spawn's way: the
    modules that hold a box (`held`), the modules with no live Reserve (`unreachable`) or with no
    BoxTransportCapability (`no_capability`), an origin that refuses to reserve
    (`refuses_reserve`), the store's refusal of the create (`refuses_create`), a notification that
    does not arrive (`undelivered`), an error of the store's read of the modules (`fails_with`;
    of its read of the boxes until 2026-10-12, jms_kapps 92, when the WMS stopped reading them),
    and the stop (`stopping`).
    """

    def __init__(
        self,
        entries: int,
        *,
        answer_after: float = 0.0,
        gate: threading.Event | None = None,
    ) -> None:
        self.entries = [module_iri(n) for n in range(1, entries + 1)]
        self.exit = module_iri(entries + 1)
        self.connections = [(entry, self.exit) for entry in self.entries]
        self.answer_after = answer_after
        self.gate = gate
        self.held: set[IRI] = set()
        self.unreachable: set[IRI] = set()
        self.no_capability: set[IRI] = set()
        self.refuses_reserve: str | None = None
        self.refuses_create: ShaclValidationError | None = None
        self.undelivered: str | None = None
        self.fails_with: Exception | None = None
        self.stopping = threading.Event()
        self._lock = threading.Lock()
        self.reserving: Counter[IRI] = Counter()
        self.most_at_once = 0
        self.one_origin_twice = False
        self.reserved: list[IRI] = []
        self.created: list[IRI] = []
        self.failed: list[IRI] = []
        self.reads = 0

    # What the WMS reads.
    def modules_and_connections(self) -> ModulesAndConnections:
        if self.fails_with is not None:
            raise self.fails_with
        with self._lock:
            self.reads += 1
        types = {entry: ["EntryModule"] for entry in self.entries}
        types[self.exit] = ["ExitModule"]
        return ModulesAndConnections(types, list(self.connections))

    def boxes_held_by(self, resource: IRI) -> list[IRI]:
        return [box_iri(7)] if resource in self.held else []

    def capability(self, resource: IRI, capability_class: IRI) -> IRI | None:
        if resource in self.no_capability:
            return None
        return IRI(f"{resource}_capability")

    def workflow_endpoint(self, resource: IRI, workflow_class: IRI) -> str | None:
        if resource in self.unreachable:
            return None
        return str(resource)

    # The origin's Reserve.
    def call(self, endpoint: str, payload: object) -> dict:
        origin = IRI(endpoint)
        with self._lock:
            self.reserved.append(origin)
            self.reserving[origin] += 1
            self.one_origin_twice |= self.reserving[origin] > 1
            self.most_at_once = max(self.most_at_once, self.reserving.total())
        try:
            if self.gate is not None:
                assert self.gate.wait(timeout=5)
            time.sleep(self.answer_after)
        finally:
            with self._lock:
                self.reserving[origin] -= 1
        if self.refuses_reserve is not None:
            return ReserveResult(
                accepted=False, reason=self.refuses_reserve
            ).model_dump()
        return ReserveResult(accepted=True).model_dump()

    # What the WMS writes, and its notification.
    def open(
        self,
        box: IRI,
        *,
        origin: IRI,
        destination: IRI,
        capability: IRI,
        possessor: IRI,
    ) -> IRI:
        if self.refuses_create is not None:
            raise self.refuses_create
        with self._lock:
            self.created.append(box)
        return IRI(f"{box}_transport")

    def fail(self, operation: IRI, reason: str, report: str | None = None) -> None:
        with self._lock:
            self.failed.append(operation)

    def notify(self, operation: IRI, resource: IRI) -> None:
        if self.undelivered is not None:
            raise NotificationUndelivered(self.undelivered)


def wms_on(
    station: Station, *, choose: Callable | None = None
) -> WarehouseManagementSystem:
    return WarehouseManagementSystem(
        WMS,
        graph=station,
        records=station,
        call=station.call,
        notify=station.notify,
        # The first of the options, unless a test says otherwise: so that two spawns would both
        # pick the same origin, if nothing kept them apart.
        choose=choose or (lambda options: options[0]),
        stopping=station.stopping.is_set,
    )


class Asked:
    """The spawns asked of a WMS, each with when it started and its answer."""

    def __init__(self, wms: WarehouseManagementSystem) -> None:
        self.wms = wms
        self._lock = threading.Lock()
        self.started: list[float] = []
        self.answers: list[SpawnBoxResult] = []

    def __call__(self, request: SpawnBoxRequest | None = None) -> SpawnBoxResult:
        with self._lock:
            self.started.append(time.monotonic())
        answer = self.wms.spawn(request or SpawnBoxRequest())
        with self._lock:
            self.answers.append(answer)
        return answer


def in_threads(*spawns: Callable[[], object]) -> list[threading.Thread]:
    threads = [threading.Thread(target=spawn) for spawn in spawns]
    for thread in threads:
        thread.start()
    return threads


def test_at_a_rate_each_spawn_starts_when_it_is_due_whether_the_one_before_has_ended_or_not():
    """A spawn takes three intervals here: its origin answers its Reserve that late. Over ten
    intervals ten spawns start, overlapping; at most two are under way, as the station has two
    entry modules, and each due spawn beyond that is refused."""
    station = Station(2, answer_after=3 * INTERVAL)
    asked = Asked(wms_on(station))
    timer = SpawnTimer(asked)

    timer.set(RATE)
    time.sleep(10.5 * INTERVAL)
    timer.stop(timeout=5)

    first = asked.started[0]
    in_ten_intervals = [t for t in asked.started if t - first < 10 * INTERVAL]
    assert 9 <= len(in_ten_intervals) <= 11, asked.started
    for k, started in enumerate(in_ten_intervals):
        assert started - first < (k + 1) * INTERVAL, (k, started - first)
    assert station.most_at_once == 2
    refused = [answer for answer in asked.answers if not answer.accepted]
    assert refused, asked.answers
    assert {answer.reason for answer in refused} == {EVERY_ORIGIN_BUSY}
    assert len(asked.answers) == len(asked.started)
    assert len([a for a in asked.answers if a.accepted]) == len(station.created)


def test_two_concurrent_spawns_never_pick_the_same_free_origin():
    """Both would pick Module 1, the first free entry module: the second finds it under way."""
    gate = threading.Event()
    station = Station(2, gate=gate)
    asked = Asked(wms_on(station))

    threads = in_threads(asked, asked)
    eventually(lambda: len(station.reserved) == 2, timeout=5)
    gate.set()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(station.reserved, key=str) == station.entries
    assert all(answer.accepted for answer in asked.answers), asked.answers
    assert len(set(station.created)) == 2


@pytest.mark.parametrize("picks", ["the first", "at random"])
def test_many_concurrent_spawns_never_reserve_one_origin_twice_at_once(picks):
    station = Station(4, answer_after=0.02)
    wms = wms_on(station, choose=None if picks == "the first" else random.choice)
    asked = Asked(wms)

    for thread in in_threads(*[asked] * 40):
        thread.join(timeout=10)

    assert not station.one_origin_twice
    assert station.most_at_once <= 4
    assert len(set(station.created)) == len(station.created)
    assert {a.reason for a in asked.answers if not a.accepted} <= {EVERY_ORIGIN_BUSY}


@pytest.mark.parametrize(
    "second",
    [SpawnBoxRequest(), SpawnBoxRequest(origin=str(module_iri(1)))],
    ids=["picked, as the timer asks", "named, as the workflow may ask"],
)
def test_the_cap_counts_every_spawn_under_way_of_the_timer_and_the_workflow_alike(
    second,
):
    """One entry module, and a spawn under way on it: the next spawn does not start."""
    gate = threading.Event()
    station = Station(1, gate=gate)
    asked = Asked(wms_on(station))
    [first] = in_threads(lambda: asked(SpawnBoxRequest(origin=str(module_iri(1)))))
    eventually(lambda: bool(station.reserved), timeout=5)

    refused = asked(second)
    gate.set()
    first.join(timeout=5)

    assert (refused.accepted, refused.reason) == (False, EVERY_ORIGIN_BUSY)
    assert (refused.origin, refused.destination) == (second.origin, second.destination)
    assert station.reserved == [module_iri(1)]
    assert refused in [answer.result for answer in asked.wms.memory.answers()]


def test_before_the_first_read_of_the_modules_there_is_no_cap():
    """The cap is the number of origins the latest read of a spawn found: the WMS makes no read
    of its own for it. A spawn that comes before any read is not refused at a cap."""

    class Slow(Station):
        def __init__(self) -> None:
            super().__init__(1)
            self.reading = threading.Event()
            self.may_read = threading.Event()

        def modules_and_connections(self) -> ModulesAndConnections:
            self.reading.set()
            assert self.may_read.wait(timeout=5)
            return super().modules_and_connections()

    station = Slow()
    asked = Asked(wms_on(station))
    [first] = in_threads(asked)
    assert station.reading.wait(timeout=5)
    [second] = in_threads(asked)
    time.sleep(0.1)
    station.may_read.set()
    for thread in (first, second):
        thread.join(timeout=5)

    reasons = sorted(str(answer.reason) for answer in asked.answers)
    assert EVERY_ORIGIN_BUSY not in reasons
    assert station.reads == 2


def test_once_the_stop_has_begun_a_spawn_is_refused_as_stopping_even_at_the_cap():
    gate = threading.Event()
    station = Station(1, gate=gate)
    wms = wms_on(station)
    [first] = in_threads(lambda: wms.spawn(SpawnBoxRequest()))
    eventually(lambda: bool(station.reserved), timeout=5)
    station.stopping.set()

    refused = wms.spawn(SpawnBoxRequest())
    gate.set()
    first.join(timeout=5)

    assert (refused.accepted, refused.reason) == (False, "the system is stopping")


def test_the_timer_s_stop_waits_for_every_spawn_under_way():
    """No spawn of the timer is cut short by the end of the process: its stop waits for each."""
    station = Station(3, answer_after=3 * INTERVAL)
    asked = Asked(wms_on(station))
    timer = SpawnTimer(asked)
    timer.set(RATE)
    eventually(lambda: station.most_at_once == 3, timeout=5)

    timer.stop(timeout=5)

    assert station.reserving.total() == 0
    assert len(asked.answers) == len(asked.started)
    time.sleep(2 * INTERVAL)
    assert len(asked.started) == len(asked.answers)


def spawn_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The WMS's records of its spawns: each a box spawned, or a spawn refused with why."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == wms_logger.name
        and record.levelno == logging.INFO
        and (
            ": spawned " in record.getMessage()
            or ": spawn refused: " in record.getMessage()
        )
    ]


def test_every_spawn_asked_for_ends_in_exactly_one_record_spawned_or_refused_with_why(
    caplog,
):
    """Spawns of the workflow and of the timer, mixed: each one asked for leaves one record on the
    WMS's log, whatever stood in its way, a spawn that raised included."""
    caplog.set_level(logging.INFO, logger="jms_kapps")
    gate = threading.Event()
    gate.set()
    station = Station(2, gate=gate)
    m1, m2, m3 = station.entries + [station.exit]
    asked = Asked(wms_on(station))
    timer = SpawnTimer(asked)

    def workflow(origin=None, destination=None) -> SpawnBoxResult:
        return asked(
            SpawnBoxRequest(
                origin=None if origin is None else str(origin),
                destination=None if destination is None else str(destination),
            )
        )

    # First, while no capability is known yet: the WMS keeps one it has read.
    station.no_capability = {m2}
    workflow(m2)
    station.no_capability = set()
    workflow()
    workflow(m1, m1)
    workflow(m3, m3)
    station.connections = [(m2, m3)]
    workflow(m1, m3)
    workflow(m1)
    station.held = {m2}
    workflow()
    workflow(destination=m3)
    station.held = {m1, m2}
    workflow()
    station.held, station.connections = set(), [(m1, m3), (m2, m3)]
    station.refuses_reserve = "occupied by Box7"
    workflow(m1)
    station.refuses_reserve = None
    station.unreachable = {m1}
    workflow(m1)
    station.unreachable = set()
    station.refuses_create = store_refusal(REPORT)
    workflow(m1)
    station.refuses_create = None
    station.undelivered = "no live service"
    workflow(m1)
    station.undelivered = None
    station.fails_with = RuntimeError("the store went away")
    with pytest.raises(RuntimeError):
        workflow(m1)
    station.fails_with = None
    # Both origins with a spawn under way: the next spawns, of the workflow and of the timer, do
    # not start.
    gate.clear()
    under_way = in_threads(workflow, workflow)
    eventually(lambda: station.reserving.total() == 2, timeout=5)
    workflow()
    timer.set(RATE)
    time.sleep(3 * INTERVAL)
    timer.stop(timeout=5)
    gate.set()
    for thread in under_way:
        thread.join(timeout=5)
    station.stopping.set()
    workflow()
    timer.set(RATE)
    time.sleep(2 * INTERVAL)
    timer.stop(timeout=5)

    records = spawn_records(caplog)
    assert len(records) == len(asked.started), records
    reasons = {
        record.split(": spawn refused: ", 1)[1].split(" (")[0]
        for record in records
        if ": spawn refused: " in record
    }
    assert reasons >= {
        "Module1 is not an exit module",
        "Module3 is not an entry module",
        "Module3 cannot be reached from Module1",
        "no exit module can be reached from Module1",
        "no free entry module can reach an exit module",
        "no free entry module can reach Module3",
        "no entry module is free",
        "Module1 refused to reserve: occupied by Box7",
        (
            "Module2 offers no BoxTransportCapability: it has never registered its "
            "BoxTransportWorkflow"
        ),
        "Module1 is not reachable: the graph names no live ReserveWorkflow of it",
        "the spawn failed: the store went away",
        EVERY_ORIGIN_BUSY,
        "the system is stopping",
    }, reasons
    # The store's refusal of a create names the box, by its UUID (jms_kapps 92).
    assert [
        reason
        for reason in reasons
        if re.fullmatch(
            r"the triple store refused creating Box-[0-9a-f-]{36} on Module1", reason
        )
    ], reasons
    # Each box whose create the store admitted, the one whose origin was not notified included:
    # a spawn counts at its answer, created once the store admitted its create.
    spawned = [record for record in records if ": spawned " in record]
    assert len(station.failed) == 1
    assert len(spawned) == len(station.created) == 4
