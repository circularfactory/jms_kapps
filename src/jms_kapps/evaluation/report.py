"""`jms-eval-report`: the report of the runs of a spec, tables and charts, beside their records.
Iteration one of the measurement harness.

    jms-eval-report <spec.toml> [--runs <folder>] [--set KEY=VALUE ...] [--seed N] [--resamples N]
                    [--live-checks <log>]

It reads the spec (`spec`), with each `--set` that the sweep was given, and the summary of every
run below `--runs` (`report_summary`: a record is read once, and its summary kept beside it), and
writes `report/` into that folder: `report.md`, the tables and the charts for a person to read,
`runs.csv`, `configurations.csv`, `steps.csv`, `unanswered.csv` and one PNG for each chart. It needs no store and no network, and
runs nothing. The sweep makes it again after each run, whatever the run's end (`update_report`).

**The series** (jms_kapps 110, interim names). `report.md`, the chart titles and the chart labels
show each configuration number with the spec's series (`spec.Spec.series`), such as "before 7",
and a label that joins the number and the run kind, "after 7 unwrapped"; each CSV file keeps the
number and gains a column `series`. The file names stay by number.

**The report points** (`report_points`, jms_kapps 97; interim names, jms_kapps 83) come after the
tables of jms_kapps 86, 87 and 91 and change none of their values: `marks.csv` (the hour of each
run, whether it overlaps a live check of the log `--live-checks` names or the server watch shows
other activity, its deadlock alarms and its dead heartbeats), `hours.csv` and one `hours-<n>.png`
for each configuration, `page_cache.csv`, `deadlock_alarms.csv` and `dead_heartbeats.csv`. A mark
says `no data` where the log, the server watch or the spans are missing.

**The runs table** has one row for each run: its configuration and the setting (topology, prefill,
SHACL, strategies, box rate, wrapper), its run kind, its status and the reason, the transports
ended per minute, the commits and their p50, p95 and p99, the p50 and p95 of the reads and of the
writes, apart, the requests the store refused, those that timed out and those whose connection
dropped, inside the window and outside it, apart (jms_kapps 109: until 2026-10-14 a dropped
connection was counted as timed out), the stuck boxes, and the client host's load in the window
(below). The status is `completed` (a valid run), `invalid` (a
check found it so), `failed` or `interrupted`. A failed or an interrupted run has no values.

**The latencies** are those of the accepted store requests of the window, logins left out, as
`jms-eval-table` reads them; the reads and the writes are told apart by the request's kind. A
request that got no answer is counted in the column of the timed-out requests where its client gave
up because the time ran out, and in that of the dropped connections where its connection closed,
reset or failed before an answer (`store_requests.unanswered_outcome`; a request that a record
written before says "timed out" is classified by its error, and an error of neither class, or
none at all, counts as connection dropped). It is not among the latencies: its record keeps its real wait, the time
until the failure, which is listed beside it (below).

**The unanswered store requests** (`unanswered.csv`, and a section of `report.md`): one row for
each store request that got no answer, in the window and outside it, with its run, the place
(`in the window` or `outside the window`), its outcome (`timed out` or `connection dropped`,
jms_kapps 109), its time, its wait in seconds (the real one) and its reason, the error as the
record keeps it. A baseline run's are listed too. (jms_kapps 91.)

**The client load** (columns `client_load`, `client_cpu_median_percent`, `client_cpu_max_percent`
and `client_memory_max_gib` of `runs.csv`, and a section of `report.md`): the median and the highest
CPU use of the client host, in percent of all its CPUs, and its highest memory use in GiB, from
the record's samples (`client_load`) in the measured window. `client_load` says `measured`, `no
data` (a record of the "before" series holds no sample, or none lies in the window) or `not
available` (the host had no `/proc`); the report shows those words in place of the numbers.
(jms_kapps 91.)

**The configurations table** is the table of `jms-eval-table` (`table.make_table`), made from the
same summaries in the same order with the same seed and resamples, so that its numbers are that
table's: one row for each configuration at its box rate and each run kind, over the runs that
count, with each statistic of the table as columns.

**The charts** show the throughput, the commit p95 and the read p95 and write p95 of each
configuration, a dot for each run (filled if valid, a ring if invalid) and a tick at the median of
the valid runs; and, for each family of configurations that differ only in the size of their
prefill, the throughput and the commit p95 against that size, on a log scale. A failed or an
interrupted run is listed in the runs table and not plotted.

**The steps** (`report_steps`, jms_kapps 87): `steps.csv` has one row for each configuration, role
(WMS or module) and step, with the steps per minute, the median and the p95 seconds and the median
size of the store's answer, pooled over the valid runs' spans in the measured window; for each
family of prefill sizes, a steps chart compares the configurations step by step on a log time
scale. The steps are read from the spans once, and kept in the run's summary.

(Added on 2026-10-12, jms_kapps 86; interim, jms_kapps 83.)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from jms_kapps.evaluation import report_charts, report_points, report_steps
from jms_kapps.evaluation.baseline import BASELINE_RUN_KIND
from jms_kapps.evaluation.bench import INTERRUPTED
from jms_kapps.evaluation.record import RUN_KIND, UNWRAPPED_RUN_KIND
from jms_kapps.evaluation.record_match import ConfigurationIndex
from jms_kapps.evaluation.report_points import Entry, LiveCheck, read_live_checks
from jms_kapps.evaluation.report_summary import MEASURED, NO_DATA, RunFigures, read_all
from jms_kapps.evaluation.spec import (
    Spec,
    SpecConfiguration,
    SpecRefused,
    configurations,
    read_spec,
    setting,
)
from jms_kapps.evaluation.stats import quantile
from jms_kapps.evaluation.summary import COMPLETED, RunSummary
from jms_kapps.evaluation.table import DEFAULT_RESAMPLES, DEFAULT_SEED, make_table

PROGRAM = "jms-eval-report"

# The folder of the report, in the runs' folder, and its files.
REPORT_FOLDER = "report"
REPORT_MARKDOWN = "report.md"
RUNS_CSV = "runs.csv"
CONFIGURATIONS_CSV = "configurations.csv"
STEPS_CSV = "steps.csv"
UNANSWERED_CSV = "unanswered.csv"

# The status of a run: valid, found invalid by a check, failed, or ended by a stop signal.
VALID = report_charts.VALID
INVALID = report_charts.INVALID
FAILED = "failed"
INTERRUPTED_STATUS = "interrupted"

# The settings of a configuration that the tables show.
SETTINGS = (
    "topology",
    "prefill",
    "shacl",
    "module_strategy",
    "wms_strategy",
    "boxes_per_minute",
    "wrapper",
)

_RUN_KIND_ORDER = (RUN_KIND, UNWRAPPED_RUN_KIND, BASELINE_RUN_KIND)

RUNS_COLUMNS = (
    "configuration",
    "series",
    "run_kind",
    "repetition",
    "run",
    *SETTINGS,
    "status",
    "reason",
    "transports_ended_per_minute",
    "commits",
    "commit_p50",
    "commit_p95",
    "commit_p99",
    "read_p50",
    "read_p95",
    "write_p50",
    "write_p95",
    "refused_in_window",
    "timed_out_in_window",
    "connection_dropped_in_window",
    "refused_outside_window",
    "timed_out_outside_window",
    "connection_dropped_outside_window",
    "stuck_boxes",
    "client_load",
    "client_cpu_median_percent",
    "client_cpu_max_percent",
    "client_memory_max_gib",
)

# The columns of the unanswered requests' table.
UNANSWERED_COLUMNS = (
    "configuration",
    "series",
    "run_kind",
    "repetition",
    "run",
    "where",
    "outcome",
    "time",
    "wait_seconds",
    "reason",
    "kind",
    "participant",
)

# The columns of the runs table that the Markdown shows (the CSV has them all).
_RUNS_SHOWN = (
    "configuration",
    "run_kind",
    "repetition",
    "status",
    "reason",
    "transports_ended_per_minute",
    "commits",
    "commit_p50",
    "commit_p95",
    "commit_p99",
    "read_p50",
    "read_p95",
    "write_p50",
    "write_p95",
    "refused_in_window",
    "timed_out_in_window",
    "connection_dropped_in_window",
    "refused_outside_window",
    "timed_out_outside_window",
    "connection_dropped_outside_window",
    "stuck_boxes",
)

# The columns of the configurations table that the Markdown shows (the CSV has them all).
_CONFIGURATIONS_SHOWN = (
    "configurations",
    "run_kind",
    "topology",
    "prefill",
    "shacl",
    "runs",
    "transports_ended_per_minute_median",
    "transports_ended_per_minute_iqr",
    "commit_latency_seconds_p50_value",
    "commit_latency_seconds_p95_value",
    "commit_latency_seconds_p99_value",
    "store_request_latency_seconds_p95_value",
    "refused_or_unanswered_share",
    "stuck",
)

_SIZE = re.compile(r"-(\d+(?:\.\d+)?)([kMG]?)\.[A-Za-z]+$")
_UNITS = {"": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


def status_of(summary: RunSummary) -> str:
    """The status of a run: `completed` for a valid run, `invalid`, `failed` or `interrupted`."""
    if summary.outcome == INTERRUPTED:
        return INTERRUPTED_STATUS
    if summary.outcome != COMPLETED:
        return FAILED
    return INVALID if summary.valid is False else VALID


def prefill_size(prefill: Any) -> float | None:
    """The size of a prefill file in statements, as its name says it (`grid-1M.ttl` is 1 000 000);
    None for a name that gives none, as `none`."""
    found = _SIZE.search(str(prefill))
    return None if found is None else float(found[1]) * _UNITS[found[2]]


def _label(series: str, number: int, run_kind: str) -> str:
    """The label of a configuration and a run kind in a chart: its number with the spec's series,
    and the run kind unless `uc2`, such as `after 7 unwrapped` (jms_kapps 110)."""
    name = f"{series} {number}" if series else str(number)
    return name if run_kind == RUN_KIND else f"{name} {run_kind.removeprefix('uc2-')}"


def _with_series(frame: pd.DataFrame, series: str) -> pd.DataFrame:
    """`frame` with the spec's series in a column `series` after its configuration's number, so
    that the CSV keeps the number and gains the series (jms_kapps 110); unchanged where it has no
    configuration column or a series already."""
    if "series" in frame.columns:
        return frame
    for column in ("configuration", "configurations"):
        if column in frame.columns:
            frame = frame.copy()
            frame.insert(list(frame.columns).index(column) + 1, "series", series)
            return frame
    return frame


def runs_table(
    found: Sequence[SpecConfiguration], figures: Sequence[RunFigures]
) -> tuple[pd.DataFrame, int]:
    """The runs table of the runs `figures` of the configurations `found`, and how many of the
    runs are of no configuration of `found` (left out of the table)."""
    rows: list[dict[str, Any]] = []
    elsewhere = 0
    for run, configuration in _configurations_of(found, figures):
        if configuration is None:
            elsewhere += 1
            continue
        rows.append(_run_row(configuration, run))
    rows.sort(
        key=lambda r: (
            r["configuration"],
            _RUN_KIND_ORDER.index(r["run_kind"]),
            r["repetition"] if r["repetition"] is not None else 0,
            r["run"],
        )
    )
    return pd.DataFrame(rows, columns=list(RUNS_COLUMNS)), elsewhere


def _configurations_of(
    found: Sequence[SpecConfiguration], figures: Sequence[RunFigures]
) -> list[tuple[RunFigures, SpecConfiguration | None]]:
    """Each run of `figures` with its configuration of `found`; a baseline run has the
    configuration of the run it replays, and a run of no configuration of `found` has none."""
    index = ConfigurationIndex(found)
    by_run = {f.summary.run_id: f.summary for f in figures}
    pairs: list[tuple[RunFigures, SpecConfiguration | None]] = []
    for run in figures:
        summary = run.summary
        source: RunSummary | None = summary
        if summary.run_kind == BASELINE_RUN_KIND:
            source = by_run.get(summary.replays or "")
        configuration = None if source is None else index.of(source)
        if summary.run_kind not in _RUN_KIND_ORDER:
            configuration = None
        pairs.append((run, configuration))
    return pairs


def steps_frame(
    found: Sequence[SpecConfiguration], figures: Sequence[RunFigures]
) -> pd.DataFrame:
    """The steps table (`report_steps.steps_table`) of the valid runs of `found` that have steps."""
    return report_steps.steps_table(
        (configuration.number, run.steps)
        for run, configuration in _configurations_of(found, figures)
        if configuration is not None
        and run.steps is not None
        and status_of(run.summary) == VALID
    )


def entries_of(
    found: Sequence[SpecConfiguration], figures: Sequence[RunFigures]
) -> list[Entry]:
    """The runs of `figures` for the tables of the report points (`report_points`), in the order
    of the runs table. A run of a configuration of `found` has that configuration's number and
    prefill. A run that matches none (`record_match`) is not left out, as the runs
    table leaves it: a deadlock alarm and a dead heartbeat belong to the run, so it has the
    configuration number its own record gives, and no prefill."""
    entries = [
        Entry(
            configuration.number if configuration else run.summary.configuration,
            None
            if configuration is None or configuration.values.get("prefill") is None
            else str(configuration.values["prefill"]),
            run.summary,
            run.points,
            status_of(run.summary),
        )
        for run, configuration in _configurations_of(found, figures)
        if run.summary.run_kind in _RUN_KIND_ORDER
    ]
    entries.sort(
        key=lambda e: (
            e.configuration if e.configuration is not None else 0,
            _RUN_KIND_ORDER.index(e.summary.run_kind),
            e.summary.repetition if e.summary.repetition is not None else 0,
            e.summary.run_id,
        )
    )
    return entries


def draw_hours(
    hours: pd.DataFrame, folder: Path, series: str = ""
) -> list[tuple[str, str]]:
    """Draw the by-hour chart of each configuration that has valid runs into `folder`; return
    each file with its title."""
    drawn: list[tuple[str, str]] = []
    for number in sorted(set(hours["configuration"])):
        name = report_points.hours_png(number)
        title = f"By hour of day (UTC), configuration {f'{series} ' if series else ''}{number}"
        report_charts.hours_chart(
            hours[hours["configuration"] == number], title, folder / name
        )
        drawn.append((name, title))
    return drawn


def _run_row(configuration: SpecConfiguration, run: RunFigures) -> dict[str, Any]:
    summary = run.summary
    status = status_of(summary)
    row: dict[str, Any] = {
        "configuration": configuration.number,
        "series": configuration.series,
        "run_kind": summary.run_kind,
        "repetition": summary.repetition,
        "run": summary.run_id,
        **{name: configuration.values.get(name) for name in SETTINGS},
        "status": status,
        "reason": summary.why_left_out(),
    }
    if status in (FAILED, INTERRUPTED_STATUS):
        return row
    commits = summary.commit_latencies
    reads, writes = run.read_latencies, run.write_latencies
    counts = run.request_counts or {}
    row |= {
        "transports_ended_per_minute": summary.transports_ended_per_minute,
        "commits": None if commits is None else len(commits),
        **{
            f"commit_p{name}": None if commits is None else quantile(commits, p)
            for name, p in (("50", 0.5), ("95", 0.95), ("99", 0.99))
        },
        **{
            f"read_p{name}": None if reads is None else quantile(reads, p)
            for name, p in (("50", 0.5), ("95", 0.95))
        },
        **{
            f"write_p{name}": None if writes is None else quantile(writes, p)
            for name, p in (("50", 0.5), ("95", 0.95))
        },
        **{
            name: counts.get(name)
            for name in (
                "refused_in_window",
                "timed_out_in_window",
                "connection_dropped_in_window",
                "refused_outside_window",
                "timed_out_outside_window",
                "connection_dropped_outside_window",
            )
        },
        "stuck_boxes": None if summary.stuck is None else len(summary.stuck),
    }
    load = run.client_load or {}
    row |= {
        "client_load": load.get("status", NO_DATA),
        "client_cpu_median_percent": load.get("cpu_median_percent"),
        "client_cpu_max_percent": load.get("cpu_max_percent"),
        "client_memory_max_gib": None
        if load.get("memory_max_bytes") is None
        else load["memory_max_bytes"] / 1024**3,
    }
    return row


def unanswered_table(
    found: Sequence[SpecConfiguration], figures: Sequence[RunFigures]
) -> pd.DataFrame:
    """The table of the store requests that got no answer, one row for each, of the runs of
    `found` (a failed or an interrupted run has none), in the order of the runs table and, within
    a run, of their times."""
    rows: list[dict[str, Any]] = []
    for run, configuration in _configurations_of(found, figures):
        if configuration is None or status_of(run.summary) in (
            FAILED,
            INTERRUPTED_STATUS,
        ):
            continue
        for request in run.unanswered or ():
            rows.append(
                {
                    "configuration": configuration.number,
                    "series": configuration.series,
                    "run_kind": run.summary.run_kind,
                    "repetition": run.summary.repetition,
                    "run": run.summary.run_id,
                    "where": "in the window"
                    if request["window"]
                    else "outside the window",
                    "outcome": request["outcome"],
                    "time": request["at"],
                    "wait_seconds": request["seconds"],
                    "reason": request["reason"],
                    "kind": request["kind"],
                    "participant": request["participant"],
                }
            )
    rows.sort(
        key=lambda r: (
            r["configuration"],
            _RUN_KIND_ORDER.index(r["run_kind"]),
            r["repetition"] if r["repetition"] is not None else 0,
            r["run"],
            str(r["time"]),
        )
    )
    return pd.DataFrame(rows, columns=list(UNANSWERED_COLUMNS))


def configurations_table(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    figures: Sequence[RunFigures],
    *,
    runs: Path,
    seed: int,
    resamples: int,
) -> pd.DataFrame:
    """The table of `jms-eval-table` as one row for each configuration and run kind, each
    statistic a column. It is `table.make_table` of the same summaries, flattened."""
    table = make_table(
        spec,
        found,
        [f.summary for f in figures],
        runs=runs,
        seed=seed,
        resamples=resamples,
    )
    rows: list[dict[str, Any]] = []
    for row in table["rows"]:
        for kind in _RUN_KIND_ORDER:
            cell = row["run_kinds"][kind]
            if cell is None:
                continue
            flat: dict[str, Any] = {
                "configurations": ", ".join(map(str, row["configurations"])),
                "series": row["series"],
                "run_kind": kind,
                **{
                    name: row["values"].get(name)
                    for name in SETTINGS
                    if name != "wrapper"
                },
            }
            _flatten("", cell, flat)
            rows.append(flat)
    return pd.DataFrame(rows)


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """The numbers of a cell of the table as columns: a nested key is joined to its parents with
    `_`; an interval is a `_low` and a `_high`; a list of runs or of findings is its length."""
    if isinstance(value, Mapping):
        for key, inner in value.items():
            name = key if not prefix else f"{prefix}_{key}"
            if key == "interval" and inner is not None:
                out[f"{prefix}_interval_low"] = inner[0]
                out[f"{prefix}_interval_high"] = inner[1]
            elif key == "interval_seconds":
                out[name] = ", ".join(f"{v:g}" for v in inner)
            else:
                _flatten(name, inner, out)
    elif isinstance(value, list):
        out[prefix] = len(value)
    else:
        out[prefix] = value


def families(found: Sequence[SpecConfiguration]) -> list[list[SpecConfiguration]]:
    """The families of configurations that differ only in the size of their prefill, each with at
    least two sizes, in the order of their lowest configuration, and each family's configurations
    in rising size."""
    grouped: dict[str, list[SpecConfiguration]] = {}
    for configuration in found:
        if prefill_size(configuration.values.get("prefill")) is None:
            continue
        rest = {k: v for k, v in configuration.values.items() if k != "prefill"}
        grouped.setdefault(json.dumps(rest, sort_keys=True, default=str), []).append(
            configuration
        )
    chosen = [
        sorted(members, key=lambda c: prefill_size(c.values["prefill"]) or 0.0)
        for members in grouped.values()
        if len({prefill_size(c.values["prefill"]) for c in members}) > 1
    ]
    return sorted(chosen, key=lambda family: min(c.number for c in family))


def _describe(family: Sequence[SpecConfiguration]) -> str:
    first = family[0].values
    numbers = ", ".join(str(c.number) for c in family)
    series = f"{family[0].series} " if family[0].series else ""
    return (
        f"configurations {series}{numbers}: {first['topology']}, "
        f"strategies {first['module_strategy']}/{first['wms_strategy']}, "
        f"SHACL {'on' if first['shacl'] else 'off'}"
    )


def draw_charts(
    runs: pd.DataFrame, found: Sequence[SpecConfiguration], folder: Path
) -> list[tuple[str, str]]:
    """Draw the charts of the runs table into `folder`; return each file with its title."""
    frame = runs.copy()
    series = found[0].series if found else ""
    frame["label"] = [
        _label(series, n, k)
        for n, k in zip(frame["configuration"], frame["run_kind"], strict=True)
    ]
    drawn: list[tuple[str, str]] = []

    def one(name: str, title: str, column: str, unit: str) -> None:
        report_charts.category_chart(frame, column, title, unit, folder / name)
        drawn.append((name, title))

    one(
        report_charts.THROUGHPUT_PNG,
        "Throughput: transports ended per minute",
        "transports_ended_per_minute",
        "transports per minute",
    )
    one(report_charts.COMMIT_P95_PNG, "Commit latency, p95", "commit_p95", "seconds")
    report_charts.pair_chart(
        frame,
        [
            ("read_p95", "Read latency, p95", "seconds"),
            ("write_p95", "Write latency, p95", "seconds"),
        ],
        folder / report_charts.READ_WRITE_P95_PNG,
    )
    drawn.append((report_charts.READ_WRITE_P95_PNG, "Read and write latency, p95"))
    for family in families(found):
        size_of = {c.number: prefill_size(c.values["prefill"]) for c in family}
        mine = frame[frame["configuration"].isin(size_of)].copy()
        mine["prefill_size"] = mine["configuration"].map(size_of)
        name = report_charts.family_png(min(c.number for c in family))
        title = f"Against the prefill size, {_describe(family)}"
        report_charts.family_chart(
            mine,
            _describe(family),
            [
                ("transports_ended_per_minute", "Throughput", "transports per minute"),
                ("commit_p95", "Commit latency, p95", "seconds"),
            ],
            folder / name,
        )
        drawn.append((name, title))
    return drawn


def draw_steps(
    steps: pd.DataFrame, found: Sequence[SpecConfiguration], folder: Path
) -> list[tuple[str, str]]:
    """Draw the steps chart of each family of configurations (see `families`) that has steps into
    `folder`; return each file with its title."""
    drawn: list[tuple[str, str]] = []
    for family in families(found):
        numbers = [c.number for c in family]
        mine = steps[steps["configuration"].isin(numbers)]
        if mine.empty:
            continue
        sizes = {c.number: str(c.values["prefill"]) for c in family}
        name = report_charts.steps_png(min(numbers))
        title = f"Where the time goes, step by step, {_describe(family)}"
        report_charts.steps_chart(
            mine,
            sizes,
            _describe(family),
            folder / name,
            names={c.number: c.name for c in family},
        )
        drawn.append((name, title))
    return drawn


def make_report(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    figures: Sequence[RunFigures],
    folder: Path,
    *,
    runs: Path,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
    made_at: datetime | None = None,
    live_checks: Sequence[LiveCheck] | None = None,
) -> Path:
    """Write the report of `figures` into `folder`, which is made if need be: its files from a
    report before are replaced, those of charts that this report does not draw are removed.
    Returns `report.md`'s path.

    Args:
        live_checks: The live checks of the orchestrator's log (`report_points`), or None for no
            log: then the mark of the live checks says `no data`.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for old in (
        *folder.glob("*.png"),
        folder / RUNS_CSV,
        folder / CONFIGURATIONS_CSV,
        folder / STEPS_CSV,
        folder / UNANSWERED_CSV,
        *(
            folder / name
            for name in (
                report_points.MARKS_CSV,
                report_points.HOURS_CSV,
                report_points.PAGE_CACHE_CSV,
                report_points.DEADLOCK_ALARMS_CSV,
                report_points.DEAD_HEARTBEATS_CSV,
            )
        ),
        folder / REPORT_MARKDOWN,
    ):
        old.unlink(missing_ok=True)
    runs_frame, elsewhere = runs_table(found, figures)
    configurations_frame = configurations_table(
        spec, found, figures, runs=runs, seed=seed, resamples=resamples
    )
    # Each CSV keeps the configuration's number and gains the spec's series (jms_kapps 110).
    steps = _with_series(steps_frame(found, figures), spec.series)
    unanswered = unanswered_table(found, figures)
    runs_frame.to_csv(folder / RUNS_CSV, index=False)
    unanswered.to_csv(folder / UNANSWERED_CSV, index=False)
    configurations_frame.to_csv(folder / CONFIGURATIONS_CSV, index=False)
    steps.to_csv(folder / STEPS_CSV, index=False)
    now = made_at or datetime.now(UTC)
    points = _Points.of(found, figures, live_checks, now).with_series(spec.series)
    for name, frame in points.files():
        frame.to_csv(folder / name, index=False)
    charts = [
        *draw_charts(runs_frame, found, folder),
        *draw_steps(steps, found, folder),
        *draw_hours(points.hours, folder, spec.series),
    ]
    path = folder / REPORT_MARKDOWN
    path.write_text(
        _markdown(
            spec,
            runs_frame,
            configurations_frame,
            unanswered,
            charts,
            runs=runs,
            seed=seed,
            resamples=resamples,
            elsewhere=elsewhere,
            made_at=now,
            points=points,
        ),
        encoding="utf-8",
    )
    return path


def update_report(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    runs: Path,
    *,
    fresh: Mapping[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
    live_checks: Path | None = None,
) -> Path:
    """Make the report of the runs below `runs`, into `runs/report`, from the summaries of their
    records (a record without a current summary is read once and summarised). Returns
    `report.md`'s path.

    Args:
        fresh: The record of a run that was just made, as the run returned it, so that its file is
            not read again.
        live_checks: The path of the orchestrator's live-check log (`report_points`), read each
            time; None for no log (every mark of the live checks says `no data`).
    """
    figures = read_all(runs, fresh=fresh)
    return make_report(
        spec,
        found,
        figures,
        runs / REPORT_FOLDER,
        runs=runs,
        seed=seed,
        resamples=resamples,
        live_checks=read_live_checks(live_checks),
    )


def _markdown(
    spec: Spec,
    runs_frame: pd.DataFrame,
    configurations_frame: pd.DataFrame,
    unanswered: pd.DataFrame,
    charts: Sequence[tuple[str, str]],
    *,
    runs: Path,
    seed: int,
    resamples: int,
    elsewhere: int,
    made_at: datetime,
    points: _Points,
) -> str:
    statuses = runs_frame["status"].value_counts()
    counted = ", ".join(
        f"{int(statuses.get(name, 0))} {name}"
        for name in (VALID, INVALID, FAILED, INTERRUPTED_STATUS)
    )
    left_out = runs_frame[runs_frame["status"] != VALID]
    lines = [
        f"# Report of {spec.path.name}",
        "",
        f"Made at {made_at.isoformat()} from the records below `{runs}`: "
        f"{len(runs_frame)} runs ({counted})"
        + (
            f"; {elsewhere} records are of no configuration of the spec"
            if elsewhere
            else ""
        )
        + f". The bootstrap of the configurations table: seed {seed}, {resamples} resamples.",
        "",
        (
            "A valid run is a filled dot in the charts and an invalid run is a ring; a tick is "
            "the median of the valid runs. A failed or an interrupted run has no values and is "
            "not plotted. Every column is in `runs.csv` and `configurations.csv`; the steps of "
            "the runs, where the time goes, are in `steps.csv` and the steps charts; the "
            "unanswered store requests are in `unanswered.csv`; the marks of the runs (the live "
            "checks, other activity, the deadlock alarms, the dead heartbeats) are in `marks.csv`, "
            "and the sections after the unanswered store requests, with their files, say the rest."
        ),
        "",
        "## Runs left out of the statistics",
        "",
    ]
    if left_out.empty:
        lines += ["None.", ""]
    else:
        lines += [
            _table(
                left_out,
                ("configuration", "run_kind", "repetition", "run", "status", "reason"),
            ),
            "",
        ]
    lines += ["## Charts", ""]
    for name, title in charts:
        lines += [f"![{title}]({name})", ""]
    lines += [
        "## Unanswered store requests",
        "",
        (
            "Each store request that got no answer, in the window and outside it, with its "
            "outcome (`timed out`: its client gave up because the time ran out; `connection "
            "dropped`: its connection closed, reset or failed before an answer, or its error "
            "is of neither class or none), its real wait and its reason, the error; the same "
            "rows are in `unanswered.csv`."
        ),
        "",
    ]
    if unanswered.empty:
        lines += ["None.", ""]
    else:
        lines += [
            _table(
                unanswered,
                ("configuration", "run_kind", "repetition", "run", "where")
                + ("outcome", "time", "wait_seconds", "reason"),
            ),
            "",
        ]
    lines += points.markdown()
    lines += [
        "## Client load",
        "",
        (
            "The client host's CPU use (median and highest, in percent of all its CPUs) and "
            "highest memory use (GiB) in the measured window. `no data`: the record has no "
            "sample in the window (a run before the client load was recorded); `not "
            "available`: the host had no `/proc`."
        ),
        "",
        _client_load_table(runs_frame),
        "",
    ]
    lines += [
        "## Configurations",
        "",
        (
            "The statistics of `jms-eval-table`, over the valid runs, with the columns of "
            "`configurations.csv` that matter most."
        ),
        "",
        _table(configurations_frame, _CONFIGURATIONS_SHOWN),
        "",
        "## Runs",
        "",
        _table(runs_frame, _RUNS_SHOWN),
        "",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class _Points:
    """The tables of the report points of one report (`report_points`)."""

    marks: pd.DataFrame
    hours: pd.DataFrame
    page_cache: pd.DataFrame
    deadlocks: pd.DataFrame
    deadlock_counts: pd.DataFrame
    dead_heartbeats: pd.DataFrame

    @staticmethod
    def of(
        found: Sequence[SpecConfiguration],
        figures: Sequence[RunFigures],
        checks: Sequence[LiveCheck] | None,
        now: datetime,
    ) -> _Points:
        entries = entries_of(found, figures)
        sizes = {e.prefill: prefill_size(e.prefill) for e in entries if e.prefill}
        return _Points(
            report_points.marks_table(entries, checks, now),
            report_points.hours_table(entries, _RUN_KIND_ORDER),
            report_points.page_cache_table(entries, _RUN_KIND_ORDER, sizes),
            report_points.deadlock_table(entries),
            report_points.deadlock_counts(entries),
            report_points.dead_heartbeats_table(entries),
        )

    def with_series(self, series: str) -> _Points:
        """The tables with the spec's series beside each configuration's number (jms_kapps
        110)."""
        return _Points(
            *(
                _with_series(frame, series)
                for frame in (
                    self.marks,
                    self.hours,
                    self.page_cache,
                    self.deadlocks,
                    self.deadlock_counts,
                    self.dead_heartbeats,
                )
            )
        )

    def files(self) -> list[tuple[str, pd.DataFrame]]:
        return [
            (report_points.MARKS_CSV, self.marks),
            (report_points.HOURS_CSV, self.hours),
            (report_points.PAGE_CACHE_CSV, self.page_cache),
            (report_points.DEADLOCK_ALARMS_CSV, self.deadlocks),
            (report_points.DEAD_HEARTBEATS_CSV, self.dead_heartbeats),
        ]

    def markdown(self) -> list[str]:
        """The sections of the report points, as lines."""
        marks = self.marks
        live = marks["live_check"].value_counts()
        activity = marks["other_activity"].value_counts()
        marked = marks[
            (marks["live_check"] == report_points.OVERLAP)
            | (marks["other_activity"] == report_points.ACTIVITY)
        ]
        lines = [
            "## Runs marked",
            "",
            (
                "A run is marked when its window overlaps a live check of the orchestrator's "
                "log (`--live-checks`), or when the server watch shows other activity: another "
                "repository's counters (`epoolReads`, `epoolWrites`) rose between the run's start "
                "and the drain's end, or its gauges (`openConnections`, `activeTransactions`) "
                "were above zero at either. `no data`: no log was given, or the record has no "
                'server watch (a run of the "before" series). Every run is in `marks.csv`.'
            ),
            "",
            "Live checks: "
            + ", ".join(
                f"{int(live.get(name, 0))} {name}"
                for name in (
                    report_points.OVERLAP,
                    report_points.NO_OVERLAP,
                    NO_DATA,
                )
            )
            + ". Other activity: "
            + ", ".join(
                f"{int(activity.get(name, 0))} {name}"
                for name in (report_points.ACTIVITY, report_points.NONE, NO_DATA)
            )
            + ".",
            "",
        ]
        if marked.empty:
            lines += ["No run is marked.", ""]
        else:
            lines += [
                _table(
                    marked,
                    ("configuration", "run_kind", "repetition", "run", "live_check")
                    + ("live_checks", "other_activity", "other_activity_why"),
                ),
                "",
            ]
        lines += [
            "## By hour of day",
            "",
            (
                "The valid runs of each configuration and run kind grouped by the hour of day "
                "(UTC) of their window's start: the median of the transports ended per minute "
                "and of the completed writes per minute, and the p95 of the pooled commit "
                "latencies and of the pooled store-request latencies (seconds). The same rows "
                "are in `hours.csv`."
            ),
            "",
        ]
        if self.hours.empty:
            lines += ["None.", ""]
        else:
            lines += [_table(self.hours, report_points.HOURS_COLUMNS), ""]
            for number in sorted(set(self.hours["configuration"])):
                name = report_points.hours_png(number)
                shown = _cell_of(self.hours, "configuration", number)
                lines += [f"![By hour of day, configuration {shown}]({name})", ""]
        lines += [
            "## Global page cache",
            "",
            (
                "The hit share of the server's global page cache for each prefill, from the "
                "window's start to the drain's end: the deltas of the hits and of the misses "
                "between the server watch's two reads, summed over the valid runs of the "
                "prefill. A run whose delta is negative (the counters reset when the server "
                "restarted) or that has no read gives `no data` and is not summed "
                "(`runs_without_data`). The same rows are in `page_cache.csv`."
            ),
            "",
        ]
        if self.page_cache.empty:
            lines += ["None.", ""]
        else:
            lines += [_table(self.page_cache, report_points.PAGE_CACHE_COLUMNS), ""]
        lines += [
            "## Deadlock alarms",
            "",
            (
                "A run in which a module raised a deadlock alarm stays invalid. The runs are "
                "listed apart here, so that they can be counted in later; the same rows are in "
                "`deadlock_alarms.csv`."
            ),
            "",
        ]
        if self.deadlocks.empty:
            lines += ["None.", ""]
        else:
            lines += [
                _table(self.deadlock_counts, self.deadlock_counts.columns),
                "",
                _table(self.deadlocks, report_points.DEADLOCK_COLUMNS),
                "",
            ]
        lines += [
            "## Dead heartbeats",
            "",
            (
                "A participant's heartbeat is dead when its last heartbeat write lies more "
                "than the live-heartbeat age (the run setting `live_heartbeat_age_seconds`, 90 s) "
                "before the start of its last other span. A run without spans has `no data` "
                "in `marks.csv`. The same rows are in `dead_heartbeats.csv`."
            ),
            "",
        ]
        if self.dead_heartbeats.empty:
            lines += ["None.", ""]
        else:
            lines += [
                _table(self.dead_heartbeats, report_points.DEAD_HEARTBEAT_COLUMNS),
                "",
            ]
        return lines


def _client_load_table(runs_frame: pd.DataFrame) -> str:
    """The client load of each run that has values, as a Markdown table: a run with no figure
    shows its state (`no data` or `not available`) in place of the numbers. A configuration's
    number shows with the spec's series, such as `before 7`, as in the other tables."""
    shown = runs_frame[~runs_frame["status"].isin([FAILED, INTERRUPTED_STATUS])]
    if shown.empty:
        return "None."
    columns = (
        "configuration",
        "run_kind",
        "repetition",
        "run",
        "client_cpu_median_percent",
        "client_cpu_max_percent",
        "client_memory_max_gib",
    )
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for _, row in shown.iterrows():
        measured = row["client_load"] == MEASURED
        cells = [
            _named_cell(row, c)
            if c in columns[:4] or measured
            else str(row["client_load"])
            for c in columns
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _table(frame: pd.DataFrame, columns: Sequence[str]) -> str:
    """`frame`'s `columns` as a Markdown table; a number with four significant digits. A
    configuration's number shows with the spec's series where `frame` holds it, such as
    `before 7` (jms_kapps 110)."""
    shown = [c for c in columns if c in frame.columns and c != "series"]
    lines = ["| " + " | ".join(shown) + " |", "|" + "---|" * len(shown)]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(_named_cell(row, c) for c in shown) + " |")
    return "\n".join(lines)


def _named_cell(row: pd.Series, column: str) -> str:
    """The cell of `column` in `row`: a configuration's number with the series of the row, where
    it has one."""
    value = _cell(row[column])
    series = (
        row.get("series") if column in ("configuration", "configurations") else None
    )
    if not value or not isinstance(series, str) or not series:
        return value
    return f"{series} {value}"


def _cell_of(frame: pd.DataFrame, column: str, value: Any) -> str:
    """`value` of `column` as a cell of `frame` shows it, with the series of its rows."""
    series = frame["series"].iloc[0] if "series" in frame.columns and len(frame) else ""
    return f"{series} {value}" if series else str(value)


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="The report of the runs of a spec, tables and charts beside their records "
        "(the measurement harness, iteration one). It needs no store and runs nothing.",
    )
    parser.add_argument(
        "spec", type=Path, help="The spec, TOML, whose runs the report holds"
    )
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("runs"),
        help="The folder of the runs' folders, as the sweep was given it; the report is "
        f"written into its folder {REPORT_FOLDER}/ (default: runs)",
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
        help=f"The seed of the configurations table's bootstrap (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--resamples",
        type=int,
        default=DEFAULT_RESAMPLES,
        help="How many resamples each bootstrap interval draws "
        f"(default: {DEFAULT_RESAMPLES})",
    )
    parser.add_argument(
        "--live-checks",
        type=Path,
        default=None,
        metavar="LOG",
        help="The orchestrator's live-check log, JSON lines with event, at, check and "
        "repository; a run that overlaps a live check is marked. Without it, the mark says "
        "no data (default: none)",
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
    path = update_report(
        spec,
        found,
        args.runs,
        seed=args.seed,
        resamples=args.resamples,
        live_checks=args.live_checks,
    )
    print(f"{PROGRAM}: the report is at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
