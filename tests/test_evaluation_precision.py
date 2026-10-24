"""The precision rule: a configuration runs until the bootstrap 95 % interval of its
pooled commit p95 is within 10 % of it and the IQR of its throughput is at most 10 % of the median,
with at least 10 valid runs and at most 20 runs made. The rounds plan the runs:
round k makes repetition k of each open configuration.

The seam is the round planner over run summaries (`rounds`), tested with fake summaries made from
fabricated records; no store, no run.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from _evaluation_records import record

from jms_kapps.evaluation.precision import judge
from jms_kapps.evaluation.rounds import Round, is_open, next_round, verdict_of
from jms_kapps.evaluation.spec import (
    PrecisionRule,
    SpecConfiguration,
    SpecRefused,
    configurations,
    read_spec,
)
from jms_kapps.evaluation.summary import RunSummary, summary_of
from jms_kapps.evaluation.table import make_table, markdown, rule_numbers

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false

[precision]

# 1 and 2: a calibration pair, with the wrapper and without it.
[[sweep]]
topology = "cross"
wrapper = [true, false]

# 3: another configuration.
[[sweep]]
topology = "pair"
"""

RULE = PrecisionRule()


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    found = read_spec(path)
    return found, {c.number: c for c in configurations(found)}


def run(
    configuration: SpecConfiguration,
    repetition: int,
    *,
    valid: bool = True,
    outcome: str = "completed",
    latency: float = 1.0,
    throughput: float = 30.0,
    pass_number: int | None = None,
) -> RunSummary:
    """The summary of one run of `configuration` with the numbers given: a tight record unless a
    test says otherwise."""
    wrapper = configuration.values["wrapper"]
    return summary_of(
        record(
            f"run-{configuration.number}-{repetition}-{outcome}",
            configuration,
            repetition=repetition,
            run_kind="uc2" if wrapper else "uc2-unwrapped",
            outcome=outcome,
            valid=valid,
            commit_latencies=[latency] * 20,
            transports_ended_per_minute=throughput,
            pass_number=pass_number,
        )
    )


def runs(
    configuration: SpecConfiguration, count: int, **numbers: Any
) -> list[RunSummary]:
    return [run(configuration, r, **numbers) for r in range(1, count + 1)]


def planned(
    spec_and_numbers, summaries: Sequence[RunSummary], **options: Any
) -> Round | None:
    found, by_number = spec_and_numbers
    return next_round(list(by_number.values()), found.precision, summaries, **options)


def shown(round_: Round | None) -> tuple[int, list[tuple[int, int]]] | None:
    """A round as its number and its runs, each by configuration number and repetition."""
    return (
        None
        if round_ is None
        else (round_.number, [(c.number, r) for c, r in round_.runs])
    )


# --- the rounds -----------------------------------------------------------------------------


def test_round_1_makes_repetition_1_of_every_configuration_in_the_spec_s_order(spec):
    assert shown(planned(spec, [])) == (1, [(1, 1), (2, 1), (3, 1)])


def test_round_k_makes_repetition_k_of_each_open_configuration(spec):
    _, c = spec
    five = [r for n in (1, 2, 3) for r in runs(c[n], 5)]
    assert shown(planned(spec, five)) == (6, [(1, 6), (2, 6), (3, 6)])
    # 10 runs of each, but the throughput of configuration 3 is far apart: only it is open.
    ten = [r for n in (1, 2) for r in runs(c[n], 10)] + [
        run(c[3], r, throughput=10.0 if r % 2 else 50.0) for r in range(1, 11)
    ]
    assert shown(planned(spec, ten)) == (11, [(3, 11)])


def test_the_rule_is_met_at_10_runs_so_no_round_is_left(spec):
    _, c = spec
    summaries = [r for n in (1, 2, 3) for r in runs(c[n], 10)]

    assert planned(spec, summaries) is None
    verdict = verdict_of(c[1], summaries, RULE)
    assert verdict.met and verdict.status == "met" and verdict.made == 10
    assert not is_open(c[1], summaries, RULE)


def test_the_rule_is_met_after_later_rounds(spec):
    """Eight valid runs of ten, two invalid: rounds 11 and 12 each add a valid run, and the rule
    holds at 12 runs made, 10 valid."""
    _, c = spec
    one = c[1]
    summaries = [
        run(one, r, valid=r > 2, latency=9.0 if r <= 2 else 1.0) for r in range(1, 11)
    ]
    assert not verdict_of(one, summaries, RULE).met
    assert is_open(one, summaries, RULE)
    assert shown(planned(spec, summaries, configuration_numbers=[1])) == (11, [(1, 11)])

    summaries.append(run(one, 11))
    assert shown(planned(spec, summaries, configuration_numbers=[1])) == (12, [(1, 12)])

    summaries.append(run(one, 12))
    assert verdict_of(one, summaries, RULE).met
    assert planned(spec, summaries, configuration_numbers=[1]) is None


def test_a_configuration_that_never_meets_the_rule_stops_at_20_runs_and_is_marked(spec):
    _, c = spec
    noisy = [run(c[3], r, throughput=10.0 if r % 2 else 50.0) for r in range(1, 20)]
    assert shown(planned(spec, noisy, configuration_numbers=[3])) == (20, [(3, 20)])

    noisy.append(run(c[3], 20, throughput=10.0))
    assert planned(spec, noisy, configuration_numbers=[3]) is None
    assert not is_open(c[3], noisy, RULE)
    verdict = verdict_of(c[3], noisy, RULE)
    assert not verdict.met
    assert verdict.status == "precision not reached (20 runs)"


def test_invalid_runs_do_not_count_toward_the_rule_but_count_toward_the_20(spec):
    _, c = spec
    # Ten valid, tight runs and two invalid, wild ones: the invalid ones change no number.
    mixed = runs(c[1], 10) + [
        run(c[1], r, valid=False, latency=90.0, throughput=1.0) for r in (11, 12)
    ]
    verdict = verdict_of(c[1], mixed, RULE)
    assert verdict.met and verdict.valid == 10 and verdict.made == 12

    # Nine valid runs of 20 made: the maximum is reached, the rule is not met.
    capped = [run(c[1], r, valid=r % 2 == 0 and r > 2) for r in range(1, 21)]
    assert sum(1 for s in capped if s.counts) == 9
    verdict = verdict_of(c[1], capped, RULE)
    assert not verdict.met and verdict.valid == 9
    assert verdict.status == "precision not reached (20 runs)"
    assert planned(spec, capped, configuration_numbers=[1]) is None


def test_a_configuration_without_the_wrapper_is_judged_on_the_throughput_alone(spec):
    _, c = spec
    unwrapped = c[2]
    # Its records hold no commit latency.
    bare = [
        summary_of(
            record(
                f"bare-{r}",
                unwrapped,
                repetition=r,
                run_kind="uc2-unwrapped",
                transports_ended_per_minute=30.0,
            )
        )
        for r in range(1, 11)
    ]
    assert all(s.commit_latencies is None for s in bare)
    assert verdict_of(unwrapped, bare, RULE).met

    # With the wrapper the same lack is unmet: no commit p95.
    wrapped = [
        summary_of(
            record(
                f"wrapped-{r}",
                c[1],
                repetition=r,
                transports_ended_per_minute=30.0,
            )
        )
        for r in range(1, 11)
    ]
    assert not verdict_of(c[1], wrapped, RULE).met

    # Its throughput decides.
    wide = [run(unwrapped, r, throughput=10.0 if r % 2 else 50.0) for r in range(1, 11)]
    assert not verdict_of(unwrapped, wide, RULE).met


def test_a_wide_commit_interval_is_unmet_and_a_narrow_one_is_met(spec):
    _, c = spec
    narrow = [run(c[1], r, latency=1.0 + (r % 2) * 0.01) for r in range(1, 11)]
    assert verdict_of(c[1], narrow, RULE).met
    # One repetition in ten is slow: the resamples of whole repetitions swing between
    # a p95 of 1 s and of 3 s.
    wide = [run(c[1], r, latency=3.0 if r == 1 else 1.0) for r in range(1, 11)]
    verdict = verdict_of(c[1], wide, RULE)
    assert not verdict.met
    assert any("commit p95" in why for why in verdict.unmet)


def test_a_spread_exactly_at_the_limit_holds_and_a_zero_median_does_not():
    numbers = {
        "runs": list(range(10)),
        "commit_latency_seconds": None,
        "transports_ended_per_minute": {"median": 100.0, "iqr": 10.0},
    }
    assert judge(numbers, made=10, wrapper=False, rule=RULE).met
    numbers["transports_ended_per_minute"] = {"median": 100.0, "iqr": 10.5}
    assert not judge(numbers, made=10, wrapper=False, rule=RULE).met
    numbers["transports_ended_per_minute"] = {"median": 0.0, "iqr": 0.0}
    assert not judge(numbers, made=10, wrapper=False, rule=RULE).met


# --- the stop at a throughput median of 0 -------------------------------------


def test_a_throughput_median_of_0_at_the_minimum_of_valid_runs_stops_the_configuration(
    spec,
):
    """A configuration whose
    throughput median is 0 after its minimum of valid runs stops there; the next round plans no
    run for it, and it does not run to 20."""
    _, c = spec
    still = runs(c[1], 9, throughput=0.0)
    # Nine valid runs: the minimum is not there yet, so it is open as before.
    assert is_open(c[1], still, RULE)
    assert shown(planned(spec, still, configuration_numbers=[1])) == (10, [(1, 10)])

    still.append(run(c[1], 10, throughput=0.0))
    assert not is_open(c[1], still, RULE)
    assert planned(spec, still, configuration_numbers=[1]) is None
    verdict = verdict_of(c[1], still, RULE)
    assert not verdict.met and verdict.made == 10 and verdict.valid == 10
    assert verdict.status == "stopped: throughput median 0"

    # In a round with the others, it has no run.
    others = [
        run(c[n], r, throughput=10.0 if r % 2 else 50.0)
        for n in (2, 3)
        for r in range(1, 11)
    ]
    assert shown(planned(spec, still + others)) == (11, [(2, 11), (3, 11)])


def test_the_stop_is_checked_again_after_each_later_run(spec):
    """At 10 valid runs half of them ended no transport: the median lies between 0 and 40, so the
    configuration is open. The 11th run ends none either, the median falls to 0, and it stops."""
    _, c = spec
    half = [run(c[3], r, throughput=0.0 if r % 2 else 40.0) for r in range(1, 11)]
    verdict = verdict_of(c[3], half, RULE)
    assert verdict.throughput_median == 20.0
    assert verdict.status == "pending (10 runs)" and is_open(c[3], half, RULE)
    assert shown(planned(spec, half, configuration_numbers=[3])) == (11, [(3, 11)])

    half.append(run(c[3], 11, throughput=0.0))
    assert verdict_of(c[3], half, RULE).status == "stopped: throughput median 0"
    assert planned(spec, half, configuration_numbers=[3]) is None


def test_only_valid_runs_count_toward_the_minimum_of_the_stop(spec):
    """Ten runs made, one of them invalid: nine valid runs, so the configuration is open."""
    _, c = spec
    summaries = [run(c[1], r, throughput=0.0, valid=r != 4) for r in range(1, 11)]
    verdict = verdict_of(c[1], summaries, RULE)
    assert verdict.made == 10 and verdict.valid == 9
    assert verdict.status == "pending (10 runs)" and is_open(c[1], summaries, RULE)

    summaries.append(run(c[1], 11, throughput=0.0))
    assert not is_open(c[1], summaries, RULE)


def test_the_half_of_the_calibration_pair_without_the_wrapper_stops_too(spec):
    """It is judged on the throughput alone, so the same stop applies to it."""
    _, c = spec
    unwrapped = c[2]
    bare = [
        summary_of(
            record(
                f"bare-{r}",
                unwrapped,
                repetition=r,
                run_kind="uc2-unwrapped",
                transports_ended_per_minute=0.0,
            )
        )
        for r in range(1, 11)
    ]

    assert not is_open(unwrapped, bare, RULE)
    assert verdict_of(unwrapped, bare, RULE).status == "stopped: throughput median 0"
    # Its half with the wrapper is open: it has no run yet.
    assert shown(planned(spec, bare, configuration_numbers=[1, 2])) == (1, [(1, 1)])


def test_a_throughput_median_above_0_is_judged_as_before(spec):
    """The stop changes no verdict of a configuration whose throughput median is above 0: a small
    median with a tight spread meets the rule, runs that ended no transport but leave the median
    above 0 keep it open with the spread unmet, and a wide spread runs to 20 and is marked
    "precision not reached"."""
    _, c = spec
    small = runs(c[1], 10, throughput=0.5)
    verdict = verdict_of(c[1], small, RULE)
    assert verdict.met and verdict.status == "met" and not verdict.zero_median

    # Four runs of ten ended no transport: the median is 30, the IQR 30.
    some = [run(c[1], r, throughput=0.0 if r <= 4 else 30.0) for r in range(1, 11)]
    verdict = verdict_of(c[1], some, RULE)
    assert (verdict.throughput_median, verdict.throughput_iqr) == (30.0, 30.0)
    assert not verdict.met and not verdict.zero_median
    assert verdict.unmet == (
        "the throughput has the median 30 and the IQR 30 per minute, beyond 10%",
    )
    assert verdict.status == "pending (10 runs)" and is_open(c[1], some, RULE)

    wide = [run(c[3], r, throughput=10.0 if r % 2 else 50.0) for r in range(1, 21)]
    assert verdict_of(c[3], wide, RULE).status == "precision not reached (20 runs)"
    assert not is_open(c[3], wide, RULE)


def test_the_judgement_stops_at_a_median_of_0_only_with_the_minimum_of_valid_runs():
    numbers: dict[str, Any] = {
        "runs": list(range(10)),
        "commit_latency_seconds": None,
        "transports_ended_per_minute": {"median": 0.0, "iqr": 0.0},
    }
    verdict = judge(numbers, made=10, wrapper=False, rule=RULE)
    assert verdict.zero_median and verdict.stopped
    assert verdict.status == "stopped: throughput median 0"
    assert verdict.record()["status"] == "stopped: throughput median 0"

    numbers["runs"] = list(range(9))
    verdict = judge(numbers, made=12, wrapper=False, rule=RULE)
    assert not verdict.zero_median and not verdict.stopped
    assert verdict.status == "pending (12 runs)"

    # A rule with another minimum stops at that minimum.
    rule = PrecisionRule(minimum_runs=6, maximum_runs=9)
    numbers["runs"] = list(range(6))
    assert judge(numbers, made=6, wrapper=True, rule=rule).zero_median


# --- the table's numbers ----------------------------------------------------------------------


def test_the_rule_s_numbers_equal_the_configurations_table_s_for_the_same_runs(spec):
    found, c = spec
    summaries = [
        run(
            c[1],
            r,
            latency=1.0 + 0.3 * (r % 4),
            throughput=28.0 + (r % 5),
        )
        for r in range(1, 13)
    ]

    table = make_table(found, list(c.values()), summaries, runs=Path("runs"))
    [row] = [r for r in table["rows"] if 1 in r["configurations"]]
    cell = row["run_kinds"]["uc2"]
    numbers = rule_numbers(c[1], summaries)

    assert numbers is not None
    assert numbers["commit_latency_seconds"] == cell["commit_latency_seconds"]
    assert numbers["transports_ended_per_minute"] == cell["transports_ended_per_minute"]
    assert numbers["runs"] == cell["runs"]
    # The verdict is made from the same numbers, with the table's seed and resamples.
    standing = row["precision"]["uc2"]
    verdict = verdict_of(c[1], summaries, RULE)
    assert standing["commit_p95"] == cell["commit_latency_seconds"]["p95"]["value"]
    assert (
        standing["commit_p95_interval"]
        == cell["commit_latency_seconds"]["p95"]["interval"]
    )
    assert (
        standing["throughput_median"] == cell["transports_ended_per_minute"]["median"]
    )
    assert standing["met"] == verdict.met
    assert verdict.commit_p95_interval == tuple(
        cell["commit_latency_seconds"]["p95"]["interval"]
    )


def test_the_table_shows_each_configuration_s_runs_and_rounds_and_marks_a_missed_rule(
    spec,
):
    found, c = spec
    summaries = [
        summary_of(
            record(
                f"r{r}",
                c[3],
                repetition=r,
                commit_latencies=[1.0] * 5,
                transports_ended_per_minute=10.0 if r % 2 else 50.0,
                round_number=r,
            )
        )
        for r in range(1, 21)
    ] + [
        summary_of(
            record(
                f"s{r}",
                c[1],
                repetition=r,
                commit_latencies=[1.0] * 5,
                transports_ended_per_minute=30.0,
                round_number=r,
            )
        )
        for r in range(1, 6)
    ]

    table = make_table(found, list(c.values()), summaries, runs=Path("runs"))
    standing = {tuple(row["configurations"]): row["precision"] for row in table["rows"]}

    assert standing[(3,)]["uc2"]["status"] == "precision not reached (20 runs)"
    assert standing[(3,)]["uc2"]["runs_made"] == 20
    assert standing[(3,)]["uc2"]["rounds"] == list(range(1, 21))
    assert standing[(1, 2)]["uc2"]["status"] == "pending (5 runs)"
    assert standing[(1, 2)]["uc2"]["rounds"] == [1, 2, 3, 4, 5]
    text = markdown(table)
    assert "## Precision rule" in text
    assert (
        "| Configurations | Run kind | Runs made | Valid runs | Rounds | Rule |" in text
    )
    assert "precision not reached (20 runs)" in text
    assert "| spec 1, 2 | uc2 | 5 | 5 | 1, 2, 3, 4, 5 | pending (5 runs) |" in text


def test_the_table_shows_the_pass_of_a_record_made_before_the_rounds_as_it_is(spec):
    """A record made in a pass keeps its pass, and the table shows it in the column of rounds,
    before the rounds of the later records."""
    found, c = spec
    summaries = [
        summary_of(
            record(
                f"p{r}",
                c[1],
                repetition=r,
                commit_latencies=[1.0] * 5,
                transports_ended_per_minute=30.0,
                pass_number=1,
            )
        )
        for r in range(1, 6)
    ] + [
        summary_of(
            record(
                f"r{r}",
                c[1],
                repetition=r,
                commit_latencies=[1.0] * 5,
                transports_ended_per_minute=30.0,
                round_number=r,
            )
        )
        for r in (6, 7)
    ]

    table = make_table(found, list(c.values()), summaries, runs=Path("runs"))
    [row] = [r for r in table["rows"] if 1 in r["configurations"]]

    assert row["precision"]["uc2"]["passes"] == [1]
    assert row["precision"]["uc2"]["rounds"] == [6, 7]
    assert "| spec 1, 2 | uc2 | 7 | 7 | pass 1, 6, 7 | pending (7 runs) |" in markdown(
        table
    )


def test_the_table_marks_a_configuration_stopped_at_a_throughput_median_of_0(spec):
    """Its precision entry and its row in the section "Precision rule" name the configuration,
    its runs and the mark "stopped: throughput median 0", apart from "precision not reached"; a
    configuration that ran to 20 before the stop, as the "after" series may have, has that mark
    too."""
    found, c = spec

    def made(configuration: SpecConfiguration, count: int) -> list[RunSummary]:
        return [
            summary_of(
                record(
                    f"z{configuration.number}-{r}",
                    configuration,
                    repetition=r,
                    commit_latencies=[1.0] * 5,
                    transports_ended_per_minute=0.0,
                    round_number=r,
                )
            )
            for r in range(1, count + 1)
        ]

    summaries = made(c[3], 10) + made(c[1], 20)

    table = make_table(found, list(c.values()), summaries, runs=Path("runs"))
    standing = {tuple(row["configurations"]): row["precision"] for row in table["rows"]}

    assert standing[(3,)]["uc2"]["status"] == "stopped: throughput median 0"
    assert standing[(3,)]["uc2"]["configuration"] == 3
    assert standing[(3,)]["uc2"]["runs_made"] == 10
    assert standing[(3,)]["uc2"]["throughput_median"] == 0.0
    assert standing[(1, 2)]["uc2"]["status"] == "stopped: throughput median 0"
    rule = markdown(table).split("## Precision rule")[1].split("\n## ")[0]
    lines = rule.splitlines()
    assert (
        "| spec 3 | uc2 | 10 | 10 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 | "
        "stopped: throughput median 0 |" in lines
    )
    [row_1_2] = [line for line in lines if line.startswith("| spec 1, 2 | uc2 | 20 |")]
    assert row_1_2.endswith("| stopped: throughput median 0 |")
    section = " ".join(rule.split("\n|")[0].split())
    assert (
        "A configuration whose throughput median is 0 once it has its minimum of valid runs "
        'stops there: "stopped: throughput median 0".' in section
    )


def test_a_table_of_a_spec_without_the_rule_has_no_precision_section(tmp_path):
    path = tmp_path / "spec.toml"
    path.write_text(
        SPEC.replace("[precision]\n", "").replace(
            "heavy = false\n", "heavy = false\nrepetitions = 2\n", 1
        ),
        encoding="utf-8",
    )
    found = read_spec(path)

    table = make_table(found, configurations(found), [], runs=Path("runs"))

    assert all("precision" not in row for row in table["rows"])
    assert "Precision rule" not in markdown(table)


# --- the restart and the narrowed call ---------------------------------------------------------


def test_a_call_narrowed_by_repetition_plans_only_the_rounds_of_those_repetitions(spec):
    _, c = spec
    assert shown(planned(spec, [], repetition_numbers=[1])) == (
        1,
        [(1, 1), (2, 1), (3, 1)],
    )

    # The call made repetition 1 of each. Without the narrowing round 2 is next.
    done = [run(c[n], 1) for n in (1, 2, 3)]
    assert shown(planned(spec, done)) == (2, [(1, 2), (2, 2), (3, 2)])
    # The same narrowed call has no round left.
    assert planned(spec, done, repetition_numbers=[1]) is None


def test_an_interrupted_attempt_does_not_count_toward_the_20_and_is_made_again(spec):
    _, c = spec
    noisy = [run(c[3], r, throughput=10.0 if r % 2 else 50.0) for r in range(1, 20)]
    # The 20th attempt was interrupted: it has a record, but it is no run made.
    noisy.append(run(c[3], 20, outcome="interrupted", throughput=10.0))

    assert verdict_of(c[3], noisy, RULE).made == 19
    assert shown(planned(spec, noisy, configuration_numbers=[3])) == (20, [(3, 20)])
    # An interrupted attempt of round 3 is made again in round 3.
    first = [run(c[1], r) for r in (1, 2, 4, 5)] + [run(c[1], 3, outcome="interrupted")]
    assert shown(planned(spec, first, configuration_numbers=[1])) == (3, [(1, 3)])


def test_a_failed_run_is_a_run_made_so_the_call_goes_on_after_it(spec):
    _, c = spec
    summaries = [run(c[1], r) for r in range(1, 5)] + [
        run(c[1], 5, outcome="failed: the store refused")
    ]
    assert shown(planned(spec, summaries, configuration_numbers=[1])) == (6, [(1, 6)])
    assert verdict_of(c[1], summaries, RULE).made == 5
    assert verdict_of(c[1], summaries, RULE).valid == 4


def test_a_configuration_is_planned_in_the_round_of_its_next_repetition(spec):
    """One with more records than the others is planned in a later round."""
    _, c = spec
    summaries = runs(c[1], 6)
    assert shown(planned(spec, summaries)) == (1, [(2, 1), (3, 1)])
    summaries += runs(c[2], 6) + runs(c[3], 6)
    assert shown(planned(spec, summaries)) == (7, [(1, 7), (2, 7), (3, 7)])


def test_records_made_in_passes_count_as_repetitions_in_the_rounds(spec):
    """The "after" series may have made pass 1 before the rounds: repetitions 1 to 5 of the first
    configurations. The rounds count them, and make the others' first repetitions first."""
    _, c = spec
    passes = [r for n in (1, 2) for r in runs(c[n], 5, pass_number=1)]
    assert shown(planned(spec, passes)) == (1, [(3, 1)])

    later = passes + runs(c[3], 5)
    assert shown(planned(spec, later)) == (6, [(1, 6), (2, 6), (3, 6)])
    assert verdict_of(c[1], later, RULE).made == 5


def test_a_baseline_run_is_none_of_a_configuration_s_runs(spec):
    _, c = spec
    baseline = summary_of(
        record("base-1", c[1], repetition=1, run_kind="baseline", replays="x")
    )
    assert shown(planned(spec, [baseline], configuration_numbers=[1])) == (
        1,
        [(1, 1)],
    )


# --- the spec ----------------------------------------------------------------------------------


def test_a_rule_spec_gives_each_configuration_the_maximum_as_its_repetitions(spec):
    found, c = spec
    assert found.precision == PrecisionRule()
    assert all(config.ruled and config.repetitions == 20 for config in c.values())


def test_the_settings_of_the_rule_come_from_the_spec_outside_the_key(tmp_path):
    base = tmp_path / "spec.toml"
    base.write_text(SPEC, encoding="utf-8")
    other = tmp_path / "other.toml"
    other.write_text(
        SPEC.replace(
            "[precision]\n",
            "[precision]\nlatency_tolerance = 0.2\nthroughput_spread = 0.05\n"
            "minimum_runs = 6\nmaximum_runs = 9\n",
        ),
        encoding="utf-8",
    )

    rule = read_spec(other).precision

    assert rule == PrecisionRule(0.2, 0.05, 6, 9)
    assert [c.key for c in configurations(read_spec(base))] == [
        c.key for c in configurations(read_spec(other))
    ]


def test_the_rounds_end_at_the_maximum_of_the_rule(spec):
    """Round k makes repetition k, so the last round is the maximum's: 9 for a maximum of 9."""
    _, c = spec
    rule = PrecisionRule(minimum_runs=6, maximum_runs=9)
    noisy = [run(c[3], r, throughput=10.0 if r % 2 else 50.0) for r in range(1, 9)]
    assert shown(next_round([c[3]], rule, noisy)) == (9, [(3, 9)])
    noisy.append(run(c[3], 9))
    assert next_round([c[3]], rule, noisy) is None


@pytest.mark.parametrize(
    ("precision", "named"),
    [
        ("latency_tolerance = 0", "latency_tolerance"),
        ("throughput_spread = 'x'", "throughput_spread"),
        ("minimum_runs = 25", "above maximum_runs"),
        ("minimum_runs = 1", "at least two time windows"),
        ("whatever = 1", "does not know"),
    ],
)
def test_a_precision_table_that_is_none_is_refused(tmp_path, precision, named):
    path = tmp_path / "spec.toml"
    path.write_text(SPEC.replace("[precision]\n", f"[precision]\n{precision}\n"))
    with pytest.raises(SpecRefused, match=named):
        read_spec(path)


@pytest.mark.parametrize("sizes", ["[5, 5]", "[3, 3, 4]"])
def test_a_precision_table_that_gives_pass_sizes_is_refused_and_named_the_rounds(
    tmp_path, sizes
):
    path = tmp_path / "spec.toml"
    path.write_text(
        SPEC.replace("[precision]\n", f"[precision]\npass_sizes = {sizes}\n")
    )
    with pytest.raises(SpecRefused) as refused:
        read_spec(path)

    said = str(refused.value)
    assert "pass_sizes" in said and "rounds" in said
    assert "round k makes repetition k" in said and "Remove pass_sizes" in said


def test_a_spec_gives_either_the_rule_or_a_fixed_number_of_repetitions(tmp_path):
    for text in (
        SPEC.replace("heavy = false\n", "heavy = false\nrepetitions = 5\n", 1),
        SPEC.replace('topology = "pair"\n', 'topology = "pair"\nrepetitions = 5\n'),
    ):
        path = tmp_path / "spec.toml"
        path.write_text(text)
        with pytest.raises(SpecRefused, match="fixed repetitions"):
            configurations(read_spec(path))
    path.write_text(SPEC)
    with pytest.raises(SpecRefused, match="fixed repetitions"):
        configurations(read_spec(path), overrides={"repetitions": 3})


def test_a_ramp_and_a_heavy_configuration_run_one_time_outside_the_rule(tmp_path):
    """A ramp's levels and a heavy configuration are heavy parts: each runs one time, in round 1,
    and again on request; one with other repetitions is refused
    (`test_evaluation_sweep_heavy.py`)."""
    path = tmp_path / "spec.toml"
    path.write_text(
        SPEC
        + """
# 4-6: a ramp.
[[sweep]]
ramp = true
topology = "cross"
boxes_per_minute = [10, 20, 40]
repetitions = 1

# 7: a heavy configuration.
[[sweep]]
topology = "grid"
boxes_per_minute = 400
heavy = true
repetitions = 1
"""
    )

    found = configurations(read_spec(path))

    assert [(c.number, c.ruled, c.repetitions) for c in found] == [
        (1, True, 20),
        (2, True, 20),
        (3, True, 20),
        (4, False, 1),
        (5, False, 1),
        (6, False, 1),
        (7, False, 1),
    ]
