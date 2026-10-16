"""The page of a run of use case 1, which `jms-uc1` serves.

It shows each participant's state as the supervisor holds it, and links to the participant's
dashboard, where the work happens: Unscrew is on the planner's. The page reloads itself every
2 s, and needs no JavaScript. `/api/state` gives the same as JSON, for a test or a script.

The page is read-only. It launches nothing and stops nothing: Ctrl+C at the terminal stops the
run. It binds a fixed port, `PAGE_PORT`, so a second `jms-uc1` on the same machine is refused at
its start, before it clears anything. Use case 2's Launcher has port 8090, so both pages can be
open on one machine.

The page takes the palette and look of use case 2's Launcher, and shows one card per
participant. Each card names the participant's resource, the middleware that serves it (its
service class and address), and links to its dashboard. A participant that has no middleware
address yet shows "not yet registered".
"""

from __future__ import annotations

import asyncio
import html
import socket
import threading
from collections.abc import Callable, Mapping, Sequence

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from kapps_triplestore_interface import IRI

from jms_kapps.common.supervision import ChildSnapshot

PAGE_HOST = "127.0.0.1"
PAGE_PORT = 8091

# The palette of use case 2's Launcher: a light ground, white cards, and the same state colours.
STYLE = """
:root {
  --ink: #1c1c1c;
  --muted: #6b6b6b;
  --line: #d5d5d2;
  --paper: #fff;
  --ground: #f7f7f5;
  --live: #2e7d32;
  --starting: #b26a00;
  --failed: #c62828;
  --stopped: #8a8a8a;
  --killed: #7b1fa2;
  --link: #0b5cad;
  --accent: #1f4e8c;
}

* { box-sizing: border-box; }

body {
  font: 15px/1.5 system-ui, sans-serif;
  color: var(--ink);
  background: var(--ground);
  margin: 0;
  padding: 16px;
}

header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  max-width: 1180px;
  margin: 0 auto 8px;
}

h1 { font-size: 20px; margin: 0; }
h2 { font-size: 16px; margin: 0 0 4px; }
main { max-width: 1180px; margin: 0 auto; }

.card {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px 16px;
  overflow-x: auto;
}

.muted { color: var(--muted); font-size: 13px; }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.spacer { flex: 1; }

.cta { margin: 12px 0 16px; }

a { color: var(--link); }
.button {
  font: 13px system-ui, sans-serif;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper);
  color: var(--ink);
  padding: 3px 10px;
  cursor: pointer;
  text-decoration: none;
  display: inline-block;
}
.button:hover { background: #f0f0ee; }
.button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.button.primary:hover { background: #173c6c; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: 12px;
  margin: 0;
}

.participant { padding: 14px 16px; }
.participant h2 { display: flex; align-items: center; gap: 8px; }
.pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--line);
  color: var(--muted);
}
.pill.live { color: var(--live); border-color: var(--live); }
.pill.starting { color: var(--starting); border-color: var(--starting); }
.pill.failed { color: var(--failed); border-color: var(--failed); }
.pill.stopped { color: var(--stopped); border-color: var(--stopped); }
.pill.killed { color: var(--killed); border-color: var(--killed); }

dl { margin: 8px 0; }
dt { color: var(--muted); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; margin-top: 6px; }
dd { margin: 0; }
.dashboard { margin-top: 10px; }
"""


Snapshot = Callable[[], Sequence[ChildSnapshot]]


def dashboard_of(address: str | None) -> str | None:
    """The dashboard page of the process that serves `address`, or None without an address."""
    if address is None:
        return None
    return f"{address.rstrip('/')}/dashboard"


def _local_name(iri: IRI) -> str:
    """The fragment of an IRI, or its IRI when it has none."""
    return IRI(iri).fragment if IRI(iri).fragment else str(iri)


def _pill(state: str) -> str:
    """The state pill for `state`, coloured by its class; an unknown state gets a plain pill."""
    known = {"live", "starting", "failed", "stopped", "killed"}
    cls = f" pill {state}" if state in known else ""
    return f'<span class="pill{cls}">{html.escape(state)}</span>'


def _card(child: ChildSnapshot, service_iri: IRI | None) -> str:
    """One card for one participant: its name, its state pill, the local names of its resource
    and of its service class, the service address or "not yet registered", and a dashboard link
    when it has one. Every value goes through `html.escape`."""
    link = dashboard_of(child.address)
    if link is not None:
        dashboard = (
            f'<div class="dashboard"><a class="button" '
            f'href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">'
            "Open dashboard</a></div>"
        )
    else:
        dashboard = ""

    if service_iri is not None:
        middleware_class = _local_name(service_iri)
        middleware = (
            f'<span class="mono">{html.escape(middleware_class)}</span> — '
            f'<span class="mono">{html.escape(child.address)}</span>'
            if child.address
            else f'<span class="mono">{html.escape(middleware_class)}</span> — not yet registered'
        )
    elif child.address:
        middleware = f'<span class="mono">{html.escape(child.address)}</span>'
    else:
        middleware = "—"

    return f"""<div class="card participant">
<h2>{html.escape(child.name)} {_pill(child.state)}</h2>
<dl>
<dt>Resource</dt>
<dd class="mono">{html.escape(_local_name(child.resource))}</dd>
<dt>Middleware</dt>
<dd>{middleware}</dd>
</dl>
{dashboard}
</div>"""


def render(
    children: Sequence[ChildSnapshot],
    services: Mapping[IRI, IRI] | None = None,
) -> str:
    """One complete HTML5 page: the heading, a call to action for the planner's dashboard, and a
    responsive grid of cards, one per child in the given order. Each card shows the child's name
    and state, the local name of its resource, the local name of its middleware service class (a
    resource missing from `services` shows "—") and the service's address, or "not yet
    registered" without an address, plus a button link to its dashboard when it has one. Every
    value goes through `html.escape`. No JavaScript, no external resource."""
    services = services or {}
    cards = "\n".join(_card(child, services.get(child.resource)) for child in children)

    planner_link = ""
    for child in children:
        dashboard = dashboard_of(child.address)
        if child.name == "Planner" and dashboard is not None:
            planner_link = (
                f'<a class="button primary" href="{html.escape(dashboard, quote=True)}" '
                'target="_blank" rel="noopener">'
                f"Open the planner's dashboard</a>"
            )
            break

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Use case 1</title>
<meta http-equiv="refresh" content="2">
<style>{STYLE}</style>
</head>
<body>
<header>
<h1>Use case 1</h1>
<span class="muted">Unscrewing, anomaly detection and learning — Ctrl+C at the terminal stops the run.</span>
</header>
<main>
<p class="cta">Press <strong>Unscrew</strong> on the planner's dashboard. {planner_link}</p>
<div class="grid">{cards}</div>
</main>
</body>
</html>"""


def build_app(
    snapshot: Snapshot,
    services: Mapping[IRI, IRI] | None = None,
) -> FastAPI:
    """The app of the page: `/` renders the snapshot, `/api/state` returns it as JSON with each
    process's resource IRI and service class IRI (or null)."""
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return render(snapshot(), services)

    @app.get("/api/state")
    def state() -> dict:
        known = services or {}
        return {
            "processes": [
                {
                    "name": child.name,
                    "state": child.state,
                    "address": child.address,
                    "dashboard": dashboard_of(child.address),
                    "resource": str(child.resource),
                    "service": str(known[child.resource])
                    if child.resource in known
                    else None,
                }
                for child in snapshot()
            ]
        }

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
        server = uvicorn.Server(uvicorn.Config(app=self._app, log_level="warning"))

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
