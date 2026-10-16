"""The WarehouseManagementSystem's dashboard on the 5-module cross, used as a person uses it, and
its form on the grid.

The parties are served in this process (`_participants`), with a transit time of 0, and the WMS
is built as its process builds it. The test reads the dashboard's boxes and posts what its forms
post: on the cross, two boxes with their origin and destination picked, one left to the WMS, and
then a spawn rate, until it is set to 0. The cross names no roles, so its form offers every module
both ways; the grid names its entry row and its exits, so its form offers only those, and a spawn
from another module is refused (added on 2026-10-06, jms_kapps 45, decided by Etienne: roles are
optional; until then the cross's form offered Modules 1 and 2 as the origin and Modules 4 and 5
as the destination, and one box was picked).

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import time

import httpx
import pytest
from _html import in_force, rows, selects
from _participants import Participants, participants
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.vocabulary import WMS, module_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

M1, M2, M3, M4, M5 = (module_iri(n) for n in (1, 2, 3, 4, 5))
CROSS_TOPOLOGY = shipped_topology("cross")
GRID_TOPOLOGY = shipped_topology("grid")
TRANSPORTS_SECONDS = 120.0
# Longer than one spawn takes on a shared store: the box made, handed to its origin, notified.
SPAWN_SECONDS = 5.0
# The cross names no entry module (changed on 2026-10-06, jms_kapps 45, decided by Etienne: roles
# are optional; until then "no entry module is free").
NO_MODULE_FREE = "no module is free"


class Dashboard:
    """The WMS's dashboard and its two workflows, at the address the WMS serves on."""

    def __init__(self, party: Participants) -> None:
        self.address = str(party.served[WMS].middleware.address).rstrip("/")

    def box(self, box_id: str) -> str:
        response = httpx.get(f"{self.address}/dashboard/box/{box_id}", timeout=10)
        response.raise_for_status()
        assert "failed to render" not in response.text, response.text
        return response.text

    def post(self, workflow: str, body: dict) -> dict:
        response = httpx.post(
            f"{self.address}/workflows/{workflow}/execute", json=body, timeout=60
        )
        response.raise_for_status()
        return response.json()

    def spawn(self, origin: str = "", destination: str = "") -> dict:
        """Spawn as the form does, asking again while no module is free, or while the origin
        refuses to reserve with a refusal that passes, as it does while Module 3 is reserved for
        another box (added on 2026-10-06, jms_kapps 43)."""
        deadline = time.monotonic() + TRANSPORTS_SECONDS
        while True:
            answer = self.post(
                "spawn_box", {"origin": origin, "destination": destination}
            )
            passes = (
                answer["reason"] == NO_MODULE_FREE
                or "refused to reserve" in answer["reason"]
            )
            if answer["accepted"] or not passes:
                return answer
            assert time.monotonic() < deadline, "no module became free"
            time.sleep(0.1)

    def statuses(self) -> list[str]:
        """The last known status of each transport, newest first."""
        return [row[3] for row in rows(self.box("transports"))]


def all_ended(dashboard: Dashboard, count: int) -> bool:
    statuses = dashboard.statuses()
    return len(statuses) == count and all(s in ("done", "failed") for s in statuses)


def test_the_dashboard_spawns_boxes_and_a_rate_on_the_cross(dataset):
    """Five boxes spawned by hand, four named and one left to the WMS, end done; then a spawn rate
    of 120 boxes per minute spawns boxes, and a rate of 0 stops it.

    The rate step shows that the rate spawns boxes that the origin accepts: it passes once 5
    accepted boxes that the rate spawned have appeared in "Last spawns" within 120 s, and the 5
    boxes spawned by hand do not count. "Last spawns" keeps the 20 newest answers, approximately the
    last 10 s at this rate, so the step collects the accepted boxes of every read over the full
    wait, by box name, and does not ask for 5 among the newest rows of one read. (Sören's decision
    of 2026-10-13 as the product owner of the tests, jms_kapps 85, note 1866296, decision 3;
    jms_kapps 102. Until then the step passed when 5 rows of one read were accepted: it depended on
    Module 3 moving a box in 2.4 s or less, a speed limit of the store and no decided behaviour,
    and it counted the boxes spawned by hand, so it could pass before the rate spawned one.)
    """
    with participants(dataset, CROSS_TOPOLOGY) as party:
        dashboard = Dashboard(party)

        every_module = [("", "the WMS chooses")] + [
            (str(m), m.fragment) for m in (M1, M2, M3, M4, M5)
        ]
        assert selects(dashboard.box("spawn")) == {
            "origin": every_module,
            "destination": every_module,
        }

        # Module 1 to Module 4, as before; then from a module that was an exit to one that was
        # an entry, until the cross lost its roles; then from and to Module 3, in the middle.
        named = [(M1, M4), (M5, M2), (M3, M1), (M2, M3)]
        answers = [dashboard.spawn(str(o), str(d)) for o, d in named]
        left = dashboard.spawn()
        assert all(a["accepted"] for a in [*answers, left]), (answers, left)
        eventually(lambda: all_ended(dashboard, 5), timeout=TRANSPORTS_SECONDS)
        assert dashboard.statuses() == ["done"] * 5
        # Last spawns shows the newest answers, among them those the origin refused while
        # Module 3 was reserved for a box; Transports shows each box's way. Each box is named by
        # a UUID (jms_kapps 92): the dashboard shows the names the answers gave.
        boxes = [IRI(a["box"]).fragment for a in [*answers, left]]
        assert rows(dashboard.box("last-spawns"))[0][1:4] == [
            boxes[-1],
            f"{IRI(left['origin']).fragment} → {IRI(left['destination']).fragment}",
            "accepted",
        ]
        ways = {row[1]: row[2] for row in rows(dashboard.box("transports"))}
        assert [ways[box] for box in boxes[:4]] == [
            f"{o.fragment} → {d.fragment}" for o, d in named
        ]
        held = [row[0] for row in rows(dashboard.box("held-boxes"))]
        assert len(held) == 5 and set(held) == set(boxes), held

        assert dashboard.post("set_spawn_rate", {"boxes_per_minute": "120"}) == {
            "boxes_per_minute": 120
        }
        assert in_force(dashboard.box("spawn-rate"))[0] == (
            "In force: 120 boxes per minute"
        )
        # The step counts the accepted boxes that the rate spawned, over the full wait of 120 s
        # (Sören's decision of 2026-10-13, jms_kapps 85, note 1866296, decision 3). Each read adds
        # the accepted boxes of "Last spawns" to a set, by box name, leaving out the boxes spawned
        # by hand above. "Last spawns" keeps 20 rows, approximately the last 10 s at 120 boxes per
        # minute, so a read each 50 ms misses no row.
        spawned_by_hand = set(boxes)
        accepted_from_the_rate: set[str] = set()

        def five_accepted_from_the_rate() -> bool:
            accepted_from_the_rate.update(
                row[1]
                for row in rows(dashboard.box("last-spawns"))
                if row[3] == "accepted" and row[1] not in spawned_by_hand
            )
            return len(accepted_from_the_rate) >= 5

        eventually(
            five_accepted_from_the_rate, timeout=TRANSPORTS_SECONDS, interval=0.05
        )
        assert dashboard.post("set_spawn_rate", {"boxes_per_minute": 0}) == {
            "boxes_per_minute": 0
        }
        assert in_force(dashboard.box("spawn-rate"))[0] == (
            "In force: 0 boxes per minute, so none spawns"
        )

        # A spawn under way when the rate changed still ends: on a shared store one takes a few
        # seconds. Then every transport the WMS opened ends, and no further box comes.
        time.sleep(SPAWN_SECONDS)
        opened = len(dashboard.statuses())
        eventually(lambda: all_ended(dashboard, opened), timeout=TRANSPORTS_SECONDS)
        answered = rows(dashboard.box("last-spawns"))
        time.sleep(SPAWN_SECONDS)
        assert rows(dashboard.box("last-spawns")) == answered
        assert len(dashboard.statuses()) == opened
        assert set(dashboard.statuses()) == {"done"}


def test_on_the_grid_the_wms_picks_only_an_entry_module_as_the_origin_and_an_exit_as_the_destination(
    dataset,
):
    """Spawns left to the WMS on the grid, read from their answers: every origin it picks is on
    the entry row, and every destination is an exit. The modules are not served, so each spawn is
    then refused at the origin, which has no BoxTransportCapability: the picks are what this
    checks; the real run of jms_kapps 45 shows boxes carried on the grid. (Added on 2026-10-06,
    jms_kapps 45, decided by Etienne: the grid keeps its entry row and its exits.)"""
    with participants(
        dataset,
        GRID_TOPOLOGY,
        elsewhere={module.resource for module in GRID_TOPOLOGY.modules},
    ) as party:
        dashboard = Dashboard(party)
        answers = [dashboard.post("spawn_box", {}) for _ in range(12)]

    entry_row = {str(module_iri(n)) for n in range(1, 7)}
    exits = {str(module_iri(n)) for n in range(25, 31)}
    assert all(not a["accepted"] for a in answers), answers
    assert {a["origin"] for a in answers} <= entry_row, answers
    assert {a["destination"] for a in answers} <= exits, answers


def test_on_the_grid_the_form_offers_the_entry_row_and_a_spawn_from_another_module_is_refused(
    dataset,
):
    """The grid names its entry row and its exits, so a named spawn is held to them, and the
    reason shows in "Last spawns". The spawn is refused before the WMS asks any module, so the WMS
    alone is served. (Added on 2026-10-06, jms_kapps 45, decided by Etienne.)"""
    with participants(
        dataset,
        GRID_TOPOLOGY,
        elsewhere={module.resource for module in GRID_TOPOLOGY.modules},
    ) as party:
        dashboard = Dashboard(party)

        offered = selects(dashboard.box("spawn"))
        answer = dashboard.post(
            "spawn_box", {"origin": str(module_iri(7)), "destination": ""}
        )
        shown = rows(dashboard.box("last-spawns"))

    wms_chooses = [("", "the WMS chooses")]
    assert offered == {
        "origin": wms_chooses
        + [(str(module_iri(n)), f"Module{n}") for n in range(1, 7)],
        "destination": wms_chooses
        + [(str(module_iri(n)), f"Module{n}") for n in range(25, 31)],
    }
    assert (answer["accepted"], answer["reason"]) == (
        False,
        "Module7 is not an entry module",
    )
    assert [row[1:] for row in shown] == [
        ["—", "Module7 → —", "refused", "Module7 is not an entry module"]
    ]
