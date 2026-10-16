"""A stand-in of GraphDB's HTTP API on this machine's loopback, for the observation's tests: the
login, the list of repositories, and SPARQL queries and updates on one repository, which rdflib
answers in memory. No GraphDB, no container, no network beyond the loopback.

It words its refusals as GraphDB does, each as GraphDB answered it on a recorded occasion:

- **A SHACL refusal** of an update: HTTP 500 with the media type
  `application/shacl-validation-report+n-quads;charset=UTF-8` and the validation report as its
  body. GraphDB 10.8.1's answer to an update that a `sh:maxCount 1` shape refused, as the
  triple-store interface's own tests record it byte for byte (`test_shacl_validation_error.py`
  of kapps_triplestore_interface).
- **A refusal for want of heap** of a query: HTTP 500 with the text "Insufficient free Heap Memory
  249Mb for group by and distinct". GraphDB's answer to a startup query of a module of the
  5-module cross on jms_kapps' own stack, measured on 2026-10-02 (the startup-failure report of
  the middleware, KSM 326), when its free heap was under its floor of 250 MB.

A test chooses which request the stand-in refuses, or answers late, by a rule on the request's
text. rdflib does not know GraphDB's own graphs, so the stand-in reads them as GraphDB does before
rdflib sees the text: the pseudo-graph of the explicit statements, which the interface's query
builder names for a read without inference, is the whole data, since nothing is inferred here, and
`<http://rdf4j.org/schema/rdf4j#nil>` is the default graph.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

from rdflib import Dataset

TOKEN = "GDB fake-store-token"

# GraphDB 10.8.1's answer to an update that a `sh:maxCount 1` shape refused.
SHACL_MEDIA_TYPE = "application/shacl-validation-report+n-quads;charset=UTF-8"
SHACL_REPORT = """\
_:987cde96ac59436c9b9161a1bcf448af1236 <http://www.w3.org/ns/shacl#conforms> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .
_:987cde96ac59436c9b9161a1bcf448af1236 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#ValidationReport> .
_:987cde96ac59436c9b9161a1bcf448af1236 <http://rdf4j.org/schema/rdf4j#truncated> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .
_:987cde96ac59436c9b9161a1bcf448af1236 <http://www.w3.org/ns/shacl#result> _:987cde96ac59436c9b9161a1bcf448af1237 .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#ValidationResult> .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://www.w3.org/ns/shacl#focusNode> <http://example.org/shacl-refusal#thing> .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://rdf4j.org/shacl-extensions#shapesGraph> <http://rdf4j.org/schema/rdf4j#SHACLShapeGraph> .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://www.w3.org/ns/shacl#resultPath> <http://example.org/shacl-refusal#part> .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://www.w3.org/ns/shacl#sourceConstraintComponent> <http://www.w3.org/ns/shacl#MaxCountConstraintComponent> .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://www.w3.org/ns/shacl#resultSeverity> <http://www.w3.org/ns/shacl#Violation> .
<http://example.org/shacl-refusal#ThingPartShape> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/ns/shacl#PropertyShape> .
_:987cde96ac59436c9b9161a1bcf448af1237 <http://www.w3.org/ns/shacl#sourceShape> <http://example.org/shacl-refusal#ThingPartShape> .
<http://example.org/shacl-refusal#ThingPartShape> <http://www.w3.org/ns/shacl#path> <http://example.org/shacl-refusal#part> .
<http://example.org/shacl-refusal#ThingPartShape> <http://www.w3.org/ns/shacl#maxCount> "1"^^<http://www.w3.org/2001/XMLSchema#integer> .
"""

# GraphDB's answer to a DISTINCT or GROUP BY while its free heap is under its floor.
HEAP_MEDIA_TYPE = "text/plain;charset=UTF-8"
HEAP_REFUSAL = "Insufficient free Heap Memory 249Mb for group by and distinct"

# RDF 1.1 makes a plain literal an `xsd:string`, and GraphDB matches the two as one; rdflib keeps
# them apart, so the stand-in writes each `xsd:string` plain.
_STRING = re.compile(
    r'("(?:[^"\\]|\\.)*")\^\^<http://www\.w3\.org/2001/XMLSchema#string>'
)
_EXPLICIT = re.compile(r"FROM\s+<http://www\.ontotext\.com/(?:explicit|implicit)>\s*")
_NIL = "<http://rdf4j.org/schema/rdf4j#nil>"
_NIL_BLOCK = re.compile(
    r"GRAPH\s+<http://rdf4j\.org/schema/rdf4j#nil>\s*\{\n(.*?)\n\s*\}", re.DOTALL
)

Rule = Callable[[str], bool]


def never(_: str) -> bool:
    return False


class FakeStore:
    """GraphDB's HTTP API for one repository, on the loopback, while it is entered.

    Args:
        dataset: The repository's name.
        data: Turtle that the repository holds from the start.
        refuse_update: Which updates it refuses as a SHACL validation does, by their text.
        refuse_for_heap: Which queries it refuses for want of heap, by their text.
        answer_late: Which requests it answers only after `late_seconds`, by their text.
        late_seconds: How late it answers those.
    """

    def __init__(
        self,
        dataset: str = "eval-fake",
        *,
        data: str = "",
        refuse_update: Rule = never,
        refuse_for_heap: Rule = never,
        answer_late: Rule = never,
        late_seconds: float = 3.0,
    ) -> None:
        self.dataset = dataset
        self.graph = Dataset(default_union=True)
        if data:
            self.graph.default_context.parse(data=data, format="turtle")
        self.refuse_update = refuse_update
        self.refuse_for_heap = refuse_for_heap
        self.answer_late = answer_late
        self.late_seconds = late_seconds
        self.requests: list[tuple[str, str, str]] = []
        self.logins: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "the store answers only while it is entered"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> Self:
        store = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # The status line, the headers and the body leave in separate writes: without this,
            # each answer would wait for the client's delayed acknowledgement.
            disable_nagle_algorithm = True

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                store._handle(self, "GET")

            def do_POST(self) -> None:
                store._handle(self, "POST")

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(handler.path)
        path = parts.path.strip("/")
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length).decode("utf-8") if length else ""
        if method == "GET" and "query" in parse_qs(parts.query):
            body = parse_qs(parts.query)["query"][0]
        self.requests.append((method, path, body))
        if self.answer_late(body):
            time.sleep(self.late_seconds)
        if (method, path) == ("POST", "rest/login"):
            self.logins.append(json.loads(body or "{}"))
            return _answer(
                handler, 200, "application/json", "", {"Authorization": TOKEN}
            )
        if handler.headers.get("Authorization") != TOKEN:
            return _answer(handler, 401, "text/plain", "Unauthorized")
        if (method, path) == ("GET", "rest/repositories"):
            listed = json.dumps([{"id": self.dataset, "state": "RUNNING"}])
            return _answer(handler, 200, "application/json", listed)
        if path == f"repositories/{self.dataset}/statements" and method == "POST":
            return self._update(handler, body)
        if path == f"repositories/{self.dataset}":
            return self._query(handler, body)
        return _answer(handler, 404, "text/plain", f"Not found: {path}")

    def _update(self, handler: BaseHTTPRequestHandler, text: str) -> None:
        if self.refuse_update(text):
            return _answer(handler, 500, SHACL_MEDIA_TYPE, SHACL_REPORT)
        update = _STRING.sub(r"\1", _NIL_BLOCK.sub(r"\1", text))
        update = update.replace(f"USING {_NIL}", "")
        with self._lock:
            self.graph.update(update)
        return _answer(handler, 204, "text/plain", "")

    def _query(self, handler: BaseHTTPRequestHandler, text: str) -> None:
        if self.refuse_for_heap(text):
            return _answer(handler, 500, HEAP_MEDIA_TYPE, HEAP_REFUSAL)
        with self._lock:
            result = self.graph.query(_STRING.sub(r"\1", _EXPLICIT.sub("", text)))
            answer = result.serialize(format="json")
        assert answer is not None
        return _answer(
            handler, 200, "application/sparql-results+json", answer.decode("utf-8")
        )


class _QuietServer(ThreadingHTTPServer):
    """A server that says nothing when a client went away before its answer, as a client that
    timed out does."""

    def handle_error(self, request: object, client_address: object) -> None:
        pass


def _answer(
    handler: BaseHTTPRequestHandler,
    status: int,
    media_type: str,
    body: str,
    headers: dict[str, str] | None = None,
) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", media_type)
    handler.send_header("Content-Length", str(len(encoded)))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(encoded)
