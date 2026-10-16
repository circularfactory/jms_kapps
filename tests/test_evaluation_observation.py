"""The observation of a UC2 run of the measurement harness, part 2 (jms_kapps 48): from the spans of
its participants, the completion matcher's reads and each participant's stamped output, a run's
record gets its measures, and a run that fails a check is marked invalid, with the check.

Each run here is part 1's run against the stand-ins of `_evaluation_fakes`: a GraphDB's REST API in
memory, a bench graph whose transports end at the times a test gives them, a supervisor that starts
no process but writes the spans a test gives each participant into the file its wrapper would
write, and a clock that moves only when the run waits. The spans are those the wrapper's hooks
write for a participant's store requests and commits (`test_evaluation_wrapper.py` shows the real
hooks against a stand-in store). Nothing reaches a store, a process or a network.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from _evaluation_fakes import FakeClock, FakeSupervisor, commit_span, store_span
from test_evaluation_bench import CROSS, Bench

from jms_kapps.evaluation import wrapper
from jms_kapps.evaluation.bench import TransportEnd
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.observation import (
    DEADLOCK_ALARM,
    DONE_AFTER_RUNNING,
    MATCHER_SAW_THE_ENDS,
    ONE_END,
    ONE_RUNNING,
    UNWRAPPED_CHECKS,
    WRAPPED_CHECKS,
    matcher_check,
    status_write_checks,
)
from jms_kapps.evaluation.record import UNWRAPPED_RUN_KIND
from jms_kapps.evaluation.store_requests import HEAP_REFUSAL
from jms_kapps.uc2.module import POSSIBLE_DEADLOCK

# A run with a warm-up of 30 s and a measured window of 60 s: the load starts at 0 s on the run's
# clock, the window from 30 s to 90 s.
WARM_UP = Configuration(**(vars(CROSS) | {"warm_up_seconds": 30.0}))

SHACL_REASON = "_:r <http://www.w3.org/ns/shacl#conforms> false ."
HEAP_REASON = f"{HEAP_REFUSAL} 249Mb for group by and distinct"

# The run's transports after the drain, and when the graph shows each ended, in the clock's
# seconds: one in the warm-up, one in the window, and one that the WarehouseManagementSystem
# ends `failed` while it is still queued.
ENDS = [
    TransportEnd("Box1_transport", "done", "WarehouseManagementSystem1", None),
    TransportEnd("Box2_transport", "done", "WarehouseManagementSystem1", None),
    TransportEnd("Box3_transport", "failed", "Module1", "no live route"),
]
ENDED_AT = {"Box1_transport": 20.0, "Box2_transport": 50.0, "Box3_transport": 60.0}


def checked_write(
    clock: FakeClock,
    participant: str,
    at: float,
    transport: str,
    status: str,
    *,
    receipt: str,
    commit_seconds: float = 0.2,
) -> list[dict]:
    """A commit of `status` for `transport` that the store accepts and applies: the commit, its
    update with its receipt, and the query that finds the receipt."""
    return [
        commit_span(clock, participant, at, commit_seconds),
        store_span(
            clock,
            participant,
            at + 0.01,
            0.05,
            text=f"INSERT {{ {transport} {status} }}",
            writes=[(transport, status)],
            receipt_written=receipt,
        ),
        store_span(
            clock,
            participant,
            at + 0.07,
            0.01,
            kind="read",
            text=f"ASK {{ {receipt} }}",
            receipt_asked=receipt,
            receipt_found=True,
        ),
    ]


def module_spans(clock: FakeClock, *, drop: str = "", add: str = "") -> list[dict]:
    """What Module1's wrapper writes in the run: two transports carried, a refused and retried
    `running`, a refusal for want of heap and a request that got no answer. `drop` leaves out
    the accepted `done` of Box2; `add` adds a second accepted `running` of Box2, or moves it
    after its `done`."""
    spans = [
        # The warm-up: Box1 runs and ends.
        *checked_write(
            clock, "Module1", 5.0, "Box1_transport", "running", receipt="r1"
        ),
        *checked_write(clock, "Module1", 20.0, "Box1_transport", "done", receipt="r2"),
        # The window: Box2's running, refused once and sent again.
        commit_span(clock, "Module1", 35.0, 0.1, error="ShaclValidationError: refused"),
        store_span(
            clock,
            "Module1",
            35.01,
            0.04,
            text="INSERT { Box2_transport running }",
            writes=[("Box2_transport", "running")],
            receipt_written="r3",
            outcome="refused",
            refusal="shacl",
            reason=SHACL_REASON,
        ),
        *checked_write(
            clock,
            "Module1",
            55.0 if add == "late running" else 36.0,
            "Box2_transport",
            "running",
            receipt="r4",
            commit_seconds=0.3,
        ),
        store_span(
            clock,
            "Module1",
            40.0,
            0.02,
            kind="read",
            text="SELECT DISTINCT ?s WHERE { ?s ?p ?o }",
            outcome="refused",
            refusal="heap",
            reason=HEAP_REASON,
        ),
        store_span(
            clock,
            "Module1",
            45.0,
            1.0,
            kind="read",
            text="ASK { ?late ?answer ?here }",
            outcome=None,
            error="ReadTimeout: read timed out",
        ),
        # After the window, in the drain: not counted.
        store_span(clock, "Module1", 95.0, 0.01, kind="read", text="ASK {}"),
    ]
    if drop != "done":
        spans += checked_write(
            clock,
            "Module1",
            50.0,
            "Box2_transport",
            "done",
            receipt="r5",
            commit_seconds=0.25,
        )
    if add == "second running":
        spans += checked_write(
            clock, "Module1", 37.0, "Box2_transport", "running", receipt="r6"
        )
    return spans


def wms_spans(clock: FakeClock) -> list[dict]:
    """The WarehouseManagementSystem ends Box3's transport `failed` while it is still queued."""
    return checked_write(
        clock,
        "WarehouseManagementSystem1",
        60.0,
        "Box3_transport",
        "failed",
        receipt="r7",
        commit_seconds=0.15,
    )


def observed_run(
    tmp_path: Path,
    configuration: Configuration = WARM_UP,
    *,
    drop: str = "",
    add: str = "",
    output_at: list | None = None,
    **graph: object,
) -> tuple[Bench, dict]:
    bench = Bench(
        tmp_path,
        ends=ENDS,
        ended_at=ENDED_AT,
        output_at=output_at or [],
        **graph,
    )
    bench.spans = {
        "Module1": module_spans(bench.clock, drop=drop, add=add),
        "WarehouseManagementSystem1": wms_spans(bench.clock),
    }
    return bench, bench.run(configuration)


def test_each_measure_of_a_run_with_a_warm_up_counts_the_window_alone(tmp_path):
    """The window runs from 30 s to 90 s on the run's clock: one minute."""
    bench, record = observed_run(tmp_path)

    assert record["outcome"] == "completed"
    measures = record["measures"]
    clock = bench.clock
    assert measures["window"] == {
        "start": clock.at_seconds(30.0).isoformat(),
        "end": clock.at_seconds(90.0).isoformat(),
        "minutes": 1.0,
    }
    # Every store request of the run is kept, the warm-up's and the drain's too.
    requests = measures["store_requests"]
    assert len(requests) == 14
    assert [r["window"] for r in requests].count(True) == 9
    assert measures["store_requests_in_window"] == 9
    assert measures["refused_in_window"] == 2
    assert measures["timed_out_in_window"] == 1
    assert measures["refused_or_unanswered_share"] == pytest.approx(3 / 9)
    assert measures["completed_writes_in_window"] == 3
    assert measures["completed_writes_per_minute"] == pytest.approx(3.0)
    assert measures["heap_refusals"] == [
        {
            "participant": "Module1",
            "at": clock.at_seconds(40.0).isoformat(),
            "reason": HEAP_REASON,
            "window": True,
        }
    ]
    assert measures["heap_refusals_in_window"] == 1
    # The latencies of the commits the store admitted; every commit is kept with its outcome.
    assert measures["commit_latencies_seconds"] == pytest.approx([0.3, 0.25, 0.15])
    assert [c["outcome"] for c in measures["commits"] if c["window"]] == [
        "ShaclValidationError",
        "accepted",
        "accepted",
        "accepted",
    ]
    # Box2's end at 50 s and Box3's at 60 s are in the window; Box1's at 20 s is not.
    assert measures["transports_ended_in_window"] == 2
    assert measures["transports_ended_per_minute"] == pytest.approx(2.0)
    assert record["valid"] is True
    assert record["failed_checks"] == []
    assert record["checks_made"] == list(WRAPPED_CHECKS)


def test_each_store_request_has_its_time_latency_size_and_outcome(tmp_path):
    bench, record = observed_run(tmp_path)

    requests = record["measures"]["store_requests"]
    by_time = {request["at"]: request for request in requests}
    clock = bench.clock
    refused = by_time[clock.at_seconds(35.01).isoformat()]
    assert refused["participant"] == "Module1"
    assert refused["seconds"] == pytest.approx(0.04)
    assert refused["request_bytes"] == len(refused["text"].encode())
    assert refused["outcome"] == "refused"
    assert (refused["refusal"], refused["reason"]) == ("shacl", SHACL_REASON)
    assert refused["writes"] == [
        {"operation": "Box2_transport", "box": None, "status": "running"}
    ]
    assert refused["applied"] is False
    timed_out = by_time[clock.at_seconds(45.0).isoformat()]
    assert timed_out["outcome"] == "timed out"
    assert timed_out["seconds"] == pytest.approx(1.0)
    assert "ReadTimeout" in timed_out["reason"]
    accepted = by_time[clock.at_seconds(36.01).isoformat()]
    assert (accepted["outcome"], accepted["applied"]) == ("accepted", True)
    assert accepted["text"] == "INSERT { Box2_transport running }"
    # The warm-up's requests are kept, with their text, for the store-only baseline's replay.
    warm_up = [
        r for r in requests if not r["window"] and r["at"] < record["window"]["start"]
    ]
    assert len(warm_up) == 4 and all(r["text"] for r in warm_up)


def test_the_matcher_reads_from_the_warm_up_s_start_and_a_transport_that_ends_in_it_fails_no_check(
    tmp_path,
):
    bench, record = observed_run(tmp_path)

    matcher = record["matcher"]
    clock = bench.clock
    assert matcher["interval_seconds"] == 2.0
    assert matcher["reads"][0]["at"] == clock.at_seconds(2.0).isoformat()
    seen = {end["transport"]: end for end in matcher["ends"]}
    assert seen["Box1_transport"] == {
        "transport": "Box1_transport",
        "status": "done",
        "place": "WarehouseManagementSystem1",
        "seen_at": clock.at_seconds(20.0).isoformat(),
    }
    assert seen["Box1_transport"]["seen_at"] < record["warm_up"]["end"]
    assert record["valid"] is True


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ({"drop": "done"}, ONE_END),
        ({"add": "second running"}, ONE_RUNNING),
        ({"add": "late running"}, DONE_AFTER_RUNNING),
    ],
    ids=[
        "a dropped accepted done",
        "two accepted running",
        "a done before its running",
    ],
)
def test_a_run_that_breaks_a_status_rule_is_invalid_and_its_record_names_the_check(
    tmp_path, change, check
):
    _, record = observed_run(tmp_path, **change)

    assert record["outcome"] == "completed"
    assert record["valid"] is False
    [failed] = record["failed_checks"]
    assert failed["check"] == check
    assert failed["transport"] == "Box2_transport"
    assert {write["status"] for write in failed["accepted"]} <= {"running", "done"}


def test_a_refused_and_retried_running_a_failed_queued_transport_and_a_stuck_one_are_valid(
    tmp_path,
):
    """Box2's `running` is refused once and sent again, Box3 ends `failed` while it is queued,
    and a transport still running after the drain counts as stuck, with its place."""
    _, record = observed_run(tmp_path, open_reads=None)

    assert record["valid"] is True
    assert record["failed_checks"] == []
    assert record["stuck"] == [
        {"transport": "Transport99", "status": "running", "place": "Module3"}
    ]


def alarm_line(module: str = "Module3") -> str:
    """A deadlock alarm as a module writes it, in the participants' log format: Python's default
    format, the level and the logger before the message."""
    record = logging.LogRecord(
        "jms_kapps.uc2.module",
        logging.WARNING,
        __file__,
        0,
        "%s %s",
        (
            POSSIBLE_DEADLOCK,
            f"{module} has waited 300 s to hand Box2 on, asking Module4 and Module5",
        ),
        None,
    )
    return logging.Formatter(logging.BASIC_FORMAT).format(record)


def test_a_deadlock_alarm_during_the_run_marks_its_record_useless(tmp_path):
    line = alarm_line()
    bench, record = observed_run(tmp_path, output_at=[(40.0, "Module3", line)])

    assert line.startswith("WARNING:jms_kapps.uc2.module:possible deadlock: Module3")
    assert record["valid"] is False
    assert record["failed_checks"] == [
        {
            "check": DEADLOCK_ALARM,
            "module": "Module3",
            "line": line,
            "at": bench.clock.at_seconds(40.0).isoformat(),
        }
    ]
    assert "asking Module4 and Module5" in record["failed_checks"][0]["line"]


def test_a_deadlock_alarm_in_the_warm_up_marks_the_run_too(tmp_path):
    _, record = observed_run(
        tmp_path, output_at=[(10.0, "Module2", alarm_line("Module2"))]
    )

    assert record["valid"] is False
    assert [(f["check"], f["module"]) for f in record["failed_checks"]] == [
        (DEADLOCK_ALARM, "Module2")
    ]


def test_an_alarm_line_before_the_load_s_start_does_not_mark_the_run(
    tmp_path, monkeypatch
):
    """Written as the module starts, 5 s before the spawn rate is set: the readiness gate takes
    that long."""
    ready = FakeSupervisor.wait_until_ready

    def slow_gate(self: FakeSupervisor) -> None:
        assert self.clock is not None
        self.clock.sleep(5.0)
        ready(self)

    monkeypatch.setattr(FakeSupervisor, "wait_until_ready", slow_gate)

    _, record = observed_run(tmp_path, output_at=[(None, "Module3", alarm_line())])

    assert record["valid"] is True
    assert record["failed_checks"] == []


def test_a_line_that_only_mentions_a_deadlock_is_no_alarm(tmp_path):
    """Only a log message that starts with the alarm counts."""
    _, record = observed_run(
        tmp_path,
        output_at=[
            (40.0, "Module3", "INFO:jms_kapps.uc2.module:no possible deadlock: here")
        ],
    )

    assert record["valid"] is True


def test_with_the_wrapper_off_the_run_has_no_spans_and_only_the_measures_that_need_none(
    tmp_path,
):
    configuration = Configuration(**(vars(WARM_UP) | {"wrapper": False}))
    bench, record = observed_run(
        tmp_path, configuration, output_at=[(40.0, "Module3", alarm_line())]
    )

    folder = bench.folder(record)
    assert not (folder / "spans").exists()
    [supervisor] = bench.supervisors
    for _, _, command in supervisor.spawned:
        assert "jms_kapps.evaluation.wrapper" not in command
        assert "--spans" not in command
    assert record["run_kind"] == UNWRAPPED_RUN_KIND == "uc2-unwrapped"
    assert set(record["measures"]) == {
        "window",
        "transports_ended_in_window",
        "transports_ended_per_minute",
        # The spawns of the window need no wrapper (jms_kapps 99).
        "spawns_offered_in_window",
        "spawns_accepted_in_window",
        "spawns_accepted_share",
    }
    assert record["measures"]["transports_ended_per_minute"] == pytest.approx(2.0)
    assert record["matcher"]["read_count"] > 0
    assert record["transports"]["done"] and record["spawns"] is not None
    assert record["checks_made"] == list(UNWRAPPED_CHECKS)
    # The output is stamped also with the wrapper off: the alarm is dated and counts.
    assert [f["check"] for f in record["failed_checks"]] == [DEADLOCK_ALARM]


def test_a_run_that_did_not_complete_has_no_measures(tmp_path):
    bench = Bench(tmp_path, not_ready="Module1 is failed")

    record = bench.run(WARM_UP)

    assert record["outcome"].startswith("failed")
    assert record["measures"] is None and record["valid"] is None


def test_a_done_without_an_accepted_running_breaks_no_status_rule():
    """At most one `running`, so none is valid too: a `running` whose answer timed out is not
    known to be accepted."""
    graph = {"done": [{"transport": "Box4_transport"}], "failed": [], "open": []}
    running = {
        "outcome": "timed out",
        "applied": False,
        "participant": "Module1",
        "at": "2026-10-11T12:00:40+00:00",
        "seconds": 60.0,
        "writes": [{"operation": "Box4_transport", "box": None, "status": "running"}],
    }
    done = running | {
        "outcome": "accepted",
        "applied": True,
        "at": "2026-10-11T12:01:50+00:00",
        "seconds": 0.05,
        "writes": [{"operation": "Box4_transport", "box": None, "status": "done"}],
    }

    assert status_write_checks(graph, [running, done]) == []


def test_the_matcher_and_the_graph_must_agree_on_the_ended_transports():
    reads = [{"at": "t1"}, {"at": "t2"}]
    graph = {
        "done": [{"transport": "A"}, {"transport": "B"}],
        "failed": [],
        "open": [{"transport": "C"}, {"transport": "D"}],
    }
    seen = [
        {"transport": "A", "seen_at": "t1"},
        # Ended just after the graph's read at the drain's end: the matcher's last read saw it.
        {"transport": "C", "seen_at": "t2"},
        # Seen ended earlier, but open after the drain: its status went back.
        {"transport": "D", "seen_at": "t1"},
    ]

    assert matcher_check({"reads": reads, "ends": seen}, graph) == [
        {
            "check": MATCHER_SAW_THE_ENDS,
            "seen_by_the_matcher_alone": ["D"],
            "shown_by_the_graph_alone": ["B"],
        }
    ]
    agreeing = [
        {"transport": "A", "seen_at": "t1"},
        {"transport": "B", "seen_at": "t2"},
    ]
    assert matcher_check({"reads": reads, "ends": agreeing}, graph) == []


def test_a_run_whose_matcher_misses_an_end_is_invalid(tmp_path, monkeypatch):
    """A matcher that reads only `done` misses Box3's `failed`."""
    graph_for = Bench.graph_for

    def done_only(self, repository, prefill, with_shapes):
        graph = graph_for(self, repository, prefill, with_shapes)
        read = graph.ended_transports_named
        graph.ended_transports_named = lambda names: [  # type: ignore[method-assign]
            end for end in read(names) if end.status == "done"
        ]
        return graph

    monkeypatch.setattr(Bench, "graph_for", done_only)
    _, record = observed_run(tmp_path)

    assert record["valid"] is False
    assert record["failed_checks"] == [
        {
            "check": MATCHER_SAW_THE_ENDS,
            "seen_by_the_matcher_alone": [],
            "shown_by_the_graph_alone": ["Box3_transport"],
        }
    ]


def test_without_opentelemetry_a_run_with_the_wrapper_creates_nothing(
    tmp_path, monkeypatch
):
    """A plain install of jms_kapps has no OpenTelemetry, the extra `evaluation`."""
    monkeypatch.setattr(wrapper, "missing", lambda: ["opentelemetry.sdk"])
    bench = Bench(tmp_path)

    with pytest.raises(wrapper.WrapperUnavailable, match="uv sync --extra evaluation"):
        bench.run(WARM_UP)

    assert bench.store.requests == []
    assert not bench.runs.exists()

    record = bench.run(Configuration(**(vars(WARM_UP) | {"wrapper": False})))
    assert record["outcome"] == "completed"


SPAWNED = "INFO:jms_kapps.uc2.wms:WarehouseManagementSystem1: spawned Box-{n} onto Module1, for Module2"
REFUSED = (
    "INFO:jms_kapps.uc2.wms:WarehouseManagementSystem1: spawn refused: every origin has a spawn "
    "under way"
)


def test_the_offered_and_accepted_spawns_are_counted_in_the_window_by_the_lines_time_stamps(
    tmp_path,
):
    """jms_kapps 99, interim names. The window runs from 30 s to 90 s: a spawn in the warm-up, one
    in the drain and a line with no stamp are none of its spawns. A box is named by a UUID, and no
    number is read from its name."""
    wms = "WarehouseManagementSystem1"
    box = "7f3c2a10-5b1e-4c55-9a0d-0e1f2a3b4c5d"
    _, record = observed_run(
        tmp_path,
        output_at=[
            (10.0, wms, SPAWNED.format(n=box)),
            (35.0, wms, SPAWNED.format(n=box)),
            (40.0, wms, REFUSED),
            (60.0, wms, SPAWNED.format(n=box)),
            (80.0, wms, REFUSED),
            (95.0, wms, SPAWNED.format(n=box)),
            (None, wms, SPAWNED.format(n=box)),
        ],
    )

    measures = record["measures"]
    assert measures["spawns_offered_in_window"] == 4
    assert measures["spawns_accepted_in_window"] == 2
    assert measures["spawns_accepted_share"] == pytest.approx(0.5)
    # The run-wide count of the record stays: it counts the whole run, the warm-up included.
    assert record["spawns"]["spawned"] > measures["spawns_accepted_in_window"]


def test_a_window_with_no_spawn_has_no_accepted_share(tmp_path):
    _, record = observed_run(tmp_path)

    measures = record["measures"]
    assert measures["spawns_offered_in_window"] == 0
    assert measures["spawns_accepted_in_window"] == 0
    assert measures["spawns_accepted_share"] is None
