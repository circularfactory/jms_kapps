"""The observation inside one participant of a run: OpenTelemetry's instrumentation of FastAPI,
requests and httpx, the spans of the object-graph mapper's public calls, and every span written
into one file of the run's folder. The wrapper (`wrapper`) sets it up before the participant's own
code starts; nothing of KSM, the object-graph mapper or the triple-store interface is changed.

**HTTP.** FastAPI's spans are the workflow calls a participant serves, httpx's the calls it makes
to other participants, and requests' its requests to the store, which the triple-store interface
sends with requests. A hook tags each **store request**, a request to the URL that
`TRIPLESTORE_URL` names (`store_requests`), with:

- `store.request` (true), `store.kind`, `store.method` and `store.path`;
- `store.text`, the text of the request, so that a replay can send it again; a login keeps none,
  since its text holds the password;
- `store.request_bytes`, the size of its body;
- `store.operation`, `store.box` and `store.status_written`: for each status the request writes,
  the Operation, the box (empty where the request does not name it) and the status, one list
  each, in the same order;
- `store.receipt_written` for the update of a checked write, and `store.receipt_asked` with
  `store.receipt_found` for the query that asks for its receipt;
- once the store answered: `store.status_code`, `store.response_bytes`, `store.outcome`, and for
  a refusal `store.refusal` and `store.reason`, the store's answer as it gave it. A request that
  got no answer has no `store.outcome`: its span holds the error instead.

**The object-graph mapper.** Its public calls are wrapped, not edited: `OGM.commit`,
`OGM.get_class_spec` (the class-spec derivation), `OGM.fetch`, `OGM.create`, `OGM.delete` and
`Node.diff`, each a span named after the call, as `kapps_ogm.OGM.commit`. The class query and the
update of a commit are store requests, so their spans come from requests' instrumentation. A
commit thus shows its class query, its class-spec derivation, its fetch, its diff and its update
as separate spans below its own.

**The spans' file.** Each span is written as it ends, one JSON object a line (`span_line`), so
that a participant that is stopped loses no span that has ended. The harness reads the files after
the run (`observation`).
"""

from __future__ import annotations

import functools
import importlib
import json
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

import httpx
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Tracer

from jms_kapps.evaluation.wrapper import OGM_CALLS, span_name


class JsonLinesExporter(SpanExporter):
    """Writes each span that ends as one line of `path` (`span_line`), at once."""

    def __init__(self, path: Path, participant: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = path.open("a", encoding="utf-8")
        self._participant = participant
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        lines = "".join(
            json.dumps(span_line(span, self._participant), ensure_ascii=False) + "\n"
            for span in spans
        )
        with self._lock:
            if self._file.closed:
                return SpanExportResult.FAILURE
            self._file.write(lines)
            self._file.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            self._file.close()


def span_line(span: ReadableSpan, participant: str) -> dict[str, Any]:
    """What the spans' file holds of `span`: the participant, the span's name and kind, its ids
    and its parent's, its start and its end in nanoseconds since the epoch, its status, its
    attributes and its events."""
    context = span.get_span_context()
    return {
        "participant": participant,
        "name": span.name,
        "kind": span.kind.name,
        "trace_id": f"{context.trace_id:032x}" if context else None,
        "span_id": f"{context.span_id:016x}" if context else None,
        "parent_id": f"{span.parent.span_id:016x}" if span.parent else None,
        "start": span.start_time,
        "end": span.end_time,
        "status": span.status.status_code.name,
        "status_description": span.status.description,
        "attributes": dict(span.attributes or {}),
        "events": [
            {
                "name": event.name,
                "time": event.timestamp,
                "attributes": dict(event.attributes or {}),
            }
            for event in span.events
        ],
    }


class Instrumentation:
    """The observation of one participant, set up by `instrument`, until `close`."""

    def __init__(
        self, provider: TracerProvider, undo: list[Callable[[], None]]
    ) -> None:
        self.provider = provider
        self._undo = undo

    def close(self) -> None:
        """Take every instrumentation and wrapping back, and write the last spans."""
        for undo in reversed(self._undo):
            undo()
        self._undo.clear()
        self.provider.shutdown()


def instrument(
    spans: Path, *, participant: str, store_url: str | None
) -> Instrumentation:
    """Observe this process: instrument FastAPI, requests and httpx, wrap the object-graph
    mapper's public calls, and write every span into `spans` as it ends.

    Args:
        spans: The file of the spans, in the run's folder.
        participant: Who this process is, by its resource's local name.
        store_url: The URL of the store, whose requests are store requests; None where the
            environment names none.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": participant}))
    provider.add_span_processor(
        SimpleSpanProcessor(JsonLinesExporter(spans, participant))
    )
    undo: list[Callable[[], None]] = []

    # FastAPI first: an app is instrumented only where its module imports FastAPI after this, and
    # the framework that builds each participant's app is loaded with the middleware itself.
    fastapi_instrumentor = FastAPIInstrumentor()
    # One span for each call a participant serves, without one more for each part of its
    # answer: a stream of events would otherwise write a span for each event.
    fastapi_instrumentor.instrument(
        tracer_provider=provider, exclude_spans=["receive", "send"]
    )
    undo.append(fastapi_instrumentor.uninstrument)
    tag = StoreRequestTags(store_url)
    requests_instrumentor = RequestsInstrumentor()
    requests_instrumentor.instrument(
        tracer_provider=provider,
        request_hook=tag.requests_request,
        response_hook=tag.requests_response,
    )
    undo.append(requests_instrumentor.uninstrument)
    httpx_instrumentor = HTTPXClientInstrumentor()
    httpx_instrumentor.instrument(
        tracer_provider=provider,
        request_hook=tag.httpx_request,
        response_hook=tag.httpx_response,
    )
    undo.append(httpx_instrumentor.uninstrument)
    undo.append(wrap_ogm(provider.get_tracer("jms_kapps.evaluation")))
    return Instrumentation(provider, undo)


def wrap_ogm(tracer: Tracer) -> Callable[[], None]:
    """Wrap each of the mapper's public calls (`OGM_CALLS`) in a span of its own. Returns what
    takes the wrapping back."""
    originals: list[tuple[type, str, Any]] = []
    for module_name, owner_name, call in OGM_CALLS:
        owner = getattr(importlib.import_module(module_name), owner_name)
        original = owner.__dict__[call]
        originals.append((owner, call, original))
        setattr(owner, call, _spanned(original, tracer, span_name(owner_name, call)))

    def unwrap() -> None:
        for owner, call, original in originals:
            setattr(owner, call, original)

    return unwrap


def _spanned(call: Any, tracer: Tracer, name: str) -> Any:
    @functools.wraps(call)
    def spanned(*args: Any, **kwargs: Any) -> Any:
        with tracer.start_as_current_span(name):
            return call(*args, **kwargs)

    return spanned


class StoreRequestTags:
    """The hooks that tag each store request's span (`store_requests`), for requests and for
    httpx."""

    def __init__(self, store_url: str | None) -> None:
        # Imported only now, once FastAPI is instrumented: reading a request needs the
        # middleware's vocabulary, and importing it loads the framework that builds each
        # participant's FastAPI app.
        from jms_kapps.evaluation import store_requests

        self._store_requests = store_requests
        self._store = _without_credentials(store_url.rstrip("/")) if store_url else None

    def requests_request(self, span: Span, request: Any) -> None:
        self._request(
            span,
            str(request.method or ""),
            str(request.url or ""),
            request.headers.get("Content-Type"),
            request.body,
        )

    def requests_response(self, span: Span, request: Any, response: Any) -> None:
        if not self._is_store(str(request.url or "")):
            return
        body = request.body
        text = (
            body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        )
        self._response(
            span,
            response.status_code,
            response.headers.get("Content-Type"),
            response.content or b"",
            asked=self._store_requests.receipt_asked(text),
        )

    def httpx_request(self, span: Span, request: Any) -> None:
        method, url, headers, stream, _ = request
        self._request(
            span,
            method.decode() if isinstance(method, bytes) else str(method),
            str(url),
            headers.get("Content-Type"),
            _bytes_of(stream),
        )

    def httpx_response(self, span: Span, request: Any, response: Any) -> None:
        _, url, _, _, _ = request
        status_code, headers, _, _ = response
        if not self._is_store(str(url)):
            return
        # httpx reads the answer's body only after this hook: the reason of a refusal is not
        # known here.
        self._response(span, status_code, headers.get("Content-Type"), None, asked=None)

    def _is_store(self, url: str) -> bool:
        return self._store is not None and _without_credentials(url).startswith(
            self._store + "/"
        )

    def _request(
        self,
        span: Span,
        method: str,
        url: str,
        content_type: str | None,
        body: bytes | str | None,
    ) -> None:
        if not self._is_store(url):
            return
        assert self._store is not None
        path = urlsplit(_without_credentials(url)).path
        path = path[len(urlsplit(self._store).path) :].strip("/")
        kind = self._store_requests.kind_of(method, path, content_type)
        raw = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        span.set_attribute("store.request", True)
        span.set_attribute("store.kind", kind)
        span.set_attribute("store.method", method.upper())
        span.set_attribute("store.path", path)
        span.set_attribute("store.request_bytes", len(raw))
        if kind == self._store_requests.LOGIN:
            return
        text = raw.decode("utf-8", errors="replace")
        span.set_attribute("store.text", text)
        writes = self._store_requests.status_writes(text)
        if writes:
            span.set_attribute("store.operation", [w.operation for w in writes])
            span.set_attribute("store.box", [w.box or "" for w in writes])
            span.set_attribute("store.status_written", [w.status for w in writes])
        written = self._store_requests.receipt_written(text)
        if written:
            span.set_attribute("store.receipt_written", written)
        asked = self._store_requests.receipt_asked(text)
        if asked:
            span.set_attribute("store.receipt_asked", asked)

    def _response(
        self,
        span: Span,
        status_code: int,
        content_type: str | None,
        body: bytes | None,
        *,
        asked: str | None,
    ) -> None:
        text = (body or b"").decode("utf-8", errors="replace")
        outcome, refusal, reason = self._store_requests.answer_of(
            status_code, content_type, text
        )
        span.set_attribute("store.status_code", status_code)
        if body is not None:
            span.set_attribute("store.response_bytes", len(body))
        span.set_attribute("store.outcome", outcome)
        if refusal is not None and reason is not None:
            span.set_attribute("store.refusal", refusal)
            span.set_attribute("store.reason", reason)
        if asked and outcome == self._store_requests.ACCEPTED and body is not None:
            found = self._store_requests.receipt_found(text)
            if found is not None:
                span.set_attribute("store.receipt_found", found)


def _bytes_of(stream: Any) -> bytes | None:
    """The body of an httpx request, where it is held whole, as a request with content is."""
    if isinstance(stream, httpx.ByteStream):
        return b"".join(stream)
    return None


def _without_credentials(url: str) -> str:
    """`url` without a user or a password in it."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return parts._replace(netloc=host).geturl()
