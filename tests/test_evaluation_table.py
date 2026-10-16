"""The table of a spec (jms_kapps 49): the statistics of its runs for each configuration and each
run kind, the SHACL overhead, the load levels, the calibration pair, the ramps and the runs left
out. Every number here comes from fabricated records and is computed by hand; no store, no run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _evaluation_records import record, request, write

import jms_kapps
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.summary import summary_of
from jms_kapps.evaluation.table import main, make_table, markdown

README = Path(jms_kapps.__file__).parent / "evaluation" / "README.md"

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 3

# 1-4: SHACL on and off, each with the wrapper and without it.
[[sweep]]
topology = "cross"
shacl = [true, false]
wrapper = [true, false]

# 5-8: a ramp.
[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20, 40, 80]
repetitions = 1
"""


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    found = read_spec(path)
    return found, {c.number: c for c in configurations(found)}


def table_of(spec, records: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
    found, by_number = spec
    return make_table(
        found,
        list(by_number.values()),
        [summary_of(r) for r in records],
        runs=Path("runs"),
        **({"resamples": 300} | options),
    )


def row(table: dict[str, Any], *numbers: int) -> dict[str, Any]:
    [found] = [r for r in table["rows"] if r["configurations"] == list(numbers)]
    return found


def centre_runs(spec) -> list[dict[str, Any]]:
    """Three valid runs of configuration 1, SHACL on with the wrapper, and three left out: an
    invalid one, a failed one and an interrupted one, whose numbers would change every
    statistic."""
    _, by_number = spec
    one = by_number[1]
    return [
        record(
            "run-1",
            one,
            repetition=1,
            commit_latencies=[1, 2, 3],
            requests=[
                request(0.1),
                request(0.2, kind="read"),
                request(5.0, outcome="timed out"),
                request(0.05, outcome="refused"),
                request(0.3, kind="login"),
                request(0.9, window=False),
            ],
            completed_writes_per_minute=100,
            transports_ended_per_minute=30,
            refused_in_window=1,
            timed_out_in_window=1,
            refused_spawns=[
                {"reason": "no free origin", "origin": None, "destination": None},
                {
                    "reason": "no candidate",
                    "origin": "Module1",
                    "destination": "Module5",
                },
            ],
            stuck=[
                {"transport": "Transport7", "status": "running", "place": "Module3"}
            ],
            failed=[
                {
                    "transport": "Transport9",
                    "place": "Module2",
                    "reason": "no live route on",
                }
            ],
        ),
        record(
            "run-2",
            one,
            repetition=2,
            commit_latencies=[4, 5],
            requests=[request(0.4)],
            completed_writes_per_minute=120,
            transports_ended_per_minute=28,
        ),
        record(
            "run-3",
            one,
            repetition=3,
            commit_latencies=[6, 7, 8, 9],
            requests=[request(0.5), request(0.6)],
            completed_writes_per_minute=80,
            transports_ended_per_minute=29,
            heap_refusals_in_window=2,
            refused_spawns=[
                {"reason": "no free origin", "origin": None, "destination": None}
            ],
        ),
        record(
            "run-4-invalid",
            one,
            repetition=3,
            valid=False,
            failed_checks=[
                {"check": "one accepted done or failed", "transport": "Transport2"},
                {"check": "deadlock alarm", "module": "Module4"},
            ],
            commit_latencies=[100, 200],
            requests=[request(50.0)],
            completed_writes_per_minute=1,
            transports_ended_per_minute=1,
        ),
        record(
            "run-5-failed",
            one,
            repetition=3,
            outcome="failed: RuntimeError: the store did not answer",
        ),
        record("run-6-interrupted", one, repetition=3, outcome="interrupted"),
    ]


def test_the_statistics_of_fabricated_records_equal_the_values_computed_by_hand(spec):
    table = table_of(spec, centre_runs(spec))

    cell = row(table, 1, 2)["run_kinds"]["uc2"]
    commit = cell["commit_latency_seconds"]
    requests = cell["store_request_latency_seconds"]

    assert cell["runs"] == ["run-1", "run-2", "run-3"]
    # The commit latencies pooled: 1 to 9.
    assert commit["count"] == 9
    assert commit["mean"] == pytest.approx(5.0)
    assert [commit[q]["value"] for q in ("p50", "p95", "p99")] == pytest.approx(
        [5.0, 8.6, 8.92]
    )
    # The accepted store requests of the window, logins left out: 0.1, 0.2, 0.4, 0.5, 0.6.
    assert requests["count"] == 5
    assert [requests[q]["value"] for q in ("p50", "p95", "p99")] == pytest.approx(
        [0.4, 0.58, 0.596]
    )
    # Medians and interquartile ranges of the three runs.
    assert cell["completed_writes_per_minute"] == pytest.approx(
        {"median": 100, "q1": 90, "q3": 110, "iqr": 20, "runs": 3}
    )
    assert cell["transports_ended_per_minute"] == pytest.approx(
        {"median": 29, "q1": 28.5, "q3": 29.5, "iqr": 1, "runs": 3}
    )
    # 2 of the 8 store requests of the window (5, 1 and 2) refused or unanswered.
    assert cell["store_requests_in_window"] == 8
    assert cell["refused_or_unanswered_in_window"] == 2
    assert cell["refused_or_unanswered_share"] == pytest.approx(2 / 8)
    assert cell["heap_refusals_under_load"] == 2


def test_each_latency_has_its_bootstrap_interval_and_the_same_seed_gives_the_same(spec):
    first = table_of(spec, centre_runs(spec), seed=11)
    again = table_of(spec, centre_runs(spec), seed=11)

    commit = row(first, 1, 2)["run_kinds"]["uc2"]["commit_latency_seconds"]
    for name in ("p50", "p95", "p99"):
        low, high = commit[name]["interval"]
        assert 1 <= low <= commit[name]["value"] <= high <= 9
    assert first["rows"] == again["rows"]
    assert first["bootstrap"] == {
        "seed": 11,
        "resamples": 300,
        "interval": 0.95,
        "resampled": "whole repetitions",
    }


def test_the_invalid_runs_are_listed_and_left_out_of_the_statistics(spec):
    table = table_of(spec, centre_runs(spec))

    left_out = {run["run"]: run for run in table["left_out"]}

    assert set(left_out) == {"run-4-invalid", "run-5-failed", "run-6-interrupted"}
    assert left_out["run-4-invalid"]["why"] == (
        "invalid: it failed the checks deadlock alarm, one accepted done or failed"
    )
    assert left_out["run-4-invalid"]["configurations"] == [1, 2]
    assert left_out["run-5-failed"]["why"].startswith("failed: RuntimeError")
    assert left_out["run-6-interrupted"]["why"].startswith("interrupted")
    cell = row(table, 1, 2)["run_kinds"]["uc2"]
    assert "run-4-invalid" not in cell["runs"]
    assert max(cell["commit_latency_seconds"]["p99"]["interval"]) <= 9


def test_a_run_kind_with_no_records_leaves_its_columns_empty(spec):
    table = table_of(spec, centre_runs(spec))

    kinds = row(table, 1, 2)["run_kinds"]

    assert kinds["uc2-unwrapped"] is None
    assert kinds["baseline"] is None
    assert all(cell is None for cell in row(table, 3, 4)["run_kinds"].values())
    shown = markdown(table)
    assert "| spec 3, 4 | uc2 |  |  |  |  |  |  |  |" in shown


def test_the_stuck_failed_and_refused_of_each_load_level_with_place_and_reason(spec):
    table = table_of(spec, centre_runs(spec))

    cell = row(table, 1, 2)["run_kinds"]["uc2"]

    assert cell["stuck"] == [
        {
            "transport": "Transport7",
            "status": "running",
            "place": "Module3",
            "run": "run-1",
        }
    ]
    assert cell["failed"] == [
        {
            "transport": "Transport9",
            "place": "Module2",
            "reason": "no live route on",
            "run": "run-1",
        }
    ]
    assert cell["refused_spawns"] == [
        {
            "reason": "no free origin",
            "origin": None,
            "destination": None,
            "run": "run-1",
        },
        {
            "reason": "no candidate",
            "origin": "Module1",
            "destination": "Module5",
            "run": "run-1",
        },
        {
            "reason": "no free origin",
            "origin": None,
            "destination": None,
            "run": "run-3",
        },
    ]
    shown = markdown(table)
    assert (
        "30 boxes per minute, configurations spec 1, 2, uc2: 1 stuck, 1 failed, 3 refused"
        in shown
    )
    assert "stuck: Transport7 at Module3 (running), run run-1" in shown
    assert "failed: Transport9 at Module2: no live route on, run run-1" in shown
    assert (
        "refused spawn: no candidate (origin Module1, destination Module5), run run-1"
        in shown
    )


def test_the_shacl_overhead_is_the_difference_in_mean_commit_latency(spec):
    _, by_number = spec
    shacl_off = [
        record(f"off-{n}", by_number[3], repetition=n, commit_latencies=[1, 1, 1])
        for n in (1, 2, 3)
    ]

    table = table_of(spec, centre_runs(spec) + shacl_off)

    assert row(table, 1, 2)["shacl_overhead"] == pytest.approx(
        {
            "shacl_off": [3, 4],
            "mean_commit_latency_seconds_on": 5.0,
            "mean_commit_latency_seconds_off": 1.0,
            "difference_seconds": 4.0,
        }
    )
    assert row(table, 3, 4)["shacl_overhead"] is None


def test_the_table_compares_a_calibration_pair_and_gives_the_matcher_s_reads(spec):
    """Configuration 2 is configuration 1 without the wrapper: one row, two run kinds."""
    _, by_number = spec
    unwrapped = [
        record(
            f"unwrapped-{n}",
            by_number[2],
            repetition=n,
            run_kind="uc2-unwrapped",
            transports_ended_per_minute=ended,
            matcher_reads=140,
            matcher_seconds=2.5,
        )
        for n, ended in ((1, 27), (2, 26), (3, 25))
    ]

    table = table_of(spec, centre_runs(spec) + unwrapped)

    calibration = row(table, 1, 2)["calibration"]
    assert calibration["transports_ended_per_minute"] == pytest.approx(
        {"uc2": 29, "uc2-unwrapped": 26, "difference": 3, "relative": 3 / 26}
    )
    # Refused spawns per run: 2, 0 and 1 with the wrapper, none without it.
    assert calibration["refused_spawns_per_run"] == pytest.approx(
        {"uc2": 1, "uc2-unwrapped": 0, "difference": 1, "relative": None}
    )
    assert calibration["matcher"]["uc2"] == {
        "interval_seconds": [2.0],
        "reads_per_run": {"median": 150, "q1": 150, "q3": 150, "iqr": 0, "runs": 3},
        "read_seconds_per_run": {
            "median": 3.0,
            "q1": 3.0,
            "q3": 3.0,
            "iqr": 0.0,
            "runs": 3,
        },
    }
    assert calibration["matcher"]["uc2-unwrapped"]["reads_per_run"]["median"] == 140
    assert (
        calibration["matcher"]["uc2-unwrapped"]["read_seconds_per_run"]["median"] == 2.5
    )
    unwrapped_cell = row(table, 1, 2)["run_kinds"]["uc2-unwrapped"]
    assert unwrapped_cell["commit_latency_seconds"] is None
    assert unwrapped_cell["store_request_latency_seconds"] is None
    shown = markdown(table)
    assert "| spec 1, 2 | 29 | 26 | 3 (+11.5%) | 1 | 0 | 1 |" in shown
    assert "150 reads every 2 s, 3 s" in shown
    assert row(table, 3, 4)["calibration"] is None


def test_a_baseline_replaying_a_run_takes_its_columns_in_that_run_s_row(spec):
    """A store-only baseline (jms_kapps 81) names the run it replays; it has store requests and
    no commit, no transport and no matcher."""
    baseline = record(
        "baseline-1",
        None,
        run_kind="baseline",
        replays="run-1",
        requests=[request(0.02), request(0.04), request(0.06, kind="read")],
    )
    lost = record("baseline-2", None, run_kind="baseline", replays="no-such-run")

    table = table_of(spec, [*centre_runs(spec), baseline, lost])

    cell = row(table, 1, 2)["run_kinds"]["baseline"]
    assert cell["runs"] == ["baseline-1"]
    assert cell["store_request_latency_seconds"]["p50"]["value"] == pytest.approx(0.04)
    assert cell["commit_latency_seconds"] is None
    assert cell["transports_ended_per_minute"] is None
    assert cell["matcher"] is None
    [left] = [run for run in table["left_out"] if run["run"] == "baseline-2"]
    assert "no-such-run" in left["why"]


def ramp_level(spec, number: int, run_id: str, **numbers: Any) -> dict[str, Any]:
    _, by_number = spec
    rate = by_number[number].values["boxes_per_minute"]
    values: dict[str, Any] = {
        "commit_latencies": [0.1] * 20,
        "requests": [request(0.01)] * 100,
        "completed_writes_per_minute": 10.0 * rate,
    }
    return record(run_id, by_number[number], **(values | numbers))


def test_the_table_reports_the_level_before_a_ramp_s_first_broken_one_as_its_scale(
    spec,
):
    levels = [
        ramp_level(spec, 5, "ramp-10"),
        ramp_level(spec, 6, "ramp-20"),
        # A heap refusal breaks the level; the 205 completed writes a minute, 2.5 % more than the
        # 200 at 20 boxes per minute, are a plateau, which is shown and breaks nothing
        # (jms_kapps 99).
        ramp_level(
            spec,
            7,
            "ramp-40",
            completed_writes_per_minute=205.0,
            heap_refusals_in_window=1,
        ),
    ]

    table = table_of(spec, levels)

    [ramp] = table["ramps"]
    assert ramp["block"] == 2
    assert ramp["broken_at"] == 40
    assert ramp["scale"] == 20
    assert [level["configuration"] for level in ramp["levels"]] == [5, 6, 7]
    assert [s["signal"] for s in ramp["levels"][-1]["signals"]] == [
        "heap refusal",
        "plateau",
    ]
    assert row(table, 5)["ramp"] == 2
    shown = markdown(table)
    assert "### The ramp of block 2" in shown
    assert "Scale: 20 boxes per minute." in shown


def test_the_command_writes_the_table_beside_the_records_and_needs_no_store(
    spec, tmp_path, capsys
):
    found, _ = spec
    runs = tmp_path / "runs"
    write(runs, *centre_runs(spec))

    code = main(
        [str(found.path), "--runs", str(runs), "--seed", "3", "--resamples", "200"]
    )

    assert code == 0
    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    assert table["bootstrap"]["seed"] == 3
    assert table["spec"] == str(found.path)
    assert row(table, 1, 2)["run_kinds"]["uc2"]["runs"] == ["run-1", "run-2", "run-3"]
    shown = (runs / "table.md").read_text(encoding="utf-8")
    for section in (
        "## Latency (seconds)",
        "## Throughput (per minute)",
        "## SHACL overhead",
        "## Calibration pairs",
        "## Ramps",
        "## Runs left out of the statistics",
    ):
        assert section in shown
    assert "table.json" in capsys.readouterr().out


def test_the_command_refuses_a_runs_folder_that_is_not_there(spec, tmp_path, capsys):
    found, _ = spec

    code = main([str(found.path), "--runs", str(tmp_path / "none")])

    assert code == 2
    assert "none" in capsys.readouterr().err


def test_a_record_of_another_spec_is_no_run_of_this_table(spec, tmp_path):
    other = record("other-1", None)
    other["sweep"] = {"key": "0123456789abcdef", "configuration": 1, "repetition": 1}

    table = table_of(spec, [*centre_runs(spec), other])

    assert table["records_of_no_configuration_of_the_spec"] == 1
    assert "other-1" not in json.dumps(table["rows"])


def test_the_readme_says_how_to_make_the_table_a_calibration_pair_and_a_ramp():
    text = README.read_text(encoding="utf-8")

    assert "jms-eval-table" in text
    assert "calibration pair" in text
    assert "wrapper = [true, false]" in text
    assert "ramp = true" in text
    assert "--heavy-run-approval" in text


def test_the_table_of_the_short_calibration_pair_made_by_part_1_s_run_on_stand_ins(
    tmp_path, monkeypatch, capsys
):
    """The shipped short calibration pair, swept by part 1's real run against the stand-ins of
    `_evaluation_fakes` (no store): its records, in the form a run writes them, give one row with
    both run kinds, its comparison and the matcher's reads."""
    from test_evaluation_sweep import Runs

    from jms_kapps.evaluation import sweep

    pair = README.parent / "specs" / "calibration-cross-short.toml"
    runs = tmp_path / "runs"
    made = Runs()
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", made)
    assert sweep.main([str(pair), "--runs", str(runs)]) == 0
    assert [c.wrapper for c, _ in made.made] == [True, False]

    assert main([str(pair), "--runs", str(runs), "--resamples", "100"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    [pair_row] = table["rows"]
    assert pair_row["configurations"] == [1, 2]
    kinds = pair_row["run_kinds"]
    assert len(kinds["uc2"]["runs"]) == 1
    assert len(kinds["uc2-unwrapped"]["runs"]) == 1
    assert kinds["baseline"] is None
    calibration = pair_row["calibration"]
    assert set(calibration["transports_ended_per_minute"]) == {
        "uc2",
        "uc2-unwrapped",
        "difference",
        "relative",
    }
    for kind in ("uc2", "uc2-unwrapped"):
        matcher = calibration["matcher"][kind]
        assert matcher["interval_seconds"] == [2.0]
        assert matcher["reads_per_run"]["median"] > 0
    assert table["left_out"] == []


def test_a_free_text_stays_on_one_line_of_the_markdown(spec):
    """A store's reason may hold line breaks and bars, as a SHACL report does."""
    _, by_number = spec
    failed = record(
        "run-7-failed",
        by_number[1],
        outcome="failed: TripleStoreResponseError: line one\nline | two",
    )

    shown = markdown(table_of(spec, [*centre_runs(spec), failed]))

    assert "| failed: TripleStoreResponseError: line one line \\| two |" in shown


def test_a_baseline_of_a_run_left_out_is_left_out(spec):
    """The baseline of an invalid run replays the requests of a run that does not count."""
    baseline = record(
        "baseline-3",
        None,
        run_kind="baseline",
        replays="run-4-invalid",
        requests=[request(0.02)],
    )

    table = table_of(spec, [*centre_runs(spec), baseline])

    assert row(table, 1, 2)["run_kinds"]["baseline"] is None
    [left] = [run for run in table["left_out"] if run["run"] == "baseline-3"]
    assert left["why"] == "it replays the run run-4-invalid, which is left out"
    assert left["configurations"] == [1, 2]


def test_the_markdown_shows_each_ramp_level_s_numbers(spec):
    levels = [
        ramp_level(spec, 5, "ramp-10"),
        ramp_level(spec, 6, "ramp-20", refused_in_window=3),
    ]

    shown = markdown(table_of(spec, levels))

    # The spawn columns are empty for a record that holds no spawn count (before jms_kapps 99).
    assert (
        "| 10 | spec 5 | ramp-10 | yes | 0 | 0.00% | 0.1 | 100 | 20 |  |  |  |  |  |  |  |"
        in shown
    )
    assert (
        "| 20 | spec 6 | ramp-20 | yes | 0 | 3.00% | 0.1 | 200 | 20 |  |  |  |  "
        "| refused or unanswered |  |  |" in shown
    )


def test_a_baseline_made_by_jms_kapps_81_s_code_takes_its_columns_in_its_run_s_row(
    tmp_path,
):
    """Both records in the form their code writes them: a UC2 run recorded by part 1 and part 2
    against their stand-ins, and its store-only baseline made by `baseline.run_baseline` against
    a stand-in store (jms_kapps 81). The table finds the baseline's row by `replayed_run`, and
    reads its measures as part 2's: the accepted request of the window, its share of refused or
    timed out, its completed writes, and the refusal for want of heap in its window but not the
    one before it."""
    from test_evaluation_baseline import (
        HEAP_REASON,
        Baseline,
        accepted,
        no_answer,
        read,
        recorded_run,
        refused,
    )
    from test_evaluation_baseline import write as recorded_write

    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 10.0, "SELECT DISTINCT ?refused_before_the_window"),
            recorded_write(clock, 31.0, "INSERT DATA { <s> <p> <accepted> }"),
            read(clock, 33.0, "SELECT DISTINCT ?refused_for_want_of_heap"),
            read(clock, 34.0, "SELECT ?timed_out"),
        ],
    )
    baseline = Baseline(
        tmp_path,
        answers={
            "INSERT DATA { <s> <p> <accepted> }": accepted(at=1.25),
            "SELECT DISTINCT ?refused_for_want_of_heap": refused(500, HEAP_REASON),
            "SELECT ?timed_out": no_answer(at=10.0),
            "SELECT DISTINCT ?refused_before_the_window": refused(500, HEAP_REASON),
        },
    )
    made = baseline.run(recorded)
    assert made["outcome"] == "completed"

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(SPEC, encoding="utf-8")
    found = read_spec(spec_path)
    one = next(c for c in configurations(found) if c.number == 1)
    replayed = json.loads((recorded / "record.json").read_text(encoding="utf-8"))
    # The recorded run as the sweep would have recorded it: configuration 1 of the spec.
    replayed["sweep"] = {"key": one.key, "configuration": 1, "repetition": 1}
    runs = tmp_path / "runs"
    write(runs, replayed, made)

    assert main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    kinds = row(table, 1, 2)["run_kinds"]
    assert kinds["uc2"]["runs"] == [replayed["run_id"]]
    cell = kinds["baseline"]
    assert cell["runs"] == [made["run_id"]]
    assert cell["store_request_latency_seconds"]["count"] == 1
    assert cell["store_request_latency_seconds"]["p50"]["value"] == pytest.approx(0.25)
    assert cell["store_requests_in_window"] == 3
    assert cell["refused_or_unanswered_share"] == pytest.approx(2 / 3)
    assert cell["heap_refusals_under_load"] == 1
    assert cell["completed_writes_per_minute"]["median"] == pytest.approx(1.0)
    assert cell["commit_latency_seconds"] is None
    assert cell["transports_ended_per_minute"] is None
    assert cell["matcher"] is None
    assert table["left_out"] == []
