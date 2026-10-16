"""A box gets a UUID, and the WarehouseManagementSystem stops reading all boxes (jms_kapps 92,
Sören's decision of 2026-10-12 as the product owner of the tests, on jms_kapps 85).

- A spawn reads nothing whose size grows with the boxes in the graph: it names its box by a new
  UUID, so that more than one middleware can create boxes without a shared number.
- When a box comes back, the WarehouseManagementSystem asks the store about that one box, in one
  `ASK`, in place of its read of every box it holds.
- The picking lock covers only the slot bookkeeping: the in-memory choice of origin and
  destination, and the spawn's entry in flight. The spawn's store reads run outside it, and a read
  that another spawn's end may have made stale is read once more before the choice.

The WarehouseManagementSystem runs on the fakes of `_station` (one spawn at a time, with every
read kept) and on `Station` of the load tests (many spawns at once, on threads).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

import pytest
from _station import (
    MiddlewareHandover,
    Moves,
    Network,
    Notifications,
    Reads,
    Records,
    Starts,
    Tasks,
    Timers,
    World,
)
from _wait import eventually
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI
from test_uc2_wms_load_spawns import Asked, Station, in_threads
from test_uc2_wms_load_spawns import wms_on as load_wms_on

from jms_kapps.uc2.discovery import ModulesAndConnections
from jms_kapps.uc2.handshake import ReserveResult
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.vocabulary import (
    FC,
    WMS,
    box_iri,
    local_name,
    module_iri,
    uuid_box_iri,
)
from jms_kapps.uc2.wms import SpawnBoxRequest, WarehouseManagementSystem
from jms_kapps.uc2.wms import logger as wms_logger

M1, M2 = module_iri(1), module_iri(2)


class SizedReads(Reads):
    """The reads of `reader`, each kept with the size of its answer: the number of items of a
    list, and 1 for anything else."""

    def __init__(self, world: World, reader: IRI) -> None:
        super().__init__(world, reader)
        self.answers: list[tuple[str, tuple, int]] = []

    def __getattribute__(self, name: str):
        found = super().__getattribute__(name)
        if name.startswith("_") or not callable(found) or name not in _READS:
            return found

        def kept(*args):
            answer = found(*args)
            size = len(answer) if isinstance(answer, list) else 1
            self.answers.append((name, args, size))
            return answer

        return kept


_READS = {
    "boxes",
    "boxes_held_by",
    "holds",
    "modules_and_connections",
    "capability",
    "workflow_endpoint",
    "connections",
    "transport_of",
}


def station_with_past_boxes(past: int) -> World:
    """Module 1, an entry module, connected to Module 2, an exit module; and `past` boxes back in
    the WarehouseManagementSystem's possession, each with its transport `done`, as a prefill
    leaves them."""
    world = World()
    world.connections = {(M1, M2)}
    world.wms_iri = WMS
    world.classes[FC.EntryModule] = [M1]
    world.classes[FC.ExitModule] = [M2]
    for n in range(1, past + 1):
        world.place(
            box_iri(n), WMS, origin=M1, destination=M2, status=OperationStatus.DONE
        )
    return world


def wms_on(world: World) -> tuple[WarehouseManagementSystem, SizedReads]:
    """The WarehouseManagementSystem on `world`, with Modules 1 and 2, whose reads it keeps."""
    network, records, tasks = Network(), Records(world), Tasks()
    notifications = Notifications(world, tasks)
    for resource in (M1, M2):
        module = FlexConveyorModule(
            resource,
            graph=Reads(world, resource),
            handover=MiddlewareHandover(world, resource),
            call=network.call,
            records=records,
            notify=notifications.notify,
            move=Moves(world).move,
            start=Starts(tasks),
            later=Timers().later,
        )
        network.peers[resource] = module
    reads = SizedReads(world, WMS)
    wms = WarehouseManagementSystem(
        WMS,
        graph=reads,
        records=records,
        call=network.call,
        notify=notifications.notify,
        choose=lambda options: options[0],
    )
    return wms, reads


def spawn(wms: WarehouseManagementSystem) -> None:
    answer = wms.spawn(SpawnBoxRequest())
    assert answer.accepted, answer


# -- The spawn reads nothing that grows with the boxes ---------------------------------------- #


def test_a_spawn_makes_the_same_reads_on_a_large_graph_as_on_a_small_one():
    """10,000 past boxes in the WMS's possession, or one: the spawn's reads, and the size of each
    answer, are the same."""
    small_wms, small = wms_on(station_with_past_boxes(1))
    large_wms, large = wms_on(station_with_past_boxes(10_000))

    spawn(small_wms)
    spawn(large_wms)

    assert small.answers == large.answers
    assert small.answers, "a spawn reads the graph"


# -- A new box's name carries a UUID ---------------------------------------------------------- #


def uuid_of(box: IRI) -> uuid.UUID:
    """The UUID that names `box`, whose local name is `Box-<UUID>`; raises if it is none."""
    name = local_name(box)
    assert name.startswith("Box-"), name
    found = uuid.UUID(name.removeprefix("Box-"))
    assert str(found) == name.removeprefix("Box-"), name
    return found


def test_two_wms_middlewares_spawning_at_the_same_time_never_name_two_boxes_alike():
    """Two WarehouseManagementSystems on one graph, each with eight spawns under way at once: no
    number is shared between them, and every box is named by a UUID of its own."""
    gate = threading.Event()
    station = Station(8, gate=gate)
    first, second = load_wms_on(station), load_wms_on(station)
    spawns = [lambda wms=wms: wms.spawn(SpawnBoxRequest()) for wms in (first, second)]

    threads = in_threads(*(spawns * 8))
    eventually(lambda: len(station.reserved) == 16, timeout=5)
    gate.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(station.created) == 16
    assert len({uuid_of(box) for box in station.created}) == 16


# -- A box's return makes one ASK for that box ------------------------------------------------ #


@pytest.mark.parametrize("held", [True, False], ids=["held", "not held"])
def test_a_done_transport_makes_one_ask_for_its_box_however_many_boxes_the_wms_holds(
    held, caplog
):
    """10,000 past boxes in the WMS's possession: the notification of a done transport costs one
    `ASK` about its one box, whether the WMS holds that box or not; and the WMS takes back a box it
    holds, and warns of one it does not hold, as before."""
    caplog.set_level(logging.INFO, logger=wms_logger.name)
    world = station_with_past_boxes(10_000)
    wms, reads = wms_on(world)
    box = uuid_box_iri(uuid.UUID("5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10"))
    operation = world.place(
        box,
        WMS if held else M2,
        origin=M1,
        destination=M2,
        status=OperationStatus.DONE,
    )

    wms.transport_ended(world.transport(operation))

    assert reads.answers == [("holds", (WMS, box), 1)]
    taken_back = [
        known.transport.box for known in wms.memory.transports_of_held_boxes()
    ]
    assert (box in taken_back) is held
    said = [
        (r.levelno, r.getMessage()) for r in caplog.records if r.name == wms_logger.name
    ]
    name = local_name(box)
    taken = f"WarehouseManagementSystem1: accepted {name} back, and {name}_transport is done"
    warned = (
        f"WarehouseManagementSystem1: notified of {name}_transport, which is done, and it "
        f"does not hold {name}"
    )
    assert said == ([(logging.INFO, taken)] if held else [(logging.WARNING, warned)])


# -- The picking lock covers only the slot bookkeeping ---------------------------------------- #


class SlowReads(Station):
    """`Station`, whose read of the modules on the thread named `slow` waits until the test sets
    `go`: a slow store read of one spawn. `reading` is set once that read has begun."""

    def __init__(self, entries: int, *, gate: threading.Event) -> None:
        super().__init__(entries, gate=gate)
        self.reading = threading.Event()
        self.go = threading.Event()

    def modules_and_connections(self) -> ModulesAndConnections:
        if threading.current_thread().name == "slow":
            self.reading.set()
            assert self.go.wait(timeout=5)
        return super().modules_and_connections()


def started(spawn: Callable[[], object], name: str) -> threading.Thread:
    thread = threading.Thread(target=spawn, name=name)
    thread.start()
    return thread


def test_a_finished_spawn_frees_its_slot_without_waiting_for_another_spawn_s_reads():
    """The first spawn waits for its origin's Reserve; meanwhile the second begins a slow read.
    The first spawn's origin answers: it creates its box and leaves the flight while the second
    spawn still reads."""
    gate = threading.Event()
    station = SlowReads(2, gate=gate)
    asked = Asked(load_wms_on(station))
    first = started(asked, "first")
    eventually(lambda: station.reserved == [M1], timeout=5)
    second = started(asked, "slow")
    assert station.reading.wait(timeout=5)

    gate.set()
    first.join(timeout=5)

    assert not first.is_alive(), "the first spawn waited for the second spawn's read"
    assert [answer.accepted for answer in asked.answers] == [True]
    station.go.set()
    second.join(timeout=5)
    assert [answer.accepted for answer in asked.answers] == [True, True]


def test_two_concurrent_spawns_never_take_the_same_origin_though_one_read_before_the_other_chose():
    """The slow spawn begins its read before the other spawn chooses Module 1, and finds Module 1
    free in it: under the lock it finds Module 1 in flight, and takes Module 2. The other spawn
    chose without waiting for the slow read."""
    gate = threading.Event()
    station = SlowReads(2, gate=gate)
    asked = Asked(load_wms_on(station))
    slow = started(asked, "slow")
    assert station.reading.wait(timeout=5)
    first = started(asked, "first")
    eventually(lambda: station.reserved == [M1], timeout=5)

    station.go.set()
    eventually(lambda: len(station.reserved) == 2, timeout=5)
    gate.set()
    for thread in (slow, first):
        thread.join(timeout=5)

    assert station.reserved == [M1, M2]
    assert not station.one_origin_twice
    assert all(answer.accepted for answer in asked.answers), asked.answers


# -- A stale read is caught before the Reserve ------------------------------------------------ #


class Occupying(Station):
    """`Station` with two entry modules, where a box stays on its origin once created there, until
    a test moves it on; an origin that holds a box refuses its Reserve, "occupied by <box>", as a
    module does. Each read by the thread named `slow` of what Module 2 holds, the last origin it
    reads, waits until the test lets it go on (`go_on`): Module 1 is read by then. `slow_reads`
    counts them."""

    def __init__(self) -> None:
        super().__init__(2)
        self.on: dict[IRI, IRI] = {}
        self.slow_reads = 0
        self._reading = threading.Semaphore(0)
        self._go = threading.Semaphore(0)

    def boxes_held_by(self, resource: IRI) -> list[IRI]:
        if threading.current_thread().name == "slow" and resource == M2:
            self.slow_reads += 1
            self._reading.release()
            assert self._go.acquire(timeout=5)
        box = self.on.get(resource)
        return [] if box is None else [box]

    def when_slow_reads(self) -> None:
        """Wait until the slow spawn reads what Module 2 holds."""
        assert self._reading.acquire(timeout=5)

    def go_on(self) -> None:
        self._go.release()

    def call(self, endpoint: str, payload: object) -> dict:
        box = self.on.get(IRI(endpoint))
        if box is not None:
            with self._lock:
                self.reserved.append(IRI(endpoint))
            return ReserveResult(
                accepted=False, reason=f"occupied by {local_name(box)}"
            ).model_dump()
        return super().call(endpoint, payload)

    def open(self, box: IRI, *, origin: IRI, **rest: IRI) -> IRI:
        operation = super().open(box, origin=origin, **rest)
        self.on[origin] = box
        return operation


def test_a_spawn_reads_again_when_another_spawn_left_the_flight_after_its_reads_began():
    """The slow spawn reads Module 1 free; before it chooses, another spawn creates its box on
    Module 1 and leaves the flight. The slow spawn reads again, finds Module 1 holding that box,
    and takes Module 2."""
    station = Occupying()
    wms = load_wms_on(station)
    asked = Asked(wms)
    slow = started(asked, "slow")
    station.when_slow_reads()

    other = wms.spawn(SpawnBoxRequest())
    station.go_on()
    station.when_slow_reads()
    station.go_on()
    slow.join(timeout=5)

    assert other.accepted and other.origin == str(M1), other
    [answer] = asked.answers
    assert answer.accepted and answer.origin == str(M2), answer
    assert station.slow_reads == 2
    assert station.reserved == [M1, M2]


def test_a_spawn_reads_again_only_once_and_the_origin_s_reserve_refuses_a_pick_still_stale():
    """As above, but Module 1's box moves on before the second read, and a third spawn fills
    Module 1 during it. The slow spawn reads no third time: it takes Module 1, whose Reserve
    refuses it with the origin's reason, and nothing of it is written."""
    station = Occupying()
    wms = load_wms_on(station)
    asked = Asked(wms)
    slow = started(asked, "slow")
    station.when_slow_reads()
    assert wms.spawn(SpawnBoxRequest()).accepted
    del station.on[M1]  # the box moved on
    station.go_on()
    station.when_slow_reads()

    third = wms.spawn(SpawnBoxRequest())
    station.go_on()
    slow.join(timeout=5)

    assert third.accepted and third.box is not None, third
    [answer] = asked.answers
    assert (answer.accepted, answer.reason) == (
        False,
        f"Module1 refused to reserve: occupied by {local_name(third.box)}",
    )
    assert (answer.box, answer.operation) == (None, None)
    assert station.slow_reads == 2
    assert len(station.created) == 2
