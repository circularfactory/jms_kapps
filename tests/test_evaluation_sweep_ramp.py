"""The ramp of the measurement harness: a sweep block marked as a ramp, with rising
box rates, whose runs are heavy. The sweep runs it one rate per run, in rising order, judges each
level with the broken rule after its run, and stops at the first broken level.

No test here makes a run: a ramp's runs are fabricated records, written by a stand-in for a run on
the configured store (a heavy run is never a test or a CI job). The
approvals here are for the spec of a temporary folder. The connection variables of a
store are removed first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _evaluation_approvals import approval_file
from _evaluation_records import record, request, write

from jms_kapps.evaluation import sweep
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.spec import (
    SpecRefused,
    configurations,
    ramps_of,
    read_spec,
    resolved,
)
from jms_kapps.evaluation.sweep import SweepRun, main, run_sweep

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 2

# 1: a configuration at a normal rate.
[[sweep]]
topology = "cross"

# 2-6: a ramp.
[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20, 40, 80, 160]
repetitions = 1

# 7: a configuration after the ramp.
[[sweep]]
topology = "pair"
"""


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


def spec_file(folder: Path, text: str = SPEC) -> Path:
    path = folder / "spec.toml"
    path.write_text(text, encoding="utf-8")
    return path


class FabricatedRuns:
    """In place of a run on the configured store: each run writes a fabricated record, with the
    numbers of its box rate, and keeps which run it was and the approval it was given."""

    def __init__(
        self, broken_from: float | None = None, limited_from: float | None = None
    ) -> None:
        self.broken_from = broken_from
        # From this rate on, 30 of 100 offered spawns are refused, and the writes stand still.
        self.limited_from = limited_from
        self.made: list[tuple[int, float, str | None]] = []

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        approval: object = None,
        **_: object,
    ) -> dict[str, Any]:
        rate = configuration.boxes_per_minute
        self.made.append((sweep["configuration"], rate, approval))
        broken = self.broken_from is not None and rate >= self.broken_from
        limited = self.limited_from is not None and rate >= self.limited_from
        found = record(
            f"run-{len(self.made)}",
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 100,
            completed_writes_per_minute=(
                10.0 * self.limited_from if limited else 10.0 * rate
            ),
            heap_refusals_in_window=1 if broken else 0,
            spawns_accepted=70 if limited else 100,
            spawns_refused=30 if limited else 0,
        )
        found["sweep"] = dict(sweep)
        write(runs, found)
        return found


def test_a_ramp_s_levels_are_heavy_in_rising_order_whatever_the_defaults_say(tmp_path):
    found = configurations(read_spec(spec_file(tmp_path)))

    [(block, levels)] = ramps_of(found).items()

    assert block == 2
    assert [level.number for level in levels] == [2, 3, 4, 5, 6]
    assert [level.values["boxes_per_minute"] for level in levels] == [
        10,
        20,
        40,
        80,
        160,
    ]
    assert all(level.values["heavy"] is True for level in levels)
    assert all(level.repetitions == 1 for level in levels)
    assert [c.ramp for c in found if c.number in (1, 7)] == [None, None]
    assert found[0].values["heavy"] is False


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (("ramp = true\ntopology", "ramp = 1\ntopology"), "true or false"),
        (
            (
                'topology = "cross"\nboxes_per_minute',
                'topology = ["cross", "pair"]\nboxes_per_minute',
            ),
            "only axis",
        ),
        (("[10, 20, 40, 80, 160]", "40"), "rising box rates"),
        (("[10, 20, 40, 80, 160]", "[10, 40, 20]"), "rise"),
        (("[10, 20, 40, 80, 160]", "[10]"), "at least two"),
        (("[10, 20, 40, 80, 160]", "[10, 20, 20]"), "twice"),
        (("repetitions = 1\n", "repetitions = 5\n"), "runs each rate once"),
        (("repetitions = 1\n", "repetitions = 1\nheavy = false\n"), "heavy"),
        (("repetitions = 1\n", "repetitions = 1\nwrapper = false\n"), "wrapper"),
        (
            (
                "heavy = false\nrepetitions = 2",
                "heavy = false\nrepetitions = 2\nramp = true",
            ),
            "ramp",
        ),
    ],
)
def test_a_ramp_that_is_none_is_refused(tmp_path, change, named):
    old, new = change
    assert old in SPEC
    text = SPEC.replace(old, new, 1)

    with pytest.raises(SpecRefused, match=named):
        configurations(read_spec(spec_file(tmp_path, text)))


def test_a_flag_that_says_a_ramp_is_not_heavy_is_refused(tmp_path):
    with pytest.raises(SpecRefused, match="heavy"):
        configurations(read_spec(spec_file(tmp_path)), overrides={"heavy": False})


def test_a_ramp_s_configuration_that_another_block_lists_too_is_refused(tmp_path):
    text = (
        SPEC
        + '\n[[sweep]]\ntopology = "cross"\nboxes_per_minute = 40\nheavy = true\nrepetitions = 1\n'
    )

    with pytest.raises(SpecRefused, match="a ramp's configurations are its own"):
        configurations(read_spec(spec_file(tmp_path, text)))


def test_a_ramp_without_the_approval_switch_is_skipped_and_listed(
    tmp_path, monkeypatch, capsys
):
    """Its levels are skipped and listed, and the sweep makes the runs at normal load."""
    fabricated = FabricatedRuns()
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)
    runs = tmp_path / "runs"

    code = main([str(spec_file(tmp_path)), "--runs", str(runs)])

    assert code == 0
    assert [number for number, _, _ in fabricated.made] == [1, 1, 7, 7]
    out = capsys.readouterr().out
    assert (
        "configuration 2, repetition 1 (the ramp of block 2 at 10 boxes per minute): "
        "no approval" in out
    )
    assert "--heavy-run-approval" in out
    assert "any person may approve" in out
    assert "Etienne" not in out


def test_a_selection_without_a_ramp_s_level_needs_no_approval(tmp_path, monkeypatch):
    fabricated = FabricatedRuns()
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = main(
        [
            str(spec_file(tmp_path)),
            "--runs",
            str(tmp_path / "runs"),
            "--configurations",
            "1,7",
        ]
    )

    assert code == 0
    assert [(number, approval) for number, _, approval in fabricated.made] == [
        (1, None),
        (1, None),
        (7, None),
        (7, None),
    ]


def test_one_approval_covers_the_levels_of_two_ramps(tmp_path, monkeypatch):
    """One approval covers one spec for one period: a selection with levels of two
    ramps to make runs, each level under the approval."""
    second = '\n[[sweep]]\nramp = true\ntopology = "pair"\nboxes_per_minute = [10, 20]\nrepetitions = 1\n'
    spec_path = spec_file(tmp_path, SPEC + second)
    approval = approval_file(tmp_path, spec_path)
    fabricated = FabricatedRuns()
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = main(
        [
            str(spec_path),
            "--runs",
            str(tmp_path / "runs"),
            "--configurations",
            "2-6,8-9",
            "--heavy-run-approval",
            str(approval),
        ]
    )

    assert code == 0
    assert [number for number, _, _ in fabricated.made] == [2, 3, 4, 5, 6, 8, 9]
    assert all(given is not None for _, _, given in fabricated.made)


def plan(spec_path: Path) -> tuple[list[SweepRun], Any, list[Any]]:
    """Every run of the spec, each with its configuration resolved: the plan the sweep makes
    once a ramp's approval is given, made here without it."""
    spec = read_spec(spec_path)
    found = configurations(spec)
    to_make = [
        SweepRun(c, repetition, resolved(c, spec=spec, prefills=spec_path.parent))
        for c in found
        for repetition in range(1, c.repetitions + 1)
    ]
    return to_make, spec, found


def test_the_sweep_runs_a_ramp_in_rising_order_and_stops_it_at_its_first_broken_level(
    tmp_path,
):
    """The level at 40 boxes per minute shows a refusal for want of heap: 80 and 160 are not
    run, and the sweep goes on with the configuration after the ramp. No approval is given here:
    the stand-in makes no run."""
    to_make, spec, found = plan(spec_file(tmp_path))
    fabricated = FabricatedRuns(broken_from=40)
    lines: list[str] = []

    completed = run_sweep(
        spec,
        to_make,
        runs=tmp_path / "runs",
        run_one=fabricated,
        echo=lines.append,
        found=found,
    )

    assert completed
    assert [(number, rate) for number, rate, _ in fabricated.made] == [
        (1, 30),
        (1, 30),
        (2, 10),
        (3, 20),
        (4, 40),
        (7, 30),
        (7, 30),
    ]
    assert all(approval is None for _, _, approval in fabricated.made)
    [ended] = [line for line in lines if "the ramp of block 2 ends" in line]
    assert "it broke at 40 boxes per minute (heap refusal)" in ended
    assert "its scale is 20 boxes per minute" in ended
    assert "2 higher levels are not run" in ended


def test_a_ramp_goes_on_past_a_uc2_limited_level_with_a_plateau_and_stops_at_a_heap_refusal(
    tmp_path,
):
    """From 40 boxes per minute on, 30 % of the spawns are refused and the writes
    plateau: the old rule stopped the ramp at 40. Now it runs 80, where the store refuses for
    want of heap, and stops there."""
    to_make, spec, found = plan(spec_file(tmp_path))
    fabricated = FabricatedRuns(broken_from=80, limited_from=40)
    lines: list[str] = []

    run_sweep(
        spec,
        to_make,
        runs=tmp_path / "runs",
        run_one=fabricated,
        echo=lines.append,
        found=found,
    )

    assert [rate for number, rate, _ in fabricated.made if 2 <= number <= 6] == [
        10,
        20,
        40,
        80,
    ]
    [ended] = [line for line in lines if "the ramp of block 2 ends" in line]
    assert "it broke at 80 boxes per minute (heap refusal)" in ended
    assert "Its 1 higher levels are not run" in ended


def test_a_ramp_goes_on_to_its_top_level_past_a_uc2_limited_level_with_a_plateau(
    tmp_path,
):
    to_make, spec, found = plan(spec_file(tmp_path))
    fabricated = FabricatedRuns(limited_from=40)

    run_sweep(
        spec,
        to_make,
        runs=tmp_path / "runs",
        run_one=fabricated,
        echo=lambda line: None,
        found=found,
    )

    assert [rate for number, rate, _ in fabricated.made if 2 <= number <= 6] == [
        10,
        20,
        40,
        80,
        160,
    ]


def test_a_ramp_that_is_not_broken_runs_every_rate(tmp_path):
    to_make, spec, found = plan(spec_file(tmp_path))
    fabricated = FabricatedRuns()

    run_sweep(
        spec,
        to_make,
        runs=tmp_path / "runs",
        run_one=fabricated,
        echo=lambda line: None,
        found=found,
    )

    assert [rate for number, rate, _ in fabricated.made if 2 <= number <= 6] == [
        10,
        20,
        40,
        80,
        160,
    ]


def test_a_ramp_that_ended_in_an_earlier_call_sends_no_higher_rate_and_needs_no_approval(
    tmp_path, capsys
):
    """The levels up to the broken one have their records; those above it are not run, so the
    call has no level of the ramp to make."""
    spec_path = spec_file(tmp_path)
    to_make, spec, found = plan(spec_path)
    runs = tmp_path / "runs"
    run_sweep(
        spec,
        to_make,
        runs=runs,
        run_one=FabricatedRuns(broken_from=40),
        echo=lambda line: None,
        found=found,
    )

    code = main([str(spec_path), "--runs", str(runs)])

    assert code == 0
    assert (
        "2 are above the level their ramp ended at, 0 to make"
        in capsys.readouterr().out
    )


def test_a_ramp_level_whose_run_failed_stops_the_ramp_in_the_next_call(
    tmp_path, capsys
):
    """A level with a run but none that counts cannot be judged: the next call skips the failed
    run, and the ramp's higher levels with it."""
    spec_path = spec_file(tmp_path)
    runs = tmp_path / "runs"
    _, _, found = plan(spec_path)
    by_number = {c.number: c for c in found}
    write(
        runs,
        record("ramp-10", by_number[2]),
        record("ramp-20", by_number[3], outcome="failed: x"),
    )

    code = main([str(spec_path), "--runs", str(runs), "--configurations", "2-6"])

    assert code == 0
    assert (
        "3 are above the level their ramp ended at, 0 to make"
        in capsys.readouterr().out
    )


def test_a_ramp_s_level_above_one_not_made_is_refused(tmp_path, capsys):
    """A ramp runs its rates in rising order and judges each before the next: a selection of 40
    boxes per minute alone, with 10 and 20 not made, would send a heavy rate that no broken rule
    guards. Refused even under an approval that covers the spec."""
    spec_path = spec_file(tmp_path)
    code = main(
        [
            str(spec_path),
            "--runs",
            str(tmp_path / "runs"),
            "--configurations",
            "4",
            "--heavy-run-approval",
            str(approval_file(tmp_path, spec_path)),
        ]
    )

    assert code == 2
    error = capsys.readouterr().err
    assert "rising order" in error
    assert "configurations 2, 3 have no record" in error
