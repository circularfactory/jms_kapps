"""The WarehouseManagementSystem's spawn control: its two workflows, its timer, and its dashboard.

The WMS's middleware is built here and never started, so it registers nothing and reads nothing
from a store: its routes are called through a test client. A spawn is either a fake, which only
counts what it was asked, or the WarehouseManagementSystem on the FlexConveyor station of
`_station`, whose reads are counted.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import pytest
from _html import (
    absolute_paths,
    attribute_values,
    in_force,
    opened_tabs,
    reaches,
    rows,
    selects,
)
from _station import (
    MiddlewareHandover,
    Moves,
    Network,
    Notifications,
    Reads,
    Records,
    Starts,
    Stop,
    Tasks,
    World,
    store_refusal,
)
from _wait import eventually
from fastapi.testclient import TestClient
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_triplestore_interface.exceptions import TripleStoreResponseError
from rdflib.namespace import RDF, SH, XSD

from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.spawn_rate import SpawnTimer
from jms_kapps.uc2.vocabulary import FC, WMS, box_iri, local_name, module_iri
from jms_kapps.uc2.wms import (
    SpawnBoxRequest,
    SpawnBoxResult,
    WarehouseManagementSystem,
)
from jms_kapps.uc2.wms_memory import WmsMemory
from jms_kapps.uc2.wms_process import serve_spawning

SPAWN = "/workflows/spawn_box/execute"
# The name of a box the WMS spawns: `Box-<UUID>` (jms_kapps 92).
UUID_BOX = r"Box-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
SET_RATE = "/workflows/set_spawn_rate/execute"
M1, M2, M3 = module_iri(1), module_iri(2), module_iri(3)
REPORT = (
    f"_:report <{RDF.type}> <{SH.ValidationReport}> .\n"
    f'_:report <{SH.conforms}> "false"^^<{XSD.boolean}> .\n'
)

# The store's refusal of a second box on Module 1, in the form GraphDB sends it: N-Quads.
POSSESSION_REPORT = (
    REPORT
    + f"_:report <{SH.result}> _:result .\n"
    + f"_:result <{RDF.type}> <{SH.ValidationResult}> .\n"
    + f"_:result <{SH.focusNode}> <{M1}> .\n"
    + f"_:result <{SH.sourceShape}> <{FC.FlexConveyorModulePossessionShape}> .\n"
    + f'_:result <{SH.resultMessage}> "A FlexConveyorModule may possess at most one Box at a '
    + 'time." .\n'
)

# 100 boxes a second: a timer that spawns shows within a few hundredths of a second.
FAST = 6000


class _NoStore:
    """An object-graph mapper with no store behind it. The middleware is never started, so it
    registers nothing, and nothing a test calls here reaches a graph."""

    db = None


def wms_middleware() -> SemanticMiddleware:
    """The WarehouseManagementSystem's middleware, as its process builds it, never started."""
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(WMS),
        service_class=str(FC.WarehouseManagementSystemService),
        ogm=_NoStore(),
        host="127.0.0.1",
        port=8000,
        activity_feed=True,
    )


class FakeSpawn:
    """A spawn that accepts every request, and keeps each one."""

    def __init__(self) -> None:
        self.requests: list[SpawnBoxRequest] = []

    def __call__(self, request: SpawnBoxRequest) -> SpawnBoxResult:
        self.requests.append(request)
        return SpawnBoxResult(accepted=True)


@pytest.fixture
def spawns() -> FakeSpawn:
    return FakeSpawn()


@pytest.fixture
def client(spawns):
    middleware = wms_middleware()
    timer = serve_spawning(middleware, spawns, WmsMemory())
    yield TestClient(middleware.app)
    timer.set(0)


def set_rate(client: TestClient, boxes_per_minute: float) -> dict:
    response = client.post(SET_RATE, json={"boxes_per_minute": boxes_per_minute})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_rate_starts_a_timer_that_spawns_leaving_origin_and_destination_to_the_wms(
    client, spawns
):
    assert set_rate(client, FAST) == {"boxes_per_minute": FAST}

    eventually(lambda: len(spawns.requests) >= 3, timeout=5)
    assert all(request == SpawnBoxRequest() for request in spawns.requests)


def test_a_rate_of_0_stops_the_timer(client, spawns):
    set_rate(client, FAST)
    eventually(lambda: len(spawns.requests) >= 1, timeout=5)

    assert set_rate(client, 0) == {"boxes_per_minute": 0}

    time.sleep(0.1)  # a spawn already under way when the rate changed may still end
    stopped_at = len(spawns.requests)
    time.sleep(0.3)
    assert len(spawns.requests) == stopped_at


def test_the_first_box_of_a_rate_is_spawned_at_once_and_the_next_one_an_interval_later(
    client, spawns
):
    set_rate(client, 1)

    eventually(lambda: len(spawns.requests) == 1, timeout=2)
    time.sleep(0.3)
    assert len(spawns.requests) == 1


def test_a_change_of_rate_counts_from_the_last_due_spawn():
    """From 1 box a minute to 600: the next box is due a tenth of a second after the first, which
    is past, so it comes at once. Then from 6000 to 6: the next is due ten seconds after the last
    due one. (Added on 2026-10-07, jms_kapps 51, decided by Etienne: "a change of rate counts from
    the last due spawn".)"""
    started: list[float] = []
    timer = SpawnTimer(lambda: started.append(time.monotonic()))
    try:
        timer.set(1)
        eventually(lambda: len(started) == 1, timeout=2)
        time.sleep(0.3)
        changed = time.monotonic()
        timer.set(600)
        eventually(lambda: len(started) == 2, timeout=2)
        assert started[1] - changed < 0.05

        timer.set(FAST)
        eventually(lambda: len(started) >= 5, timeout=2)
        timer.set(6)
        # A spawn the timer started just before the change may still append.
        time.sleep(0.05)
        slowed = len(started)
        time.sleep(0.3)
        assert len(started) == slowed
    finally:
        timer.stop(timeout=5)


def test_stopping_the_timer_waits_until_every_spawn_under_way_has_ended():
    """The timer spawns on daemon threads, which the end of the process would cut short, mid
    spawn. Its stop at the process's shutdown waits for every spawn under way, and no spawn
    follows them. (Changed on 2026-10-07, jms_kapps 51, decided by Etienne: spawns of the timer
    overlap. Until then the test was named "..._waits_until_the_spawn_under_way_has_ended", and
    its one spawn was followed by none.)"""
    some_under_way, may_end = threading.Event(), threading.Event()
    lock = threading.Lock()
    spawned = {"started": 0, "ended": 0}

    def spawn() -> None:
        with lock:
            spawned["started"] += 1
            if spawned["started"] == 3:
                some_under_way.set()
        may_end.wait(timeout=5)
        with lock:
            spawned["ended"] += 1

    timer = SpawnTimer(spawn)
    timer.set(FAST)
    assert some_under_way.wait(timeout=5)
    threading.Timer(0.2, may_end.set).start()

    timer.stop()
    with lock:
        at_the_stop = dict(spawned)

    assert at_the_stop["started"] >= 3
    assert at_the_stop["ended"] == at_the_stop["started"]
    assert timer.boxes_per_minute == 0
    time.sleep(0.1)
    assert spawned == at_the_stop


def test_a_due_spawn_for_which_no_thread_starts_runs_on_the_timer_s_own_thread(
    monkeypatch, caplog
):
    """A process that can start no more threads loses no spawn that was due: the timer spawns on
    its own thread instead, and says so. (Added on 2026-10-07, jms_kapps 51: "no box is lost
    silently"; running it on the timer's thread is the build's reading.)"""
    start = threading.Thread.start

    def no_thread_for_a_spawn(thread: threading.Thread) -> None:
        if thread.name == "spawn":
            raise RuntimeError("can't start new thread")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", no_thread_for_a_spawn)
    spawned: list[str] = []
    timer = SpawnTimer(lambda: spawned.append(threading.current_thread().name))

    timer.set(FAST)
    eventually(lambda: len(spawned) >= 2, timeout=5)
    timer.stop(timeout=5)

    assert set(spawned) == {"spawn-timer"}
    assert any("could start no thread" in r.getMessage() for r in caplog.records)


def test_stopping_the_timer_waits_for_the_spawn_under_way_no_longer_than_its_bound():
    """A spawn that hangs does not hold the process's stop until the Launcher kills it."""
    under_way, may_end = threading.Event(), threading.Event()

    def spawn() -> None:
        under_way.set()
        may_end.wait(timeout=5)

    timer = SpawnTimer(spawn)
    timer.set(FAST)
    assert under_way.wait(timeout=5)
    asked = time.monotonic()

    timer.stop(timeout=0.2)

    assert time.monotonic() - asked < 2
    assert timer.boxes_per_minute == 0
    may_end.set()


@pytest.mark.parametrize("boxes_per_minute", [-1, "many"])
def test_a_rate_that_is_no_number_of_0_or_more_is_refused_and_the_rate_stays(
    client, spawns, boxes_per_minute
):
    response = client.post(SET_RATE, json={"boxes_per_minute": boxes_per_minute})

    assert response.status_code == 422
    time.sleep(0.1)
    assert spawns.requests == []


# -- The boxes, each rendered from what the WMS holds ------------------------------------------ #


@dataclass
class Station:
    """The WMS on Module 1, an entry module, connected to Module 2, an exit module, served by a
    test client."""

    world: World
    records: Records
    tasks: Tasks
    wms: WarehouseManagementSystem
    client: TestClient
    stop: Stop

    def box(self, box_id: str) -> str:
        """The fragment the dashboard serves for the box `box_id`."""
        response = self.client.get(f"/dashboard/box/{box_id}")
        assert response.status_code == 200, response.text
        assert "failed to render" not in response.text, response.text
        return response.text


@pytest.fixture
def station():
    world = World()
    world.connections = {(M1, M2)}
    world.classes[FC.EntryModule] = [M1]
    world.classes[FC.ExitModule] = [M2]
    served, timer = serve_station(world)
    yield served
    timer.set(0)


def serve_station(world: World) -> tuple[Station, SpawnTimer]:
    """The WMS on Modules 1 and 2 of `world`, served by a test client, and its spawn timer. Its
    form offers the modules the WMS reads, as its process builds it."""
    world.wms_iri = WMS
    records, tasks, network = Records(world), Tasks(), Network()
    notifications, moves = Notifications(world, tasks), Moves(world)
    stop = Stop()
    wms = WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=records,
        call=network.call,
        notify=notifications.notify,
        choose=lambda options: options[-1],
        stopping=stop.has_begun,
    )
    notifications.receivers[WMS] = wms.transport_ended
    for resource in (M1, M2):
        module = FlexConveyorModule(
            resource,
            graph=Reads(world, resource),
            handover=MiddlewareHandover(world, resource),
            call=network.call,
            records=records,
            notify=notifications.notify,
            move=moves.move,
            start=Starts(tasks),
        )
        network.peers[resource] = module
        notifications.receivers[resource] = module.box_transport
    middleware = wms_middleware()
    origins, destinations = wms.origins_and_destinations()
    timer = serve_spawning(
        middleware,
        wms.spawn,
        wms.memory,
        origins=origins,
        destinations=destinations,
    )
    return Station(world, records, tasks, wms, TestClient(middleware.app), stop), timer


def test_once_the_stop_has_begun_the_spawn_rate_s_timer_opens_no_transport(station):
    """The timer spawns as the workflow does, so each of its spawns is refused and says why."""
    station.stop.begun = True

    set_rate(station.client, FAST)

    eventually(lambda: len(station.wms.memory.answers()) >= 3, timeout=5)
    assert {answer.result.reason for answer in station.wms.memory.answers()} == {
        "the system is stopping"
    }
    assert station.records.writes == []


def test_the_spawn_form_offers_the_entry_and_exit_modules_or_the_wms_s_choice(station):
    fragment = station.box("spawn")

    assert selects(fragment) == {
        "origin": [("", "the WMS chooses"), (str(M1), "Module1")],
        "destination": [("", "the WMS chooses"), (str(M2), "Module2")],
    }
    (posted_to,) = attribute_values(fragment, "hx-post")
    assert reaches("/dashboard", posted_to, SPAWN), posted_to
    assert 'hx-ext="json-enc"' in fragment
    assert 'hx-swap="none"' in fragment


def test_where_the_topology_names_no_roles_the_spawn_form_offers_every_module_both_ways():
    """Added on 2026-10-06, jms_kapps 45, decided by Etienne: roles are optional."""
    world = World()
    world.connections = {(M1, M2), (M2, M1)}
    world.classes[FC.FlexConveyorModule] = [M1, M2]
    served, timer = serve_station(world)
    try:
        fragment = served.box("spawn")
        named = spawn_as_the_form_does(served, str(M2), str(M1))
    finally:
        timer.set(0)

    every_module = [("", "the WMS chooses"), (str(M1), "Module1"), (str(M2), "Module2")]
    assert selects(fragment) == {"origin": every_module, "destination": every_module}
    assert named["accepted"], named


def spawn_as_the_form_does(
    station: Station, origin: str = "", destination: str = ""
) -> dict:
    """POST a spawn as the form encodes it: each select's value, empty for the WMS's choice."""
    response = station.client.post(
        SPAWN, json={"origin": origin, "destination": destination}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_spawn_from_the_form_left_to_the_wms_answers_in_last_spawns(station):
    answer = spawn_as_the_form_does(station)

    assert answer["accepted"], answer
    assert (answer["origin"], answer["destination"]) == (str(M1), str(M2))
    [(time_of_day, box, way, outcome, reason)] = rows(station.box("last-spawns"))
    assert len(time_of_day) == len("12:34:56")
    # The box's whole name, `Box-<UUID>` (jms_kapps 92).
    assert re.fullmatch(UUID_BOX, box), box
    assert box == local_name(answer["box"])
    assert (way, outcome, reason) == ("Module1 → Module2", "accepted", "—")


def test_a_spawn_with_no_free_entry_module_shows_why_it_was_refused(station):
    station.world.place(box_iri(7), M1)

    answer = spawn_as_the_form_does(station, destination=str(M2))

    assert not answer["accepted"]
    [(_, box, way, outcome, reason)] = rows(station.box("last-spawns"))
    assert (box, way, outcome, reason) == (
        "—",
        "— → Module2",
        "refused",
        "no entry module is free",
    )


@pytest.mark.parametrize(
    ("origin", "destination", "way", "reason"),
    [
        (M2, M2, "Module2 → Module2", "Module2 is not an entry module"),
        (M1, M1, "Module1 → Module1", "Module1 is not an exit module"),
    ],
    ids=["an exit module as the origin", "an entry module as the destination"],
)
def test_a_script_that_names_a_module_of_the_wrong_kind_reads_why_in_its_answer_and_last_spawns(
    station, origin, destination, way, reason
):
    """The form offers only entry modules as the origin and exit modules as the destination; a
    script may post any module."""
    answer = spawn_as_the_form_does(station, str(origin), str(destination))

    assert (answer["accepted"], answer["reason"]) == (False, reason)
    [(_, box, shown_way, outcome, shown_reason)] = rows(station.box("last-spawns"))
    assert (box, shown_way, outcome, shown_reason) == ("—", way, "refused", reason)
    assert station.records.writes == []


def test_last_spawns_shows_the_20_newest_answers_newest_first(station):
    station.world.place(box_iri(7), M1)
    for _ in range(21):
        spawn_as_the_form_does(station)
    station.world.held[M1].clear()

    spawn_as_the_form_does(station)

    fragment = station.box("last-spawns")
    shown = rows(fragment)
    assert len(shown) == 20
    assert [row[3] for row in shown] == ["accepted"] + ["refused"] * 19
    # Said as "Held boxes" and "Transports" say it.
    assert "The newest 20 of 22 spawns, newest first." in fragment


def test_last_spawns_says_nothing_of_the_newest_while_it_shows_every_answer(station):
    for _ in range(20):
        spawn_as_the_form_does(station)

    fragment = station.box("last-spawns")

    assert len(rows(fragment)) == 20
    assert "The newest" not in fragment


def test_a_spawn_that_raised_shows_its_error_in_last_spawns(station):
    station.records.fails_with = TripleStoreResponseError("Error while querying GraphDB (503)")

    with pytest.raises(TripleStoreResponseError):
        station.client.post(SPAWN, json={"origin": str(M1), "destination": ""})

    [(_, box, way, outcome, reason)] = rows(station.box("last-spawns"))
    assert (box, way, outcome) == ("—", "Module1 → —", "refused")
    assert reason == "the spawn failed: Error while querying GraphDB (503)"


def test_held_boxes_and_transports_follow_the_spawns_and_the_notifications(station):
    box = local_name(spawn_as_the_form_does(station)["box"])

    assert rows(station.box("transports")) == [
        [f"{box}_transport", box, "Module1 → Module2", "queued", "—"]
    ]
    assert rows(station.box("held-boxes")) == []
    assert "holds no box" in station.box("held-boxes")

    station.tasks.run()  # Module 1 hands the box to Module 2, which gives it back: done

    assert rows(station.box("transports")) == [
        [f"{box}_transport", box, "Module1 → Module2", "done", "—"]
    ]
    assert rows(station.box("held-boxes")) == [[box, f"{box}_transport", "done"]]


def test_a_create_the_store_refuses_shows_the_store_s_reason_in_last_spawns(station):
    """As when a neighbour with `skip-reserve` armed has filled the origin since its answer to
    Reserve: nothing is written, so no transport and no held box are shown, and "Last spawns"
    reads why, the rule the store names and its message (jms_kapps 41)."""
    station.records.fails_with = store_refusal(POSSESSION_REPORT)

    spawn_as_the_form_does(station)

    assert rows(station.box("transports")) == []
    assert rows(station.box("held-boxes")) == []
    [(_, box, way, outcome, why)] = rows(station.box("last-spawns"))
    assert (box, way, outcome) == ("—", "Module1 → Module2", "refused")
    assert re.fullmatch(
        f"the triple store refused creating {UUID_BOX} on Module1, by "
        "FlexConveyorModulePossessionShape at Module1: A FlexConveyorModule may possess at "
        r"most one Box at a time\.",
        why,
    ), why


def test_a_transport_that_failed_on_the_station_is_shown_failed_with_why(station):
    """Module 1 cannot reach Module 2, the box's destination, and has no other way there: the
    transport fails with no live route left, and the WMS's dashboard shows that reason. (Changed
    on 2026-10-11, jms_kapps 77, decided by Etienne: the transport fails only with no live route
    left. Until then the reason was only checked to start with "Module2".)"""
    spawn_as_the_form_does(station)
    station.world.held[M2].append(box_iri(7))  # Module 2 holds a box of its own

    station.world.unregistered.add(M2)  # and Module 1 cannot reach its Reserve
    station.tasks.run()

    [(_, _, _, status, why)] = rows(station.box("transports"))
    assert status == "failed"
    assert why == "no live route to Module2: Module2 is not reachable"
    assert rows(station.box("held-boxes")) == []


def test_transports_and_held_boxes_show_the_newest_50_and_say_how_many_there_are(
    station,
):
    for _ in range(51):
        newest = spawn_as_the_form_does(station)
        assert newest["accepted"]
        station.tasks.run()

    for box_id, things in [
        ("transports", "transports"),
        ("held-boxes", "boxes it holds"),
    ]:
        fragment = station.box(box_id)
        shown = rows(fragment)
        assert len(shown) == 50
        assert shown[0][0].startswith(local_name(newest["box"]))
        assert f"The newest 50 of 51 {things}, newest first." in fragment


BOXES = ["spawn", "spawn-rate", "last-spawns", "held-boxes", "transports"]


def test_every_box_renders_from_memory_with_no_read_of_the_graph(station):
    spawn_as_the_form_does(station)
    station.tasks.run()
    station.world.place(box_iri(7), M1)
    spawn_as_the_form_does(station)
    read_so_far = list(station.world.reads)

    for box_id in BOXES:
        station.box(box_id)

    assert station.world.reads == read_so_far


def rate_box(client: TestClient) -> str:
    response = client.get("/dashboard/box/spawn-rate")
    assert response.status_code == 200, response.text
    return response.text


def test_the_rate_form_posts_to_the_rate_workflow(client):
    fragment = rate_box(client)

    (posted_to,) = attribute_values(fragment, "hx-post")
    assert reaches("/dashboard", posted_to, SET_RATE), posted_to
    assert 'hx-ext="json-enc"' in fragment
    assert 'hx-swap="none"' in fragment
    assert 'name="boxes_per_minute"' in fragment


def test_the_rate_box_shows_the_rate_in_force_and_asks_for_it_again(client):
    assert (
        in_force(rate_box(client))[0] == "In force: 0 boxes per minute, so none spawns"
    )

    set_rate(client, 1.5)

    text, attributes = in_force(rate_box(client))
    assert text == "In force: 1.5 boxes per minute"
    assert reaches("/dashboard", attributes["hx-get"], "/dashboard/box/spawn-rate")
    assert attributes["hx-select"] == ".rate-in-force"
    assert attributes["hx-swap"] == "outerHTML"


def test_a_rate_is_taken_as_the_form_encodes_it_a_string(client):
    """`json-enc` sends each field of a form as a string."""
    assert set_rate(client, "0.5") == {"boxes_per_minute": 0.5}


# -- What the WMS serves ------------------------------------------------------------------------ #


def paths(middleware: SemanticMiddleware) -> set[str]:
    return {route.path for route in middleware.app.routes}


def test_beside_the_dashboard_the_wms_adds_no_route_but_its_two_workflows(spawns):
    bare = wms_middleware()
    enable_dashboard(bare)
    served = wms_middleware()

    serve_spawning(served, spawns, WmsMemory())

    added = paths(served) - paths(bare)
    assert {SPAWN, SET_RATE} <= added
    assert all(path.startswith("/workflows/") for path in added), added
    assert {path.split("/")[2] for path in added} == {"spawn_box", "set_spawn_rate"}


def test_the_dashboard_has_a_tab_wms_with_the_five_boxes(client):
    page = client.get("/dashboard").text

    assert '<label for="tab-wms" class="tab-label">WMS</label>' in page
    asked = attribute_values(page, "hx-get")
    for box_id in BOXES:
        assert any(
            reaches("/dashboard", path, f"/dashboard/box/{box_id}") for path in asked
        ), (box_id, asked)


def test_the_tab_system_shows_the_middleware_s_box_of_refused_writes(client):
    """A write of the WMS that the store refused shows there, as the middleware shows any refused
    write of an instance; an instance just started has none."""
    page = client.get("/dashboard").text
    asked = attribute_values(page, "hx-get")
    assert any(
        reaches("/dashboard", path, "/dashboard/box/refused-writes") for path in asked
    ), asked

    box = client.get("/dashboard/box/refused-writes")

    assert box.status_code == 200
    assert "No write of this instance was refused since it started." in box.text


def test_the_dashboard_opens_on_the_tab_wms(client):
    """The WMS's boxes are what a person comes for; the boxes of the instance wait in the tab
    "System". A link that names another tab opens that one."""
    assert opened_tabs(client.get("/dashboard").text) == ["tab-wms"]
    assert opened_tabs(client.get("/dashboard?tab=System").text) == ["tab-system"]


def test_the_dashboard_and_its_boxes_ask_for_everything_by_a_relative_path(station):
    """So that they work at the WMS's own address and through the Launcher, under `/p/<id>/`:
    the middleware's page with its scripts, and every box the page asks for, the WMS's and the
    instance's alike, a box added later too."""
    page = station.client.get("/dashboard").text
    assert absolute_paths(page) == []
    asked = {
        urlsplit(urljoin("http://wms/dashboard", path)).path
        for path in attribute_values(page, "hx-get")
    }
    assert {f"/dashboard/box/{box_id}" for box_id in BOXES} < asked, asked
    for route in sorted(asked):
        fragment = station.client.get(route)
        assert fragment.status_code == 200, (route, fragment.text)
        assert absolute_paths(fragment.text) == [], route


WHOLE_WIDTH = "<style>#box-transports { grid-column: 1 / -1; }</style>"


def test_transports_takes_the_whole_width_of_the_tab(station):
    """Its column "Reason" is not cut to its first letter at half the tab's width. The rule spans
    the element that the dashboard draws the box in, `box-transports`, one cell of the tab's grid:
    were the dashboard to name that element otherwise, the rule would span nothing."""
    page = station.client.get("/dashboard").text
    assert '<div id="box-transports" ' in page
    assert 'class="tab-panel box-grid"' in page

    assert WHOLE_WIDTH in station.box("transports")
    spawn_as_the_form_does(station)
    assert WHOLE_WIDTH in station.box("transports")
