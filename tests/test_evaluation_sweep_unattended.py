"""The sweep runs with no person present: a failed run does
not stop the sweep, 3 failed runs in a row do, and a heavy run that no approval covers at its start
is skipped and listed, so that the sweep goes on with the next run.

No test here makes a run: each run and each replay level is a fabricated record, written by a
stand-in for a run on the configured store (a heavy run is never a test or a CI job). The clock is
a stand-in too, and each run takes 12 minutes of it. The approvals are for the spec of a temporary
folder. The connection variables of a store are removed first.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _evaluation_approvals import approval_file
from _evaluation_records import record, request, write

import jms_kapps
from jms_kapps.evaluation import harness, sweep
from jms_kapps.evaluation.approval import Covered
from jms_kapps.evaluation.baseline import BaselineRefused
from jms_kapps.evaluation.configuration import Configuration

README = Path(jms_kapps.__file__).parent / "evaluation" / "README.md"

START = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)

# Three configurations at normal load, two repetitions each: six runs.
FIXED = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 2

[[sweep]]
topology = ["cross", "pair", "grid"]
"""

# Three configurations under a rule of at most 3 runs, at least 2 valid.
RULED = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false

[precision]
minimum_runs = 2
maximum_runs = 3

[[sweep]]
topology = ["cross", "pair", "grid"]
"""

# A configuration replayed at the factors 1, 2 and 4, a ramp of two levels, a configuration at
# normal load, and a sustained load: one run each.
HEAVY = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 1

# 1: replayed at three factors; the factors 2 and 4 are heavy.
[[sweep]]
topology = "cross"
replay_factors = [1, 2, 4]

# 2-3: a ramp.
[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20]
repetitions = 1

# 4: at normal load.
[[sweep]]
topology = "pair"

# 5: a sustained load.
[[sweep]]
topology = "grid"
boxes_per_minute = 60
heavy = true
"""


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


@pytest.fixture
def configured(monkeypatch):
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")


class Clock:
    """In place of this machine's clock: it moves on when a run takes its time, and by `tick` at
    each read, as a clock moves on while the sweep checks a heavy run's approval at its start."""

    def __init__(self, now: datetime = START, tick: timedelta = timedelta()) -> None:
        self.now = now
        self.tick = tick

    def __call__(self) -> datetime:
        read = self.now
        self.now += self.tick
        return read


# How a stand-in run ends, by its place among the runs made (from 1).
COMPLETED = "completed"
INVALID = "invalid"
FAILED = "failed"
OVER_TIME = "over time"
LEFT = "left repository"
MISSING = "missing record"
FAILED_MISSING = "failed, and its record missing"
INTERRUPTED = "interrupted"


class Night:
    """In place of the runs and the replay levels on the configured store: each writes a
    fabricated record with the sweep fields it was given and takes 12 minutes of `clock`. `ends`
    gives how a run or a level ends by its place among them (from 1): completed by default;
    invalid (a deadlock alarm); failed; failed over time; completed with its repository left on
    the server; completed, but its record written nowhere the sweep finds it; failed, and its
    record written nowhere the sweep finds it; or interrupted by a stop signal. `log` holds each run and level made, in order, with the approval it was given."""

    def __init__(
        self,
        ends: Callable[[int], str] = lambda place: COMPLETED,
        clock: Clock | None = None,
    ) -> None:
        self.ends = ends
        self.clock = clock or Clock()
        self.log: list[str] = []
        self.covered: dict[str, Covered | None] = {}
        self.refuse_replays: str | None = None

    def _written(
        self, runs: Path, found: dict[str, Any], how: str, sweep: dict[str, Any]
    ) -> dict[str, Any]:
        if how == LEFT:
            found["repository_deletion"] = {
                "deleted": False,
                "why": "the store refused the deletion",
                "answer_lost": None,
            }
        else:
            found["repository_deletion"] = {
                "deleted": True,
                "why": None,
                "answer_lost": None,
            }
        found["sweep"] = dict(sweep)
        if how in (MISSING, FAILED_MISSING):
            # The record names another run: the sweep does not find this one below `runs`.
            found["sweep"]["key"] = "0000000000000000"
        write(runs, found)
        return found

    def run(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        approval: Covered | None = None,
        **_: object,
    ) -> dict[str, Any]:
        name = f"run {sweep['configuration']}.{sweep['repetition']}"
        self.log.append(name)
        self.covered[name] = approval
        how = self.ends(len(self.log))
        self.clock.now += timedelta(minutes=12)
        found = record(
            f"run-{len(self.log):03d}",
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 100,
            transports_ended_per_minute=10.0 if sweep["repetition"] % 2 else 50.0,
            valid=how != INVALID,
            outcome={
                FAILED: "failed: RuntimeError: the store went away",
                FAILED_MISSING: "failed: RuntimeError: the store went away",
                OVER_TIME: "failed (over time): the run limit of 1800 s came during "
                "the window",
                INTERRUPTED: "interrupted",
            }.get(how, "completed"),
        )
        return self._written(runs, found, how, sweep)

    def replay(
        self,
        recorded: Path,
        *,
        runs: Path,
        factor: float,
        sweep: dict[str, Any],
        approval: Covered | None = None,
    ) -> dict[str, Any]:
        if self.refuse_replays is not None:
            raise BaselineRefused(self.refuse_replays)
        name = f"replay {sweep['configuration']} x{factor:g}"
        self.log.append(name)
        self.covered[name] = approval
        how = self.ends(len(self.log))
        self.clock.now += timedelta(minutes=12)
        replayed = json.loads(recorded.read_text(encoding="utf-8"))
        found = record(
            f"replay-{len(self.log):03d}",
            run_kind="baseline",
            replays=replayed["run_id"],
            requests=[request(0.01)] * 100,
            outcome="failed: RuntimeError: the store went away"
            if how == FAILED
            else "completed",
        )
        found["replay_level"] = {
            "factor": factor,
            "offered_per_minute": 100.0 * factor,
            "achieved_per_minute": 100.0 * factor,
            "requests_in_window": 100,
            "answered_in_window": 100,
            "refused_in_window": 0,
            "timed_out_in_window": 0,
            "heap_refusals_in_window": 0,
            "replay_span_seconds": 60.0 / factor,
            "broken": False,
            "signals": [],
        }
        return self._written(runs, found, how, sweep)

    def runs(self) -> list[str]:
        """The UC2 runs made, in order."""
        return [line for line in self.log if line.startswith("run ")]


def at(*places: int, then: str = COMPLETED, how: str = FAILED) -> Callable[[int], str]:
    """Runs that end as `how` at `places`, and as `then` elsewhere."""
    return lambda place: how if place in places else then


def stand_in(monkeypatch, night: Night, module: Any = sweep) -> Night:
    monkeypatch.setattr(module, "run_on_the_configured_store", night.run)
    monkeypatch.setattr(module, "run_replay_on_the_configured_store", night.replay)
    return night


def spec_file(folder: Path, text: str) -> Path:
    path = folder / "spec.toml"
    path.write_text(text, encoding="utf-8")
    return path


def call(
    tmp_path: Path,
    text: str,
    *options: str,
    clock: Callable[[], datetime] | None = None,
) -> int:
    return sweep.main(
        [str(spec_file(tmp_path, text)), "--runs", str(tmp_path / "runs"), *options],
        clock=clock or Clock(),
    )


def records(runs: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(runs.glob("*/record.json"))
    ]


# --- a failed run does not stop the sweep; 3 failed runs in a row do -----------------------------


def test_after_one_or_two_failed_runs_the_sweep_goes_on(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(2, 3)))

    code = call(tmp_path, FIXED)

    # Every run is made; a run failed, so not every run the call made completed.
    assert night.runs() == [
        "run 1.1",
        "run 1.2",
        "run 2.1",
        "run 2.2",
        "run 3.1",
        "run 3.2",
    ]
    assert code == 1
    out = capsys.readouterr().out
    assert "failed run 1 of 3 in a row" in out
    assert "failed run 2 of 3 in a row" in out
    assert "2 runs of this call failed" in out


def test_after_the_third_failed_run_in_a_row_the_sweep_stops_and_names_the_three(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(2, 3, 4)))

    code = call(tmp_path, FIXED)

    assert code == 1
    assert night.runs() == ["run 1.1", "run 1.2", "run 2.1", "run 2.2"]
    out = capsys.readouterr().out
    [stop] = [line for line in out.splitlines() if "3 failed runs in a row" in line]
    assert "configuration 1, repetition 2" in stop
    assert "configuration 2, repetition 1" in stop
    assert "configuration 2, repetition 2" in stop
    assert "the store went away" in stop
    # The next call skips the failed runs, a failure being a result, and goes on after them.
    night.log.clear()
    night.ends = lambda place: COMPLETED
    assert call(tmp_path, FIXED) == 0
    assert night.runs() == ["run 3.1", "run 3.2"]


@pytest.mark.parametrize("between", [COMPLETED, INVALID], ids=["valid", "invalid"])
def test_a_completed_run_between_failed_runs_resets_the_count(
    tmp_path, configured, monkeypatch, capsys, between
):
    """A completed run resets the count, valid or invalid (a deadlock alarm is a completed run)."""
    ends = {1: FAILED, 2: FAILED, 3: between, 4: FAILED, 5: FAILED}
    night = stand_in(monkeypatch, Night(lambda place: ends.get(place, COMPLETED)))

    code = call(tmp_path, FIXED)

    assert len(night.runs()) == 6
    assert code == 1
    assert "3 failed runs in a row" not in capsys.readouterr().out


def test_a_run_failed_over_time_counts_as_a_failed_run(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(1, 2, 3, how=OVER_TIME)))

    assert call(tmp_path, FIXED) == 1

    assert len(night.runs()) == 3
    assert "3 failed runs in a row" in capsys.readouterr().out


def test_a_stop_signal_still_ends_the_call_at_once(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(2, how=INTERRUPTED)))

    assert call(tmp_path, FIXED) == 1

    assert night.runs() == ["run 1.1", "run 1.2"]
    assert "a stop signal ended" in capsys.readouterr().out


def test_under_the_precision_rule_the_rounds_go_on_after_a_failed_run(
    tmp_path, configured, monkeypatch
):
    """A failed run counts toward the 20 as before; the configuration stays open."""
    night = stand_in(monkeypatch, Night(at(2)))

    assert call(tmp_path, RULED) == 1

    assert night.runs() == [f"run {n}.{r}" for r in (1, 2, 3) for n in (1, 2, 3)]


# --- a run whose record the call cannot find --------------------------------------


def test_one_missing_record_does_not_end_the_call_and_leaves_its_configuration_out(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(2, how=MISSING)))

    code = call(tmp_path, RULED)

    # Configuration 2's record of round 1 is not found: no later round of the call plans it.
    assert night.runs() == [
        "run 1.1",
        "run 2.1",
        "run 3.1",
        "run 1.2",
        "run 3.2",
        "run 1.3",
        "run 3.3",
    ]
    assert code == 1
    out = capsys.readouterr().out
    assert "configuration 2, repetition 1" in out
    assert "cannot be found" in out
    assert "left out for the rest of the call" in out


def test_three_missing_records_in_a_row_end_the_call(
    tmp_path, configured, monkeypatch, capsys
):
    """A run that leaves no record would be planned again in the next round, all night long."""
    night = stand_in(monkeypatch, Night(lambda place: MISSING))

    assert call(tmp_path, RULED) == 1

    assert night.runs() == ["run 1.1", "run 2.1", "run 3.1"]
    assert "3 failed runs in a row" in capsys.readouterr().out


def test_a_failed_run_whose_record_is_missing_says_that_its_configuration_is_left_out(
    tmp_path, configured, monkeypatch, capsys
):
    """The line names the outcome and the configuration left out."""
    night = stand_in(monkeypatch, Night(at(2, how=FAILED_MISSING)))

    assert call(tmp_path, RULED) == 1

    assert "run 2.2" not in night.runs()
    [line] = [
        line
        for line in capsys.readouterr().out.splitlines()
        if "failed run 1 of 3 in a row" in line
    ]
    assert "configuration 2, repetition 1" in line
    assert "the store went away" in line
    assert "cannot be found" in line
    assert "left out for the rest of the call" in line


def moves_away(
    night: Night, runs: Path, after: int, *run_ids: str
) -> Callable[..., dict[str, Any]]:
    """The stand-in run of `night`, and a person who moves the folders `run_ids` out of `runs`
    when the run at the place `after` has ended: their records were found after their runs, and
    are gone before the next round."""

    def run(configuration: Configuration, **options: Any) -> dict[str, Any]:
        found = night.run(configuration, **options)
        if len(night.log) == after:
            moved = runs.parent / "moved"
            moved.mkdir(exist_ok=True)
            for run_id in run_ids:
                (runs / run_id).rename(moved / run_id)
        return found

    return run


def test_a_record_gone_before_the_next_round_counts_as_a_failed_run_and_the_call_goes_on(
    tmp_path, configured, monkeypatch, capsys
):
    """The guard between rounds no longer stops the call. A record that was found
    after its run, and is gone when the next round is chosen, counts as a failed run: the call
    leaves its configuration out for the rest of the call and goes on."""
    night = stand_in(monkeypatch, Night())
    monkeypatch.setattr(
        sweep,
        "run_on_the_configured_store",
        moves_away(night, tmp_path / "runs", 3, "run-001"),
    )

    code = call(tmp_path, RULED)

    assert night.runs() == [
        "run 1.1",
        "run 2.1",
        "run 3.1",
        "run 2.2",
        "run 3.2",
        "run 2.3",
        "run 3.3",
    ]
    assert code == 1
    out = capsys.readouterr().out
    assert "the call stops" not in out
    [line] = [line for line in out.splitlines() if "failed run 1 of 3 in a row" in line]
    assert "configuration 1, repetition 1" in line
    assert "cannot be found" in line
    assert "left out for the rest of the call" in line
    assert "1 run of this call failed" in out


def test_records_gone_before_the_next_round_count_toward_the_three_in_a_row(
    tmp_path, configured, monkeypatch, capsys
):
    """Run 3.1 fails, and the records of runs 1.1 and 2.1 are gone before round 2: three failed
    runs in a row end the call."""
    night = stand_in(monkeypatch, Night(at(3)))
    monkeypatch.setattr(
        sweep,
        "run_on_the_configured_store",
        moves_away(night, tmp_path / "runs", 3, "run-001", "run-002"),
    )

    assert call(tmp_path, RULED) == 1

    assert night.runs() == ["run 1.1", "run 2.1", "run 3.1"]
    out = capsys.readouterr().out
    [stop] = [line for line in out.splitlines() if "3 failed runs in a row" in line]
    assert "configuration 1, repetition 1" in stop
    assert "configuration 2, repetition 1" in stop
    assert "configuration 3, repetition 1" in stop
    assert "the call stops" not in out


# --- a left repository counts toward the 3 ---------------------------------------


def test_one_left_repository_between_two_completed_runs_does_not_end_the_call(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(2, how=LEFT)))

    code = call(tmp_path, FIXED)

    assert len(night.runs()) == 6
    # A left repository is a completed run: it counts by its validity, and the call exits 0.
    assert code == 0
    assert sum(r["outcome"] == "completed" for r in records(tmp_path / "runs")) == 6
    out = capsys.readouterr().out
    assert "failed run 1 of 3 in a row" in out
    assert "was not deleted" in out


def test_three_left_repositories_in_a_row_end_the_call(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night(at(1, 2, 3, how=LEFT)))

    assert call(tmp_path, FIXED) == 1

    assert len(night.runs()) == 3
    [stop] = [
        line
        for line in capsys.readouterr().out.splitlines()
        if "3 failed runs in a row" in line
    ]
    assert stop.count("was not deleted") == 3


def test_a_left_repository_does_not_reset_the_count(
    tmp_path, configured, monkeypatch, capsys
):
    ends = {1: FAILED, 2: LEFT, 3: FAILED}
    night = stand_in(monkeypatch, Night(lambda place: ends.get(place, COMPLETED)))

    assert call(tmp_path, FIXED) == 1

    assert len(night.runs()) == 3
    assert "3 failed runs in a row" in capsys.readouterr().out


# --- replay levels ---------------------------------------------------------------------------


def test_a_failed_replay_level_counts_toward_the_three_and_ends_its_replay(
    tmp_path, configured, monkeypatch, capsys
):
    """Run 1.1, then the replay at the factor 1 (place 2) fails: no higher factor is sent, and
    the sweep goes on with the ramp, whose two levels fail too: the third failed run in a row."""
    night = stand_in(monkeypatch, Night(at(2, 3)))
    spec = spec_file(tmp_path, HEAVY)

    code = sweep.main(
        [
            str(spec),
            "--runs",
            str(tmp_path / "runs"),
            "--heavy-run-approval",
            str(
                approval_file(
                    tmp_path, spec, start=START, end=START + timedelta(hours=8)
                )
            ),
        ],
        clock=Clock(),
    )

    assert code == 1
    assert night.log == ["run 1.1", "replay 1 x1", "run 2.1", "run 4.1", "run 5.1"]
    assert "3 failed runs in a row" not in capsys.readouterr().out

    night.log.clear()
    night.ends = lambda place: FAILED
    code = sweep.main(
        [
            str(spec),
            "--runs",
            str(tmp_path / "runs2"),
            "--heavy-run-approval",
            str(
                approval_file(
                    tmp_path, spec, start=START, end=START + timedelta(hours=8)
                )
            ),
        ],
        clock=Clock(),
    )

    # Run 1.1 failed, so the replay waits; the ramp's first level fails, and so its second is
    # not sent; run 4.1 is the third failed run in a row.
    assert code == 1
    assert night.log == ["run 1.1", "run 2.1", "run 4.1"]
    assert "3 failed runs in a row" in capsys.readouterr().out


def test_a_replay_level_that_cannot_start_is_skipped_and_listed_and_the_sweep_goes_on(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night())
    night.refuse_replays = "the recorded run holds no store request"

    code = call(tmp_path, HEAVY, "--configurations", "1,4")

    assert night.log == ["run 1.1", "run 4.1"]
    assert code == 0
    out = capsys.readouterr().out
    assert "the replay of configuration 1 at the factor 1" in out
    assert "cannot start" in out and "the recorded run holds no store request" in out
    # Its higher factors are skipped in this call.
    assert "the replay of configuration 1 at the factor 2" in out


# --- a heavy run that no approval covers is skipped and listed --------------------------------


def heavy_call(
    tmp_path: Path,
    *options: str,
    approval: Path | None = None,
    clock: Clock | None = None,
) -> int:
    spec = tmp_path / "spec.toml"
    return sweep.main(
        [
            str(spec),
            "--runs",
            str(tmp_path / "runs"),
            *options,
            *([] if approval is None else ["--heavy-run-approval", str(approval)]),
        ],
        clock=clock or Clock(),
    )


def skipped_lines(out: str) -> list[str]:
    """The lines that list the heavy runs the call skipped."""
    return [line for line in out.splitlines() if line.startswith("  - ")]


def test_without_an_approval_the_sweep_makes_every_normal_run_and_skips_each_heavy_run(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night())
    spec_file(tmp_path, HEAVY)

    code = heavy_call(tmp_path)

    # As if the heavy runs were not selected.
    assert code == 0
    assert night.log == ["run 1.1", "replay 1 x1", "run 4.1"]
    assert all(given is None for given in night.covered.values())
    out = capsys.readouterr().out
    assert "skipped 5 heavy runs" in out
    listed = skipped_lines(out)
    assert any(
        "the replay of configuration 1 at the factor 2" in l and "no approval" in l
        for l in listed
    )
    assert any("the replay of configuration 1 at the factor 4" in l for l in listed)
    assert any("configuration 2" in l and "no approval" in l for l in listed)
    assert any(
        "configuration 3" in l and "a lower level of its ramp was skipped" in l
        for l in listed
    )
    assert any("configuration 5" in l and "no approval" in l for l in listed)
    assert "--heavy-run-approval" in out
    # A skip writes no record.
    assert len(records(tmp_path / "runs")) == 3


@pytest.mark.parametrize(
    ("approve", "options", "reason", "detail"),
    [
        (
            lambda folder, spec: approval_file(
                folder,
                spec,
                start=START + timedelta(days=1),
                end=START + timedelta(days=2),
            ),
            (),
            "a period that is not now",
            "outside the period",
        ),
        (
            lambda folder, spec: approval_file(
                folder,
                spec_file_named(folder, "other.toml", HEAVY + "\n# another spec\n"),
                start=START,
                end=START + timedelta(hours=8),
            ),
            (),
            "another or a changed spec",
            "it is another spec",
        ),
        (
            lambda folder, spec: approval_file(
                folder, spec, start=START, end=START + timedelta(hours=8)
            ),
            ("--set", "window_seconds=60"),
            "--set overrides",
            "--set overrides is not covered",
        ),
    ],
    ids=["a period that is not now", "another spec", "--set overrides"],
)
def test_an_approval_that_does_not_cover_a_heavy_run_skips_it_with_the_reason(
    tmp_path, configured, monkeypatch, capsys, approve, options, reason, detail
):
    night = stand_in(monkeypatch, Night())
    spec = spec_file(tmp_path, HEAVY)

    code = heavy_call(tmp_path, *options, approval=approve(tmp_path, spec))

    assert code == 0
    assert night.log == ["run 1.1", "replay 1 x1", "run 4.1"]
    listed = skipped_lines(capsys.readouterr().out)
    assert len(listed) == 5
    assert all(reason in l for l in listed if "lower level" not in l)
    assert any(detail in l for l in listed)


def spec_file_named(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_spec_changed_since_its_approval_skips_its_heavy_runs_with_the_reason(
    tmp_path, configured, monkeypatch, capsys
):
    night = stand_in(monkeypatch, Night())
    spec = spec_file(tmp_path, HEAVY)
    approval = approval_file(
        tmp_path, spec, start=START, end=START + timedelta(hours=8)
    )
    spec.write_text(HEAVY.replace("[10, 20]", "[10, 30]"), encoding="utf-8")

    assert heavy_call(tmp_path, approval=approval) == 0

    assert night.log == ["run 1.1", "replay 1 x1", "run 4.1"]
    listed = skipped_lines(capsys.readouterr().out)
    assert any(
        "another or a changed spec" in l and "changed since it was approved" in l
        for l in listed
    )


def test_a_sweep_that_reaches_the_end_of_the_period_skips_the_heavy_runs_after_it(
    tmp_path, configured, monkeypatch, capsys
):
    """The period ends at 20:30: runs 1.1 (20:00), the replay at the factors 1 (20:12) and 2
    (20:24) run in it; the factor 4 starts at 20:36, after it, and so does the ramp. The normal
    run 4.1 goes on."""
    clock = Clock()
    night = stand_in(monkeypatch, Night(clock=clock))
    spec = spec_file(tmp_path, HEAVY)
    approval = approval_file(
        tmp_path, spec, start=START, end=START + timedelta(minutes=30)
    )

    code = heavy_call(tmp_path, approval=approval, clock=clock)

    assert code == 0
    assert night.log == ["run 1.1", "replay 1 x1", "replay 1 x2", "run 4.1"]
    assert isinstance(night.covered["replay 1 x2"], Covered)
    listed = skipped_lines(capsys.readouterr().out)
    assert len(listed) == 4
    assert any(
        "the replay of configuration 1 at the factor 4" in l
        and "outside the period" in l
        for l in listed
    )
    assert any(
        "configuration 5" in l and "a period that is not now" in l for l in listed
    )


def test_a_period_that_begins_partway_through_a_call_does_not_let_level_2_run_after_level_1(
    tmp_path, configured, monkeypatch, capsys
):
    """A heavy run is checked at its start, and the clock moves on by a minute at each check.
    The period begins at 20:24:30: the ramp's level 1 is checked at 20:24, before it, and is
    skipped; level 2 is skipped with it, although the period has begun at its turn. The
    sustained load is checked at 20:37, in the period, and runs."""
    clock = Clock(tick=timedelta(minutes=1))
    night = stand_in(monkeypatch, Night(clock=clock))
    spec = spec_file(
        tmp_path, HEAVY.replace("replay_factors = [1, 2, 4]", "replay_factors = [1]")
    )
    approval = approval_file(
        tmp_path,
        spec,
        start=START + timedelta(minutes=24, seconds=30),
        end=START + timedelta(hours=8),
    )

    code = heavy_call(tmp_path, approval=approval, clock=clock)

    assert code == 0
    assert night.log == ["run 1.1", "replay 1 x1", "run 4.1", "run 5.1"]
    assert isinstance(night.covered["run 5.1"], Covered)
    listed = skipped_lines(capsys.readouterr().out)
    assert any(
        "configuration 2" in l and "a period that is not now" in l for l in listed
    )
    assert any(
        "configuration 3" in l and "a lower level of its ramp was skipped" in l
        for l in listed
    )


def test_a_skipped_heavy_run_is_made_by_a_later_call_whose_approval_covers_it(
    tmp_path, configured, monkeypatch
):
    night = stand_in(monkeypatch, Night())
    spec = spec_file(tmp_path, HEAVY)
    assert heavy_call(tmp_path) == 0

    night.log.clear()
    approval = approval_file(
        tmp_path, spec, start=START, end=START + timedelta(hours=8)
    )
    assert heavy_call(tmp_path, approval=approval) == 0

    assert night.log == ["replay 1 x2", "replay 1 x4", "run 2.1", "run 3.1", "run 5.1"]
    assert all(isinstance(given, Covered) for given in night.covered.values() if given)


def test_under_the_precision_rule_a_skipped_heavy_part_goes_on_in_a_later_round(
    tmp_path, configured, monkeypatch
):
    """The period begins at 20:40, during round 1: round 1 skips the sustained load (20:36), and
    round 2 makes it (21:12), in the period. A skip writes no record, so the part has not ended."""
    clock = Clock()
    night = stand_in(monkeypatch, Night(clock=clock))
    text = RULED + (
        '\n[[sweep]]\ntopology = "cross"\nboxes_per_minute = 60\nheavy = true\n'
        "repetitions = 1\n"
    )
    spec = spec_file(tmp_path, text)
    approval = approval_file(
        tmp_path,
        spec,
        start=START + timedelta(minutes=40),
        end=START + timedelta(hours=8),
    )

    code = heavy_call(tmp_path, approval=approval, clock=clock)

    assert code == 0
    assert night.runs() == [
        "run 1.1",
        "run 2.1",
        "run 3.1",
        "run 1.2",
        "run 2.2",
        "run 3.2",
        "run 4.1",
        "run 1.3",
        "run 2.3",
        "run 3.3",
    ]
    assert isinstance(night.covered["run 4.1"], Covered)


# --- jms-eval ------------------------------------------------------------------------------


def test_jms_eval_with_an_approval_that_does_not_cover_the_spec_makes_the_normal_runs(
    tmp_path, configured, monkeypatch, capsys
):
    """In place of the refusal with exit code 2 before the first run."""
    night = stand_in(monkeypatch, Night(), module=harness)
    spec = spec_file(tmp_path, HEAVY)
    other = spec_file_named(tmp_path, "other.toml", HEAVY + "\n# another spec\n")

    code = harness.main(
        [
            str(spec),
            "--output",
            str(tmp_path / "out"),
            "--prefills",
            str(tmp_path / "prefills"),
            "--heavy-run-approval",
            str(approval_file(tmp_path, other)),
        ]
    )

    assert code == 0
    assert night.log == ["run 1.1", "replay 1 x1", "run 4.1"]
    out = capsys.readouterr().out
    listed = skipped_lines(out)
    assert len(listed) == 5
    assert all(
        "another or a changed spec" in l for l in listed if "lower level" not in l
    )
    assert (tmp_path / "out" / "table.md").is_file()


# --- the documentation -----------------------------------------------------------------------


def test_the_readme_says_the_new_rules():
    text = " ".join(README.read_text(encoding="utf-8").split())

    assert "3 failed runs in a row" in text
    assert "jms_kapps 112" not in text
    assert "skipped and listed" in text
    # The page keeps no dated note of the old rules, and says why a single failure goes on.
    assert "until then the sweep stopped" not in text
    assert "One failed run at 01:00 must not end the night" in text
    assert (
        "an approval that ends at 02:00 does not stop the normal runs after it"
        in text.replace("So an", "an")
    )
    # What the guard between rounds does now.
    assert (
        "a record that was there after its run and is gone when the next round is chosen "
        "counts as a failed run the same way" in text
    )
    assert "a run that it finds counts as a failed run, and the call goes on" in text
