"""The Launcher's one port: every page of every process of a run, through the Launcher.

The Launcher passes a request to `/p/<id>/<path>` on to `<path>` at the process `<id>`, and
its answer back as it comes, so that a person who reaches the Launcher's port reaches every page
of the run: over SSH, WSL or VS Code, one forwarded port is enough.

The Launcher and the process behind it are both served by uvicorn on a port of their own, as in a
run, because a stream passes through only where both ends are real servers. The process is a small
app with a page, a workflow and a stream, standing in for Module 1. Its children are processes that
only sleep, and the graph is a fake that says Module 1 is live at the small app's address.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Collection, Iterator

import httpx
import pytest
from _services import live_services
from _serving import serving
from _wait import eventually
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import bind_socket
from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import module_iri

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]
M1 = module_iri(1)


class Module1:
    """The process behind the Launcher: a page, a workflow, and a stream that sends its first
    event at once and its second only once `release` is set."""

    def __init__(self) -> None:
        self.release = threading.Event()
        # Set when the stream ends, however it ends: done, or its reader gone.
        self.stream_ended = threading.Event()
        self.app = FastAPI()

        @self.app.get("/page")
        def page(request: Request) -> dict:
            return {
                "query": request.url.query,
                "probe": request.headers.get("x-probe"),
                "cookie": request.headers.get("cookie"),
            }

        @self.app.get("/sign-in")
        def sign_in(response: Response) -> dict:
            response.set_cookie("person", "first")
            return {}

        @self.app.get("/moved")
        def moved(request: Request) -> RedirectResponse:
            return RedirectResponse(f"page?{request.url.query}")

        @self.app.post("/workflows/spawn_box/execute")
        def execute(body: dict) -> dict:
            return {"executed": body}

        @self.app.get("/stream")
        def stream() -> StreamingResponse:
            async def events():
                try:
                    yield "id: 1\ndata: first\n\n"
                    while not self.release.is_set():
                        await asyncio.sleep(0.02)
                    yield "id: 2\ndata: second\n\n"
                finally:
                    self.stream_ended.set()

            return StreamingResponse(events(), media_type="text/event-stream")


class Graph:
    """Says that each resource in `addresses` is live there, and seeds nothing."""

    def __init__(self, addresses: dict[IRI, str]) -> None:
        self.addresses = addresses

    def seed(
        self, topology: object, transit_seconds: float, *, collect_history: bool = False
    ) -> None:
        pass

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return live_services(self.addresses, workflows, self.addresses)


@pytest.fixture
def module_1() -> Module1:
    return Module1()


def nothing_listens() -> str:
    """An address on 127.0.0.1 where nothing listens: a port that was free a moment ago."""
    sock = bind_socket("127.0.0.1")
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def launcher(module_1) -> Iterator[str]:
    """The address of a Launcher that runs the cross, with Module 1 live at the small app, and
    Module 2 live at an address where nothing answers any more."""
    with serving(module_1.app) as address:
        launcher = Launcher(
            graph=Graph({M1: address, module_iri(2): nothing_listens()}),
            dataset="test-dataset",
            module_command=lambda resource: RUNS,
            wms_command=RUNS,
            monitor_command=RUNS,
            echo=lambda line: None,
            stop_timeout=5.0,
        )
        # Forced: the fake graph calls the two live from the start, before they were launched.
        launcher.launch(load_topology("cross"), name="cross", force=True)
        try:
            with serving(create_app(launcher)) as page:
                eventually(
                    lambda: (
                        sum(
                            p["state"] == "live"
                            for p in httpx.get(f"{page}/api/state").json()["processes"]
                        )
                        == 2
                    )
                )
                yield page
        finally:
            launcher.stop_all()


def test_a_request_passes_through_to_the_process_under_its_path(launcher):
    response = httpx.get(
        f"{launcher}/p/Module1/page?box=Box1", headers={"X-Probe": "through"}
    )

    assert response.status_code == 200
    assert response.json() == {"query": "box=Box1", "probe": "through", "cookie": None}


def test_a_cookie_that_the_process_sets_goes_to_the_browser_alone(launcher):
    """The Launcher keeps no cookie of its own: one person's cookie never goes on with another
    person's request."""
    signed_in = httpx.get(f"{launcher}/p/Module1/sign-in")
    assert signed_in.cookies["person"] == "first"

    assert httpx.get(f"{launcher}/p/Module1/page").json()["cookie"] is None


def test_a_header_of_the_connection_to_the_launcher_ends_there(launcher):
    """A header that the request's `Connection` header names belongs to that one connection."""
    response = httpx.get(
        f"{launcher}/p/Module1/page",
        headers={"X-Probe": "through", "Connection": "close, X-Probe"},
    )

    assert response.status_code == 200
    assert response.json()["probe"] is None


def test_a_form_posts_its_json_through_to_a_workflow(launcher):
    """As the WMS's dashboard posts a spawn."""
    response = httpx.post(
        f"{launcher}/p/Module1/workflows/spawn_box/execute",
        json={"origin": "", "destination": ""},
    )

    assert response.status_code == 200
    assert response.json() == {"executed": {"origin": "", "destination": ""}}


def test_a_stream_passes_through_as_it_comes(launcher, module_1):
    """The first event reaches the reader while the process still holds back the second: the
    Launcher passes on each part of an answer when it comes, not the answer once it ended."""
    with httpx.stream("GET", f"{launcher}/p/Module1/stream", timeout=10) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        assert next(lines) == "id: 1"
        assert next(lines) == "data: first"
        assert not module_1.stream_ended.is_set()

        module_1.release.set()

        assert [line for line in lines if line] == ["id: 2", "data: second"]


def test_a_stream_whose_reader_goes_away_ends_at_the_process(launcher, module_1):
    """A closed tab ends the stream at the process too, so no connection is left behind."""
    with httpx.stream("GET", f"{launcher}/p/Module1/stream", timeout=10) as response:
        assert next(response.iter_lines()) == "id: 1"

    assert module_1.stream_ended.wait(timeout=5)
    assert not module_1.release.is_set()


def test_the_process_s_path_without_its_slash_leads_to_it(launcher):
    """A page that asks for `static/…` must be at `p/<id>/`, or its paths miss the process."""
    response = httpx.get(f"{launcher}/p/Module1")

    assert response.status_code == 307
    assert response.headers["location"] == "Module1/"
    assert httpx.get(f"{launcher}/p/Module1?box=Box1").headers["location"] == (
        "Module1/?box=Box1"
    )


def test_a_redirect_of_the_process_reaches_the_browser_as_the_process_sent_it(launcher):
    """The browser follows it, from where it is: under `p/<id>/`. Followed by the Launcher, the
    page would be served at the address it was redirected from, and its relative paths would
    resolve from there."""
    response = httpx.get(f"{launcher}/p/Module1/moved?box=Box1")

    assert response.status_code == 307
    assert response.headers["location"] == "page?box=Box1"
    followed = httpx.get(f"{launcher}/p/Module1/moved?box=Box1", follow_redirects=True)
    assert str(followed.url) == f"{launcher}/p/Module1/page?box=Box1"
    assert followed.json()["query"] == "box=Box1"


def test_a_process_without_a_live_service_says_so(launcher):
    response = httpx.get(f"{launcher}/p/Module3/dashboard")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/plain")
    assert "Module 3 has no live service" in response.text


def test_a_process_that_does_not_answer_is_a_bad_gateway_that_says_so(launcher):
    response = httpx.get(f"{launcher}/p/Module2/dashboard")

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("text/plain")
    assert "Module 2 did not answer" in response.text


def test_a_name_no_process_has_is_not_found(launcher):
    response = httpx.get(f"{launcher}/p/Module9/dashboard")

    assert response.status_code == 404
    assert "Module9" in response.text


def test_the_launcher_s_own_page_and_api_stay_where_they_are(launcher):
    assert httpx.get(f"{launcher}/").status_code == 200
    assert httpx.get(f"{launcher}/api/topologies").status_code == 200
