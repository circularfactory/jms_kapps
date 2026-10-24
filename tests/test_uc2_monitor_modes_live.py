"""The monitor's two modes in a real run: the real `jms-uc2` on the cross, and the monitor's page in a
headless browser, opened through the Launcher's port as a person opens it.

It starts and stops `jms-uc2` with the helpers of `test_uc2_running_system.py`, against the
dataset `--dataset` names, which it clears. It imports them from that test module, unlike
the helpers in `tests/_*.py`, so that this change leaves the running system's test as it is: if a
helper there is renamed, this test breaks at its import, and moving them into a helper module of
their own is a refactor of its own. It arms `complete-without-releasing` on Module 5
through the Launcher, and spawns Box A from Module 1 toward Module 5 through the WMS's spawn
workflow: Module 5 commits the transport `done` before it gives the box back, the store refuses
that, and Module 5 emits the refusal on its handover event stream. Then it spawns Box B from Module
2 toward Module 4, and on the page:

- In "graph + streams" the Refusals view shows Module 5's refusal, and Box B, on its way, has a
  disc on its destination.
- In "graph only" the Refusals view says why it is empty, and the query for the streams is no
  longer sent: its last time stays. Every other view still comes from the graph: Box B comes back
  to the WMS meanwhile, and the Handovers view shows it.
- The interval set on the page applies: the monitor reads the graph every 2 s.
- Back in "graph + streams" the query for the streams goes again, and the refusal shows again.

A screenshot of the page in each mode goes into the test's own temporary directory, whose path it
prints: the evidence of a real run for a person. Ctrl+C then stops the run cleanly.

This test clears the dataset it runs against. It runs only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default), and needs
the Launcher's port 8090 free and a browser.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import pytest
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from playwright.sync_api import Page, expect
from test_uc2_running_system import (
    CROSS_PROCESSES,
    arm,
    assert_monitor_mode,
    back_in_the_wms,
    monitor_state,
    running,
    spawn_until_accepted,
    through_the_launcher,
)

from jms_kapps.common.services import live_resources
from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.vocabulary import WMS, local_name, module_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.browser,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
    pytest.mark.browser_context_args(viewport={"width": 1400, "height": 900}),
]

M1, M2, M4, M5 = (module_iri(n) for n in (1, 2, 4, 5))

# A box's journey over two hops of a few seconds each, on the shared KIT store.
JOURNEY_SECONDS = 120.0

# The monitor finds the streams every 5 s: a query for them that has not gone for longer than
# this is not sent.
QUIET_SECONDS = 7.0

# Long enough for a discovery under way at the switch to have kept its query.
SETTLE_SECONDS = 2.0

# Every process stops within the stop timeout, in parallel, and the Launcher after them.
STOPPED_WITHIN_SECONDS = 60.0


def query(monitor: str, read: str) -> dict:
    """The query the read `read` sent last, as the monitor shows it."""
    (sent,) = [q for q in monitor_state(monitor)["queries"] if q["read"] == read]
    return sent


def boxes_sent(monitor: str, seconds: float) -> list[datetime]:
    """Each time the query for the boxes went, watched for `seconds`."""
    seen: list[str] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sent = query(monitor, "boxes")["sent"]
        if not seen or seen[-1] != sent:
            seen.append(sent)
        time.sleep(0.1)
    return [datetime.fromisoformat(sent) for sent in seen]


def test_the_monitor_page_switches_between_graph_and_streams_and_graph_only_in_a_real_run(
    page: Page, dataset, tmp_path: Path
):
    with running(dataset, "cross", CROSS_PROCESSES) as run:
        state = run.wait_until_live()
        wms = through_the_launcher(state, "wms")
        monitor = through_the_launcher(state, "monitor")
        # The Launcher starts the monitor in "graph only": the page shows it, and
        # the switch at Refusals turns it on.
        assert_monitor_mode(monitor, "graph only")
        page.goto(f"{monitor}/")
        refusals = page.locator("#refusals article.refusal")
        follow = page.get_by_role(
            "checkbox", name="Follow the services' handover event streams"
        )
        expect(follow).not_to_be_checked()
        follow.check()
        expect(follow).to_be_checked()

        # Box A: Module 5 refuses to complete it without releasing it.
        arm(M5, "complete-without-releasing")
        opened: dict = {}
        box_a = spawn_until_accepted(wms, M1, M5, opened, threading.Event())
        assert box_a is not None
        expect(refusals).to_have_count(1, timeout=JOURNEY_SECONDS * 1000)
        expect(refusals.first).to_contain_text(box_a)
        eventually(lambda: back_in_the_wms(monitor, [box_a]), timeout=JOURNEY_SECONDS)

        # Box B, on its way while the page is shot in each mode.
        box_b = spawn_until_accepted(wms, M2, M4, opened, threading.Event())
        assert box_b is not None
        destination = page.locator("#topology g.destination")
        expect(destination).to_have_count(1, timeout=JOURNEY_SECONDS * 1000)
        expect(destination.locator("title")).to_contain_text(
            f"{box_b} goes to Module 4"
        )
        page.screenshot(path=tmp_path / "1-graph-and-streams.png", full_page=True)

        follow.uncheck()

        eventually(lambda: monitor_state(monitor)["reading"]["mode"] == "graph only")
        switched_at = time.monotonic()
        expect(refusals).to_have_count(0)
        expect(page.locator("#refusals")).to_contain_text(
            "a refused write leaves no trace in the knowledge graph"
        )
        streams = page.locator('#queries article.query[data-read="streams"]')
        expect(streams.locator(".every")).to_have_text("not sent in graph only")
        interval = page.get_by_label("Read the knowledge graph every")
        interval.fill("2")
        interval.press("Enter")
        eventually(lambda: monitor_state(monitor)["reading"]["graph_seconds"] == 2.0)
        expect(
            page.locator('#queries article.query[data-read="boxes"] .every')
        ).to_have_text("every 2 s")
        page.screenshot(path=tmp_path / "2-graph-only.png", full_page=True)

        # The graph still shows everything but the refusals: Box B comes back to the WMS, and
        # the Handovers view shows it, from the WMS, which the graph names by its class, onto
        # Module 2, and from Module 4 back to the WMS, all from the graph.
        time.sleep(max(0.0, SETTLE_SECONDS - (time.monotonic() - switched_at)))
        streams_last_sent = query(monitor, "streams")["sent"]
        eventually(lambda: back_in_the_wms(monitor, [box_b]), timeout=JOURNEY_SECONDS)
        handovers = page.locator("#handovers tbody")
        expect(handovers).to_contain_text(f"{local_name(WMS)} → Module2")
        expect(handovers).to_contain_text(f"Module4 → {local_name(WMS)}")
        sent = boxes_sent(monitor, 7.0)
        gaps = [(b - a).total_seconds() for a, b in pairwise(sent)]
        assert len(gaps) >= 2 and min(gaps) >= 1.9, sent
        time.sleep(max(0.0, QUIET_SECONDS - (time.monotonic() - switched_at)))
        assert query(monitor, "streams")["sent"] == streams_last_sent
        page.screenshot(path=tmp_path / "3-graph-only-box-back.png", full_page=True)

        follow.check()

        eventually(
            lambda: query(monitor, "streams")["sent"] != streams_last_sent, timeout=10
        )
        expect(refusals).to_have_count(1)
        expect(streams.locator(".every")).to_have_text("every 5 s")
        page.screenshot(path=tmp_path / "4-graph-and-streams-again.png", full_page=True)
        print(f"screenshots in {tmp_path}")

        # Ctrl+C: jms-uc2 stops every process, and no service is left live.
        run.ctrl_c()
        assert run.process.wait(timeout=STOPPED_WITHIN_SECONDS) == 0, run.tail()
    assert live_resources(connect(dataset)) == set()
