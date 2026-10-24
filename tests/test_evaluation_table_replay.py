"""The table keeps the replay levels of the store-only baseline apart: one column per
factor, and the factor-1 column as it was. A runs folder made from fabricated records, read by
`jms-eval-table` and `jms-eval-report`; no store, no run.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from _evaluation_records import record, request, write

from jms_kapps.evaluation import report, table
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.summary import summary_of
from jms_kapps.evaluation.table import make_table, markdown

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 1

[[sweep]]
topology = "cross"
replay_factors = [1, 2, 4]
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the table or the report opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def spec_path(tmp_path) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    return path


@pytest.fixture
def one(spec_path):
    return configurations(read_spec(spec_path))[0]


def level(
    factor: float,
    *,
    broken: bool = False,
    offered: float = 100.0,
    achieved: float = 100.0,
    refused: int = 0,
    timed_out: int = 0,
    heap: int = 0,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The `replay_level` block of a baseline's record."""
    return {
        "factor": factor,
        "offered_per_minute": offered * factor,
        "achieved_per_minute": achieved * factor,
        "requests_in_window": 100,
        "answered_in_window": 100 - timed_out,
        "refused_in_window": refused,
        "timed_out_in_window": timed_out,
        "heap_refusals_in_window": heap,
        "replay_span_seconds": 60.0 / factor,
        "broken": broken,
        "signals": signals or [],
    }


def baseline(
    run_id: str,
    factor: float | None,
    *,
    replays: str = "uc2-1",
    latency: float = 0.02,
    **options: Any,
) -> dict[str, Any]:
    """A baseline of the run `replays` at `factor`; None for a record that says no level, as an
    earlier baseline's."""
    found = record(
        run_id,
        None,
        run_kind="baseline",
        replays=replays,
        requests=[request(latency), request(latency * 2, kind="read")],
        **{k: v for k, v in options.items() if k in ("outcome", "valid")},
    )
    if factor is not None:
        found["replay_level"] = level(
            factor,
            **{k: v for k, v in options.items() if k not in ("outcome", "valid")},
        )
    return found


def uc2(one, **options: Any) -> dict[str, Any]:
    return record(
        "uc2-1",
        one,
        commit_latencies=[0.1, 0.2],
        requests=[request(0.1)],
        completed_writes_per_minute=10.0,
        transports_ended_per_minute=5.0,
        **options,
    )


def table_of(spec_path: Path, one, records: list[dict[str, Any]]) -> dict[str, Any]:
    return make_table(
        read_spec(spec_path),
        [one],
        [summary_of(r) for r in records],
        runs=Path("runs"),
        resamples=200,
    )


def only_row(made: dict[str, Any]) -> dict[str, Any]:
    [found] = made["rows"]
    return found


def test_the_factor_one_column_shows_what_it_showed_before(spec_path, one):
    """A baseline that says its level at factor 1 and one of an earlier record, which says none,
    are the same cell."""
    with_level = table_of(spec_path, one, [uc2(one), baseline("b", 1.0)])
    without = table_of(spec_path, one, [uc2(one), baseline("b", None)])

    new = only_row(with_level)["run_kinds"]["baseline"]
    old = only_row(without)["run_kinds"]["baseline"]

    assert new == old
    assert new["runs"] == ["b"]
    assert new["store_request_latency_seconds"]["p50"]["value"] == pytest.approx(0.03)
    assert only_row(with_level)["replays"] == {}


def test_each_factor_above_one_has_a_column_of_its_own(spec_path, one):
    made = table_of(
        spec_path,
        one,
        [
            uc2(one),
            baseline("b1", 1.0, latency=0.02),
            baseline("b2", 2.0, latency=0.04),
            baseline("b4", 4.0, latency=0.08),
        ],
    )

    found = only_row(made)

    assert found["run_kinds"]["baseline"]["runs"] == ["b1"]
    assert list(found["replays"]) == ["2", "4"]
    assert found["replays"]["2"]["runs"] == ["b2"]
    assert found["replays"]["4"]["runs"] == ["b4"]
    # Each column has its own run's numbers.
    p50 = {
        name: cell["store_request_latency_seconds"]["p50"]["value"]
        for name, cell in (
            ("1", found["run_kinds"]["baseline"]),
            ("2", found["replays"]["2"]),
            ("4", found["replays"]["4"]),
        )
    }
    assert p50["1"] == pytest.approx(0.03)
    assert p50["2"] == pytest.approx(0.06)
    assert p50["4"] == pytest.approx(0.12)


def test_the_levels_of_a_row_are_listed_in_rising_order_of_the_factor_with_their_rates(
    spec_path, one
):
    broken = {"signal": "achieved rate below offered", "share": 0.5}
    made = table_of(
        spec_path,
        one,
        [
            uc2(one),
            baseline(
                "b4", 4.0, broken=True, achieved=50.0, signals=[broken], refused=2
            ),
            baseline("b1", 1.0),
            baseline("b2", 2.0),
        ],
    )

    levels = only_row(made)["replay_levels"]

    assert [(entry["factor"], entry["run"]) for entry in levels] == [
        (1.0, "b1"),
        (2.0, "b2"),
        (4.0, "b4"),
    ]
    last = levels[-1]
    assert last["offered_per_minute"] == pytest.approx(400.0)
    assert last["achieved_per_minute"] == pytest.approx(200.0)
    assert (last["refused_in_window"], last["timed_out_in_window"]) == (2, 0)
    assert last["broken"] is True
    assert last["signals"] == [broken]
    assert levels[0]["broken"] is False


def test_the_text_of_the_table_shows_a_column_for_each_factor_and_the_levels(
    spec_path, one
):
    broken = {"signal": "heap refusal", "heap_refusals": 1}
    made = table_of(
        spec_path,
        one,
        [
            uc2(one),
            baseline("b1", 1.0),
            baseline("b2", 2.0, broken=True, heap=1, signals=[broken]),
        ],
    )

    shown = markdown(made)

    # A row of the latency section for each run kind that counts, and one for the factor of 2.
    assert "| spec 1 | baseline | 1 |" in shown
    assert "| spec 1 | baseline x2 | 1 |" in shown
    section = shown.split("## Replay levels")[1].split("\n## ")[0]
    # Each level with the repetition of its replay: a record that names none is
    # of the first.
    assert (
        "| Configurations | Repetition | Factor | Run | Offered per minute |" in section
    )
    # The dropped connections have a column of their own.
    assert "| spec 1 | 1 | 1 | b1 | 100 | 100 | 100 | 0 | 0 | 0 | 0 |  |" in section
    assert (
        "| spec 1 | 1 | 2 | b2 | 200 | 200 | 100 | 0 | 0 | 0 | 1 | heap refusal |"
        in section
    )


def test_a_table_of_a_spec_without_a_replay_level_has_no_section_for_it(spec_path, one):
    shown = markdown(table_of(spec_path, one, [uc2(one), baseline("b", None)]))

    assert "## Replay levels" not in shown
    assert "baseline x" not in shown


def test_a_baseline_of_a_run_that_is_left_out_is_left_out_at_every_factor(
    spec_path, one
):
    made = table_of(
        spec_path,
        one,
        [uc2(one, valid=False), baseline("b2", 2.0), baseline("b1", 1.0)],
    )

    found = only_row(made)

    assert found["run_kinds"]["baseline"] is None
    assert found["replays"] == {}
    assert found["replay_levels"] == []
    assert {run["run"] for run in made["left_out"]} >= {"uc2-1", "b1", "b2"}


def test_a_level_that_failed_is_left_out_with_its_reason_and_has_no_numbers(
    spec_path, one
):
    made = table_of(
        spec_path,
        one,
        [uc2(one), baseline("b2", 2.0, outcome="failed: ReplayRefused: login")],
    )

    found = only_row(made)

    assert found["replays"]["2"] is None
    [left] = [run for run in made["left_out"] if run["run"] == "b2"]
    assert "failed" in left["why"]


def test_the_command_writes_the_table_of_a_runs_folder_with_the_levels(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(
        runs,
        uc2(one),
        baseline("b1", 1.0),
        baseline("b2", 2.0),
        baseline("old", None, replays="uc2-1"),
    )

    code = table.main([str(spec_path), "--runs", str(runs), "--resamples", "100"])

    assert code == 0
    made = json.loads((runs / "table.json").read_text())
    found = only_row(made)
    # The baseline of the earlier record and the factor-1 one are the factor-1 column.
    assert sorted(found["run_kinds"]["baseline"]["runs"]) == ["b1", "old"]
    assert found["replays"]["2"]["runs"] == ["b2"]
    assert "## Replay levels" in (runs / "table.md").read_text(encoding="utf-8")


def test_the_report_still_reads_the_baselines_of_earlier_records_beside_the_levels(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(
        runs,
        uc2(one),
        baseline("old", None),
        baseline("b1", 1.0),
        baseline("b2", 2.0),
        baseline("b4", 4.0, broken=True, signals=[{"signal": "heap refusal"}], heap=1),
    )

    code = report.main([str(spec_path), "--runs", str(runs), "--resamples", "100"])

    assert code == 0
    listed = pd.read_csv(runs / "report" / "runs.csv")
    assert sorted(listed["run"]) == ["b1", "b2", "b4", "old", "uc2-1"]
    assert set(listed[listed["run_kind"] == "baseline"]["status"]) == {"completed"}
