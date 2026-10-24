"""`jms-eval-table`: the numbers of the runs of a spec, as one table beside their records.
Iteration one of the measurement harness.

    jms-eval-table <spec.toml> [--runs <folder>] [--set KEY=VALUE ...] [--seed N] [--resamples N]

It reads the spec (`spec`), with each `--set` that the sweep was given, and every record below
`--runs`, and writes the table into that folder, beside the records: `table.json`, the table
itself, and `table.md`, the same for a person to read. It needs no store and runs nothing.

**The rows.** One row for each configuration of the spec at its box rate, its load level: the
configurations that differ only in the wrapper are one row, since the run kind tells them apart.
A row has columns for each run kind: `uc2`, with the wrapper; `uc2-unwrapped`, without it; and
`baseline`, the store-only baseline that replays a run of the row. A run kind with
no run that counts leaves its columns empty.

**The statistics,** for each row and each run kind, over the runs that count, from the measured
window alone, which is all that a baseline measures:

- the commit latency and the store-request latency, each as the pooled p50, p95 and p99 with a
  bootstrap 95 % interval that resamples whole repetitions, from the recorded seed (`stats`);
- the completed writes per minute and the transports ended per minute, each as the median and the
  interquartile range of the runs;
- the store requests that the store refused, those that timed out and those whose connection
  dropped, apart, and the share of the three together, refused or unanswered (a
  request that a record written before says "timed out" is classified by its error, `summary`),
  and the refusals for want of heap, which count under the load: in the warm-up, the window and
  the drain (`summary`);
- at each load level: the stuck boxes, the failed boxes and the refused spawns, each with its
  place and reason, and its run;
- the completion matcher's reads: their number, their interval and their time in all, the
  harness's own load on the store.

**The SHACL overhead** of a row with SHACL on is the difference in mean commit latency between
it and the row that differs from it only in SHACL, off. **A calibration pair** is one
configuration run with the wrapper and without it: its row compares the measures that need no
wrapper, the transports ended per minute and the refused spawns, which give the wrapper's own
overhead, beside the matcher's reads of both. **Each ramp** of the spec shows each level it
judged with the broken rule, where it ended and its scale, the level before the first broken one
(`ramp`), for each of its repetitions apart, with how many it has: a repetition on request
has its own judgement, compared with the replay's first repetition. The row of a
ramp's level holds its first repetition only. **The runs
left out** of the statistics are listed, each with why: invalid, with the
checks it failed; failed; or interrupted. A baseline of a run left out is left out too.

**The precision rule** (interim names). For a spec with a
`[precision]` table each row has a `precision` entry per run kind of the configurations that run
under the rule: the runs made, the valid runs, the rounds they were made in (as their records say;
a record made before the rounds, says its pass, which the table shows as it is),
and the rule's status (`precision`): `met`, `pending (n runs)`, `precision not reached (n
runs)` where the maximum is made without it, or `stopped: throughput median 0` where the median
of the transports ended per minute is 0 at the minimum of valid runs or later. The verdict is made on the row's own numbers;
`rule_numbers` makes the same ones for the sweep's rounds, without the other bootstraps of a cell.

**The series** (interim names). The text shows each
configuration number with the spec's series (`spec.Spec.series`), such as "before 7" or "after 1,
2" for a row of two configurations, so that the numbers of two specs cannot be confused; the JSON
keeps the numbers and gains `series`, beside them in each row and at the top.

**The replay levels** (interim names). A baseline
replays a run compressed in time by a factor (`replay_levels`), and the table keeps the levels
apart. The factor-1 baseline, and a baseline of an earlier record, which says no factor, stay the
row's `baseline` columns, as they were. Each factor above 1 has columns of its own, `baseline x2`,
`baseline x4`, and so on, in `replays` of the row, with the statistics of the other run kinds; and
the section "Replay levels" holds, for every baseline that says its level, the repetition of the
replay, the factor, the offered and the achieved rate per minute, the refusals, the timeouts and the
dropped connections, and the signals that broke it, each by its name now (a record written before the
rename says `refused or timed out`; the table shows `refused or unanswered`): the first
broken level of a row is where its store broke under the
replay. The `baseline` and `baseline x2` columns hold the first repetition of a replay only, the
one the ramp comparison uses; the later repetitions are kept apart
in the section, which says how many each replay has (interim names).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jms_kapps.evaluation.baseline import BASELINE_RUN_KIND
from jms_kapps.evaluation.bench import INTERRUPTED
from jms_kapps.evaluation.precision import judge, made_repetitions
from jms_kapps.evaluation.ramp import (
    BREAKING_SIGNALS,
    PLATEAU,
    ramp_repetitions,
    steps_of,
)
from jms_kapps.evaluation.ramp import judge as judge_ramp
from jms_kapps.evaluation.ramp_comparison import NO_REPLAY, comparison
from jms_kapps.evaluation.record import ITERATION, RUN_KIND, UNWRAPPED_RUN_KIND
from jms_kapps.evaluation.record_match import ConfigurationIndex
from jms_kapps.evaluation.spec import (
    PrecisionRule,
    Spec,
    SpecConfiguration,
    SpecRefused,
    configurations,
    ramps_of,
    read_spec,
    setting,
)
from jms_kapps.evaluation.stats import (
    INTERVAL,
    mean,
    median_and_iqr,
    pooled_quantiles,
)
from jms_kapps.evaluation.summary import (
    RunSummary,
    read_summaries,
    repetition_of,
    repetitions_made,
)

PROGRAM = "jms-eval-table"

# The files of the table, in the runs' folder.
TABLE_JSON = "table.json"
TABLE_MARKDOWN = "table.md"

# The run kinds, each with its columns.
RUN_KINDS = (RUN_KIND, UNWRAPPED_RUN_KIND, BASELINE_RUN_KIND)

# The quantiles of a latency.
LATENCY_QUANTILES = (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))

# The seed of the bootstrap, and how many resamples it draws, unless the command is given others.
DEFAULT_SEED = 0
DEFAULT_RESAMPLES = 10_000

# The key of a configuration that the run kind tells.
_WRAPPER = "wrapper"


@dataclass
class _Row:
    key: str
    values: dict[str, Any]
    configurations: list[SpecConfiguration]
    runs: dict[str, list[RunSummary]] = field(
        default_factory=lambda: {kind: [] for kind in RUN_KINDS}
    )
    # The baselines of a replay compressed by a factor above 1, by the factor.
    replays: dict[float, list[RunSummary]] = field(default_factory=dict)
    # The runs of a ramp's level and the replay levels of a later repetition, which no cell pools:
    # the cells show the first repetition.
    later: list[RunSummary] = field(default_factory=list)

    @property
    def numbers(self) -> list[int]:
        return [configuration.number for configuration in self.configurations]


def make_table(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    *,
    runs: Path,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
    made_at: datetime | None = None,
) -> dict[str, Any]:
    """The table of the runs `summaries` of the configurations `found` of `spec`.

    Args:
        runs: The folder of the records, which the table names.
        seed: The seed of the bootstrap, which the table records.
        resamples: How many resamples each bootstrap interval draws.
        made_at: When the table is made: now if None.
    """
    rows: dict[str, _Row] = {}
    row_of: dict[str, _Row] = {}
    for configuration in found:
        values = {k: v for k, v in configuration.values.items() if k != _WRAPPER}
        row_key = _row_key(values)
        row = rows.setdefault(row_key, _Row(row_key, values, []))
        row.configurations.append(configuration)
        row_of[configuration.key] = row
    index = ConfigurationIndex(found)
    by_run_id = {summary.run_id: summary for summary in summaries}
    left_out: list[dict[str, Any]] = []
    not_of_the_spec = 0
    for summary in summaries:
        source = summary
        if summary.run_kind == BASELINE_RUN_KIND:
            replayed = by_run_id.get(summary.replays or "")
            if replayed is None:
                left_out.append(
                    _left_out(
                        summary,
                        None,
                        f"it replays the run {summary.replays}, whose record is not below "
                        "the runs' folder",
                    )
                )
                continue
            source = replayed
            replayed_to = index.of(replayed)
            if not replayed.counts and replayed_to is not None:
                left_out.append(
                    _left_out(
                        summary,
                        row_of[replayed_to.key],
                        f"it replays the run {replayed.run_id}, which is left out",
                    )
                )
                continue
        its_configuration = index.of(source)
        its_row = None if its_configuration is None else row_of[its_configuration.key]
        if its_row is None or summary.run_kind not in RUN_KINDS:
            not_of_the_spec += 1
            continue
        factor = summary.replay_factor
        if repetition_of(summary) > 1 and (
            summary.run_kind == BASELINE_RUN_KIND
            or (its_configuration is not None and its_configuration.ramp is not None)
        ):
            its_row.later.append(summary)
        elif (
            summary.run_kind == BASELINE_RUN_KIND and factor is not None and factor > 1
        ):
            its_row.replays.setdefault(factor, []).append(summary)
        else:
            its_row.runs[summary.run_kind].append(summary)
        why = summary.why_left_out()
        if why is not None:
            left_out.append(_left_out(summary, its_row, why))
    table_rows: list[dict[str, Any]] = []
    for row in rows.values():
        cells = {
            kind: _cell(row.key, kind, row.runs[kind], seed, resamples)
            for kind in RUN_KINDS
        }
        table_rows.append(
            {
                "configurations": row.numbers,
                # The spec's series, which the text shows with each number.
                "series": spec.series,
                "values": row.values,
                "ramp": row.configurations[0].ramp,
                "run_kinds": cells,
                "replays": {
                    f"{factor:g}": _cell(
                        row.key,
                        f"{BASELINE_RUN_KIND} x{factor:g}",
                        made,
                        seed,
                        resamples,
                    )
                    for factor, made in sorted(row.replays.items())
                },
                "replay_levels": _replay_levels(row),
                "replay_repetitions": _replay_repetitions(row),
            }
            | (
                {}
                if spec.precision is None
                else {"precision": _precision(row, cells, spec.precision)}
            )
        )
    by_values = {_row_key(table_row["values"]): table_row for table_row in table_rows}
    for table_row in table_rows:
        table_row["shacl_overhead"] = _shacl_overhead(table_row, by_values)
        table_row["calibration"] = _calibration(table_row)
    return {
        "table": "the numbers of a spec's runs",
        "iteration": ITERATION,
        "spec": str(spec.path),
        "series": spec.series,
        "runs": str(runs),
        "made_at": (made_at or datetime.now(UTC)).isoformat(),
        "bootstrap": {
            "seed": seed,
            "resamples": resamples,
            "interval": INTERVAL,
            "resampled": "whole repetitions",
        },
        "records_read": len(summaries),
        "records_of_no_configuration_of_the_spec": not_of_the_spec,
        "rows": table_rows,
        "ramps": _ramps(found, summaries, table_rows),
        "left_out": sorted(left_out, key=lambda run: (run["run"], run["run_kind"])),
    }


def _ramps(
    found: Sequence[SpecConfiguration],
    summaries: Sequence[RunSummary],
    table_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Each repetition of each ramp, judged apart, with how many repetitions the
    ramp has and its comparison with the replay's first repetition (`ramp_comparison`)."""
    ramps: list[dict[str, Any]] = []
    for block, levels in ramps_of(found).items():
        repetitions = ramp_repetitions(levels, summaries)
        for repetition in repetitions:
            judged = judge_ramp(steps_of(levels, summaries, repetition=repetition))
            ramps.append(
                {
                    "block": block,
                    "repetition": repetition,
                    "repetitions": len(repetitions),
                    "configurations": [
                        level["configuration"] for level in judged.levels
                    ],
                }
                | judged.record()
                | {"comparison": comparison(levels[0].values, judged, table_rows)}
            )
    return ramps


def _baselines(row: _Row) -> list[RunSummary]:
    """Every baseline of a row, of every factor and repetition."""
    return [
        *row.runs[BASELINE_RUN_KIND],
        *(made for runs in row.replays.values() for made in runs),
        *(run for run in row.later if run.run_kind == BASELINE_RUN_KIND),
    ]


def _replay_levels(row: _Row) -> list[dict[str, Any]]:
    """The replay levels of a row: for each baseline that counts and says its level, whatever its
    factor, the repetition of the replay (1 for a record that names none), the
    factor, its run, the rates, the refusals, the timeouts and the dropped connections, and the
    signals that broke it, by
    repetition in rising order of the factor."""
    levels = [
        {"run": run.run_id, "repetition": repetition_of(run)}
        | {
            key: run.replay_level.get(key)
            for key in (
                "factor",
                "offered_per_minute",
                "achieved_per_minute",
                "requests_in_window",
                "refused_in_window",
                "timed_out_in_window",
                "connection_dropped_in_window",
                "heap_refusals_in_window",
                "replay_span_seconds",
                "broken",
                "signals",
            )
        }
        # The licensed cores of the server, from the record's license view.
        | {"licensed_cores": run.licensed_cores}
        for run in _baselines(row)
        if run.counts and run.replay_level is not None
    ]
    return sorted(
        levels,
        key=lambda level: (level["repetition"], level["factor"] or 1.0, level["run"]),
    )


def _replay_repetitions(row: _Row) -> int:
    """How many repetitions the replay of a row has: those of its baselines that say their level
    and that a stop signal did not end, whether they count or not."""
    return len(
        repetitions_made(
            (run for run in _baselines(row) if run.replay_level is not None),
            run_kind=BASELINE_RUN_KIND,
        )
    )


def _row_key(values: Mapping[str, Any]) -> str:
    """The values of a row, each number as a float, as one text."""
    canonical = {
        key: float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else value
        for key, value in values.items()
    }
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)


def _left_out(summary: RunSummary, row: _Row | None, why: str) -> dict[str, Any]:
    return {
        "run": summary.run_id,
        "run_kind": summary.run_kind,
        "configurations": None if row is None else row.numbers,
        "repetition": summary.repetition,
        "why": why,
    }


def _cell(
    row: str, kind: str, runs: Sequence[RunSummary], seed: int, resamples: int
) -> dict[str, Any] | None:
    """The columns of one run kind of one row, over its runs that count; None where none
    counts."""
    counting = [run for run in runs if run.counts]
    if not counting:
        return None

    def spread(values: list[float | None]) -> dict[str, Any] | None:
        return median_and_iqr([value for value in values if value is not None])

    def total(values: list[int | None]) -> int | None:
        given = [value for value in values if value is not None]
        return sum(given) if given else None

    def listed(field_of: str, keys: Sequence[str]) -> list[dict[str, Any]] | None:
        found: list[dict[str, Any]] = []
        any_given = False
        for run in counting:
            items = getattr(run, field_of)
            if items is None:
                continue
            any_given = True
            found += [
                {key: item.get(key) for key in keys} | {"run": run.run_id}
                for item in items
            ]
        return found if any_given else None

    requests = total([run.store_requests_in_window for run in counting])
    unanswered = total([run.refused_or_unanswered_in_window for run in counting])
    matchers = [run.matcher for run in counting if run.matcher]
    return {
        "runs": [run.run_id for run in counting],
        "commit_latency_seconds": _latency(
            row,
            kind,
            "commit latency",
            [run.commit_latencies for run in counting],
            seed,
            resamples,
        ),
        "store_request_latency_seconds": _latency(
            row,
            kind,
            "store-request latency",
            [run.store_request_latencies for run in counting],
            seed,
            resamples,
        ),
        "completed_writes_per_minute": spread(
            [run.completed_writes_per_minute for run in counting]
        ),
        "transports_ended_per_minute": spread(
            [run.transports_ended_per_minute for run in counting]
        ),
        "store_requests_in_window": requests,
        "refused_in_window": total([run.refused_in_window for run in counting]),
        "timed_out_in_window": total([run.timed_out_in_window for run in counting]),
        "connection_dropped_in_window": total(
            [run.connection_dropped_in_window for run in counting]
        ),
        "refused_or_unanswered_in_window": unanswered,
        "refused_or_unanswered_share": None
        if not requests or unanswered is None
        else unanswered / requests,
        "heap_refusals_under_load": total(
            [run.heap_refusals_under_load for run in counting]
        ),
        "stuck": listed("stuck", ("transport", "status", "place")),
        "failed": listed("failed", ("transport", "place", "reason")),
        "refused_spawns": listed("refused_spawns", ("reason", "origin", "destination")),
        "refused_spawns_per_run": spread(
            [
                float(len(run.refused_spawns))
                for run in counting
                if run.refused_spawns is not None
            ]
        ),
        "matcher": None
        if not matchers
        else {
            "interval_seconds": sorted(
                {m.get("interval_seconds") for m in matchers}, key=str
            ),
            "reads_per_run": spread([m.get("read_count") for m in matchers]),
            "read_seconds_per_run": spread([m.get("read_seconds") for m in matchers]),
        },
    }


def _precision(
    row: _Row, cells: Mapping[str, Mapping[str, Any] | None], rule: PrecisionRule
) -> dict[str, Any]:
    """How each configuration of the row that runs under the precision rule stands against it, by
    run kind: the verdict (`precision.judge`) on the row's own cell, so on the table's own
    numbers, with the runs made and the rounds they were made in (as their records say), and the
    passes of the records made before the rounds."""
    standing: dict[str, Any] = {}
    for configuration in row.configurations:
        if not configuration.ruled:
            continue
        wrapper = bool(configuration.values[_WRAPPER])
        kind = RUN_KIND if wrapper else UNWRAPPED_RUN_KIND
        made = [run for run in row.runs[kind] if run.outcome != INTERRUPTED]
        verdict = judge(
            cells[kind], made=len(made_repetitions(made)), wrapper=wrapper, rule=rule
        )
        standing[kind] = verdict.record() | {
            "configuration": configuration.number,
            "rounds": sorted({run.round_number for run in made if run.round_number}),
            "passes": sorted({run.pass_number for run in made if run.pass_number}),
        }
    return standing


def _latency(
    row: str,
    kind: str,
    measure: str,
    values: Sequence[Sequence[float] | None],
    seed: int,
    resamples: int,
) -> dict[str, Any] | None:
    """The pooled p50, p95 and p99 of one latency of a row and run kind, each with its bootstrap
    interval over whole repetitions: the count and the mean too. None where no run has a value."""
    repetitions = [list(v) for v in values if v is not None]
    if not repetitions:
        return None
    pooled = [value for repetition in repetitions for value in repetition]
    found = pooled_quantiles(
        repetitions,
        [p for _, p in LATENCY_QUANTILES],
        seed=seed,
        drawn_for=f"{row}|{kind}|{measure}",
        resamples=resamples,
    )
    return {"count": len(pooled), "mean": mean(pooled)} | {
        name: quantile
        for (name, _), quantile in zip(LATENCY_QUANTILES, found, strict=True)
    }


def rule_numbers(
    configuration: SpecConfiguration,
    summaries: Sequence[RunSummary],
    *,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
) -> dict[str, Any] | None:
    """The numbers that the precision rule reads (`precision`) for `configuration`, from the
    runs of `summaries` that are its own and count: the table's own functions and defaults
    (`_latency`, `stats.median_and_iqr`), over the same runs, so they equal the table's cell for
    it. Only the commit latency and the transports ended per minute, without the other
    bootstraps of a cell. None where no run counts."""
    kind = RUN_KIND if configuration.values[_WRAPPER] else UNWRAPPED_RUN_KIND
    row = _row_key({k: v for k, v in configuration.values.items() if k != _WRAPPER})
    counting = [
        run
        for run in summaries
        if run.key == configuration.key and run.run_kind == kind and run.counts
    ]
    if not counting:
        return None
    return {
        "runs": [run.run_id for run in counting],
        "commit_latency_seconds": _latency(
            row,
            kind,
            "commit latency",
            [run.commit_latencies for run in counting],
            seed,
            resamples,
        ),
        "transports_ended_per_minute": median_and_iqr(
            [
                run.transports_ended_per_minute
                for run in counting
                if run.transports_ended_per_minute is not None
            ]
        ),
    }


def _shacl_overhead(
    row: Mapping[str, Any], by_values: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any] | None:
    """The SHACL overhead of a row with SHACL on: the difference in mean commit latency between
    it and the row that differs from it only in SHACL, off. None where either has no commit."""
    if row["values"].get("shacl") is not True:
        return None
    off = by_values.get(_row_key({**row["values"], "shacl": False}))
    if off is None:
        return None
    on_mean = _commit_mean(row)
    off_mean = _commit_mean(off)
    if on_mean is None or off_mean is None:
        return None
    return {
        "shacl_off": off["configurations"],
        "mean_commit_latency_seconds_on": on_mean,
        "mean_commit_latency_seconds_off": off_mean,
        "difference_seconds": on_mean - off_mean,
    }


def _commit_mean(row: Mapping[str, Any]) -> float | None:
    cell = row["run_kinds"][RUN_KIND]
    if cell is None or cell["commit_latency_seconds"] is None:
        return None
    mean_found: float | None = cell["commit_latency_seconds"]["mean"]
    return mean_found


def _calibration(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The comparison of a calibration pair: the row's runs with the wrapper and without it,
    by the measures that need no wrapper, beside the matcher's reads of both. None where the row
    has no run that counts of either."""
    wrapped = row["run_kinds"][RUN_KIND]
    unwrapped = row["run_kinds"][UNWRAPPED_RUN_KIND]
    if wrapped is None or unwrapped is None:
        return None

    def compared(measure: str) -> dict[str, Any]:
        with_it = _median(wrapped[measure])
        without = _median(unwrapped[measure])
        difference = None if with_it is None or without is None else with_it - without
        return {
            RUN_KIND: with_it,
            UNWRAPPED_RUN_KIND: without,
            "difference": difference,
            "relative": None
            if difference is None or not without
            else difference / without,
        }

    return {
        "transports_ended_per_minute": compared("transports_ended_per_minute"),
        "refused_spawns_per_run": compared("refused_spawns_per_run"),
        "matcher": {
            RUN_KIND: wrapped["matcher"],
            UNWRAPPED_RUN_KIND: unwrapped["matcher"],
        },
    }


def _median(spread: Mapping[str, Any] | None) -> float | None:
    return None if spread is None else spread["median"]


def write_table(runs: Path, table: Mapping[str, Any]) -> tuple[Path, Path]:
    """Write `table` into `runs`, beside the records, as `table.json` and `table.md`, and return
    their paths."""
    as_json = runs / TABLE_JSON
    as_markdown = runs / TABLE_MARKDOWN
    as_json.write_text(
        json.dumps(table, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    as_markdown.write_text(markdown(table), encoding="utf-8")
    return as_json, as_markdown


def markdown(table: Mapping[str, Any]) -> str:
    """The table, for a person to read: one section for each kind of number."""
    rows = table["rows"]
    bootstrap = table["bootstrap"]
    counted = sum(
        len(cell["runs"])
        for row in rows
        for cell in row["run_kinds"].values()
        if cell is not None
    )
    lines = [
        f"# The numbers of {Path(table['spec']).name}",
        "",
        (
            f"Made by `{PROGRAM}` at {table['made_at']} from the records below "
            f"`{table['runs']}`, {table['iteration']}: {table['records_read']} records read, "
            f"{counted} runs counted, {len(table['left_out'])} left out, "
            f"{table['records_of_no_configuration_of_the_spec']} of no configuration of the "
            "spec."
        ),
        "",
        (
            "Every latency and rate counts the measured window alone, over the runs that "
            "count. A latency is the pooled quantile in seconds with its "
            f"{bootstrap['interval']:.0%} bootstrap interval, which resamples whole "
            f"repetitions (seed {bootstrap['seed']}, {bootstrap['resamples']} resamples). A "
            "rate is the median per minute with its interquartile range. An empty cell: no "
            "run of that run kind counts."
        ),
        "",
        "## Configurations",
        "",
        "| Configurations | Values |",
        "|---|---|",
    ]
    for row in rows:
        values = ", ".join(
            f"{key} = {_shown(value)}" for key, value in row["values"].items()
        )
        ramp = f" (ramp, block {row['ramp']})" if row["ramp"] is not None else ""
        lines.append(f"| {_numbers(row)}{ramp} | {values} |")
    lines += _latency_section(rows)
    lines += _throughput_section(rows)
    lines += _store_section(rows)
    lines += _shacl_section(rows)
    lines += _load_level_section(rows)
    lines += _precision_section(rows)
    lines += _calibration_section(rows)
    lines += _replay_section(rows)
    lines += _ramp_section(table["ramps"], table.get("series"))
    lines += _left_out_section(table["left_out"], table.get("series"))
    return "\n".join(lines) + "\n"


def _precision_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The runs and rounds of each configuration under the precision rule, and where it stands;
    nothing for a spec without the rule. A record made before the rounds shows its pass."""
    standing = [
        (row, kind, entry)
        for row in rows
        for kind, entry in row.get("precision", {}).items()
    ]
    if not standing:
        return []
    lines = [
        "",
        "## Precision rule",
        "",
        (
            "A configuration runs until it has enough valid runs, the interval of its commit p95 "
            "is narrow and the IQR of its throughput is small (a configuration without the "
            "wrapper is judged on the throughput alone), or until it has made its maximum of "
            "runs. A configuration whose throughput median is 0 once it has its minimum of "
            'valid runs stops there: "stopped: throughput median 0". Rounds: the rounds the '
            "runs were made in, each a time window; a run made before the rounds shows its "
            "pass (pass N)."
        ),
        "",
        "| Configurations | Run kind | Runs made | Valid runs | Rounds | Rule |",
        "|---|---|---|---|---|---|",
    ]
    for row, kind, entry in standing:
        rounds = (
            ", ".join(
                [f"pass {n}" for n in entry.get("passes", [])]
                + [str(n) for n in entry.get("rounds", [])]
            )
            or "none"
        )
        lines.append(
            f"| {_numbers(row)} | {kind} | {entry['runs_made']} | {entry['valid_runs']} | "
            f"{rounds} | {entry['status']} |"
        )
    return lines


def _latency_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Latency (seconds)",
        "",
        (
            "| Configurations | Run kind | Runs | Commit p50 | Commit p95 | Commit p99 "
            "| Store request p50 | Store request p95 | Store request p99 |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row, kind, cell in _cells(rows):
        commit = _get(cell, "commit_latency_seconds")
        request = _get(cell, "store_request_latency_seconds")
        lines.append(
            f"| {_numbers(row)} | {kind} | {_count(cell)} | "
            + " | ".join(_quantile(commit, name) for name, _ in LATENCY_QUANTILES)
            + " | "
            + " | ".join(_quantile(request, name) for name, _ in LATENCY_QUANTILES)
            + " |"
        )
    return lines


def _throughput_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Throughput (per minute)",
        "",
        "| Configurations | Run kind | Runs | Completed writes | Transports ended |",
        "|---|---|---|---|---|",
    ]
    for row, kind, cell in _cells(rows):
        lines.append(
            f"| {_numbers(row)} | {kind} | {_count(cell)} | "
            f"{_spread(_get(cell, 'completed_writes_per_minute'))} | "
            f"{_spread(_get(cell, 'transports_ended_per_minute'))} |"
        )
    return lines


def _store_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Store requests refused or unanswered, and refusals for want of heap",
        "",
        (
            "A store request without an answer timed out where its client gave up because the "
            "time ran out, and its connection dropped where the connection closed, reset or "
            "failed before an answer. The share is that of the three together, refused or "
            "unanswered, which the broken rule reads."
        ),
        "",
        (
            "| Configurations | Run kind | Store requests | Refused | Timed out "
            "| Connection dropped | Refused or unanswered | Share "
            "| Heap refusals under the load |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row, kind, cell in _cells(rows):
        share = _get(cell, "refused_or_unanswered_share")
        lines.append(
            f"| {_numbers(row)} | {kind} | {_shown(_get(cell, 'store_requests_in_window'))} | "
            f"{_shown(_get(cell, 'refused_in_window'))} | "
            f"{_shown(_get(cell, 'timed_out_in_window'))} | "
            f"{_shown(_get(cell, 'connection_dropped_in_window'))} | "
            f"{_shown(_get(cell, 'refused_or_unanswered_in_window'))} | "
            f"{'' if share is None else f'{share:.2%}'} | "
            f"{_shown(_get(cell, 'heap_refusals_under_load'))} |"
        )
    return lines


def _shacl_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## SHACL overhead",
        "",
        (
            "The mean commit latency (`uc2`) with SHACL on, less that of the same "
            "configuration with SHACL off."
        ),
        "",
        "| SHACL on | SHACL off | Mean on (s) | Mean off (s) | Difference (s) |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        overhead = row["shacl_overhead"]
        if overhead is None:
            continue
        lines.append(
            f"| {_numbers(row)} | {_named(row.get('series'), overhead['shacl_off'])} | "
            f"{_number(overhead['mean_commit_latency_seconds_on'])} | "
            f"{_number(overhead['mean_commit_latency_seconds_off'])} | "
            f"{_number(overhead['difference_seconds'])} |"
        )
    return lines


def _load_level_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Stuck boxes, failed boxes and refused spawns, by load level",
        "",
    ]
    for row in sorted(
        rows,
        key=lambda r: (float(r["values"]["boxes_per_minute"]), r["configurations"]),
    ):
        for kind in RUN_KINDS:
            cell = row["run_kinds"][kind]
            if cell is None:
                continue
            items = (
                [
                    f"stuck: {s['transport']} at {s['place']} ({s['status']}), run {s['run']}"
                    for s in cell["stuck"] or []
                ]
                + [
                    f"failed: {f['transport']} at {f['place']}: {_text(f['reason'])}, run {f['run']}"
                    for f in cell["failed"] or []
                ]
                + [
                    f"refused spawn: {_text(r['reason'])}"
                    + _where(r)
                    + f", run {r['run']}"
                    for r in cell["refused_spawns"] or []
                ]
            )
            if (
                cell["stuck"] is None
                and cell["failed"] is None
                and cell["refused_spawns"] is None
            ):
                continue
            lines.append(
                f"- {_shown(row['values']['boxes_per_minute'])} boxes per minute, "
                f"configurations {_numbers(row)}, {kind}: {len(cell['stuck'] or [])} stuck, "
                f"{len(cell['failed'] or [])} failed, "
                f"{len(cell['refused_spawns'] or [])} refused spawns"
            )
            lines += [f"  - {item}" for item in items]
    return lines


def _calibration_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Calibration pairs: the harness's own overhead",
        "",
        (
            "One configuration run with the wrapper (`uc2`) and without it (`uc2-unwrapped`), "
            "compared by the measures that need no wrapper (medians of the runs), beside the "
            "completion matcher's reads: their number per run, their interval and their time "
            "in all per run, the harness's own load on the store."
        ),
        "",
        (
            "| Configurations | Transports ended per minute, uc2 | uc2-unwrapped | Difference "
            "| Refused spawns per run, uc2 | uc2-unwrapped | Difference | Matcher, uc2 "
            "| Matcher, uc2-unwrapped |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        calibration = row["calibration"]
        if calibration is None:
            continue
        ended = calibration["transports_ended_per_minute"]
        refused = calibration["refused_spawns_per_run"]
        lines.append(
            f"| {_numbers(row)} | {_number(ended[RUN_KIND])} | "
            f"{_number(ended[UNWRAPPED_RUN_KIND])} | {_difference(ended)} | "
            f"{_number(refused[RUN_KIND])} | {_number(refused[UNWRAPPED_RUN_KIND])} | "
            f"{_difference(refused)} | {_matcher(calibration['matcher'][RUN_KIND])} | "
            f"{_matcher(calibration['matcher'][UNWRAPPED_RUN_KIND])} |"
        )
    return lines


def _replay_section(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The replay levels of the baselines, a row of the section for each level, in rising order of
    the factor: nothing where no baseline says its level."""
    if not any(row["replay_levels"] for row in rows):
        return []
    lines = [
        "",
        "## Replay levels",
        "",
        (
            "The store-only baseline replays a recorded run compressed in time by a factor. The "
            "offered rate is the window's requests over its length over the factor, the achieved "
            "rate the answered requests over the span from the window's start to the last "
            "answer, at least the window, so it is never more than the offered rate and 100 % "
            "means that the store kept up. A level is broken by a refusal for want of heap, more than 1 % of the "
            "requests refused or unanswered (timed out or with their connection dropped), or an "
            "achieved rate below 90 % of the offered rate. Each repetition of a replay replays the same run, and its levels are listed "
            "apart; the baseline columns above hold the first repetition only."
        ),
        "",
        "Repetitions of each replay: "
        + "; ".join(
            f"configuration {_numbers(row)}, {row.get('replay_repetitions', 1)}"
            for row in rows
            if row["replay_levels"]
        )
        + ".",
        "",
        (
            "| Configurations | Repetition | Factor | Run | Offered per minute "
            "| Achieved per minute | Requests | Refused | Timed out | Connection dropped "
            "| Heap refusals | Broken by |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        for level in row["replay_levels"]:
            lines.append(
                f"| {_numbers(row)} | {_shown(level.get('repetition', 1))} | "
                f"{_shown(level['factor'])} | {level['run']} | "
                f"{_number(level['offered_per_minute'])} | "
                f"{_number(level['achieved_per_minute'])} | "
                f"{_shown(level['requests_in_window'])} | "
                f"{_shown(level['refused_in_window'])} | "
                f"{_shown(level['timed_out_in_window'])} | "
                f"{_shown(level.get('connection_dropped_in_window'))} | "
                f"{_shown(level['heap_refusals_in_window'])} | "
                f"{', '.join(s['signal'] for s in level['signals'] or [])} |"
            )
    return lines


def _ramp_section(
    ramps: Sequence[Mapping[str, Any]], series: str | None = None
) -> list[str]:
    lines = ["", "## Ramps", ""]
    if not ramps:
        return [*lines, "The spec has no ramp."]
    lines += [
        (
            "The licensed cores beside a ramp's replay are the maximum of CPU cores of the "
            "server's license, as the records of the replay's compared level keep its license "
            "view: GraphDB Enterprise runs at most one concurrent query for each licensed core, "
            "so the first broken level of the compressed replay, an open loop, can be this limit "
            "of the license rather than the capacity of the store."
        ),
        "",
    ]
    for ramp in ramps:
        scale = ramp["scale"]
        lines += [
            (
                f"### The ramp of block {ramp['block']}, repetition "
                f"{ramp.get('repetition', 1)} of {ramp.get('repetitions', 1)}"
            ),
            "",
            f"Judgement: {ramp['judgement']['text']}.",
            "",
            f"Scale: {'' if scale is None else _shown(scale)}"
            + (" boxes per minute" if scale is not None else "none")
            + f". {_text(ramp['why'][0].upper() + ramp['why'][1:])}.",
            "",
            _comparison_text(ramp["comparison"], series),
            "",
            (
                "| Box rate | Configuration | Runs | Judged | Heap refusals "
                "| Refused or unanswered | p95 commit latency (s) | Completed writes per minute "
                "| Store requests per minute | Offered spawns | Accepted spawns | Accepted share "
                "| UC2-limited | Broken by | Plateau | Not judged |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for level in ramp["levels"]:
            numbers = level.get("numbers") or {}
            share = numbers.get("refused_or_unanswered_share")
            accepted = numbers.get("spawns_accepted_share")
            judged = "yes" if level["judged"] else "no: " + _text(level["why"])
            found = [s["signal"] for s in level.get("signals", [])]
            limited = level.get("uc2_limited")
            lines.append(
                f"| {_shown(level['boxes_per_minute'])} | "
                f"{_named(series, [level['configuration']])} | "
                f"{', '.join(level['runs'])} | {judged} | "
                f"{_shown(numbers.get('heap_refusals_under_load'))} | "
                f"{'' if share is None else f'{share:.2%}'} | "
                f"{_number(numbers.get('p95_commit_latency_seconds'))} | "
                f"{_number(numbers.get('completed_writes_per_minute'))} | "
                f"{_number(numbers.get('store_requests_per_minute'))} | "
                f"{_shown(numbers.get('spawns_offered'))} | "
                f"{_shown(numbers.get('spawns_accepted'))} | "
                f"{'' if accepted is None else f'{accepted:.2%}'} | "
                f"{'' if limited is None else 'yes' if limited else 'no'} | "
                f"{', '.join(name for name in found if name in BREAKING_SIGNALS)} | "
                f"{'yes' if PLATEAU in found else ''} | "
                f"{_text('; '.join(level.get('not_judged', [])))} |"
            )
        lines.append("")
    return lines


def _comparison_text(compared: Mapping[str, Any], series: str | None = None) -> str:
    """The ramp's comparison with the replay of the matching UC2 configuration, as a sentence."""
    replay = compared["replay"]
    where = (
        ""
        if replay["configuration"] is None
        else f" of configuration {_named(series, [replay['configuration']])}"
    )
    if compared["uc2_limited_at"] is None:
        achieved = "No level is UC2-limited"
    else:
        achieved = (
            f"UC2 achieved {_number(compared['uc2_store_requests_per_minute'])} store "
            f"requests per minute at its first UC2-limited level, "
            f"{_shown(compared['uc2_limited_at'])} boxes per minute"
        )
    if replay["outcome"] == NO_REPLAY:
        return f"{achieved}. Replay: {NO_REPLAY}."
    offered = replay["offered_store_requests_per_minute"]
    return (
        f"{achieved}. Replay{where}: {replay['text']}"
        + (
            ""
            if offered is None
            else f", offered {_number(offered)} store requests per minute"
        )
        + f". Licensed cores: {replay['licensed_cores_text']}."
    )


def _left_out_section(
    left_out: Sequence[Mapping[str, Any]], series: str | None = None
) -> list[str]:
    lines = [
        "",
        "## Runs left out of the statistics",
        "",
        "| Run | Run kind | Configurations | Repetition | Why |",
        "|---|---|---|---|---|",
    ]
    for run in left_out:
        numbers = run["configurations"]
        lines.append(
            f"| {run['run']} | {run['run_kind']} | "
            f"{'' if numbers is None else _named(series, numbers)} | "
            f"{_shown(run['repetition'])} | {_text(run['why'])} |"
        )
    return lines


def _text(value: Any) -> str:
    """A free text, as a store's reason or why a run failed, on one line of a Markdown table or
    list: its line breaks as spaces, its bars escaped."""
    return " ".join(str(value).split()).replace("|", "\\|")


def _cells(
    rows: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], str, Mapping[str, Any] | None]]:
    """Each row with each run kind and its columns, None where no run of that kind counts; after
    its run kinds, a baseline column for each factor above 1 that the row has (`baseline x2`)."""
    return [
        cell
        for row in rows
        for cell in [
            *((row, kind, row["run_kinds"][kind]) for kind in RUN_KINDS),
            *(
                (row, f"{BASELINE_RUN_KIND} x{factor}", replayed)
                for factor, replayed in row["replays"].items()
            ),
        ]
    ]


def _get(cell: Mapping[str, Any] | None, key: str) -> Any:
    return None if cell is None else cell[key]


def _numbers(row: Mapping[str, Any]) -> str:
    """The configuration numbers of a row with the spec's series, such as `after 1, 2`."""
    return _named(row.get("series"), row["configurations"])


def _named(series: str | None, numbers: Sequence[int]) -> str:
    """Configuration numbers with their series, such as `before 7`: the numbers
    alone where the table names no series."""
    shown = ", ".join(map(str, numbers))
    return f"{series} {shown}" if series else shown


def _count(cell: Mapping[str, Any] | None) -> str:
    return "" if cell is None else str(len(cell["runs"]))


def _quantile(latency: Mapping[str, Any] | None, name: str) -> str:
    if latency is None or latency[name] is None:
        return ""
    found = latency[name]
    interval = found["interval"]
    shown = _number(found["value"])
    if interval is None:
        return shown
    return f"{shown} [{_number(interval[0])}, {_number(interval[1])}]"


def _spread(spread: Mapping[str, Any] | None) -> str:
    if spread is None:
        return ""
    return f"{_number(spread['median'])} (IQR {_number(spread['iqr'])})"


def _difference(compared: Mapping[str, Any]) -> str:
    if compared["difference"] is None:
        return ""
    relative = compared["relative"]
    return _number(compared["difference"]) + (
        "" if relative is None else f" ({relative:+.1%})"
    )


def _matcher(matcher: Mapping[str, Any] | None) -> str:
    if matcher is None:
        return ""
    intervals = ", ".join(f"{_shown(i)} s" for i in matcher["interval_seconds"])
    return (
        f"{_number(_median(matcher['reads_per_run']))} reads every {intervals}, "
        f"{_number(_median(matcher['read_seconds_per_run']))} s"
    )


def _where(refused: Mapping[str, Any]) -> str:
    where = [
        f"{key} {refused[key]}" for key in ("origin", "destination") if refused.get(key)
    ]
    return f" ({', '.join(where)})" if where else ""


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:.4g}"


def _shown(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="The numbers of the runs of a spec, as one table beside their records "
        "(the measurement harness, iteration one). It needs no store and runs nothing.",
    )
    parser.add_argument(
        "spec", type=Path, help="The spec, TOML, whose runs the table holds"
    )
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("runs"),
        help="The folder of the runs' folders, as the sweep was given it; the table is "
        "written into it (default: runs)",
    )
    parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        help="A value of every configuration, over the spec's, as the sweep was given it. "
        "May be given more than once",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"The seed of the bootstrap, which the table records (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--resamples",
        type=int,
        default=DEFAULT_RESAMPLES,
        help="How many resamples each bootstrap interval draws "
        f"(default: {DEFAULT_RESAMPLES})",
    )
    args = parser.parse_args(argv)
    if args.resamples < 1:
        print(f"{PROGRAM}: --resamples is a whole number from 1", file=sys.stderr)
        return 2
    if not args.runs.is_dir():
        print(
            f"{PROGRAM}: there is no runs' folder {str(args.runs)!r}", file=sys.stderr
        )
        return 2
    try:
        spec = read_spec(args.spec)
        found = configurations(spec, overrides=dict(map(setting, args.set)))
    except SpecRefused as refused:
        print(f"{PROGRAM}: {refused}", file=sys.stderr)
        return 2
    table = make_table(
        spec,
        found,
        read_summaries(args.runs),
        runs=args.runs,
        seed=args.seed,
        resamples=args.resamples,
    )
    as_json, as_markdown = write_table(args.runs, table)
    print(
        f"{PROGRAM}: {table['records_read']} records read, {len(table['left_out'])} left out. "
        f"The table is at {as_json} and {as_markdown}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
