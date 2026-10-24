"""The one port of use case 1: every page of a run, through the page's own port.

The page of `jms-uc1` passes a request to `/p/<id>/<path>` on to `<path>` at the process `<id>`,
with the passthrough that use case 2's Launcher uses, so that over SSH, WSL or VS Code one
forwarded port, 8091, reaches every page of the run.

The page and the service behind it are both served by uvicorn on a port of their own, as in a run,
because a stream passes through only where both ends are real servers. The service stands in for
the planner: an app with the middleware's own activity feed and the API docs by relative paths
that every process of a run has, a dashboard, and the planner's workflow. Beside it, the learner's
own middleware instance, as its process builds it, with no store behind it and never started: its
dashboard with its boxes. The anomaly detector has no address yet.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from urllib.parse import urljoin

import httpx
import pytest
from _html import attribute_values
from _serving import serving
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from kapps_semantic_middleware.activity import enable_activity_feed
from kapps_triplestore_interface import IRI
from starlette.types import ASGIApp, Receive, Scope, Send

from jms_kapps.common.api_docs import relative_api_docs
from jms_kapps.common.process import bind_socket
from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.learner_process import build as build_learner
from jms_kapps.uc1.page import PageServer, address_block, build_app
from jms_kapps.uc1.vocabulary import JMS


def _planner() -> FastAPI:
    """The stand-in for the planner's process: its middleware's activity page and stream, its API
    docs by relative paths, a dashboard whose button posts to the workflow by a relative path, and
    that workflow."""
    middleware = SimpleNamespace(app=FastAPI(title="Planner"))
    enable_activity_feed(middleware)
    app = middleware.app

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request) -> str:
        return (
            f"<p>tab {request.query_params.get('tab', 'State')}</p>"
            '<form action="workflows/unscrew/execute">Unscrew</form>'
        )

    @app.post("/workflows/unscrew/execute")
    def unscrew(body: dict) -> dict:
        logging.getLogger("kapps_semantic_middleware.middleware").info(
            "Planner: a press of Unscrew on %s", body["screw"]
        )
        return {"started": True, "screw": body["screw"]}

    relative_api_docs(app)
    return app


class _NoStore:
    """An object-graph mapper with no store behind it: the learner's middleware is never started,
    so nothing reaches a graph."""

    db = None


def _never_started(app: ASGIApp) -> ASGIApp:
    """`app` served without its lifespan, which would start the middleware against the store:
    the server's start and stop are answered, and every request goes to `app`."""

    async def served(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await app(scope, receive, send)
            return
        while (await receive())["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})

    return served


def _child(name: str, resource: IRI, address: str | None) -> ChildSnapshot:
    return ChildSnapshot(
        resource=resource,
        name=name,
        state="live" if address else "starting",
        pid=1,
        command="python -m x",
        output=[],
        address=address,
        unconfirmed_for=None,
        stoppable=True,
        why_failed=None,
    )


@pytest.fixture
def page() -> Iterator[str]:
    """The address of the page of a run, served by its `PageServer` as `jms-uc1` serves it, with
    the planner live at the stand-in and the anomaly detector without an address."""
    learner = build_learner(_NoStore(), "127.0.0.1", 8000, influxdb=None)
    relative_api_docs(learner.app)
    with (
        serving(_planner()) as planner,
        serving(_never_started(learner.app)) as learner_address,
    ):
        children = [
            _child("Anomaly detector", DETECTOR, None),
            _child("Learner", JMS.Learner1, learner_address + "/"),
            _child("Planner", JMS.Planner1, planner + "/"),
        ]
        sock = bind_socket("127.0.0.1")
        server = PageServer(build_app(lambda: children), sock)
        server.start()
        try:
            yield f"http://127.0.0.1:{sock.getsockname()[1]}/"
        finally:
            server.stop()
            sock.close()


def _links(page: str, name: str) -> dict[str, str]:
    """The links of the row of the process called `name`, by label, as the page's script reads
    them."""
    state = httpx.get(urljoin(page, "api/state"), timeout=10).json()
    (process,) = [p for p in state["processes"] if p["name"] == name]
    return {link["label"]: link["href"] for link in process["links"]}


def test_each_link_of_a_row_opens_its_page_through_the_page_s_port(page):
    links = _links(page, "Planner")

    assert set(links) == {"API docs", "activity", "dashboard", "logs"}
    for href in links.values():
        assert href.startswith("p/Planner1/")
        response = httpx.get(urljoin(page, href), timeout=10)
        assert response.status_code == 200, href

    dashboard = httpx.get(urljoin(page, links["dashboard"]), timeout=10).text
    assert "tab State" in dashboard
    assert "tab Logs" in httpx.get(urljoin(page, links["logs"]), timeout=10).text


def test_each_box_of_the_learner_s_dashboard_answers_through_the_page_s_port(page):
    """The learner's own dashboard asks for each of its boxes by a relative path, which reaches
    the box through the page's port too: the box "Learner" among them."""
    dashboard_url = urljoin(page, _links(page, "Learner")["dashboard"])
    dashboard = httpx.get(dashboard_url, timeout=10)
    assert dashboard.status_code == 200
    asked = attribute_values(dashboard.text, "hx-get")
    assert any(path.endswith("box/learner") for path in asked), asked

    for path in asked:
        box = urljoin(dashboard_url, path)
        assert box.startswith(urljoin(page, "p/Learner1/dashboard/box/")), box
        assert httpx.get(box, timeout=10).status_code == 200, box
    assert "Learner" in httpx.get(
        urljoin(dashboard_url, "dashboard/box/learner"), timeout=10
    ).text


def test_the_api_docs_load_their_description_through_the_page_s_port(page):
    docs_url = urljoin(page, _links(page, "Planner")["API docs"])

    docs = httpx.get(docs_url, timeout=10).text
    assert "'openapi.json'" in docs or '"openapi.json"' in docs

    description = httpx.get(urljoin(docs_url, "openapi.json"), timeout=10).json()
    assert description["info"]["title"] == "Planner"
    assert "/workflows/unscrew/execute" in description["paths"]


def test_the_activity_page_s_live_stream_passes_through(page):
    """A press of Unscrew, posted through the page's port as the dashboard posts it, shows on the
    activity stream, read through the page's port too."""
    activity = urljoin(page, _links(page, "Planner")["activity"])
    pressed = httpx.post(
        urljoin(page, "p/Planner1/workflows/unscrew/execute"),
        json={"screw": "Screw1"},
        timeout=10,
    )
    assert pressed.json() == {"started": True, "screw": "Screw1"}

    with httpx.stream("GET", urljoin(activity, "activity/stream"), timeout=10) as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        messages = [
            json.loads(line.removeprefix("data: ")).get("message", "")
            for line in stream.iter_lines()
            if line.startswith("data: ")
        ]
    assert any("a press of Unscrew on Screw1" in m for m in messages), messages


def test_each_dashboard_of_the_block_answers_through_the_page_s_port(page):
    state = httpx.get(urljoin(page, "api/state"), timeout=10).json()["processes"]
    children = [
        _child(p["name"], IRI(p["resource"]), p["address"]) for p in state
    ]

    block = address_block(page, children)

    planner_dashboard = urljoin(page, "p/Planner1/dashboard")
    assert f"  {planner_dashboard}\n" in block
    assert httpx.get(planner_dashboard, timeout=10).status_code == 200


def test_a_process_without_an_address_says_so_and_has_no_links(page):
    assert _links(page, "Anomaly detector") == {}

    response = httpx.get(urljoin(page, "p/AnomalyDetector1/dashboard"), timeout=10)

    assert response.status_code == 503
    assert "Anomaly detector has no live service" in response.text


def test_a_name_no_process_has_is_not_found(page):
    assert httpx.get(urljoin(page, "p/Learner9/dashboard"), timeout=10).status_code == 404


def test_the_page_s_own_routes_stay_where_they_are(page):
    assert "Use case 1" in httpx.get(page, timeout=10).text
    assert httpx.get(urljoin(page, "sequence"), timeout=10).status_code == 200
    assert httpx.get(urljoin(page, "static/style.css"), timeout=10).status_code == 200
