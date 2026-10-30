"""The replay of the store-only baseline: the store requests of a recorded UC2 run, sent again
straight to the store, with nothing of the semantic middleware, the OGM or the access module on
the way. This module imports none of them; the baseline (`baseline`) prepares the store and
reads the answers.

**Its own login.** The replay logs in with its own credentials, from `TRIPLESTORE_URL`,
`TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD`, each with its `GRAPHDB_*` variable as the
deprecated fallback, as the triple-store interface reads them (`store_login`). A recorded login
carries no secret, so the replay sends none again.

**Its own repository.** Each request goes to the baseline's own repository in place of the one the
recording names, whatever that is (`retargeted`), through part 1's name guard (`store.guarded`):
`commit-time-` and a run id, never `jms-kapps`, `kapps-demo` or `Tests`.

**Before the window**, the requests go out in their order, each after the answer to the one
before (`Replay.send_in_order`). **In the window**, each goes out at its recorded offset from the
window's start, by the clock, and the replay never waits for an answer before it sends the next:
requests whose offsets overlap go out at the same time, each on a thread of its own
(`Replay.send_at_offsets`). Of each request it keeps when it left, how late that was against its
offset, how long the store took to answer it, and the store's status, media type and size of its
answer; the answer itself only where it is no success, or where the caller asks for it. A request
that got no answer within `REQUEST_TIMEOUT_SECONDS`, or whose connection dropped, has the error
instead. A request that the replay could not send at all, a fault of its own and no answer of the
store, ends the window with that fault (`ReplayedWindow.answers`).

**The receipt's verification** (the writes-only replay). A checked write inserts a
receipt; where the request names the recorded query for it (`Request.verification`), the replay
sends that query right after the write's own answer, on the write's thread, where the store
accepted the write, and keeps its answer on the write (`Sent.verification`). It is the harness's
check, not a replayed request: no window's list holds it. A receipt's removal that waits for it
(`Request.waits_for`) starts at its offset, releases the window's sender, and leaves only once the
write of its receipt has ended, verification and all; its lateness is measured as it leaves. A
removal waits for a write of the window only, and for nothing else, so no request waits for ever.
"""

from __future__ import annotations

import os
import threading
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

import httpx

from jms_kapps.commit_time.store import guarded

# How long a replayed request may wait for its answer, in seconds: the triple-store interface's
# own default, so that a request it would give up on is one the replay gives up on too.
REQUEST_TIMEOUT_SECONDS = 60.0

# The variables of the replay's login, each with its deprecated fallback, as the triple-store
# interface reads them.
STORE_VARIABLES = {
    "TRIPLESTORE_URL": "GRAPHDB_URL",
    "TRIPLESTORE_USERNAME": "GRAPHDB_USERNAME",
    "TRIPLESTORE_PASSWORD": "GRAPHDB_PASSWORD",
}

# The path segments after which a path names a repository: the RDF4J protocol's
# `repositories/<id>/...`, and GraphDB's REST API's `rest/repositories/<id>` and
# `rest/monitor/repository/<id>`.
_NAMES_A_REPOSITORY = ("repositories", "repository")


class ReplayRefused(RuntimeError):
    """The store refused the replay's login, or the environment names no store. The message says
    which, for a person to read, and holds no secret."""


@dataclass(frozen=True)
class StoreLogin:
    """Where the store is and who logs in to it: the URL, the user and the password."""

    url: str
    username: str
    password: str = field(repr=False)


def missing_store_variables() -> list[str]:
    """The variables of the replay's login that the environment sets neither themselves nor by
    their fallback: none where the replay can log in."""
    return [
        name
        for name, fallback in STORE_VARIABLES.items()
        if not os.environ.get(name) and not os.environ.get(fallback)
    ]


def store_login() -> StoreLogin:
    """The replay's login, from the environment: each `TRIPLESTORE_*` variable, or else its
    `GRAPHDB_*` fallback, with one DeprecationWarning that names the variable which replaces it.

    Raises:
        ReplayRefused: If a variable is set neither itself nor by its fallback, naming it.
    """
    missing = missing_store_variables()
    if missing:
        raise ReplayRefused(f"no store is configured: set {', '.join(missing)}")
    return StoreLogin(
        url=_variable("TRIPLESTORE_URL"),
        username=_variable("TRIPLESTORE_USERNAME"),
        password=_variable("TRIPLESTORE_PASSWORD"),
    )


def _variable(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    fallback = STORE_VARIABLES[name]
    warnings.warn(
        f"{fallback} is deprecated; set {name}, which replaces it.",
        DeprecationWarning,
        stacklevel=3,
    )
    return os.environ[fallback]


def retargeted(path: str, repository: str) -> str:
    """`path`, a store request's path after the store's URL, with each repository it names
    replaced by `repository`, the baseline's own.

    Raises:
        StoreRefused: If the guard refuses `repository` (`store.guarded`).
    """
    guarded(repository)
    parts = path.strip("/").split("/")
    for at in range(len(parts) - 1):
        if parts[at] in _NAMES_A_REPOSITORY:
            parts[at + 1] = repository
    return "/".join(parts)


@dataclass(frozen=True)
class Request:
    """One recorded store request, to send again.

    Args:
        method: Its HTTP method.
        path: Its path after the store's URL, as recorded: `retargeted` names the baseline's own
            repository in it.
        body: Its text, or None for a request without one.
        media_type: The media type of its body, or None.
        accept: What it accepts as an answer, or None.
        offset_seconds: When it goes out, in seconds from the window's start; None before the
            window.
        keep_answer: Whether its answer is kept where it is a success too.
        receipt: The receipt that it inserts, as a checked write does, or None; only in the
            writes-only replay.
        verification: The recorded query for its receipt, which the replay sends after its
            answer where the store accepted it, or None.
        waits_for: The receipt that it removes, whose write it waits for in the window, or None.
    """

    method: str
    path: str
    body: str | None
    media_type: str | None = None
    accept: str | None = None
    offset_seconds: float | None = None
    keep_answer: bool = False
    receipt: str | None = None
    verification: Request | None = None
    waits_for: str | None = None


@dataclass(frozen=True)
class Sent:
    """What the replay saw of one request.

    Args:
        request: The request.
        path: The path it went to, the baseline's own repository's.
        at: When it left, by the clock's wall time.
        late_seconds: How much later than its offset it left; None before the window.
        seconds: How long the store took to answer, or to fail to.
        status_code: The store's status, or None for a request without an answer.
        content_type: The media type of the store's answer, or None.
        response_bytes: The size of the store's answer, or None.
        answer: The store's answer, where it is no success or the request asked to keep it.
        error: Why the request got no answer, or None.
        verification: What the replay saw of the query for its receipt, sent after its answer
            (`Request.verification`), or None where none was sent.
    """

    request: Request
    path: str
    at: datetime
    late_seconds: float | None
    seconds: float
    status_code: int | None = None
    content_type: str | None = None
    response_bytes: int | None = None
    answer: str | None = None
    error: str | None = None
    verification: Sent | None = None


class Clock(Protocol):
    """The time of a replay: the wall clock, a monotonic clock, and a wait. `bench.SystemClock`
    is this machine's. It is the bench launcher's `bench.Clock`, written again here, since that
    module imports the middleware."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class ReplayedWindow:
    """The window of a replay, once it has ended: when it started and ended by the clock's wall
    time, and its requests, which `answers` waits for."""

    def __init__(
        self,
        start: datetime,
        end: datetime,
        threads: list[threading.Thread],
        sent: list[Sent | BaseException | None],
    ) -> None:
        self.start = start
        self.end = end
        self._threads = threads
        self._sent = sent

    def answers(self) -> list[Sent]:
        """Every request of the window, once each has its answer or its error, in the order they
        were given.

        Raises:
            RuntimeError: If a request could not be sent at all, as for a fault of the replay's
                own: a request without an answer is no such fault, and has its error instead.
        """
        for thread in self._threads:
            thread.join()
        unsent = [
            (at, sent)
            for at, sent in enumerate(self._sent)
            if not isinstance(sent, Sent)
        ]
        if unsent:
            at, error = unsent[0]
            raise RuntimeError(
                f"the replay could not send request {at} of the window: {error!r}"
            ) from (error if isinstance(error, BaseException) else None)
        return [sent for sent in self._sent if isinstance(sent, Sent)]


class Replay:
    """The replay's client of the store: its own login, and the requests sent again to the
    baseline's own repository.

    Args:
        login: Where the store is, and who logs in.
        repository: The baseline's own repository, which every request goes to.
        clock: The replay's time.
        client: The HTTP client, one of its own if None: a test's stand-in of the store.
        timeout: How long a request may wait for its answer, in seconds.
        receipt_event: Makes the event on which a receipt's removal waits for its write: a
            test's stand-in where it is not `threading.Event`.
    """

    def __init__(
        self,
        login: StoreLogin,
        *,
        repository: str,
        clock: Clock,
        client: httpx.Client | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        receipt_event: Callable[[], threading.Event] = threading.Event,
    ) -> None:
        self.repository = guarded(repository)
        self._login = login
        self._clock = clock
        self._receipt_event = receipt_event
        # No limit on the connections: a request that waited for a free one would leave later
        # than its offset, and the replay sends every request at its time.
        self._client = (
            client
            if client is not None
            else httpx.Client(
                base_url=login.url.rstrip("/"),
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=None, max_keepalive_connections=None
                ),
            )
        )
        self._authorization: str | None = None

    def log_in(self) -> None:
        """Log in with the replay's own credentials, once, before any request.

        Raises:
            ReplayRefused: If the store refuses the login or gives no answer.
        """
        try:
            response = self._client.post(
                "/rest/login",
                json={
                    "username": self._login.username,
                    "password": self._login.password,
                },
            )
        except httpx.HTTPError as error:
            raise ReplayRefused(
                f"the store gave no answer to the replay's login: {error}"
            ) from None
        if response.status_code != 200:
            raise ReplayRefused(
                f"the store refused the replay's login: {response.status_code}"
            )
        self._authorization = response.headers.get("Authorization", "")

    def send_in_order(self, requests: Sequence[Request]) -> list[Sent]:
        """Send `requests` in their order, each after the answer to the one before, and after
        the verification of a checked write's receipt."""
        sent = []
        for request in requests:
            sent.append(self._send_and_verify(request, due=None))
        return sent

    def send_at_offsets(
        self, requests: Sequence[Request], seconds: float
    ) -> ReplayedWindow:
        """Start the window now, send each of `requests` at its offset from the window's start,
        without waiting for an answer, and return at the window's end, `seconds` after its
        start. The requests still open then are answered in `ReplayedWindow.answers`.

        A request leaves on a thread of its own, and the next is sent only once it has left, so
        that its time of leaving is its own; a request that left late keeps by how much. A
        receipt's removal that waits for its write releases the next as it starts waiting."""
        began = self._clock.monotonic()
        start = self._clock.now()
        sent: list[Sent | BaseException | None] = [None] * len(requests)
        threads = []
        # One event for each receipt that a write of the window inserts: set once that write has
        # ended, verification and all.
        written: dict[str, threading.Event] = {}
        for request in requests:
            if request.receipt is not None:
                written[request.receipt] = self._receipt_event()
        for at in sorted(
            range(len(requests)), key=lambda at: requests[at].offset_seconds or 0.0
        ):
            request = requests[at]
            due = began + (request.offset_seconds or 0.0)
            wait = due - self._clock.monotonic()
            if wait > 0:
                self._clock.sleep(wait)
            left = threading.Event()
            thread = threading.Thread(
                target=self._send_on_its_own,
                args=(request, due, left, sent, at, written),
                name=f"replay-{at}",
                daemon=True,
            )
            thread.start()
            left.wait()
            threads.append(thread)
        rest = began + seconds - self._clock.monotonic()
        if rest > 0:
            self._clock.sleep(rest)
        return ReplayedWindow(start, self._clock.now(), threads, sent)

    def _send_on_its_own(
        self,
        request: Request,
        due: float,
        left: threading.Event,
        sent: list[Sent | BaseException | None],
        at: int,
        written: dict[str, threading.Event],
    ) -> None:
        try:
            if request.waits_for is not None and request.waits_for in written:
                # A receipt's removal: the window's sender goes on, and the removal leaves once
                # the write of its receipt has ended.
                left.set()
                written[request.waits_for].wait()
            sent[at] = self._send_and_verify(request, due=due, left=left)
        except Exception as error:  # noqa: BLE001 - `ReplayedWindow.answers` raises it
            sent[at] = error
        finally:
            # Also where the request could not be sent at all: the window goes on, and a
            # removal that waits for this write goes out.
            left.set()
            if request.receipt is not None and request.receipt in written:
                written[request.receipt].set()

    def _send_and_verify(
        self,
        request: Request,
        *,
        due: float | None,
        left: threading.Event | None = None,
    ) -> Sent:
        """Send `request` and wait for its answer; then, where it names the query for its
        receipt and the store accepted it, send that query and wait for its answer too."""
        sent = self._send(request, due=due, left=left)
        accepted = sent.status_code is not None and 200 <= sent.status_code < 300
        if request.verification is None or not accepted:
            return sent
        verification = self._send(request.verification, due=None)
        return replace(sent, verification=verification)

    def _send(
        self,
        request: Request,
        *,
        due: float | None,
        left: threading.Event | None = None,
    ) -> Sent:
        """Send `request` and wait for its answer. `due` is when it should leave, on the
        monotonic clock, None before the window; `left` is set as it leaves."""
        path = retargeted(request.path, self.repository)
        headers = {}
        if self._authorization:
            headers["Authorization"] = self._authorization
        if request.media_type:
            headers["Content-Type"] = request.media_type
        if request.accept:
            headers["Accept"] = request.accept
        content = request.body.encode("utf-8") if request.body is not None else None
        leaving = self._clock.monotonic()
        at = self._clock.now()
        if left is not None:
            left.set()
        late = None if due is None else leaving - due
        try:
            response = self._client.request(
                request.method, "/" + path, content=content, headers=headers
            )
        except httpx.HTTPError as error:
            # No answer: the store did not answer in time, or the connection dropped.
            return Sent(
                request=request,
                path=path,
                at=at,
                late_seconds=late,
                seconds=self._clock.monotonic() - leaving,
                error=f"{type(error).__name__}: {error}",
            )
        seconds = self._clock.monotonic() - leaving
        success = 200 <= response.status_code < 300
        return Sent(
            request=request,
            path=path,
            at=at,
            late_seconds=late,
            seconds=seconds,
            status_code=response.status_code,
            content_type=response.headers.get("Content-Type"),
            response_bytes=len(response.content),
            answer=response.text if request.keep_answer or not success else None,
        )

    def close(self) -> None:
        self._client.close()
