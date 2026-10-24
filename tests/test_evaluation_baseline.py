"""The store-only baseline of a recorded UC2 run, against stand-ins only.

Each recorded run here is made by part 1 and part 2 of the harness against their stand-ins
(`test_evaluation_bench.Bench`): a UC2 run whose participants' wrappers write the spans that a test
gives them, so that its record holds their store requests as part 2 writes them. Its window runs
from 30 s to 90 s on its clock.

The baseline then gets a stand-in of GraphDB's REST API, part 1's bench graph whose seed writes to
a stand-in client, a stand-in of the store that the replay sends its requests to, and a clock that
moves only when the replay waits. The stand-in store gives an answer at the clock's time that a
test holds it to, so each latency is known; the clock moves on only once each request that left
has its answer or is held. Nothing reaches a store, a process or a network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import entry_points
from pathlib import Path

import httpx
import pytest
from _evaluation_fakes import (
    PASSWORD,
    USERNAME,
    Events,
    FakeBenchGraph,
    FakeClock,
    FakeGraphDB,
    other_repositories_named,
    store_span,
)
from _repo import find_repo_root
from kapps_triplestore_interface.queries.checked_write import (
    RECEIPT_GRAPH,
    RECEIPT_PREDICATE,
)
from test_evaluation_bench import CROSS, NO_WATCH_INTERVAL, STRATEGIES, Bench

from jms_kapps.evaluation import baseline as baseline_module
from jms_kapps.evaluation import client_load
from jms_kapps.evaluation.approval import HeavyRunRefused
from jms_kapps.evaluation.baseline import (
    BASELINE_RUN_KIND,
    BaselineRefused,
    read_recorded_run,
    run_baseline,
)
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.prefill import transactions, write_prefill
from jms_kapps.evaluation.replay import (
    Replay,
    ReplayRefused,
    StoreLogin,
    retargeted,
    store_login,
)
from jms_kapps.evaluation.seed import PREFILL_TRANSACTION_STATEMENTS
from jms_kapps.evaluation.store import StoreRefused
from jms_kapps.evaluation.store_requests import HEAP_REFUSAL
from jms_kapps.uc2.history import ENABLE_HISTORY
from jms_kapps.uc2.topology import shipped_topology

# A recorded run with a warm-up of 30 s and a measured window of 60 s: its window runs from 30 s
# to 90 s on its clock.
WARM_UP = Configuration(**(vars(CROSS) | {"warm_up_seconds": 30.0}))
WINDOW_START = 30.0

SHACL_MEDIA_TYPE = "application/shacl-validation-report+n-quads"
SHACL_REASON = "_:r <http://www.w3.org/ns/shacl#conforms> false ."
HEAP_REASON = f"{HEAP_REFUSAL} 249Mb for group by and distinct"

# The variables of a store's login, each with its deprecated fallback.
STORE_ENVIRONMENT = (
    "TRIPLESTORE_URL",
    "TRIPLESTORE_USERNAME",
    "TRIPLESTORE_PASSWORD",
    "GRAPHDB_URL",
    "GRAPHDB_USERNAME",
    "GRAPHDB_PASSWORD",
)


class ReplayClock(FakeClock):
    """A clock whose time moves only when the replay waits, at which the stand-in store gives
    each answer that a test holds to a time.

    A wait first lets each request that left reach the store: it goes on only once each one has
    its answer, is held (`answer_at`), or its thread has ended. It then gives each held answer
    whose time it reaches, in the order of their times, at that time, and waits until the
    replay has read the clock for it. So each latency, and each time a request left, is the one
    a test gives it.

    Args:
        oversleep: How much longer each wait lasts than it was asked to: a slow client.
    """

    def __init__(self, events: Events, *, oversleep: float = 0.0) -> None:
        super().__init__(events)
        self.oversleep = oversleep
        self._changed = threading.Condition()
        # The replay's threads that read the clock as their request left and not yet for its
        # answer, by their idents.
        self._sending: dict[int | None, threading.Thread] = {}
        self._held: list[tuple[float, int | None]] = []
        self._released: set[int | None] = set()

    def monotonic(self) -> float:
        with self._changed:
            thread = threading.current_thread()
            if thread.name.startswith("replay-"):
                # A thread of the replay reads the clock twice: as its request leaves, and once
                # it has its answer. Another thread that reads it, as the run limit's, is none of
                # them.
                if self._sending.get(thread.ident) is thread:
                    del self._sending[thread.ident]
                else:
                    self._sending[thread.ident] = thread
                self._changed.notify_all()
            return self.seconds

    def answer_at(self, seconds: float) -> None:
        """Hold the answer of the calling thread's request until a wait reaches `seconds`."""
        with self._changed:
            if seconds <= self.seconds:
                return
            ident = threading.get_ident()
            self._held.append((seconds, ident))
            self._held.sort(key=lambda held: held[0])
            self._changed.notify_all()
            self._wait_until(lambda: ident in self._released)
            self._released.discard(ident)

    def _settled(self) -> bool:
        held = {ident for _, ident in self._held}
        return all(
            ident in held or not thread.is_alive()
            for ident, thread in self._sending.items()
        )

    def _wait_until(self, done: Callable[[], bool]) -> None:
        """Wait, holding the clock's lock between looks, until `done`; a thread that ends
        tells no one, so look every 10 ms."""
        deadline = time.monotonic() + 10
        while not done():
            assert time.monotonic() < deadline, "the replay's requests did not settle"
            self._changed.wait(0.01)

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))
        with self._changed:
            self._wait_until(self._settled)
            target = self.seconds + seconds + self.oversleep
            while self._held and self._held[0][0] <= target:
                due, ident = self._held.pop(0)
                self.seconds = max(self.seconds, due)
                self._released.add(ident)
                self._changed.notify_all()
                self._wait_until(self._settled)
            self.seconds = target


@dataclass(frozen=True)
class Arrival:
    """One request as the stand-in store got it, at the clock's time."""

    seconds: float
    method: str
    path: str
    content_type: str | None
    accept: str | None
    authorization: str | None
    text: str


# How the stand-in store answers a request, by its text: given the clock and the request.
Answer = Callable[[ReplayClock, Arrival], httpx.Response]


def accepted(at: float | None = None) -> Answer:
    """Accept the request, at the clock's `at` where it is given."""

    def answer(clock: ReplayClock, arrival: Arrival) -> httpx.Response:
        if at is not None:
            clock.answer_at(at)
        return httpx.Response(204)

    return answer


def refused(
    status: int, body: str, content_type: str = "text/plain", at: float | None = None
) -> Answer:
    def answer(clock: ReplayClock, arrival: Arrival) -> httpx.Response:
        if at is not None:
            clock.answer_at(at)
        return httpx.Response(
            status, content=body.encode(), headers={"Content-Type": content_type}
        )

    return answer


def no_answer(at: float) -> Answer:
    """No answer: the store did not answer in time, which the client finds at the clock's
    `at`."""

    def answer(clock: ReplayClock, arrival: Arrival) -> httpx.Response:
        clock.answer_at(at)
        raise httpx.ReadTimeout("read timed out")

    return answer


class ReplayStore:
    """The store as the replay reaches it: its login, and each request to a repository, kept with
    the clock's time it arrived, answered as a test gives it by the request's text, or else
    accepted at once (an `ASK` with its receipt found)."""

    TOKEN = "GDB replay-token"

    def __init__(
        self, clock: ReplayClock, answers: Mapping[str, Answer] | None = None
    ) -> None:
        self.clock = clock
        self.answers = dict(answers or {})
        # The body of each request to the login, as it came.
        self.logins: list[str] = []
        self.arrived: list[Arrival] = []
        # What happened, in order: ("arrived", text) and ("answered", text).
        self.log: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self.handle), base_url="http://graphdb.test"
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if (request.method, path) == ("POST", "rest/login"):
            with self._lock:
                self.logins.append(request.content.decode())
            return httpx.Response(200, headers={"Authorization": self.TOKEN})
        arrival = Arrival(
            seconds=self.clock.seconds,
            method=request.method,
            path=path,
            content_type=request.headers.get("Content-Type"),
            accept=request.headers.get("Accept"),
            authorization=request.headers.get("Authorization"),
            text=request.content.decode(),
        )
        with self._lock:
            self.arrived.append(arrival)
            self.log.append(("arrived", arrival.text))
        try:
            answer = self.answers.get(arrival.text)
            if answer is not None:
                return answer(self.clock, arrival)
            if arrival.text.startswith("ASK"):
                return httpx.Response(200, json={"head": {}, "boolean": True})
            return httpx.Response(204)
        finally:
            with self._lock:
                self.log.append(("answered", arrival.text))

    def texts(self) -> list[str]:
        return [arrival.text for arrival in self.arrived]


class Baseline:
    """One baseline run against stand-ins, and what each saw."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        answers: Mapping[str, Answer] | None = None,
        oversleep: float = 0.0,
        login: StoreLogin | None = None,
    ) -> None:
        self.events = Events()
        self.store = FakeGraphDB(events=self.events)
        self.server = self.store.server()
        self.clock = ReplayClock(self.events, oversleep=oversleep)
        self.replay_store = ReplayStore(self.clock, answers)
        self.login = login or StoreLogin("http://graphdb.test", USERNAME, PASSWORD)
        self.runs = tmp_path / "baselines"
        self.graphs: list[tuple[str, Path | None, bool]] = []
        self.printed: list[str] = []

    def graph_for(
        self, repository: str, prefill: Path | None, with_shapes: bool
    ) -> FakeBenchGraph:
        self.graphs.append((repository, prefill, with_shapes))
        return FakeBenchGraph(
            self.events, prefill=prefill, with_shapes=with_shapes, clock=self.clock
        )

    def replay_for(self, repository: str, clock: object) -> Replay:
        return Replay(
            self.login,
            repository=repository,
            clock=clock,  # type: ignore[arg-type]
            client=self.replay_store.client(),
        )

    def run(self, recorded: Path, **options: object) -> dict:
        options.setdefault("watch_interval_seconds", NO_WATCH_INTERVAL)
        return run_baseline(
            read_recorded_run(recorded, **STRATEGIES),
            runs=self.runs,
            server=self.server,
            graph_for=self.graph_for,
            replay_for=self.replay_for,
            clock=self.clock,
            echo=self.printed.append,
            **options,  # type: ignore[arg-type]
        )


def recorded_run(
    tmp_path: Path,
    spans: Callable[[FakeClock], list[dict]],
    configuration: Configuration = WARM_UP,
) -> Path:
    """The folder of a UC2 run that part 1 and part 2 recorded against their stand-ins, in which
    Module1's wrapper wrote `spans`."""
    bench = Bench(tmp_path / "uc2")
    bench.spans = {"Module1": spans(bench.clock)}
    record = bench.run(configuration)
    assert record["outcome"] == "completed", record["outcome"]
    return bench.folder(record)


def read(clock: FakeClock, at: float, text: str, **options: object) -> dict:
    """A query of Module1's, recorded at `at` on the recorded run's clock."""
    return store_span(clock, "Module1", at, 0.01, kind="read", text=text, **options)  # type: ignore[arg-type]


def write(clock: FakeClock, at: float, text: str, **options: object) -> dict:
    """An update of Module1's, recorded at `at`."""
    return store_span(clock, "Module1", at, 0.01, kind="write", text=text, **options)  # type: ignore[arg-type]


def login(clock: FakeClock, at: float) -> dict:
    """Module1's login, recorded at `at`: its text is not kept, since it holds the password."""
    span = store_span(clock, "Module1", at, 0.01, kind="login")
    span["attributes"]["store.path"] = "rest/login"  # type: ignore[index]
    del span["attributes"]["store.text"]  # type: ignore[attr-defined]
    return span


def at_path(span: dict, path: str) -> dict:
    """`span` with the path the recording names."""
    span["attributes"]["store.path"] = path
    return span


def seconds_of(baseline: Baseline, at: str) -> float:
    """The time `at`, as a record writes it, in seconds of the baseline's clock."""
    return (datetime.fromisoformat(at) - baseline.clock.start).total_seconds()


def measured(record: dict) -> dict[str, dict]:
    """The measured requests of a baseline's record, by their text."""
    return {
        request["text"]: request for request in record["measures"]["store_requests"]
    }


# The criteria, one by one.


def test_the_requests_before_the_window_go_out_first_in_their_order_and_are_not_measured(
    tmp_path,
):
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 2.0, "SELECT ?first"),
            write(clock, 5.0, "INSERT DATA { <s> <p> <second> }"),
            read(clock, 20.0, "SELECT ?third"),
            read(clock, 31.0, "SELECT ?in_the_window_at_1"),
            write(clock, 45.5, "INSERT DATA { <s> <p> <in_the_window_at_15.5> }"),
            read(clock, 95.0, "SELECT ?after_the_window"),
        ],
    )
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    assert record["outcome"] == "completed", record["outcome"]
    store = baseline.replay_store
    assert store.texts() == [
        "SELECT ?first",
        "INSERT DATA { <s> <p> <second> }",
        "SELECT ?third",
        "SELECT ?in_the_window_at_1",
        "INSERT DATA { <s> <p> <in_the_window_at_15.5> }",
    ]
    # Each request before the window leaves after the answer to the one before it.
    assert store.log[:6] == [
        ("arrived", "SELECT ?first"),
        ("answered", "SELECT ?first"),
        ("arrived", "INSERT DATA { <s> <p> <second> }"),
        ("answered", "INSERT DATA { <s> <p> <second> }"),
        ("arrived", "SELECT ?third"),
        ("answered", "SELECT ?third"),
    ]
    # The window's requests leave at their recorded offsets from the window's start, by the
    # clock, and the window lasts as long as the recorded one.
    window_start = seconds_of(baseline, record["window"]["start"])
    assert seconds_of(baseline, record["window"]["end"]) == window_start + 60.0
    assert [a.seconds - window_start for a in store.arrived[3:]] == [1.0, 15.5]
    # Every request sent is kept, as part 2 keeps them; only the window's are measured.
    requests = record["measures"]["store_requests"]
    assert [(r["text"], r["window"], r["offset_seconds"]) for r in requests] == [
        ("SELECT ?first", False, None),
        ("INSERT DATA { <s> <p> <second> }", False, None),
        ("SELECT ?third", False, None),
        ("SELECT ?in_the_window_at_1", True, 1.0),
        ("INSERT DATA { <s> <p> <in_the_window_at_15.5> }", True, 15.5),
    ]
    assert all(r["late_seconds"] is None for r in requests[:3])
    assert record["measures"]["store_requests_in_window"] == 2
    assert record["measures"]["completed_writes_in_window"] == 1
    assert record["replay"] == {
        "recorded_requests": 6,
        "logins_left_out": 0,
        "before_the_window": 3,
        "in_the_window": 2,
        "after_the_window_left_out": 1,
    }


def test_a_request_whose_offset_comes_while_an_earlier_one_is_open_goes_out_at_its_offset(
    tmp_path,
):
    """The first request of the window is answered only at 3 s, after the second left at 2 s."""
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            write(clock, 31.0, "INSERT DATA { <s> <p> <first> }"),
            read(clock, 32.0, "SELECT ?second"),
        ],
    )
    baseline = Baseline(
        tmp_path,
        answers={"INSERT DATA { <s> <p> <first> }": accepted(at=3.0)},
    )

    record = baseline.run(recorded)

    # Nothing waits before the window, so it starts at 0 s on the baseline's clock: the times
    # the stand-in store holds its answers to are offsets from the window's start.
    assert seconds_of(baseline, record["window"]["start"]) == 0.0
    store = baseline.replay_store
    assert store.log == [
        ("arrived", "INSERT DATA { <s> <p> <first> }"),
        ("arrived", "SELECT ?second"),
        ("answered", "SELECT ?second"),
        ("answered", "INSERT DATA { <s> <p> <first> }"),
    ]
    assert [a.seconds for a in store.arrived] == [1.0, 2.0]
    first, second = record["measures"]["store_requests"]
    assert (first["seconds"], first["late_seconds"]) == (2.0, 0.0)
    assert (second["seconds"], second["late_seconds"]) == (0.0, 0.0)


def test_a_request_that_leaves_late_has_its_lateness_in_the_record(tmp_path):
    """A client whose every wait lasts half a second longer than asked: each request of the
    window leaves half a second after its offset."""
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 31.0, "SELECT ?first"),
            read(clock, 32.0, "SELECT ?second"),
        ],
    )
    baseline = Baseline(tmp_path, oversleep=0.5)

    record = baseline.run(recorded)

    requests = record["measures"]["store_requests"]
    assert [r["offset_seconds"] for r in requests] == [1.0, 2.0]
    assert [r["late_seconds"] for r in requests] == [0.5, 0.5]
    # The measured window lasts as long as the recorded one, although the wait for its end
    # lasted longer.
    assert record["measures"]["window"]["minutes"] == 1.0


def test_each_measured_request_s_latency_and_outcome_is_in_the_record_as_part_2_s_measures(
    tmp_path,
):
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 10.0, "SELECT DISTINCT ?refused_before_the_window"),
            write(clock, 31.0, "INSERT DATA { <s> <p> <accepted> }"),
            write(clock, 32.0, "INSERT DATA { <s> <p> <refused by shacl> }"),
            read(clock, 33.0, "SELECT DISTINCT ?refused_for_want_of_heap"),
            read(clock, 34.0, "SELECT ?timed_out"),
        ],
    )
    baseline = Baseline(
        tmp_path,
        answers={
            "INSERT DATA { <s> <p> <accepted> }": accepted(at=1.25),
            "INSERT DATA { <s> <p> <refused by shacl> }": refused(
                400, SHACL_REASON, SHACL_MEDIA_TYPE, at=2.5
            ),
            "SELECT DISTINCT ?refused_for_want_of_heap": refused(500, HEAP_REASON),
            "SELECT ?timed_out": no_answer(at=10.0),
            "SELECT DISTINCT ?refused_before_the_window": refused(500, HEAP_REASON),
        },
    )

    record = baseline.run(recorded)

    assert record["run_kind"] == BASELINE_RUN_KIND
    replayed = json.loads((recorded / "record.json").read_text())
    assert record["replayed_run"]["run_id"] == replayed["run_id"]
    assert record["replayed_run"]["repository"] == replayed["repository"]
    requests = measured(record)
    ok = requests["INSERT DATA { <s> <p> <accepted> }"]
    assert (ok["outcome"], ok["seconds"], ok["applied"]) == ("accepted", 0.25, True)
    shacl = requests["INSERT DATA { <s> <p> <refused by shacl> }"]
    assert (shacl["outcome"], shacl["refusal"], shacl["reason"]) == (
        "refused",
        "shacl",
        SHACL_REASON,
    )
    assert (shacl["seconds"], shacl["applied"]) == (0.5, False)
    heap = requests["SELECT DISTINCT ?refused_for_want_of_heap"]
    assert (heap["outcome"], heap["refusal"], heap["reason"]) == (
        "refused",
        "heap",
        HEAP_REASON,
    )
    late = requests["SELECT ?timed_out"]
    assert (late["outcome"], late["seconds"]) == ("timed out", 6.0)
    assert "ReadTimeout" in late["reason"]
    measures = record["measures"]
    assert measures["window"]["minutes"] == 1.0
    assert measures["store_requests_in_window"] == 4
    assert (measures["refused_in_window"], measures["timed_out_in_window"]) == (2, 1)
    assert measures["refused_or_unanswered_share"] == pytest.approx(3 / 4)
    assert measures["completed_writes_in_window"] == 1
    assert measures["completed_writes_per_minute"] == pytest.approx(1.0)
    # Every refusal for want of heap is kept, as part 2 keeps them, and counts in the window
    # only where it came in it.
    assert [(r["reason"], r["window"]) for r in measures["heap_refusals"]] == [
        (HEAP_REASON, False),
        (HEAP_REASON, True),
    ]
    assert measures["heap_refusals_in_window"] == 1
    # The form of part 2's measures: each request has every field a UC2 run's has, and its
    # offset and lateness besides.
    [part_2] = replayed["measures"]["store_requests"][:1]
    for request in measures["store_requests"]:
        assert set(request) == set(part_2) | {"offset_seconds", "late_seconds"}


def test_a_checked_write_counts_as_applied_where_the_replayed_query_finds_its_receipt(
    tmp_path,
):
    """The interface's checked write: its update inserts a receipt, and its query asks for it."""
    receipt = "urn:uuid:receipt-1"
    update = f"INSERT DATA {{ <s> <p> <o> . <{receipt}> <{RECEIPT_PREDICATE}> true }}"
    asked = f"ASK {{ GRAPH <{RECEIPT_GRAPH}> {{ <{receipt}> ?p ?o }} }}"
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            write(clock, 31.0, update, receipt_written=receipt),
            read(clock, 31.5, asked, receipt_asked=receipt, receipt_found=True),
        ],
    )
    for found in (True, False):
        baseline = Baseline(
            tmp_path / str(found),
            answers={
                asked: lambda clock, arrival, found=found: httpx.Response(
                    200, json={"head": {}, "boolean": found}
                )
            },
        )

        record = baseline.run(recorded)

        assert measured(record)[update]["applied"] is found


def test_a_recorded_run_without_the_wrapper_is_refused(tmp_path):
    recorded = recorded_run(
        tmp_path,
        lambda clock: [],
        Configuration(**(vars(WARM_UP) | {"wrapper": False})),
    )

    with pytest.raises(
        BaselineRefused, match="uc2-unwrapped: it ran without the wrapper"
    ):
        read_recorded_run(recorded, **STRATEGIES)


def test_the_command_refuses_a_run_without_the_wrapper_before_it_reads_the_environment(
    tmp_path, monkeypatch, capsys
):
    for name in STORE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    recorded = recorded_run(
        tmp_path,
        lambda clock: [],
        Configuration(**(vars(WARM_UP) | {"wrapper": False})),
    )

    assert baseline_module.main([str(recorded), "--runs", str(tmp_path / "b")]) == 2

    assert "uc2-unwrapped" in capsys.readouterr().err
    assert not (tmp_path / "b").exists()


def test_a_recorded_login_is_not_replayed_and_the_store_sees_the_baseline_s_own(
    tmp_path, monkeypatch
):
    for name in STORE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRIPLESTORE_URL", "http://graphdb.test")
    monkeypatch.setenv("TRIPLESTORE_USERNAME", "baseline-user-k3")
    monkeypatch.setenv("TRIPLESTORE_PASSWORD", "baseline-secret-w8")
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            login(clock, 1.0),
            read(clock, 2.0, "SELECT ?before"),
            login(clock, 31.0),
            read(clock, 32.0, "SELECT ?in_the_window"),
        ],
    )
    baseline = Baseline(tmp_path, login=store_login())

    record = baseline.run(recorded)

    store = baseline.replay_store
    assert [json.loads(body) for body in store.logins] == [
        {"username": "baseline-user-k3", "password": "baseline-secret-w8"}
    ]
    assert store.texts() == ["SELECT ?before", "SELECT ?in_the_window"]
    assert all(a.authorization == ReplayStore.TOKEN for a in store.arrived)
    replay = record["replay"]
    assert replay["logins_left_out"] == 2
    assert (replay["before_the_window"], replay["in_the_window"]) == (1, 1)
    written = (baseline.runs / record["run_id"] / "record.json").read_text()
    assert "baseline-secret-w8" not in written
    assert "baseline-user-k3" not in written


def test_the_replay_s_login_falls_back_to_the_deprecated_variables(monkeypatch):
    for name in STORE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRIPLESTORE_URL", "http://graphdb.test")
    monkeypatch.setenv("GRAPHDB_USERNAME", "old-user")
    monkeypatch.setenv("GRAPHDB_PASSWORD", "old-secret")

    with pytest.warns(
        DeprecationWarning, match="is deprecated; set TRIPLESTORE_"
    ) as caught:
        found = store_login()

    assert sorted(str(warning.message) for warning in caught) == [
        "GRAPHDB_PASSWORD is deprecated; set TRIPLESTORE_PASSWORD, which replaces it.",
        "GRAPHDB_USERNAME is deprecated; set TRIPLESTORE_USERNAME, which replaces it.",
    ]

    assert (found.url, found.username, found.password) == (
        "http://graphdb.test",
        "old-user",
        "old-secret",
    )
    assert "old-secret" not in repr(found)


def test_without_a_configured_store_the_replay_names_what_is_missing(monkeypatch):
    for name in STORE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GRAPHDB_URL", "http://graphdb.test")

    with pytest.raises(
        ReplayRefused, match="TRIPLESTORE_USERNAME, TRIPLESTORE_PASSWORD"
    ):
        store_login()


def test_the_replay_s_own_modules_import_neither_ksm_nor_the_ogm_nor_the_interface():
    """Imported alone in a fresh interpreter, the replay loads none of them: every replayed
    request goes from its own HTTP client straight to the store."""
    checked = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, jms_kapps.evaluation.replay; "
                "print(sorted({m.split('.')[0] for m in sys.modules} & "
                "{'kapps_semantic_middleware', 'kapps_ogm', 'kapps_triplestore_interface'}))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TRIPLESTORE_", "GRAPHDB_"))
        },
    )

    assert checked.stdout.strip() == "[]"


def test_no_replayed_request_goes_through_the_interface_and_the_preparation_is_not_measured(
    tmp_path,
):
    """The preparation, the reset and the seed through part 1's bench graph, writes through its
    client of the interface (a stand-in here); the replayed requests reach the store through the
    replay's own client alone."""
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 2.0, "SELECT ?before"),
            write(clock, 32.0, "INSERT DATA { <s> <p> <in_the_window> }"),
        ],
    )
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    through_the_interface = [str(e) for e in baseline.events if e[0] == "write"]
    assert through_the_interface
    for text in ("SELECT ?before", "INSERT DATA { <s> <p> <in_the_window> }"):
        assert not any(text in event for event in through_the_interface)
    assert baseline.replay_store.texts() == [
        "SELECT ?before",
        "INSERT DATA { <s> <p> <in_the_window> }",
    ]
    assert [(r["text"], r["window"]) for r in record["measures"]["store_requests"]] == [
        ("SELECT ?before", False),
        ("INSERT DATA { <s> <p> <in_the_window> }", True),
    ]
    assert record["measures"]["store_requests_in_window"] == 1
    prepared = record["actions"][
        [a["action"] for a in record["actions"]].index("store prepared")
    ]
    first = [
        a for a in record["actions"] if a["action"] == "requests before the window sent"
    ]
    assert prepared["seconds"] <= first[0]["seconds"]


def test_the_new_repository_has_the_recorded_run_s_store_and_every_request_goes_to_it(
    tmp_path,
):
    """A recorded run with SHACL off, history collected and a prefill file: the baseline's
    repository is the twin, seeded with the recorded topology, loaded with the same prefill file
    and then collecting history; every replayed request goes to it, whatever repository the
    recording names."""
    prefill = tmp_path / "prefill.ttl"
    write_prefill(prefill, topology="cross", size=25_000, seed=7)
    configuration = Configuration(
        **(
            vars(WARM_UP)
            | {"shacl": False, "collect_history": True, "prefill": prefill}
        )
    )
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 2.0, "SELECT ?before"),
            at_path(
                write(clock, 31.0, "INSERT DATA { <a> <b> <c> }"),
                "repositories/jms-kapps/statements",
            ),
            at_path(read(clock, 32.0, "SELECT ?elsewhere"), "repositories/Tests"),
        ],
        configuration,
    )
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    assert record["outcome"] == "completed", record["outcome"]
    repository = record["repository"]
    assert (
        repository.startswith("eval-")
        and repository
        != json.loads((recorded / "record.json").read_text())["repository"]
    )
    # The store: the SHACL-off twin, the recorded topology, the prefill file, then the history.
    assert "ShaclSail" not in record["server"]["repository_config"]["sent"]
    assert baseline.graphs == [(repository, prefill, False)]
    [reset] = [e for e in baseline.events if e[:2] == ("write", "reset")]
    assert reset[2] == shipped_topology("cross")
    assert reset[3]["with_shapes"] is False
    imports = [e[2] for e in baseline.events if e[:2] == ("write", "import")]
    assert imports == list(
        transactions(prefill, statements=PREFILL_TRANSACTION_STATEMENTS)
    )
    history = baseline.events.index_of("write", "query", ENABLE_HISTORY)
    assert baseline.events.index(("write", "import", imports[-1])) < history
    assert (
        record["prefill"]["sha256"]
        == json.loads((recorded / "record.json").read_text())["prefill"]["sha256"]
    )
    # Every replayed request goes to the baseline's repository.
    paths = [a.path for a in baseline.replay_store.arrived]
    assert paths == [
        f"repositories/{repository}",
        f"repositories/{repository}/statements",
        f"repositories/{repository}",
    ]
    assert [r["path"] for r in record["measures"]["store_requests"]] == paths
    # As the triple-store interface sends them: a query and an update.
    assert [a.content_type for a in baseline.replay_store.arrived] == [
        "application/sparql-query",
        "application/sparql-update",
        "application/sparql-query",
    ]
    # The repository is deleted at the end, and the REST API was sent no other's name.
    assert repository not in baseline.store.repositories
    assert ("repository deleted", repository) in baseline.events
    others = other_repositories_named(record["server"]["requests"], repository)
    assert others and all(r.startswith("GET rest/monitor/repository/") for r in others)


def test_the_record_holds_the_round_trip_and_part_1_s_facts(tmp_path):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    assert record["client"]["round_trip_ms"] and record["client"]["logical_cpus"]
    assert record["client"]["round_trip_median_ms"] is not None
    assert record["software"]["jms-kapps"]["version"]
    assert record["software"]["python"]["version"]
    server = record["server"]
    assert server["url"] == "http://graphdb.test"
    assert server["version"]["answer"]["productVersion"] == "10.8.5"
    assert server["repository_config"]["ruleset"] == "rdfsplus-optimized"
    for view in (
        "repositories_at_the_start",
        "resources_at_the_start",
        "resources_at_the_window_start",
        "repository_monitor_at_the_window_start",
        "repositories_at_the_window_start",
        "resources_at_the_drain_end",
        "repository_monitor_at_the_drain_end",
        "repositories_at_the_drain_end",
    ):
        assert server[view]["status"] == 200, view
    assert record["iteration"] == "iteration one"
    assert record["run_settings"]["request_timeout_seconds"] == 60.0
    assert record["run_settings"]["transit_seconds"] == 3.0


def test_the_baseline_samples_the_client_host_s_load_like_a_run(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu  10 0 10 80 0 0 0 0 0 0\n", encoding="utf-8")
    (proc / "meminfo").write_text(
        "MemTotal: 8000 kB\nMemAvailable: 2000 kB\n", encoding="utf-8"
    )
    monkeypatch.setattr(client_load, "PROC", proc)
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])

    record = Baseline(tmp_path).run(recorded)

    assert record["run_settings"]["client_load_interval_seconds"] == 1.0
    load = record["client_load"]
    assert load["status"] == "sampled"
    assert load["interval_seconds"] == 1.0
    assert load["samples"]
    assert load["memory_total_bytes"] == 8000 * 1024


def test_the_baseline_on_a_host_without_the_figures_says_not_available(
    tmp_path, monkeypatch
):
    empty = tmp_path / "no-proc"
    empty.mkdir()
    monkeypatch.setattr(client_load, "PROC", empty)
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])

    record = Baseline(tmp_path).run(recorded)

    assert record["outcome"] == "completed"
    assert record["client_load"]["status"] == "not available"


def test_the_readme_says_how_to_make_a_baseline_run():
    root = find_repo_root(Path(__file__))
    if root is None:
        pytest.skip("an installed copy has no README of the evaluation folder")
    readme = (root / "src/jms_kapps/evaluation/README.md").read_text(encoding="utf-8")

    assert "## The store-only baseline" in readme
    assert "uv run jms-eval-baseline runs/" in readme
    [script] = [
        e
        for e in entry_points(group="console_scripts")
        if e.name == "jms-eval-baseline"
    ]
    assert script.value == "jms_kapps.evaluation.baseline:main"


# Beside the criteria.


def test_a_heavy_recorded_run_needs_the_approval_and_nothing_is_sent_without_it(
    tmp_path,
):
    """No test gives an approval that covers a real spec: the recorded run is made heavy in its
    record, as a heavy run under an approval would have recorded it."""
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    record = json.loads((recorded / "record.json").read_text())
    record["configuration"] = record["configuration"].replace(
        "heavy = false", "heavy = true"
    )
    (recorded / "record.json").write_text(json.dumps(record))
    baseline = Baseline(tmp_path)

    with pytest.raises(HeavyRunRefused, match="any person may approve"):
        baseline.run(recorded)

    assert baseline.store.requests == []
    assert baseline.replay_store.arrived == []
    assert not baseline.runs.exists()


def test_a_prefill_file_that_is_not_the_recorded_one_is_refused(tmp_path):
    prefill = tmp_path / "prefill.ttl"
    write_prefill(prefill, topology="cross", size=25_000, seed=7)
    recorded = recorded_run(
        tmp_path,
        lambda clock: [],
        Configuration(**(vars(WARM_UP) | {"prefill": prefill})),
    )
    write_prefill(prefill, topology="cross", size=25_000, seed=8)

    with pytest.raises(BaselineRefused, match="not the one the run"):
        read_recorded_run(recorded, **STRATEGIES)


def test_a_run_that_did_not_complete_is_refused(tmp_path):
    folder = recorded_run(tmp_path, lambda clock: [])
    record = json.loads((folder / "record.json").read_text())
    record |= {
        "outcome": "failed: RuntimeError: the WMS did not answer",
        "measures": None,
    }
    (folder / "record.json").write_text(json.dumps(record))

    with pytest.raises(BaselineRefused, match="did not complete"):
        read_recorded_run(folder, **STRATEGIES)


def test_the_repository_is_deleted_after_a_failed_baseline(tmp_path):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    baseline.replay_store.handle = refuse  # type: ignore[method-assign]

    record = baseline.run(recorded)

    assert record["outcome"].startswith("failed: ReplayRefused")
    assert baseline.store.created() == []
    assert record["actions"][-2]["action"] == "repository deleted"
    assert (baseline.runs / record["run_id"] / "record.json").is_file()


def test_a_request_the_replay_cannot_send_fails_the_run_and_is_no_timeout(tmp_path):
    """A fault of the replay's own is no answer of the store: the run fails, and says which
    request, and its repository is deleted all the same."""
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])

    def fault(clock: ReplayClock, arrival: Arrival) -> httpx.Response:
        raise ValueError("a fault of the replay's own")

    baseline = Baseline(tmp_path, answers={"SELECT ?x": fault})

    record = baseline.run(recorded)

    assert record["outcome"].startswith(
        "failed: RuntimeError: the replay could not send request 0 of the window"
    )
    assert "a fault of the replay's own" in record["outcome"]
    assert record["measures"] is None
    assert baseline.store.created() == []


def test_the_guard_refuses_a_repository_that_is_not_a_run_s_own():
    for repository in ("jms-kapps", "Tests", "kapps-demo", "production"):
        with pytest.raises(StoreRefused):
            retargeted("repositories/eval-1/statements", repository)
    assert (
        retargeted("repositories/jms-kapps/statements", "eval-1")
        == "repositories/eval-1/statements"
    )
    assert retargeted("rest/repositories/Tests", "eval-1") == "rest/repositories/eval-1"


# The server watch: the baseline gets the same watch as a UC2 run, from the
# window's start to the drain's end.


def test_the_baseline_samples_the_server_through_the_window_and_records_the_same_watch(
    tmp_path,
):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded, watch_interval_seconds=5.0)

    assert record["outcome"] == "completed", record["outcome"]
    watch = record["server"]["watch"]
    began = next(a for a in record["actions"] if a["action"] == "window began")
    assert record["run_settings"]["server_watch_interval_seconds"] == 5.0
    assert watch["interval_seconds"] == 5.0
    assert watch["samples"][0]["at"] == began["at"]
    # The measured window lasts 60 s: a sample at its start, then one every 5 s up to its end.
    assert watch["sample_count"] == 13
    assert {s["resources"]["status"] for s in watch["samples"]} == {200}
    assert {s["round_trip"]["status"] for s in watch["samples"]} == {200}
    requests = record["server"]["requests"]
    assert requests.count("GET rest/monitor/infrastructure") == 3 + 13
    assert requests.count("GET rest/info/version") == 1 + 1 + 5 + 13


def test_a_refused_watch_read_does_not_stop_the_baseline(tmp_path):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)
    baseline.store.refusing = {"rest/monitor/infrastructure": 403}

    record = baseline.run(recorded, watch_interval_seconds=5.0)

    assert record["outcome"] == "completed", record["outcome"]
    assert {s["resources"]["status"] for s in record["server"]["watch"]["samples"]} == {
        403
    }


def test_the_baseline_records_the_counters_the_statement_counts_and_the_license(
    tmp_path,
):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    server = record["server"]
    assert set(server["repository_monitors_at_the_start"]) == {
        "jms-kapps",
        "kapps-demo",
        "Tests",
    }
    assert set(server["repository_monitors_at_the_drain_end"]) == {
        "jms-kapps",
        "kapps-demo",
        "Tests",
        record["repository"],
    }
    assert server["structures_at_the_window_start"]["status"] == 200
    assert server["structures_at_the_drain_end"]["status"] == 200
    assert server["license"]["answer"] == {
        "productType": "enterprise",
        "maxCpuCores": 8,
    }
    names = [a["action"] for a in record["actions"]]
    counts = [i for i, n in enumerate(names) if n == "statement counts read"]
    assert (
        names.index("repository created")
        < names.index("store prepared")
        < counts[0]
        < names.index("window began")
        < names.index("drain ended")
        < counts[1]
        < names.index("repository deleted")
    )
    for key in (
        "statements_before_the_participants",
        "statements_after_the_participants",
    ):
        assert server[key]["answer"] == {"explicit": 7, "inferred": 3, "total": 10}


def _transit_of_the_store_preparation(baseline: Baseline) -> float:
    [reset] = [e for e in baseline.events if e[:2] == ("write", "reset")]
    return reset[3]["transit_seconds"]


def test_the_baseline_reads_the_transit_time_from_the_recorded_configuration(tmp_path):
    """A run of the "after" series holds its transit time in its configuration."""
    configuration = Configuration(**(vars(WARM_UP) | {"transit_seconds": 2.5}))
    recorded = recorded_run(
        tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")], configuration
    )
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    assert record["outcome"] == "completed", record["outcome"]
    assert _transit_of_the_store_preparation(baseline) == 2.5


def _as_the_before_series_wrote_it(recorded: Path, run_settings: dict) -> None:
    """The recorded run's record as the "before" series holds it: a configuration with neither
    the transit time nor the retry wait, and those under `run_settings`."""
    path = recorded / "record.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["configuration"] = "".join(
        line + "\n"
        for line in record["configuration"].splitlines()
        if not line.startswith(("transit_seconds", "retry_seconds", "wms_pick_seed"))
    )
    record["run_settings"] = run_settings
    path.write_text(json.dumps(record), encoding="utf-8")


def test_the_baseline_still_reads_the_transit_time_of_a_record_of_the_before_series(
    tmp_path,
):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    _as_the_before_series_wrote_it(
        recorded, {"transit_seconds": 4.0, "retry_seconds": 1.5}
    )
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    assert record["outcome"] == "completed", record["outcome"]
    assert _transit_of_the_store_preparation(baseline) == 4.0
    assert "transit_seconds" not in record["configuration"]


def test_a_record_of_the_before_series_without_run_settings_takes_the_use_case_s_default(
    tmp_path,
):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    _as_the_before_series_wrote_it(recorded, {})
    baseline = Baseline(tmp_path)

    baseline.run(recorded)

    assert _transit_of_the_store_preparation(baseline) == 3.0
