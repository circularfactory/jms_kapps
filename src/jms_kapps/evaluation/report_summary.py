"""What the report of a spec (`report`) keeps of one run: its summary, a small JSON file beside the
run's record. A record is large (24 to 90 MB, mostly its store requests), so each is read once; the
summary holds what the tables and the charts need, and a later report reads the summaries and not
the records again.

**The file** is `report-summary.json` in the run's folder, beside `record.json`. It holds its
`format`, the size and the modification time of the record it was made from, the numbers the table
of a spec reads of a record (`summary.RunSummary`), and what the report adds: the latencies of the
accepted store requests of the window, logins left out, kept apart by their kind (a read, a write,
or another), the requests the store refused, those that timed out and those whose connection
dropped, inside the window and outside it, and, for a run that counts and has spans, its steps (`steps`: the minutes of the window
and, for each role and step, the seconds of its spans in the window and the sizes of the store's
answers, `report_steps`), so that the spans are read once and the runs can be pooled. Since
format 3 it also holds each unanswered store request (`unanswered`: its time, its real wait, its
reason, and whether it was in the window) and the client host's load in the window (`client_load`:
the median and the highest CPU use and the highest memory use of the samples in the window, or why
there are none). Since format 4 it also holds the report points of the run (`points`: the hour of
its window, other activity on the server, the page cache's deltas, the deadlock alarms and the dead
heartbeats, `report_points`). Since format 5 the requests that timed out and those whose connection
dropped are two counts, and each unanswered request says its `outcome` (jms_kapps 109): a request
that a record written before says "timed out" is classified by its error
(`store_requests.outcome_of`).

**When it is made again.** A summary whose `format` is not `FORMAT` is made again from its record,
and so is one whose record is not the size or the age it was made from. A summary that cannot be
read is made again, too. A record that cannot be read has no summary.

**The format** is a whole number, raised when the file's keys change: 2 added `steps`, and a
summary of format 1 is made again from its record and its spans. Format 3 added `unanswered` and
`client_load`, and a summary of format 2 is made again. Format 4 added `points`, and a summary of
format 3 is made again. Format 5 counted the dropped connections apart, and a summary of format 4,
which counted them as timed out, is made again. (Added on 2026-10-12, jms_kapps 86; format 2 on
2026-10-12, jms_kapps 87; format 3 on 2026-10-12, jms_kapps 91; format 4 on 2026-10-12, jms_kapps
97; format 5 on 2026-10-14, jms_kapps 109; interim, jms_kapps 83.)
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jms_kapps.evaluation import client_load, report_points, store_requests
from jms_kapps.evaluation.report_steps import (
    RunSteps,
    read_run_spans,
    steps_of,
)
from jms_kapps.evaluation.summary import RunSummary, summary_of

# The file of a run's summary, beside its record, and the format it is written in.
SUMMARY_FILE = "report-summary.json"
RECORD_FILE = "record.json"
FORMAT = 5

# What a run's client load figures say when they hold no figure: the record has no samples (a run
# of the "before" series), none lies in the window, or the host had no `/proc`.
MEASURED = "measured"
NO_DATA = "no data"
NOT_AVAILABLE = client_load.NOT_AVAILABLE


@dataclass(frozen=True)
class RunFigures:
    """What the report reads of one run.

    `summary` is what the table of a spec reads (its `store_request_latencies` hold the reads, the
    writes and the other requests together). `read_latencies` and `write_latencies` are the
    latencies of the accepted store requests of the window that are reads and writes; None for a
    run without store requests. `request_counts` holds the requests the store refused, those that
    timed out and those whose connection dropped, inside the window and outside it, or None. `other_latencies` are those of the
    accepted requests of the window that are neither (logins left out). `steps` are the steps of a
    run that counts and has spans (`report_steps`), else None. `unanswered` lists each store
    request that got no answer, in the window and outside it, in the order of their times, each
    with its `outcome` (timed out or connection dropped, jms_kapps 109), its `at`, its real wait
    `seconds`, its `reason`, its `kind`, its `participant` and whether it was in the `window`;
    None for a run without store
    requests. `client_load` is the client host's load in the window (`client_load_of`). `points` is
    what the report points need of the run (`report_points.points_of`: the hour of its window,
    other activity on the server, the page cache, the deadlock alarms and the dead heartbeats).
    """

    summary: RunSummary
    read_latencies: Sequence[float] | None
    write_latencies: Sequence[float] | None
    other_latencies: Sequence[float] | None
    request_counts: Mapping[str, int] | None
    steps: RunSteps | None = None
    unanswered: Sequence[Mapping[str, Any]] | None = None
    client_load: Mapping[str, Any] | None = None
    points: Mapping[str, Any] | None = None


def client_load_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The client host's load in the window, from the samples of `record`: `status` (`measured`;
    `no data` where the record holds no sample or none lies in the window, as a record of the
    "before" series; `not available` where the host had no `/proc`), and, for a measured one,
    the median and the highest CPU use in percent (None where no sample holds one), the highest
    memory use in bytes, and how many samples lie in the window."""
    figures: dict[str, Any] = {
        "status": NO_DATA,
        "cpu_median_percent": None,
        "cpu_max_percent": None,
        "memory_max_bytes": None,
        "samples_in_window": 0,
    }
    load = record.get("client_load")
    if not isinstance(load, Mapping):
        return figures
    if load.get("status") == client_load.NOT_AVAILABLE:
        return figures | {"status": NOT_AVAILABLE}
    window = record.get("window") or (record.get("measures") or {}).get("window") or {}
    if window.get("start") is None or window.get("end") is None:
        return figures
    start = datetime.fromisoformat(window["start"])
    end = datetime.fromisoformat(window["end"])
    inside = [
        sample
        for sample in load.get("samples") or []
        if start <= datetime.fromisoformat(sample["at"]) <= end
    ]
    if not inside:
        return figures
    cpu = [
        float(sample["cpu_percent"])
        for sample in inside
        if sample.get("cpu_percent") is not None
    ]
    return figures | {
        "status": MEASURED,
        "cpu_median_percent": statistics.median(cpu) if cpu else None,
        "cpu_max_percent": max(cpu) if cpu else None,
        "memory_max_bytes": max(int(sample["memory_used_bytes"]) for sample in inside),
        "samples_in_window": len(inside),
    }


def figures_of(record: Mapping[str, Any], folder: Path | None = None) -> RunFigures:
    """The figures of `record`, a record as a run of the harness writes it. `folder` is the run's
    folder, whose spans give the run's steps if the run counts."""
    summary = summary_of(record)
    # The spans are read once, for the steps and for the dead heartbeats.
    spans = None if folder is None else read_run_spans(folder)
    steps = steps_of(record, spans) if spans and summary.counts else None
    load = client_load_of(record)
    points = report_points.points_of(record, spans)
    requests = (record.get("measures") or {}).get("store_requests")
    if requests is None:
        return RunFigures(summary, None, None, None, None, steps, None, load, points)
    reads: list[float] = []
    writes: list[float] = []
    others: list[float] = []
    unanswered: list[dict[str, Any]] = []
    counts = {
        f"{what}_{where}": 0
        for where in ("in_window", "outside_window")
        for what in ("refused", "timed_out", "connection_dropped")
    }
    named = {
        store_requests.REFUSED: "refused",
        store_requests.TIMED_OUT: "timed_out",
        store_requests.CONNECTION_DROPPED: "connection_dropped",
    }
    for request in requests:
        # A request that a record written before says "timed out" is classified by its error.
        outcome = store_requests.outcome_of(request)
        if outcome in named:
            where = "in_window" if request.get("window") else "outside_window"
            counts[f"{named[outcome]}_{where}"] += 1
            if outcome in store_requests.UNANSWERED:
                # The request keeps its real wait in `seconds`; it is listed, and never among the
                # latencies of the answered requests.
                unanswered.append(
                    {
                        "outcome": outcome,
                        "at": request.get("at"),
                        "seconds": float(request["seconds"]),
                        "reason": request.get("reason"),
                        "kind": request.get("kind"),
                        "participant": request.get("participant"),
                        "window": bool(request.get("window")),
                    }
                )
        elif (
            outcome == store_requests.ACCEPTED
            and request.get("window")
            and request.get("kind") != store_requests.LOGIN
        ):
            kind = request.get("kind")
            seconds = float(request["seconds"])
            if kind == store_requests.READ:
                reads.append(seconds)
            elif kind == store_requests.WRITE:
                writes.append(seconds)
            else:
                others.append(seconds)
    unanswered.sort(key=lambda found: str(found["at"]))
    return RunFigures(
        summary, reads, writes, others, counts, steps, unanswered, load, points
    )


def _to_json(figures: RunFigures, record_path: Path) -> dict[str, Any]:
    summary = asdict(figures.summary)
    # The latencies are kept once, apart by kind, and put together again when they are read.
    del summary["store_request_latencies"]
    stat = record_path.stat()
    return {
        "format": FORMAT,
        "record": {"bytes": stat.st_size, "modified_ns": stat.st_mtime_ns},
        "summary": summary,
        "latencies": None
        if figures.read_latencies is None
        else {
            "read": list(figures.read_latencies),
            "write": list(figures.write_latencies or ()),
            "other": list(figures.other_latencies or ()),
        },
        "requests": None
        if figures.request_counts is None
        else dict(figures.request_counts),
        "steps": None if figures.steps is None else figures.steps.to_json(),
        "unanswered": None
        if figures.unanswered is None
        else [dict(found) for found in figures.unanswered],
        "client_load": None
        if figures.client_load is None
        else dict(figures.client_load),
        "points": None if figures.points is None else dict(figures.points),
    }


def _from_json(found: Mapping[str, Any]) -> RunFigures:
    latencies = found["latencies"]
    summary = dict(found["summary"])
    steps = None if found["steps"] is None else RunSteps.from_json(found["steps"])
    unanswered, load = found["unanswered"], found["client_load"]
    points = found["points"]
    if latencies is None:
        return RunFigures(
            RunSummary(**summary, store_request_latencies=None),
            None,
            None,
            None,
            None,
            steps,
            unanswered,
            load,
            points,
        )
    every = [*latencies["read"], *latencies["write"], *latencies["other"]]
    return RunFigures(
        RunSummary(**summary, store_request_latencies=every),
        latencies["read"],
        latencies["write"],
        latencies["other"],
        found["requests"],
        steps,
        unanswered,
        load,
        points,
    )


def write_summary(folder: Path, figures: RunFigures) -> Path:
    """Write the summary of the run whose record is in `folder`, beside it, and return its path."""
    path = folder / SUMMARY_FILE
    path.write_text(
        json.dumps(_to_json(figures, folder / RECORD_FILE), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def read_figures(
    folder: Path, *, fresh: Mapping[str, Any] | None = None
) -> RunFigures | None:
    """The figures of the run in `folder`: from its summary where that is current, else from its
    record, which is read once and then summarised beside it. None where the folder holds no
    record that can be read.

    Args:
        fresh: The record of the run in `folder` as the run just returned it, so that it is not
            read from the file again.
    """
    record_path = folder / RECORD_FILE
    try:
        stat = record_path.stat()
    except OSError:
        return None
    if fresh is None:
        current = _current(folder, stat.st_size, stat.st_mtime_ns)
        if current is not None:
            return current
        try:
            fresh = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    try:
        figures = figures_of(fresh, folder)
    except (TypeError, KeyError, AttributeError, ValueError):
        return None
    try:
        write_summary(folder, figures)
    except OSError:
        # A folder that cannot be written to still gives its figures; the next report reads the
        # record again.
        pass
    return figures


def _current(folder: Path, size: int, modified_ns: int) -> RunFigures | None:
    try:
        found = json.loads((folder / SUMMARY_FILE).read_text(encoding="utf-8"))
        if found["format"] != FORMAT or found["record"] != {
            "bytes": size,
            "modified_ns": modified_ns,
        }:
            return None
        return _from_json(found)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def read_all(runs: Path, *, fresh: Mapping[str, Any] | None = None) -> list[RunFigures]:
    """The figures of each run below `runs`, one folder each, in the order of their folders (the
    order in which `summary.read_summaries` reads them, which the table's bootstrap depends on).
    A folder with no record that can be read is none of them.

    Args:
        fresh: The record of a run just made, as the run returned it (see `read_figures`).
    """
    found = []
    for path in sorted(runs.glob(f"*/{RECORD_FILE}")):
        is_fresh = fresh is not None and path.parent.name == str(fresh.get("run_id"))
        figures = read_figures(path.parent, fresh=fresh if is_fresh else None)
        if figures is not None:
            found.append(figures)
    return found
