"""The rounds of a sweep under the precision rule (interim names): which runs the next round makes. Pure functions over the run summaries, with no
store and no clock.

**A round** makes one run of each selected configuration under the rule that is still **open**, in
the spec's order: round k makes repetition k. A configuration is open while it does not meet the
rule and has made fewer than the rule's maximum of runs, 20 (`is_open`, the one place that decides
it); one whose throughput median is 0 once it has its minimum of valid runs stops there. A round is a time window in the
sense of the numbers decision, so the minimum of 10 valid runs comes from at least
10 time windows.

**The next round** is the lowest round that has a run to make: a repetition without a record of
an open selected configuration (`next_round`). A run has a record when it completed or failed;
an interrupted attempt has none, so its repetition is planned again. Everything is read from the
records (the summaries): a call that stops goes on at its round in the next call, a configuration
with more records than the others is planned in the round of its next repetition, and the records
made in the passes before the rounds count as their repetitions.


"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from jms_kapps.evaluation.precision import Verdict, judge, made_repetitions
from jms_kapps.evaluation.record import RUN_KIND, UNWRAPPED_RUN_KIND
from jms_kapps.evaluation.spec import PrecisionRule, SpecConfiguration
from jms_kapps.evaluation.summary import RunSummary
from jms_kapps.evaluation.table import rule_numbers


@dataclass(frozen=True)
class Round:
    """The round a call makes: its number, which is the repetition it makes, and its runs
    without a record, each an open configuration under the rule with that repetition, in the
    spec's order."""

    number: int
    runs: tuple[tuple[SpecConfiguration, int], ...]


def own_summaries(
    configuration: SpecConfiguration, summaries: Sequence[RunSummary]
) -> list[RunSummary]:
    """The runs among `summaries` that are the configuration's own: its key, a run of use case 2
    with the wrapper or without it as the configuration says (not a baseline)."""
    kind = RUN_KIND if configuration.values["wrapper"] else UNWRAPPED_RUN_KIND
    return [s for s in summaries if s.key == configuration.key and s.run_kind == kind]


def verdict_of(
    configuration: SpecConfiguration,
    summaries: Sequence[RunSummary],
    rule: PrecisionRule,
) -> Verdict:
    """How `configuration` stands against the rule, from its runs among `summaries`: the numbers
    are the table's (`table.rule_numbers`)."""
    own = own_summaries(configuration, summaries)
    valid = sum(1 for summary in own if summary.counts)
    if valid < rule.minimum_runs:
        # The rule cannot hold yet, whatever the numbers: no bootstrap is needed to say so.
        return Verdict(
            met=False,
            made=len(made_repetitions(own)),
            valid=valid,
            maximum=rule.maximum_runs,
            unmet=(f"{valid} valid runs, {rule.minimum_runs} needed",),
        )
    return judge(
        rule_numbers(configuration, own),
        made=len(made_repetitions(own)),
        wrapper=bool(configuration.values["wrapper"]),
        rule=rule,
    )


def is_open(
    configuration: SpecConfiguration,
    summaries: Sequence[RunSummary],
    rule: PrecisionRule,
    *,
    verdict: Verdict | None = None,
) -> bool:
    """Whether the rounds still plan runs of `configuration` (`Verdict.stopped`): it does not
    meet the rule, has made fewer than its maximum of runs, and its throughput median is not 0 at
    its minimum of valid runs or later. The one place that decides it.

    Args:
        verdict: Its verdict, where the caller has it already (`verdict_of`).
    """
    standing = (
        verdict if verdict is not None else verdict_of(configuration, summaries, rule)
    )
    return not standing.stopped


def open_configurations(
    found: Sequence[SpecConfiguration],
    rule: PrecisionRule,
    summaries: Sequence[RunSummary],
    *,
    configuration_numbers: Collection[int] | None = None,
) -> list[SpecConfiguration]:
    """The selected configurations under the rule that are open, in the spec's order.

    Args:
        found: The spec's configurations; only those under the rule (`ruled`) can be open.
        configuration_numbers: The configurations selected by their numbers, or None for all.
    """
    return [
        configuration
        for configuration in found
        if configuration.ruled
        and (
            configuration_numbers is None
            or configuration.number in configuration_numbers
        )
        and is_open(configuration, summaries, rule)
    ]


def next_round(
    found: Sequence[SpecConfiguration],
    rule: PrecisionRule,
    summaries: Sequence[RunSummary],
    *,
    configuration_numbers: Collection[int] | None = None,
    repetition_numbers: Collection[int] | None = None,
) -> Round | None:
    """The lowest round that has a run to make among the open selected configurations, with its
    runs, or None where there is none: every selected configuration meets the rule, has made its
    maximum or stopped at a throughput median of 0, or, with `repetition_numbers`, every selected
    repetition of each open one has a record.

    Args:
        found: The spec's configurations; only those under the rule (`ruled`) are planned.
        configuration_numbers: The configurations selected by their numbers, or None for all.
        repetition_numbers: The repetitions selected by their numbers, or None for all: a call
            narrowed to them makes only their rounds (`--repetitions`).
    """
    candidates = open_configurations(
        found, rule, summaries, configuration_numbers=configuration_numbers
    )
    made = {
        configuration.key: made_repetitions(own_summaries(configuration, summaries))
        for configuration in candidates
    }
    for number in range(1, rule.maximum_runs + 1):
        if repetition_numbers is not None and number not in repetition_numbers:
            continue
        runs = tuple(
            (configuration, number)
            for configuration in candidates
            if number not in made[configuration.key]
        )
        if runs:
            return Round(number, runs)
    return None
