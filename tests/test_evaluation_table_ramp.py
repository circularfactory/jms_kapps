"""The table gives each ramp its judgement and its comparison with the replay of the matching UC2
configuration (jms_kapps 99, interim names). A runs folder made from fabricated levels; no store,
no run.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
from _evaluation_records import record, request, write

from jms_kapps.evaluation import table
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.summary import summary_of
from jms_kapps.evaluation.table import make_table, markdown

# 1: the UC2 configuration, replayed at 1, 2, 4 and 8. 2-4: a ramp with SHACL on, so
# configuration 1 is its replay. 5-7: a ramp with SHACL off. 8-9: a ramp with history collection
# on, which no UC2 configuration has: no replay.
SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 1

[[sweep]]
topology = "cross"
replay_factors = [1, 2, 4, 8]

[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20, 40]

[[sweep]]
ramp = true
topology = "cross"
shacl = false
boxes_per_minute = [10, 20, 40]

[[sweep]]
ramp = true
topology = "cross"
collect_history = true
boxes_per_minute = [10, 20]
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


def level(spec, number: int, run_id: str, **numbers: Any) -> dict[str, Any]:
    """A level of a ramp: a healthy one unless `numbers` say otherwise; 100 spawns offered and
    accepted."""
    _, by_number = spec
    rate = by_number[number].values["boxes_per_minute"]
    values: dict[str, Any] = {
        "commit_latencies": [0.1] * 20,
        "requests": [request(0.01)] * 100,
        "completed_writes_per_minute": 10.0 * rate,
        "spawns_accepted": 100,
    }
    return record(run_id, by_number[number], **(values | numbers))


def limited(spec, number: int, run_id: str, **numbers: Any) -> dict[str, Any]:
    """A level at which 70 of 100 offered spawns were accepted."""
    return level(spec, number, run_id, spawns_accepted=70, spawns_refused=30, **numbers)


def replay(
    spec, run_id: str, factor: float, *, number: int = 1, broken: bool = False
) -> dict[str, Any]:
    """The baseline of configuration `number`'s run `uc2-<number>` at `factor`: it offers 100
    store requests per minute at factor 1."""
    found = record(
        run_id,
        None,
        run_kind="baseline",
        replays=f"uc2-{number}",
        requests=[request(0.01)],
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
        "broken": broken,
        "signals": [{"signal": "achieved rate below offered"}] if broken else [],
    }
    return found


def uc2(spec, number: int) -> dict[str, Any]:
    _, by_number = spec
    return record(
        f"uc2-{number}",
        by_number[number],
        commit_latencies=[0.1],
        requests=[request(0.1)],
        completed_writes_per_minute=10.0,
    )


def table_of(spec, records: list[dict[str, Any]]) -> dict[str, Any]:
    found, by_number = spec
    return make_table(
        found,
        list(by_number.values()),
        [summary_of(r) for r in records],
        runs=Path("runs"),
        resamples=100,
    )


def ramp_of(made: dict[str, Any], block: int) -> dict[str, Any]:
    [found] = [r for r in made["ramps"] if r["block"] == block]
    return found


def replays(spec, number: int = 1, *, broken_at: float | None) -> list[dict[str, Any]]:
    """The run of configuration `number` with its baselines at 1, 2, 4 and 8, broken from
    `broken_at` on (None: never)."""
    return [
        uc2(spec, number),
        *(
            replay(
                spec,
                f"replay-{number}-{factor:g}",
                factor,
                number=number,
                broken=broken_at is not None and factor >= broken_at,
            )
            for factor in (1.0, 2.0, 4.0, 8.0)
        ),
    ]


def test_a_ramp_is_uc2_limited_where_its_accepted_share_falls_below_90_percent_and_goes_on(
    spec,
):
    levels = [
        level(spec, 2, "r-10"),
        # The plateau that a UC2-limited level shows (100 -> 104 writes is a rise of 4 %).
        limited(spec, 3, "r-20", completed_writes_per_minute=104.0),
        limited(spec, 4, "r-40", completed_writes_per_minute=106.0),
    ]

    ramp = ramp_of(table_of(spec, levels), 2)

    assert ramp["judgement"]["kind"] == "UC2-limited"
    assert ramp["judgement"]["level"] == 20
    assert ramp["judgement"]["text"] == "UC2-limited at 20 boxes per minute"
    assert ramp["uc2_limited_at"] == 20
    assert not ramp["ended"]
    assert [lv["uc2_limited"] for lv in ramp["levels"]] == [False, True, True]
    assert [s["signal"] for s in ramp["levels"][1]["signals"]] == ["plateau"]
    assert ramp["levels"][1]["numbers"]["spawns_accepted_share"] == pytest.approx(0.7)
    assert ramp["levels"][1]["numbers"]["spawns_offered"] == 100


def test_a_ramp_that_broke_before_any_uc2_limited_level_is_judged_broken(spec):
    levels = [
        level(spec, 2, "r-10"),
        level(spec, 3, "r-20", heap_refusals_in_window=1),
        limited(spec, 4, "r-40"),
    ]

    ramp = ramp_of(table_of(spec, levels), 2)

    assert ramp["judgement"]["kind"] == "broken"
    assert ramp["judgement"]["text"] == "broken at 20 boxes per minute (heap refusal)"
    assert ramp["scale"] == 10


def test_a_ramp_with_only_a_plateau_cannot_be_told(spec):
    levels = [
        level(spec, 2, "r-10"),
        level(spec, 3, "r-20", completed_writes_per_minute=101.0),
        level(spec, 4, "r-40", completed_writes_per_minute=102.0),
    ]

    ramp = ramp_of(table_of(spec, levels), 2)

    assert ramp["judgement"]["kind"] == "cannot tell"
    assert ramp["judgement"]["text"] == "cannot tell"
    assert not ramp["ended"]


def test_a_ramp_limited_and_broken_at_the_same_level_cannot_be_told(spec):
    levels = [
        level(spec, 2, "r-10"),
        limited(spec, 3, "r-20", heap_refusals_in_window=1),
    ]

    assert ramp_of(table_of(spec, levels), 2)["judgement"]["kind"] == "cannot tell"


def test_the_comparison_puts_the_offered_rate_at_the_replay_s_first_broken_level_beside_uc2s(
    spec,
):
    """UC2 made 100 store requests in a window of 5 minutes: 20 a minute. The replay broke at
    factor 4, where it offered 400 a minute."""
    levels = [
        level(spec, 2, "r-10"),
        limited(spec, 3, "r-20", requests=[request(0.01)] * 150),
    ]

    ramp = ramp_of(table_of(spec, [*levels, *replays(spec, broken_at=4.0)]), 2)

    compared = ramp["comparison"]
    assert compared["uc2_limited_at"] == 20
    assert compared["uc2_store_requests_per_minute"] == pytest.approx(30.0)
    assert compared["replay"]["outcome"] == "broken"
    assert compared["replay"]["configuration"] == 1
    assert compared["replay"]["factor"] == 4
    assert compared["replay"]["offered_store_requests_per_minute"] == pytest.approx(
        400.0
    )


def test_a_replay_that_broke_at_no_level_says_not_broken_up_to_the_highest_factor(spec):
    levels = [level(spec, 2, "r-10"), limited(spec, 3, "r-20")]

    made = table_of(spec, [*levels, *replays(spec, broken_at=None)])

    replay_side = ramp_of(made, 2)["comparison"]["replay"]
    assert replay_side["outcome"] == "not broken"
    assert replay_side["text"] == "not broken up to factor 8"
    assert replay_side["offered_store_requests_per_minute"] is None
    assert "Replay of configuration spec 1: not broken up to factor 8." in markdown(
        made
    )


def test_a_ramp_whose_configuration_has_no_replay_says_no_replay(spec):
    """Block 4's ramp (8, 9) has history collection on, which no UC2 configuration of the spec has."""
    levels = [level(spec, 8, "h-10"), limited(spec, 9, "h-20")]

    made = table_of(spec, [*levels, *replays(spec, broken_at=2.0)])

    replay_side = ramp_of(made, 4)["comparison"]["replay"]
    assert replay_side["outcome"] == "no replay"
    assert replay_side["text"] == "no replay"
    assert replay_side["configuration"] is None
    assert "Replay: no replay." in markdown(made)


def test_a_matching_configuration_with_no_baseline_says_no_replay(spec):
    levels = [level(spec, 2, "r-10"), limited(spec, 3, "r-20")]

    made = table_of(spec, [*levels, uc2(spec, 1)])

    assert ramp_of(made, 2)["comparison"]["replay"]["outcome"] == "no replay"


def test_the_ramp_matches_on_shacl_so_the_other_configuration_s_replay_is_not_its_own(
    spec,
):
    """Block 3's ramp has SHACL off. Configuration 1 has SHACL on, replays broke there, and
    configuration 2 (no SHACL, not in this spec's first block) does not exist: no replay."""
    levels = [level(spec, 5, "s-10"), limited(spec, 6, "s-20")]

    made = table_of(spec, [*levels, *replays(spec, broken_at=2.0)])

    assert ramp_of(made, 3)["comparison"]["replay"]["outcome"] == "no replay"
    assert ramp_of(made, 3)["judgement"]["kind"] == "UC2-limited"


def test_the_first_of_several_matching_configurations_in_spec_order_counts(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text(
        SPEC.replace(
            '[[sweep]]\nramp = true\ntopology = "cross"\nboxes_per_minute = [10, 20, 40]',
            '[[sweep]]\ntopology = "cross"\nboxes_per_minute = 60\nreplay_factors = [1, 2, 4, 8]'
            '\n\n[[sweep]]\nramp = true\ntopology = "cross"\nboxes_per_minute = [10, 20, 40]',
            1,
        ),
        encoding="utf-8",
    )
    found = read_spec(path)
    spec = (found, {c.number: c for c in configurations(found)})
    # 1 and 2 both match the ramp (3-5); the replay of 1 broke at 2, that of 2 at 8.
    first = replays(spec, 1, broken_at=2.0)
    second = replays(spec, 2, broken_at=8.0)
    for found_record in second:
        found_record["run_id"] = "b-" + found_record["run_id"]
        if found_record["replayed_run"]:
            found_record["replayed_run"] = {"run_id": "uc2-2"}
    levels = [level(spec, 3, "r-10"), limited(spec, 4, "r-20")]

    made = table_of(spec, [*levels, *first, *second])

    replay_side = ramp_of(made, 3)["comparison"]["replay"]
    assert replay_side["configuration"] == 1
    assert replay_side["factor"] == 2


def test_the_markdown_gives_the_judgement_the_comparison_and_the_spawn_columns(spec):
    levels = [level(spec, 2, "r-10"), limited(spec, 3, "r-20")]

    shown = markdown(table_of(spec, [*levels, *replays(spec, broken_at=4.0)]))

    assert "Judgement: UC2-limited at 20 boxes per minute." in shown
    assert (
        "UC2 achieved 20 store requests per minute at its first UC2-limited level, "
        "20 boxes per minute. Replay of configuration spec 1: broken at factor 4, offered 400 "
        "store requests per minute."
    ) in shown
    assert (
        "| Offered spawns | Accepted spawns | Accepted share | UC2-limited |" in shown
    )
    assert "| 100 | 70 | 70.00% | yes |" in shown
    assert "| 100 | 100 | 100.00% | no |" in shown


def test_the_command_writes_the_judgement_into_table_json(spec, tmp_path, capsys):
    import json

    runs = tmp_path / "runs"
    runs.mkdir()
    write(
        runs,
        level(spec, 2, "r-10"),
        limited(spec, 3, "r-20"),
        *replays(spec, broken_at=4.0),
    )

    code = table.main(
        [str(tmp_path / "spec.toml"), "--runs", str(runs), "--resamples", "50"]
    )

    assert code == 0
    [ramp] = [
        r
        for r in json.loads((runs / "table.json").read_text())["ramps"]
        if r["block"] == 2
    ]
    assert ramp["judgement"]["text"] == "UC2-limited at 20 boxes per minute"
    assert ramp["comparison"]["replay"]["factor"] == 4
