"""The table keeps the repetitions of a ramp and of a replay apart (jms_kapps 114, Sören's decision
of 2026-10-13 on jms_kapps 85, note 1866296, decision 6): each repetition of a ramp has its own
judgement, compared with the replay's first repetition, and the table shows how many repetitions
each ramp and each replay has. A runs folder made from fabricated records; no store, no run.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
from _evaluation_records import record, request

from jms_kapps.evaluation.replay_levels import level_key
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.summary import summary_of
from jms_kapps.evaluation.table import make_table, markdown

# 1: the UC2 configuration, replayed at 1, 2 and 4. 2-4: a ramp that matches it.
SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false

[precision]

[[sweep]]
topology = "cross"
replay_factors = [1, 2, 4]

[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20, 40]
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the table opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    found = read_spec(path)
    return found, {c.number: c for c in configurations(found)}


def level(
    spec, number: int, repetition: int, *, limited: bool = False, broken: bool = False
) -> dict[str, Any]:
    """Repetition `repetition` of the ramp's level `number`: 70 of 100 offered spawns accepted
    where `limited`, a refusal for want of heap where `broken`."""
    _, by_number = spec
    rate = by_number[number].values["boxes_per_minute"]
    return record(
        f"ramp-{number}-r{repetition}",
        by_number[number],
        repetition=repetition,
        commit_latencies=[0.1] * 20,
        requests=[request(0.01)] * 100,
        completed_writes_per_minute=10.0 * rate,
        spawns_accepted=70 if limited else 100,
        spawns_refused=30 if limited else 0,
        heap_refusals_in_window=1 if broken else 0,
    )


def replay(spec, factor: float, repetition: int, *, broken: bool) -> dict[str, Any]:
    """Repetition `repetition` of configuration 1's replay level at `factor`, of its run uc2-1."""
    _, by_number = spec
    found = record(
        f"replay-x{factor:g}-r{repetition}",
        None,
        run_kind="baseline",
        replays="uc2-1",
        requests=[request(0.01)],
    )
    found["sweep"] = {
        "spec": "spec.toml",
        "configuration": 1,
        "key": level_key(by_number[1].key, factor),
        "replayed_key": by_number[1].key,
        "replay_factor": factor,
        "repetition": repetition,
        "repetitions": 1,
    }
    found["replay_level"] = {
        "factor": factor,
        "offered_per_minute": 100.0 * factor,
        "achieved_per_minute": 40.0 * factor if broken else 100.0 * factor,
        "requests_in_window": 100,
        "answered_in_window": 100,
        "refused_in_window": 0,
        "timed_out_in_window": 0,
        "heap_refusals_in_window": 0,
        "replay_span_seconds": 60.0 / factor,
        "broken": broken,
        "signals": [{"signal": "achieved rate below offered"}] if broken else [],
    }
    return found


def table_of(spec, records: list[dict[str, Any]]) -> dict[str, Any]:
    found, by_number = spec
    return make_table(
        found,
        list(by_number.values()),
        [summary_of(r) for r in records],
        runs=Path("runs"),
        resamples=50,
    )


def runs_of(spec) -> list[dict[str, Any]]:
    """Configuration 1's run; its replay broke at the factor 2 in repetition 1, and at the factor
    4 in repetition 2. The ramp was UC2-limited at 20 in repetition 1, and broke at 20 in
    repetition 2."""
    _, by_number = spec
    uc2 = record(
        "uc2-1",
        by_number[1],
        commit_latencies=[0.1],
        requests=[request(0.1)],
        completed_writes_per_minute=10.0,
    )
    return [
        uc2,
        replay(spec, 1.0, 1, broken=False),
        replay(spec, 2.0, 1, broken=True),
        replay(spec, 1.0, 2, broken=False),
        replay(spec, 2.0, 2, broken=False),
        replay(spec, 4.0, 2, broken=True),
        level(spec, 2, 1),
        level(spec, 3, 1, limited=True),
        level(spec, 4, 1, limited=True),
        level(spec, 2, 2),
        level(spec, 3, 2, broken=True),
    ]


def test_each_repetition_of_a_ramp_has_its_own_judgement_compared_with_the_replay_s_first(
    spec,
):
    made = table_of(spec, runs_of(spec))

    ramps = [r for r in made["ramps"] if r["block"] == 2]
    assert [(r["repetition"], r["repetitions"]) for r in ramps] == [(1, 2), (2, 2)]
    first, second = ramps
    assert first["judgement"]["text"] == "UC2-limited at 20 boxes per minute"
    assert [level["runs"] for level in first["levels"]] == [
        ["ramp-2-r1"],
        ["ramp-3-r1"],
        ["ramp-4-r1"],
    ]
    assert second["judgement"]["text"] == "broken at 20 boxes per minute (heap refusal)"
    assert [level["runs"] for level in second["levels"]] == [
        ["ramp-2-r2"],
        ["ramp-3-r2"],
    ]
    # Both compare with the replay's first repetition, which broke at the factor 2.
    for ramp in ramps:
        assert ramp["comparison"]["replay"]["factor"] == 2
        assert ramp["comparison"]["replay"]["text"] == "broken at factor 2"


def test_the_replay_levels_keep_their_repetitions_apart_and_say_how_many(spec):
    made = table_of(spec, runs_of(spec))

    [row] = [r for r in made["rows"] if r["configurations"] == [1]]
    assert row["replay_repetitions"] == 2
    assert [
        (lv["repetition"], lv["factor"], lv["broken"]) for lv in row["replay_levels"]
    ] == [
        (1, 1.0, False),
        (1, 2.0, True),
        (2, 1.0, False),
        (2, 2.0, False),
        (2, 4.0, True),
    ]


def test_the_markdown_shows_each_repetition_of_a_ramp_and_of_a_replay(spec):
    shown = markdown(table_of(spec, runs_of(spec)))

    assert "### The ramp of block 2, repetition 1 of 2" in shown
    assert "### The ramp of block 2, repetition 2 of 2" in shown
    second = shown.split("### The ramp of block 2, repetition 2 of 2")[1]
    assert "Judgement: broken at 20 boxes per minute (heap refusal)." in second
    assert "Replay of configuration spec 1: broken at factor 2" in second
    section = shown.split("## Replay levels")[1].split("\n## ")[0]
    assert "Repetitions of each replay: configuration spec 1, 2." in section
    assert "| Configurations | Repetition | Factor | Run |" in section
    assert "| spec 1 | 2 | 4 | replay-x4-r2 |" in section


def test_the_pooled_cells_show_the_first_repetition_alone(spec):
    """Sören's ruling of 2026-10-14 on jms_kapps 114: the `baseline` and `baseline xF` cells and
    a ramp level's row cells show repetition 1, the one the ramp comparison uses; the later
    repetitions appear apart in the sections on ramps and replay levels."""
    made = table_of(spec, runs_of(spec))

    rows = {tuple(r["configurations"]): r for r in made["rows"]}
    replayed = rows[(1,)]
    assert replayed["run_kinds"]["baseline"]["runs"] == ["replay-x1-r1"]
    assert replayed["replays"]["2"]["runs"] == ["replay-x2-r1"]
    assert "4" not in replayed["replays"]
    assert rows[(2,)]["run_kinds"]["uc2"]["runs"] == ["ramp-2-r1"]
    assert rows[(3,)]["run_kinds"]["uc2"]["runs"] == ["ramp-3-r1"]
    # Repetition 2 still appears apart.
    assert [lv["run"] for lv in replayed["replay_levels"] if lv["repetition"] == 2] == [
        "replay-x1-r2",
        "replay-x2-r2",
        "replay-x4-r2",
    ]
    second = [r for r in made["ramps"] if r["repetition"] == 2]
    assert [level["runs"] for level in second[0]["levels"]] == [
        ["ramp-2-r2"],
        ["ramp-3-r2"],
    ]
