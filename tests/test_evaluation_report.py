"""The report of a spec: `jms-eval-report` turns the runs below a folder into tables
and charts, `report/` beside the records. The seam is the command on a runs folder made from
fabricated records (`_evaluation_records`): the tests check the files, their columns and their
values, not how a chart looks. No store, no network, no run.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd
import pytest
from _evaluation_records import record, request, server_watch, write
from matplotlib.collections import PathCollection

import jms_kapps
from jms_kapps.evaluation import report, report_charts, report_summary, table
from jms_kapps.evaluation.report import main
from jms_kapps.evaluation.report_summary import FORMAT, SUMMARY_FILE
from jms_kapps.evaluation.spec import configurations, read_spec

README = Path(jms_kapps.__file__).parent / "evaluation" / "README.md"

# The error of a store request whose client gave up because the time ran out.
READ_TIMEOUT = (
    "ReadTimeout: HTTPConnectionPool(host='graphdb', port=7200): Read timed out."
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

# 1-2: a family of two prefill sizes.
[[sweep]]
topology = "cross"
prefill = ["{topology}-1k.ttl", "{topology}-1M.ttl"]

# 3: SHACL off, at 1k only.
[[sweep]]
topology = "cross"
prefill = "{topology}-1k.ttl"
shacl = false
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The report reaches no network: any socket it opened would fail the test."""

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


def made(spec_path: Path, runs: Path, *flags: str) -> pd.DataFrame:
    """Run the command; return the runs table it wrote."""
    code = main([str(spec_path), "--runs", str(runs), "--resamples", "200", *flags])
    assert code == 0
    return pd.read_csv(runs / "report" / "runs.csv")


def configurations_csv(runs: Path) -> pd.DataFrame:
    return pd.read_csv(
        runs / "report" / "configurations.csv", dtype={"configurations": str}
    )


def good(run_id, configuration, repetition, **numbers: Any) -> dict[str, Any]:
    return record(run_id, configuration, repetition=repetition, **numbers)


def folder_of_runs(runs: Path, by_number) -> list[dict[str, Any]]:
    """Configuration 1: three valid runs. Configuration 2: a valid one, an invalid one, a failed
    one. Configuration 3: an interrupted one."""
    records = [
        good(
            f"a{n}",
            by_number[1],
            n,
            commit_latencies=[1.0 * n, 2.0 * n, 3.0 * n, 4.0 * n, 5.0 * n],
            requests=[
                request(0.1 * n, kind="read"),
                request(0.3 * n, kind="read"),
                request(0.2 * n, kind="write"),
                request(0.4 * n, kind="write"),
                request(0.9, kind="read", window=False),
                request(0.5, kind="login"),
            ],
            transports_ended_per_minute=10.0 + n,
            completed_writes_per_minute=20.0 * n,
        )
        for n in (1, 2, 3)
    ] + [
        good(
            "b1",
            by_number[2],
            1,
            commit_latencies=[10.0, 20.0],
            requests=[request(1.0, kind="read"), request(2.0, kind="write")],
            transports_ended_per_minute=3.0,
        ),
        good(
            "b2",
            by_number[2],
            2,
            valid=False,
            failed_checks=[{"check": "deadlock alarm"}],
            commit_latencies=[30.0],
            requests=[request(3.0, kind="read")],
            transports_ended_per_minute=2.0,
        ),
        good("b3", by_number[2], 3, outcome="failed: StoreRefused: no heap"),
        good("c1", by_number[3], 1, outcome="interrupted"),
    ]
    write(runs, *records)
    return records


def test_the_command_writes_the_report_files(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)

    made(spec_path, runs)

    files = sorted(p.name for p in (runs / "report").iterdir())
    assert files == [
        "commit-p95.png",
        "configurations.csv",
        "dead_heartbeats.csv",
        "deadlock_alarms.csv",
        "hours-1.png",
        "hours-2.png",
        "hours.csv",
        "marks.csv",
        "page_cache.csv",
        "prefill-family-1.png",
        "read-write-p95.png",
        "report.md",
        "runs.csv",
        "steps.csv",
        "throughput.png",
        "unanswered.csv",
    ]
    for png in (runs / "report").glob("*.png"):
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    markdown = (runs / "report" / "report.md").read_text(encoding="utf-8")
    for chart in (
        "throughput.png",
        "commit-p95.png",
        "read-write-p95.png",
        "prefill-family-1.png",
    ):
        assert f"]({chart})" in markdown
    assert "## Configurations" in markdown and "## Runs" in markdown


def test_the_report_reads_the_records_of_the_before_series_and_of_the_after_series_alike(
    tmp_path, spec_path, by_number
):
    """The 35 records of iteration one hold `transports.highest_box_before_the_window`; a record
    of today does not. The report reads both, and the key changes no number."""
    runs = tmp_path / "runs"
    numbers = {
        "commit_latencies": [1.0, 2.0, 3.0],
        "requests": [request(0.1, kind="read"), request(0.2, kind="write")],
        "transports_ended_per_minute": 11.0,
        "completed_writes_per_minute": 20.0,
        "failed": [{"transport": "Box3_transport", "place": "Module1", "reason": "r"}],
    }
    before = good("old", by_number[1], 1, highest_box_before_the_window=1000, **numbers)
    after = good("new", by_number[1], 2, **numbers)
    assert "highest_box_before_the_window" in before["transports"]
    assert "highest_box_before_the_window" not in after["transports"]
    write(runs, before, after)

    table = made(spec_path, runs).set_index("run")

    columns = [
        "status",
        "transports_ended_per_minute",
        "commit_p95",
        "read_p95",
        "write_p95",
    ]
    old, new = table.loc["old", columns], table.loc["new", columns]
    assert old.tolist() == new.tolist()
    assert (runs / "report" / "steps.csv").is_file()


def test_the_charts_are_drawn_with_the_headless_backend():
    assert matplotlib.get_backend().lower() == "agg"
    assert report_charts.matplotlib.get_backend().lower() == "agg"


def test_the_runs_table_has_one_row_for_each_run_with_its_setting_and_status(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)

    frame = made(spec_path, runs)

    assert list(frame.columns) == list(report.RUNS_COLUMNS)
    assert list(frame["run"]) == ["a1", "a2", "a3", "b1", "b2", "b3", "c1"]
    assert list(frame["status"]) == [
        "completed",
        "completed",
        "completed",
        "completed",
        "invalid",
        "failed",
        "interrupted",
    ]
    first = frame.iloc[0]
    assert (first["configuration"], first["run_kind"], first["repetition"]) == (
        1,
        "uc2",
        1,
    )
    assert first["topology"] == "cross"
    assert first["prefill"] == "cross-1k.ttl"
    assert bool(first["shacl"]) is True
    assert (first["module_strategy"], first["wms_strategy"]) == ("real", "real")
    assert frame.iloc[-1]["shacl"] in (False, "False")


def test_the_runs_table_gives_the_throughput_commits_and_latencies_of_each_run(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)

    frame = made(spec_path, runs)

    second = frame[frame["run"] == "a2"].iloc[0]
    assert second["transports_ended_per_minute"] == 12.0
    assert second["commits"] == 5
    # The commits [2, 4, 6, 8, 10]: p50 6, p95 9.6, p99 9.92 (linear interpolation).
    assert second["commit_p50"] == pytest.approx(6.0)
    assert second["commit_p95"] == pytest.approx(9.6)
    assert second["commit_p99"] == pytest.approx(9.92)


def test_reads_and_writes_are_kept_apart_and_count_only_accepted_requests_of_the_window(
    tmp_path, spec_path, by_number
):
    """The reads of a1 in the window are 0.1 and 0.3 s, its writes 0.2 and 0.4 s; a read outside
    the window, a login, a refused and a timed-out request (which keeps its real wait) are not among
    the latencies."""
    runs = tmp_path / "runs"
    write(
        runs,
        good(
            "a1",
            by_number[1],
            1,
            commit_latencies=[1.0],
            requests=[
                request(0.1, kind="read"),
                request(0.3, kind="read"),
                request(0.2, kind="write"),
                request(0.4, kind="write"),
                request(0.9, kind="read", window=False),
                request(0.7, kind="login"),
                request(0.05, kind="write", outcome="refused"),
                request(0.0, kind="read", outcome="timed out"),
            ],
        ),
    )

    row = made(spec_path, runs).iloc[0]

    assert row["read_p50"] == pytest.approx(0.2)
    assert row["read_p95"] == pytest.approx(0.29)
    assert row["write_p50"] == pytest.approx(0.3)
    assert row["write_p95"] == pytest.approx(0.39)


def test_refused_and_timed_out_requests_are_counted_inside_and_outside_the_window_apart(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(
        runs,
        good(
            "a1",
            by_number[1],
            1,
            commit_latencies=[1.0],
            requests=[
                request(0.1, kind="read"),
                # Their client gave up because the time ran out: a request "timed out" whose
                # record holds no error is connection dropped.
                request(0.0, kind="read", outcome="timed out", reason=READ_TIMEOUT),
                request(0.0, kind="write", outcome="timed out", reason=READ_TIMEOUT),
                request(
                    0.0,
                    kind="write",
                    outcome="timed out",
                    window=False,
                    reason=READ_TIMEOUT,
                ),
                request(0.2, kind="write", outcome="refused"),
                request(0.2, kind="write", outcome="refused", window=False),
                request(0.2, kind="write", outcome="refused", window=False),
                request(0.2, kind="write", outcome="refused", window=False),
            ],
            stuck=[
                {"transport": "T1", "status": "running", "place": "M1"},
                {"transport": "T2", "status": "running", "place": "M2"},
            ],
        ),
    )

    row = made(spec_path, runs).iloc[0]

    assert (
        row["refused_in_window"],
        row["timed_out_in_window"],
        row["refused_outside_window"],
        row["timed_out_outside_window"],
    ) == (1, 2, 3, 1)
    assert row["stuck_boxes"] == 2


def test_a_failed_and_an_interrupted_run_are_listed_with_their_reason_and_have_no_values(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)

    frame = made(spec_path, runs)

    failed = frame[frame["run"] == "b3"].iloc[0]
    interrupted = frame[frame["run"] == "c1"].iloc[0]
    assert failed["reason"] == "failed: StoreRefused: no heap"
    assert "stop signal" in interrupted["reason"]
    for row in (failed, interrupted):
        for column in (
            "transports_ended_per_minute",
            "commits",
            "commit_p95",
            "read_p95",
        ):
            assert pd.isna(row[column])
    invalid = frame[frame["run"] == "b2"].iloc[0]
    assert invalid["reason"] == "invalid: it failed the checks deadlock alarm"
    assert invalid["transports_ended_per_minute"] == 2.0
    markdown = (runs / "report" / "report.md").read_text(encoding="utf-8")
    left_out = markdown.split("## Runs left out of the statistics")[1].split(
        "## Charts"
    )[0]
    for run in ("b2", "b3", "c1"):
        assert run in left_out
    assert "a1" not in left_out


def test_the_configurations_table_gives_the_numbers_of_the_table_of_a_spec(
    tmp_path, spec_path, by_number
):
    """The same records, the same seed and resamples: the same numbers as `jms-eval-table`."""
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    code = table.main(
        [str(spec_path), "--runs", str(runs), "--seed", "7", "--resamples", "200"]
    )
    assert code == 0
    expected = json.loads((runs / "table.json").read_text(encoding="utf-8"))["rows"]

    made(spec_path, runs, "--seed", "7")
    frame = configurations_csv(runs)

    counting = [r for r in expected if r["run_kinds"]["uc2"]]
    assert len(counting) == 2 and len(frame) == 2
    for row in counting:
        cell = row["run_kinds"]["uc2"]
        mine = frame[
            frame["configurations"] == ", ".join(map(str, row["configurations"]))
        ].iloc[0]
        assert mine["runs"] == len(cell["runs"])
        for measure in ("commit_latency_seconds", "store_request_latency_seconds"):
            for name in ("p50", "p95", "p99"):
                found = cell[measure][name]
                assert mine[f"{measure}_{name}_value"] == pytest.approx(found["value"])
                assert mine[f"{measure}_{name}_interval_low"] == pytest.approx(
                    found["interval"][0]
                )
                assert mine[f"{measure}_{name}_interval_high"] == pytest.approx(
                    found["interval"][1]
                )
        spread = cell["transports_ended_per_minute"]
        assert mine["transports_ended_per_minute_median"] == pytest.approx(
            spread["median"]
        )
        assert mine["transports_ended_per_minute_iqr"] == pytest.approx(spread["iqr"])
        assert mine["refused_or_unanswered_share"] == pytest.approx(
            cell["refused_or_unanswered_share"] or 0
        ) or pd.isna(mine["refused_or_unanswered_share"])


def test_the_configurations_table_counts_only_the_valid_runs(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    made(spec_path, runs)

    frame = configurations_csv(runs)

    assert dict(zip(frame["configurations"], frame["runs"], strict=True)) == {
        "1": 3,
        "2": 1,
    }
    two = frame[frame["configurations"] == "2"].iloc[0]
    # The invalid run's commit (30 s) is not in the statistics of configuration 2.
    assert two["commit_latency_seconds_p95_value"] == pytest.approx(19.5)


def test_a_record_is_read_once_and_its_summary_is_kept_beside_it(
    tmp_path, spec_path, by_number, monkeypatch
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    made(spec_path, runs)

    for folder in runs.iterdir():
        if folder.name == "report":
            continue
        summary = json.loads((folder / SUMMARY_FILE).read_text(encoding="utf-8"))
        assert summary["format"] == FORMAT
        assert (folder / "record.json").is_file()

    # A later report reads the summaries: a record read again would fail here.
    def refuse(record: object) -> None:
        raise AssertionError("a record was read again")

    monkeypatch.setattr(report_summary, "figures_of", refuse)
    before = pd.read_csv(runs / "report" / "runs.csv")

    after = made(spec_path, runs)

    pd.testing.assert_frame_equal(before, after)


def test_a_summary_of_another_format_is_made_again_from_its_record(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    made(spec_path, runs)
    path = runs / "a1" / SUMMARY_FILE
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale["format"] = FORMAT + 1
    stale["summary"]["transports_ended_per_minute"] = 999.0
    path.write_text(json.dumps(stale), encoding="utf-8")

    frame = made(spec_path, runs)

    assert frame[frame["run"] == "a1"].iloc[0]["transports_ended_per_minute"] == 11.0
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == FORMAT


def test_a_summary_of_a_record_that_changed_is_made_again(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    records = folder_of_runs(runs, by_number)
    made(spec_path, runs)
    changed = dict(records[0]) | {
        "measures": records[0]["measures"] | {"transports_ended_per_minute": 77.0}
    }
    (runs / "a1" / "record.json").write_text(json.dumps(changed), encoding="utf-8")

    frame = made(spec_path, runs)

    assert frame[frame["run"] == "a1"].iloc[0]["transports_ended_per_minute"] == 77.0


def test_a_summary_from_a_fresh_record_is_the_summary_from_its_file(
    tmp_path, by_number
):
    runs = tmp_path / "runs"
    records = folder_of_runs(runs, by_number)
    folder = runs / "a2"

    from_memory = report_summary.read_figures(folder, fresh=records[1])
    kept = json.loads((folder / SUMMARY_FILE).read_text(encoding="utf-8"))
    from_file = report_summary.read_figures(folder)

    assert from_memory is not None and from_file is not None
    assert from_file == from_memory
    assert kept["latencies"]["read"] == [0.2, 0.6]


def test_a_folder_with_no_readable_record_is_none_of_the_runs(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    (runs / "broken").mkdir()
    (runs / "broken" / "record.json").write_text("{not json", encoding="utf-8")

    frame = made(spec_path, runs)

    assert len(frame) == 7


def test_only_a_valid_run_is_a_filled_dot_and_an_invalid_run_is_a_ring(
    tmp_path, spec_path, by_number, monkeypatch
):
    """Read from the drawn figure: the dots of the valid runs have a face, the invalid run's has
    none, and the failed and the interrupted runs are not drawn at all."""
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    drawn: list[Any] = []
    monkeypatch.setattr(report_charts.plt, "close", lambda figure: drawn.append(figure))

    made(spec_path, runs)

    throughput = drawn[0]
    [ax] = throughput.axes
    dots = [c for c in ax.collections if isinstance(c, PathCollection)]
    points = [(offsets, c) for c in dots for offsets in c.get_offsets()]
    # a1-a3 and b1 are valid, b2 is the ring; b3 and c1 have no throughput.
    assert len(points) == 5
    rings = [c for _, c in points if len(c.get_facecolor()) == 0]
    filled = [c for _, c in points if len(c.get_facecolor()) > 0]
    assert (len(filled), len(rings)) == (4, 1)
    assert ax.get_ylabel() == "transports per minute"


def test_a_family_of_prefill_sizes_has_a_chart_and_other_configurations_do_not(
    spec_path, by_number
):
    found = list(by_number.values())

    assert [[c.number for c in family] for family in report.families(found)] == [[1, 2]]
    assert report.prefill_size("grid-1M.ttl") == 1_000_000
    assert report.prefill_size("grid-60-100k.ttl") == 100_000
    assert report.prefill_size("none") is None


def test_the_report_of_a_folder_with_no_run_is_still_written(tmp_path, spec_path):
    runs = tmp_path / "runs"
    runs.mkdir()

    frame = made(spec_path, runs)

    assert frame.empty
    assert (runs / "report" / "report.md").is_file()
    assert (runs / "report" / "throughput.png").is_file()


def test_a_run_of_no_configuration_of_the_spec_is_left_out_of_the_tables(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    write(runs, record("stranger", None))

    frame = made(spec_path, runs)

    assert "stranger" not in set(frame["run"])
    assert "1 records are of no configuration of the spec" in (
        runs / "report" / "report.md"
    ).read_text(encoding="utf-8")


def test_a_report_made_again_removes_the_charts_it_no_longer_draws(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)
    made(spec_path, runs)
    (runs / "report" / "prefill-family-9.png").write_bytes(b"old")

    made(spec_path, runs)

    assert not (runs / "report" / "prefill-family-9.png").exists()


def test_a_runs_folder_that_is_not_there_is_refused(tmp_path, spec_path, capsys):
    assert main([str(spec_path), "--runs", str(tmp_path / "nowhere")]) == 2
    assert "no runs' folder" in capsys.readouterr().err


def test_a_flag_sets_a_value_of_every_configuration_as_the_sweep_was_given_it(
    tmp_path, spec_path, by_number
):
    """`--set` changes the configurations' keys, so that records of the other values are of no
    configuration."""
    runs = tmp_path / "runs"
    folder_of_runs(runs, by_number)

    code = main(
        [
            str(spec_path),
            "--runs",
            str(runs),
            "--resamples",
            "50",
            "--set",
            "boxes_per_minute=99",
        ]
    )

    assert code == 0
    assert pd.read_csv(runs / "report" / "runs.csv").empty


def test_the_readme_describes_the_command_and_its_files():
    text = README.read_text(encoding="utf-8")
    for name in (
        "jms-eval-report",
        "report/",
        "report.md",
        "runs.csv",
        "configurations.csv",
        SUMMARY_FILE,
    ):
        assert name in text


def test_the_report_and_the_table_read_the_runs_of_the_before_series_beside_those_with_a_watch(
    tmp_path, spec_path, by_number
):
    """A record of the "before" series has no samples and none of the watch's
    counters; a record of the "after" series has them. Both are runs of the same configuration,
    and the report and the table give the same numbers for both."""
    runs = tmp_path / "runs"
    numbers: dict[str, Any] = {
        "commit_latencies": [1.0, 2.0, 3.0],
        "requests": [request(0.1, kind="read"), request(0.2, kind="write")],
        "transports_ended_per_minute": 12.0,
        "completed_writes_per_minute": 20.0,
    }
    before_only = {"url": "http://graphdb.test", "requests": ["GET rest/info/version"]}
    write(
        runs,
        good("before", by_number[1], 1, server=before_only, **numbers),
        good("after", by_number[1], 2, server=server_watch(samples=4), **numbers),
        good("older", by_number[1], 3, **numbers),
    )

    frame = made(spec_path, runs).set_index("run")

    assert set(frame.index) == {"before", "after", "older"}
    columns = [c for c in frame.columns if c != "repetition"]
    for run_id in ("after", "older"):
        pd.testing.assert_series_equal(
            frame.loc["before", columns],
            frame.loc[run_id, columns],
            check_names=False,
        )
    assert table.main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0


def test_the_report_still_reads_a_record_of_the_before_series_whose_key_the_new_keys_changed(
    tmp_path, spec_path, by_number
):
    """Every configuration's key changed, so a run of the "before" series is of no
    configuration of the spec any more. Its record, with the transit time under `run_settings`
    and no pick seed, is still read: the report counts it and goes on with the runs of the
    "after" series."""
    runs = tmp_path / "runs"
    before = record("before", None, repetition=1, transports_ended_per_minute=9.0)
    before["sweep"] = {
        "spec": "spec.toml",
        "configuration": 1,
        "key": "0123456789abcdef",
        "repetition": 1,
        "repetitions": 3,
    }
    before["run_settings"] = {
        "transit_seconds": 3.0,
        "retry_seconds": 1.0,
        "wms_pick_seed": None,
    }
    before["configuration"] = 'topology = "cross"\nheavy = false\n'
    after = good("after", by_number[1], 1, transports_ended_per_minute=9.0)
    after["run_settings"] = {"wms_pick_seed": 4218638168896275004}
    write(runs, before, after)

    frame = made(spec_path, runs)

    assert set(frame["run"]) == {"after"}
    assert "1 records are of no configuration of the spec" in (
        runs / "report" / "report.md"
    ).read_text(encoding="utf-8")
