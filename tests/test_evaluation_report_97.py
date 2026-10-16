"""The run report groups the runs by hour, marks overlapping activity, counts the deadlock alarms
apart and shows each dead heartbeat (jms_kapps 97, Sören's decisions of 2026-10-12, jms_kapps 85).

The seam is `jms-eval-report` on a runs folder made from fabricated records (`_evaluation_records`)
and from a committed synthetic fixture of spans (`tests/reports/dead-heartbeat`, shaped like the
known case of iteration one, with no raw record in it); the live-check log is a file of the
test's own, never the real one. No store, no network, no run.
"""

from __future__ import annotations

import json
import shutil
import socket
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from _evaluation_records import record, request, store_span, write, write_spans
from _repo import withheld_text

import jms_kapps
from jms_kapps.evaluation import report_points
from jms_kapps.evaluation.report import main
from jms_kapps.evaluation.report_summary import FORMAT, SUMMARY_FILE
from jms_kapps.evaluation.spec import configurations, read_spec, sweep_fields

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"
FIXTURE = Path(__file__).parent / "reports" / "dead-heartbeat"

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 3

# 1-2: a family of two prefill sizes.
[[sweep]]
topology = "cross"
prefill = ["{topology}-1k.ttl", "{topology}-1M.ttl"]
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the report opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def spec_path(tmp_path) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    return path


@pytest.fixture
def by_number(spec_path):
    return {c.number: c for c in configurations(read_spec(spec_path))}


def made(spec_path: Path, runs: Path, *flags: str) -> Path:
    assert (
        main([str(spec_path), "--runs", str(runs), "--resamples", "200", *flags]) == 0
    )
    return runs / "report"


def csv(report: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(report / name)


def section(report: Path, title: str) -> str:
    text = (report / "report.md").read_text(encoding="utf-8")
    return text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def window(start: str, minutes: int = 5) -> dict[str, Any]:
    """A window that starts at `start` (an ISO time) and lasts `minutes`."""
    from datetime import datetime, timedelta

    begin = datetime.fromisoformat(start)
    return {
        "start": begin.isoformat(),
        "end": (begin + timedelta(minutes=minutes)).isoformat(),
        "minutes": float(minutes),
    }


def live_checks(path: Path, *lines: Mapping[str, str] | str) -> Path:
    """A live-check log of the test's own: one JSON object (or one raw line) for each argument."""
    path.write_text(
        "".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines),
        encoding="utf-8",
    )
    return path


def event(kind: str, at: str, check: str = "jms-96", repository: str = "Tests"):
    return {"event": kind, "at": at, "check": check, "repository": repository}


# --- By hour of day ---


def at(start: str, run_id: str, one, **numbers: Any) -> dict[str, Any]:
    return record(run_id, one, window=window(start), **numbers)


def test_a_table_groups_the_valid_runs_of_each_configuration_by_the_utc_hour_of_the_window_start(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    one = by_number[1]
    write(
        runs,
        at(
            "2026-10-12T10:01:00+00:00",
            "a1",
            one,
            transports_ended_per_minute=10.0,
            completed_writes_per_minute=20.0,
            commit_latencies=[1.0, 2.0],
            requests=[request(0.1, kind="read"), request(0.2, kind="write")],
        ),
        # 12:30 in the zone +02:00 is 10:30 UTC: the same hour as the run before.
        at(
            "2026-10-12T12:30:00+02:00",
            "a2",
            one,
            repetition=2,
            transports_ended_per_minute=20.0,
            completed_writes_per_minute=40.0,
            commit_latencies=[3.0, 4.0],
            requests=[request(0.3, kind="read"), request(0.4, kind="write")],
        ),
        at(
            "2026-10-12T23:59:00+00:00",
            "a3",
            one,
            repetition=3,
            transports_ended_per_minute=5.0,
            completed_writes_per_minute=6.0,
            commit_latencies=[9.0],
            requests=[request(0.9, kind="read")],
        ),
        # An invalid run is not among the valid runs of an hour.
        at(
            "2026-10-12T10:40:00+00:00",
            "a4",
            one,
            repetition=4,
            valid=False,
            failed_checks=[{"check": "matcher saw the ends"}],
            transports_ended_per_minute=999.0,
            commit_latencies=[99.0],
        ),
    )

    report = made(spec_path, runs)

    hours = csv(report, "hours.csv")
    # The CSV keeps the number and gains the spec's series (jms_kapps 110).
    assert list(hours.columns) == [
        "configuration",
        "series",
        "run_kind",
        "hour",
        "runs",
        "transports_ended_per_minute",
        "completed_writes_per_minute",
        "commit_p95",
        "store_request_p95",
    ]
    ten, late = hours.iloc[0], hours.iloc[1]
    assert (ten["configuration"], ten["run_kind"], ten["hour"], ten["runs"]) == (
        1,
        "uc2",
        10,
        2,
    )
    assert ten["transports_ended_per_minute"] == 15.0
    assert ten["completed_writes_per_minute"] == 30.0
    # The p95 of the pooled latencies of the hour's valid runs: 1, 2, 3, 4 gives 3.85.
    assert ten["commit_p95"] == pytest.approx(3.85)
    assert ten["store_request_p95"] == pytest.approx(0.385)
    assert (late["hour"], late["runs"]) == (23, 1)
    assert len(hours) == 2
    assert (report / "hours-1.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert not (report / "hours-2.png").exists()
    text = section(report, "By hour of day")
    assert "![" in text and "hours-1.png" in text
    assert "| 10 |" in text and "| 23 |" in text


def test_the_hours_of_a_folder_without_valid_runs_say_none(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(runs, record("c1", by_number[1], outcome="interrupted"))

    report = made(spec_path, runs)

    assert csv(report, "hours.csv").empty
    assert section(report, "By hour of day").strip().endswith("None.")


# --- Overlap with a live check ---


def three_runs(runs: Path, one) -> None:
    write(
        runs,
        at("2026-10-12T10:00:00+00:00", "a1", one),
        at("2026-10-12T11:00:00+00:00", "a2", one, repetition=2),
        at("2026-10-12T12:00:00+00:00", "a3", one, repetition=3),
    )


def test_a_run_is_marked_when_its_window_overlaps_a_paired_start_and_end(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])
    log = live_checks(
        tmp_path / "live.jsonl",
        event("start", "2026-10-12T10:03:00Z"),
        event("end", "2026-10-12T10:20:00Z"),
        # The window of a3 ends at 12:05; this check starts at 12:05 exactly: touching counts.
        event("start", "2026-10-12T12:05:00Z", check="other"),
        event("end", "2026-10-12T12:09:00Z", check="other"),
    )

    report = made(spec_path, runs, "--live-checks", str(log))

    marks = csv(report, "marks.csv").set_index("run")
    assert marks.loc["a1", "live_check"] == "overlap"
    assert marks.loc["a1", "live_checks"] == "jms-96"
    assert marks.loc["a2", "live_check"] == "no overlap"
    assert marks.loc["a3", "live_check"] == "overlap"
    assert marks.loc["a3", "live_checks"] == "other"
    assert "a1" in section(report, "Runs marked")


def test_a_start_pairs_with_the_next_end_of_the_same_check_and_repository(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])
    log = live_checks(
        tmp_path / "live.jsonl",
        event("start", "2026-10-12T09:50:00Z"),
        # Another check and another repository end early; they do not end this start.
        event("end", "2026-10-12T09:55:00Z", check="other"),
        event("end", "2026-10-12T09:56:00Z", repository="jms-kapps"),
        event("end", "2026-10-12T11:30:00Z"),
        # An end with no start before it is no span.
        event("end", "2026-10-12T12:30:00Z", check="never-started"),
    )

    report = made(spec_path, runs, "--live-checks", str(log))

    marks = csv(report, "marks.csv").set_index("run")
    assert list(marks["live_check"]) == ["overlap", "overlap", "no overlap"]


def test_a_start_without_an_end_runs_until_the_report_is_built(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])
    log = live_checks(tmp_path / "live.jsonl", event("start", "2026-10-12T10:30:00Z"))

    report = made(spec_path, runs, "--live-checks", str(log))

    marks = csv(report, "marks.csv").set_index("run")
    assert list(marks["live_check"]) == ["no overlap", "overlap", "overlap"]


def test_a_line_that_is_no_event_is_left_out(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])
    log = live_checks(
        tmp_path / "live.jsonl",
        "not json at all",
        {"event": "start"},
        event("start", "not a time"),
        "",
        event("start", "2026-10-12T10:03:00Z"),
        event("end", "2026-10-12T10:20:00Z"),
    )

    report = made(spec_path, runs, "--live-checks", str(log))

    marks = csv(report, "marks.csv").set_index("run")
    assert list(marks["live_check"]) == ["overlap", "no overlap", "no overlap"]


def test_without_a_live_check_log_the_mark_says_no_data(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])

    report = made(spec_path, runs)
    missing = made(spec_path, runs, "--live-checks", str(tmp_path / "absent.jsonl"))

    for folder in (report, missing):
        assert set(csv(folder, "marks.csv")["live_check"]) == {"no data"}
    assert "no data" in section(report, "Runs marked")


def test_the_live_check_log_is_read_by_the_report_and_never_the_real_file(
    tmp_path, spec_path, by_number, monkeypatch
):
    """Without the option, the report opens no log, wherever it runs."""
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])
    home = tmp_path / "home"
    (home / ".claude" / "orchestrator").mkdir(parents=True)
    live_checks(
        home / ".claude" / "orchestrator" / "live-checks.jsonl",
        event("start", "2026-10-12T09:00:00Z"),
    )
    monkeypatch.setenv("HOME", str(home))

    report = made(spec_path, runs)

    assert set(csv(report, "marks.csv")["live_check"]) == {"no data"}


# --- Other activity on the server ---


def monitor(
    reads: int = 0,
    writes: int = 0,
    connections: int = 0,
    transactions: int = 0,
) -> dict[str, Any]:
    return {
        "request": "GET rest/monitor/repository/x",
        "status": 200,
        "answer": {
            "epoolReads": reads,
            "epoolWrites": writes,
            "openConnections": connections,
            "activeTransactions": transactions,
        },
    }


def unread(state: str = "INACTIVE") -> dict[str, Any]:
    return {
        "request": None,
        "state": state,
        "read": False,
        "reason": f"not read: the repository is {state}, not RUNNING",
    }


def cache(hit: int, miss: int) -> dict[str, Any]:
    return {
        "request": "GET rest/monitor/structures",
        "status": 200,
        "answer": {"cacheHit": hit, "cacheMiss": miss},
    }


def watched(
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    *,
    own: str,
    before: tuple[int, int] = (0, 0),
    after: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    return {
        "repository_monitors_at_the_start": dict(start),
        "repository_monitors_at_the_drain_end": {
            **end,
            own: monitor(transactions=1, connections=3),
        },
        "structures_at_the_window_start": cache(*before),
        "structures_at_the_drain_end": cache(*after),
    }


def activity_of(tmp_path, spec_path, one, server: Mapping[str, Any] | None):
    runs = tmp_path / "runs"
    write(runs, record("a1", one, server=server))
    marks = csv(made(spec_path, runs), "marks.csv")
    return marks.iloc[0]


@pytest.mark.parametrize(
    ("start", "end", "why"),
    [
        ({"x": monitor(reads=5)}, {"x": monitor(reads=6)}, "x: epoolReads"),
        ({"x": monitor(writes=5)}, {"x": monitor(writes=9)}, "x: epoolWrites"),
        ({"x": monitor(connections=1)}, {"x": monitor(connections=1)}, "x: open"),
        ({"x": monitor()}, {"x": monitor(transactions=2)}, "x: active"),
    ],
)
def test_a_run_is_marked_when_another_repository_shows_activity(
    tmp_path, spec_path, by_number, start, end, why
):
    server = watched(start, end, own="eval-a1")

    found = activity_of(tmp_path, spec_path, by_number[1], server)

    assert found["other_activity"] == "activity"
    assert why in found["other_activity_why"]


def test_a_quiet_server_marks_nothing_and_the_runs_own_repository_is_no_other_one(
    tmp_path, spec_path, by_number
):
    # The run's own repository has an active transaction and connections at the end; it is not
    # "other". The other repository's counters did not move and its gauges are zero.
    server = watched(
        {"x": monitor(reads=5, writes=5), "y": unread()},
        {"x": monitor(reads=5, writes=5), "y": unread()},
        own="eval-a1",
    )

    found = activity_of(tmp_path, spec_path, by_number[1], server)

    assert found["other_activity"] == "none"


def test_a_repository_that_was_not_read_is_no_activity(tmp_path, spec_path, by_number):
    server = watched(
        {"x": unread("INACTIVE")}, {"x": unread("INACTIVE")}, own="eval-a1"
    )

    found = activity_of(tmp_path, spec_path, by_number[1], server)

    # Nothing could be read of any other repository: no data, not "none".
    assert found["other_activity"] == "no data"


def test_counters_that_fell_are_no_activity(tmp_path, spec_path, by_number):
    server = watched({"x": monitor(reads=50)}, {"x": monitor(reads=2)}, own="eval-a1")

    found = activity_of(tmp_path, spec_path, by_number[1], server)

    assert found["other_activity"] == "none"


def test_a_record_of_the_before_series_has_no_data_for_every_server_mark(
    tmp_path, spec_path, by_number
):
    found = activity_of(tmp_path, spec_path, by_number[1], None)

    assert found["other_activity"] == "no data"
    assert found["live_check"] == "no data"


def test_a_refused_view_is_no_data_and_does_not_stop_the_report(
    tmp_path, spec_path, by_number
):
    refused = {"request": "GET rest/monitor/repository/x", "status": 403}
    server = watched({"x": refused}, {"x": refused}, own="eval-a1")

    found = activity_of(tmp_path, spec_path, by_number[1], server)

    assert found["other_activity"] == "no data"


# --- The page cache ---


def with_cache(
    run_id: str,
    one,
    before: tuple[int, int] | None,
    after: tuple[int, int] | None,
    **kw,
):
    server = watched(
        {}, {}, own=f"eval-{run_id}", before=before or (0, 0), after=after or (0, 0)
    )
    if before is None:
        server["structures_at_the_window_start"] = {"status": 500}
    return record(run_id, one, server=server, **kw)


def test_the_page_cache_hit_share_is_summed_over_the_valid_runs_of_each_prefill_size(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(
        runs,
        with_cache("a1", by_number[1], (100, 50), (190, 60)),  # +90 hits, +10 misses
        with_cache("a2", by_number[1], (0, 0), (60, 40), repetition=2),  # +60, +40
        # Counters that reset when the server restarted: this run has no data.
        with_cache("a3", by_number[1], (500, 500), (10, 10), repetition=3),
        # An invalid run is not among the valid ones.
        with_cache(
            "a4",
            by_number[1],
            (0, 0),
            (1000, 0),
            repetition=4,
            valid=False,
            failed_checks=[{"check": "matcher saw the ends"}],
        ),
        with_cache("b1", by_number[2], (0, 0), (30, 70)),
        # A read that failed at the start: no data for this run.
        with_cache("b2", by_number[2], None, (0, 0), repetition=2),
    )

    report = made(spec_path, runs)

    frame = csv(report, "page_cache.csv")
    assert list(frame.columns) == [
        "prefill",
        "run_kind",
        "runs",
        "runs_without_data",
        "hits",
        "misses",
        "hit_share",
    ]
    small = frame[frame["prefill"] == "cross-1k.ttl"].iloc[0]
    assert (small["runs"], small["runs_without_data"]) == (2, 1)
    assert (small["hits"], small["misses"]) == (150, 50)
    assert small["hit_share"] == pytest.approx(0.75)
    big = frame[frame["prefill"] == "cross-1M.ttl"].iloc[0]
    assert (big["runs"], big["runs_without_data"]) == (1, 1)
    assert big["hit_share"] == pytest.approx(0.3)
    assert "cross-1k.ttl" in section(report, "Global page cache")


def test_a_prefill_size_without_any_run_with_data_has_a_hit_share_of_no_data(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(
        runs,
        with_cache("a1", by_number[1], (500, 500), (10, 10)),
        record("a2", by_number[1], repetition=2),
    )

    report = made(spec_path, runs)

    frame = csv(report, "page_cache.csv")
    assert frame.iloc[0]["hit_share"] == "no data"
    assert frame.iloc[0]["runs"] == 0
    assert frame.iloc[0]["runs_without_data"] == 2
    assert "no data" in section(report, "Global page cache")


def test_the_other_forms_of_the_structures_view_are_read_too():
    forms = (
        lambda hit, miss: {"cache": {"hit": hit, "miss": miss}},
        lambda hit, miss: {"graphdb_cache_hit": hit, "graphdb_cache_miss": miss},
        lambda hit, miss: {"cacheHit": hit, "cacheMiss": miss},
    )
    for form in forms:
        found = report_points.page_cache(
            {
                "server": {
                    "structures_at_the_window_start": {
                        "status": 200,
                        "answer": form(0, 0),
                    },
                    "structures_at_the_drain_end": {
                        "status": 200,
                        "answer": form(3, 4),
                    },
                }
            }
        )
        assert (found["hits"], found["misses"]) == (3, 4)


# --- Deadlock alarms ---


def alarmed(run_id: str, one, repetition: int, modules: Sequence[str]):
    checks = [
        {
            "check": "deadlock alarm",
            "module": module,
            "line": "WARNING:x:possible deadlock: waiting",
            "at": "2026-10-12T10:03:00+00:00",
        }
        for module in modules
    ]
    return record(
        run_id,
        one,
        repetition=repetition,
        valid=False,
        failed_checks=checks,
        transports_ended_per_minute=3.0,
    )


def test_runs_with_a_deadlock_alarm_stay_invalid_and_are_listed_apart_with_their_count(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(
        runs,
        record("a1", by_number[1], transports_ended_per_minute=10.0),
        alarmed("a2", by_number[1], 2, ["Module3", "Module5"]),
        alarmed("a3", by_number[1], 3, ["Module3"]),
        # Invalid for another reason: no alarm.
        record(
            "a4",
            by_number[1],
            repetition=4,
            valid=False,
            failed_checks=[{"check": "matcher saw the ends"}],
        ),
        alarmed("b1", by_number[2], 1, ["Module1"]),
    )

    report = made(spec_path, runs)

    assert dict(csv(report, "runs.csv").set_index("run")["status"]) == {
        "a1": "completed",
        "a2": "invalid",
        "a3": "invalid",
        "a4": "invalid",
        "b1": "invalid",
    }
    listed = csv(report, "deadlock_alarms.csv")
    assert list(listed["run"]) == ["a2", "a3", "b1"]
    assert list(listed["alarms"]) == [2, 1, 1]
    assert list(listed["modules"]) == ["Module3, Module5", "Module3", "Module1"]
    text = section(report, "Deadlock alarms")
    counts = csv(report, "marks.csv").set_index("run")["deadlock_alarms"]
    assert dict(counts) == {"a1": 0, "a2": 2, "a3": 1, "a4": 0, "b1": 1}
    # The count of the runs with an alarm, for each configuration.
    assert "| spec 1 | 2 | 4 |" in text
    assert "| spec 2 | 1 | 1 |" in text


def test_no_deadlock_alarm_says_none(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    write(runs, record("a1", by_number[1]))

    report = made(spec_path, runs)

    assert csv(report, "deadlock_alarms.csv").empty
    assert section(report, "Deadlock alarms").strip().endswith("None.")


# --- Dead heartbeats ---

DEAD = {
    "Module2",
    "Module10",
    "Module12",
    "Module15",
    "Module24",
    "Module25",
    "Module26",
    "WarehouseManagementSystem1",
}


def with_spans(runs: Path, run_id: str) -> None:
    shutil.copytree(FIXTURE / "spans", runs / run_id / "spans")


def test_the_report_shows_the_eight_participants_whose_heartbeat_died_in_the_fixture(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(runs, record("a1", by_number[1], repetition=4))
    with_spans(runs, "a1")

    report = made(spec_path, runs)

    frame = csv(report, "dead_heartbeats.csv")
    assert set(frame["participant"]) == DEAD and len(frame) == 8
    assert set(frame["run"]) == {"a1"}
    row = frame[frame["participant"] == "Module2"].iloc[0]
    assert row["last_heartbeat"] == "2026-10-05T03:54:33+00:00"
    assert row["last_other_span"].startswith("2026-10-05T04:05:35")
    assert row["gap_seconds"] == pytest.approx(662.0, abs=1.0)
    assert row["repetition"] == 4
    marks = csv(report, "marks.csv")
    assert marks.iloc[0]["dead_heartbeats"] == 8
    text = section(report, "Dead heartbeats")
    assert "WarehouseManagementSystem1" in text and "03:54:33" in text


def test_the_live_heartbeat_age_is_the_run_setting_of_the_record(
    tmp_path, spec_path, by_number
):
    """Module1's last heartbeat lies 32 s before its last other span: dead under a 30 s age."""
    runs = tmp_path / "runs"
    found = record("a1", by_number[1])
    found["run_settings"] = {"live_heartbeat_age_seconds": 30.0}
    write(runs, found)
    with_spans(runs, "a1")

    report = made(spec_path, runs)

    assert {"Module1", "Module3"} <= set(
        csv(report, "dead_heartbeats.csv")["participant"]
    )


def test_a_heartbeat_within_the_age_is_alive_and_a_run_without_spans_has_no_data(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(
        runs,
        record("a1", by_number[1]),
        record("a2", by_number[1], repetition=2),
    )
    write_spans(
        runs,
        "a1",
        store_span("Module1", "INSERT lastHeartbeat", 0, 0.1, kind="write"),
        store_span("Module1", "SELECT ?x", 89, 0.1, kind="read"),
    )

    report = made(spec_path, runs)

    assert csv(report, "dead_heartbeats.csv").empty
    marks = csv(report, "marks.csv").set_index("run")
    assert marks.loc["a1", "dead_heartbeats"] == "0"
    assert marks.loc["a2", "dead_heartbeats"] == "no data"


def test_a_heartbeat_refused_by_the_store_is_not_a_heartbeat_write(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(runs, record("a1", by_number[1]))
    write_spans(
        runs,
        "a1",
        store_span("Module1", "INSERT lastHeartbeat", 0, 0.1, kind="write"),
        store_span(
            "Module1", "INSERT lastHeartbeat", 100, 0.1, kind="write", outcome="refused"
        ),
        store_span("Module1", "SELECT ?x", 200, 0.1, kind="read"),
    )

    report = made(spec_path, runs)

    assert list(csv(report, "dead_heartbeats.csv")["participant"]) == ["Module1"]


def test_a_participant_that_never_wrote_a_heartbeat_is_not_listed(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(runs, record("a1", by_number[1]))
    write_spans(runs, "a1", store_span("Module1", "SELECT ?x", 300, 0.1, kind="read"))

    report = made(spec_path, runs)

    assert csv(report, "dead_heartbeats.csv").empty


# --- Records as a real sweep makes them ---


def test_the_points_have_their_rows_for_runs_keyed_as_a_sweep_keys_them(
    tmp_path, spec_path, by_number
):
    """The record's `sweep` is made by `spec.sweep_fields`, as the sweep and `jms-eval-uc2` make
    it, not by hand; its key is the configuration's."""
    spec = read_spec(spec_path)
    runs = tmp_path / "runs"
    found = alarmed("s1", by_number[1], 4, ["Module3"])
    found["sweep"] = sweep_fields(spec, by_number[1], 4)
    write(runs, found)
    with_spans(runs, "s1")

    report = made(spec_path, runs)

    assert len(csv(report, "runs.csv")) == 1
    assert list(csv(report, "marks.csv")["run"]) == ["s1"]
    assert list(csv(report, "deadlock_alarms.csv")["run"]) == ["s1"]
    assert set(csv(report, "dead_heartbeats.csv")["participant"]) == DEAD


def test_a_run_whose_key_the_spec_does_not_know_still_has_its_points(
    tmp_path, spec_path, by_number
):
    """A record of iteration one has a key made before the transit time and the retry wait joined
    it: the runs table leaves it out as of no configuration of the spec, but its deadlock alarm
    and its dead heartbeats are the run's, and the report shows them under the configuration
    number its record gives."""
    runs = tmp_path / "runs"
    found = alarmed("old1", by_number[1], 4, ["Module3"])
    found["sweep"]["key"] = "b33d0759659948ea"
    found["sweep"]["configuration"] = 7
    write(runs, found)
    with_spans(runs, "old1")

    report = made(spec_path, runs)

    assert csv(report, "runs.csv").empty
    marks = csv(report, "marks.csv")
    assert list(marks["run"]) == ["old1"]
    assert list(marks["configuration"]) == [7]
    assert list(csv(report, "deadlock_alarms.csv")["configuration"]) == [7]
    dead = csv(report, "dead_heartbeats.csv")
    assert set(dead["participant"]) == DEAD and set(dead["configuration"]) == {7}
    assert csv(report, "hours.csv").empty


# --- The before series and the summary ---


def test_the_report_builds_without_a_log_and_without_server_samples(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])

    report = made(spec_path, runs)

    marks = csv(report, "marks.csv")
    assert set(marks["live_check"]) == {"no data"}
    assert set(marks["other_activity"]) == {"no data"}
    assert set(marks["deadlock_alarms"]) == {0}
    assert csv(report, "page_cache.csv").iloc[0]["hit_share"] == "no data"


def test_the_summary_format_is_raised_and_an_older_summary_is_made_again(
    tmp_path, spec_path, by_number
):
    assert FORMAT == 5
    runs = tmp_path / "runs"
    write(runs, record("a1", by_number[1]))
    made(spec_path, runs)
    path = runs / "a1" / SUMMARY_FILE
    stale = json.loads(path.read_text(encoding="utf-8"))
    assert "points" in stale
    stale["format"] = 3
    del stale["points"]
    path.write_text(json.dumps(stale), encoding="utf-8")

    report = made(spec_path, runs)

    assert csv(report, "marks.csv").iloc[0]["other_activity"] == "no data"
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == 5


def test_the_live_check_log_is_read_each_time_so_a_later_log_changes_the_marks(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    three_runs(runs, by_number[1])
    log = live_checks(tmp_path / "live.jsonl")
    first = made(spec_path, runs, "--live-checks", str(log))
    assert set(csv(first, "marks.csv")["live_check"]) == {"no overlap"}
    live_checks(
        log,
        event("start", "2026-10-12T10:03:00Z"),
        event("end", "2026-10-12T10:20:00Z"),
    )

    second = made(spec_path, runs, "--live-checks", str(log))

    assert list(csv(second, "marks.csv")["live_check"]) == [
        "overlap",
        "no overlap",
        "no overlap",
    ]


def test_the_pairing_of_a_log_is_a_function_of_its_events():
    checks = report_points.read_live_checks_text(
        [
            json.dumps(event("start", "2026-10-12T10:00:00Z")),
            json.dumps(event("end", "2026-10-12T10:10:00Z")),
            json.dumps(event("start", "2026-10-12T11:00:00Z")),
        ]
    )

    assert [(c.check, c.end is None) for c in checks] == [
        ("jms-96", False),
        ("jms-96", True),
    ]


# --- The documentation ---


def test_the_readme_and_the_context_describe_the_new_files_and_names():
    text = (EVALUATION / "README.md").read_text(encoding="utf-8")
    for name in (
        "marks.csv",
        "hours.csv",
        "page_cache.csv",
        "deadlock_alarms.csv",
        "dead_heartbeats.csv",
        "--live-checks",
        "live-check log",
        "live_heartbeat_age_seconds",
    ):
        assert name in text, name
    context = withheld_text(Path(jms_kapps.__file__).parents[2] / "CONTEXT.md")
    assert "jms_kapps 97" in context and "jms_kapps 83" in context
