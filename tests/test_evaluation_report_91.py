"""The run report shows each unanswered store request and each run's client load (jms_kapps 91).

The seam is `jms-eval-report` on a runs folder made from fabricated records (`_evaluation_records`),
as for the report itself; the last tests run it on a copy of iteration one's real records when one
is named by the environment. No store, no network, no run.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from _evaluation_records import client_sample, record, request, sampled_load, write

import jms_kapps
from jms_kapps.evaluation import report_summary
from jms_kapps.evaluation.report import main
from jms_kapps.evaluation.report_summary import FORMAT, SUMMARY_FILE
from jms_kapps.evaluation.spec import configurations, read_spec

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"
REASON = (
    "ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed'))"
)

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 3

[[sweep]]
topology = "cross"
prefill = "none"
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
def one(spec_path):
    return configurations(read_spec(spec_path))[0]


def made(spec_path: Path, runs: Path) -> Path:
    assert main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0
    return runs / "report"


def csv(report: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(report / name)


def markdown_section(report: Path, title: str) -> str:
    text = (report / "report.md").read_text(encoding="utf-8")
    return text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def dropped(seconds: float, *, window: bool, at: str, **more: Any) -> dict[str, Any]:
    return request(
        seconds,
        kind="read",
        outcome="timed out",
        window=window,
        at=at,
        reason=REASON,
        **more,
    )


# --- The unanswered store requests ---


def test_the_report_lists_each_unanswered_request_with_its_time_wait_and_reason(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(
        runs,
        record(
            "a1",
            one,
            repetition=4,
            requests=[
                request(0.1, kind="read"),
                dropped(0.002121, window=True, at="2026-10-12T10:03:14+00:00"),
                dropped(0.0094, window=False, at="2026-10-12T10:08:00+00:00"),
                request(0.2, outcome="refused", window=False),
            ],
        ),
        record("a2", one, repetition=5, requests=[request(0.1)]),
    )

    report = made(spec_path, runs)

    frame = csv(report, "unanswered.csv")
    assert list(frame["run"]) == ["a1", "a1"]
    assert list(frame["where"]) == ["in the window", "outside the window"]
    assert list(frame["time"]) == [
        "2026-10-12T10:03:14+00:00",
        "2026-10-12T10:08:00+00:00",
    ]
    assert list(frame["wait_seconds"]) == [0.002121, 0.0094]
    assert list(frame["reason"]) == [REASON, REASON]
    assert list(frame["configuration"]) == [1, 1]
    assert list(frame["repetition"]) == [4, 4]
    section = markdown_section(report, "Unanswered store requests")
    assert "a1" in section
    assert "2026-10-12T10:03:14+00:00" in section
    assert "0.002121" in section
    assert "RemoteDisconnected" in section
    assert "in the window" in section and "outside the window" in section


def test_a_runs_folder_with_no_unanswered_request_says_none(tmp_path, spec_path, one):
    runs = tmp_path / "runs"
    write(runs, record("a1", one, requests=[request(0.1, kind="read")]))

    report = made(spec_path, runs)

    assert csv(report, "unanswered.csv").empty
    assert (
        markdown_section(report, "Unanswered store requests").strip().endswith("None.")
    )


def test_a_baseline_run_s_unanswered_requests_are_listed_too(tmp_path, spec_path, one):
    runs = tmp_path / "runs"
    write(
        runs,
        record("a1", one),
        record(
            "b1",
            run_kind="baseline",
            replays="a1",
            requests=[dropped(5.0, window=True, at="2026-10-12T10:03:00+00:00")],
        ),
    )

    report = made(spec_path, runs)

    frame = csv(report, "unanswered.csv")
    assert list(frame["run"]) == ["b1"]
    assert list(frame["run_kind"]) == ["baseline"]
    assert list(frame["wait_seconds"]) == [5.0]


def test_the_waits_of_unanswered_requests_are_never_counted_as_latencies(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(
        runs,
        record(
            "a1",
            one,
            requests=[
                request(0.5, kind="read"),
                dropped(9.0, window=True, at="2026-10-12T10:03:00+00:00"),
            ],
        ),
    )

    row = csv(made(spec_path, runs), "runs.csv").iloc[0]

    assert row["read_p95"] == 0.5
    # A dropped connection by its error (jms_kapps 109), no timeout.
    assert row["connection_dropped_in_window"] == 1
    assert row["timed_out_in_window"] == 0


def test_no_documentation_says_an_unanswered_request_is_recorded_with_zero_seconds():
    import re

    paths = [*EVALUATION.glob("*.py"), EVALUATION / "README.md"]
    wrong = re.compile(r"recorded with 0\.0\s*s|never timed", re.IGNORECASE)
    assert [p.name for p in paths if wrong.search(p.read_text(encoding="utf-8"))] == []


# --- The client load ---


def window_samples() -> list[dict[str, Any]]:
    return [
        client_sample("2026-10-12T10:00:30+00:00", 99.0, 30.0),  # before the window
        client_sample("2026-10-12T10:02:00+00:00", 10.0, 4.0),
        client_sample("2026-10-12T10:03:00+00:00", 30.0, 6.0),
        client_sample("2026-10-12T10:04:00+00:00", 20.0, 5.0),
        client_sample("2026-10-12T10:04:30+00:00", None, 5.5),
        client_sample("2026-10-12T10:07:00+00:00", 95.0, 40.0),  # in the drain
    ]


def test_the_report_shows_the_median_and_highest_cpu_and_highest_memory_in_the_window(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(runs, record("a1", one, client_load=sampled_load(*window_samples())))

    report = made(spec_path, runs)

    row = csv(report, "runs.csv").iloc[0]
    assert row["client_load"] == "measured"
    assert row["client_cpu_median_percent"] == 20.0
    assert row["client_cpu_max_percent"] == 30.0
    assert row["client_memory_max_gib"] == pytest.approx(6.0)
    section = markdown_section(report, "Client load")
    assert "a1" in section and "measured" in section
    assert "20" in section and "30" in section and "6" in section
    assert "99" not in section and "95" not in section


def test_a_record_without_samples_shows_no_data(tmp_path, spec_path, one):
    runs = tmp_path / "runs"
    write(runs, record("a1", one))

    report = made(spec_path, runs)

    row = csv(report, "runs.csv").iloc[0]
    assert row["client_load"] == "no data"
    assert pd.isna(row["client_cpu_median_percent"])
    assert pd.isna(row["client_cpu_max_percent"])
    assert pd.isna(row["client_memory_max_gib"])
    assert "no data" in markdown_section(report, "Client load")


def test_a_run_whose_samples_all_lie_outside_the_window_shows_no_data(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    outside = client_sample("2026-10-12T10:00:30+00:00", 50.0, 3.0)
    write(runs, record("a1", one, client_load=sampled_load(outside)))

    row = csv(made(spec_path, runs), "runs.csv").iloc[0]

    assert row["client_load"] == "no data"


def test_a_host_without_the_figures_shows_not_available(tmp_path, spec_path, one):
    runs = tmp_path / "runs"
    write(runs, record("a1", one, client_load=sampled_load(status="not available")))

    report = made(spec_path, runs)

    assert csv(report, "runs.csv").iloc[0]["client_load"] == "not available"
    assert "not available" in markdown_section(report, "Client load")


def test_a_run_whose_samples_hold_no_cpu_share_still_shows_its_memory(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    only = client_sample("2026-10-12T10:02:00+00:00", None, 4.0)
    write(runs, record("a1", one, client_load=sampled_load(only)))

    row = csv(made(spec_path, runs), "runs.csv").iloc[0]

    assert row["client_load"] == "measured"
    assert pd.isna(row["client_cpu_median_percent"])
    assert row["client_memory_max_gib"] == pytest.approx(4.0)


def test_a_baseline_run_s_client_load_is_shown_like_a_run_s(tmp_path, spec_path, one):
    runs = tmp_path / "runs"
    write(
        runs,
        record("a1", one),
        record(
            "b1",
            run_kind="baseline",
            replays="a1",
            client_load=sampled_load(*window_samples()),
        ),
    )

    frame = csv(made(spec_path, runs), "runs.csv")

    baseline = frame[frame["run_kind"] == "baseline"].iloc[0]
    assert baseline["client_cpu_max_percent"] == 30.0


# --- The summary ---


def test_the_summary_format_is_raised_and_an_older_summary_is_made_again(
    tmp_path, spec_path, one
):
    assert FORMAT == 5
    runs = tmp_path / "runs"
    write(runs, record("a1", one, client_load=sampled_load(*window_samples())))
    made(spec_path, runs)
    path = runs / "a1" / SUMMARY_FILE
    stale = json.loads(path.read_text(encoding="utf-8"))
    assert {"unanswered", "client_load"} <= set(stale)
    stale["format"] = 2
    del stale["client_load"], stale["unanswered"]
    path.write_text(json.dumps(stale), encoding="utf-8")

    report = made(spec_path, runs)

    assert csv(report, "runs.csv").iloc[0]["client_cpu_max_percent"] == 30.0
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == 5


def test_the_summary_keeps_both_so_the_record_is_read_once(
    tmp_path, spec_path, one, monkeypatch
):
    runs = tmp_path / "runs"
    write(
        runs,
        record(
            "a1",
            one,
            requests=[dropped(0.003, window=True, at="2026-10-12T10:03:00+00:00")],
            client_load=sampled_load(*window_samples()),
        ),
    )
    first = made(spec_path, runs)
    before = (csv(first, "unanswered.csv"), csv(first, "runs.csv"))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the record was read again")

    monkeypatch.setattr(report_summary, "figures_of", refuse)
    after = made(spec_path, runs)

    pd.testing.assert_frame_equal(before[0], csv(after, "unanswered.csv"))
    pd.testing.assert_frame_equal(before[1], csv(after, "runs.csv"))


def test_the_readme_describes_the_new_files_and_columns():
    text = (EVALUATION / "README.md").read_text(encoding="utf-8")
    for name in (
        "unanswered.csv",
        "client_load",
        "client_cpu_median_percent",
        "client_cpu_max_percent",
        "client_memory_max_gib",
        "client_load_interval_seconds",
        "no data",
        "not available",
    ):
        assert name in text, name


# --- Iteration one's real records ---

COPY = os.environ.get("JMS_KAPPS_ITERATION_ONE_COPY")


@pytest.mark.skipif(not COPY, reason="needs a copy of iteration one's records")
def test_on_iteration_one_s_records_the_report_lists_the_four_dropped_connections(
    tmp_path,
):
    """The copy test of jms_kapps 91, with the two outcomes of jms_kapps 109: the orchestrator
    runs it on its copy of iteration one's records."""
    assert COPY is not None
    runs = Path(COPY)
    spec = EVALUATION / "specs" / "iteration-one.toml"

    assert main([str(spec), "--runs", str(runs), "--resamples", "200"]) == 0

    frame = csv(runs / "report", "unanswered.csv")
    assert len(frame) == 4
    assert frame["reason"].str.contains("RemoteDisconnected").all()
    assert (frame["wait_seconds"] < 0.01).all()
    assert sorted(frame["where"]) == ["in the window"] * 2 + ["outside the window"] * 2
    # Four dropped connections and no timeout (jms_kapps 109): the records keep "timed out",
    # and the report classifies each by its error.
    assert list(frame["outcome"]) == ["connection dropped"] * 4
    runs_frame = csv(runs / "report", "runs.csv")
    assert runs_frame["timed_out_in_window"].fillna(0).sum() == 0
    assert runs_frame["timed_out_outside_window"].fillna(0).sum() == 0
    assert runs_frame["connection_dropped_in_window"].fillna(0).sum() == 2
    assert runs_frame["connection_dropped_outside_window"].fillna(0).sum() == 2
    assert set(runs_frame["client_load"]) == {"no data"}
