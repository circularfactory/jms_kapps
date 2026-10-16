"""Offline tests of UC1's page: the rendering, the routes, the server on a bound socket, and
the page-port guard of `jms-uc1`.

No GraphDB, no InfluxDB, no network except a bound local socket José of the page itself. The
snapshots are hand-made `ChildSnapshot`s, with all fields. The page's stop is checked on a real
`PageServer` over a socket bound to port 0.
"""

from __future__ import annotations

import socket
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import bind_socket
from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.page import PageServer, build_app, render


def _child(
    name: str,
    *,
    state: str = "live",
    address: str | None = None,
    resource: IRI | None = None,
) -> ChildSnapshot:
    """One child of the page's table, with all the fields a `ChildSnapshot` has."""
    return ChildSnapshot(
        resource=resource or IRI(f"http://example.org/test#{quote(name, safe='')}"),
        name=name,
        state=state,
        pid=1,
        command="",
        output=[],
        address=address,
        unconfirmed_for=None,
        stoppable=False,
    )


def test_render_has_one_card_per_child_in_order() -> None:
    """Each child is one card, in the given order, and the planner's call to action links to its
    dashboard."""
    children = [
        _child("Planner", address="http://127.0.0.1:1234/"),
        _child("Detector", address=None),
    ]
    services = {
        children[0].resource: IRI("http://example.org/test#PlannerService"),
        children[1].resource: IRI("http://example.org/test#DetectorService"),
    }

    page = render(children, services)

    assert page.count('<div class="card participant">') == 2
    assert page.index("Planner") < page.index("Detector")
    assert "Press <strong>Unscrew</strong>" in page
    assert "http://127.0.0.1:1234/dashboard" in page

    # The middleware line shows the service class and the address.
    assert "PlannerService" in page
    assert "http://127.0.0.1:1234/" in page


def test_render_shows_resource_and_middleware() -> None:
    """A card names the local name of its resource; a child without an address shows 'not yet
    registered', and one without a service shows '—'."""
    child = _child("Planner", resource=IRI("http://example.org/test#Planner1"))
    services = {child.resource: IRI("http://example.org/test#PlannerService")}

    page = render([child], services)

    assert "Planner1" in page
    assert "PlannerService" in page
    assert "not yet registered" in page

    orphan = _child(
        "Detector",
        resource=IRI("http://example.org/test#Detector1"),
        address="http://127.0.0.1:9999/",
    )
    page2 = render([orphan], {})
    assert '<dd class="mono">Detector1</dd>' in page2
    assert "http://127.0.0.1:9999/" in page2


def test_render_escapes_every_value() -> None:
    """A name or a state that holds markup appears escaped, and the page holds no raw tag from
    it. An IRI cannot hold markup: the IRI class refuses it."""
    children = [_child("<b>x</b>", state='<i>"quoted"</i>')]

    page = render(children)

    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<b>x</b>" not in page
    assert "&lt;i&gt;&quot;quoted&quot;&lt;/i&gt;" in page
    assert "<i>" not in page


def test_render_has_no_planner_link_without_address() -> None:
    """Without a planner address there is no Unscrew button."""
    children = [_child("Planner", address=None)]
    page = render(children)
    assert "Open the planner's dashboard</a>" not in page


def test_routes_answer_over_the_test_client() -> None:
    """`GET /` answers 200 with text/html; `GET /api/state` answers the six keys per process,
    including the resource IRI and the service class IRI or null."""
    children = [
        _child("Planner", address="http://127.0.0.1:1234/"),
        _child("Detector", address=None),
    ]
    services = {
        children[0].resource: IRI("http://example.org/test#PlannerService"),
    }
    app = build_app(lambda: children, services)
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "Use case 1" in page.text

    state = client.get("/api/state").json()
    assert [p["name"] for p in state["processes"]] == ["Planner", "Detector"]
    planner = state["processes"][0]
    assert set(planner) == {
        "name",
        "state",
        "address",
        "dashboard",
        "resource",
        "service",
    }
    assert planner["resource"] == str(children[0].resource)
    assert planner["service"] == "http://example.org/test#PlannerService"
    assert state["processes"][1]["resource"] == str(children[1].resource)
    assert state["processes"][1]["service"] is None


def test_page_server_answers_over_http_and_stops_its_thread() -> None:
    """The server on a socket bound to port 0 answers `GET /` over httpx within 10 s, and its
    thread ends after `stop()`."""
    sock = bind_socket("127.0.0.1")
    port = sock.getsockname()[1]
    server = PageServer(build_app(list), sock)
    server.start()

    try:
        response = httpx.get(f"http://127.0.0.1:{port}/", timeout=10)
        assert response.status_code == 200
        assert "Use case 1" in response.text
    finally:
        server.stop()
        sock.close()

    assert server._thread is not None and not server._thread.is_alive()


def test_main_refuses_a_taken_page_port_before_any_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """While another socket holds the page's port, `main` returns 1, its stderr says "in use",
    and `connect` is never called."""
    from jms_kapps.uc1 import __main__ as main_module

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)

    def fail_connect(*args, **kwargs):
        raise AssertionError("connect must not be called")

    monkeypatch.setattr(main_module, "connect", fail_connect)

    try:
        code = main_module.main(["--page-port", str(port)])
    finally:
        sock.close()

    assert code == 1
    assert "in use" in capsys.readouterr().err
