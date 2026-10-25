"""Offline tests of UC1's page: the rendering, the routes, the help texts, the block of addresses
for the terminal, the server on a bound socket, and the page-port guard of `jms-uc1`.

No GraphDB, no InfluxDB, no network except a bound local socket of the page itself. The snapshots
are hand-made `ChildSnapshot`s, with all fields. The page's stop is checked on a real `PageServer`
over a socket bound to port 0. The page in a browser: `test_uc1_page_browser.py`.
"""

from __future__ import annotations

import html
import re
import socket
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from _uc1_lists import HEAD_LITERAL, ListStore
from fastapi.testclient import TestClient
from kapps_triplestore_interface import IRI

import jms_kapps.uc1.page
import jms_kapps.uc2.launcher
from jms_kapps.common.process import bind_socket
from jms_kapps.common.supervision import ChildSnapshot, Supervisor
from jms_kapps.uc1.page import (
    SAVE_UQ_LABEL,
    PageServer,
    address_block,
    build_app,
    render,
)
from jms_kapps.uc1.page_lists import (
    LEARNED_VALUES,
    MEASURED_POSITIONS,
    OPERATIONS,
    PageLists,
)

NAMES = ["Anomaly detector", "Screwing resource", "Learner", "Planner"]


def _child(
    name: str,
    *,
    state: str = "live",
    address: str | None = None,
    resource: IRI | None = None,
    output: list[str] | None = None,
    pid: int = 1,
    has_service: bool = True,
) -> ChildSnapshot:
    """One child of the page's table, with all the fields a `ChildSnapshot` has."""
    return ChildSnapshot(
        resource=resource or IRI(f"http://example.org/test#{quote(name.replace(' ', ''), safe='')}"),
        name=name,
        state=state,
        pid=pid,
        command="python -m x",
        output=output or [],
        address=address,
        unconfirmed_for=None,
        stoppable=False,
        why_failed=None,
        has_service=has_service,
    )


def _four() -> list[ChildSnapshot]:
    return [
        _child(name, address=f"http://127.0.0.1:{9000 + i}/", pid=100 + i)
        for i, name in enumerate(NAMES)
    ]


class _Rows(HTMLParser):
    """The process rows of a rendered page: for each, its cells' text and its links."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict] = []
        self._row: dict | None = None
        self._cell = -1
        self._link: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and "data-resource" in a and "output" not in a.get("class", ""):
            self._row = {"resource": a["data-resource"], "cells": ["", "", "", ""], "links": []}
            self.rows.append(self._row)
            self._cell = -1
        elif self._row is not None and tag == "td":
            self._cell += 1
        elif self._row is not None and tag == "a":
            self._link = a["href"]
            self._row["links"].append([a["href"], ""])

    def handle_endtag(self, tag):
        if tag == "tr":
            self._row = None
        elif tag == "a":
            self._link = None

    def handle_data(self, data):
        if self._row is not None and 0 <= self._cell < 4:
            self._row["cells"][self._cell] += data
            if self._link is not None:
                self._row["links"][-1][1] += data


def _rows(page: str) -> list[dict]:
    parser = _Rows()
    parser.feed(page)
    return parser.rows


def test_render_has_one_row_per_child_in_order_with_name_state_and_pid() -> None:
    """Each child is one row, in the given order, with its name, its state and its PID."""
    children = [
        _child("Planner", state="live", pid=7, address="http://127.0.0.1:1234/"),
        _child("Detector", state="starting", pid=8),
    ]

    rows = _rows(render(children))

    assert [r["cells"][:3] for r in rows] == [
        ["Planner", "live", "7"],
        ["Detector", "starting", "8"],
    ]


def test_each_link_of_a_row_points_at_the_page_of_the_process_it_names() -> None:
    """API docs, activity, dashboard and logs, each through the page's own port under the path of
    its own row's process, relative to the page, and the logs link opens the dashboard's Logs
    tab."""
    children = _four()

    rows = _rows(render(children))

    for child, row in zip(children, rows, strict=True):
        base = f"p/{child.resource.fragment}"
        assert row["links"] == [
            [f"{base}/docs", "API docs"],
            [f"{base}/activity", "activity"],
            [f"{base}/dashboard", "dashboard"],
            [f"{base}/dashboard?tab=Logs", "logs"],
        ]
    # The same links in the state route, from the same table.
    state = TestClient(build_app(lambda: children)).get("/api/state").json()
    for child, process in zip(children, state["processes"], strict=True):
        assert [(link["label"], link["href"]) for link in process["links"]] == [
            (label, href) for href, label in _rows(render([child]))[0]["links"]
        ]


def test_a_row_has_no_links_before_its_process_has_an_address() -> None:
    (row,) = _rows(render([_child("Planner", address=None)]))

    assert row["links"] == []


def test_the_output_of_a_process_is_in_the_page_and_hidden_until_clicked() -> None:
    page = render([_child("Planner", output=["one", "two <b>"])])

    assert "<pre>one\ntwo &lt;b&gt;</pre>" in page
    assert re.search(r'<tr class="output"[^>]* hidden>', page)


def test_the_page_has_no_refresh_of_its_own_and_loads_its_script_from_its_static_folder() -> None:
    page = render(_four())

    assert "http-equiv" not in page
    assert '<script src="static/page.js"></script>' in page
    assert '<link rel="stylesheet" href="static/style.css">' in page
    assert "<script>" not in page


def test_what_to_do_is_visible_with_its_three_steps_and_names_a_dashboard_in_the_presses() -> None:
    """The section has no `details` around its steps, and each press links the dashboards where
    its result shows. The third step names the switch and the list where its result shows."""
    page = render(_four())
    section = page.split('<section id="what-to-do">')[1].split("</section>")[0]
    steps = re.findall(r"<li>(.*?)</li>", section, re.DOTALL)

    assert len(steps) == 3
    before_details = section.split("<details>")[0]
    assert before_details.count("<li>") == 3
    assert "Screw1" in steps[0] and "Screw2" in steps[1]
    assert f"<b>{SAVE_UQ_LABEL}</b>" in steps[2]
    assert 'href="#measured-positions-section"' in steps[2]
    for step in steps[:2]:
        assert 'data-dashboard-of="Planner"' in step
        assert 'data-dashboard-of="Anomaly detector"' in step
    assert 'data-dashboard-of="Learner"' in steps[0]
    assert 'href="p/Planner/dashboard"' in steps[0]  # the planner's


class _Text(HTMLParser):
    """The text of every `p.intro`, and of every `p` and `summary` of the page."""

    def __init__(self) -> None:
        super().__init__()
        self.intros: list[str] = []
        self.helps: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "p" and "intro" in dict(attrs).get("class", ""):
            self._in = "intro"
            self.intros.append("")
        elif tag in ("p", "summary"):
            self._in = "help"
            self.helps.append("")

    def handle_endtag(self, tag):
        if tag in ("p", "summary"):
            self._in = None

    def handle_data(self, data):
        if self._in == "intro":
            self.intros[-1] += data
        elif self._in == "help":
            self.helps[-1] += data


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def test_no_section_has_an_intro_of_more_than_two_sentences() -> None:
    parser = _Text()
    parser.feed(render(_four()))

    assert len(parser.intros) == 5
    for intro in parser.intros:
        assert 1 <= len(_sentences(intro)) <= 2, intro


def test_the_help_texts_name_no_reader_state_no_purpose_and_compare_nothing() -> None:
    parser = _Text()
    parser.feed(render(_four()))
    texts = " ".join(parser.intros + parser.helps).lower()

    for words in (
        r"\byou\b",
        r"\buser\b",
        r"\bperson\b",
        r"\breader\b",
        r"this page",
        r"\bno longer\b",
        r"\bnow\b",
        r"\binstead\b",
        r"\bearlier\b",
        r"\bpreviously\b",
    ):
        assert not re.search(words, texts), words


def test_render_escapes_every_value() -> None:
    """A name or a state that holds markup appears escaped, and the page holds no raw tag from
    it. An IRI cannot hold markup: the IRI class refuses it."""
    children = [_child("<b>x</b>", state='<i>"quoted"</i>', output=["<script>"])]

    page = render(children)

    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<b>x</b>" not in page
    assert "&lt;i&gt;&quot;quoted&quot;&lt;/i&gt;" in page
    assert "<i>" not in page
    assert "<script>" not in page


def test_the_planner_button_shows_with_its_address_and_hides_without() -> None:
    with_address = render([_child("Planner", address="http://127.0.0.1:1234/")])
    without = render([_child("Planner", address=None)])

    assert 'href="p/Planner/dashboard" target="_blank" rel="noopener" data-dashboard-of="Planner" class="button primary"' in with_address
    assert re.search(r"<a [^>]*data-dashboard-of=\"Planner\" class=\"button primary\"[^>]* hidden>", without)


def test_routes_answer_over_the_test_client() -> None:
    """`GET /` answers 200 with text/html; `GET /api/state` answers the keys per process,
    including the resource IRI and the service class IRI or null."""
    children = [
        _child("Planner", address="http://127.0.0.1:1234/", output=["hello"]),
        _child("Detector", address=None),
    ]
    services = {
        children[0].resource: IRI("http://example.org/test#PlannerService"),
    }
    client = TestClient(build_app(lambda: children, services))

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
        "pid",
        "command",
        "output",
        "address",
        "dashboard",
        "links",
        "unconfirmed_for",
        "resource",
        "service",
    }
    assert planner["output"] == ["hello"]
    assert planner["dashboard"] == "p/Planner/dashboard"
    assert planner["resource"] == str(children[0].resource)
    assert planner["service"] == "http://example.org/test#PlannerService"
    assert state["processes"][1]["links"] == []
    assert state["processes"][1]["service"] is None


def test_the_static_files_are_served_next_to_the_page() -> None:
    client = TestClient(build_app(list))

    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/page.js").status_code == 200
    assert client.get("/static/sfb-logo.png").content.startswith(b"\x89PNG")


class CountingReader:
    """The supervisor's reader of the store, which counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, workflows):  # type: ignore[no-untyped-def]
        self.calls += 1
        return []


def test_a_poll_of_the_page_sends_no_request_to_the_store() -> None:
    """The page reads the supervisor's snapshot, which is its memory: polling `/` and
    `/api/state` many times never calls the supervisor's reader of the store."""
    reader = CountingReader()
    supervisor = Supervisor(reader, echo=lambda line: None, stop_timeout=5.0)
    supervisor.spawn(
        IRI("http://example.org/test#Sleeper"),
        "Sleeper",
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        client = TestClient(build_app(supervisor.snapshot))
        for _ in range(5):
            assert client.get("/").status_code == 200
            (process,) = client.get("/api/state").json()["processes"]
            assert process["name"] == "Sleeper"
    finally:
        supervisor.stop_all()

    assert reader.calls == 0


ROW = re.compile(r"  (.+?)\s{2,}(http\S*|no address yet)")


def test_the_block_of_addresses_lists_the_page_and_each_dashboard_between_blank_lines() -> None:
    """Each dashboard through the page's own port, so that one forwarded port reaches each."""
    block = address_block("http://127.0.0.1:8091/", _four())

    assert block.startswith("\nPages of the run:\n")
    assert block.endswith("\n") and not block.endswith("\n\n")
    rows = {m[1].strip(): m[2] for line in block.splitlines() if (m := ROW.match(line))}
    assert rows == {
        "Use case 1 page": "http://127.0.0.1:8091/",
        "Anomaly detector dashboard": "http://127.0.0.1:8091/p/Anomalydetector/dashboard",
        "Screwing resource dashboard": "http://127.0.0.1:8091/p/Screwingresource/dashboard",
        "Learner dashboard": "http://127.0.0.1:8091/p/Learner/dashboard",
        "Planner dashboard": "http://127.0.0.1:8091/p/Planner/dashboard",
    }
    # Two columns: every address starts at the same place.
    assert len({line.index("http") for line in block.splitlines() if "http" in line}) == 1


def test_a_process_without_an_address_shows_that_in_the_block() -> None:
    block = address_block("http://127.0.0.1:8091/", [_child("Planner", address=None)])

    assert "Planner dashboard  no address yet" in block


def test_a_device_has_no_dashboard_row_in_the_block_but_says_what_it_is() -> None:
    """The robot controller stand-in speaks only MQTT: its row names no dashboard and does not
    wait for an address that never comes."""
    robot = _child("Robot controller", address=None, has_service=False)
    block = address_block(
        "http://127.0.0.1:8091/",
        [_child("Planner", address="http://127.0.0.1:1234/"), robot],
    )

    assert re.search(r"  Robot controller\s{2,}no address: a device, it speaks only MQTT$", block, re.MULTILINE)
    assert "no address yet" not in block
    assert "Robot controller dashboard" not in block


def test_the_run_prints_the_block_after_the_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` prints the block of the supervisor's four children once they are ready, set apart
    by a blank line before and after, and before the line that says the run is on."""
    from jms_kapps.uc1 import __main__ as main_module

    children = _four()

    class FakeSupervisor:
        def __init__(self, reader, service_of=None) -> None:
            pass

        def begin_stop(self) -> None:
            pass

        def wait_until_ready(self) -> None:
            pass

        def snapshot(self):
            return children

        def stop_all(self) -> None:
            pass

        def may_not_have_deregistered(self):
            return set()

    class FakeEvent:
        def __init__(self) -> None:
            self.calls = 0

        def set(self) -> None:
            pass

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            self.calls += 1
            return True

    class FakePage:
        def __init__(self, *a) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(main_module, "connect", lambda dataset: object())
    monkeypatch.setattr(main_module, "reader", lambda db: (lambda w: []))
    monkeypatch.setattr(main_module, "refuse_if_live", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(main_module, "PageServer", FakePage)
    monkeypatch.setattr(main_module, "start_run", lambda *a: None)
    monkeypatch.setattr(main_module, "endpoint_for", lambda d: type("E", (), {"url": "x"}))
    monkeypatch.setattr(main_module.threading, "Event", FakeEvent)
    monkeypatch.setattr(main_module, "bind_socket", lambda host, port: socket.socket())

    code = main_module.main(["--page-port", "8999"])

    out = capsys.readouterr().out
    assert code == 0
    block = address_block("http://127.0.0.1:8999/", children)
    assert block + "\n" in out
    assert out.index("Pages of the run:") < out.index("Use case 1 runs.")
    assert out.split("Pages of the run:")[0].endswith("\n\n")


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


# The look: the page carries use case 2's colour variables, and its logo.
UC1 = Path(jms_kapps.uc1.page.__file__).parent / "static"
LAUNCHER = Path(jms_kapps.uc2.launcher.__file__).parent / "static"


def _colour_variables(stylesheet: str) -> dict[str, str]:
    for block in re.findall(r":root\s*{([^}]*)}", stylesheet):
        found = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))
        if any(value.strip().startswith("#") for value in found.values()):
            return {name: value.strip() for name, value in found.items()}
    return {}


def test_the_stylesheet_starts_with_the_colour_variables_of_the_launcher() -> None:
    mine = (UC1 / "style.css").read_text(encoding="utf-8")
    theirs = (LAUNCHER / "style.css").read_text(encoding="utf-8")

    assert _colour_variables(mine) == _colour_variables(theirs) != {}
    # At the top: the first rule is the :root block.
    assert re.sub(r"/\*.*?\*/", "", mine, flags=re.DOTALL).lstrip().startswith(":root")


def test_the_header_carries_the_logo_and_the_logo_is_the_launchers_file() -> None:
    header = render(_four()).split("</header>")[0]

    assert 'src="static/sfb-logo.png"' in header
    assert (UC1 / "sfb-logo.png").read_bytes() == (LAUNCHER / "sfb-logo.png").read_bytes()


def test_the_page_has_a_white_background() -> None:
    stylesheet = (UC1 / "style.css").read_text(encoding="utf-8")

    assert _colour_variables(stylesheet)["--ground"].lower() in {"#fff", "#ffffff"}
    assert re.search(r"body\s*{[^}]*background:\s*var\(--ground\)", stylesheet)


def test_the_page_has_no_absolute_path_to_its_own_files() -> None:
    """Every path the page asks for is relative, so that it works under any path in front."""
    page = render(_four())

    for attribute in re.findall(r'(?:src|href)="([^"]+)"', page):
        assert not attribute.startswith("/"), attribute


# -- Operations and Learned values -------------------------------------------------------------- #

DETECTOR_DASHBOARD = "p/Anomalydetector/dashboard"


def _read_lists(children: list[ChildSnapshot]) -> tuple[ListStore, PageLists]:
    """The lists after one read, the deciding resource of each operation the first child's (the
    anomaly detector's) and issued by the fourth's (the planner's)."""
    store = ListStore(str(children[0].resource), str(children[3].resource))
    lists = PageLists(store)
    lists.read()
    return store, lists


def test_the_state_gives_each_operation_with_its_deciding_service_and_its_dashboard() -> None:
    """Both operations, each with its Screw, issuer, status, outcome and reason; the deciding
    service of both is the anomaly detector, named as its process and linked to its dashboard."""
    children = _four()
    _, lists = _read_lists(children)
    client = TestClient(build_app(lambda: children, None, lists.last))

    operations = client.get("/api/state").json()["operations"]

    assert operations["query"] == OPERATIONS
    assert operations["error"] is None
    assert [
        (r["screw"], r["issuer_name"], r["status"], r["outcome"], r["reason"])
        for r in operations["rows"]
    ] == [
        ("Screw1", "Planner", "done", "ScrewIntact", None),
        ("Screw2", "Planner", "failed", "MissingScrew", "ANOMALY: <MissingScrew>"),
    ]
    for row in operations["rows"]:
        assert row["decided_by"] == str(children[0].resource)
        assert row["decided_by_name"] == "Anomaly detector"
        assert row["decided_by_dashboard"] == DETECTOR_DASHBOARD


def test_the_state_gives_the_learned_values_with_their_query() -> None:
    children = _four()
    _, lists = _read_lists(children)
    client = TestClient(build_app(lambda: children, None, lists.last))

    learned = client.get("/api/state").json()["learned_values"]

    assert learned["query"] == LEARNED_VALUES
    assert learned["rows"] == [{"screw_type": "M4", "now": "1.8", "at_start": "1.8"}]


def test_a_resource_of_no_process_of_the_run_shows_by_its_local_name_without_a_link() -> None:
    children = _four()
    _, lists = _read_lists(children)
    client = TestClient(build_app(lambda: children[1:3], None, lists.last))

    row = client.get("/api/state").json()["operations"]["rows"][0]

    assert row["decided_by_name"] == "Anomalydetector"
    assert row["decided_by_dashboard"] is None


def test_the_page_renders_both_lists_and_each_query_exactly_as_sent_with_a_copy_button() -> None:
    """Without JavaScript the page shows the rows, the deciding service linked to its dashboard,
    and each query's text, which unescapes to exactly the text the store received."""
    children = _four()
    store, lists = _read_lists(children)

    page = render(children, lists.last())

    for name, query in (("operations", OPERATIONS), ("learned-values", LEARNED_VALUES)):
        shown = re.search(rf'<pre id="{name}-query"[^>]*>(.*?)</pre>', page, re.DOTALL)
        assert shown is not None
        assert html.unescape(shown.group(1)) == query
        assert query in store.received
        assert re.search(rf'<button[^>]*data-copies="{name}-query"[^>]*>Copy the query', page)
    decided = (
        f'<td class="decided-by"><a href="{DETECTOR_DASHBOARD}" target="_blank" '
        'rel="noopener">Anomaly detector</a></td>'
    )
    assert page.count(decided) == 2
    assert "ANOMALY: &lt;MissingScrew&gt;" in page
    assert "<td>M4</td><td>1.8</td><td>1.8</td>" in page


def test_before_the_first_read_the_lists_are_empty_and_their_copy_buttons_off() -> None:
    page = render(_four())

    for name in ("operations", "learned-values"):
        assert re.search(rf'<button[^>]*data-copies="{name}-query" disabled>', page)
        assert re.search(rf'<pre id="{name}-query"[^>]*></pre>', page)


def test_a_poll_of_the_page_sends_no_query_of_the_lists() -> None:
    """The lists come from their background read: ten polls each of `/` and `/api/state` send
    no query to the store."""
    children = _four()
    store, lists = _read_lists(children)
    sent = len(store.received)
    client = TestClient(build_app(lambda: children, None, lists.last))

    for _ in range(10):
        assert client.get("/").status_code == 200
        assert client.get("/api/state").json()["operations"]["rows"]

    assert len(store.received) == sent


def test_a_failed_read_keeps_the_last_answer_and_the_page_says_why() -> None:
    children = _four()
    store, lists = _read_lists(children)
    client = TestClient(build_app(lambda: children, None, lists.last))
    before = client.get("/api/state").json()

    store.away = "the store is away"
    lists.read()

    after = client.get("/api/state").json()
    for name in ("operations", "learned_values"):
        assert after[name]["error"] == "the store is away"
        assert after[name]["rows"] == before[name]["rows"]
        assert after[name]["read_at"] == before[name]["read_at"]
    assert re.search(
        r'<p id="operations-read" class="message error">The latest read failed: '
        r"the store is away\. The list is the one read at \d\d:\d\d:\d\d UTC\.</p>",
        client.get("/").text,
    )


def test_the_run_reads_the_lists_from_the_seed_on_and_stops_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` starts the background read of the lists only after the seed, since the dataset
    holds the previous run's values before it, and stops it at the end of the run."""
    from jms_kapps.uc1 import __main__ as main_module

    events: list[str] = []

    class FakeLists:
        def __init__(self, db) -> None:
            pass

        def last(self):
            return None

        def start(self) -> None:
            events.append("lists start")

        def stop(self) -> None:
            events.append("lists stop")

    class FakeSupervisor:
        def __init__(self, reader, service_of=None) -> None:
            pass

        def begin_stop(self) -> None:
            pass

        def wait_until_ready(self) -> None:
            pass

        def snapshot(self):
            return []

        def stop_all(self) -> None:
            pass

        def may_not_have_deregistered(self):
            return set()

    class FakePage:
        def __init__(self, *a) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    class StoppedEvent:
        def set(self) -> None:
            pass

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            return True

    monkeypatch.setattr(main_module, "connect", lambda dataset: object())
    monkeypatch.setattr(main_module, "reader", lambda db: (lambda w: []))
    monkeypatch.setattr(main_module, "refuse_if_live", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(main_module, "PageServer", FakePage)
    monkeypatch.setattr(main_module, "PageLists", FakeLists)
    monkeypatch.setattr(main_module, "run_core_seed", lambda db: events.append("seed"))
    monkeypatch.setattr(main_module, "start_run", lambda seed, *a: seed())
    monkeypatch.setattr(main_module, "endpoint_for", lambda d: type("E", (), {"url": "x"}))
    monkeypatch.setattr(main_module.threading, "Event", StoppedEvent)
    monkeypatch.setattr(main_module, "bind_socket", lambda host, port: socket.socket())

    assert main_module.main(["--page-port", "8999"]) == 0
    assert events == ["seed", "lists start", "lists stop"]


# -- Measured positions and the switch ---------------------------------------------------------- #

MODE = "p/Anomalydetector/save-measurements-as-uq"


def _section(page: str, name: str) -> str:
    return page.split(f'<section id="{name}"')[1].split("</section>")[0]


def test_the_state_gives_the_measured_positions_with_their_query() -> None:
    children = _four()
    _, lists = _read_lists(children)
    client = TestClient(build_app(lambda: children, None, lists.last))

    measured = client.get("/api/state").json()["measured_positions"]

    assert measured["query"] == MEASURED_POSITIONS
    assert measured["error"] is None
    assert measured["rows"] == [
        {
            "subject": "Screw1",
            "position": "screw-head position",
            "estimate": "12.25",
            "standard_deviation": "0.1",
            "literal": str(HEAD_LITERAL),
        }
    ]


def test_the_page_renders_measured_positions_as_its_third_list_after_the_learned_values() -> None:
    children = _four()
    store, lists = _read_lists(children)

    page = render(children, lists.last())

    assert page.index('id="learned-values-section"') < page.index(
        'id="measured-positions-section"'
    )
    section = _section(page, "measured-positions-section")
    assert '<h2 id="measured-positions-title">Measured positions</h2>' in section
    shown = re.search(r'<pre id="measured-positions-query"[^>]*>(.*?)</pre>', section, re.DOTALL)
    assert shown is not None
    assert html.unescape(shown.group(1)) == MEASURED_POSITIONS
    assert MEASURED_POSITIONS in store.received
    assert re.search(
        r'<button[^>]*data-copies="measured-positions-query"[^>]*>Copy the query', section
    )
    literal = html.escape(str(HEAD_LITERAL))
    assert (
        "<td>Screw1</td><td>screw-head position</td><td>12.25</td><td>0.1</td>"
        f'<td class="mono literal">{literal}</td>'
    ) in section


def test_the_switch_carries_its_exact_label_and_starts_off_and_disabled() -> None:
    """The page's server holds no connection to the detector, so it draws the switch off and
    disabled; `page.js` reads the mode through the passthrough and turns it on."""
    section = _section(render(_four()), "measured-positions-section")

    assert re.search(
        r'<input type="checkbox" id="save-uq" data-mode-route="'
        + re.escape(MODE)
        + r'" disabled>',
        section,
    )
    label = re.search(r'<label for="save-uq">(.*?)</label>', section)
    assert label is not None
    assert label.group(1) == SAVE_UQ_LABEL == "Save measurements as uncertainty quantifications"
    assert "checked" not in section.split('id="save-uq"')[1].split(">")[0]


def test_the_switch_has_no_route_while_the_detector_has_no_address() -> None:
    children = [_child(name) for name in NAMES]

    section = _section(render(children), "measured-positions-section")

    assert '<input type="checkbox" id="save-uq" disabled>' in section


def test_the_credit_stands_beside_the_switch_with_both_dois_as_links() -> None:
    section = _section(render(_four()), "measured-positions-section")
    credit = re.search(r'<p class="switch">.*?<span class="muted credit">(.*?)</span></p>', section, re.DOTALL)

    assert credit is not None
    assert section.index('id="save-uq"') < section.index('class="muted credit"')
    assert re.sub(r"<[^>]+>", "", credit.group(1)) == (
        "Representation after Wu et al., ProbSPARQL (ISWC 2026, "
        "doi:10.1007/978-3-032-42029-9_19) and Hoffmann et al., Representing Measurement "
        "Uncertainty by Gaussian Mixtures (Sensoren und Messsysteme 2026, "
        "doi:10.5162/sensoren2026/D3-2.1)."
    )
    links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', credit.group(1))
    assert links == [
        ("https://doi.org/10.1007/978-3-032-42029-9_19", "doi:10.1007/978-3-032-42029-9_19"),
        ("https://doi.org/10.5162/sensoren2026/D3-2.1", "doi:10.5162/sensoren2026/D3-2.1"),
    ]
    assert "The switch takes effect from the next classification." in section


def test_the_state_gives_the_route_of_the_detectors_mode_through_the_passthrough() -> None:
    children = _four()
    client = TestClient(build_app(lambda: children))

    assert client.get("/api/state").json()["mode_route"] == MODE

    alone = [c for c in children if c.name != "Anomaly detector"]
    assert TestClient(build_app(lambda: alone)).get("/api/state").json()["mode_route"] is None
