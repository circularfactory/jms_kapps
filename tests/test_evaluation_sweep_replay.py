"""The sweep replays a recorded run compressed in time, level by level (jms_kapps 98, from the
decisions of jms_kapps 85, note 1864593): a block of the spec gives replay factors; after the UC2
runs of each of its configurations the sweep replays the first valid one once per factor, in rising
order, and stops the replay of that configuration at the first broken level.

No test here makes a run: the UC2 runs and the replay levels are fabricated records, written by
stand-ins for a run on the configured store (a heavy run is never a test or a CI job). The
approvals are for the spec of a temporary folder (jms_kapps 96). The connection variables of a store
are removed first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _evaluation_approvals import approval_file
from _evaluation_records import record, request, write

from jms_kapps.evaluation import sweep
from jms_kapps.evaluation.approval import Covered, HeavyRunRefused
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.replay_levels import level_key
from jms_kapps.evaluation.spec import SpecRefused, configurations, read_spec
from jms_kapps.evaluation.summary import read_summaries, summary_of
from jms_kapps.evaluation.sweep import (
    chosen_replays,
    first_valid_run,
    main,
    replay_ends,
)

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 2

# 1: a configuration whose first valid run is replayed at four factors.
[[sweep]]
topology = "cross"
replay_factors = [1, 2, 4, 8]

# 2: a configuration with no factor.
[[sweep]]
topology = "pair"

# 3: a configuration replayed at normal load only.
[[sweep]]
topology = "grid"
replay_factors = [1]
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
    """In place of a run on the configured store: each UC2 run writes a fabricated record with the
    sweep fields it was given, and keeps which run it was. `invalid` are the (configuration,
    repetition) pairs whose record says it is invalid."""

    def __init__(self, invalid: set[tuple[int, int]] | None = None) -> None:
        self.invalid = invalid or set()
        self.made: list[tuple[int, int]] = []
        self.log: list[str] = []

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        approval: object = None,
        **_: object,
    ) -> dict[str, Any]:
        pair = (sweep["configuration"], sweep["repetition"])
        self.made.append(pair)
        self.log.append(f"run {pair[0]}.{pair[1]}")
        found = record(
            f"run-{pair[0]}-{pair[1]}",
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 100,
            valid=pair not in self.invalid,
        )
        found["sweep"] = dict(sweep)
        write(runs, found)
        return found


class FabricatedReplays:
    """In place of a replay level on the configured store: each writes a fabricated baseline
    record with the numbers of its factor, and keeps what it was given. A level is broken from
    `broken_from` up; `failing` is the factor whose record says it failed."""

    def __init__(
        self,
        runs_log: list[str] | None = None,
        *,
        broken_from: float | None = None,
        failing: float | None = None,
        refusing: bool = False,
    ) -> None:
        self.broken_from = broken_from
        self.failing = failing
        self.refusing = refusing
        self.log = runs_log if runs_log is not None else []
        self.made: list[tuple[int, float, str, Covered | None]] = []

    def __call__(
        self,
        recorded: Path,
        *,
        runs: Path,
        factor: float,
        sweep: dict[str, Any],
        approval: Covered | None = None,
    ) -> dict[str, Any]:
        if self.refusing:
            raise HeavyRunRefused("outside the period of the approval")
        replayed = json.loads(recorded.read_text(encoding="utf-8"))
        self.made.append((sweep["configuration"], factor, replayed["run_id"], approval))
        self.log.append(f"replay {sweep['configuration']} x{factor:g}")
        broken = self.broken_from is not None and factor >= self.broken_from
        failed = self.failing == factor
        found = record(
            f"replay-{sweep['configuration']}-x{factor:g}",
            run_kind="baseline",
            replays=replayed["run_id"],
            requests=[request(0.01)] * 100,
            outcome="failed: RuntimeError: the store went away"
            if failed
            else "completed",
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
def configured(monkeypatch):
    """A store is configured in the environment, and the runs and the replays are stand-ins."""
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")

    def install(
        runs: FabricatedRuns | None = None, replays: FabricatedReplays | None = None
    ) -> tuple[FabricatedRuns, FabricatedReplays]:
        runs = runs or FabricatedRuns()
        replays = replays or FabricatedReplays(runs.log)
        replays.log = runs.log
        monkeypatch.setattr(sweep, "run_on_the_configured_store", runs)
        monkeypatch.setattr(sweep, "run_replay_on_the_configured_store", replays)
        return runs, replays

    return install


def call(
    tmp_path: Path, *options: str, spec_path: Path | None = None, approve: bool = True
) -> int:
    """The sweep command, with the approval of the spec of the folder unless `approve` is off."""
    spec_path = spec_path or spec_file(tmp_path)
    approval = (
        ["--heavy-run-approval", str(approval_file(tmp_path, spec_path))]
        if approve
        else []
    )
    return main([str(spec_path), "--runs", str(tmp_path / "runs"), *approval, *options])


# The spec gives replay factors.


def test_a_block_gives_replay_factors_outside_the_configuration_s_key(tmp_path):
    with_factors = configurations(read_spec(spec_file(tmp_path)))
    without = configurations(
        read_spec(
            spec_file(tmp_path, SPEC.replace("replay_factors = [1, 2, 4, 8]\n", ""))
        )
    )

    assert [c.replay_factors for c in with_factors] == [
        (1.0, 2.0, 4.0, 8.0),
        (),
        (1.0,),
    ]
    assert [c.replay_factors for c in without] == [(), (), (1.0,)]
    # The factors are no axis of the block and no part of the key: the keys, the numbers and the
    # repetitions are the same.
    assert [(c.number, c.key, c.repetitions) for c in with_factors] == [
        (c.number, c.key, c.repetitions) for c in without
    ]
    assert "replay_factors" not in with_factors[0].values


def test_the_factors_come_in_rising_order_whatever_order_the_block_lists_them(tmp_path):
    text = SPEC.replace("[1, 2, 4, 8]", "[8, 2, 1, 4]")

    [first, *_] = configurations(read_spec(spec_file(tmp_path, text)))

    assert first.replay_factors == (1.0, 2.0, 4.0, 8.0)


@pytest.mark.parametrize(
    ("factors", "named"),
    [
        ("[]", "list of numbers from 1"),
        ("[0.5, 2]", "list of numbers from 1"),
        ("[1, -2]", "list of numbers from 1"),
        ('["2"]', "list of numbers from 1"),
        ("[true]", "list of numbers from 1"),
        ("[2, 2]", "twice"),
        ("[2, 2.0]", "twice"),
    ],
)
def test_replay_factors_that_are_none_are_refused(tmp_path, factors, named):
    text = SPEC.replace("[1, 2, 4, 8]", factors)

    with pytest.raises(SpecRefused, match=named):
        read_spec(spec_file(tmp_path, text))


def test_a_configuration_without_the_wrapper_has_no_replay(tmp_path):
    text = SPEC.replace(
        'topology = "cross"\n', 'topology = "cross"\nwrapper = false\n', 1
    )

    with pytest.raises(SpecRefused, match="wrapper = true"):
        configurations(read_spec(spec_file(tmp_path, text)))


def test_the_factors_are_no_default_and_no_flag(tmp_path):
    text = SPEC.replace(
        "repetitions = 2\n", "repetitions = 2\nreplay_factors = [2]\n", 1
    )

    with pytest.raises(SpecRefused, match="replay_factors"):
        read_spec(spec_file(tmp_path, text))
    with pytest.raises(SpecRefused, match="replay_factors"):
        configurations(
            read_spec(spec_file(tmp_path)), overrides={"replay_factors": [2]}
        )


def test_a_configuration_that_a_later_block_lists_again_gets_the_factors_of_both(
    tmp_path,
):
    text = SPEC + '\n[[sweep]]\ntopology = "cross"\nreplay_factors = [3, 4]\n'

    found = configurations(read_spec(spec_file(tmp_path, text)))

    assert len(found) == 3
    assert found[0].replay_factors == (1.0, 2.0, 3.0, 4.0, 8.0)


# A dry run of the sweep lists the replay levels.


def test_a_dry_run_lists_the_replay_levels_and_needs_no_store(tmp_path, capsys):
    code = main([str(spec_file(tmp_path)), "--runs", str(tmp_path / "runs"), "--list"])

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    [first] = [
        line for line in lines if "replay levels of configuration spec 1" in line
    ]
    assert "1 (normal load, no record)" in first
    assert "2 (heavy, no record)" in first
    assert "8 (heavy, no record)" in first
    [third] = [
        line for line in lines if "replay levels of configuration spec 3" in line
    ]
    assert "1 (normal load, no record)" in third
    assert not [
        line for line in lines if "replay levels of configuration spec 2" in line
    ]
    # The levels follow their configuration's line.
    at = next(
        i for i, line in enumerate(lines) if "configuration spec 1 (block 1" in line
    )
    assert "replay levels of configuration spec 1" in lines[at + 1]


def test_a_dry_run_shows_the_record_of_a_level(tmp_path, configured, capsys):
    configured()
    assert call(tmp_path, "--configurations", "1") == 0
    capsys.readouterr()

    main([str(spec_file(tmp_path)), "--runs", str(tmp_path / "runs"), "--list"])

    [first] = [
        line
        for line in capsys.readouterr().out.splitlines()
        if "replay levels of configuration spec 1" in line
    ]
    assert "1 (normal load, completed)" in first
    assert "8 (heavy, completed)" in first


# The sweep replays the first valid UC2 run once per factor, in rising order, after its UC2 runs.


def test_the_sweep_replays_each_configuration_after_its_runs_in_rising_order(
    tmp_path, configured
):
    runs, replays = configured()

    code = call(tmp_path)

    assert code == 0
    assert runs.log == [
        "run 1.1",
        "run 1.2",
        "replay 1 x1",
        "replay 1 x2",
        "replay 1 x4",
        "replay 1 x8",
        "run 2.1",
        "run 2.2",
        "run 3.1",
        "run 3.2",
        "replay 3 x1",
    ]
    # Every level replays the first run of its configuration.
    assert {(number, replayed) for number, _, replayed, _ in replays.made} == {
        (1, "run-1-1"),
        (3, "run-3-1"),
    }


def test_the_replay_takes_the_first_valid_run_of_the_configuration(
    tmp_path, configured
):
    _, replays = configured(FabricatedRuns(invalid={(1, 1)}))

    assert call(tmp_path) == 0

    assert {replayed for number, _, replayed, _ in replays.made if number == 1} == {
        "run-1-2"
    }


def test_the_first_valid_run_is_a_uc2_run_with_the_wrapper_that_counts(tmp_path):
    found = configurations(read_spec(spec_file(tmp_path)))
    key = found[0].key
    folder = tmp_path / "runs"
    for fabricated in (
        {
            **record("a-unwrapped", run_kind="uc2-unwrapped"),
            "sweep": {"key": key, "repetition": 1},
        },
        {**record("b-invalid", valid=False), "sweep": {"key": key, "repetition": 1}},
        {
            **record("c-failed", outcome="failed: boom"),
            "sweep": {"key": key, "repetition": 1},
        },
        {**record("d-late"), "sweep": {"key": key, "repetition": 3}},
        {**record("e-first"), "sweep": {"key": key, "repetition": 2}},
        {**record("f-other"), "sweep": {"key": "0123456789abcdef", "repetition": 1}},
        {
            **record("g-baseline", run_kind="baseline"),
            "sweep": {"key": key, "repetition": 1},
        },
    ):
        write(folder, fabricated)

    first = first_valid_run(read_summaries(folder), key)

    assert first is not None and first.run_id == "e-first"
    assert first_valid_run(read_summaries(folder), "no-such-key") is None


def test_each_level_has_a_key_of_its_own_in_its_record(tmp_path, configured):
    _, _ = configured()

    assert call(tmp_path, "--configurations", "1") == 0

    found = configurations(read_spec(spec_file(tmp_path)))
    by_run = {
        r["run_id"]: r["sweep"]
        for r in (
            json.loads(path.read_text())
            for path in sorted((tmp_path / "runs").glob("*/record.json"))
        )
    }
    levels = [by_run[f"replay-1-x{f}"] for f in (1, 2, 4, 8)]
    assert [level["key"] for level in levels] == [
        level_key(found[0].key, f) for f in (1.0, 2.0, 4.0, 8.0)
    ]
    assert len({level["key"] for level in levels}) == 4
    assert {level["replayed_key"] for level in levels} == {found[0].key}
    assert [level["replay_factor"] for level in levels] == [1.0, 2.0, 4.0, 8.0]
    assert {level["repetition"] for level in levels} == {1}


def test_a_level_with_a_record_is_skipped_and_the_sweep_goes_on_where_it_stopped(
    tmp_path, configured
):
    runs, _ = configured()
    assert call(tmp_path) == 0
    made = len(runs.log)

    assert call(tmp_path) == 0

    assert len(runs.log) == made


def test_a_selection_of_a_configuration_replays_its_levels_alone_once_its_runs_have_records(
    tmp_path, configured
):
    runs, _ = configured()
    assert call(tmp_path, "--configurations", "1") == 0
    for path in (tmp_path / "runs").glob("replay-*"):
        for child in path.iterdir():
            child.unlink()
        path.rmdir()
    runs.log.clear()

    assert call(tmp_path, "--configurations", "1") == 0

    assert runs.log == ["replay 1 x1", "replay 1 x2", "replay 1 x4", "replay 1 x8"]


def test_a_configuration_with_no_valid_run_to_replay_leaves_its_levels_and_says_so(
    tmp_path, configured, capsys
):
    configured(FabricatedRuns(invalid={(1, 1), (1, 2)}))

    code = call(tmp_path)

    # The sweep went on with the other configurations, and exits 1: not every level has a record.
    assert code == 1
    out = capsys.readouterr().out
    # The run line names the configuration with its series.
    assert "configuration spec 1 has no valid run of use case 2 to replay" in out
    assert "replay 1 x1" not in out


# A level is broken by the rule, and the sweep stops the replay at the first broken level.


def test_the_sweep_stops_the_replay_of_a_configuration_at_its_first_broken_level(
    tmp_path, configured, capsys
):
    runs, replays = configured(replays=FabricatedReplays(broken_from=4))

    code = call(tmp_path)

    assert code == 0
    assert [n for n, f, _, _ in replays.made if n == 1] == [1, 1, 1]
    assert [f for n, f, _, _ in replays.made if n == 1] == [1.0, 2.0, 4.0]
    # The sweep goes on with the runs after the replay.
    assert "run 2.1" in runs.log and "run 3.2" in runs.log
    out = capsys.readouterr().out
    [ended] = [line for line in out.splitlines() if "is broken at the factor 4" in line]
    assert "heap refusal" in ended
    assert "its 1 higher levels are not run" in ended


def test_a_replay_that_ended_in_an_earlier_call_sends_no_higher_factor(
    tmp_path, configured
):
    _, replays = configured(replays=FabricatedReplays(broken_from=2))
    assert call(tmp_path) == 0
    assert [f for n, f, _, _ in replays.made if n == 1] == [1.0, 2.0]

    # A later call has nothing of the replay of configuration 1 to make, and needs no approval for
    # its higher levels.
    code = call(tmp_path, approve=False)

    assert code == 0
    assert [f for n, f, _, _ in replays.made if n == 1] == [1.0, 2.0]


def test_the_broken_level_is_named_in_the_record_of_the_run_and_the_table_of_ends(
    tmp_path, configured
):
    configured(replays=FabricatedReplays(broken_from=2))
    assert call(tmp_path) == 0

    found = configurations(read_spec(spec_file(tmp_path)))
    summaries = read_summaries(tmp_path / "runs")

    assert replay_ends(found, summaries) == {found[0].key: 2.0}
    [broken] = [s for s in summaries if s.key == level_key(found[0].key, 2.0)]
    assert broken.replay_level is not None
    assert broken.replay_level["broken"] is True
    assert broken.replay_level["signals"][0]["signal"] == "heap refusal"


def test_a_level_that_failed_ends_the_replay_and_the_sweep_goes_on(
    tmp_path, configured, capsys
):
    """A failed level counts toward the 3 failed runs in a row, and the sweep goes on (jms_kapps
    112: until 2026-10-14 it stopped the sweep)."""
    runs, replays = configured(replays=FabricatedReplays(failing=2.0))

    code = call(tmp_path)

    assert code == 1
    assert [f for n, f, _, _ in replays.made if n == 1] == [1.0, 2.0]
    assert "run 2.1" in runs.log
    assert "no higher factor of its replay is sent" in capsys.readouterr().out
    # The next call skips the failed level and sends no higher factor of the configuration.
    found = configurations(read_spec(spec_file(tmp_path)))
    assert replay_ends(found, read_summaries(tmp_path / "runs")) == {found[0].key: 2.0}
    assert call(tmp_path) == 0
    assert [f for n, f, _, _ in replays.made if n == 1] == [1.0, 2.0]


def test_a_level_that_a_stop_signal_ended_has_not_ended_the_replay(tmp_path):
    found = configurations(read_spec(spec_file(tmp_path)))
    key = level_key(found[0].key, 1.0)
    interrupted = record("replay-x1", run_kind="baseline", outcome="interrupted")
    interrupted["sweep"] = {"key": key, "repetition": 1}
    write(tmp_path / "runs", interrupted)

    assert replay_ends(found, read_summaries(tmp_path / "runs")) == {}


# Heavy: every factor above 1 needs an approval that covers the spec.


def test_a_factor_above_one_is_skipped_and_listed_without_an_approval(
    tmp_path, configured, capsys
):
    """Skipped at its start, with its higher factors, and the sweep goes on (jms_kapps 112: until
    2026-10-14 the call was refused before its first run)."""
    runs, replays = configured()

    code = call(tmp_path, approve=False)

    assert code == 0
    assert len(runs.made) == 6
    assert [(n, f) for n, f, _, _ in replays.made] == [(1, 1.0), (3, 1.0)]
    out = capsys.readouterr().out
    assert "the replay of configuration 1 at the factor 2: no approval" in out
    assert "the replay of configuration 1 at the factor 4" in out
    assert "any person may approve" in out


def test_a_selection_of_factors_of_one_needs_no_approval(tmp_path, configured):
    _, replays = configured()

    code = call(tmp_path, "--configurations", "2,3", approve=False)

    assert code == 0
    assert [(n, f, a) for n, f, _, a in replays.made] == [(3, 1.0, None)]


def test_every_level_above_one_is_given_the_covered_record_and_a_factor_of_one_is_not(
    tmp_path, configured
):
    _, replays = configured()

    assert call(tmp_path, "--configurations", "1") == 0

    given = {factor: covered for _, factor, _, covered in replays.made}
    assert given[1.0] is None
    for factor in (2.0, 4.0, 8.0):
        covered = given[factor]
        assert isinstance(covered, Covered)
        assert (
            covered.configuration_key
            == configurations(read_spec(spec_file(tmp_path)))[0].key
        )


def test_an_approval_of_another_spec_covers_no_level_above_one(
    tmp_path, configured, capsys
):
    other = tmp_path / "other"
    other.mkdir()
    spec_path = spec_file(tmp_path)
    approval = approval_file(
        other, spec_file(other, SPEC + "# another spec\n"), name="approval.toml"
    )
    runs, replays = configured()

    code = main(
        [
            str(spec_path),
            "--runs",
            str(tmp_path / "runs"),
            "--heavy-run-approval",
            str(approval),
        ]
    )

    assert code == 0
    assert "it is another spec" in capsys.readouterr().out
    assert len(runs.made) == 6
    assert [(n, f) for n, f, _, _ in replays.made] == [(1, 1.0), (3, 1.0)]


def test_flag_overrides_are_not_covered_by_an_approval(tmp_path, configured, capsys):
    _, replays = configured()

    code = call(tmp_path, "--set", "shacl=false")

    assert code == 0
    assert "--set overrides" in capsys.readouterr().out
    assert [(n, f) for n, f, _, _ in replays.made] == [(1, 1.0), (3, 1.0)]


def test_a_replay_whose_approval_ended_is_skipped_and_the_sweep_goes_on(
    tmp_path, configured, capsys
):
    """jms_kapps 112: until 2026-10-14 the sweep stopped before the level."""
    runs, _ = configured(replays=FabricatedReplays(refusing=True))

    code = call(tmp_path)

    assert code == 0
    out = capsys.readouterr().out
    assert "the replay of configuration 1 at the factor 1: not covered" in out
    assert "outside the period of the approval" in out
    assert "run 2.1" in runs.log


# Under the precision rule the rounds plan the levels as they plan a ramp's: in round 1, and in a
# later round only while the replay has not ended (jms_kapps 111; jms_kapps 114).

PRECISION_SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false

[precision]

# 1: under the rule.
[[sweep]]
topology = "cross"
replay_factors = [1, 2]

# 2: heavy, so with fixed repetitions.
[[sweep]]
topology = "pair"
heavy = true
repetitions = 1
replay_factors = [1, 2]
"""


def test_under_the_precision_rule_the_rounds_plan_the_levels_as_they_plan_a_ramp(
    tmp_path,
):
    found = configurations(read_spec(spec_file(tmp_path, PRECISION_SPEC)))
    ruled, heavy = found
    assert ruled.ruled and not heavy.ruled

    first = sweep.replays_due(
        found, [], [(ruled, 1), (heavy, 1)], configuration_numbers=None
    )
    # Round 2: the first valid run of each was replayed at both factors, so both replays ended.
    made = [
        summary_of(r)
        for r in (
            {**record("run-1", ruled), "sweep": {"key": ruled.key, "repetition": 1}},
            {**record("run-2", heavy), "sweep": {"key": heavy.key, "repetition": 1}},
            *(
                {
                    **record(f"x{f:g}-{c.number}", run_kind="baseline", replays="r"),
                    "sweep": {"key": level_key(c.key, f), "repetition": 1},
                }
                for c in found
                for f in (1.0, 2.0)
            ),
        )
    ]
    later = sweep.replays_due(found, made, [(ruled, 2)], configuration_numbers=None)
    # A spec of fixed repetitions chooses the replays of the configurations with a run.
    fixed = chosen_replays([(heavy, 1)])

    assert first == {ruled.key: 1, heavy.key: 1}
    assert later == {}
    assert [c.number for c in fixed] == [2]


def test_the_levels_of_a_configuration_under_the_rule_follow_its_run_of_round_one(
    tmp_path, configured, capsys
):
    spec_path = spec_file(tmp_path, PRECISION_SPEC)
    runs, _ = configured()

    code = main(
        [
            str(spec_path),
            "--runs",
            str(tmp_path / "runs"),
            "--heavy-run-approval",
            str(approval_file(tmp_path, spec_path)),
            "--configurations",
            "1",
            "--repetitions",
            "1-3",
        ]
    )

    assert code == 0
    # Round 1 makes repetition 1, then the two levels of the first valid run; rounds 2 and 3 make
    # repetitions 2 and 3.
    assert runs.log == [
        "run 1.1",
        "replay 1 x1",
        "replay 1 x2",
        "run 1.2",
        "run 1.3",
    ]
    capsys.readouterr()
    runs.log.clear()
    # Rounds 4 and 5 make repetitions 4 and 5 and no level again.
    code = main(
        [
            str(spec_path),
            "--runs",
            str(tmp_path / "runs"),
            "--heavy-run-approval",
            str(approval_file(tmp_path, spec_path)),
            "--configurations",
            "1",
            "--repetitions",
            "4-5",
        ]
    )
    assert code == 0
    assert runs.log == ["run 1.4", "run 1.5"]
