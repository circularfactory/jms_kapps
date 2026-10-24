"""The sweep under the precision rule, in rounds: round k makes
repetition k of each open configuration, in the spec's order, and one call goes on round after
round until the stop time, the stop file or the end of the rule. A call after a stop goes on at the
round that the records show, a record holds the maximum and its round, `--list` shows where each
configuration stands, and the draft spec of the "after" series loads in the decided order with the
keys it had before the rounds.

Each run is a fabricated record, written by a stand-in for a run on the configured store: no
store, no run. The clock is a stand-in too. The connection variables of a store are removed first.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _evaluation_records import record, write
from _repo import withheld_text

import jms_kapps
from jms_kapps.evaluation import sweep
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.sweep import REMOVED_FILE, STOP_FILE, main
from jms_kapps.evaluation.table import main as table_main

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false

[precision]

[[sweep]]
topology = ["cross", "pair"]
"""

# Three configurations under a rule of at most 3 runs, at least 2 valid.
THREE = """\
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

AFTER_SERIES = (
    Path(jms_kapps.__file__).parent / "evaluation" / "specs" / "after-series.toml"
)
ITERATION_ONE = AFTER_SERIES.parent / "iteration-one.toml"
README = Path(jms_kapps.__file__).parent / "evaluation" / "README.md"

# The configurations' keys of the shipped specs on main 13a0869, before the rounds: the rounds
# change no key, so the runs made before them stay valid.
ITERATION_ONE_KEYS = [
    "c47c56a71876d187", "9fcf03ae9c495ab4", "fa129651371f0569", "10d538f2c44d2e00",
    "07a337990d73732f", "b796a2c6bd7615fc", "59fb3ec129a9a3af", "ad4766fa7bfa7175",
    "b1b668ee2c13934b", "17be14b11b0da1df", "ba59e70219aafc59", "fcde3dbeee9188aa",
    "4c22175617842976", "fa18f363d9650357", "289325ce7e6ef05a", "7e066d2aa8e8e020",
    "8000a9866d4c2fc9", "1eb3d47e3aadee17", "f1a8c329c4da0850", "3e36549b1fa4e7e2",
    "d833c7889d2730ac", "8dbe2636e7a0b446", "00515e887bcb5db5", "3c67b04df7e198d4",
    "d6ad35f809948324",
]  # fmt: skip
AFTER_SERIES_KEYS = [
    "c47c56a71876d187", "3b7e7bb3e73bbb83", "3e36549b1fa4e7e2", "d833c7889d2730ac",
    "ad4766fa7bfa7175", "8dbe2636e7a0b446", "fa18f363d9650357", "289325ce7e6ef05a",
    "fa129651371f0569", "7e066d2aa8e8e020", "8000a9866d4c2fc9", "1eb3d47e3aadee17",
    "10d538f2c44d2e00", "f1a8c329c4da0850", "17be14b11b0da1df", "ba59e70219aafc59",
    "fcde3dbeee9188aa", "9fcf03ae9c495ab4", "4c22175617842976", "07a337990d73732f",
    "b796a2c6bd7615fc", "59fb3ec129a9a3af", "00515e887bcb5db5", "3c67b04df7e198d4",
    "b1b668ee2c13934b", "d6ad35f809948324",
]  # fmt: skip

START = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


class Clock:
    """In place of this machine's clock: it moves on only when a run takes its time."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FabricatedRuns:
    """In place of a run on the configured store: each run writes a fabricated record with the
    sweep fields it was given, and keeps which run it was. `throughput` gives the transports
    ended per minute of a run by its configuration's number and repetition, `outcome` its
    outcome by its place among the runs made (from 1). Each run takes 12 minutes of `clock`, and
    `during` is called in each run with its place, as a person who acts while it runs."""

    def __init__(
        self,
        throughput: Callable[[int, int], float] = lambda number, repetition: 30.0,
        outcome: Callable[[int], str] = lambda made: "completed",
        clock: Clock | None = None,
        during: Callable[[int, Path], None] = lambda made, runs: None,
    ) -> None:
        self.throughput = throughput
        self.outcome = outcome
        self.clock = clock or Clock()
        self.during = during
        self.made: list[dict[str, Any]] = []
        self.count = 0

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        **_: object,
    ) -> dict[str, Any]:
        self.made.append(sweep)
        self.count += 1
        self.during(len(self.made), runs)
        self.clock.now += timedelta(minutes=12)
        outcome = self.outcome(len(self.made))
        found = record(
            f"run-{self.count:04d}",
            commit_latencies=[0.1] * 20,
            transports_ended_per_minute=self.throughput(
                sweep["configuration"], sweep["repetition"]
            ),
            outcome=outcome,
        )
        found["sweep"] = dict(sweep)
        write(runs, found)
        return found

    def runs(self) -> list[tuple[int, int, int | None]]:
        """Each run made: its configuration's number, its repetition and its round."""
        return [
            (s["configuration"], s["repetition"], s.get("round")) for s in self.made
        ]


def noisy(number: int, repetition: int) -> float:
    """A throughput whose IQR is far beyond 10 % of its median: the rule is never met."""
    return 10.0 if repetition % 2 else 50.0


@pytest.fixture
def configured(monkeypatch):
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")


def start(monkeypatch, runs: FabricatedRuns) -> None:
    monkeypatch.setattr(sweep, "run_on_the_configured_store", runs)


def spec_file(folder: Path, text: str = SPEC) -> Path:
    path = folder / "spec.toml"
    path.write_text(text, encoding="utf-8")
    return path


def call(
    tmp_path: Path,
    *arguments: str,
    text: str = SPEC,
    clock: Callable[[], datetime] | None = None,
) -> int:
    return main(
        [str(spec_file(tmp_path, text)), "--runs", str(tmp_path / "runs"), *arguments],
        clock=clock or Clock(),
    )


def stop_file_in(made: int) -> Callable[[int, Path], None]:
    """A person who makes the stop file while the run `made` runs."""

    def during(place: int, runs: Path) -> None:
        if place == made:
            runs.mkdir(parents=True, exist_ok=True)
            (runs / STOP_FILE).write_text("", encoding="utf-8")

    return during


# --- the rounds ------------------------------------------------------------------------------


def test_one_call_makes_round_after_round_in_the_spec_s_order_until_the_rule_ends(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy)
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 0

    assert made.runs() == [(n, r, r) for r in (1, 2, 3) for n in (1, 2, 3)]
    out = capsys.readouterr().out
    assert "round 1: repetition 1 of each of 3 open configurations" in out
    assert "round 3: repetition 3 of each of 3 open configurations" in out
    assert (
        "the call ends: no selected configuration under the precision rule is open; each "
        "meets the rule, has made 3 runs or stopped at a throughput median of 0" in out
    )

    # A later call finds no round left and makes nothing.
    made.made.clear()
    assert call(tmp_path, text=THREE) == 0
    assert made.made == []
    assert "no selected configuration under the precision rule is open" in (
        capsys.readouterr().out
    )


def test_a_configuration_that_meets_the_rule_is_planned_in_no_later_round(
    tmp_path, configured, monkeypatch
):
    # Configuration 2's throughput swings; 1 and 3 meet the rule at their 2 valid runs.
    made = FabricatedRuns(
        throughput=lambda number, repetition: (
            noisy(number, repetition) if number == 2 else 30.0
        )
    )
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 0

    assert made.runs() == [(1, 1, 1), (2, 1, 1), (3, 1, 1), (1, 2, 2), (2, 2, 2)] + [
        (3, 2, 2),
        (2, 3, 3),
    ]

    # The table shows the rounds of each, and marks the one that missed the rule.
    spec = str(spec_file(tmp_path, THREE))
    assert table_main([spec, "--runs", str(tmp_path / "runs")]) == 0
    text = (tmp_path / "runs" / "table.md").read_text()
    assert "| spec 1 | uc2 | 2 | 2 | 1, 2 | met |" in text
    assert "| spec 2 | uc2 | 3 | 3 | 1, 2, 3 | precision not reached (3 runs) |" in text


def test_a_configuration_whose_throughput_median_is_0_stops_after_its_minimum_of_valid_runs(
    tmp_path, configured, monkeypatch, capsys
):
    """No transport ends in a run of configuration 2: with its minimum of 2 valid runs its
    throughput median is 0, so it stops there, and round 3 plans no run for it. `--list` and the table mark
    it "stopped: throughput median 0"."""
    made = FabricatedRuns(
        throughput=lambda number, repetition: {1: 30.0, 2: 0.0}.get(
            number, noisy(number, repetition)
        )
    )
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 0

    assert made.runs() == [(n, r, r) for r in (1, 2) for n in (1, 2, 3)] + [(3, 3, 3)]
    out = capsys.readouterr().out
    assert "round 3: repetition 3 of each of 1 open configurations" in out
    assert (
        "the call ends: no selected configuration under the precision rule is open; each "
        "meets the rule, has made 3 runs or stopped at a throughput median of 0" in out
    )

    assert call(tmp_path, "--list", text=THREE) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2].endswith(
        "; one whose throughput median is 0 once it has 2 valid runs stops there"
    )
    assert lines[3] == (
        "no round is left: every configuration under the rule meets it, has made 3 runs or "
        "stopped at a throughput median of 0"
    )
    assert lines[5].startswith(
        "configuration spec 2 (block 1), under the precision rule, up to 3 runs: 2 made, 2 valid, "
        "not open: stopped: throughput median 0, with a record: 1 completed, 2 completed: "
    )

    spec = str(spec_file(tmp_path, THREE))
    assert table_main([spec, "--runs", str(tmp_path / "runs")]) == 0
    text = (tmp_path / "runs" / "table.md").read_text()
    assert "| spec 2 | uc2 | 2 | 2 | 1, 2 | stopped: throughput median 0 |" in text
    assert "| spec 3 | uc2 | 3 | 3 | 1, 2, 3 | precision not reached (3 runs) |" in text


def test_the_readme_s_precision_rule_states_the_stop_at_a_throughput_median_of_0():
    """The README states the stop at a throughput median of 0."""
    section = " ".join(
        README.read_text(encoding="utf-8")
        .split("### The precision rule and the rounds")[1]
        .split("## The table of a spec")[0]
        .split()
    )
    stop = section.split("**A throughput median of 0**")[1].split("\n- **")[0]

    assert "Sören" not in stop and "jms_kapps 113" not in stop
    assert "such a configuration can never meet the rule" in stop
    assert "after its minimum of valid runs" in stop and "stops there" in stop
    assert "again after each later run" in stop
    assert '"stopped: throughput median 0"' in stop and "without the wrapper" in stop
    assert "Changed on 2026-10-14" not in stop
    assert 'apart from "precision not reached"' in stop
    assert "runs again only when its records are moved out of the runs' folder" in stop
    # Open, the end of the rule and `--list` name the stop too.
    assert "has no throughput median of 0 at its minimum of valid runs" in section
    assert "has made 20 runs or stopped at a throughput median of 0" in section
    assert "`stopped: throughput median 0`" in section.split("**`--list`**")[1]


def test_a_record_holds_the_maximum_and_its_round(tmp_path, configured, monkeypatch):
    start(monkeypatch, FabricatedRuns())
    call(tmp_path, "--repetitions", "1")

    records = [
        json.loads(path.read_text())["sweep"]
        for path in sorted((tmp_path / "runs").glob("*/record.json"))
    ]

    assert [(s["repetition"], s["repetitions"], s["round"]) for s in records] == [
        (1, 20, 1),
        (1, 20, 1),
    ]
    assert not any("pass" in s for s in records)


# --- the ends --------------------------------------------------------------------------------


def test_the_call_stops_before_its_first_run_after_the_stop_time(
    tmp_path, configured, monkeypatch, capsys
):
    clock = Clock()
    made = FabricatedRuns(throughput=noisy, clock=clock)
    start(monkeypatch, made)

    # Each run takes 12 minutes: runs start at 18:00, 18:12 and 18:24; the third ends at 18:36,
    # after the stop time, and runs to its end.
    code = call(tmp_path, "--stop-at", "2026-10-13T18:30Z", text=THREE, clock=clock)

    assert code == 0
    assert made.runs() == [(1, 1, 1), (2, 1, 1), (3, 1, 1)]
    out = capsys.readouterr().out
    assert "the stop time 2026-10-13 18:30 UTC has come" in out
    assert "before its next run" in out


def test_the_stop_time_takes_a_zone_and_lies_ahead(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns()
    start(monkeypatch, made)

    assert call(tmp_path, "--stop-at", "2026-10-14T06:00") == 2
    assert "names its zone" in capsys.readouterr().err
    assert call(tmp_path, "--stop-at", "2026-10-13T17:00+00:00") == 2
    assert "has passed" in capsys.readouterr().err
    assert call(tmp_path, "--stop-at", "tomorrow") == 2
    assert made.made == []


def test_the_call_stops_at_the_stop_file_removes_it_and_the_next_call_goes_on(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy, during=stop_file_in(2))
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 0

    # The person made the stop file while run 2 ran: the call stops before run 3.
    assert made.runs() == [(1, 1, 1), (2, 1, 1)]
    assert not (tmp_path / "runs" / STOP_FILE).exists()
    out = capsys.readouterr().out
    assert f"the stop file {tmp_path / 'runs' / STOP_FILE} is there" in out
    assert "removed it" in out

    # The next call goes on at the round and the repetition that the records show.
    made.made.clear()
    made.during = lambda place, runs: None
    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == [(3, 1, 1)] + [(n, r, r) for r in (2, 3) for n in (1, 2, 3)]


def test_a_stop_file_there_before_the_first_run_stops_the_call_before_it(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns()
    start(monkeypatch, made)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / STOP_FILE).write_text("", encoding="utf-8")

    assert call(tmp_path) == 0

    assert made.made == []
    assert not (tmp_path / "runs" / STOP_FILE).exists()
    assert "is there" in capsys.readouterr().out


def test_the_stop_file_and_the_stop_time_stop_a_spec_of_fixed_repetitions_too(
    tmp_path, configured, monkeypatch
):
    made = FabricatedRuns(during=stop_file_in(1))
    start(monkeypatch, made)
    fixed = SPEC.replace("[precision]\n", "").replace(
        "heavy = false\n", "heavy = false\nrepetitions = 3\n"
    )

    assert call(tmp_path, text=fixed) == 0

    assert [(n, r) for n, r, _ in made.runs()] == [(1, 1)]


def test_an_interrupted_attempt_stops_the_call_and_is_made_again_in_its_round(
    tmp_path, configured, monkeypatch
):
    made = FabricatedRuns(
        throughput=noisy,
        outcome=lambda number: "interrupted" if number == 2 else "completed",
    )
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 1
    assert made.runs() == [(1, 1, 1), (2, 1, 1)]

    made.made.clear()
    made.outcome = lambda number: "completed"
    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == [(2, 1, 1), (3, 1, 1)] + [
        (n, r, r) for r in (2, 3) for n in (1, 2, 3)
    ]


def test_a_failed_run_does_not_stop_the_call_and_counts_toward_the_maximum(
    tmp_path, configured, monkeypatch, capsys
):
    """The call goes on after a failed run ."""
    made = FabricatedRuns(
        throughput=noisy,
        outcome=lambda number: (
            "failed: the store refused" if number == 4 else "completed"
        ),
    )
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 1
    assert made.runs() == [(n, r, r) for r in (1, 2, 3) for n in (1, 2, 3)]
    assert "failed run 1 of 3 in a row" in capsys.readouterr().out

    # The failed run is a run made: configuration 1 has made its 3 runs, and nothing is left.
    made.made.clear()
    made.outcome = lambda number: "completed"
    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == []


def test_a_smaller_selection_in_a_later_call_leaves_the_removed_configurations(
    tmp_path, configured, monkeypatch
):
    made = FabricatedRuns(throughput=noisy, during=stop_file_in(3))
    start(monkeypatch, made)
    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == [(1, 1, 1), (2, 1, 1), (3, 1, 1)]

    # The person removes configuration 2: the next call goes on with 1 and 3.
    made.made.clear()
    made.during = lambda place, runs: None
    assert call(tmp_path, "--configurations", "1,3", text=THREE) == 0

    assert made.runs() == [(n, r, r) for r in (2, 3) for n in (1, 3)]
    sweeps = [
        json.loads(path.read_text())["sweep"]
        for path in (tmp_path / "runs").glob("*/record.json")
    ]
    assert sorted(s["repetition"] for s in sweeps if s["configuration"] == 2) == [1]


def test_a_call_narrowed_by_repetition_makes_only_those_rounds(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns()
    start(monkeypatch, made)

    assert call(tmp_path, "--configurations", "1-2", "--repetitions", "1-2") == 0
    assert made.runs() == [(1, 1, 1), (2, 1, 1), (1, 2, 2), (2, 2, 2)]
    assert "every selected repetition of the open configurations has a record" in (
        capsys.readouterr().out
    )

    made.made.clear()
    assert call(tmp_path, "--repetitions", "3") == 0
    assert made.runs() == [(1, 3, 3), (2, 3, 3)]


def test_records_made_in_passes_count_as_repetitions_in_the_rounds(
    tmp_path, configured, monkeypatch
):
    """Pass 1 of the "after" series may have run before the rounds: its records count."""
    spec = read_spec(spec_file(tmp_path, THREE))
    found = configurations(spec)
    for repetition in (1, 2):
        made_before = record(
            f"before-{repetition}",
            found[0],
            repetition=repetition,
            commit_latencies=[0.1] * 20,
            transports_ended_per_minute=noisy(1, repetition),
            pass_number=1,
        )
        write(tmp_path / "runs", made_before)
    made = FabricatedRuns(throughput=noisy)
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 0

    assert made.runs() == [(2, 1, 1), (3, 1, 1), (2, 2, 2), (3, 2, 2)] + [
        (n, 3, 3) for n in (1, 2, 3)
    ]


def test_a_call_whose_records_do_not_show_its_runs_stops_before_it_makes_them_again(
    tmp_path, configured, monkeypatch, capsys
):
    """A run that leaves no record would be planned again in the next round, all night long:
    each such run counts as a failed run, its configuration is left out for the rest of the
    call, and the third in a row stops the call."""
    made: list[tuple[int, int]] = []

    def leaves_no_record(
        configuration: Configuration, *, runs: Path, sweep: dict[str, Any], **_: object
    ) -> dict[str, Any]:
        made.append((sweep["configuration"], sweep["repetition"]))
        return record(f"lost-{len(made)}", commit_latencies=[0.1] * 20)

    monkeypatch.setattr(sweep, "run_on_the_configured_store", leaves_no_record)

    assert call(tmp_path, text=THREE) == 1

    assert made == [(1, 1), (2, 1), (3, 1)]
    out = capsys.readouterr().out
    assert "3 failed runs in a row" in out and "its record cannot be found" in out


# --- a stop file never outlives its call ---------------------------


def assert_removed(tmp_path: Path, out: str) -> None:
    """The stop file is gone, and the call said so."""
    assert not (tmp_path / "runs" / STOP_FILE).exists()
    assert "the stop file" in out and "is removed" in out


def test_a_stop_file_made_during_a_failed_run_is_removed_when_the_call_stops(
    tmp_path, configured, monkeypatch, capsys
):
    """The third failed run in a row stops the call."""
    made = FabricatedRuns(
        throughput=noisy,
        outcome=lambda number: (
            "failed: the store refused" if number <= 3 else "completed"
        ),
        during=stop_file_in(3),
    )
    start(monkeypatch, made)

    assert call(tmp_path, text=THREE) == 1

    assert made.runs() == [(1, 1, 1), (2, 1, 1), (3, 1, 1)]
    assert_removed(tmp_path, capsys.readouterr().out)
    # The next call is not stopped before its first run.
    made.made.clear()
    made.outcome = lambda number: "completed"
    made.during = lambda place, runs: None
    assert call(tmp_path, text=THREE) == 0
    assert made.runs()[0] == (1, 2, 2)


def test_a_stop_file_made_during_the_last_run_is_removed_at_the_end_of_the_rule(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy, during=stop_file_in(3))
    start(monkeypatch, made)

    assert call(tmp_path, "--configurations", "1", text=THREE) == 0

    assert made.runs() == [(1, 1, 1), (1, 2, 2), (1, 3, 3)]
    out = capsys.readouterr().out
    assert "no selected configuration under the precision rule is open" in out
    assert_removed(tmp_path, out)


def test_a_stop_file_is_removed_when_the_next_round_cannot_start(
    tmp_path, configured, monkeypatch, capsys
):
    prefills = tmp_path / "prefills"
    prefills.mkdir()
    for topology in ("cross", "pair", "grid"):
        (prefills / f"{topology}-1k.ttl").write_text("", encoding="utf-8")

    def during(place: int, runs: Path) -> None:
        # While the last run of round 1 runs, a prefill file of round 2 goes missing.
        if place == 3:
            (prefills / "grid-1k.ttl").unlink()
            (runs / STOP_FILE).write_text("", encoding="utf-8")

    made = FabricatedRuns(throughput=noisy, during=during)
    start(monkeypatch, made)
    text = THREE + 'prefill = "{topology}-1k.ttl"\n'

    assert call(tmp_path, "--prefills", str(prefills), text=text) == 1

    assert made.runs() == [(1, 1, 1), (2, 1, 1), (3, 1, 1)]
    out = capsys.readouterr().out
    assert "the next round cannot start" in out
    assert_removed(tmp_path, out)


def test_a_stop_file_is_removed_when_the_guard_stops_the_call(
    tmp_path, configured, monkeypatch, capsys
):
    made: list[tuple[int, int]] = []

    def leaves_no_record(
        configuration: Configuration, *, runs: Path, sweep: dict[str, Any], **_: object
    ) -> dict[str, Any]:
        made.append((sweep["configuration"], sweep["repetition"]))
        if len(made) == 3:
            runs.mkdir(parents=True, exist_ok=True)
            (runs / STOP_FILE).write_text("", encoding="utf-8")
        return record(f"lost-{len(made)}", commit_latencies=[0.1] * 20)

    monkeypatch.setattr(sweep, "run_on_the_configured_store", leaves_no_record)

    assert call(tmp_path, text=THREE) == 1

    out = capsys.readouterr().out
    assert "3 failed runs in a row" in out
    assert_removed(tmp_path, out)


def test_a_stop_file_is_removed_when_ctrl_c_stops_the_call_between_two_runs(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy, during=stop_file_in(2))

    def interrupted(configuration: Configuration, **options: Any) -> dict[str, Any]:
        found = made(configuration, **options)
        if made.count == 2:
            raise KeyboardInterrupt
        return found

    monkeypatch.setattr(sweep, "run_on_the_configured_store", interrupted)

    assert call(tmp_path, text=THREE) == 1

    captured = capsys.readouterr()
    assert "stopped between two runs" in captured.err
    assert_removed(tmp_path, captured.out)


# --- a round with nothing to make ends the call -------------------


def unreadable_record(runs: Path, configuration_number: int, repetition: int) -> None:
    """A record that the sweep's skip finds (its sweep fields hold), but whose summary cannot be
    made (it names no run kind), so the rule does not count it."""
    spec = read_spec(runs.parent / "spec.toml")
    configuration = configurations(spec)[configuration_number - 1]
    found = record(
        f"unreadable-{configuration_number}-{repetition}",
        configuration,
        repetition=repetition,
        commit_latencies=[0.1] * 20,
    )
    del found["run_kind"]
    write(runs, found)


def test_a_first_round_with_nothing_to_make_ends_the_call_with_its_own_message(
    tmp_path, configured, monkeypatch, capsys
):
    spec_file(tmp_path, THREE)
    unreadable_record(tmp_path / "runs", 1, 1)
    made = FabricatedRuns(throughput=noisy)
    start(monkeypatch, made)

    assert call(tmp_path, "--configurations", "1", text=THREE) == 1

    assert made.made == []
    out = capsys.readouterr().out
    assert "round 1 has nothing to make" in out
    assert "configuration 1 repetition 1" in out
    assert "no selected configuration under the precision rule is open" not in out


def test_a_later_round_with_nothing_to_make_ends_the_call_and_removes_the_stop_file(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy)

    def unreadable_second(
        configuration: Configuration, **options: Any
    ) -> dict[str, Any]:
        found = made(configuration, **options)
        if made.count == 2:
            # The record of round 2 cannot be summarised, and a person made the stop file.
            path = options["runs"] / found["run_id"] / "record.json"
            broken = json.loads(path.read_text(encoding="utf-8"))
            del broken["run_kind"]
            path.write_text(json.dumps(broken), encoding="utf-8")
            (options["runs"] / STOP_FILE).write_text("", encoding="utf-8")
        return found

    monkeypatch.setattr(sweep, "run_on_the_configured_store", unreadable_second)

    assert call(tmp_path, "--configurations", "1", text=THREE) == 1

    assert made.runs() == [(1, 1, 1), (1, 2, 2)]
    out = capsys.readouterr().out
    assert "round 2 has nothing to make" in out
    assert_removed(tmp_path, out)


# --- removed configurations ----------------


def sweeps_of(tmp_path: Path, number: int) -> list[int]:
    """The repetitions that have a record of configuration `number`."""
    return sorted(
        json.loads(path.read_text())["sweep"]["repetition"]
        for path in (tmp_path / "runs").glob("*/record.json")
        if json.loads(path.read_text())["sweep"]["configuration"] == number
    )


def test_a_removed_configuration_is_planned_by_no_later_call(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy)
    start(monkeypatch, made)
    assert call(tmp_path, "--repetitions", "1", text=THREE) == 0

    # The person removes configuration 2: the option records it and runs nothing.
    made.made.clear()
    assert call(tmp_path, "--remove", "2", text=THREE) == 0
    assert made.made == []
    removed = json.loads((tmp_path / "runs" / REMOVED_FILE).read_text())
    key = configurations(read_spec(tmp_path / "spec.toml"))[1].key
    assert list(removed["removed"]) == [key]
    assert removed["removed"][key]["configuration"] == 2
    assert "configuration 2 is removed" in capsys.readouterr().out

    # A later call without a selection leaves it out, and says so.
    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == [(n, r, r) for r in (2, 3) for n in (1, 3)]
    assert sweeps_of(tmp_path, 2) == [1]
    assert "removed: configuration 2" in capsys.readouterr().out


def test_a_configuration_put_back_is_planned_again(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(throughput=noisy)
    start(monkeypatch, made)
    assert call(tmp_path, "--repetitions", "1", text=THREE) == 0
    assert call(tmp_path, "--remove", "2-3", text=THREE) == 0

    made.made.clear()
    assert call(tmp_path, "--put-back", "3", text=THREE) == 0
    assert made.made == []
    assert "configuration 3 is put back" in capsys.readouterr().out

    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == [(n, r, r) for r in (2, 3) for n in (1, 3)]
    assert call(tmp_path, "--put-back", "2", text=THREE) == 0
    made.made.clear()
    assert call(tmp_path, text=THREE) == 0
    assert made.runs() == [(2, 2, 2), (2, 3, 3)]
    assert json.loads((tmp_path / "runs" / REMOVED_FILE).read_text()) == {"removed": {}}


def test_a_selection_cannot_bring_back_a_removed_configuration(
    tmp_path, configured, monkeypatch
):
    made = FabricatedRuns(throughput=noisy)
    start(monkeypatch, made)
    assert call(tmp_path, "--remove", "1", text=THREE) == 0

    assert (
        call(tmp_path, "--configurations", "1-2", "--repetitions", "1", text=THREE) == 0
    )

    assert made.runs() == [(2, 1, 1)]


def test_the_list_shows_a_removed_configuration_as_removed(
    tmp_path, configured, monkeypatch, capsys
):
    start(monkeypatch, FabricatedRuns(throughput=noisy))
    assert call(tmp_path, "--repetitions", "1", text=THREE) == 0
    assert call(tmp_path, "--remove", "2", text=THREE) == 0
    capsys.readouterr()

    assert call(tmp_path, "--list", text=THREE) == 0
    lines = capsys.readouterr().out.splitlines()

    assert (
        lines[3] == "the next call starts at round 2: 2 runs of 2 open configurations"
    )
    assert "up to 3 runs: 1 made, 1 valid, removed since " in lines[5]
    assert "--put-back 2" in lines[5]
    assert "up to 3 runs: 1 made, 1 valid, open" in lines[4]


def test_a_removal_of_a_number_the_spec_does_not_have_is_refused(
    tmp_path, configured, monkeypatch, capsys
):
    start(monkeypatch, FabricatedRuns())

    assert call(tmp_path, "--remove", "4", text=THREE) == 2
    assert "there is no configuration 4" in capsys.readouterr().err
    assert not (tmp_path / "runs" / REMOVED_FILE).exists()


def test_a_removed_file_that_cannot_be_read_refuses_the_call(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns()
    start(monkeypatch, made)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / REMOVED_FILE).write_text("not json", encoding="utf-8")

    assert call(tmp_path, text=THREE) == 2
    assert REMOVED_FILE in capsys.readouterr().err
    assert made.made == []


# --- the words for a round ---------------------------------------------------


def test_a_round_is_a_time_window_and_never_a_window_alone():
    """The numbers decision's "time window" is a round; "window" alone is the measured part of a
    run, so no sentence about a round says "window" alone: in the glossary, the module texts, the
    draft spec's header and the README's rounds."""
    evaluation = Path(jms_kapps.__file__).parent / "evaluation"
    context = withheld_text(Path(jms_kapps.__file__).parents[2] / "CONTEXT.md")
    entry = context.split("**Precision rule**, **round**")[1].split("\n\n")[0]
    readme = README.read_text(encoding="utf-8")
    texts = {
        name: ast.get_docstring(
            ast.parse((evaluation / name).read_text(encoding="utf-8"))
        )
        or ""
        for name in ("rounds.py", "table.py", "spec.py", "sweep.py", "harness.py")
    }
    texts["CONTEXT.md"] = entry
    texts["after-series.toml"] = AFTER_SERIES.read_text(encoding="utf-8").split(
        "[defaults]"
    )[0]
    texts["README.md"] = readme.split("**Over two time windows.**")[1].split(
        "## The table"
    )[0]
    for name, text in texts.items():
        flat = " ".join(text.replace("#", " ").split())
        for sentence in flat.split(". "):
            if not re.search(r"\brounds?\b", sentence) or "_Avoid_" in sentence:
                continue
            for found in re.finditer(r"(\w+ )?windows?\b", sentence):
                assert (found.group(1) or "") == "time ", (name, sentence)
    assert "A round is a time window" in entry
    assert "window alone" in entry


# --- the list --------------------------------------------------------------------------------


def test_the_list_shows_the_rounds_and_where_each_configuration_stands(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(
        outcome=lambda number: (
            "failed: the store refused" if number == 4 else "completed"
        )
    )
    start(monkeypatch, made)
    call(tmp_path, "--repetitions", "1-2")
    capsys.readouterr()

    assert call(tmp_path, "--list") == 0
    lines = capsys.readouterr().out.splitlines()

    assert lines[0].startswith("2 configurations, 2 of them under the precision rule")
    assert lines[2] == (
        "rounds: round k makes repetition k of each open configuration under the rule, in "
        "the spec's order, up to round 20; a configuration is open while it does not meet "
        "the rule and has made fewer than 20 runs; one whose throughput median is 0 once it "
        "has 10 valid runs stops there"
    )
    assert (
        lines[3] == "the next call starts at round 3: 2 runs of 2 open configurations"
    )
    assert lines[4].startswith(
        "configuration spec 1 (block 1), under the precision rule, up to 20 runs: 2 made, "
        "2 valid, open, with a record: 1 completed, 2 completed: "
    )
    assert lines[5].startswith(
        "configuration spec 2 (block 1), under the precision rule, up to 20 runs: 2 made, "
        "1 valid, open, with a record: 1 completed, 2 failed: "
    )


def test_the_list_says_why_a_configuration_is_not_open(
    tmp_path, configured, monkeypatch, capsys
):
    made = FabricatedRuns(
        throughput=lambda number, repetition: (
            30.0 if number == 1 else noisy(1, repetition)
        )
    )
    start(monkeypatch, made)
    call(tmp_path, text=THREE)
    capsys.readouterr()

    assert call(tmp_path, "--list", text=THREE) == 0
    lines = capsys.readouterr().out.splitlines()

    assert lines[3] == (
        "no round is left: every configuration under the rule meets it, has made 3 runs or "
        "stopped at a throughput median of 0"
    )
    assert "up to 3 runs: 2 made, 2 valid, not open: met, with a record" in lines[4]
    assert (
        "up to 3 runs: 3 made, 3 valid, not open: precision not reached (3 runs), "
        "with a record" in lines[5]
    )


def test_a_ramp_s_levels_without_a_record_are_made_in_round_1(tmp_path):
    """A ramp is a heavy part: round 1 makes it, and a later round only while it has not ended
    or a request asks for it (`test_evaluation_sweep_heavy.py`)."""
    text = (
        SPEC
        + """
[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20]
repetitions = 1
"""
    )
    spec = read_spec(spec_file(tmp_path, text))
    found = configurations(spec)

    in_round_1, first = sweep.chosen_runs(
        spec, found, [], configuration_numbers=None, repetition_numbers=None
    )
    assert first is not None and first.number == 1
    assert [(c.number, r) for c, r in in_round_1] == [(1, 1), (2, 1), (3, 1), (4, 1)]

    # A call that selects only the ramp makes only the ramp.
    only, none = sweep.chosen_runs(
        spec, found, [], configuration_numbers=[3, 4], repetition_numbers=None
    )
    assert none is None and [(c.number, r) for c, r in only] == [(3, 1), (4, 1)]


# --- the draft spec of the "after" series ----------------------------------------------------


def test_the_draft_spec_loads_and_lists_the_cells_in_the_decided_order():
    found = configurations(read_spec(AFTER_SERIES))

    def values(*numbers: int) -> list[tuple[Any, ...]]:
        return [
            (
                c.values["topology"],
                c.values["prefill"],
                c.values["shacl"],
                c.values["wrapper"],
                c.values["collect_history"],
                c.values["module_strategy"],
            )
            for c in found
            if c.number in numbers
        ]

    assert len(found) == 26 and all(c.ruled for c in found)
    # The calibration pair first: the centre with the wrapper and without it, the centre once.
    assert values(1, 2) == [
        ("grid", "grid-1M.ttl", True, True, False, "real"),
        ("grid", "grid-1M.ttl", True, False, False, "real"),
    ]
    centre = [
        c
        for c in found
        if c.values["wrapper"]
        and c.values["shacl"]
        and c.values["topology"] == "grid"
        and c.values["prefill"] == "grid-1M.ttl"
        and not c.values["collect_history"]
        and c.values["module_strategy"] == "real"
    ]
    assert [c.number for c in centre] == [1]
    # Then SHACL off at each store size, the 60- and 120-module grids, history on, the rest, and
    # southward last.
    blocks = [c.block for c in found]
    assert blocks == sorted(blocks)
    assert [c.number for c in found if c.values["shacl"] is False] == [3, 4, 5, 6]
    assert [c.values["topology"] for c in found if c.block == 3] == [
        "../topologies/grid-60.json"
    ] * 4 + ["../topologies/grid-120.json"] * 4
    assert [c.number for c in found if c.values["collect_history"]] == [15]
    assert [c.number for c in found if c.values["module_strategy"] == "southward"] == [
        23,
        24,
        25,
        26,
    ]
    assert not any(
        c.values["module_strategy"] == "southward" for c in found if c.number < 23
    )


def test_the_draft_spec_s_list_needs_no_topology_or_prefill_file(tmp_path, capsys):
    assert main([str(AFTER_SERIES), "--list", "--runs", str(tmp_path / "none")]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("26 configurations, 26 of them under the precision rule")
    assert "the next call starts at round 1: 26 runs of 26 open configurations" in lines


def test_the_rounds_change_no_key_of_the_shipped_specs():
    assert [
        c.key for c in configurations(read_spec(ITERATION_ONE))
    ] == ITERATION_ONE_KEYS
    assert [c.key for c in configurations(read_spec(AFTER_SERIES))] == AFTER_SERIES_KEYS


def test_the_draft_spec_s_header_states_the_rounds_the_open_values_and_the_dependencies():
    text = AFTER_SERIES.read_text(encoding="utf-8")
    header = " ".join(text.split("[defaults]")[0].replace("#", " ").split())

    assert "transit time and the retry wait" in header
    assert "grid-60.json" in header
    assert "compressed replay" in header
    assert "in rounds" in header and "round k makes repetition k" in header
    assert "The rounds replaced the passes" in header
    assert "pass_sizes" not in text.split("\n[precision]\n")[1]


def test_the_readme_describes_the_rounds_instead_of_the_passes():
    text = README.read_text(encoding="utf-8")
    windows = " ".join(
        text.split("**Over two time windows.**")[1].split("## The table")[0].split()
    )

    assert "--repetitions 4-5" not in text
    assert "### The precision rule and the rounds" in windows
    assert "round k makes repetition k" in windows
    assert "A call makes at most one pass and exits" not in text
    assert "specs/after-series.toml" in windows
    # The stop time and the stop file, in the sweep's section and in the one command's.
    for named in ("--stop-at", f"`{STOP_FILE}`"):
        assert (
            named in text.split("## One command")[1].split("### The shipped specs")[0]
        )
        assert named in text.split("### Selection, records and stops")[1]
    assert "Until 2026-10-13" not in windows and "pass_sizes" in windows
    # An approval covers one spec as written: the draft spec's file changed with the rounds.
    assert "needs an approval issued for the file as it is" in windows
