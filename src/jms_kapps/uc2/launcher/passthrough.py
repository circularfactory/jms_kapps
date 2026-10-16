"""The Launcher's one port: every page of every process of a run, passed through the Launcher.

A request to `/p/<id>/<path>` goes on to `<path>` at the live service of the process `<id>`, and
its answer comes back as it comes: a page, a form's post to a workflow, a dashboard box, and a stream,
which reaches the reader part by part. So a person who reaches the Launcher's port reaches every
page of the run, and over SSH, WSL or VS Code one forwarded port is enough. Every other process
takes a free port at each start, which a forward would have to follow.

**Nothing is rewritten.** Every page that a process serves asks for what it needs by a relative
path, so the same page works at the process's own address and under `/p/<id>/`. The Launcher
passes the request's path and query, its headers and its body on as they came, and the answer's
status, headers and body back, a redirect included, which the browser follows.

**Only what the supervisor holds is reached**: the address that it read when the process turned
live, as the fault switches use it. A request never reads the graph, so the pages work while the
graph is away, and an id is looked up among the processes of the current launch only.

**A stream ends at both ends.** When its reader goes away, the Launcher closes its connection to
the process, so the process's stream ends too. The streams of a run end on their own every two
seconds, and the reader connects again, so an open page holds no stop of the Launcher longer than
that.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse, StreamingResponse
from kapps_triplestore_interface import IRI
from starlette.types import Receive, Scope, Send

from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc2.vocabulary import FCI_NS

# The path under which the Launcher passes requests through to its processes, one each.
PASSTHROUGH = "p"

# The headers of one connection, which end at the Launcher and are not passed on either way, with
# each header that a `Connection` header names.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Nor these of a request: `host` names the Launcher, and the connection to the process names the
# process; the client states the length of the body it sends on.
_OF_THE_REQUEST = _HOP_BY_HOP | {"host", "content-length"}

# A process on this machine that does not take the connection within 5 s has gone, and a request
# that finds every connection to the processes taken for 10 s does not wait on. Once a process
# answers, nothing is bounded: a stream waits for its next event as long as it takes, and a
# workflow as long as it runs, as they would at the process's own address.
_TIMEOUT = httpx.Timeout(None, connect=5.0, pool=10.0)

_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def process_id(resource: IRI) -> str:
    """The id of the process of `resource` in the Launcher's paths: the resource's name in `fci:`.

    Every process of a run has its resource there, a module's under its id in the topology, which
    a topology allows only as a name that is safe in a path.
    """
    return str(resource).removeprefix(FCI_NS)


def process_path(resource: IRI) -> str:
    """Where the Launcher passes requests through to the process of `resource`: `p/<id>/`.

    Relative, so that a link reaches the process through whatever address the Launcher page was
    opened at, over SSH, WSL or a forwarded port. Every page a process serves uses relative paths
    too, so it works there as it does at the process's own address.
    """
    return f"{PASSTHROUGH}/{process_id(resource)}/"


def _passed_on(
    headers: list[tuple[bytes, bytes]], dropped: frozenset[str]
) -> list[tuple[bytes, bytes]]:
    """`headers` without the `dropped` ones and those their `Connection` header names, each name
    in lower case, as ASGI has it."""
    named = {
        token.strip().lower().decode("latin-1")
        for key, value in headers
        if key.lower() == b"connection"
        for token in value.split(b",")
    }
    return [
        (key.lower(), value)
        for key, value in headers
        if key.lower().decode("latin-1") not in dropped | named
    ]


class _Answer(StreamingResponse):
    """The process's answer, passed back as it comes. Its connection to the process is closed when
    the answer ends, and when its reader goes away first."""

    def __init__(self, upstream: httpx.Response) -> None:
        super().__init__(upstream.aiter_raw(), status_code=upstream.status_code)
        # Its length too: the body passes as the process sent it, not decoded.
        self.raw_headers = _passed_on(upstream.headers.raw, _HOP_BY_HOP)
        self._upstream = upstream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream.aclose()


class Passthrough:
    """Serves each process of the run under `/p/<id>/` on the Launcher's app.

    Args:
        process: The process whose id is given (see `process_id`), as the supervisor holds it
            now, or None where there is none. `Launcher.process` is the real one. (Changed on
            2026-10-11, jms_kapps 33: until then it raised a `KeyError` where there was none.)
    """

    def __init__(self, process: Callable[[str], ChildSnapshot | None]) -> None:
        self._process = process
        self._connections: httpx.AsyncHTTPTransport | None = None

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        """The app's lifespan: one pool of connections to the processes, while it serves.

        The pool, not a client: a client keeps the cookies that answers set and sends them with
        every later request, whoever makes it, and logs each request it sends, one line for each
        dashboard box that a page polls.
        """
        async with httpx.AsyncHTTPTransport() as connections:
            self._connections = connections
            try:
                yield
            finally:
                self._connections = None

    def mount(self, app: FastAPI) -> None:
        """Add the routes under `/p/` to `app`, whose lifespan runs `self.lifespan`."""

        @app.api_route(
            f"/{PASSTHROUGH}/{{process}}", methods=_METHODS, include_in_schema=False
        )
        async def to_its_root(request: Request, process: str) -> Response:
            # A page at `p/<id>` would resolve its relative paths one level too high.
            query = request.url.query
            return RedirectResponse(f"{process}/?{query}" if query else f"{process}/")

        @app.api_route(
            f"/{PASSTHROUGH}/{{process}}/{{path:path}}",
            methods=_METHODS,
            include_in_schema=False,
        )
        async def through(request: Request, process: str, path: str) -> Response:
            return await self._pass(request, process, path)

    async def _pass(self, request: Request, process: str, path: str) -> Response:
        child = self._process(process)
        if child is None:
            return PlainTextResponse(
                f"No process of this run is called {process!r}.", 404
            )
        if child.address is None:
            return PlainTextResponse(
                f"{child.name} has no live service to pass this request to. The Launcher "
                "page shows its state.",
                503,
            )
        if self._connections is None:
            raise RuntimeError("the passthrough serves only within its lifespan")
        upstream_request = httpx.Request(
            request.method,
            httpx.URL(child.address).copy_with(
                raw_path=_path_and_query(request, process, path)
            ),
            headers=_passed_on(request.headers.raw, _OF_THE_REQUEST),
            content=await request.body(),
            extensions={"timeout": _TIMEOUT.as_dict()},
        )
        try:
            upstream = await self._connections.handle_async_request(upstream_request)
        except httpx.TransportError as error:
            return PlainTextResponse(
                f"{child.name} did not answer at {child.address}: {error!r}", 502
            )
        return _Answer(upstream)


def _path_and_query(request: Request, process: str, path: str) -> bytes:
    """The request's path after `/p/<id>`, and its query, as they came: still encoded, so that an
    encoded `/` in a segment stays one. Encoded again from `path` only where the path came in a
    spelling of `/p/<id>/` of its own."""
    prefix = f"/{PASSTHROUGH}/{process}/".encode()
    raw = request.scope.get("raw_path") or b""
    rest = (
        raw[len(prefix) - 1 :] if raw.startswith(prefix) else quote("/" + path).encode()
    )
    query = request.scope.get("query_string") or b""
    return rest + b"?" + query if query else rest
