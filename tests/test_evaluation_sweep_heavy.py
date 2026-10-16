"""The heavy parts of a spec with the precision rule, in its rounds (jms_kapps 114, Sören's decision
of 2026-10-13 on jms_kapps 85, note 1866296, decision 6): each ramp, each replay and each
sustained load runs one time, in round 1, at its place in the spec's order; a later round makes no
level of one that ended, and goes on with one that round 1 left unfinished; a person asks for one
more repetition with `--repeat`.

Each run and each replay level is a fabricated record, written by a stand-in for a run on the
configured store: no store, no run. The approvals are for the spec of a temporary folder (jms_kapps
96). The report, which a sweep makes after each run, is a stand-in too, so that the tests stay
short. The connection variables of a store are removed first.
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
from _repo import withheld_text

from jms_kapps.evaluation import sweep
from jms_kapps.evaluation.approval import Covered
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.sweep import STOP_FILE, main

# 1: under the rule, replayed at 1, 2 and 4. 2: under the rule. 3-5: a ramp. 6: a sustained load.
# A rule of at most 3 runs: three rounds.
SPEC = """\
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
topology = "cross"
replay_factors = [1, 2, 4]

[[sweep]]
topology = "pair"

[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20, 40]

[[sweep]]
topology = "pair"
boxes_per_minute = 60
heavy = true
"""

START = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


class Clock:
    """In place of this machine's clock: it moves on only when a run takes its time."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def noisy(number: int, repetition: int) -> float:
    """A throughput whose IQR is far beyond 10 % of its median: the rule is never met."""
    return 10.0 if repetition % 2 else 50.0


class Fabricated:
    """In place of a UC2 run and of a replay level on the configured store: each writes a
    fabricated record with the sweep fields it was given, and `log` keeps what was made, in order:
    `run N.R` for repetition R of configuration N, `replay N xF.R` for repetition R of the level at
    the factor F of the replay of configuration N.

    Args:
        ramp_broken_from: The box rate from which a level of a ramp shows a refusal for want of
            heap, or None for never.
        replay_broken_from: The factor from which a replay level is broken, or None for never.
        invalid: The UC2 runs, each (configuration, repetition), whose record says it is invalid.
        during: Called in each UC2 run and each level with its place among those made (from 1)
            and the runs' folder, as a person who acts while it runs.
    """

    def __init__(
        self,
        *,
        ramp_broken_from: float | None = None,
        replay_broken_from: float | None = None,
        invalid: set[tuple[int, int]] | None = None,
        during: Callable[[int, Path], None] = lambda made, runs: None,
        clock: Clock | None = None,
    ) -> None:
        self.ramp_broken_from = ramp_broken_from
        self.replay_broken_from = replay_broken_from
        self.invalid = invalid or set()
        self.during = during
        self.clock = clock or Clock()
        self.log: list[str] = []
        self.sweeps: list[dict[str, Any]] = []
        self.replayed: list[tuple[str, str]] = []
        self.covered: list[tuple[str, Covered | None]] = []
        # Every run made, across calls: its run id.
        self.count = 0

    def run(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        approval: Covered | None = None,
        **_: object,
    ) -> dict[str, Any]:
        number, repetition = sweep["configuration"], sweep["repetition"]
        made = f"run {number}.{repetition}"
        self.log.append(made)
        self.sweeps.append(dict(sweep))
        self.covered.append((made, approval))
        self.during(len(self.log), runs)
        self.clock.now += timedelta(minutes=12)
        rate = configuration.boxes_per_minute
        broken = self.ramp_broken_from is not None and rate >= self.ramp_broken_from
        self.count += 1
        found = record(
            f"run-{self.count:04d}",
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 100,
            completed_writes_per_minute=10.0 * rate,
            transports_ended_per_minute=noisy(number, repetition),
            heap_refusals_in_window=1 if broken else 0,
            spawns_accepted=100,
            valid=(number, repetition) not in self.invalid,
        )
        found["sweep"] = dict(sweep)
        write(runs, found)
        return found

    def replay(
        self,
        recorded: Path,
        *,
        runs: Path,
        factor: float,
        sweep: dict[str, Any],
        approval: Covered | None = None,
    ) -> dict[str, Any]:
        replayed = json.loads(recorded.read_text(encoding="utf-8"))
        number, repetition = sweep["configuration"], sweep["repetition"]
        made = f"replay {number} x{factor:g}.{repetition}"
        self.log.append(made)
        self.sweeps.append(dict(sweep))
        self.replayed.append((made, replayed["run_id"]))
        self.covered.append((made, approval))
        self.during(len(self.log), runs)
        self.clock.now += timedelta(minutes=12)
        broken = (
            self.replay_broken_from is not None and factor >= self.replay_broken_from
        )
        self.count += 1
        found = record(
            f"replay-{self.count:04d}",
            run_kind="baseline",
            replays=replayed["run_id"],
            requests=[request(0.01)] * 100,
        )
        found["sweep"] = dict(sweep)
        found["replay_level"] = {
            "factor": factor,
            "offered_per_minute": 100.0 * factor,
            "achieved_per_minute": 40.0 * factor if broken else 100.0 * factor,
            "requests_in_window": 100,
            "answered_in_window": 100,
            "refused_in_window": 0,
            "timed_out_in_window": 0,
            "heap_refusals_in_window": 1 if broken else 0,
            "replay_span_seconds": 60.0 / factor,
            "broken": broken,
            "signals": [{"signal": "heap refusal", "heap_refusals": 1}]
            if broken
            else [],
        }
        write(runs, found)
        return found


@pytest.fixture
def fabricated(monkeypatch):
    """A store is configured in the environment; the runs, the replay levels and the report are
    stand-ins. Returns a function that installs a `Fabricated` and returns it."""
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "update_the_report", lambda *args, **kwargs: None)

    def install(made: Fabricated | None = None) -> Fabricated:
        made = made or Fabricated()
        monkeypatch.setattr(sweep, "run_on_the_configured_store", made.run)
        monkeypatch.setattr(sweep, "run_replay_on_the_configured_store", made.replay)
        return made

    return install


def spec_file(folder: Path, text: str = SPEC) -> Path:
    path = folder / "spec.toml"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def approval_of_the_clock(folder: Path, spec_path: Path) -> Path:
    """The approval of the spec for two days from an hour before the stand-in clock's start: the
    sweep covers each heavy run at its start, by the call's clock (jms_kapps 112)."""
    return approval_file(
        folder,
        spec_path,
        start=START - timedelta(hours=1),
        end=START + timedelta(days=2),
    )


def call(
    tmp_path: Path,
    *options: str,
    text: str = SPEC,
    approve: bool = True,
    clock: Clock | None = None,
) -> int:
    """The sweep command, with the approval of the spec of the folder unless `approve` is off."""
    spec_path = spec_file(tmp_path, text)
    approval = (
        ["--heavy-run-approval", str(approval_of_the_clock(tmp_path, spec_path))]
        if approve
        else []
    )
    return main(
        [str(spec_path), "--runs", str(tmp_path / "runs"), *approval, *options],
        clock=clock or Clock(),
    )


def stop_file_in(made: int) -> Callable[[int, Path], None]:
    """A person who makes the stop file while the run or level `made` runs."""

    def during(place: int, runs: Path) -> None:
        if place == made:
            runs.mkdir(parents=True, exist_ok=True)
            (runs / STOP_FILE).write_text("", encoding="utf-8")

    return during


ROUND_1 = [
    "run 1.1",
    "replay 1 x1.1",
    "replay 1 x2.1",
    "replay 1 x4.1",
    "run 2.1",
    "run 3.1",
    "run 4.1",
    "run 5.1",
    "run 6.1",
]


# --- round 1 ---------------------------------------------------------------------------------


def test_round_1_makes_each_heavy_part_once_in_the_spec_s_order(tmp_path, fabricated):
    made = fabricated()

    assert call(tmp_path) == 0

    # Round 1: configuration 1, its replay levels after its first valid run, configuration 2,
    # the ramp at its block's place, its rates rising, and the sustained load. Rounds 2 and 3
    # make no heavy part again.
    assert made.log == [*ROUND_1, "run 1.2", "run 2.2", "run 1.3", "run 2.3"]


# --- later rounds ----------------------------------------------------------------------------


def test_a_ramp_that_round_1_left_unfinished_goes_on_in_round_2_from_its_lowest_level_without_a_record(
    tmp_path, fabricated
):
    # The person makes the stop file while the ramp's second level runs.
    made = fabricated(Fabricated(during=stop_file_in(7)))
    assert call(tmp_path) == 0
    assert made.log == ROUND_1[:7]

    made.log.clear()
    made.during = lambda place, runs: None
    assert call(tmp_path) == 0

    # The next call makes round 2: the ramp goes on at its third level, and the sustained load
    # that round 1 left makes its run, each at its place in the spec's order.
    assert made.log == [
        "run 1.2",
        "run 2.2",
        "run 5.1",
        "run 6.1",
        "run 1.3",
        "run 2.3",
    ]


def test_a_later_round_makes_no_level_of_a_ramp_or_a_replay_that_ended_in_round_1(
    tmp_path, fabricated
):
    made = fabricated(Fabricated(ramp_broken_from=20, replay_broken_from=2))

    assert call(tmp_path) == 0

    # The ramp broke at 20 boxes per minute and the replay at the factor 2: neither sends a
    # higher level, in round 1 or later.
    assert made.log == [
        "run 1.1",
        "replay 1 x1.1",
        "replay 1 x2.1",
        "run 2.1",
        "run 3.1",
        "run 4.1",
        "run 6.1",
        "run 1.2",
        "run 2.2",
        "run 1.3",
        "run 2.3",
    ]

    # A later call has nothing of them to make.
    made.log.clear()
    assert call(tmp_path) == 0
    assert made.log == []


# 1: under the rule. 2: under the rule, replayed at 1, 2 and 4, the last in the spec's order.
LAST_REPLAYED = """\
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
topology = "cross"

[[sweep]]
topology = "pair"
replay_factors = [1, 2, 4]
"""


def test_a_replay_that_round_1_left_unfinished_goes_on_in_round_2_from_its_lowest_factor_without_a_record(
    tmp_path, fabricated
):
    # The person makes the stop file while the level at the factor 2 runs.
    made = fabricated(Fabricated(during=stop_file_in(4)))
    assert call(tmp_path, text=LAST_REPLAYED) == 0
    assert made.log == ["run 1.1", "run 2.1", "replay 2 x1.1", "replay 2 x2.1"]

    made.log.clear()
    made.during = lambda place, runs: None
    assert call(tmp_path, text=LAST_REPLAYED) == 0

    # Round 2 goes on with the factor 4, right after configuration 2's run of round 2; it
    # replays the run that the levels before it replayed.
    assert made.log == [
        "run 1.2",
        "run 2.2",
        "replay 2 x4.1",
        "run 1.3",
        "run 2.3",
    ]
    assert {replayed for _, replayed in made.replayed} == {"run-0002"}


def test_a_configuration_without_a_valid_run_in_round_1_has_its_replay_after_its_first_valid_run(
    tmp_path, fabricated, capsys
):
    made = fabricated(Fabricated(invalid={(1, 1)}))

    code = call(tmp_path)

    assert made.log == [
        "run 1.1",
        "run 2.1",
        "run 3.1",
        "run 4.1",
        "run 5.1",
        "run 6.1",
        "run 1.2",
        "replay 1 x1.1",
        "replay 1 x2.1",
        "replay 1 x4.1",
        "run 2.2",
        "run 1.3",
        "run 2.3",
    ]
    # The levels replay configuration 1's first valid run, its repetition 2 (the 7th run made).
    assert {replayed for _, replayed in made.replayed} == {"run-0007"}
    assert "configuration spec 1 has no valid run of use case 2 to replay" in (
        capsys.readouterr().out
    )
    # The replay is not left at the end of the call: a later round made it.
    assert code == 0


def _remove_run(tmp_path: Path, run_id: str) -> None:
    """Move the record of `run_id` out of the runs' folder, as a person who tidies it."""
    import shutil

    shutil.rmtree(tmp_path / "runs" / run_id)


@pytest.mark.parametrize(
    "case",
    ["a repetition 2 whose run is gone", "a repetition 1 that some levels started"],
)
def test_a_replay_left_for_want_of_its_run_ends_the_call_with_1(
    tmp_path, fabricated, capsys, case
):
    """The exit code says what the message says. The run that the replay's first
    repetition replayed (run-0001) has no record any more, so its levels are left."""
    if case == "a repetition 2 whose run is gone":
        made = fabricated()
        assert call(tmp_path, "--repetitions", "1") == 0
        options: tuple[str, ...] = ("--repeat", "1:2")
    else:
        # The person makes the stop file while the level at the factor 2 runs.
        made = fabricated(Fabricated(during=stop_file_in(3)))
        assert call(tmp_path) == 0
        made.during = lambda place, runs: None
        options = ()
    _remove_run(tmp_path, "run-0001")
    capsys.readouterr()

    code = call(tmp_path, *options)

    out = capsys.readouterr().out
    assert (
        "which the first repetition of the replay of configuration spec 1 replayed"
        in out
    )
    assert not [
        entry
        for entry in made.log
        if entry.startswith("replay 1") and entry.endswith(".2")
    ]
    assert code == 1


# --- one more repetition on request ------------------------------------------------------------


def records_of(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "runs").glob("*/record.json"))
    ]


def test_a_sustained_load_runs_one_time_in_round_1_and_again_on_request(
    tmp_path, fabricated, capsys
):
    made = fabricated()
    assert call(tmp_path) == 0
    assert [entry for entry in made.log if entry.startswith("run 6.")] == ["run 6.1"]

    # No round is left; the request makes repetition 2 of the sustained load, and nothing
    # else.
    made.log.clear()
    assert call(tmp_path, "--repeat", "6:2") == 0

    assert made.log == ["run 6.2"]
    runs_of_6 = [
        r["sweep"] for r in records_of(tmp_path) if r["sweep"]["configuration"] == 6
    ]
    assert sorted(s["repetition"] for s in runs_of_6) == [1, 2]
    assert {s["key"] for s in runs_of_6} == {runs_of_6[0]["key"]}
    out = capsys.readouterr().out
    assert "configuration 6, a sustained load: repetition 2" in out
    # The next call, with the same request or none, makes no repetition 3.
    made.log.clear()
    assert call(tmp_path, "--repeat", "6:2") == 0
    assert call(tmp_path) == 0
    assert made.log == []


def test_on_request_the_next_round_repeats_a_ramp_and_a_replay_from_their_lowest_level_until_they_end(
    tmp_path, fabricated
):
    made = fabricated(Fabricated(ramp_broken_from=20, replay_broken_from=4))
    assert call(tmp_path, "--repetitions", "1") == 0
    assert made.log == [
        "run 1.1",
        "replay 1 x1.1",
        "replay 1 x2.1",
        "replay 1 x4.1",
        "run 2.1",
        "run 3.1",
        "run 4.1",
        "run 6.1",
    ]

    made.log.clear()
    assert call(tmp_path, "--repeat", "block3:2,1:2") == 0

    # Round 2 makes repetition 2 of the replay right after configuration 1's run, and of the
    # ramp at its block's place, each from its lowest level until it ends by its rule; round 3
    # makes no repetition 3: the target is repetition 2.
    assert made.log == [
        "run 1.2",
        "replay 1 x1.2",
        "replay 1 x2.2",
        "replay 1 x4.2",
        "run 2.2",
        "run 3.2",
        "run 4.2",
        "run 1.3",
        "run 2.3",
    ]
    repeated = [
        s for s in made.sweeps if s["repetition"] == 2 and s["configuration"] != 2
    ]
    assert [(s["configuration"], s.get("replay_factor")) for s in repeated] == [
        (1, None),
        (1, 1.0),
        (1, 2.0),
        (1, 4.0),
        (3, None),
        (4, None),
    ]


def test_the_number_of_a_heavy_configuration_with_replay_factors_repeats_both(
    tmp_path, fabricated
):
    text = SPEC.replace(
        "boxes_per_minute = 60\nheavy = true\n",
        "boxes_per_minute = 60\nheavy = true\nreplay_factors = [1, 2]\n",
    )
    made = fabricated()
    assert call(tmp_path, "--repetitions", "1", text=text) == 0
    assert made.log[-3:] == ["run 6.1", "replay 6 x1.1", "replay 6 x2.1"]
    made.log.clear()
    made.replayed.clear()

    assert call(tmp_path, "--repeat", "6:2", "--repetitions", "2", text=text) == 0

    # Round 2 makes repetition 2 of the sustained load and of its replay, at its place; the
    # replay replays the sustained load's first run, as its first repetition did.
    assert made.log == [
        "run 1.2",
        "run 2.2",
        "run 6.2",
        "replay 6 x1.2",
        "replay 6 x2.2",
    ]
    assert {replayed for _, replayed in made.replayed} == {"run-0009"}


def test_a_repeated_replay_replays_the_same_run_as_its_first_repetition(
    tmp_path, fabricated
):
    """The first repetition replayed configuration 1's repetition 2. A record of repetition 1
    that counts came later (a run moved back into the folder): the first valid run is now
    another one, and the repetition still replays repetition 2's run."""
    made = fabricated(Fabricated(invalid={(1, 1)}))
    assert call(tmp_path, "--repetitions", "1-2") == 0
    first = {replayed for _, replayed in made.replayed}
    assert first == {"run-0007"}
    spec_path = spec_file(tmp_path)
    from jms_kapps.evaluation.spec import configurations, read_spec

    configuration = configurations(read_spec(spec_path))[0]
    earlier = record(
        "run-0000-moved-back",
        configuration,
        commit_latencies=[0.1] * 20,
        requests=[request(0.01)] * 100,
    )
    write(tmp_path / "runs", earlier)
    made.replayed.clear()

    assert call(tmp_path, "--repeat", "1:2") == 0

    assert [entry for entry, _ in made.replayed] == [
        "replay 1 x1.2",
        "replay 1 x2.2",
        "replay 1 x4.2",
    ]
    assert {replayed for _, replayed in made.replayed} == {"run-0007"}


FIXED_REPLAYED = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 2

[[sweep]]
topology = "cross"
replay_factors = [1, 2, 4]
"""


def test_a_spec_of_fixed_repetitions_replays_its_first_valid_run_as_before(
    tmp_path, fabricated
):
    """A spec without the precision rule replays the first valid run that each
    call finds, as before jms_kapps 114, and not the run that its levels before replayed."""
    made = fabricated(Fabricated(invalid={(1, 1)}, during=stop_file_in(3)))
    assert call(tmp_path, text=FIXED_REPLAYED) == 0
    assert made.replayed == [("replay 1 x1.1", "run-0002")]
    from jms_kapps.evaluation.spec import configurations, read_spec

    configuration = configurations(read_spec(spec_file(tmp_path, FIXED_REPLAYED)))[0]
    write(
        tmp_path / "runs",
        record(
            "run-0000-moved-back",
            configuration,
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 100,
        ),
    )
    made.during = lambda place, runs: None
    made.replayed.clear()

    assert call(tmp_path, text=FIXED_REPLAYED) == 0

    assert made.replayed == [
        ("replay 1 x2.1", "run-0000-moved-back"),
        ("replay 1 x4.1", "run-0000-moved-back"),
    ]


def test_a_repetition_on_request_of_a_ramp_runs_only_under_an_approval_that_covers_it(
    tmp_path, fabricated, capsys
):
    made = fabricated()
    assert call(tmp_path) == 0
    made.log.clear()
    made.covered.clear()

    # Without an approval each level is skipped and listed, and nothing else is made (jms_kapps
    # 112: until then the call was refused before its first run).
    assert call(tmp_path, "--repeat", "block3:2", approve=False) == 0
    assert made.log == []
    out = capsys.readouterr().out
    assert "configuration 3, repetition 2 (the ramp of block 3" in out
    assert "no approval" in out

    assert call(tmp_path, "--repeat", "block3:2") == 0
    assert made.log == ["run 3.2", "run 4.2", "run 5.2"]
    assert all(isinstance(covered, Covered) for _, covered in made.covered)


@pytest.mark.parametrize(
    ("named", "said"),
    [
        ("block2:2", "block 2 of the spec is no ramp"),
        ("4:2", "configuration 4 is a level of the ramp of block 3: name the ramp"),
        ("2:2", "configuration 2 is no heavy part"),
        ("9:2", "there is no configuration 9"),
        ("ramp3:2", "names each heavy part with its target repetition"),
        ("blockx:2", "names each heavy part with its target repetition"),
        ("block3", "names each heavy part with its target repetition"),
        ("6:0", "names each heavy part with its target repetition"),
    ],
)
def test_a_request_that_names_no_heavy_part_is_refused_before_the_first_run(
    tmp_path, fabricated, capsys, named, said
):
    made = fabricated()

    assert call(tmp_path, "--repeat", named) == 2

    assert made.log == []
    assert said in capsys.readouterr().err


def test_a_restart_with_the_same_request_before_during_and_after_the_target_repetition(
    tmp_path, fabricated, capsys
):
    """Sören's ruling of 2026-10-14 on jms_kapps 114: `--repeat block3:2` makes sure that
    repetition 2 of the ramp exists. A restart with the same command line goes on, and makes
    nothing more once the repetition exists."""
    made = fabricated()
    assert call(tmp_path, "--repetitions", "1") == 0
    made.log.clear()
    line = ("--repeat", "block3:2")

    # Before: a stop file stops the call before its first run.
    (tmp_path / "runs" / STOP_FILE).write_text("", encoding="utf-8")
    assert call(tmp_path, *line) == 0
    assert made.log == []

    # During: the person makes the stop file while the ramp's second level of repetition 2 runs.
    made.during = stop_file_in(4)
    assert call(tmp_path, *line) == 0
    assert made.log == ["run 1.2", "run 2.2", "run 3.2", "run 4.2"]

    # The restart goes on with the ramp's third level, in round 3.
    made.log.clear()
    made.during = lambda place, runs: None
    assert call(tmp_path, *line) == 0
    assert made.log == ["run 1.3", "run 2.3", "run 5.2"]

    # After: repetition 2 exists, so the same line makes nothing, and exits with 0.
    made.log.clear()
    capsys.readouterr()
    assert call(tmp_path, *line) == 0
    assert made.log == []
    assert "repetition 2 exists" in capsys.readouterr().out


def test_a_target_at_or_below_an_existing_repetition_does_nothing(
    tmp_path, fabricated, capsys
):
    made = fabricated()
    assert call(tmp_path) == 0
    made.log.clear()
    capsys.readouterr()

    assert call(tmp_path, "--repeat", "block3:1,6:1") == 0

    assert made.log == []
    out = capsys.readouterr().out
    assert "the ramp of block 3: repetition 1 exists" in out


def test_a_higher_target_makes_one_more_repetition_in_each_round_until_it_exists(
    tmp_path, fabricated
):
    made = fabricated()
    assert call(tmp_path, "--repetitions", "1") == 0
    made.log.clear()

    assert call(tmp_path, "--repeat", "6:3") == 0

    assert made.log == [
        "run 1.2",
        "run 2.2",
        "run 6.2",
        "run 1.3",
        "run 2.3",
        "run 6.3",
    ]


def test_a_request_for_a_heavy_part_that_has_not_ended_lets_it_end_first(
    tmp_path, fabricated
):
    """The ramp is unfinished and the sustained load has not run yet: the request is accepted,
    each goes on as the rounds make it, and the next round makes its repetition 2."""
    made = fabricated(Fabricated(during=stop_file_in(7)))
    assert call(tmp_path) == 0
    made.log.clear()
    made.during = lambda place, runs: None

    assert call(tmp_path, "--repeat", "block3:2,6:2") == 0

    assert made.log == [
        "run 1.2",
        "run 2.2",
        "run 5.1",
        "run 6.1",
        "run 1.3",
        "run 2.3",
        "run 3.2",
        "run 4.2",
        "run 5.2",
        "run 6.2",
    ]


def test_a_request_outside_the_call_s_selection_is_refused(
    tmp_path, fabricated, capsys
):
    made = fabricated()
    assert call(tmp_path, "--repetitions", "1") == 0
    made.log.clear()

    assert call(tmp_path, "--repeat", "block3:2", "--configurations", "1-2") == 2
    assert "--configurations does not select" in capsys.readouterr().err
    assert call(tmp_path, "--repeat", "block3:2", "--repetitions", "3") == 2
    assert "--repetitions does not select" in capsys.readouterr().err
    assert made.log == []


def test_a_request_whose_next_repetition_repetitions_leaves_out_makes_nothing_and_says_so(
    tmp_path, fabricated, capsys
):
    """After repetition 1 of the sustained load, `--repeat 6:3 --repetitions 3`
    selects the target, but the next round would make repetition 2, which `--repetitions 3`
    leaves out. The call says so in one line, makes nothing for that part and goes on, with no
    refusal, so that a restart with the same line stays safe."""
    made = fabricated()
    assert call(tmp_path, "--repetitions", "1") == 0
    capsys.readouterr()

    for _ in range(2):
        made.log.clear()

        assert call(tmp_path, "--repeat", "6:3", "--repetitions", "3") == 0

        assert not [line for line in made.log if line.startswith("run 6.")]
        out = capsys.readouterr().out
        [said] = [line for line in out.splitlines() if "configuration 6" in line]
        assert "on request" in said
        assert "repetition 2" in said
        assert "--repetitions does not select" in said


def test_a_request_needs_a_spec_with_the_precision_rule(tmp_path, fabricated, capsys):
    fixed = SPEC.replace(
        "[precision]\nminimum_runs = 2\nmaximum_runs = 3\n", ""
    ).replace("heavy = false\n", "heavy = false\nrepetitions = 1\n", 1)
    made = fabricated()
    assert call(tmp_path, text=fixed) == 0
    made.log.clear()

    assert call(tmp_path, "--repeat", "6:2", text=fixed) == 2

    assert made.log == []
    assert "a spec with a [precision] table" in capsys.readouterr().err


# --- a heavy configuration under the precision rule --------------------------------------------


HEAVY_ALONE = """\
[defaults]
boxes_per_minute = 60
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = true
repetitions = 2

[precision]

[[sweep]]
topology = "pair"
"""


@pytest.mark.parametrize(
    "text",
    [
        # The sustained load of the spec, with 3 repetitions in its block.
        SPEC.replace(
            "boxes_per_minute = 60\nheavy = true\n",
            "boxes_per_minute = 60\nheavy = true\nrepetitions = 3\n",
        ),
        # A spec of a heavy configuration alone, with 2 repetitions in its defaults.
        HEAVY_ALONE,
    ],
    ids=["in its block", "in the defaults"],
)
def test_a_heavy_configuration_with_other_repetitions_than_1_is_refused_under_the_precision_rule(
    tmp_path, text
):
    from jms_kapps.evaluation.spec import SpecRefused, configurations, read_spec

    with pytest.raises(SpecRefused) as refused:
        configurations(read_spec(spec_file(tmp_path, text)))

    message = str(refused.value)
    assert "[precision]" in message
    assert "repetitions" in message
    assert "one time, in round 1" in message
    assert "--repeat" in message


def test_a_heavy_configuration_with_one_repetition_runs_under_the_precision_rule(
    tmp_path,
):
    from jms_kapps.evaluation.spec import configurations, read_spec

    text = SPEC.replace(
        "boxes_per_minute = 60\nheavy = true\n",
        "boxes_per_minute = 60\nheavy = true\nrepetitions = 1\n",
    )

    found = configurations(read_spec(spec_file(tmp_path, text)))

    assert [(c.number, c.repetitions, c.ruled) for c in found if not c.ruled] == [
        (3, 1, False),
        (4, 1, False),
        (5, 1, False),
        (6, 1, False),
    ]


# --- the list --------------------------------------------------------------------------------


def listed(tmp_path: Path, capsys, text: str = SPEC) -> list[str]:
    capsys.readouterr()
    assert (
        main(
            [str(spec_file(tmp_path, text)), "--runs", str(tmp_path / "runs"), "--list"]
        )
        == 0
    )
    return capsys.readouterr().out.splitlines()


def test_the_list_shows_the_heavy_parts_that_round_1_makes_in_the_spec_s_order(
    tmp_path, capsys
):
    lines = listed(tmp_path, capsys)

    at = lines.index(
        "heavy parts: each ramp, replay and sustained load runs one time, in round 1, at its "
        "place in the spec's order (a replay right after its configuration's first valid run), "
        "and again on request (--repeat <part>:<repetition>)"
    )
    assert lines[at + 1 : at + 4] == [
        (
            "  the replay of configuration 1 (1): waits for the first valid run of its "
            "configuration; a round with a run of configuration 1 makes its repetition 1 right "
            "after that run"
        ),
        (
            "  the ramp of block 3 (block3): no record yet; the next round makes its "
            "repetition 1"
        ),
        (
            "  configuration 6, a sustained load (6): no record yet; the next round "
            "makes its repetition 1"
        ),
    ]


def test_the_list_shows_where_each_heavy_part_stands_after_round_1(
    tmp_path, fabricated, capsys
):
    made = fabricated(Fabricated(ramp_broken_from=20, during=stop_file_in(2)))
    call(tmp_path)

    lines = listed(tmp_path, capsys)

    at = next(i for i, line in enumerate(lines) if line.startswith("heavy parts:"))
    assert lines[at + 1 : at + 4] == [
        (
            "  the replay of configuration 1 (1): repetition 1 has not ended (not every "
            "factor has a record); the next round goes on with it, from its lowest level without "
            "a record"
        ),
        (
            "  the ramp of block 3 (block3): no record yet; the next round makes its "
            "repetition 1"
        ),
        (
            "  configuration 6, a sustained load (6): no record yet; the next round "
            "makes its repetition 1"
        ),
    ]
    made.during = lambda place, runs: None
    call(tmp_path, "--repetitions", "1")

    lines = listed(tmp_path, capsys)
    at = next(i for i, line in enumerate(lines) if line.startswith("heavy parts:"))
    assert lines[at + 1 : at + 4] == [
        (
            "  the replay of configuration 1 (1): repetition 1 ended: it was not broken "
            "up to the factor 4, its highest; --repeat 1:2 asks for repetition 2"
        ),
        (
            "  the ramp of block 3 (block3): repetition 1 ended: it broke at 20 boxes per "
            "minute (heap refusal); its scale is 10 boxes per minute; --repeat block3:2 asks for "
            "repetition 2"
        ),
        (
            "  configuration 6, a sustained load (6): repetition 1 ended: its run has a "
            "record; --repeat 6:2 asks for repetition 2"
        ),
    ]


# --- the one command -------------------------------------------------------------------------


def eval_call(tmp_path: Path, *options: str, approve: bool = True) -> int:
    """`jms-eval` on the spec of the folder, with its approval unless `approve` is off."""
    from jms_kapps.evaluation import harness

    spec_path = spec_file(tmp_path)
    approval = (
        ["--heavy-run-approval", str(approval_of_the_clock(tmp_path, spec_path))]
        if approve
        else []
    )
    return harness.main(
        [
            str(spec_path),
            "--output",
            str(tmp_path / "runs"),
            "--prefills",
            str(tmp_path / "prefills"),
            *approval,
            *options,
        ],
        clock=Clock(),
    )


@pytest.fixture
def one_command(monkeypatch, fabricated):
    """The stand-ins of `fabricated`, installed for `jms-eval` too; the report and the table
    at its end are a stand-in."""
    from jms_kapps.evaluation import harness

    monkeypatch.setattr(harness, "make_report_and_table", lambda *a, **k: True)

    def install(made: Fabricated | None = None) -> Fabricated:
        made = fabricated(made)
        monkeypatch.setattr(harness, "run_on_the_configured_store", made.run)
        monkeypatch.setattr(harness, "run_replay_on_the_configured_store", made.replay)
        return made

    return install


def test_the_one_command_takes_the_request_and_shows_the_heavy_parts_in_its_dry_run(
    tmp_path, one_command, capsys
):
    made = one_command()
    assert eval_call(tmp_path) == 0
    assert made.log == [*ROUND_1, "run 1.2", "run 2.2", "run 1.3", "run 2.3"]
    made.log.clear()
    capsys.readouterr()

    assert eval_call(tmp_path, "--repeat", "block3:2", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "on request: the ramp of block 3: repetition 2" in out
    assert "  the ramp of block 3 (block3): repetition 1 ended" in out
    assert made.log == []

    assert eval_call(tmp_path, "--repeat", "block3:2") == 0
    assert made.log == ["run 3.2", "run 4.2", "run 5.2"]
    # A restart with the same line makes nothing more.
    assert eval_call(tmp_path, "--repeat", "block3:2") == 0
    assert made.log == ["run 3.2", "run 4.2", "run 5.2"]


def test_without_an_approval_the_one_command_skips_and_lists_the_heavy_levels_of_a_request(
    tmp_path, one_command, capsys
):
    made = one_command()
    assert eval_call(tmp_path) == 0
    made.log.clear()
    capsys.readouterr()

    assert eval_call(tmp_path, "--repeat", "1:2", approve=False) == 0

    # The factor 1 runs at normal load; the factors 2 and 4 are heavy and are listed as skipped.
    assert made.log == ["replay 1 x1.2"]
    out = capsys.readouterr().out
    assert "the replay of configuration 1 at the factors 2, 4" in out


# --- the README and the glossary ---------------------------------------------------------------


def test_the_readme_says_the_rules_of_the_heavy_parts_with_a_dated_note_of_soeren_s_decision():
    import jms_kapps

    text = (Path(jms_kapps.__file__).parent / "evaluation" / "README.md").read_text(
        encoding="utf-8"
    )

    def section(first: str, after: str) -> str:
        return " ".join(text.split(first)[1].split(after)[0].split())

    ramp = section("### A ramp: where the store breaks", "## The store-only baseline")
    replay = section("### Replay levels in a sweep", "### The iteration-one spec")
    rounds = section("### The precision rule and the rounds", "## The table of a spec")
    for said in (ramp, replay, rounds):
        assert "jms_kapps 114" in said
        assert "Sören's decision of 2026-10-13" in said
        assert "note 1866296, decision 6" in said
    assert "runs one time, in round 1" in ramp and "--repeat block3" in ramp
    assert "right after its configuration's first valid run" in replay
    heavy = rounds.split("### The heavy parts in the rounds")[1]
    assert "Each heavy part runs one time, in round 1" in heavy
    assert "A later round makes no level of a heavy part that ended" in heavy
    assert "goes on in the next round from its lowest level without a record" in heavy
    assert "A repeated replay replays the same run" in heavy
    assert "Until 2026-10-14" in heavy


def test_the_module_docs_name_soeren_s_rulings_with_the_dated_note_of_the_house():
    """The docstrings rewritten after a review keep the old rule in the
    house form, "(Changed on 2026-10-14, jms_kapps 114: until then ...)"."""
    import ast

    from jms_kapps.evaluation import heavy_parts, table

    for module in (heavy_parts, sweep, table):
        doc = " ".join(
            (
                ast.get_docstring(ast.parse(Path(module.__file__).read_text())) or ""
            ).split()
        )
        assert "(Changed on 2026-10-14, jms_kapps 114: until then " in doc, module
        assert "Sören's ruling of 2026-10-14)" not in doc, module
        assert "Sören's ruling of 2026-10-14;" not in doc, module


def test_the_glossary_defines_the_heavy_part_and_the_repetition_on_request():
    import jms_kapps

    context = withheld_text(Path(jms_kapps.__file__).parents[2] / "CONTEXT.md")

    entry = context.split(
        "**Heavy part**, **sustained load**, **repetition on request**"
    )[1]
    entry = entry.split("\n\n")[0]
    assert "runs one time, in round 1" in entry
    assert "`--repeat`" in entry
    assert "jms_kapps 114" in entry
    assert "Interim names (Etienne's rule of 2026-10-11), of the heavy parts" in context
