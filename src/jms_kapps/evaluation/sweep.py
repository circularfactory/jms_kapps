"""`jms-eval-sweep`: the runs of a spec, one by one, each a UC2 run of one configuration on its own
repository of the configured GraphDB, with its record (`jms-eval-uc2`). Iteration one of the
measurement harness.

    jms-eval-sweep <spec.toml> [--runs <folder>] [--prefills <folder>]
                   [--configurations 1-9,12] [--repetitions 1-3] [--set KEY=VALUE ...] [--list]
                   [--heavy-run-approval "<who>, <when>"] [--stop-at <time>]
                   [--repeat block3:2,7:2]

It reads the spec (`spec`), with each `--set` over its values, and runs each selected repetition of
each selected configuration, in the spec's order: the repetitions of one configuration one after
the other. `--configurations` and `--repetitions` select by number, from 1, in the order `--list`
shows; without them every run of the spec is selected.

**A run whose record exists is skipped**, so that a sweep continues in a later time window: a
record below `--runs` that says it is that repetition of a configuration with the same values
(`spec.SpecConfiguration.key`). A run that failed is skipped too: a failure is a result. A run
that a stop signal ended is no measurement: its record's `outcome` is `interrupted`, and the next
call makes the run again, in a folder of its own, beside the interrupted attempt's, whose record
stays as it is.

**The precision rule and the rounds** (interim names). A spec with a `[precision]` table gives no fixed repetitions
to its configurations at normal load: each runs until the rule holds (`precision`), at most 20
times, in rounds (`rounds`). **A round** makes one run of each selected configuration that is still
open, in the spec's order: round k makes repetition k. A configuration is open while it does not
meet the rule and has made fewer than 20 runs (`rounds.is_open`); one whose throughput median is
0 once it has its minimum of valid runs stops there. **One call goes on round after round** (`sweep_rounds`) until one of its ends:
the stop time, the stop file, or the end of the rule, where no selected configuration is open. The
state is in the records, as above: a call that stops goes on at its round in the next call, an
interrupted attempt is made again and does not count toward the 20, a call with a smaller
`--configurations` leaves the configurations it no longer selects as they are, and `--repetitions`
narrows the rounds to those of its repetitions.
**Removed configurations:**
`--remove 3,7` records configurations by their keys in `removed-configurations.json` in `--runs`,
and every later call of this command and of `jms-eval` leaves them out, whatever it selects, until
`--put-back 3` puts them back; both run nothing, and `--list` shows each removed configuration as
removed. A running call reads the file again before each round. A record
holds `sweep.repetitions` 20 and `sweep.round`; a record made in a pass before the rounds keeps its
`sweep.pass`, and its run counts as its repetition. `--list` shows the next round and, for each
configuration, its runs made, its valid runs and whether it is open.

**The heavy parts in the rounds** (interim names). A ramp, a
replay and a sustained load (a heavy configuration that is no ramp's level) each run one time, in
round 1, at its place in the spec's order (`heavy_parts`, `chosen_runs`, `replays_due`): a ramp at
its block's place, its rates rising, until it ends by its rule; a replay right after its
configuration's first valid run, its factors rising, until its first broken level; a configuration
without a valid run keeps its replay for the round that makes one. A later round makes no level of
one that ended, and goes on with one that a round left unfinished, from its lowest level without a
record. `--repeat block3:2,7:2` makes sure a target repetition of each heavy part it names exists:
ramps by their block, replays and sustained loads by their configuration's number (both for a
heavy configuration with replay factors). While a part's last repetition ended below its target,
the next round makes one more, from the lowest level until it ends, under the same approval, and
its records say `sweep.repetition` 2 (then 3); a repeated replay replays the run that its first
repetition replayed. A target that exists or is under way makes nothing, so a restart with the
same command line is safe. A target whose next repetition
`--repetitions` leaves out makes nothing in that call, which says so and goes on. A heavy
configuration of such a spec with `repetitions` other than 1
is refused (`spec`). `--list` shows where each heavy part stands.

**The stop time and the stop file** (interim names). Before each run, a replay
level too, the call checks its stop time, `--stop-at` (a date and time with its zone, such as
2026-10-14T06:00+02:00), and the stop file, `STOP` in `--runs`. At the stop time, or where the stop
file is there, it stops before that run; a run that started before the stop time runs to its end.
A call that stops at the stop file removes it, and a call that ends another way removes a stop file
that came meanwhile, so that it never stops the next call. So a person removes configurations at
any run: make the stop file, wait for the call to stop, remove them with `--remove`, and call the
sweep again; the next call goes on at the round that the records show. Both hold for a spec of
fixed repetitions too. A round that has nothing to make, since the rule cannot read records that
name its runs, ends the call with its own message.

**The series** (interim names). `--list` and each run line, a replay level's lines
too, show a configuration's number with the spec's series
(`spec.SpecConfiguration.name`), such as "before 7"; a record, a
run's folder and the options that select (`--configurations`, `--remove`, `--put-back`,
`--repeat`) keep the number.

**The pick seed.** Repetition k of every configuration takes the seed `pick_seed(base, k)` for the
WarehouseManagementSystem's picks: the base is the spec's `pick_base_seed`, else the fixed
`DEFAULT_BASE_SEED`, so repetition k has the same seed in every configuration and the repetitions
of two configurations stay paired. The seed is written into the run's saved configuration as a run
setting, outside the configuration's key, and into its record; `jms-eval-uc2` run on that saved
configuration makes the same picks.

**Before the first run** it refuses a spec in which a configuration misses a value, naming the
value, and it resolves each selected configuration that has a run to make, so that a file it names
that is not there, or a strategy that is not found, is refused before anything reaches the store.
It refuses a configuration with the wrapper where OpenTelemetry, the extra `evaluation`, is not
installed.

**A heavy run** (`heavy = true`: a sustained load or a ramp's level) runs only under an approval
that covers it (`approval`): the option `--heavy-run-approval` gives the approval file, which any
person may give for one spec and one period, in agreement with the server's admins. Inside the
period the sweep runs every heavy configuration of the approved spec under that one approval,
several ramps and sustained loads alike. **Each heavy run is checked at its start, and one that no
approval covers then is skipped and listed**: where
there is no approval, the period is not now, the spec is another or changed since it was approved,
or the call has `--set` overrides, which no approval covers. The sweep goes on with the next run,
a skipped level of a ramp or a replay skips its higher levels in that round, and at its end the
call lists each run it skipped with the reason. A skip writes no record, so a later call whose
approval covers the run makes it; a call that only skipped heavy runs exits as if they were not
selected. A run at normal load needs no
approval, and the heavy switch is off by default. Each heavy run's record keeps the whole approval.

**A ramp** (`spec`, `ramp`) runs its rates one per run, in rising order, and a selection with a
level of a ramp whose lower levels have neither a record nor a run to make is refused, since a ramp
judges each rate before the next. After each run the sweep judges the level
with the broken rule, and it stops the ramp at the first level the ramp ends at, a broken one or
one with no run that counts: the ramp's higher rates are not run, and the sweep goes on with the
runs after them. A plateau or a UC2-limited level breaks nothing, so the ramp goes on past it
(`ramp`). A ramp that ended in an earlier call sends no higher rate either; under the precision
rule a repetition on request starts again from its lowest rate, and each repetition is judged
apart.

**A failed run does not stop the sweep; 3 failed runs in a row do**: one failed run at night must
not end a series that runs with no person present, so the sweep goes on after a failed
run, and stops after `FAILED_RUNS_IN_A_ROW` failed runs one after the other, so that a store that
refuses every run is not sent the rest of the spec. A run counts as failed where its outcome is
failed or failed over time, or where the records do not show it after the call made it, right after
the run or when the call chooses its next round: the call then leaves its configuration out for
the rest of the call. A completed run whose repository the
store did not delete counts too and resets nothing; any other completed run, valid or invalid,
resets the count. A run that a stop signal ended (Ctrl+C, SIGTERM or a closing terminal's SIGHUP)
stops the call at once. The next call skips a failed run and goes on after it, and makes an
interrupted one again.

**The replay levels** (interim names). A block of the
spec may give `replay_factors` (`spec`). After the UC2 runs of each configuration of that block the
sweep replays the first valid run of that configuration, with the store-only baseline (`baseline`),
once per factor in rising order, each level on a repository of its own (`replay_levels`). A level is
a run of the run kind `baseline` whose record says `sweep.key`, the configuration's key and the
factor, and holds the level's numbers (`replay_level`). After each level the sweep reads whether it
is broken, and the replay of that configuration stops at its first broken level: the higher factors
are not run, in this call or a later one. A level that failed ends the replay too, and counts
toward the failed runs in a row. **Every factor above 1 is a heavy run** and runs only under an
approval that covers the spec and the replayed configuration, exactly as a heavy configuration
does (`approval.cover`): a level above 1 that none covers at its start is skipped and listed, with
its higher levels, and so is a level that cannot start. A factor of 1 is a replay
at normal load. The rounds of the precision rule plan a replay as a heavy part (above): in round 1,
right after its configuration's first valid run, and later only while it has not ended or has a
request; a level's record says the replay's repetition. `--list` shows each configuration's
levels.

**The report** (`report`, `jms-eval-report`) is made again after each run, right after the run
returns and before the sweep judges how it ended, whatever the end: completed, invalid, failed or
interrupted. It is made with the sweep's `--set` overrides, into `report/` below `--runs`. An error
of the report is told on the sweep's output and never stops or fails the sweep.

It exits with 0 once every selected run has its record, or no selected configuration under the
precision rule is open, or it stopped at its stop time or at the stop file, and every run it made
completed; 1 when a run it made failed, when it stopped at a run that a stop signal ended, at the
third failed run in a row, by a stop signal between two runs, or at a round with nothing to make;
and 2 when it refused to start: nothing reached a store then. A heavy run that it skipped changes
no exit code.
`--remove` and `--put-back` exit with 0 once the removed configurations are written. `--list`
shows the spec's configurations and their runs, and runs nothing, so it needs no store.

The connection to the configured GraphDB is that of `jms-eval-uc2`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jms_kapps.evaluation import wrapper
from jms_kapps.evaluation.approval import (
    APPROVAL_FORM,
    NO_APPROVAL,
    OPTION,
    Approval,
    Covered,
    HeavyRunRefused,
    cover,
    load_approval,
)
from jms_kapps.evaluation.baseline import (
    BaselineRefused,
    read_recorded_run,
)
from jms_kapps.evaluation.baseline import (
    run_on_the_configured_store as run_baseline_on_the_configured_store,
)
from jms_kapps.evaluation.bench import INTERRUPTED, outcome_line
from jms_kapps.evaluation.configuration import Configuration, ConfigurationRefused
from jms_kapps.evaluation.heavy_parts import (
    RAMP,
    REPEAT_OPTION,
    REPLAY,
    HeavyPart,
    PartId,
    due_repetitions,
    first_replayed,
    first_valid_run,
    heavy_parts,
    part_of,
    replay_ended_at,
    requested_repetitions,
    standing_of,
)
from jms_kapps.evaluation.ramp import Judgement, judge, steps_of
from jms_kapps.evaluation.replay_levels import (
    RECORD_KEY,
    is_heavy,
    level_key,
    signal_names,
    why_heavy,
)
from jms_kapps.evaluation.rounds import (
    Round,
    is_open,
    next_round,
    open_configurations,
    verdict_of,
)
from jms_kapps.evaluation.run_uc2 import (
    missing_store_variables,
    run_on_the_configured_store,
)
from jms_kapps.evaluation.spec import (
    PrecisionRule,
    Spec,
    SpecConfiguration,
    SpecRefused,
    configurations,
    for_repetition,
    numbers,
    ramps_of,
    read_spec,
    resolved,
    setting,
    sweep_fields,
)
from jms_kapps.evaluation.summary import (
    COMPLETED,
    RunSummary,
    read_summaries,
    summary_of,
)

PROGRAM = "jms-eval-sweep"

# The stop file: where it is in the runs' folder, a call stops before its next run and removes it
# (interim name).
STOP_FILE = "STOP"

# The option that gives a call's stop time (interim name).
STOP_TIME_OPTION = "--stop-at"

# The removed configurations of a sweep, by their keys, in the runs' folder, and the options that
# remove configurations and put them back (interim names).
REMOVED_FILE = "removed-configurations.json"
REMOVE_OPTION = "--remove"
PUT_BACK_OPTION = "--put-back"

# What makes one run of a configuration and returns its record: `run_on_the_configured_store`.
RunOne = Callable[..., dict[str, Any]]

# What makes one replay level of a recorded run, by the path of its record, and returns the
# level's record: `run_replay_on_the_configured_store`.
RunReplay = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class HeavyCheck:
    """What covers a heavy run at its start (interim name): `approval.cover` with
    the call's approval, for the spec, the configuration's key and the configuration resolved,
    with whether the call has `--set` overrides. `why` says what makes the run heavy (a heavy
    configuration, or a replay at a factor above 1)."""

    approval: Approval | None
    spec: Path
    key: str
    configuration: Configuration
    overridden: bool = False
    why: str = "heavy = true"

    def cover(self, moment: datetime) -> Covered:
        """The covered record of the run that starts at `moment`.

        Raises:
            HeavyRunRefused: As `approval.cover`, with its short reason.
        """
        return cover(
            self.approval,
            spec=self.spec,
            key=self.key,
            configuration=self.configuration,
            overridden=self.overridden,
            now=moment,
            why=self.why,
        )


@dataclass(frozen=True)
class SweepRun:
    """One run of a sweep: a repetition of a configuration of the spec, and `to_run`, the
    configuration it runs with, resolved, or None where it is skipped: where its record exists,
    or where it is a level of a ramp above the level the ramp ended at (`beyond_the_ramp`)."""

    configuration: SpecConfiguration
    repetition: int
    to_run: Configuration | None
    beyond_the_ramp: bool = False
    # What covers a heavy run at its start (`HeavyCheck`), or None for a run at
    # normal load.
    heavy: HeavyCheck | None = None
    # The round of the precision rule that makes the run, or None for a run of fixed repetitions
    # (interim name): round k makes repetition k.
    round_number: int | None = None
    # The factor of a replay level of the configuration's first valid run, or None for a UC2 run
    # (interim name). Then `repetition` is the replay's repetition,
    # and `beyond_the_ramp` says that the replay ended below the level.
    replay_factor: float | None = None

    @property
    def skipped(self) -> bool:
        """Whether the sweep skips it: its record exists, or its ramp or replay ended below it."""
        return self.to_run is None

    def fields(self, spec: Spec) -> dict[str, Any]:
        """What its record says of the sweep: the spec, the configuration's number and key, the
        repetition of how many (under the precision rule, the maximum), and under the rule the
        round. A replay level says the key of the level (the configuration's key and the factor),
        the key it replays and its factor."""
        if self.replay_factor is not None:
            return {
                "spec": str(spec.path),
                "configuration": self.configuration.number,
                "key": level_key(self.configuration.key, self.replay_factor),
                "replayed_key": self.configuration.key,
                "replay_factor": self.replay_factor,
                "repetition": self.repetition,
                "repetitions": 1,
            }
        return sweep_fields(spec, self.configuration, self.repetition) | (
            {} if self.round_number is None else {"round": self.round_number}
        )


# The runs of a sweep that have a record, each by its configuration's key and its repetition,
# with the outcome its record says.
Recorded = dict[tuple[str, int], str]


def recorded(runs: Path) -> Recorded:
    """The runs of a sweep whose record is below `runs`, each by its configuration's key and its
    repetition, with its outcome: `completed`, `interrupted`, or `failed` with why. Where a run
    has an interrupted record and another, the other's outcome. A record of no sweep, or one that
    is no JSON, is none of them."""
    found: Recorded = {}
    for path in sorted(runs.glob("*/record.json")):
        read = _recorded_run(path)
        if read is None:
            continue
        run, outcome = read
        if found.get(run, INTERRUPTED) == INTERRUPTED:
            found[run] = outcome
    return found


def _recorded_run(path: Path) -> tuple[tuple[str, int], str] | None:
    """The run that the record at `path` says it is, by its configuration's key and its
    repetition, with its outcome; None for a record of no sweep, or one that is no JSON."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        run = (str(record["sweep"]["key"]), int(record["sweep"]["repetition"]))
        return run, str(record["outcome"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def shows(runs: Path, record: Mapping[str, Any], key: str, repetition: int) -> bool:
    """Whether the records below `runs` show the run just made, which returned `record`: its
    record file, in the folder of its run id, says that it is the repetition `repetition` of the
    configuration (or the level) of `key`, as `recorded` reads it, so that a later round skips it
    (interim name). Only that one record is read."""
    read = _recorded_run(runs / str(record.get("run_id")) / "record.json")
    return read is not None and read[0] == (key, repetition) and read[1] != INTERRUPTED


def has_run(found: Recorded, key: str, repetition: int) -> bool:
    """Whether the repetition `repetition` of the configuration of `key` has run, so that the
    sweep skips it: it has a record whose outcome is `completed` or `failed`. One that a stop
    signal ended, `interrupted`, has not."""
    outcome = found.get((key, repetition))
    return outcome is not None and outcome != INTERRUPTED


# The removed configurations of a sweep, by their keys, each with what its removal says: the
# configuration's number and spec when it was removed, and when (`REMOVED_FILE`).
Removed = dict[str, dict[str, Any]]


def read_removed(runs: Path) -> Removed:
    """The removed configurations of the sweep below `runs` (`REMOVED_FILE`): none where the file
    is not there (interim name).

    Raises:
        SpecRefused: If the file is there and cannot be read, so that no call plans a removed
            configuration again by mistake.
    """
    path = runs / REMOVED_FILE
    if not path.exists():
        return {}
    try:
        removed = json.loads(path.read_text(encoding="utf-8"))["removed"]
        if not isinstance(removed, dict) or not all(
            isinstance(key, str) and isinstance(entry, dict)
            for key, entry in removed.items()
        ):
            raise TypeError("removed is no table of keys")
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise SpecRefused(
            f"the removed configurations, {path}, cannot be read ({error}): repair the file "
            f"or put the configurations back with {PUT_BACK_OPTION}"
        ) from None
    return dict(removed)


def change_removed(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    runs: Path,
    *,
    remove: Sequence[int] = (),
    put_back: Sequence[int] = (),
    at: datetime | None = None,
) -> list[str]:
    """Remove the configurations of the numbers `remove` from the sweep below `runs`, and put
    back those of `put_back`, in the file `REMOVED_FILE`, by their keys; returns what to say.
    Every later call of the sweep and of `jms-eval` leaves a removed configuration out, whatever
    it selects, until it is put back (interim names).

    Raises:
        SpecRefused: If a number is not one of the spec's, or the file cannot be read.
    """
    beyond = sorted({n for n in (*remove, *put_back) if not 1 <= n <= len(found)})
    if beyond:
        raise SpecRefused(
            f"the spec has {len(found)} configurations: there is no configuration "
            f"{', '.join(map(str, beyond))}"
        )
    removed = read_removed(runs)
    said: list[str] = []
    for configuration in found:
        if configuration.number in remove and configuration.key not in removed:
            removed[configuration.key] = {
                "configuration": configuration.number,
                "spec": spec.path.name,
                "removed_at": (at or now()).isoformat(timespec="seconds"),
            }
            said.append(
                f"{PROGRAM}: configuration {configuration.number} is removed: no later call "
                f"plans it until {PUT_BACK_OPTION} {configuration.number}"
            )
        elif configuration.number in remove:
            said.append(
                f"{PROGRAM}: configuration {configuration.number} was removed already"
            )
        if configuration.number in put_back and configuration.key in removed:
            del removed[configuration.key]
            said.append(
                f"{PROGRAM}: configuration {configuration.number} is put back: the next "
                "call plans it again"
            )
        elif configuration.number in put_back:
            said.append(
                f"{PROGRAM}: configuration {configuration.number} was not removed"
            )
    runs.mkdir(parents=True, exist_ok=True)
    (runs / REMOVED_FILE).write_text(
        json.dumps({"removed": removed}, indent=2) + "\n", encoding="utf-8"
    )
    return said


def removed_line(call: Call, runs: Path) -> str | None:
    """What a call says of the configurations it leaves out as removed, or None for none."""
    left_out = [c.number for c in call.found if c.key in call.removed]
    if not left_out:
        return None
    numbers_removed = ", ".join(map(str, left_out))
    return (
        f"{PROGRAM}: removed: configuration {numbers_removed}, left out until "
        f"{PROGRAM} {PUT_BACK_OPTION} puts it back (the list is {runs / REMOVED_FILE})"
    )


def selected(
    found: Sequence[SpecConfiguration],
    *,
    configuration_numbers: Sequence[int] | None,
    repetition_numbers: Sequence[int] | None,
) -> list[tuple[SpecConfiguration, int]]:
    """Each selected repetition of each selected configuration, in the spec's order.

    Raises:
        SpecRefused: If a configuration selected is not one of the spec's.
    """
    if configuration_numbers is not None:
        beyond = [n for n in configuration_numbers if n > len(found)]
        if beyond:
            raise SpecRefused(
                f"the spec has {len(found)} configurations: there is no configuration "
                f"{', '.join(map(str, beyond))}"
            )
    return [
        (configuration, repetition)
        for configuration in found
        if configuration_numbers is None
        or configuration.number in configuration_numbers
        for repetition in range(1, configuration.repetitions + 1)
        if repetition_numbers is None or repetition in repetition_numbers
    ]


def chosen_runs(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    *,
    configuration_numbers: Sequence[int] | None,
    repetition_numbers: Sequence[int] | None,
    requested: Mapping[PartId, int] | None = None,
) -> tuple[list[tuple[SpecConfiguration, int]], Round | None]:
    """The runs a call's next round chooses, in the spec's order, and the round of the precision
    rule or None.

    A spec of fixed repetitions: `selected`. A spec with the rule (interim names):
    the runs of the lowest round that has a run to make among the open selected configurations
    under the rule (`rounds.next_round`), narrowed by `repetition_numbers`; and the selected runs
    of the heavy parts that the round makes (`heavy_parts.due_repetitions`): the
    levels of a ramp and the run of a sustained load, of the repetition that is due, with the
    repetitions `requested` for some. Each heavy part runs in round 1, and in a later round only
    while it has not ended or has a request. Where no round is left, those runs alone. A call
    that selects only those chooses them alone.

    Raises:
        SpecRefused: If a configuration selected is not one of the spec's.
    """
    everything = selected(
        found,
        configuration_numbers=configuration_numbers,
        repetition_numbers=repetition_numbers,
    )
    if spec.precision is None:
        return everything, None
    due = due_repetitions(found, summaries, requested)
    heavy = [
        (configuration, due[part])
        for configuration in found
        if (part := part_of(configuration)) is not None
        and part in due
        and (
            configuration_numbers is None
            or configuration.number in configuration_numbers
        )
        and (repetition_numbers is None or due[part] in repetition_numbers)
    ]
    planned = next_round(
        found,
        spec.precision,
        summaries,
        configuration_numbers=configuration_numbers,
        repetition_numbers=repetition_numbers,
    )
    if planned is None:
        return heavy, None
    runs = [*planned.runs, *heavy]
    return sorted(runs, key=lambda run: (run[0].number, run[1])), planned


def chosen_replays(
    chosen: Sequence[tuple[SpecConfiguration, int]],
) -> list[SpecConfiguration]:
    """The configurations whose replay levels a call of a spec of fixed repetitions chooses
    (interim names), in the spec's order: those with replay factors that have a run
    among the `chosen` ones. Under the precision rule the rounds choose them (`replays_due`)."""
    found: dict[str, SpecConfiguration] = {}
    for configuration, _ in chosen:
        if configuration.replay_factors:
            found.setdefault(configuration.key, configuration)
    return list(found.values())


def replays_due(
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    chosen: Sequence[tuple[SpecConfiguration, int]],
    *,
    configuration_numbers: Sequence[int] | None,
    requested: Mapping[PartId, int] | None = None,
) -> dict[str, int]:
    """The replays that a round of a spec with the precision rule makes (interim names), each by its configuration's key with the repetition that is due
    (`heavy_parts.due_repetitions`), with the repetitions `requested` for some: a selected
    configuration's replay that has not ended or has a request. A replay that has no record and no
    valid run to replay yet is made only in a round with a run of its configuration among the
    `chosen` ones, right after it: that run may be its first valid run."""
    due = due_repetitions(found, summaries, requested)
    with_a_run = {configuration.key for configuration, _ in chosen}
    planned: dict[str, int] = {}
    for configuration in found:
        repetition = due.get((REPLAY, configuration.number))
        if repetition is None or (
            configuration_numbers is not None
            and configuration.number not in configuration_numbers
        ):
            continue
        if (
            configuration.key not in with_a_run
            and first_replayed(configuration, summaries) is None
            and first_valid_run(summaries, configuration.key) is None
        ):
            continue
        planned[configuration.key] = repetition
    return planned


def replay_ends(
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    repetitions: Mapping[str, int] | None = None,
) -> dict[str, float]:
    """The factor at which the replay of each configuration with replay factors ended in earlier
    calls, by the configuration's key: the lowest factor whose level has a record that is broken
    or failed (`heavy_parts.replay_ended_at`). A broken level is the end the sweep stops the
    replay at; a failed one cannot be judged, so no higher factor is sent, as a ramp sends no
    higher rate to a level that cannot be judged. A level that a stop signal ended has not run
    yet. A configuration whose replay did not end has no entry.

    Args:
        repetitions: The repetition of each replay that counts, by the configuration's key: the
            first one where none is given.
    """
    ends: dict[str, float] = {}
    for configuration in found:
        ended = replay_ended_at(
            configuration, summaries, (repetitions or {}).get(configuration.key, 1)
        )
        if ended is not None:
            ends[configuration.key] = ended
    return ends


def plan_runs(
    spec: Spec,
    chosen: Sequence[tuple[SpecConfiguration, int]],
    *,
    done: Recorded,
    prefills: Path,
    approval: Approval | None = None,
    overridden: bool = False,
    ramp_ends: Mapping[int, Judgement] | None = None,
    found: Sequence[SpecConfiguration] = (),
    replays: Sequence[SpecConfiguration] = (),
    replay_ended: Mapping[str, float] | None = None,
    replay_repetitions: Mapping[str, int] | None = None,
) -> list[SweepRun]:
    """The chosen runs, each with its configuration resolved where it has a run to make: no
    record yet, and, for a level of a ramp, no level below it that the ramp ended at. A heavy run
    is given what covers it at its start (`HeavyCheck`): no heavy run is refused here, since the
    sweep checks each at its start and skips one that no approval covers then.

    Args:
        approval: The approval file's approval, or None where the option is off.
        overridden: Whether the call has `--set` overrides, which no approval covers.
        ramp_ends: How far each ramp went in earlier calls, by its block (`ramp.judge`).
        found: The spec's configurations, among them every level of each ramp.
        replays: The configurations whose replay levels the call plans (`chosen_replays`,
            `replays_due`): after the last chosen run of each, or at its place in the spec's order
            where it has none, a level for each of its factors, in rising order.
        replay_ended: The factor at which the replay of each configuration ended in earlier calls,
            by its key (`replay_ends`); the levels above it are skipped.
        replay_repetitions: The repetition of the replay that the call plans, by the
            configuration's key: the first one where none is given.

    Raises:
        SpecRefused: If a configuration with a run to make cannot be resolved; or if a level of
            a ramp has a run to make while a level below it has neither a record nor a run to
            make in this call, since a ramp runs its rates in rising order.
    """
    ramp_ends = ramp_ends or {}
    configurations_to_run: dict[str, Configuration] = {}
    beyond: set[str] = set()
    ramps_to_make: dict[int, int] = {}
    heavy_by_key: dict[str, HeavyCheck] = {}
    for configuration, repetition in chosen:
        if has_run(done, configuration.key, repetition):
            continue
        if _beyond_the_ramp(configuration, ramp_ends):
            beyond.add(configuration.key)
            continue
        if configuration.ramp is not None:
            ramps_to_make[configuration.ramp] = repetition
        if configuration.key not in configurations_to_run:
            resolved_configuration = resolved(
                configuration, spec=spec, prefills=prefills
            )
            if resolved_configuration.heavy:
                heavy_by_key[configuration.key] = HeavyCheck(
                    approval,
                    spec.path,
                    configuration.key,
                    resolved_configuration,
                    overridden,
                )
            _refuse_without_the_wrapper(configuration, resolved_configuration)
            configurations_to_run[configuration.key] = resolved_configuration
    for block, repetition in sorted(ramps_to_make.items()):
        _refuse_a_level_above_one_not_made(
            ramps_of(found).get(block, []),
            {key for key in configurations_to_run},
            done,
            block,
            repetition,
        )
    planned = [
        SweepRun(
            configuration,
            repetition,
            for_repetition(spec, configurations_to_run[configuration.key], repetition)
            if configuration.key in configurations_to_run
            and not has_run(done, configuration.key, repetition)
            else None,
            heavy=heavy_by_key.get(configuration.key),
            beyond_the_ramp=configuration.key in beyond
            and not has_run(done, configuration.key, repetition),
            round_number=None
            if spec.precision is None or not configuration.ruled
            else repetition,
        )
        for configuration, repetition in chosen
    ]
    if not replays:
        return planned
    levels = {
        configuration.key: _replay_levels(
            configuration,
            spec=spec,
            done=done,
            prefills=prefills,
            approval=approval,
            overridden=overridden,
            ended=(replay_ended or {}).get(configuration.key),
            repetition=(replay_repetitions or {}).get(configuration.key, 1),
        )
        for configuration in replays
    }
    last = {run.configuration.key: at for at, run in enumerate(planned)}
    # The replays of configurations without a run among the chosen ones, at their place.
    alone = sorted(
        (c for c in replays if c.key not in last), key=lambda c: c.number, reverse=True
    )
    with_levels: list[SweepRun] = []
    for at, run in enumerate(planned):
        while alone and alone[-1].number < run.configuration.number:
            with_levels += levels[alone.pop().key]
        with_levels.append(run)
        if last[run.configuration.key] == at:
            with_levels += levels.get(run.configuration.key, [])
    for configuration in reversed(alone):
        with_levels += levels[configuration.key]
    return with_levels


def _refuse_without_the_wrapper(
    configuration: SpecConfiguration, resolved_configuration: Configuration
) -> None:
    """Refuse a configuration with the wrapper where OpenTelemetry, the extra `evaluation`, is not
    installed."""
    if not resolved_configuration.wrapper:
        return
    try:
        wrapper.available()
    except wrapper.WrapperUnavailable as unavailable:
        raise SpecRefused(
            f"configuration {configuration.number} of the spec: {unavailable}"
        ) from None


def _replay_levels(
    configuration: SpecConfiguration,
    *,
    spec: Spec,
    done: Recorded,
    prefills: Path,
    approval: Approval | None,
    overridden: bool,
    ended: float | None,
    repetition: int = 1,
) -> list[SweepRun]:
    """The replay levels of `configuration`, one for each of its factors in rising order (interim
    names), of the replay's repetition `repetition`: skipped where the level has a record, or
    where the replay ended below it. A level to make is resolved, and,
    where it is heavy (a factor above 1, or a heavy configuration), given what covers it at its
    start (`HeavyCheck`): `approval.cover` for the spec and the configuration's key.

    Raises:
        SpecRefused: If the configuration cannot be resolved.
    """
    levels: list[SweepRun] = []
    resolved_configuration: Configuration | None = None
    for factor in configuration.replay_factors:
        if has_run(done, level_key(configuration.key, factor), repetition):
            levels.append(
                SweepRun(configuration, repetition, None, replay_factor=factor)
            )
            continue
        if ended is not None and factor > ended:
            levels.append(
                SweepRun(
                    configuration,
                    repetition,
                    None,
                    beyond_the_ramp=True,
                    replay_factor=factor,
                )
            )
            continue
        if resolved_configuration is None:
            resolved_configuration = resolved(
                configuration, spec=spec, prefills=prefills
            )
        heavy = None
        if resolved_configuration.heavy or is_heavy(factor):
            heavy = HeavyCheck(
                approval,
                spec.path,
                configuration.key,
                resolved_configuration,
                overridden,
                why_heavy(resolved_configuration.heavy, factor),
            )
        levels.append(
            SweepRun(
                configuration,
                repetition,
                resolved_configuration,
                heavy=heavy,
                replay_factor=factor,
            )
        )
    return levels


def _refuse_a_level_above_one_not_made(
    levels: Sequence[SpecConfiguration],
    to_make: set[str],
    done: Recorded,
    block: int,
    repetition: int = 1,
) -> None:
    """Refuse a level of the ramp of `block`, its `levels` in rising order, that has a run to make
    (`to_make`, by key) while a level below it has neither a record of the ramp's repetition
    `repetition` nor a run to make: a ramp runs its rates in rising order, and judges each before
    the next."""
    missing: list[SpecConfiguration] = []
    for level in levels:
        if level.key in to_make and missing:
            raise SpecRefused(
                f"the ramp of block {block} runs its rates in rising order: configuration "
                f"{level.number} runs only after the levels below it, and configurations "
                f"{', '.join(str(m.number) for m in missing)} have no record and are not "
                "selected. Select the ramp's levels from its lowest without a record"
            )
        if level.key not in to_make and not has_run(done, level.key, repetition):
            missing.append(level)


def _beyond_the_ramp(
    configuration: SpecConfiguration, ramp_ends: Mapping[int, Judgement]
) -> bool:
    """Whether `configuration` is a level of a ramp that ended below it."""
    if configuration.ramp is None:
        return False
    ended = ramp_ends.get(configuration.ramp)
    if ended is None or not ended.ended:
        return False
    last = ended.broken_at if ended.broken_at is not None else ended.stopped_at
    return last is not None and float(configuration.values["boxes_per_minute"]) > last


# The ends of a call (interim names), each with its own message (`Ended`).
MADE = "made"
RULE_ENDED = "no configuration open"
STOP_TIME = "stop time"
STOP_FILE_FOUND = "stop file"
RUN_NOT_COMPLETED = "a run did not complete"
FAILED_IN_A_ROW = "failed runs in a row"
CANNOT_START = "cannot start"
NOTHING_TO_MAKE = "a round has nothing to make"
# The ends at which a call has done what it was asked, so it exits with 0.
PLANNED_ENDS = frozenset({MADE, RULE_ENDED, STOP_TIME, STOP_FILE_FOUND})

# The failed runs in a row at which a call stops (interim name).
FAILED_RUNS_IN_A_ROW = 3


@dataclass(frozen=True)
class Ended:
    """How a call's runs ended (interim names): `end`, one of the ends above, and
    the message that says it; `left_levels` where a replay level was left for want of a run to
    replay, and `waiting` the replays whose levels were left so, each by its configuration's key
    and the replay's repetition (interim name); `failed` the runs of the call that
    failed, the sweep having gone on after them (interim name)."""

    end: str
    message: str = ""
    left_levels: bool = False
    waiting: frozenset[tuple[str, int]] = frozenset()
    failed: int = 0

    @property
    def completed(self) -> bool:
        """Whether every run of the call was made and completed, and no replay level was left. A
        heavy run that the call skipped is as if it was not selected."""
        return (
            self.end in (MADE, RULE_ENDED) and not self.left_levels and not self.failed
        )

    @property
    def exit_code(self) -> int:
        """0 where the call ended as it was asked: every run made, the end of the rule, the stop
        time or the stop file, with no replay level left and no run failed; else 1."""
        return (
            0
            if self.end in PLANNED_ENDS and not self.left_levels and not self.failed
            else 1
        )


# Why a heavy run or a replay level is skipped where a lower level of its ramp or replay was
# skipped in the same round, or where its replay level cannot start (interim names).
LOWER_RAMP_LEVEL_SKIPPED = "a lower level of its ramp was skipped"
LOWER_REPLAY_LEVEL_SKIPPED = "a lower level of its replay was skipped"
REPLAY_CANNOT_START = "cannot start"


@dataclass
class Unattended:
    """What a call keeps from run to run so that it runs with no person present (interim name):
    the failed runs in a row, the runs of the call that failed, the heavy runs and
    replay levels it skipped, each by what it is with why, and the configurations that it leaves
    out for the rest of the call, by their keys, since the records do not show a run of theirs
    that it made."""

    in_a_row: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    not_heavy: set[str] = field(default_factory=set)
    left_out: set[str] = field(default_factory=set)
    no_approval: bool = False

    def skip(self, what: str, why: str, *, heavy: bool = True) -> None:
        """Keep that the run `what` is skipped, with `why`: the last why of a run skipped in
        several rounds."""
        self.skipped.pop(what, None)
        self.skipped[what] = why
        if not heavy:
            self.not_heavy.add(what)

    def skip_refused(self, what: str, refused: HeavyRunRefused) -> None:
        """Keep that the heavy run `what` is skipped, since no approval covered it at its start."""
        if refused.reason == NO_APPROVAL:
            self.no_approval = True
            self.skip(what, NO_APPROVAL)
        else:
            self.skip(what, f"{refused.reason} ({refused})")

    def made(self, what: str) -> None:
        """Keep that the run `what` was made: a later round made a run that an earlier one
        skipped."""
        self.skipped.pop(what, None)
        self.not_heavy.discard(what)

    def judged(
        self, what: str, *, failed: str | None, left: str | None
    ) -> Ended | None:
        """Count the run `what` that was just made toward the failed runs in a row: where it
        `failed` (why it failed, a record the call cannot find included), or `left` its
        repository on the server (why), it counts, else it resets the count. Returns the end of
        the call where the run is the third failed run in a row, else None."""
        why = failed or left
        if why is None:
            self.in_a_row.clear()
            return None
        if failed is not None:
            self.failed.append(f"{what} ({failed})")
        self.in_a_row.append(f"{what} ({why})")
        if len(self.in_a_row) < FAILED_RUNS_IN_A_ROW:
            return None
        return Ended(
            FAILED_IN_A_ROW,
            f"{PROGRAM}: the sweep stops after {FAILED_RUNS_IN_A_ROW} failed runs in a row, so "
            "that a store that refuses every run is not sent the rest of the spec: "
            f"{'; '.join(self.in_a_row[-FAILED_RUNS_IN_A_ROW:])}. The next call skips a failed "
            "run and goes on after it",
        )

    def lines(self) -> list[str]:
        """What the call says at its end: each run it skipped, with why, and the runs of the call
        that failed."""
        lines: list[str] = []
        if self.skipped:
            heavy = not self.not_heavy
            lines.append(
                f"{PROGRAM}: skipped {len(self.skipped)} "
                + (
                    "heavy runs, which no approval covered at their start"
                    if heavy
                    else "runs"
                )
                + (
                    ""
                    if heavy
                    else ": heavy runs that no approval covered at their start, and "
                    "replay levels that could not start"
                )
                + ". A skip writes no record, so a later call makes them where an approval "
                "covers them:"
            )
            lines += [f"  - {what}: {why}" for what, why in self.skipped.items()]
            if self.no_approval:
                lines.append(
                    f"{PROGRAM}: a heavy run runs only under an approval that covers it: any "
                    "person may approve, in agreement with the admins of the server. Give the "
                    f"approval with {OPTION}: {APPROVAL_FORM}"
                )
        if self.failed:
            count = len(self.failed)
            lines.append(
                f"{PROGRAM}: {count} run{'' if count == 1 else 's'} of this call failed, and "
                f"the sweep went on after each: {'; '.join(self.failed)}"
            )
        return lines


def _run_name(run: SweepRun) -> str:
    """The run `run` for a person to read: a UC2 run by its configuration and repetition, with its
    ramp or its heavy switch; a replay level by its configuration and factor."""
    configuration = run.configuration
    if run.replay_factor is not None:
        return (
            f"the replay of configuration {configuration.number} at the factor "
            f"{run.replay_factor:g}"
            + ("" if run.repetition == 1 else f", repetition {run.repetition}")
        )
    name = f"configuration {configuration.number}, repetition {run.repetition}"
    if configuration.ramp is not None:
        rate = float(configuration.values["boxes_per_minute"])
        return f"{name} (the ramp of block {configuration.ramp} at {rate:g} boxes per minute)"
    if configuration.values["heavy"] is True:
        return f"{name} (heavy = true)"
    return name


def _not_found(runs: Path) -> str:
    """Why a run counts as failed where the call cannot find its record below `runs` right after
    it made it."""
    return (
        f"its record cannot be found below {runs}, so its configuration is left out for the "
        "rest of the call"
    )


def _gone(runs: Path) -> str:
    """Why a run counts as failed where its record was found right after it, and is gone when the
    call chooses its next round."""
    return (
        f"its record was found after the run, but cannot be found below {runs} when the next "
        "round is chosen, so its configuration is left out for the rest of the call"
    )


def _failure(
    record: Mapping[str, Any], found: bool, runs: Path
) -> tuple[str | None, str | None]:
    """Why a run just made counts toward the failed runs in a row: `failed`, where
    it did not complete or the records below `runs` do not show it, both said where both hold,
    and `left`, where it completed but left its repository on the server; each
    None where it does not."""
    deletion = record.get("repository_deletion")
    left = (
        f"completed, but its repository {record.get('repository')} was not deleted"
        if isinstance(deletion, Mapping) and deletion.get("deleted") is False
        else None
    )
    if record["outcome"] != COMPLETED:
        outcome = str(record["outcome"])
        return (outcome if found else f"{outcome}; {_not_found(runs)}"), None
    if not found:
        return _not_found(runs), None
    return None, left


def now() -> datetime:
    """The time now, of this machine, with its zone (interim name)."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class Stops:
    """Where a call stops between two runs (interim names): at its stop time, or
    where the stop file is in the runs' folder. `before_run` checks both before each run.

    Args:
        runs: The runs' folder, whose `STOP_FILE` stops the call.
        stop_time: The call's stop time, or None for none.
        clock: The time now: this machine's by default.
    """

    runs: Path
    stop_time: datetime | None = None
    clock: Callable[[], datetime] = now

    @property
    def stop_file(self) -> Path:
        """The stop file of the runs' folder."""
        return self.runs / STOP_FILE

    def before_run(self) -> Ended | None:
        """Why the call stops before its next run, or None where it goes on. A stop file that is
        there is removed, so that the next call goes on."""
        if self.stop_file.exists():
            try:
                self.stop_file.unlink()
                removed = "and removed it"
            except OSError as error:
                removed = (
                    f"and could not remove it ({error}): remove it before the next call"
                )
            return Ended(
                STOP_FILE_FOUND,
                f"{PROGRAM}: the stop file {self.stop_file} is there: the call stops before "
                f"its next run, {removed}. The next call goes on at the round that the records "
                f"show; {REMOVE_OPTION} removes configurations before it",
            )
        if self.stop_time is not None and self.clock() >= self.stop_time:
            return Ended(
                STOP_TIME,
                f"{PROGRAM}: the stop time {_shown_time(self.stop_time)} has come: the call "
                "stops before its next run. The next call goes on at the round that the records "
                "show",
            )
        return None

    def remove_left_over(self) -> str | None:
        """Remove a stop file that is still there at the end of a call that ended another way:
        it never outlives the call that it was made for. Returns what to say,
        or None where there is none."""
        if not self.stop_file.exists():
            return None
        try:
            self.stop_file.unlink()
        except OSError as error:
            return (
                f"{PROGRAM}: the stop file {self.stop_file} is still there at the end of the "
                f"call and could not be removed ({error}): remove it before the next call"
            )
        return (
            f"{PROGRAM}: the stop file {self.stop_file} is still there at the end of the call, "
            "which ended another way: it is removed, so that it does not stop the next call"
        )


def add_stop_time_option(parser: argparse.ArgumentParser, *, folder: str) -> None:
    """Add the option of a call's stop time to `parser`, of a command whose runs' folder is the
    option `folder` (interim name)."""
    parser.add_argument(
        STOP_TIME_OPTION,
        metavar="TIME",
        default=None,
        help="The stop time: a date and time with its zone, such as 2026-10-14T06:00+02:00. "
        "The call stops before its first run after it; a run that started before it runs to "
        f"its end. The call stops before its next run at the stop file too, {STOP_FILE} in "
        f"{folder}, and removes it",
    )


def add_repeat_option(parser: argparse.ArgumentParser) -> None:
    """Add the option that asks for one more repetition of heavy parts to `parser` (interim name)."""
    parser.add_argument(
        REPEAT_OPTION,
        metavar="PART:REPETITION",
        default=None,
        help="Make sure these repetitions of heavy parts exist: each part with its target "
        "repetition, ramps by their block, such as block3:2, replays and sustained loads by "
        "their configuration's number, such as 7:2 (a heavy configuration with replay factors: "
        "both). Each round makes one more repetition below the target, from its lowest level "
        "until it ends; a target that exists or is under way makes nothing, so a restart with "
        "the same option is safe. Only for a spec with [precision]",
    )


def stops_of(
    runs: Path, given: str | None, clock: Callable[[], datetime] = now
) -> Stops:
    """The stops of a call whose runs' folder is `runs`, with the stop time `given` by its option,
    or None for none (interim name).

    Raises:
        SpecRefused: As `stop_time`.
    """
    return Stops(runs, None if given is None else stop_time(given, at=clock()), clock)


def _shown_time(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def stop_time(text: str, *, at: datetime | None = None) -> datetime:
    """The stop time that `--stop-at` gives: a date and time with its zone, such as
    2026-10-14T06:00+02:00 or 2026-10-14T04:00Z (interim name).

    Raises:
        SpecRefused: If it is no date and time, names no zone, or has passed at `at` (this
            machine's time where None).
    """
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise SpecRefused(
            f"{STOP_TIME_OPTION} is a date and time that names its zone, such as "
            f"2026-10-14T06:00+02:00 or 2026-10-14T04:00Z, not {text!r}"
        )
    if moment <= (at or now()):
        raise SpecRefused(
            f"the stop time {_shown_time(moment)} has passed: give a time ahead of now"
        )
    return moment


def run_sweep(
    spec: Spec,
    runs_to_make: Sequence[SweepRun],
    *,
    runs: Path,
    run_one: RunOne,
    echo: Callable[[str], None] = print,
    found: Sequence[SpecConfiguration] = (),
    run_replay: RunReplay | None = None,
    stops: Stops | None = None,
) -> bool:
    """Make each of `runs_to_make` that is not skipped, as `make_runs` does, and say at the end
    what it skipped and which runs failed (`Unattended.lines`). Returns whether every run made
    completed, and no replay level was left for want of a run to replay."""
    unattended = Unattended()
    ended = make_runs(
        spec,
        runs_to_make,
        runs=runs,
        run_one=run_one,
        echo=echo,
        found=found,
        run_replay=run_replay,
        stops=stops,
        unattended=unattended,
    )
    for line in unattended.lines():
        echo(line)
    return ended.completed


def make_runs(
    spec: Spec,
    runs_to_make: Sequence[SweepRun],
    *,
    runs: Path,
    run_one: RunOne,
    echo: Callable[[str], None] = print,
    found: Sequence[SpecConfiguration] = (),
    run_replay: RunReplay | None = None,
    stops: Stops | None = None,
    made_runs: set[tuple[str, int]] | None = None,
    unattended: Unattended | None = None,
) -> Ended:
    """Make each of `runs_to_make` that is not skipped, one by one, and stop where `stops` says
    so before a run, at a run that a stop signal ended, or at the third failed run in a row.
    Returns how it ended (`Ended`): `MADE` where it went through every run.

    **A failed run does not stop the sweep**: it goes on, and stops after
    `FAILED_RUNS_IN_A_ROW` failed runs one after the other, so that a store that refuses every run
    is not sent the rest of the spec. A run counts as failed where it did not complete (failed,
    or failed over time), or where the records below `runs` do not show it after it was made:
    its configuration is then left out for the rest of the call. A completed run
    whose repository the store did not delete counts too, and does not reset the count; any other completed run, valid or invalid, resets it. A run that a stop signal ended
    stops the call at once.

    **A heavy run that no approval covers at its start is skipped and listed**:
    each heavy run is covered at its start (`SweepRun.heavy`, `HeavyCheck.cover`, with the time of
    `stops`), and one that is not covered then (no approval, another or a changed spec, a period
    that is not now, `--set` overrides) is skipped, with no record, and the sweep goes on with the
    next run. A skipped level of a ramp or a replay skips its higher levels in this round. A
    replay level that cannot start is skipped and listed the same way.

    After each run of a level of a ramp it judges the ramp with the broken rule (`ramp.judge`),
    from its runs below `runs` and the one just made, and where the ramp ended, it skips the
    ramp's higher levels and goes on with the runs after them.

    A replay level (`SweepRun.replay_factor`) replays the first valid UC2 run of its
    configuration below `runs` with `run_replay`; after it the sweep reads whether the level is
    broken, and where it is, it skips the configuration's higher levels. A configuration with no
    valid run to replay has its levels left, and the sweep says so.

    Args:
        found: The spec's configurations, of which a ramp's levels are judged.
        run_replay: Makes one replay level: the configured store's by default.
        stops: The call's stop time and stop file, checked before each run, and
            its clock, which a heavy run is covered at.
        made_runs: Where each UC2 run made is added, by its configuration's key and repetition.
        unattended: What the call keeps from run to run (`Unattended`): a new one by default.
    """
    unattended = unattended if unattended is not None else Unattended()
    clock = now if stops is None else stops.clock
    to_make = [run for run in runs_to_make if not run.skipped]
    ramps = ramps_of(found)
    ramp_runs: dict[int, list[RunSummary]] = {}
    ended: set[int] = set()
    skipped_ramps: set[int] = set()
    replays = _Replays()
    waiting: set[tuple[str, int]] = set()

    def stopped(end: Ended) -> Ended:
        echo(end.message)
        return replace(
            end,
            left_levels=bool(waiting),
            waiting=frozenset(waiting),
            failed=len(unattended.failed),
        )

    for made, run in enumerate(to_make, 1):
        assert run.to_run is not None
        if run.configuration.key in unattended.left_out:
            continue
        if run.replay_factor is not None:
            stop = (
                None
                if stops is None or replays.over(run.configuration.key)
                else stops.before_run()
            )
            if stop is not None:
                return stopped(stop)
            level = _replay_level(
                spec,
                run,
                position=(made, len(to_make)),
                runs=runs,
                run_replay=run_replay or run_replay_on_the_configured_store,
                found=found,
                echo=echo,
                replays=replays,
                later=[later for later in to_make[made:] if later.replay_factor],
                unattended=unattended,
                clock=clock,
            )
            if isinstance(level, Ended):
                return stopped(level)
            if not level:
                waiting.add((run.configuration.key, run.repetition))
            continue
        ramp = run.configuration.ramp
        if ramp is not None and ramp in ended:
            continue
        name = _run_name(run)
        if ramp is not None and ramp in skipped_ramps:
            unattended.skip(name, LOWER_RAMP_LEVEL_SKIPPED)
            continue
        stop = None if stops is None else stops.before_run()
        if stop is not None:
            return stopped(stop)
        try:
            covered = None if run.heavy is None else run.heavy.cover(clock())
            echo(
                f"{PROGRAM}: run {made} of {len(to_make)}: configuration "
                f"{run.configuration.name}, "
                + (
                    f"repetition {run.repetition} of {run.configuration.repetitions}"
                    if run.repetition <= run.configuration.repetitions
                    else f"repetition {run.repetition}, a repetition on request"
                )
                + ("" if run.round_number is None else f", round {run.round_number}")
            )
            record = run_one(
                run.to_run,
                runs=runs,
                sweep=run.fields(spec),
                approval=covered,
            )
        except HeavyRunRefused as refused:
            unattended.skip_refused(name, refused)
            echo(
                f"{PROGRAM}: run {made} of {len(to_make)}: {name} is heavy, and no approval "
                f"covers it at its start ({refused.reason}): it is skipped, and the sweep goes "
                "on"
            )
            if ramp is not None:
                skipped_ramps.add(ramp)
            continue
        update_the_report(spec, found, runs, record, echo=echo)
        echo(outcome_line(PROGRAM, record, runs))
        if record["outcome"] == INTERRUPTED:
            return stopped(
                Ended(
                    RUN_NOT_COMPLETED,
                    f"{PROGRAM}: the sweep stops at a run that a stop signal ended; "
                    f"{len(to_make) - made} selected runs are left. The next call makes it "
                    "again",
                )
            )
        if made_runs is not None:
            made_runs.add((run.configuration.key, run.repetition))
        unattended.made(name)
        shown = shows(runs, record, run.configuration.key, run.repetition)
        if not shown:
            unattended.left_out |= {run.configuration.key} | (
                set() if ramp is None else {level.key for level in ramps.get(ramp, [])}
            )
            if ramp is not None:
                ended.add(ramp)
        failed, left = _failure(record, shown, runs)
        three = unattended.judged(name, failed=failed, left=left)
        if three is not None:
            return stopped(three)
        if failed or left:
            echo(
                f"{PROGRAM}: failed run {len(unattended.in_a_row)} of "
                f"{FAILED_RUNS_IN_A_ROW} in a row, and the sweep goes on: {name} "
                f"({failed or left})"
            )
        if ramp is None or ramp not in ramps or not shown:
            continue
        if ramp not in ramp_runs:
            keys = {level.key for level in ramps[ramp]}
            ramp_runs[ramp] = [s for s in read_summaries(runs) if s.key in keys]
        made_summary = summary_of(record)
        ramp_runs[ramp] = [
            s for s in ramp_runs[ramp] if s.run_id != made_summary.run_id
        ] + [made_summary]
        judged = judge(
            steps_of(ramps[ramp], ramp_runs[ramp], repetition=run.repetition)
        )
        if judged.ended:
            ended.add(ramp)
            higher = sum(
                1 for later in to_make[made:] if later.configuration.ramp == ramp
            )
            echo(
                f"{PROGRAM}: the ramp of block {ramp}"
                + ("" if run.repetition == 1 else f", repetition {run.repetition},")
                + f" ends: {judged.why}. Its {higher} higher levels are not run"
            )
    return Ended(
        MADE,
        left_levels=bool(waiting),
        waiting=frozenset(waiting),
        failed=len(unattended.failed),
    )


def sweep_rounds(
    spec: Spec,
    call: Call,
    runs_to_make: Sequence[SweepRun],
    *,
    runs: Path,
    run_one: RunOne,
    plan: Callable[[Call], list[SweepRun]],
    choose: Callable[[Collection[str]], Call],
    run_replay: RunReplay | None = None,
    stops: Stops | None = None,
    echo: Callable[[str], None] = print,
) -> Ended:
    """Make the runs of `call` (`runs_to_make`, as `plan` planned them), and under the precision
    rule go on round after round (interim names), each chosen again from the
    records (`choose`, given the keys of the configurations that the call leaves out since their
    records cannot be found) and planned (`plan`), until an end: the stop time or
    the stop file (`stops`, checked before each run), a run that a stop signal ended, the third
    failed run in a row (`make_runs`), or no run left to make, where no selected configuration
    under the rule is open. A spec of fixed repetitions has one part only. Each end is told with
    `echo`, in its own message.

    A round with nothing to make ends the call (`NOTHING_TO_MAKE`, `round_without_runs`), and
    so does a later round that cannot start (`CANNOT_START`). At every end the call says each run
    it skipped and each run that failed (`Unattended.lines`), and a stop file that is still there
    is removed (`Stops.remove_left_over`), Ctrl+C included, so that it never stops the next call.
    """
    unattended = Unattended()
    try:
        ended = _rounds(
            spec,
            call,
            runs_to_make,
            runs=runs,
            run_one=run_one,
            plan=plan,
            choose=choose,
            run_replay=run_replay,
            stops=stops,
            echo=echo,
            unattended=unattended,
        )
        return replace(ended, failed=len(unattended.failed))
    finally:
        for line in unattended.lines():
            echo(line)
        left_over = None if stops is None else stops.remove_left_over()
        if left_over is not None:
            echo(left_over)


def _rounds(
    spec: Spec,
    call: Call,
    runs_to_make: Sequence[SweepRun],
    *,
    runs: Path,
    run_one: RunOne,
    plan: Callable[[Call], list[SweepRun]],
    choose: Callable[[Collection[str]], Call],
    run_replay: RunReplay | None,
    stops: Stops | None,
    echo: Callable[[str], None],
    unattended: Unattended,
) -> Ended:
    """The rounds of `sweep_rounds`, without the removal of a stop file left over. A replay left
    for want of a valid run in one round, which a later round makes, is not left at the end
    (it runs right after its configuration's first valid run, in the round that makes one)."""
    made_runs: set[tuple[str, int]] = set()
    waiting: set[tuple[str, int]] = set()

    def left() -> bool:
        return _still_waiting(waiting, call.found, runs)

    while True:
        empty = round_without_runs(call, runs_to_make, runs=runs)
        if empty is not None:
            echo(empty.message)
            return replace(empty, left_levels=left())
        if call.the_round is not None:
            echo(
                f"{PROGRAM}: round {call.the_round.number}: repetition "
                f"{call.the_round.number} of each of {len(call.the_round.runs)} open "
                "configurations"
            )
        ended = make_runs(
            spec,
            runs_to_make,
            runs=runs,
            run_one=run_one,
            echo=echo,
            found=call.found,
            run_replay=run_replay,
            stops=stops,
            made_runs=made_runs,
            unattended=unattended,
        )
        waiting |= ended.waiting
        if ended.end != MADE:
            return replace(ended, left_levels=left())
        if call.the_round is None:
            break
        chosen = _next_round(
            choose=choose,
            plan=plan,
            runs=runs,
            echo=echo,
            made_runs=made_runs,
            unattended=unattended,
        )
        if isinstance(chosen, Ended):
            return replace(chosen, left_levels=left())
        call, planned = chosen
        if planned is None:
            break
        runs_to_make = planned
    ended = Ended(_end_of(spec, call), _end_message(spec, call), left_levels=left())
    echo(ended.message)
    return ended


def _next_round(
    *,
    choose: Callable[[Collection[str]], Call],
    plan: Callable[[Call], list[SweepRun]],
    runs: Path,
    echo: Callable[[str], None],
    made_runs: set[tuple[str, int]],
    unattended: Unattended,
) -> Ended | tuple[Call, list[SweepRun] | None]:
    """The next round of a call's rounds (`sweep_rounds`): its call and its runs, chosen
    (`choose`) and planned (`plan`) without the configurations that the call leaves out; the call
    and None where no run is left to make; or the end of the call, where the next round cannot
    start (`CANNOT_START`) or at the third failed run in a row.

    The second check of a run's record: the first
    is right after each run (`make_runs`, `shows`); this one is between rounds. A run that the call
    made, whose record was found after it and is gone when the next round is chosen, would be made
    again. It counts as a failed run (`Unattended.judged`), its configuration is left out for the
    rest of the call (for a ramp's level, the whole ramp), and the round is chosen again without
    it.
    """
    while True:
        try:
            call = choose(frozenset(unattended.left_out))
            if call.the_round is None and not has_work(call):
                return call, None
            runs_to_make = plan(call)
        except (SpecRefused, HeavyRunRefused) as refused:
            message = f"{PROGRAM}: the next round cannot start: {refused}"
            echo(message)
            return Ended(CANNOT_START, message)
        gone: dict[tuple[str, int], SweepRun] = {}
        for run in runs_to_make:
            made = (run.configuration.key, run.repetition)
            if not run.skipped and run.replay_factor is None and made in made_runs:
                gone.setdefault(made, run)
        if not gone:
            return call, runs_to_make
        ramps = ramps_of(call.found)
        for run in gone.values():
            ramp = run.configuration.ramp
            unattended.left_out |= {run.configuration.key} | (
                set() if ramp is None else {level.key for level in ramps.get(ramp, [])}
            )
            name = _run_name(run)
            three = unattended.judged(name, failed=_gone(runs), left=None)
            if three is not None:
                echo(three.message)
                return three
            echo(
                f"{PROGRAM}: failed run {len(unattended.in_a_row)} of "
                f"{FAILED_RUNS_IN_A_ROW} in a row, and the sweep goes on: {name} "
                f"({_gone(runs)})"
            )


def _still_waiting(
    waiting: set[tuple[str, int]], found: Sequence[SpecConfiguration], runs: Path
) -> bool:
    """Whether a replay among those whose levels a round left for want of a run to replay, each
    by its configuration's key and the replay's repetition (`waiting`), has still not ended that
    repetition by the records below `runs` (`heavy_parts.standing_of`): its levels are still
    left, so the exit code says what the call said."""
    if not waiting:
        return False
    summaries = read_summaries(runs)
    for configuration in found:
        for key, repetition in waiting:
            if key != configuration.key:
                continue
            standing = standing_of(
                HeavyPart(REPLAY, configuration.number, (configuration,)), summaries
            )
            if standing.last < repetition or (
                standing.last == repetition and not standing.ended
            ):
                return True
    return False


def round_without_runs(
    call: Call, runs_to_make: Sequence[SweepRun], *, runs: Path
) -> Ended | None:
    """The end of a call whose round has nothing to make, or None: the round
    plans runs that the rule does not count, but whose records the skip finds, so none is made.
    That happens where a record below `runs` names its run but its summary cannot be made; a
    person repairs the record or moves it out of the runs' folder."""
    if call.the_round is None:
        return None
    if any(not run.skipped and run.round_number is not None for run in runs_to_make):
        return None
    blocked = ", ".join(
        f"configuration {configuration.number} repetition {repetition}"
        for configuration, repetition in call.the_round.runs
    )
    return Ended(
        NOTHING_TO_MAKE,
        f"{PROGRAM}: the call stops: round {call.the_round.number} has nothing to make. The "
        f"records below {runs} name its runs ({blocked}), but the precision rule cannot read "
        "them; repair them or move them out of the runs' folder",
    )


def _end_of(spec: Spec, call: Call) -> str:
    """`RULE_ENDED` where the call made every run it could under the precision rule and no
    selected configuration under it is open, else `MADE`."""
    if (
        spec.precision is not None
        and not call.open
        and any(c.ruled for c in call.selected)
    ):
        return RULE_ENDED
    return MADE


def _end_message(spec: Spec, call: Call) -> str:
    """What a call says where it made every run it could: under the precision rule, that no
    selected configuration is open, or that every selected repetition has a record."""
    rule = spec.precision
    if rule is None or not any(c.ruled for c in call.selected):
        return f"{PROGRAM}: the call ends: every selected run has its record"
    if call.open and call.repetition_numbers is not None:
        return (
            f"{PROGRAM}: the call ends: every selected repetition of the open configurations "
            f"has a record ({len(call.open)} open configurations under the precision rule)"
        )
    return (
        f"{PROGRAM}: the call ends: no selected configuration under the precision rule is "
        f"open; each meets the rule, has made {rule.maximum_runs} runs or stopped at a "
        "throughput median of 0"
    )


@dataclass
class _Replays:
    """What one round knows of its replays, each by its configuration's key: `ended`, a replay
    that broke, failed or has no run to replay, so its higher levels are not sent; `skipped`, a
    replay whose level was skipped, so its higher levels are skipped too; and
    `sources`, the run each replays, found at its first level."""

    ended: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    sources: dict[str, RunSummary | None] = field(default_factory=dict)

    def over(self, key: str) -> bool:
        """Whether the replay of the configuration of `key` sends no more level in this round."""
        return key in self.ended or key in self.skipped


def _replay_level(
    spec: Spec,
    run: SweepRun,
    *,
    position: tuple[int, int],
    runs: Path,
    run_replay: RunReplay,
    found: Sequence[SpecConfiguration],
    echo: Callable[[str], None],
    replays: _Replays,
    later: Sequence[SweepRun],
    unattended: Unattended,
    clock: Callable[[], datetime],
) -> bool | Ended:
    """Make the replay level `run` of the sweep: the baseline of the first valid
    UC2 run of its configuration at its factor. Returns how the sweep stops where it stops
    (`Ended`), False where the level was left for want of a run to replay, else True.

    A heavy level that no approval covers at its start, and a level that cannot start, is skipped
    and listed (`unattended`), and so are the replay's higher levels in this round; a failed
    level ends the replay and counts toward the failed runs in a row.

    Args:
        position: This run's place among the sweep's runs to make, and their number.
        replays: What the round knows of its replays (`_Replays`).
        later: The replay levels still to make after this one.
        unattended: What the call keeps from run to run (`Unattended`).
        clock: The time now, at which a heavy level is covered.
    """
    made, total = position
    configuration = run.configuration
    factor = run.replay_factor
    assert factor is not None
    name = _run_name(run)
    if configuration.key in replays.ended:
        return True
    if configuration.key in replays.skipped:
        unattended.skip(name, LOWER_REPLAY_LEVEL_SKIPPED, heavy=run.heavy is not None)
        return True
    named: str | None = None
    if configuration.key not in replays.sources:
        summaries = read_summaries(runs)
        # Under the precision rule every repetition replays the run of the first one;
        # a spec of fixed repetitions replays the first valid run, as before.
        named = (
            None if spec.precision is None else first_replayed(configuration, summaries)
        )
        replays.sources[configuration.key] = (
            first_valid_run(summaries, configuration.key)
            if named is None
            else next((s for s in summaries if s.run_id == named), None)
        )
    source = replays.sources[configuration.key]
    if source is None:
        replays.ended.add(configuration.key)
        echo(
            f"{PROGRAM}: run {made} of {total}: configuration {configuration.name} has no "
            "valid run of use case 2 to replay, so its replay levels are not run. A later call "
            "replays it once a valid run has a record"
            if named is None
            else f"{PROGRAM}: run {made} of {total}: the run {named}, which the first "
            f"repetition of the replay of configuration {configuration.name} replayed, has no "
            f"record below {runs}, so its replay levels are not run"
        )
        return False
    try:
        covered = None if run.heavy is None else run.heavy.cover(clock())
        echo(
            f"{PROGRAM}: run {made} of {total}: configuration {configuration.name}, replay of "
            f"the run {source.run_id} at the factor {factor:g}"
            + ("" if run.repetition == 1 else f", repetition {run.repetition}")
        )
        record = run_replay(
            runs / source.run_id / "record.json",
            runs=runs,
            factor=factor,
            sweep=run.fields(spec),
            approval=covered,
        )
    except HeavyRunRefused as refused:
        replays.skipped.add(configuration.key)
        unattended.skip_refused(name, refused)
        echo(
            f"{PROGRAM}: run {made} of {total}: {name} is heavy, and no approval covers it at "
            f"its start ({refused.reason}): it and its higher levels are skipped, and the sweep "
            "goes on"
        )
        return True
    except (BaselineRefused, ConfigurationRefused) as refused:
        replays.skipped.add(configuration.key)
        unattended.skip(
            name, f"{REPLAY_CANNOT_START} ({refused})", heavy=run.heavy is not None
        )
        echo(
            f"{PROGRAM}: run {made} of {total}: {name} cannot start ({refused}): it and its "
            "higher levels are skipped, and the sweep goes on; nothing reached a store for it"
        )
        return True
    update_the_report(spec, found, runs, record, echo=echo)
    echo(outcome_line(PROGRAM, record, runs))
    if record["outcome"] == INTERRUPTED:
        return Ended(
            RUN_NOT_COMPLETED,
            f"{PROGRAM}: the sweep stops at a run that a stop signal ended; {total - made} "
            "selected runs are left. The next call makes it again",
        )
    unattended.made(name)
    shown = shows(runs, record, level_key(configuration.key, factor), run.repetition)
    if not shown:
        unattended.left_out.add(configuration.key)
    failed, left = _failure(record, shown, runs)
    if failed is not None:
        # A level that cannot be judged sends no higher factor (`replay_ended_at`).
        replays.ended.add(configuration.key)
    three = unattended.judged(name, failed=failed, left=left)
    if three is not None:
        return three
    if failed or left:
        echo(
            f"{PROGRAM}: failed run {len(unattended.in_a_row)} of {FAILED_RUNS_IN_A_ROW} in "
            f"a row, and the sweep goes on: {name} ({failed or left})"
            + ("" if failed is None else "; no higher factor of its replay is sent")
        )
    level = record.get(RECORD_KEY) or {}
    if failed is None and level.get("broken"):
        replays.ended.add(configuration.key)
        higher = sum(
            1 for other in later if other.configuration.key == configuration.key
        )
        echo(
            f"{PROGRAM}: the replay of configuration {configuration.name} is broken at the "
            f"factor {factor:g} ({signal_names(level)}); its {higher} higher levels are not "
            "run"
        )
    return True


def run_replay_on_the_configured_store(
    recorded: Path,
    *,
    runs: Path,
    factor: float,
    sweep: dict[str, Any],
    approval: Covered | None = None,
) -> dict[str, Any]:
    """One replay level of the recorded UC2 run whose record is at `recorded`, on the configured
    GraphDB: the store-only baseline at `factor` (`baseline.run_on_the_configured_store`), its
    record in a folder of its own below `runs`.

    Raises:
        BaselineRefused: If the recorded run cannot be replayed.
        ConfigurationRefused: If its configuration does not resolve.
        HeavyRunRefused: If the level is heavy and `approval` does not cover it.
    """
    return run_baseline_on_the_configured_store(
        read_recorded_run(recorded),
        runs=runs,
        approval=approval,
        factor=factor,
        sweep=sweep,
    )


@dataclass(frozen=True)
class Call:
    """What one call of the sweep chooses next (interim names; `choose_call`).

    `chosen` are the runs it makes or skips, in the spec's order, and `replays` the configurations
    whose replay levels it plans, with the factors it plans. `the_round` is the round of the
    precision rule it makes next, or None. `done` and `summaries` are what the
    records below the runs' folder say. A call that withholds the heavy parts holds them apart:
    `withheld` are the heavy runs, each a configuration with its repetition, and
    `withheld_levels` the replay levels above the factor 1 and those of a heavy configuration,
    each a configuration with its factor; both only those without a record. `open` are the
    selected configurations under the rule that are open, which later rounds of the call make,
    and `configuration_numbers` and `repetition_numbers` the call's selection, None for all.
    `removed` are the removed configurations by their keys (`read_removed`), which the call
    leaves out, so `configuration_numbers` leaves them out too. `replay_repetitions` holds the
    repetition of each replay it plans, by the configuration's key, where it is not the first.
    """

    found: list[SpecConfiguration]
    chosen: list[tuple[SpecConfiguration, int]]
    replays: list[SpecConfiguration]
    the_round: Round | None
    done: Recorded
    summaries: list[RunSummary]
    withheld: list[tuple[SpecConfiguration, int]] = field(default_factory=list)
    withheld_levels: list[tuple[SpecConfiguration, float]] = field(default_factory=list)
    open: list[SpecConfiguration] = field(default_factory=list)
    configuration_numbers: tuple[int, ...] | None = None
    repetition_numbers: tuple[int, ...] | None = None
    removed: Removed = field(default_factory=dict)
    replay_repetitions: dict[str, int] = field(default_factory=dict)

    def replay_repetition(self, configuration: SpecConfiguration) -> int:
        """The repetition of the replay of `configuration` that the call plans."""
        return self.replay_repetitions.get(configuration.key, 1)

    @property
    def selected(self) -> list[SpecConfiguration]:
        """The configurations the call selects, in the spec's order."""
        return [
            c
            for c in self.found
            if self.configuration_numbers is None
            or c.number in self.configuration_numbers
        ]


def has_work(call: Call) -> bool:
    """Whether the call has a run or a replay level without a record to make."""
    return any(not has_run(call.done, c.key, r) for c, r in call.chosen) or any(
        not has_run(call.done, level_key(c.key, factor), call.replay_repetition(c))
        for c in call.replays
        for factor in c.replay_factors
    )


def progress(
    spec: Spec, found: Sequence[SpecConfiguration], runs: Path
) -> tuple[Recorded, list[RunSummary]]:
    """What the records below `runs` say of a sweep of `spec`: its runs that have a record, and
    the summaries of the records where the precision rule, a ramp or a replay reads them."""
    summaries = (
        read_summaries(runs)
        if spec.precision is not None
        or any(c.ramp is not None or c.replay_factors for c in found)
        else []
    )
    return recorded(runs), list(summaries)


def choose_call(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    runs: Path,
    *,
    configuration_numbers: Sequence[int] | None = None,
    repetition_numbers: Sequence[int] | None = None,
    withhold_heavy: bool = False,
    requested: Mapping[PartId, int] | None = None,
    left_out: Collection[str] = (),
) -> Call:
    """The runs and replay levels that a call chooses (`chosen_runs`, `chosen_replays`, under the
    precision rule `replays_due`), from the records below `runs`, with the repetitions of heavy
    parts `requested` (`heavy_parts.requested_repetitions`). Where
    `withhold_heavy`, the heavy ones (a heavy configuration, a ramp's level, a replay level above
    1) are left out of `chosen` and `replays` and listed in `withheld` and `withheld_levels`: what
    a call without an approval can run. A configuration that is removed (`read_removed`) is left
    out of the selection, whatever it names, and so is one whose key is in `left_out`: the call
    left it out for its rest, since the records do not show a run of it that the call made.

    Raises:
        SpecRefused: If a configuration selected is not one of the spec's, or the removed
            configurations cannot be read.
    """
    if configuration_numbers is not None:
        selected(
            found, configuration_numbers=configuration_numbers, repetition_numbers=None
        )
    removed = read_removed(runs)
    gone = set(removed) | set(left_out)
    if any(c.key in gone for c in found):
        configuration_numbers = [
            c.number
            for c in found
            if c.key not in gone
            and (configuration_numbers is None or c.number in configuration_numbers)
        ]
    done, summaries = progress(spec, found, runs)
    chosen, the_round = chosen_runs(
        spec,
        found,
        summaries,
        configuration_numbers=configuration_numbers,
        repetition_numbers=repetition_numbers,
        requested=requested,
    )
    replay_repetitions: dict[str, int] = {}
    if spec.precision is None:
        replays = chosen_replays(chosen)
    else:
        replay_repetitions = replays_due(
            found,
            summaries,
            chosen,
            configuration_numbers=configuration_numbers,
            requested=requested,
        )
        replays = [c for c in found if c.key in replay_repetitions]
    withheld: list[tuple[SpecConfiguration, int]] = []
    withheld_levels: list[tuple[SpecConfiguration, float]] = []
    if withhold_heavy:
        withheld = [
            (c, r)
            for c, r in chosen
            if c.values["heavy"] is True and not has_run(done, c.key, r)
        ]
        chosen = [(c, r) for c, r in chosen if c.values["heavy"] is not True]
        light: list[SpecConfiguration] = []
        for configuration in replays:
            heavy = configuration.values["heavy"] is True
            for factor in configuration.replay_factors:
                if (heavy or is_heavy(factor)) and not has_run(
                    done,
                    level_key(configuration.key, factor),
                    replay_repetitions.get(configuration.key, 1),
                ):
                    withheld_levels.append((configuration, factor))
            kept = tuple(
                factor
                for factor in configuration.replay_factors
                if not heavy and not is_heavy(factor)
            )
            if kept:
                light.append(replace(configuration, replay_factors=kept))
        replays = light
    return Call(
        list(found),
        chosen,
        replays,
        the_round,
        done,
        summaries,
        withheld,
        withheld_levels,
        open=[]
        if spec.precision is None
        else open_configurations(
            found,
            spec.precision,
            summaries,
            configuration_numbers=configuration_numbers,
        ),
        configuration_numbers=None
        if configuration_numbers is None
        else tuple(configuration_numbers),
        repetition_numbers=None
        if repetition_numbers is None
        else tuple(repetition_numbers),
        removed=removed,
        replay_repetitions={k: r for k, r in replay_repetitions.items() if r != 1},
    )


def repetitions_on_request(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    runs: Path,
    text: str,
    *,
    configuration_numbers: Sequence[int] | None = None,
    repetition_numbers: Sequence[int] | None = None,
) -> tuple[dict[PartId, int], list[str]]:
    """The target repetitions of heavy parts that `text`, the value of `REPEAT_OPTION`, gives
    (`heavy_parts.requested_repetitions`, interim name), and what the call says of
    them, from the records below `runs`. Every round of the call makes the next repetition below
    each target (`heavy_parts.Standing.after`); a target that exists or is under way makes
    nothing, so a restart with the same command line is safe. A removed part is left out. A part
    whose next repetition `repetition_numbers` does not select makes nothing in this call, which
    says so in one line and goes on, with no refusal.

    Raises:
        SpecRefused: As `requested_repetitions`; or if the spec has no precision rule, whose
            rounds make the heavy parts, or the call's selection, `configuration_numbers` or
            `repetition_numbers`, does not select a part or a target repetition.
    """
    if spec.precision is None:
        raise SpecRefused(
            f"{REPEAT_OPTION} asks for a repetition of a heavy part in the rounds of a spec with "
            "a [precision] table; a spec of fixed repetitions gives each configuration its "
            "repetitions"
        )
    targets, left_out = requested_repetitions(
        found, text, removed=tuple(read_removed(runs))
    )
    summaries = read_summaries(runs)
    said = [
        f"{PROGRAM}: on request: {part.name} is removed, and is left out"
        for part in left_out
    ]
    for part in heavy_parts(found):
        target = targets.get(part.id)
        if target is None:
            continue
        if repetition_numbers is not None and target not in repetition_numbers:
            raise SpecRefused(
                f"{REPEAT_OPTION} asks for repetition {target} of {part.name}, which "
                "--repetitions does not select"
            )
        if configuration_numbers is not None and not all(
            c.number in configuration_numbers for c in part.configurations
        ):
            raise SpecRefused(
                f"{REPEAT_OPTION} asks for repetition {target} of {part.name}, which "
                "--configurations does not select"
            )
        standing = standing_of(part, summaries)
        if standing.last > target or (standing.last == target and standing.ended):
            said.append(
                f"{PROGRAM}: on request: {part.name}: repetition {target} exists, so nothing "
                "more is made"
            )
            continue
        if standing.last == target:
            said.append(
                f"{PROGRAM}: on request: {part.name}: repetition {target} is under way, and "
                "the next round goes on with it"
            )
            continue
        lowest = (
            "rate" if part.kind == RAMP else "factor" if part.kind == REPLAY else "run"
        )
        upcoming = standing.after(target)
        if (
            repetition_numbers is not None
            and upcoming is not None
            and upcoming not in repetition_numbers
        ):
            # The next round would make a repetition that --repetitions leaves out: nothing is
            # made for the part, and the call goes on, so that a restart with the same line
            # stays safe.
            del targets[part.id]
            said.append(
                f"{PROGRAM}: on request: {part.name}: the next round would make repetition "
                f"{upcoming}, which --repetitions does not select, so nothing is made for it "
                "in this call"
            )
            continue
        said.append(
            f"{PROGRAM}: on request: {part.name}: repetition {upcoming} next, from its lowest "
            f"{lowest} until it ends, at its place in the spec's order"
            + (
                ""
                if upcoming == target
                else f"; then one more in each round until repetition {target} exists"
            )
        )
    return targets, said


def plan_call(
    spec: Spec,
    call: Call,
    *,
    prefills: Path,
    approval: Approval | None = None,
    overridden: bool = False,
) -> list[SweepRun]:
    """The runs of `call`, each with its configuration resolved where it has a run to make
    (`plan_runs`), with the ramps' and replays' ends that earlier calls reached, each of the
    repetition the call makes. Each open
    configuration under the precision rule is resolved too (`Call.open`), so that a file or a
    strategy that a later round needs is refused before the first run.

    Raises:
        SpecRefused: As `plan_runs`; or if an open configuration cannot be resolved, or has the
            wrapper where OpenTelemetry is not installed.
    """
    for configuration in call.open:
        _refuse_without_the_wrapper(
            configuration, resolved(configuration, spec=spec, prefills=prefills)
        )
    ramps = ramps_of(call.found)
    ramp_ends = {
        configuration.ramp: judge(
            steps_of(ramps[configuration.ramp], call.summaries, repetition=repetition)
        )
        for configuration, repetition in call.chosen
        if configuration.ramp is not None
    }
    return plan_runs(
        spec,
        call.chosen,
        done=call.done,
        prefills=prefills,
        approval=approval,
        overridden=overridden,
        ramp_ends=ramp_ends,
        found=call.found,
        replays=call.replays,
        replay_ended=replay_ends(
            call.found,
            call.summaries,
            {c.key: call.replay_repetition(c) for c in call.replays},
        ),
        replay_repetitions=call.replay_repetitions,
    )


def update_the_report(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    runs: Path,
    record: Mapping[str, Any],
    *,
    echo: Callable[[str], None] = print,
    **options: Any,
) -> None:
    """Make the report of the runs below `runs` again (`report.update_report`), with the run just
    made, `record`: called right after a run returns, whatever its end, and before the sweep
    judges that end. An error of the report is told with `echo` and goes no further: it never
    stops or fails the sweep. The report is imported here, so that a sweep without pandas or
    matplotlib still runs.

    Args:
        found: The spec's configurations with the sweep's overrides; the spec's own where empty.
    """
    try:
        from jms_kapps.evaluation import report

        report.update_report(
            spec, found or configurations(spec), runs, fresh=record, **options
        )
    except Exception as error:  # noqa: BLE001 - a report never stops a sweep
        echo(
            f"{PROGRAM}: the report of the runs could not be made, and the sweep goes on: "
            f"{type(error).__name__}: {error}"
        )


def listing(
    found: Sequence[SpecConfiguration],
    done: Recorded,
    spec: Spec | None = None,
    summaries: Sequence[RunSummary] = (),
    removed: Removed | None = None,
) -> list[str]:
    """The lines `--list` shows: each configuration with its values, and its runs that have a
    record, each with its outcome. For a spec with the precision rule (`spec`,
    interim names) also the rounds, the round the next call starts at, and for
    each configuration under the rule its runs made, its valid runs and whether it is open (from
    the `summaries` of the records). A configuration in `removed` (`read_removed`) shows that it
    is removed, and the next round leaves it out."""
    removed = removed or {}
    rule = None if spec is None else spec.precision
    ruled = [c for c in found if c.ruled]
    runs = [(c.key, r) for c in found for r in range(1, c.repetitions + 1)]
    with_records = sum(run in done for run in runs)
    to_make_again = sum(done.get(run) == INTERRUPTED for run in runs)
    if rule is None or not ruled:
        lines = [
            (
                f"{len(found)} configurations, {len(runs)} runs, {with_records} with a "
                f"record, {to_make_again} of them interrupted, which run again"
            )
        ]
    else:
        lines = [
            (
                f"{len(found)} configurations, {len(ruled)} of them under the precision rule, "
                f"{len(runs) - sum(c.repetitions for c in ruled)} fixed runs and up to "
                f"{rule.maximum_runs} for each under the rule, {with_records} with a record, "
                f"{to_make_again} of them interrupted, which run again"
            ),
            (
                f"precision rule: at least {rule.minimum_runs} valid runs, the bootstrap 95 % "
                f"interval of the commit p95 within {rule.latency_tolerance:.0%} of it, the IQR "
                f"of the transports ended per minute at most {rule.throughput_spread:.0%} of "
                "their median (a configuration without the wrapper: the throughput alone); "
                f"at most {rule.maximum_runs} runs"
            ),
        ]
        lines += _round_lines(
            [c for c in found if c.key not in removed], rule, summaries
        )
        lines += _heavy_lines(found, summaries, removed)
    for configuration in found:
        with_record = [
            f"{repetition} {done[(configuration.key, repetition)].split(':')[0]}"
            for repetition in range(1, configuration.repetitions + 1)
            if (configuration.key, repetition) in done
        ]
        values = ", ".join(
            f"{key} = {_shown(value)}" for key, value in configuration.values.items()
        )
        ramp = ", a level of its ramp" if configuration.ramp is not None else ""
        if configuration.ruled and rule is not None:
            verdict = verdict_of(configuration, summaries, rule)
            if configuration.key in removed:
                since = removed[configuration.key].get("removed_at")
                standing = (
                    "removed"
                    + ("" if since is None else f" since {since}")
                    + f" ({PUT_BACK_OPTION} {configuration.number} puts it back)"
                )
            elif is_open(configuration, summaries, rule, verdict=verdict):
                standing = "open"
            else:
                standing = f"not open: {verdict.status}"
            lines.append(
                f"configuration {configuration.name} (block {configuration.block}), under the "
                f"precision rule, up to {configuration.repetitions} runs: {verdict.made} made, "
                f"{verdict.valid} valid, {standing}, with a record: "
                f"{', '.join(map(str, with_record)) or 'none'}: {values}"
            )
            lines += _replay_lines(configuration, done)
            continue
        gone = (
            f", removed ({PUT_BACK_OPTION} {configuration.number} puts it back)"
            if configuration.key in removed
            else ""
        )
        lines.append(
            f"configuration {configuration.name} (block {configuration.block}{ramp}), "
            f"{configuration.repetitions} repetitions{gone}, with a record: "
            f"{', '.join(map(str, with_record)) or 'none'}: {values}"
        )
        lines += _replay_lines(configuration, done)
    return lines


def _replay_lines(configuration: SpecConfiguration, done: Recorded) -> list[str]:
    """The line that `--list` shows for the replay levels of `configuration`: each
    factor in rising order, with whether it is heavy and its record. None for a configuration
    without factors."""
    if not configuration.replay_factors:
        return []
    levels = []
    for factor in configuration.replay_factors:
        outcome = done.get((level_key(configuration.key, factor), 1))
        record = "no record" if outcome is None else outcome.split(":")[0]
        load = "heavy" if is_heavy(factor) else "normal load"
        levels.append(f"{factor:g} ({load}, {record})")
    return [
        (
            f"  replay levels of configuration {configuration.name}, each a replay of its "
            f"first valid UC2 run, in rising order, after its runs: {', '.join(levels)}"
        )
    ]


def _heavy_lines(
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    removed: Removed,
) -> list[str]:
    """Where each heavy part of a spec with the precision rule stands, and what the next round
    makes of it (interim): none for a spec without one. A removed one is left
    out."""
    parts = [
        part
        for part in heavy_parts(found)
        if not any(c.key in removed for c in part.configurations)
    ]
    if not parts:
        return []
    lines = [
        (
            "heavy parts: each ramp, replay and sustained load runs one time, in round 1, at its "
            "place in the spec's order (a replay right after its configuration's first valid "
            f"run), and again on request ({REPEAT_OPTION} <part>:<repetition>)"
        )
    ]
    for part in parts:
        standing = standing_of(part, summaries)
        if standing.waiting:
            state = (
                f"{standing.why}; a round with a run of configuration {part.number} makes "
                "its repetition 1 right after that run"
            )
        elif standing.last == 0:
            state = "no record yet; the next round makes its repetition 1"
        elif standing.ended:
            state = (
                f"repetition {standing.last} ended: {standing.why}; {REPEAT_OPTION} "
                f"{part.handle}:{standing.last + 1} asks for repetition {standing.last + 1}"
            )
        else:
            state = (
                f"repetition {standing.last} has not ended ({standing.why}); the next round "
                "goes on with it, from its lowest level without a record"
            )
        lines.append(f"  {part.name} ({part.handle}): {state}")
    return lines


def _round_lines(
    found: Sequence[SpecConfiguration],
    rule: PrecisionRule,
    summaries: Sequence[RunSummary],
) -> list[str]:
    """What the rounds of the rule make, and the round the next call starts at."""
    following = next_round(found, rule, summaries)
    return [
        (
            "rounds: round k makes repetition k of each open configuration under the rule, in "
            f"the spec's order, up to round {rule.maximum_runs}; a configuration is open while "
            f"it does not meet the rule and has made fewer than {rule.maximum_runs} runs; one "
            f"whose throughput median is 0 once it has {rule.minimum_runs} valid runs stops "
            "there"
        ),
        "no round is left: every configuration under the rule meets it, has made "
        f"{rule.maximum_runs} runs or stopped at a throughput median of 0"
        if following is None
        else f"the next call starts at round {following.number}: {len(following.runs)} runs "
        f"of {len(following.runs)} open configurations",
    ]


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def main(argv: list[str] | None = None, *, clock: Callable[[], datetime] = now) -> int:
    """Run the command as the module docstring says; returns its exit code.

    Args:
        clock: The time now, which the stop time and the approval of each heavy run at its start
            are checked against: this machine's by default.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="The runs of a spec, one by one, each a UC2 run of one configuration on its "
        "own repository of the configured GraphDB, with its record (the measurement harness, "
        "iteration one). A run whose record exists is skipped.",
    )
    parser.add_argument("spec", type=Path, help="The spec, TOML: its sweep blocks")
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("runs"),
        help="The folder of the runs' folders, where a run's record is found again "
        "(default: runs)",
    )
    parser.add_argument(
        "--prefills",
        type=Path,
        default=Path("prefills"),
        help="The folder from which a prefill file that the spec names is read "
        "(default: prefills)",
    )
    parser.add_argument(
        "--configurations",
        metavar="NUMBERS",
        help="Only these configurations, by their numbers in the spec's order, such as 1-9,12 "
        "(default: all)",
    )
    parser.add_argument(
        "--repetitions",
        metavar="NUMBERS",
        help="Only these repetitions of each configuration, by number, such as 1-3 "
        "(default: all)",
    )
    parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        help="A value of every configuration, over the spec's: a key of a configuration, or "
        "repetitions. May be given more than once. No approval covers a heavy run with it",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show the spec's configurations and which of their runs have a record, and run "
        "nothing",
    )
    parser.add_argument(
        "--heavy-run-approval",
        metavar="TEXT",
        default=None,
        type=Path,
        help="The approval of the heavy runs (heavy = true) of this spec, which any person may "
        "give, in agreement with the server's admins, for one spec and one period: "
        f"{APPROVAL_FORM}. Off by default; each heavy run's record keeps it",
    )
    add_stop_time_option(parser, folder="--runs")
    add_repeat_option(parser)
    parser.add_argument(
        REMOVE_OPTION,
        metavar="NUMBERS",
        default=None,
        help=f"Remove these configurations, such as 3,7-9: {REMOVED_FILE} in --runs keeps "
        "their keys, and no later call of this command or of jms-eval plans them, whatever it "
        f"selects, until {PUT_BACK_OPTION}. Runs nothing",
    )
    parser.add_argument(
        PUT_BACK_OPTION,
        metavar="NUMBERS",
        default=None,
        help="Put back these removed configurations, such as 3: the next call plans them "
        "again. Runs nothing",
    )
    args = parser.parse_args(argv)
    try:
        spec = read_spec(args.spec)
        found = configurations(spec, overrides=dict(map(setting, args.set)))
        if args.list:
            done, summaries = progress(spec, found, args.runs)
            for line in listing(found, done, spec, summaries, read_removed(args.runs)):
                print(line)
            return 0
        if args.remove is not None or args.put_back is not None:
            for line in change_removed(
                spec,
                found,
                args.runs,
                remove=() if args.remove is None else numbers(args.remove),
                put_back=() if args.put_back is None else numbers(args.put_back),
                at=clock(),
            ):
                print(line)
            return 0
        stops = stops_of(args.runs, args.stop_at, clock)
        configuration_numbers = (
            None if args.configurations is None else numbers(args.configurations)
        )
        repetition_numbers = (
            None if args.repetitions is None else numbers(args.repetitions)
        )
        requested, on_request = (
            ({}, [])
            if args.repeat is None
            else repetitions_on_request(
                spec,
                found,
                args.runs,
                args.repeat,
                configuration_numbers=configuration_numbers,
                repetition_numbers=repetition_numbers,
            )
        )

        def choose(left_out: Collection[str] = ()) -> Call:
            return choose_call(
                spec,
                found,
                args.runs,
                configuration_numbers=configuration_numbers,
                repetition_numbers=repetition_numbers,
                requested=requested,
                left_out=left_out,
            )

        call = choose()
        approval = load_approval(args.heavy_run_approval)

        def plan(chosen: Call) -> list[SweepRun]:
            return plan_call(
                spec,
                chosen,
                prefills=args.prefills,
                approval=approval,
                overridden=bool(args.set),
            )

        runs_to_make = plan(call)
    except (SpecRefused, HeavyRunRefused) as refused:
        print(f"{PROGRAM}: {refused}", file=sys.stderr)
        return 2
    skipped = sum(run.skipped for run in runs_to_make)
    beyond = sum(
        run.beyond_the_ramp and run.replay_factor is None for run in runs_to_make
    )
    beyond_replay = sum(
        run.beyond_the_ramp and run.replay_factor is not None for run in runs_to_make
    )
    levels = sum(len(c.replay_factors) for c in found)
    gone = removed_line(call, args.runs)
    if gone is not None:
        print(gone)
    for line in on_request:
        print(line)
    if spec.precision is not None:
        print(f"{PROGRAM}: precision rule: {_rounds_line(call, stops)}")
    print(
        f"{PROGRAM}: {args.spec}: {len(found)} configurations, "
        f"{sum(c.repetitions for c in found)} runs"
        + (f" and {levels} replay levels" if levels else "")
        + f"; {len(runs_to_make)} selected, "
        f"{skipped - beyond - beyond_replay} have a record and are skipped, "
        + (f"{beyond} are above the level their ramp ended at, " if beyond else "")
        + (
            f"{beyond_replay} are above the level their replay ended at, "
            if beyond_replay
            else ""
        )
        + f"{len(runs_to_make) - skipped} to make"
    )
    empty = round_without_runs(call, runs_to_make, runs=args.runs)
    if empty is not None:
        print(empty.message)
        return empty.exit_code
    if skipped == len(runs_to_make):
        print(_end_message(spec, call))
        return 0
    missing = missing_store_variables()
    if missing:
        print(
            f"{PROGRAM}: no GraphDB is configured: set {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    try:
        ended = sweep_rounds(
            spec,
            call,
            runs_to_make,
            runs=args.runs,
            run_one=run_on_the_configured_store,
            plan=plan,
            choose=choose,
            run_replay=run_replay_on_the_configured_store,
            stops=stops,
        )
    except KeyboardInterrupt:
        print(f"{PROGRAM}: stopped between two runs", file=sys.stderr)
        return 1
    return ended.exit_code


def _rounds_line(call: Call, stops: Stops) -> str:
    """What the call says of its rounds before its first run."""
    ends = [f"the stop file {stops.stop_file}"]
    if stops.stop_time is not None:
        ends.insert(0, f"the stop time {_shown_time(stops.stop_time)}")
    if call.the_round is None:
        return "no round is left to make"
    return (
        f"this call starts at round {call.the_round.number} and goes on round after round "
        f"until no selected configuration is open, or until {' or '.join(ends)}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
