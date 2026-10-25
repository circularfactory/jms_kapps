"""The page of a run of use case 1, which `jms-uc1` serves.

The page says what to do, in three steps, and lists the five processes as use case 2's Launcher
lists its own: a row for each, with its state, its PID, its recent output on a click, and links
to its API docs, its activity feed, its dashboard and its dashboard's Logs tab.

**One port for the run.** The page passes every page of every process through its own port,
under `/p/<id>/`, with the passthrough that use case 2's Launcher uses (`passthrough`), and every
link of the page, relative to the page, goes there. So over SSH, WSL or VS Code, forwarding the
page's port is enough. Every process takes a free port at each start, which a forward would have
to follow. The server draws
the page whole once, so that it shows the state at load without JavaScript. The script in
`static/page.js` then asks `/api/state` every second and changes what differs, in place: the page
never reloads itself. `/api/state` gives the same as JSON, for a test or a script.

Below the processes, the page lists the run's unscrewing operations, each with the service that
decided its outcome, the learned values, and the measured positions saved as uncertainty
quantifications, each from one SPARQL query that it shows exactly as it was sent, with a copy
button, as use case 2's Launcher shows its query of the boxes. The queries run in a background
read of their own (`page_lists.PageLists`), every second.

**The switch** "Save measurements as uncertainty quantifications" (interim name), at the head of
the measured positions, shows and sets the anomaly detector's mode. The page's server holds no
connection to the detector: it draws the switch off and disabled, and `page.js` reads and sets the
mode on the detector's own route (`anomaly_detector.MODE_ROUTE`) through the passthrough, like
every other page of a process.

Everything the page shows comes from memory, the supervisor's (`Supervisor.snapshot`) and the
background read's (`PageLists.last`), so a poll sends no request to the triple store.

The page links to "The UC1 sequence", the paper's scenario told step by step with the seeded names,
which it serves itself at `/sequence` (`sequence`).

The page launches nothing and stops nothing: Ctrl+C at the terminal stops the run. What passes
through to a process, a press of Unscrew and a change of the switch included, goes to the
process's own route.
It binds a fixed port, `PAGE_PORT`, so a second `jms-uc1` on the same machine is refused at its
start, before it clears anything. Use case 2's Launcher has port 8090, so both pages can be
open on one machine.

The page takes the look of use case 2's pages: its stylesheet carries the same colour variables
with the same names and values, and its header carries the same logo. It imports nothing of use
case 2, so the stylesheet and the logo are its own copies in `static/`; the passthrough is
shared by both use cases in `jms_kapps.common`.
"""

from __future__ import annotations

import asyncio
import html
import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urljoin

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from kapps_triplestore_interface import IRI

from jms_kapps.common.passthrough import Passthrough, process_of, process_path
from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.anomaly_detector import MODE_ROUTE
from jms_kapps.uc1.page_lists import (
    LearnedValue,
    Lists,
    MeasuredPositionRow,
    OperationRow,
    ShownList,
    local_name,
)
from jms_kapps.uc1.sequence import sequence_html

PAGE_HOST = "127.0.0.1"
PAGE_PORT = 8091

STATIC = Path(__file__).parent / "static"

# The tab of the dashboard that lists a process's newest log records, with a level filter: the
# middleware library's documented public tab name.
LOGS_TAB = "Logs"

# The pages a process offers once its service has an address: the label of the link, and the path
# below the process's own (`process_path`). The one table that the rows, `/api/state` and the
# block of addresses read.
PAGES: tuple[tuple[str, str], ...] = (
    ("API docs", "docs"),
    ("activity", "activity"),
    ("dashboard", "dashboard"),
    ("logs", f"dashboard?tab={LOGS_TAB}"),
)

Snapshot = Callable[[], Sequence[ChildSnapshot]]
LastLists = Callable[[], Lists]

# What the block of addresses says of a device, a process without pages of its own: the robot
# controller stand-in, which speaks only MQTT, to the broker the screwing resource hosts.
DEVICE_ADDRESS = "no address: a device, it speaks only MQTT"

# What the copy button of a query says, until it says what became of a copy.
COPY_QUERY = "Copy the query"

# The label of the switch of the anomaly detector's mode (interim name).
SAVE_UQ_LABEL = "Save measurements as uncertainty quantifications"

# The process whose mode the switch shows and sets, by its name in the run.
DETECTOR_NAME = "Anomaly detector"

# The credit beside the switch: the representation of a measured position as an uncertainty
# quantification, and the two publications it follows, each linked at its DOI.
CREDIT = (
    "Representation after Wu et al., ProbSPARQL (ISWC 2026, {wu}) and Hoffmann et al., "
    "Representing Measurement Uncertainty by Gaussian Mixtures (Sensoren und Messsysteme 2026, "
    "{hoffmann})."
)
WU_DOI = "10.1007/978-3-032-42029-9_19"
HOFFMANN_DOI = "10.5162/sensoren2026/D3-2.1"


def dashboard_of(child: ChildSnapshot | None) -> str | None:
    """The dashboard page of `child` through the page's port, relative to the page; None without
    a child, or while it has no address."""
    if child is None or child.address is None:
        return None
    return process_path(child.resource) + "dashboard"


def mode_route_of(children: Sequence[ChildSnapshot]) -> str | None:
    """The anomaly detector's mode route through the page's port, relative to the page; None
    without the detector, or while it has no address."""
    detector = next((c for c in children if c.name == DETECTOR_NAME), None)
    if detector is None or detector.address is None:
        return None
    return process_path(detector.resource) + MODE_ROUTE.lstrip("/")


def links_of(child: ChildSnapshot) -> list[dict[str, str]]:
    """The links of the row of `child`: `PAGES`, each as its `label` and its `href` through the
    page's port, relative to the page (`process_path`). None until the process has an address,
    since nothing answers there before."""
    if child.address is None:
        return []
    base = process_path(child.resource)
    return [{"label": label, "href": base + path} for label, path in PAGES]


def address_block(page_address: str, children: Sequence[ChildSnapshot]) -> str:
    """The addresses of the run, for the terminal: the page, then the dashboard of each process
    in the order of `children` through the page's port, or "no address yet" for one without an
    address. A process without a service has no pages at all: it is a device, such as the robot
    controller stand-in, which speaks only its protocol, and its row says so. Each process takes a
    new port at each start, so its own address would not stay; these do. The block starts and ends
    with a newline, so that a blank line sets it apart from the lines around it, and the names and
    addresses stand in two columns."""

    def dashboard(child: ChildSnapshot) -> str:
        href = dashboard_of(child)
        return urljoin(page_address, href) if href else "no address yet"

    rows = [("Use case 1 page", page_address)] + [
        (f"{child.name} dashboard", dashboard(child))
        if child.has_service
        else (child.name, DEVICE_ADDRESS)
        for child in children
    ]
    width = max(len(name) for name, _ in rows)
    lines = [f"  {name.ljust(width)}  {address}" for name, address in rows]
    return "\n" + "\n".join(["Pages of the run:", *lines]) + "\n"


def _link(href: str, label: str) -> str:
    """One link that opens in a new tab, with `href` and `label` escaped."""
    return (
        f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">'
        f"{html.escape(label)}</a>"
    )


def _rows(child: ChildSnapshot) -> str:
    """The two rows of one process: its own, and the one that holds its recent output and stays
    hidden until `page.js` opens it. Every value goes through `html.escape`."""
    state = html.escape(child.state)
    age = child.unconfirmed_for
    classes = f"{state} unconfirmed" if age is not None else state
    title = (
        f' title="Last confirmed against the knowledge graph {round(age)} s ago."'
        if age is not None
        else ""
    )
    links = "".join(_link(link["href"], link["label"]) for link in links_of(child))
    resource = html.escape(str(child.resource), quote=True)
    output = html.escape("\n".join(child.output)) if child.output else "(no output yet)"
    return (
        f'<tr class="{classes}" data-resource="{resource}">'
        f'<td><span class="dot"></span><button type="button" class="link">'
        f"{html.escape(child.name)}</button></td>"
        f'<td class="state"{title}>{state}</td>'
        f'<td class="mono pid">{child.pid}</td>'
        f'<td class="links">{links}</td></tr>\n'
        f'<tr class="output" data-resource="{resource}" hidden><td colspan="4">'
        f'<div class="muted mono command">{html.escape(child.command)}</div>'
        f"<pre>{output}</pre></td></tr>"
    )


def _dashboard_link(
    children: Sequence[ChildSnapshot], name: str, text: str, *, button: bool = False
) -> str:
    """The link to the dashboard of the process called `name`, with `text` as its words. As a
    `button` it is the header's primary button, hidden until the process has an address. Without
    an address the link has no `href`, and `page.js` gives it one once the process has one."""
    child = next((c for c in children if c.name == name), None)
    href = dashboard_of(child)
    target = f' href="{html.escape(href, quote=True)}"' if href else ""
    extra = ""
    if button:
        hidden = "" if href else " hidden"
        extra = f' class="button primary" data-hide-without-address{hidden}'
    return (
        f'<a{target} target="_blank" rel="noopener" data-dashboard-of="{html.escape(name)}"'
        f"{extra}>{html.escape(text)}</a>"
    )


def _process_with_resource(children: Sequence[ChildSnapshot], iri: str | None) -> ChildSnapshot | None:
    """The process of the run whose resource is `iri`, or None."""
    return next((c for c in children if str(c.resource) == iri), None)


def _named_resource(
    children: Sequence[ChildSnapshot], iri: str | None
) -> tuple[str | None, str | None]:
    """A resource of an operation, as a cell shows it: the name of the process whose resource it
    is, else its local name; and that process's dashboard, or None."""
    if iri is None:
        return None, None
    process = _process_with_resource(children, iri)
    if process is None:
        return local_name(iri), None
    return process.name, dashboard_of(process)


def operation_json(row: OperationRow, children: Sequence[ChildSnapshot]) -> dict:
    """One row of Operations as `/api/state` gives it: the row as the query answered it, with
    the names of its issuer and of its deciding resource, and the deciding one's dashboard."""
    issuer_name, _ = _named_resource(children, row.issuer)
    decider_name, decider_dashboard = _named_resource(children, row.decided_by)
    return row.model_dump() | {
        "issuer_name": issuer_name,
        "decided_by_name": decider_name,
        "decided_by_dashboard": decider_dashboard,
    }


def _list_json(shown: ShownList, rows: list[dict]) -> dict:
    """One list as `/api/state` gives it: its `rows`, the query as it was sent, when it was read,
    and why the latest read failed."""
    return {
        "query": shown.query,
        "rows": rows,
        "read_at": shown.read_at,
        "error": shown.error,
    }


def lists_json(lists: Lists, children: Sequence[ChildSnapshot]) -> dict:
    """Operations, Learned values and Measured positions as `/api/state` gives them."""
    return {
        "operations": _list_json(
            lists.operations,
            [operation_json(row, children) for row in lists.operations.rows],
        ),
        "learned_values": _list_json(
            lists.learned_values, [row.model_dump() for row in lists.learned_values.rows]
        ),
        "measured_positions": _list_json(
            lists.measured_positions,
            [row.model_dump() for row in lists.measured_positions.rows],
        ),
    }


def _cell(value: str | None, classes: str = "") -> str:
    """One cell with `value` escaped, empty for None."""
    attribute = f' class="{classes}"' if classes else ""
    return f"<td{attribute}>{html.escape(value or '')}</td>"


def _operation_row(row: dict) -> str:
    """One row of Operations, from its JSON (`operation_json`). The deciding service's cell links
    to its dashboard once it has one."""
    decider = row["decided_by_name"]
    if decider is not None and row["decided_by_dashboard"] is not None:
        decided = f'<td class="decided-by">{_link(row["decided_by_dashboard"], decider)}</td>'
    else:
        decided = _cell(decider, "decided-by")
    cells = "".join(
        _cell(row[key])
        for key in ("operation", "screw", "issuer_name", "status", "outcome", "reason")
    )
    return f"<tr>{cells}{decided}</tr>"


def _learned_row(row: LearnedValue) -> str:
    return f"<tr>{_cell(row.screw_type)}{_cell(row.at_start)}{_cell(row.now)}</tr>"


def _measured_row(row: MeasuredPositionRow) -> str:
    """One row of Measured positions, the literal in a cell of its own that may break anywhere."""
    cells = "".join(
        _cell(value)
        for value in (row.subject, row.position, row.estimate, row.standard_deviation)
    )
    return f"<tr>{cells}{_cell(row.literal, 'mono literal')}</tr>"


def _doi_link(doi: str) -> str:
    """A link to `doi` at doi.org, written as `doi:<doi>`."""
    return _link(f"https://doi.org/{doi}", f"doi:{doi}")


def _switch(children: Sequence[ChildSnapshot]) -> str:
    """The switch of the detector's mode, off and disabled, with the detector's mode route
    through the passthrough once the detector has an address; and the credit beside it."""
    route = mode_route_of(children)
    data = f' data-mode-route="{html.escape(route, quote=True)}"' if route else ""
    credit = CREDIT.format(wu=_doi_link(WU_DOI), hoffmann=_doi_link(HOFFMANN_DOI))
    return (
        f'<p class="switch"><input type="checkbox" id="save-uq"{data} disabled> '
        f'<label for="save-uq">{html.escape(SAVE_UQ_LABEL)}</label> '
        f'<span class="muted credit">{credit}</span></p>'
    )


def _read_line(shown: ShownList, one: str, many: str) -> tuple[str, str]:
    """What the line under a list says of its latest read, and the line's class: when it was read
    and how many rows it found, `one` or `many` of them, or why the latest read failed. The time
    is UTC here; `page.js` writes it in the browser's own time."""
    when = f"{shown.read_at[11:19]} UTC" if shown.read_at else None
    count = len(shown.rows)
    text = f"Read at {when}: {count} {one if count == 1 else many}." if when else ""
    if shown.error is None:
        return text, "muted"
    kept = f" The list is the one read at {when}." if when else ""
    return f"The latest read failed: {shown.error}.{kept}", "message error"


def _query_block(name: str, shown: ShownList, one: str, many: str) -> str:
    """Under the table of the list `name`: the line on its latest read, the head of its query
    with the copy button, and the query's text exactly as it was sent."""
    text, classes = _read_line(shown, one, many)
    disabled = "" if shown.query else " disabled"
    return (
        f'<p id="{name}-read" class="{classes}">{html.escape(text)}</p>\n'
        f'<div class="query-head"><b id="{name}-query-title">The query</b>'
        f'<button type="button" class="copy" data-copies="{name}-query"{disabled}>'
        f"{COPY_QUERY}</button></div>\n"
        f'<pre id="{name}-query" class="mono query-text" '
        f'aria-labelledby="{name}-query-title">{html.escape(shown.query or "")}</pre>'
    )


def render(children: Sequence[ChildSnapshot], lists: Lists | None = None) -> str:
    """One complete HTML5 page: the header, the section "What to do" with its three steps, the
    section "Processes" with the rows of `children` in the given order, and the sections
    "Operations", "Learned values" and "Measured positions" with the rows and the queries of
    `lists`, empty without, the last with the switch of the detector's mode and its credit.
    The stylesheet and the script are `static/style.css` and `static/page.js`, by relative paths,
    so that the page works under any path it is served at. Every value goes through
    `html.escape`."""
    rows = "\n".join(_rows(child) for child in children)
    shown = lists or Lists()
    operation_rows = "\n".join(
        _operation_row(operation_json(row, children)) for row in shown.operations.rows
    )
    operations_query = _query_block(
        "operations", shown.operations, "operation", "operations"
    )
    learned_rows = "\n".join(_learned_row(row) for row in shown.learned_values.rows)
    learned_query = _query_block(
        "learned-values", shown.learned_values, "Screw type", "Screw types"
    )
    measured_rows = "\n".join(_measured_row(row) for row in shown.measured_positions.rows)
    measured_query = _query_block(
        "measured-positions", shown.measured_positions, "position", "positions"
    )
    switch = _switch(children)
    planner = _dashboard_link(children, "Planner", "the planner's dashboard")
    detector = _dashboard_link(children, DETECTOR_NAME, "the anomaly detector's dashboard")
    learner = _dashboard_link(children, "Learner", "the learner's dashboard")
    # Hidden while the planner has no address: nothing to open yet.
    planner_button = _dashboard_link(
        children, "Planner", "Open the planner's dashboard", button=True
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Use case 1</title>
<link rel="stylesheet" href="static/style.css">
</head>
<body>
<header>
<img class="logo" src="static/sfb-logo.png" alt="SFB 1574">
<h1>Use case 1 — unscrewing, detection and learning</h1>
<span class="muted">Ctrl+C at the terminal stops the run.</span>
<span class="spacer"></span>
{planner_button}
<a class="button" href="sequence">Sequence</a>
</header>
<main>
<p id="status" class="message warning" role="status" hidden>The page's server does not answer. What is shown is from its last answer.</p>

<section id="what-to-do">
<h2>What to do</h2>
<p class="muted intro">Two presses of Unscrew run the whole loop, first on an intact screw and then on a missing one.</p>
<ol>
<li>On {planner}, press Unscrew on Screw1. The screwing resource replays a good attempt, the anomaly detector decides ScrewIntact, and the operation ends <code>done</code>. The learner then commits a new maximum dynamic torque for the screw type M4.
<span class="result">The result shows on {planner} (done), on {detector} (ScrewIntact) and on {learner} (the new value).</span></li>
<li>Press Unscrew on Screw2. The anomaly detector decides MissingScrew with the learned value, and the operation ends <code>failed</code>. The learner learns only from ScrewIntact attempts, so Screw2 changes no value.
<span class="result">The result shows on {planner} (failed) and on {detector} (MissingScrew).</span></li>
<li>Optional, before step 1: turn on <b>{SAVE_UQ_LABEL}</b> under <a href="#measured-positions-section">Measured positions</a>. Steps 1 and 2 then also save Screw1's head position and the failed operation's approach end position, each as a random variable with its literal.
<span class="result">The result shows under <a href="#measured-positions-section">Measured positions</a>.</span></li>
</ol>
<details>
<summary>Where the attempts come from</summary>
<p>The seed gives each screw a recording to replay: a good attempt for Screw1 and one with no screw in it for Screw2. The <a href="sequence">sequence</a> tells the scenario step by step with the seeded names.</p>
</details>
</section>

<section id="processes-section">
<h2>Processes</h2>
<p class="muted intro">One row for each of the five processes of the run; the robot controller is a device that speaks only MQTT, so it has no pages. Click a name for its recent output.</p>
<table id="processes">
<thead>
<tr><th>Process</th><th>State</th><th>PID</th><th>Links</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
<details>
<summary>What the columns show</summary>
<p>A process is live while its service carries a fresh heartbeat in the knowledge graph. The links open the process's own pages, each through the port of the page: its API docs, its activity feed, its dashboard and the Logs tab of that dashboard. The state comes from the supervisor's memory, so a poll sends nothing to the triple store.</p>
</details>
</section>

<section id="operations-section" aria-labelledby="operations-title">
<h2 id="operations-title">Operations</h2>
<p class="muted intro">Each unscrewing operation of the run, with the service that decided its outcome. The query below finds them, and runs unchanged in the triple store's workbench.</p>
<table id="operations">
<thead>
<tr><th>Operation</th><th>Screw</th><th>Issued by</th><th>Status</th><th>Outcome</th><th>Reason</th><th>Deciding service</th></tr>
</thead>
<tbody>
{operation_rows}
</tbody>
</table>
{operations_query}
<details>
<summary>What the columns show</summary>
<p>The service that decided an outcome is the agent of the classification that generated the values the operation used: the anomaly detector commits that chain with each decision. An operation that was never classified has no outcome and no deciding service, and its reason says why it failed. The query runs every second in the background, so a poll sends nothing to the triple store.</p>
</details>
</section>

<section id="learned-values-section" aria-labelledby="learned-values-title">
<h2 id="learned-values-title">Learned values</h2>
<p class="muted intro">Each Screw type's maximum dynamic torque, the threshold that the learner refines, at the start of the run and at the latest read.</p>
<table id="learned-values">
<thead>
<tr><th>Screw type</th><th>At the start</th><th>Latest</th></tr>
</thead>
<tbody>
{learned_rows}
</tbody>
</table>
{learned_query}
<details>
<summary>What the columns show</summary>
<p>The value at the start is the one that the first read after the seed found. The latest value is the one that the query answers at the latest read; the learner raises it after an intact screw.</p>
</details>
</section>

<section id="measured-positions-section" aria-labelledby="measured-positions-title">
<h2 id="measured-positions-title">Measured positions</h2>
<p class="muted intro">Each measured position that the anomaly detector saved as an uncertainty quantification, with its best estimate, its standard deviation and its literal. The query below finds them, and runs unchanged in the triple store's workbench.</p>
{switch}
<p class="muted">The switch takes effect from the next classification.</p>
<table id="measured-positions">
<thead>
<tr><th>Screw or operation</th><th>Position</th><th>Best estimate (mm)</th><th>Standard deviation (mm)</th><th>Literal</th></tr>
</thead>
<tbody>
{measured_rows}
</tbody>
</table>
{measured_query}
<details>
<summary>What the columns show</summary>
<p>The best estimate is the measured value. The literal is the Gaussian mixture of the position's random variable, as the store returns it: one component, with the measured value as its mean and the square of the robot's standard deviation as its variance. The standard deviation is the square root of that variance.</p>
</details>
</section>
</main>
<script src="static/page.js"></script>
</body>
</html>"""


def _process_json(child: ChildSnapshot, services: Mapping[IRI, IRI]) -> dict:
    """One process as `/api/state` gives it."""
    return {
        "name": child.name,
        "state": child.state,
        "pid": child.pid,
        "command": child.command,
        "output": list(child.output),
        "address": child.address,
        "dashboard": dashboard_of(child),
        "links": links_of(child),
        "unconfirmed_for": child.unconfirmed_for,
        "resource": str(child.resource),
        "service": str(services[child.resource]) if child.resource in services else None,
    }


def build_app(
    snapshot: Snapshot,
    services: Mapping[IRI, IRI] | None = None,
    lists: LastLists | None = None,
) -> FastAPI:
    """The app of the page: `/` renders the snapshot, `/sequence` serves the page "The UC1
    sequence", `/static` serves the stylesheet, the script and the logo, `/api/state` returns
    the snapshot as JSON: for each process its name, state, PID, command line, recent output,
    address, dashboard, links, how long its state has been unconfirmed (or null), resource IRI
    and service class IRI (or null), and `/p/<id>/` passes every page of each process through
    (`passthrough`), the process found in the snapshot by its id (`process_of`). No route reads
    the triple store: each calls `snapshot` alone. The passthrough's pool of connections lives as
    long as the app's lifespan, which `PageServer` runs.

    With `lists`, the latest answers of the background read (`PageLists.last`), `/api/state` also
    gives Operations, Learned values and Measured positions (`lists_json`) and `/` renders them;
    without, all three are empty, as before the first read. Neither route sends a query: each
    calls `lists` alone. `/api/state` also gives `mode_route`, the detector's mode route through
    the passthrough (`mode_route_of`), which `page.js` reads and sets."""
    known = services or {}
    last = lists or Lists

    passthrough = Passthrough(lambda id: process_of(snapshot(), id))
    app = FastAPI(lifespan=passthrough.lifespan)
    passthrough.mount(app)

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return render(snapshot(), last())

    @app.get("/sequence", response_class=HTMLResponse)
    def sequence() -> str:
        return sequence_html()

    @app.get("/api/state")
    def state() -> dict:
        children = snapshot()
        return {
            "processes": [_process_json(child, known) for child in children],
            "mode_route": mode_route_of(children),
            **lists_json(last(), children),
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


class PageServer:
    """Serves the app on a socket the caller bound, on a daemon thread. Its stop sets uvicorn's
    `should_exit` and joins the thread."""

    def __init__(self, app: FastAPI, sock: socket.socket) -> None:
        self._app = app
        self._sock = sock
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Run uvicorn's server on a daemon thread, over the socket the caller bound. Off the
        main thread uvicorn sets no signal handler (uvicorn 0.53 `capture_signals` returns at
        once there), so the stop signals stay the caller's — `one_stop`'s in `jms-uc1`."""
        # The lifespan "on": the passthrough's pool of connections opens there, and a start that
        # fails to open it fails the server rather than serving pages that cannot pass through.
        server = uvicorn.Server(
            uvicorn.Config(app=self._app, log_level="warning", lifespan="on")
        )

        def run() -> None:
            asyncio.run(server.serve(sockets=[self._sock]))

        self._server = server
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the server to stop and wait for the thread to end."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
