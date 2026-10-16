"""What a module keeps in memory of its own work, and the boxes of its dashboard that render it:
its decisions, with their ties and the Reserve refusals that followed, the connections it read, the
box it holds, and its faults armed.

The module runs on the FlexConveyor station of `_station`, without a network or a triple store, so
every read it makes is counted: keeping its memory and rendering its boxes cost no read of their
own. Its middleware is built here and never started, so it registers nothing and reads nothing from
a store: its dashboard is called through a test client.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import pytest
from _html import absolute_paths, attribute_values, rows
from _module_boxes import drawn, matrix_of
from _station import (
    MiddlewareHandover,
    Moves,
    Network,
    Notifications,
    Reads,
    Records,
    Tasks,
    Timers,
    Waits,
    World,
    store_refusal,
)
from fastapi.testclient import TestClient
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.discovery import DirectedConnection, StartRead
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.handshake import ReceiveRequest, ReserveRequest
from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.module_dashboard import register_boxes
from jms_kapps.uc2.vocabulary import (
    FC,
    WMS,
    box_iri,
    module_iri,
    transport_iri,
    uuid_box_iri,
)

M1, M2, M3, M4 = (module_iri(n) for n in range(1, 5))
B1, B2, B3 = box_iri(1), box_iri(2), box_iri(3)
T1, T2 = transport_iri(B1), transport_iri(B2)


@pytest.fixture
def world():
    world = World()
    world.wms_iri = WMS
    return world


@pytest.fixture
def tasks():
    return Tasks()


@pytest.fixture
def waits():
    return Waits()


@pytest.fixture
def moves(world):
    return Moves(world)


@pytest.fixture
def station(world, tasks, waits, moves):
    """Builds the modules it is given, on one network."""
    network, records = Network(), Records(world)
    notifications = Notifications(world, tasks)
    # The bound of a reservation does not pass in these tests.
    timers = Timers()

    def build(*resources: IRI) -> list[FlexConveyorModule]:
        built = []
        for resource in resources:
            module = FlexConveyorModule(
                resource,
                graph=Reads(world, resource),
                handover=MiddlewareHandover(world, resource),
                call=network.call,
                records=records,
                notify=notifications.notify,
                move=moves.move,
                start=tasks.start,
                retry_seconds=0.1,
                wait=waits.wait,
                later=timers.later,
                events=HandoverEvents(resource, token=resource.fragment),
            )
            network.peers[resource] = module
            notifications.receivers[resource] = module.box_transport
            built.append(module)
        return built

    return build


# -- Decisions --------------------------------------------------------------------------------- #


def test_a_decision_is_recorded_with_the_next_module_and_the_modules_it_ties_with(
    world, station
):
    """Module 2 and Module 3 are both one hop from Module 4: the tie goes to the lowest IRI."""
    world.connections = {(M1, M3), (M1, M2), (M2, M4), (M3, M4)}
    m1, *_ = station(M1, M2, M3, M4)
    world.place(B1, M1, destination=M4)

    m1.box_transport(world.transport(T1))

    [decision] = m1.memory.decisions()
    assert (
        decision.box,
        decision.transport,
        decision.destination,
        decision.next_module,
        decision.ties,
        decision.refusals,
    ) == (B1, T1, M4, M2, (M3,), ())


def test_the_reserve_refusals_that_follow_a_decision_are_recorded_on_it_in_order(
    world, tasks, waits, station
):
    """Module 2 holds Box 2, then has reserved itself for Box 3 of Module 3, which asked before
    Box 1, then is free: two refusals, each answered by asking again, and the third Reserve is
    accepted."""
    world.connections = {(M1, M2)}
    m1, m2 = station(M1, M2)
    world.place(B2, M2)
    world.place(B1, M1, destination=M2)
    assert m2.reserve(ReserveRequest(box=B3, source_module=M3)).refusal == "occupied"

    def reserved_for_box_3() -> None:
        world.held[M2].remove(B2)
        assert m2.reserve(ReserveRequest(box=B3, source_module=M3)).accepted

    waits.meanwhile = [
        reserved_for_box_3,
        lambda: m2.receive(ReceiveRequest(box=B3, source_module=M3)),
    ]

    m1.box_transport(world.transport(T1))

    [decision] = m1.memory.decisions()
    assert (decision.next_module, decision.refusals) == (M2, ("occupied", "reserved"))


# -- The connections it read ------------------------------------------------------------------- #


def test_the_connections_read_at_a_decision_are_kept_with_their_directions(
    world, station
):
    world.connections = {(M1, M2), (M2, M1), (M2, M3)}
    world.directions = {(M1, M2): "South", (M2, M1): "North", (M2, M3): "East"}
    m1, _ = station(M1, M2)
    world.place(B1, M1, destination=M2)

    m1.box_transport(world.transport(T1))

    read = m1.memory.connections()
    assert read is not None
    assert read.box == B1
    assert sorted(read.connections) == [
        (M1, M2, "South"),
        (M2, M1, "North"),
        (M2, M3, "East"),
    ]
    assert read.at == m1.memory.decisions()[0].at


def test_keeping_a_decision_costs_no_read_beyond_the_one_of_the_connections(
    world, tasks, station
):
    world.connections = {(M1, M2)}
    m1, _ = station(M1, M2)
    world.place(B1, M1, destination=M2)

    m1.box_transport(world.transport(T1))
    tasks.run()

    assert [
        read
        for read in world.reads
        if read[0] == M1 and read[1] in ("connections", "boxes_held_by")
    ] == [(M1, "connections", ())]
    assert [d.next_module for d in m1.memory.decisions()] == [M2]


# -- The box it holds -------------------------------------------------------------------------- #


def test_a_module_holds_a_box_from_its_arrival_until_it_has_moved_off(
    world, tasks, moves, station
):
    """Module 1, the origin, holds Box 1 once the store admits its transport `running` there,
    and during its convey; Module 2 holds it once its Receive confirms the arrival."""
    world.connections = {(M1, M2)}
    m1, m2 = station(M1, M2)
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)
    during_the_move: list[tuple] = []
    moves.meanwhile = [
        lambda: during_the_move.append((m1.memory.held(), m2.memory.held()))
    ]

    m1.box_transport(world.transport(T1))

    assert during_the_move == [(B1, None)]
    assert (m1.memory.held(), m2.memory.held()) == (None, B1)
    tasks.run()  # Module 2 gives it back to the WMS
    assert (m1.memory.held(), m2.memory.held()) == (None, None)


def test_a_module_that_starts_while_the_graph_says_it_possesses_a_box_shows_that_box(
    world, tasks, station
):
    """A restarted module: its start read finds Box 1 in its possession. It holds Box 1 until
    the box has moved off it."""
    world.connections = {(M1, M2)}
    m1, _ = station(M1, M2)

    m1.memory.read_at_start(
        StartRead(connections=[DirectedConnection(M1, M2, "East")], held=[B1])
    )

    assert m1.memory.held() == B1
    assert drawn(box(dashboard_of(m1), "module")).boxes == ["Box1"]
    assert "Module1 holds Box1." in box(dashboard_of(m1), "module")


def test_a_running_transport_assigned_to_a_module_whose_box_is_elsewhere_shows_no_box_held(
    world, tasks, station
):
    """Module 1 is notified of Box 1's running transport, assigned to it, while Box 1 is on
    Module 2. A transport assigned to a module is no possession: Module 1 holds no box, and
    the handover it starts fails."""
    world.connections = {(M1, M3)}
    m1, _ = station(M1, M3)
    world.place(B1, M2, destination=M3)
    world.jobs[T1].assigned = M1

    # Module 1's handover finds it does not possess Box 1.
    m1.box_transport(world.transport(T1))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert m1.memory.held() is None
    assert drawn(box(dashboard_of(m1), "module")).boxes == []
    assert "Module1 holds no box." in box(dashboard_of(m1), "module")


def test_a_module_that_cannot_go_on_still_holds_the_box(world, station):
    """No path leads to Module 2: the transport fails, and Box 1 stays on Module 1."""
    m1, _ = station(M1, M2)
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)

    m1.box_transport(world.transport(T1))

    assert world.jobs[T1].status == OperationStatus.FAILED
    assert m1.memory.held() == B1
    assert drawn(box(dashboard_of(m1), "module")).boxes == ["Box1"]


def test_the_drawing_shows_a_box_named_by_a_uuid_by_the_first_four_characters_of_its_uuid(
    world, station
):
    """A box the WMS spawns is named `Box-<UUID>` (jms_kapps 92): the drawing's box, a square of
    its own, shows `Box-` and the first four characters of the UUID, as the monitor's disc shows
    those four; the box's whole name is its title, and the sentence below the drawing names it
    whole. A box named by a number shows its whole name, as before."""
    m1, _ = station(M1, M2)
    box_id = uuid_box_iri(UUID("5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10"))
    operation = world.place(box_id, M1, destination=M2, status=OperationStatus.QUEUED)

    m1.box_transport(world.transport(operation))

    fragment = box(dashboard_of(m1), "module")
    assert m1.memory.held() == box_id
    assert drawn(fragment).boxes == ["Box-5b0e"]
    assert f"<title>{box_id}</title>" in fragment
    assert "Module1 holds Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10." in fragment


def test_a_module_whose_handover_without_asking_is_refused_holds_the_box_until_it_moves_off(
    world, tasks, waits, station
):
    """With `skip-reserve` armed, the store refuses the handover while Module 2 holds Box 2, so
    nothing moves. Module 1 asks Reserve, is refused as occupied, waits its turn, and holds
    Box 1 until it has moved onto Module 2."""
    world.connections = {(M1, M2)}
    m1, m2 = station(M1, M2)
    world.place(B2, M2)
    world.place(B1, M1, destination=M2, status=OperationStatus.QUEUED)
    m1.faults["skip-reserve"] = True
    m1.handover.fails_with = [store_refusal("Module 2 holds Box 2.")]
    held_while_waiting = []

    def box_2_leaves() -> None:
        held_while_waiting.append(m1.memory.held())
        world.held[M2].remove(B2)

    waits.meanwhile = [box_2_leaves]

    m1.box_transport(world.transport(T1))

    assert held_while_waiting == [B1]
    [decision] = m1.memory.decisions()
    assert (decision.next_module, decision.refusals) == (M2, ("occupied",))
    # Module 2 reserved itself, and Module 1 conveyed the box onto it.
    assert (m1.memory.held(), m2.memory.held()) == (None, B1)


# -- Its dashboard ------------------------------------------------------------------------------ #

BOXES = ["module", "adjacency-matrix", "routing-decisions"]


class _NoStore:
    """An object-graph mapper with no store behind it. The middleware is never started, so it
    registers nothing, and nothing a test calls here reaches a graph."""

    db = None


def dashboard_of(module: FlexConveyorModule) -> TestClient:
    """`module`'s dashboard, as its process serves it, on a middleware never started."""
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(module.resource),
        service_class=str(FC.FlexConveyorModuleService),
        ogm=_NoStore(),
        host="127.0.0.1",
        port=8000,
        activity_feed=True,
    )
    register_boxes(enable_dashboard(middleware), module, 0.0)
    return TestClient(middleware.app)


def box(dashboard: TestClient, box_id: str) -> str:
    """The fragment the dashboard serves for the box `box_id`."""
    response = dashboard.get(f"/dashboard/box/{box_id}")
    assert response.status_code == 200, response.text
    assert "failed to render" not in response.text, response.text
    return response.text


def test_the_dashboard_has_a_tab_named_after_the_module_with_its_three_boxes(
    world, station
):
    m3, *_ = station(module_iri(3))

    page = dashboard_of(m3).get("/dashboard").text

    assert ">Module3</label>" in page
    slots = re.findall(r'id="box-([\w-]+)"', page)
    assert slots[-3:] == BOXES


def refused_twice_then_accepted(world, tasks, waits, m2) -> None:
    """Module 2 holds Box 2, then has reserved itself for Box 3 of Module 3, which asked first,
    then is free."""
    world.place(B2, M2)
    assert m2.reserve(ReserveRequest(box=B3, source_module=M3)).refusal == "occupied"

    def reserved_for_box_3() -> None:
        world.held[M2].remove(B2)
        assert m2.reserve(ReserveRequest(box=B3, source_module=M3)).accepted

    waits.meanwhile = [
        reserved_for_box_3,
        lambda: m2.receive(ReceiveRequest(box=B3, source_module=M3)),
    ]


def test_routing_decisions_shows_each_decision_with_its_refusals_and_retries(
    world, tasks, waits, station
):
    world.connections = {(M1, M2)}
    m1, m2 = station(M1, M2)
    refused_twice_then_accepted(world, tasks, waits, m2)
    world.place(B1, M1, destination=M2)

    m1.box_transport(world.transport(T1))
    tasks.run()  # Module 2, the destination, gives Box 1 back

    [(time_of_day, *decision)] = rows(box(dashboard_of(m1), "routing-decisions"))
    assert len(time_of_day) == len("12:34:56")
    assert decision == [
        "Box1",
        "Module2",
        "Module2",
        "—",
        "occupied, reserved",
        "—",
        "2",
    ]
    [(_, *given_back)] = rows(box(dashboard_of(m2), "routing-decisions"))
    assert given_back == ["Box1", "Module2", "back to the WMS", "—", "—", "—", "0"]


def test_routing_decisions_shows_the_modules_a_decision_tied_with(world, station):
    world.connections = {(M1, M3), (M1, M2), (M2, M4), (M3, M4)}
    m1, *_ = station(M1, M2, M3, M4)
    world.place(B1, M1, destination=M4)

    m1.box_transport(world.transport(T1))

    [(_, *decision)] = rows(box(dashboard_of(m1), "routing-decisions"))
    assert decision == [
        "Box1",
        "Module4",
        "Module2",
        "Module3",
        "—",
        "Module3: not asked",
        "0",
    ]


def test_routing_decisions_shows_a_detour_that_a_declined_reserve_led_to(
    world, tasks, station
):
    """Module 2, the next module, holds a box: Module 1 asks Module 3, the module it ties with,
    which accepts, and the box takes the detour (Etienne, 2026-10-06, jms_kapps 43)."""
    world.connections = {(M1, M3), (M1, M2), (M2, M4), (M3, M4)}
    m1, *_ = station(M1, M2, M3, M4)
    world.place(B2, M2)
    world.place(B1, M1, destination=M4)

    m1.box_transport(world.transport(T1))
    tasks.run()

    [(_, *decision)] = rows(box(dashboard_of(m1), "routing-decisions"))
    assert decision == [
        "Box1",
        "Module4",
        "Module2",
        "Module3",
        "occupied",
        "Module3: taken",
        "0",
    ]


def test_routing_decisions_shows_a_detour_that_refused_too(
    world, tasks, waits, station
):
    world.connections = {(M1, M3), (M1, M2), (M2, M4), (M3, M4)}
    m1, *_ = station(M1, M2, M3, M4)
    world.place(B2, M2)
    world.place(B3, M3)
    waits.meanwhile = [lambda: world.held[M2].remove(B2)]
    world.place(B1, M1, destination=M4)

    m1.box_transport(world.transport(T1))
    tasks.run()

    [(_, *decision)] = rows(box(dashboard_of(m1), "routing-decisions"))
    assert decision[4:] == ["occupied", "Module3: refused as occupied", "1"]


def test_routing_decisions_shows_a_decision_that_found_no_path(world, station):
    m1, _ = station(M1, M2)
    world.place(B1, M1, destination=M2)

    m1.box_transport(world.transport(T1))

    [(_, *decision)] = rows(box(dashboard_of(m1), "routing-decisions"))
    assert decision == [
        "Box1",
        "Module2",
        "none: no path leads there",
        "—",
        "—",
        "—",
        "0",
    ]


def test_routing_decisions_shows_the_20_newest_newest_first_and_says_so(
    world, tasks, station
):
    world.connections = {(M1, M2)}
    m1, _ = station(M1, M2)
    for n in range(1, 22):
        box_n = box_iri(n)
        world.place(box_n, M1, destination=M2)
        m1.box_transport(world.transport(transport_iri(box_n)))
        tasks.run()

    fragment = box(dashboard_of(m1), "routing-decisions")

    shown = rows(fragment)
    assert len(shown) == 20
    assert [row[1] for row in shown[:2]] == ["Box21", "Box20"]
    assert "The newest 20 of 21 decisions, newest first." in fragment


def test_routing_decisions_says_when_the_module_has_decided_nothing_yet(station):
    m1, *_ = station(M1)

    fragment = box(dashboard_of(m1), "routing-decisions")

    assert rows(fragment) == []
    assert "Module1 has decided nothing yet." in fragment


M5 = module_iri(5)

# The cross, as the shipped topology seeds it: Module 3 connected both ways, by direction, to each
# module that has the entry role (Modules 1 and 2) and each that has the exit role (4 and 5).
CROSS = {
    (M1, M3): "South",
    (M2, M3): "East",
    (M3, M1): "North",
    (M3, M2): "West",
    (M3, M4): "East",
    (M3, M5): "South",
    (M4, M3): "West",
    (M5, M3): "North",
}


FIVE = ["Module1", "Module2", "Module3", "Module4", "Module5"]


def connected(fragment: str) -> dict[tuple[str, str], str]:
    """The cells of the matrix that show a connection, by (row, column)."""
    return {pair: text for pair, text in matrix_of(fragment).cells.items() if text}


def test_the_adjacency_matrix_shows_module_by_module_the_direction_of_each_connection_read(
    world, tasks, station
):
    """A row is the module a connection leaves, a column the module it leads to, and a cell
    the side it leaves by; on the cross, each connection runs both ways."""
    world.connections = set(CROSS)
    world.directions = dict(CROSS)
    m1, m3, _ = station(M1, M3, M4)
    world.place(B1, M1, destination=M4)
    m1.box_transport(world.transport(T1))
    tasks.run()  # Module 3 decides on Box 1 too

    fragment = box(dashboard_of(m3), "adjacency-matrix")

    matrix = matrix_of(fragment)
    assert (matrix.rows, matrix.columns) == (FIVE, FIVE)
    assert len(matrix.cells) == 25
    assert connected(fragment) == {
        ("Module1", "Module3"): "S↔",
        ("Module2", "Module3"): "E↔",
        ("Module3", "Module1"): "N↔",
        ("Module3", "Module2"): "W↔",
        ("Module3", "Module4"): "E↔",
        ("Module3", "Module5"): "S↔",
        ("Module4", "Module3"): "W↔",
        ("Module5", "Module3"): "N↔",
    }
    assert matrix.marked == ["Module3"]
    read_at = m3.memory.connections().at.strftime("%H:%M:%S")
    assert f"Read at {read_at} for the decision on Box1." in fragment


def spans_the_whole_width(fragment: str, box_id: str) -> bool:
    """Whether `fragment` carries the style that lays its box across every column of the tab's
    grid, where the dashboard draws each box in its element `box-<id>`."""
    return (
        re.search(
            rf"<style>[^<]*#box-{box_id}\s*{{\s*grid-column:\s*1\s*/\s*-1;?\s*}}",
            fragment,
        )
        is not None
    )


def test_the_adjacency_matrix_spans_the_whole_width_of_the_module_s_tab(station):
    """The matrix and the routing decisions each take a row of their own; the drawing of the
    module keeps its cell. So does the matrix before the module has read any connections."""
    m1, *_ = station(M1)
    dashboard = dashboard_of(m1)
    assert spans_the_whole_width(box(dashboard, "adjacency-matrix"), "adjacency-matrix")

    m1.memory.read_at_start(
        StartRead(connections=[DirectedConnection(M1, M2, "East")], held=[])
    )

    assert spans_the_whole_width(box(dashboard, "adjacency-matrix"), "adjacency-matrix")
    assert spans_the_whole_width(
        box(dashboard, "routing-decisions"), "routing-decisions"
    )
    assert not spans_the_whole_width(box(dashboard, "module"), "module")


def test_the_adjacency_matrix_is_a_drawing_that_scales_to_its_dashboard_box(station):
    m1, *_ = station(M1)
    m1.memory.read_at_start(
        StartRead(connections=[DirectedConnection(M1, M2, "East")], held=[])
    )

    fragment = box(dashboard_of(m1), "adjacency-matrix")

    svg = matrix_of(fragment).svg
    assert len((svg.get("viewbox") or "").split()) == 4
    assert "width" not in svg and "height" not in svg
    assert "width: 100%" in (svg.get("style") or "")
    assert "<table" not in fragment and "<script" not in fragment


def test_the_adjacency_matrix_shows_a_connection_one_way_in_one_cell_only(station):
    """On the grid, Module 1 hands boxes South and East, and takes none."""
    m1, *_ = station(M1)
    m1.memory.read_at_start(
        StartRead(
            connections=[
                DirectedConnection(M1, M2, "East"),
                DirectedConnection(M1, M3, "South"),
            ],
            held=[],
        )
    )

    fragment = box(dashboard_of(m1), "adjacency-matrix")

    assert matrix_of(fragment).rows == ["Module1", "Module2", "Module3"]
    assert connected(fragment) == {
        ("Module1", "Module2"): "E",
        ("Module1", "Module3"): "S",
    }
    assert matrix_of(fragment).marked == ["Module1"]


def test_the_adjacency_matrix_marks_a_connection_whose_direction_the_graph_does_not_name(
    station,
):
    m1, *_ = station(M1)
    m1.memory.read_at_start(
        StartRead(connections=[DirectedConnection(M1, M2, None)], held=[])
    )

    assert connected(box(dashboard_of(m1), "adjacency-matrix")) == {
        ("Module1", "Module2"): "•"
    }


def test_the_adjacency_matrix_lists_the_modules_in_the_order_of_their_numbers(
    world, station
):
    m10 = module_iri(10)
    m2, _ = station(M2, m10)
    world.connections = {(M2, m10), (m10, M2)}
    world.directions = {(M2, m10): "East", (m10, M2): "West"}
    world.place(B1, M2, destination=m10)

    m2.box_transport(world.transport(T1))

    matrix = matrix_of(box(dashboard_of(m2), "adjacency-matrix"))
    assert matrix.rows == matrix.columns == ["Module2", "Module10"]


def test_the_adjacency_matrix_shows_the_connections_read_at_the_start_until_a_decision(
    station,
):
    m1, *_ = station(M1)

    m1.memory.read_at_start(
        StartRead(connections=[DirectedConnection(M1, M2, "South")], held=[])
    )

    fragment = box(dashboard_of(m1), "adjacency-matrix")
    assert connected(fragment) == {("Module1", "Module2"): "S"}
    read_at = m1.memory.connections().at.strftime("%H:%M:%S")
    assert f"Read at {read_at}, when Module1 started." in fragment


def test_the_adjacency_matrix_keeps_the_read_of_the_last_decision_that_read_the_connections(
    world, tasks, station
):
    """Module 2 reads the connections to send Box 1 on to Module 3. Box 2's destination is
    Module 2 itself, so it gives Box 2 back without a read: the matrix still shows the read it
    made for Box 1, and says so."""
    world.connections = {(M1, M2), (M2, M3)}
    world.directions = {(M1, M2): "East", (M2, M3): "South"}
    m2, _ = station(M2, M3)
    world.place(B1, M2, destination=M3, status=OperationStatus.QUEUED)
    m2.box_transport(world.transport(T1))
    tasks.run()  # Module 3 takes Box 1 and gives it back
    world.place(B2, M2, destination=M2, status=OperationStatus.QUEUED)

    m2.box_transport(world.transport(T2))

    assert [decision.box for decision in m2.memory.decisions()] == [B2, B1]
    fragment = box(dashboard_of(m2), "adjacency-matrix")
    assert connected(fragment) == {
        ("Module1", "Module2"): "E",
        ("Module2", "Module3"): "S",
    }
    assert "for the decision on Box1." in fragment


def test_the_adjacency_matrix_says_when_the_module_has_read_no_connections(station):
    m1, *_ = station(M1)

    fragment = box(dashboard_of(m1), "adjacency-matrix")

    assert matrix_of(fragment).cells == {}
    assert "Module1 has read no connections yet." in fragment


def test_the_drawing_shows_the_box_the_module_holds_and_its_connections_by_direction(
    world, tasks, station
):
    world.connections = set(CROSS)
    world.directions = dict(CROSS)
    m1, m3, _ = station(M1, M3, M4)
    world.place(B1, M1, destination=M4)
    m1.box_transport(world.transport(T1))  # Box 1 moves onto Module 3
    # Module 3 decides on Box 1, and cannot reach Module 4: it keeps the box.
    world.unregistered.add(M4)
    tasks.run()

    fragment = box(dashboard_of(m3), "module")

    assert "<svg" in fragment and "<script" not in fragment
    drawing = drawn(fragment)
    assert drawing.boxes == ["Box1"]
    assert drawing.connections == {
        ("North", "out", "Module1"),
        ("East", "out", "Module4"),
        ("South", "out", "Module5"),
        ("West", "out", "Module2"),
        ("North", "in", "Module1"),
        ("East", "in", "Module4"),
        ("South", "in", "Module5"),
        ("West", "in", "Module2"),
    }
    assert "Module3 holds Box1." in fragment


def test_the_drawing_shows_a_connection_that_leads_one_way_on_one_side_only(
    world, station
):
    """On the grid, Module 1 hands boxes South and East, and takes none."""
    m1, *_ = station(M1)

    m1.memory.read_at_start(
        StartRead(
            connections=[
                DirectedConnection(M1, M2, "East"),
                DirectedConnection(M1, M3, "South"),
            ],
            held=[],
        )
    )

    fragment = box(dashboard_of(m1), "module")
    assert drawn(fragment).connections == {
        ("East", "out", "Module2"),
        ("South", "out", "Module3"),
    }
    assert drawn(fragment).boxes == []
    assert "Module1 holds no box." in fragment


def test_the_drawing_names_the_faults_armed(station):
    m1, *_ = station(M1)

    assert "Faults armed: none." in box(dashboard_of(m1), "module")
    m1.faults["skip-reserve"] = True
    m1.faults["complete-without-releasing"] = True
    assert "Faults armed: skip-reserve, complete-without-releasing." in box(
        dashboard_of(m1), "module"
    )


def test_the_dashboard_and_its_boxes_ask_for_everything_by_a_relative_path(
    world, tasks, station
):
    """So that they work at the module's own address and through the Launcher, under `/p/<id>/`:
    the middleware's page with its scripts, and every box the page asks for, the module's and
    the instance's alike, each with something to show."""
    world.connections = set(CROSS)
    world.directions = dict(CROSS)
    m1, m3, _ = station(M1, M3, M4)
    world.place(B1, M1, destination=M4, status=OperationStatus.QUEUED)
    m1.box_transport(world.transport(T1))  # Box 1 moves onto Module 3
    # Module 3 decides on Box 1, and cannot reach Module 4: it keeps the box.
    world.unregistered.add(M4)
    tasks.run()
    m3.faults["skip-reserve"] = True
    dashboard = dashboard_of(m3)
    assert drawn(box(dashboard, "module")).boxes == ["Box1"]
    assert rows(box(dashboard, "routing-decisions")) != []
    assert connected(box(dashboard, "adjacency-matrix")) != {}

    page = dashboard.get("/dashboard").text

    assert absolute_paths(page) == []
    asked = {
        urlsplit(urljoin("http://module/dashboard", path)).path
        for path in attribute_values(page, "hx-get")
    }
    assert {f"/dashboard/box/{box_id}" for box_id in BOXES} < asked, asked
    for route in sorted(asked):
        fragment = dashboard.get(route)
        assert fragment.status_code == 200, (route, fragment.text)
        assert absolute_paths(fragment.text) == [], route


def test_every_box_renders_from_memory_with_no_read_of_the_graph(world, tasks, station):
    world.connections = set(CROSS)
    world.directions = dict(CROSS)
    m1, m3, _ = station(M1, M3, M4)
    world.place(B1, M1, destination=M4)
    m1.box_transport(world.transport(T1))
    tasks.run()
    dashboards = [dashboard_of(m) for m in (m1, m3)]
    read_so_far = list(world.reads)

    for dashboard in dashboards:
        for box_id in BOXES:
            box(dashboard, box_id)

    assert world.reads == read_so_far
