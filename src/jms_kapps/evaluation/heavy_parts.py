"""The heavy parts of a spec with the precision rule, in its rounds (interim names): which repetition of each the rounds make, and the repetitions a person asks
for. Pure functions over the run summaries, with no store and no clock.

**A heavy part** is one of three:

- **a ramp** (`spec`, `ramp`), named by its block: its levels, one run each, in rising order of
  their box rates, until the ramp ends by its rule;
- **a replay** (`replay_levels`), named by the number of its configuration: the levels of the
  store-only baseline that replay the configuration's first valid UC2 run, one for each factor in
  rising order, until the first broken level. A replay at the factor 1 runs at normal load, but it
  follows the same rules;
- **a sustained load**, named by the number of its configuration: a heavy configuration
  (`heavy = true`) that is no ramp's level, one run. A heavy configuration with replay factors is
  a sustained load and a replay.

**Each runs one time, in round 1.** The rounds (`rounds`) make the first repetition of each heavy
part in round 1, at its place in the spec's order: a ramp at its block's place, a sustained load
at its configuration's place, and a replay right after its configuration's first valid run. A
configuration with no valid run keeps its replay for the round that makes one. Then a person reads
the result and decides if a repetition is necessary. A later round makes no level of a heavy part
whose repetition **ended** (`Standing.ended`): a ramp that ended by its rule or reached its top
level, a replay that broke, failed or reached its highest factor, a sustained load with a record.
One that a round left **unfinished** (a stop, a run that did not complete, a level skipped for want
of an approval) goes on in the next round, from its lowest level without a record.

**A repetition on request** (`requested_repetitions`, the option `REPEAT_OPTION`): a call names each heavy part with a target
repetition, a ramp by its block (`block3:2`), a replay or a sustained load by its configuration's
number (`7:2`); for a heavy configuration with replay factors the number names both. The target
asks the rounds to make sure that repetition exists: while the part's last repetition ended below
the target, the next round makes one more (`Standing.after`), a ramp again from its lowest rate, a
replay again from its lowest factor, each until it ends by its rule. A target at or below a
repetition that exists or is under way makes nothing, so a restart with the same command line is
safe. A repetition has its own number in its records (`sweep.repetition` 2, then 3), and its runs
take the pick seed of that repetition. A repeated replay replays the same run as its first
repetition did (`first_replayed`), so the repetitions show the store and the time of day, with
the same input.


"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jms_kapps.evaluation.baseline import BASELINE_RUN_KIND
from jms_kapps.evaluation.bench import INTERRUPTED
from jms_kapps.evaluation.precision import made_repetitions
from jms_kapps.evaluation.ramp import judge, steps_of
from jms_kapps.evaluation.record import RUN_KIND, UNWRAPPED_RUN_KIND
from jms_kapps.evaluation.replay_levels import level_key
from jms_kapps.evaluation.spec import SpecConfiguration, SpecRefused, numbers, ramps_of
from jms_kapps.evaluation.summary import (
    COMPLETED,
    RunSummary,
    repetition_of,
    repetitions_made,
)

# The three kinds of heavy part.
RAMP = "ramp"
REPLAY = "replay"
SUSTAINED_LOAD = "sustained load"

# The option that asks for one more repetition of heavy parts, and the prefix that names a ramp by
# its block in it (`block3`).
REPEAT_OPTION = "--repeat"
BLOCK = "block"

# A heavy part by its kind and its number: the block of a ramp, else the configuration's number.
PartId = tuple[str, int]


@dataclass(frozen=True)
class HeavyPart:
    """One heavy part of a spec: its kind, its number (the block of a ramp, else the number of its
    configuration) and its configurations, a ramp's levels in rising order of their box rates, or
    the one configuration of a replay or a sustained load."""

    kind: str
    number: int
    configurations: tuple[SpecConfiguration, ...]

    @property
    def id(self) -> PartId:
        return (self.kind, self.number)

    @property
    def configuration(self) -> SpecConfiguration:
        """The configuration of a replay or a sustained load; a ramp's lowest level."""
        return self.configurations[0]

    @property
    def name(self) -> str:
        """The part, for a person to read."""
        if self.kind == RAMP:
            return f"the ramp of block {self.number}"
        if self.kind == REPLAY:
            return f"the replay of configuration {self.number}"
        return f"configuration {self.number}, a sustained load"

    @property
    def handle(self) -> str:
        """How `REPEAT_OPTION` names the part: `block3` for a ramp, else the number."""
        return f"{BLOCK}{self.number}" if self.kind == RAMP else str(self.number)


def heavy_parts(found: Sequence[SpecConfiguration]) -> list[HeavyPart]:
    """The heavy parts among the configurations `found`, in the spec's order: a ramp at its lowest
    level's place, and a heavy configuration's sustained load before its replay."""
    parts: list[HeavyPart] = []
    ramps = ramps_of(found)
    for configuration in found:
        if configuration.ramp is not None:
            levels = ramps[configuration.ramp]
            if levels[0].key == configuration.key:
                parts.append(HeavyPart(RAMP, configuration.ramp, tuple(levels)))
            continue
        if configuration.values["heavy"] is True:
            parts.append(
                HeavyPart(SUSTAINED_LOAD, configuration.number, (configuration,))
            )
        if configuration.replay_factors:
            parts.append(HeavyPart(REPLAY, configuration.number, (configuration,)))
    return parts


def part_of(configuration: SpecConfiguration) -> PartId | None:
    """The ramp or the sustained load whose run `configuration` makes, or None for a configuration
    at normal load (its replay, if any, is a part of its own)."""
    if configuration.ramp is not None:
        return (RAMP, configuration.ramp)
    if configuration.values["heavy"] is True:
        return (SUSTAINED_LOAD, configuration.number)
    return None


@dataclass(frozen=True)
class Standing:
    """Where a heavy part stands, from its records: `last`, its highest repetition with a record
    (0 for none), whether that repetition `ended`, and why, for a person to read. `waiting` is a
    replay with no record and no valid run to replay yet."""

    part: HeavyPart
    last: int = 0
    ended: bool = False
    why: str = ""
    waiting: bool = False

    @property
    def due(self) -> int | None:
        """The repetition that the rounds make of the part without a request: the first one where
        it has none, the last one where it did not end, and None where it ended."""
        if self.last == 0:
            return 1
        return None if self.ended else self.last

    def after(self, target: int | None) -> int | None:
        """The repetition that the rounds make of the part, with the target repetition `target`
        that a request gives it (`requested_repetitions`), or None for none: one more than the
        last where the last ended below the target, else `due`. So the rounds make one more
        repetition in each round until the target exists, and nothing more once it exists or is
        under way."""
        if target is not None and self.ended and self.last < target:
            return self.last + 1
        return self.due


def standing_of(part: HeavyPart, summaries: Sequence[RunSummary]) -> Standing:
    """Where `part` stands, from its runs among `summaries`. A run that a stop signal ended is no
    record: its level has not run yet."""
    if part.kind == RAMP:
        return _ramp_standing(part, summaries)
    if part.kind == REPLAY:
        return _replay_standing(part, summaries)
    configuration = part.configuration
    kind = RUN_KIND if configuration.values["wrapper"] else UNWRAPPED_RUN_KIND
    made = made_repetitions(
        s for s in summaries if s.key == configuration.key and s.run_kind == kind
    )
    last = max(made, default=0)
    return Standing(
        part,
        last,
        ended=last > 0,
        why="its run has a record" if last else "no record yet",
    )


def _ramp_standing(part: HeavyPart, summaries: Sequence[RunSummary]) -> Standing:
    made = repetitions_made(
        summaries,
        run_kind=RUN_KIND,
        keys={level.key for level in part.configurations},
    )
    last = max(made, default=0)
    if not last:
        return Standing(part, why="no record yet")
    judged = judge(steps_of(part.configurations, summaries, repetition=last))
    ended = judged.ended or len(judged.levels) == len(part.configurations)
    return Standing(part, last, ended=ended, why=judged.why)


def replay_ended_at(
    configuration: SpecConfiguration,
    summaries: Sequence[RunSummary],
    repetition: int = 1,
) -> float | None:
    """The factor at which repetition `repetition` of the replay of `configuration` ended: the
    lowest factor whose level has a record that is broken or failed, or None where none has. A
    failed level cannot be judged, so no higher factor is sent after it, as a ramp sends no higher
    rate after a level that cannot be judged. A level that a stop signal ended has not run yet."""
    for factor in configuration.replay_factors:
        if any(
            run.outcome != COMPLETED or bool((run.replay_level or {}).get("broken"))
            for run in _levels(configuration, factor, summaries, repetition)
        ):
            return factor
    return None


def _levels(
    configuration: SpecConfiguration,
    factor: float,
    summaries: Sequence[RunSummary],
    repetition: int | None,
) -> list[RunSummary]:
    """The records of the level of `configuration`'s replay at `factor`, of `repetition` (None for
    every repetition), that are not interrupted."""
    key = level_key(configuration.key, factor)
    return [
        run
        for run in summaries
        if run.key == key
        and run.run_kind == BASELINE_RUN_KIND
        and run.outcome != INTERRUPTED
        and (repetition is None or repetition_of(run) == repetition)
    ]


def _replay_standing(part: HeavyPart, summaries: Sequence[RunSummary]) -> Standing:
    configuration = part.configuration
    made = repetitions_made(
        summaries,
        run_kind=BASELINE_RUN_KIND,
        keys={level_key(configuration.key, f) for f in configuration.replay_factors},
    )
    last = max(made, default=0)
    if not last:
        waiting = first_valid_run(summaries, configuration.key) is None
        return Standing(
            part,
            why="waits for the first valid run of its configuration"
            if waiting
            else "no record yet",
            waiting=waiting,
        )
    ended_at = replay_ended_at(configuration, summaries, last)
    if ended_at is not None:
        return Standing(
            part, last, ended=True, why=f"it ended at the factor {ended_at:g}"
        )
    highest = configuration.replay_factors[-1]
    if all(
        _levels(configuration, factor, summaries, last)
        for factor in configuration.replay_factors
    ):
        return Standing(
            part,
            last,
            ended=True,
            why=f"it was not broken up to the factor {highest:g}, its highest",
        )
    return Standing(part, last, why="not every factor has a record")


def first_valid_run(
    summaries: Sequence[RunSummary], configuration_key: str
) -> RunSummary | None:
    """The first valid run of use case 2 of the configuration of `configuration_key` among
    `summaries`, which the sweep replays: of the run kind `uc2` (with the wrapper, so its record
    holds its store requests), completed and not found invalid; the lowest repetition, and the
    earliest of two with one. None where there is none."""
    valid = [
        run
        for run in summaries
        if run.run_kind == RUN_KIND and run.key == configuration_key and run.counts
    ]
    return min(valid, key=lambda run: (run.repetition or 0, run.run_id), default=None)


def first_replayed(
    configuration: SpecConfiguration, summaries: Sequence[RunSummary]
) -> str | None:
    """The run id of the run that the first repetition of the replay of `configuration` replayed,
    as its levels' records name it, or None where no level of it names one. Every repetition of
    the replay replays that run."""
    named = sorted(
        (factor, run.run_id, run.replays)
        for factor in configuration.replay_factors
        for run in _levels(configuration, factor, summaries, 1)
        if run.replays
    )
    return named[0][2] if named else None


def due_repetitions(
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    requested: Mapping[PartId, int] | None = None,
) -> dict[PartId, int]:
    """The repetition that the next round makes of each heavy part among `found`, by its id, with
    the target repetitions `requested` for some (`requested_repetitions`): the first one of a part
    without a record, the one a round left unfinished, or the next one below its target; no entry
    for a part whose last repetition ended and reached its target or has none."""
    due: dict[PartId, int] = {}
    for part in heavy_parts(found):
        repetition = standing_of(part, summaries).after((requested or {}).get(part.id))
        if repetition is not None:
            due[part.id] = repetition
    return due


def requested_repetitions(
    found: Sequence[SpecConfiguration],
    text: str,
    *,
    removed: Sequence[str] = (),
) -> tuple[dict[PartId, int], list[HeavyPart]]:
    """The target repetition that `text`, the value of `REPEAT_OPTION`, gives each heavy part it
    names, by the part's id, and the parts it names that are removed, which it leaves out. A
    target asks the rounds to make sure that repetition exists (`Standing.after`); a number of a
    heavy configuration with replay factors names both its parts.

    Args:
        removed: The keys of the removed configurations.

    Raises:
        SpecRefused: If `text` is no list of parts with their target repetitions, or names a block
            that is no ramp, a configuration the spec does not have, a level of a ramp, or a
            configuration at normal load without replay factors.
    """
    parts = heavy_parts(found)
    by_number = {configuration.number: configuration for configuration in found}
    targets: dict[PartId, int] = {}
    left_out: list[HeavyPart] = []
    for kind, number, target in _named(text):
        if kind == BLOCK:
            named = [p for p in parts if p.kind == RAMP and p.number == number]
            if not named:
                raise SpecRefused(
                    f"{REPEAT_OPTION} names {BLOCK}{number}, and block {number} of the spec is "
                    "no ramp: name a ramp by its block, a replay or a sustained load by its "
                    "configuration's number"
                )
        else:
            configuration = by_number.get(number)
            if configuration is None:
                raise SpecRefused(
                    f"{REPEAT_OPTION}: the spec has {len(found)} configurations: there is no "
                    f"configuration {number}"
                )
            if configuration.ramp is not None:
                raise SpecRefused(
                    f"{REPEAT_OPTION}: configuration {number} is a level of the ramp of block "
                    f"{configuration.ramp}: name the ramp by its block, "
                    f"{BLOCK}{configuration.ramp}:{target}"
                )
            named = [
                p
                for p in parts
                if p.kind in (SUSTAINED_LOAD, REPLAY) and p.number == number
            ]
            if not named:
                raise SpecRefused(
                    f"{REPEAT_OPTION}: configuration {number} is no heavy part: it runs at "
                    "normal load and has no replay factors. A repetition on request is made of "
                    "a ramp, a replay or a sustained load"
                )
        for part in named:
            if any(c.key in removed for c in part.configurations):
                left_out.append(part)
            else:
                targets[part.id] = max(target, targets.get(part.id, 0))
    return targets, left_out


# How `REPEAT_OPTION` names the parts, for a refusal to say.
_REPEAT_FORM = (
    f"{REPEAT_OPTION} names each heavy part with its target repetition: a ramp by its block, "
    f"such as {BLOCK}3:2, a replay or a sustained load by its configuration's number, such as "
    "7:2 (a range such as 7-9:2 names each)"
)


def _named(text: str) -> list[tuple[str, int, int]]:
    """The parts that `text` names, each with its target repetition: `(BLOCK, block, target)` for
    a ramp, `("configuration", number, target)` for a configuration, from items such as
    `block3:2`, `7:2` and `9-11:3`, separated by commas.

    Raises:
        SpecRefused: If an item is none of them, or its target is no whole number from 1.
    """
    named: list[tuple[str, int, int]] = []
    for item in text.split(","):
        name, colon, target = item.strip().partition(":")
        if not colon or not target.strip().isdigit() or int(target) < 1:
            raise SpecRefused(f"{_REPEAT_FORM}: not {text!r}")
        name = name.strip()
        if name.startswith(BLOCK):
            block = name[len(BLOCK) :].strip()
            if not block.isdigit() or int(block) < 1:
                raise SpecRefused(f"{_REPEAT_FORM}: not {text!r}")
            named.append((BLOCK, int(block), int(target)))
            continue
        try:
            named += [("configuration", n, int(target)) for n in numbers(name)]
        except SpecRefused:
            raise SpecRefused(f"{_REPEAT_FORM}: not {text!r}") from None
    return named
