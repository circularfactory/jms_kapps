"""The commit latency of the two arms of a comparison, read from fabricated run records alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jms_kapps.evaluation.arm_latency import arm_latency, main, render
from jms_kapps.evaluation.summary import read_summaries


def write_run(
    arm: Path,
    name: str,
    latencies: list[float],
    *,
    outcome: str = "completed",
    valid: bool | None = True,
) -> None:
    folder = arm / name
    folder.mkdir(parents=True)
    record = {
        "run_id": name,
        "run_kind": "uc2",
        "outcome": outcome,
        "valid": valid,
        "measures": {"commit_latencies_seconds": latencies},
    }
    (folder / "record.json").write_text(json.dumps(record), encoding="utf-8")


def test_an_arm_pools_the_commits_of_its_runs_that_count(tmp_path: Path) -> None:
    write_run(tmp_path, "r1", [0.010, 0.020])
    write_run(tmp_path, "r2", [0.030, 0.040, 0.050])
    write_run(tmp_path, "r3", [9.0], valid=False)

    arm = arm_latency(read_summaries(tmp_path))

    assert arm.runs == 2
    assert arm.commits == 5
    assert arm.median == pytest.approx(0.030)
    assert arm.p95 == pytest.approx(0.048)
    assert len(arm.left_out) == 1
    assert arm.left_out[0].startswith("r3: invalid")


def test_an_arm_without_commits_says_none(tmp_path: Path) -> None:
    arm = arm_latency(read_summaries(tmp_path))

    assert (arm.runs, arm.commits, arm.median, arm.p95) == (0, 0, None, None)
    assert "median none, 95th percentile none" in render("empty", arm)


def test_the_command_prints_both_arms_in_milliseconds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_run(tmp_path / "with", "w1", [0.010, 0.030])
    write_run(tmp_path / "without", "o1", [0.020])

    assert main([str(tmp_path / "with"), str(tmp_path / "without")]) == 0

    out = capsys.readouterr().out
    assert "with the shape: 1 runs that count, 2 commits" in out
    assert "median 20.0 ms, 95th percentile 29.0 ms" in out
    assert "without the shape: 1 runs that count, 1 commits" in out
    assert "median 20.0 ms, 95th percentile 20.0 ms" in out
