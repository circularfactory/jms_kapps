"""A module's process serves the middleware's state dashboard: the middleware's own boxes, and a
tab named after the module with three of the module's own.

The dashboard is the page of what one middleware instance holds: its identity and health, the
data it holds, the services it serves, its connectors, and the writes the store refused, in the tab
"System", and its log records, in the tab "Logs". The middleware library renders those. Use case 2
adds a tab for each module, which draws the module from above and shows its
routing decisions and the connections it read, from the module's memory. The test reaches the page
at the address the module's service has in the graph; the Launcher page links the same page
through the Launcher's port, under `/p/<id>/`, and every path the page asks for is relative, so it
works at both.

The parties are served in this process (`_participants`), with the same `build` their processes
use. These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import re

import httpx
import pytest
from _html import rows
from _module_boxes import drawn, matrix_of
from _participants import M1, M2, participants
from _wait import eventually
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present

from jms_kapps.uc2.discovery import service_addresses
from jms_kapps.uc2.seed import place_box
from jms_kapps.uc2.vocabulary import box_iri, local_name

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# The middleware's own boxes, by the ids its documentation gives them: five in the tab "System",
# "Refused writes" among them, and the one of the tab "Logs".
THE_MIDDLEWARE_S_BOXES = {
    "header",
    "held-data",
    "services",
    "connectors",
    "refused-writes",
    "logs",
}

# The module's own three, in the tab named after it.
THE_MODULE_S_BOXES = {"module", "adjacency-matrix", "routing-decisions"}


def box(base: str, box_id: str) -> str:
    fragment = httpx.get(f"{base}/dashboard/box/{box_id}", timeout=10)
    assert fragment.status_code == 200
    assert "failed to render" not in fragment.text, (box_id, fragment.text)
    return fragment.text


def test_each_module_process_serves_the_state_dashboard_with_a_tab_of_the_module_s_own(
    dataset,
):
    with participants(dataset) as party:
        addresses = service_addresses(party.db)
        for module in (M1, M2):
            base = addresses[module].rstrip("/")

            page = httpx.get(f"{base}/dashboard", timeout=10)

            assert page.status_code == 200
            assert page.headers["content-type"].startswith("text/html")
            boxes = set(re.findall(r'hx-get="dashboard/box/([\w-]+)"', page.text))
            assert boxes == THE_MIDDLEWARE_S_BOXES | THE_MODULE_S_BOXES
            assert f">{local_name(module)}</label>" in page.text
            for box_id in boxes:
                box(base, box_id)
            # The Instance box shows the module it runs, read from the instance itself.
            assert str(module) in box(base, "header")
            # Its transit time, which its process read from the graph at its start (jms_kapps 8).
            assert "transit time: 0 s" in box(base, "module")


def test_a_module_s_tab_shows_the_connections_from_its_start_and_each_decision_it_made(
    dataset,
):
    """Module 1 hands boxes East to Module 2. A box crosses from Module 1 to Module 2 and back to
    the WMS: Module 1 decides on Module 2, and Module 2, its destination, gives it back."""
    with participants(dataset) as party:
        addresses = service_addresses(party.db)
        first, second = (addresses[m].rstrip("/") for m in (M1, M2))
        # Read from the graph when each module started, before any decision.
        assert drawn(box(second, "module")).connections == {("West", "in", "Module1")}
        assert "Module2 has decided nothing yet." in box(second, "routing-decisions")

        answer = party.spawn(origin=str(M1), destination=str(M2))
        assert answer["accepted"], answer
        eventually(lambda: rows(box(second, "routing-decisions")) != [], timeout=60)

        [(_, *decided)] = rows(box(first, "routing-decisions"))
        box_name = local_name(answer["box"])
        # The detour column, added on 2026-10-06 (jms_kapps 43): no other way leads on.
        assert decided == [box_name, "Module2", "Module2", "—", "—", "—", "0"]
        [(_, *given_back)] = rows(box(second, "routing-decisions"))
        assert given_back == [
            box_name,
            "Module2",
            "back to the WMS",
            "—",
            "—",
            "—",
            "0",
        ]
        drawing = drawn(box(first, "module"))
        assert drawing.connections == {("East", "out", "Module2")}
        assert drawing.boxes == []
        matrix = box(first, "adjacency-matrix")
        shown = matrix_of(matrix)
        assert shown.rows == shown.columns == ["Module1", "Module2"]
        assert {pair: text for pair, text in shown.cells.items() if text} == {
            ("Module1", "Module2"): "E"
        }
        assert shown.marked == ["Module1"]
        assert f"for the decision on {box_name}." in matrix


def box_1_on_module_1(db) -> None:
    """Setup before any party starts, as after a restart: Box 1 on Module 1, its transport
    running to Module 2."""
    place_box(OGM(db=db), box_iri(1), on=M1, origin=M1, destination=M2)


def test_a_module_that_starts_while_the_graph_says_it_possesses_a_box_draws_that_box(
    dataset,
):
    with participants(dataset, before_start=box_1_on_module_1) as party:
        addresses = service_addresses(party.db)
        first, second = (addresses[m].rstrip("/") for m in (M1, M2))

        drawing = box(first, "module")
        assert drawn(drawing).boxes == ["Box1"]
        assert "Module1 holds Box1." in drawing
        assert drawn(box(second, "module")).boxes == []
