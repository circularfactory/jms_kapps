"""The report points: the runs of the "after"
series go on around the clock, and other use of the KIT GraphDB may overlap them. The report groups
the runs by hour, marks a run that overlaps a live check or whose server watch shows other
activity, shows the global page cache's hit share, counts the runs with a deadlock alarm apart and
shows each dead heartbeat. Interim names: every name
here, the files `marks.csv`, `hours.csv`, `page_cache.csv`, `deadlock_alarms.csv` and
`dead_heartbeats.csv`, the option `--live-checks` and the summary's key `points`.)

**What a run keeps** (`points_of`, in the run's summary, so that a record and its spans are read
once): the window and the hour of its start (UTC), whether the server watch shows other activity,
the page cache's deltas, each deadlock alarm and each dead heartbeat. Only the overlap with a live
check is worked out when the report is built, because the live-check log grows while the runs go on.

**The live-check log** is the orchestrator's file of JSON lines, one at the start and one at the
end of each live check on the KIT GraphDB, with the fields `event` (`start` or `end`), `at` (ISO
8601, UTC, seconds), `check` (free text) and `repository`. The report reads it from the path of
`--live-checks`; without a path, or where the file cannot be read, every mark says `no data`. A
line that is no event is left out. A `start` pairs with the next `end` that has the same `check`
and `repository`; a `start` without an `end` counts as running until the report is built. A run
overlaps a check when its window and the check's span share a moment (touching counts).

**Other activity.** The server watch reads the monitoring view of every repository at the run's
start and at the drain's end (`server.repository_monitors_at_the_start` and `..._at_the_drain_end`).
A run is marked when, for a repository other than its own, a cumulative counter (`epoolReads`,
`epoolWrites`) rose between the two reads, or a gauge (`openConnections`, `activeTransactions`) was
above zero at either. A repository that was not read (`read: false`, an inactive one), a refused
view and a counter that fell (the server restarted) are no activity. Where no other repository could
be read at all, the mark says `no data`, not `none`.

**The page cache.** The deltas of the global page cache's hits and misses between
`server.structures_at_the_window_start` and `server.structures_at_the_drain_end`. A negative delta
(the counters reset when the server restarted), a refused view or a view without the two counters
makes that run's share `no data`. The hit share of a prefill is the sum of the hits over the sum of
the hits and misses of the valid runs with data.

**The dead heartbeat.** A heartbeat is a participant's write of its last-heartbeat time: a store
write that the store accepted and whose step category is `heartbeat update` (`report_steps`); there
are no heartbeat spans of their own. A participant's heartbeat is dead when its last heartbeat write
started more than the live-heartbeat age (`live_heartbeat_age_seconds` of the record's run settings;
90 s where the record has none) before the start of its last other span. A participant that wrote
no heartbeat at all is not listed. A run without spans has `no data`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from jms_kapps.common.services import MAX_HEARTBEAT_AGE_SECONDS
from jms_kapps.evaluation import report_charts, store_requests
from jms_kapps.evaluation.observation import DEADLOCK_ALARM, Span
from jms_kapps.evaluation.report_steps import category_of
from jms_kapps.evaluation.stats import quantile
from jms_kapps.evaluation.summary import RunSummary

# What a mark says where there is nothing to tell it by.
NO_DATA = "no data"

# The marks of the live checks and of other activity.
OVERLAP = "overlap"
NO_OVERLAP = "no overlap"
ACTIVITY = "activity"
NONE = "none"

# The category of the store write that is a heartbeat (`report_steps.WRITE_CATEGORIES`).
HEARTBEAT_CATEGORY = "heartbeat update"

# The monitoring figures of a repository: counters that only rise, and gauges.
COUNTERS = ("epoolReads", "epoolWrites")
GAUGES = ("openConnections", "activeTransactions")

# The files of the report points, in the report's folder.
MARKS_CSV = "marks.csv"
HOURS_CSV = "hours.csv"
PAGE_CACHE_CSV = "page_cache.csv"
DEADLOCK_ALARMS_CSV = "deadlock_alarms.csv"
DEAD_HEARTBEATS_CSV = "dead_heartbeats.csv"


def hours_png(number: int) -> str:
    """The file of the by-hour chart of configuration `number`."""
    return f"hours-{number}.png"


# --- The live-check log ---


@dataclass(frozen=True)
class LiveCheck:
    """One live check of the log: its `check` text, its `repository`, when it started and when it
    ended (None where the log has no end for it: it runs until the report is built)."""

    check: str
    repository: str
    start: datetime
    end: datetime | None


def _time(text: Any) -> datetime | None:
    try:
        found = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return found if found.tzinfo is not None else found.replace(tzinfo=UTC)


def read_live_checks_text(lines: Iterable[str]) -> list[LiveCheck]:
    """The live checks of the lines of a log, in the order of their starts. A line that is no
    event (no JSON object, an unknown event, a missing field, a time that is none) is left out."""
    starts: list[tuple[datetime, str, str]] = []
    ends: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for line in lines:
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        at = _time(raw.get("at"))
        check, repository = raw.get("check"), raw.get("repository")
        if (
            raw.get("event") not in ("start", "end")
            or at is None
            or not isinstance(check, str)
            or not isinstance(repository, str)
        ):
            continue
        if raw["event"] == "start":
            starts.append((at, check, repository))
        else:
            ends[(check, repository)].append(at)
    found = []
    for start, check, repository in sorted(starts, key=lambda s: s[0]):
        later = [at for at in ends[(check, repository)] if at >= start]
        found.append(LiveCheck(check, repository, start, min(later, default=None)))
    return found


def read_live_checks(path: Path | None) -> list[LiveCheck] | None:
    """The live checks of the log at `path`, or None where there is no path or the file cannot be
    read: then every mark says `no data`."""
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return read_live_checks_text(text.splitlines())


def overlapping(
    checks: Sequence[LiveCheck], start: datetime, end: datetime, now: datetime
) -> list[str]:
    """The names of the live checks that share a moment with the window from `start` to `end`; a
    check without an end runs until `now`."""
    return [
        found.check
        for found in checks
        if found.start <= end and (found.end or now) >= start
    ]


# --- What a run keeps ---


def _answer(view: Any) -> Mapping[str, Any] | None:
    """The answer of a server view that was read, or None (not read, refused, failed)."""
    if not isinstance(view, Mapping):
        return None
    answer = view.get("answer")
    return answer if isinstance(answer, Mapping) else None


def _number(value: Any) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def other_activity(record: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the server watch of `record` shows activity of other repositories: `status`
    (`activity`, `none` or `no data`) and `why`, one line for each sign of it."""
    server = record.get("server")
    server = server if isinstance(server, Mapping) else {}
    start = server.get("repository_monitors_at_the_start")
    end = server.get("repository_monitors_at_the_drain_end")
    start = start if isinstance(start, Mapping) else {}
    end = end if isinstance(end, Mapping) else {}
    own = record.get("repository")
    why: list[str] = []
    seen = False
    for name in sorted({*start, *end} - {own}):
        before, after = _answer(start.get(name)), _answer(end.get(name))
        if before is None and after is None:
            continue
        seen = True
        for moment, answer in (("start", before), ("end", after)):
            for gauge in GAUGES:
                value = _number((answer or {}).get(gauge))
                if value is not None and value > 0:
                    why.append(f"{name}: {gauge} {value:g} at the {moment}")
        if before is not None and after is not None:
            for counter in COUNTERS:
                first, second = (
                    _number(before.get(counter)),
                    _number(after.get(counter)),
                )
                if first is not None and second is not None and second > first:
                    why.append(f"{name}: {counter} +{second - first:g}")
    status = NO_DATA if not seen else ACTIVITY if why else NONE
    return {"status": status, "why": why}


def _cache_counts(view: Any) -> tuple[int, int] | None:
    """The hits and the misses of a structures view, or None where it holds no two counters. The
    KIT server answers `{"cacheHit": n, "cacheMiss": n}` (seen in a live check); the
    nested `cache.hit` and `cache.miss` and the Prometheus names (`graphdb_cache_hit`,
    `graphdb_cache_miss`) are read too, and harm nothing."""
    answer = _answer(view)
    if answer is None:
        return None
    nested = answer.get("cache")
    nested = nested if isinstance(nested, Mapping) else {}
    for hit, miss in (
        (answer.get("cacheHit"), answer.get("cacheMiss")),
        (nested.get("hit"), nested.get("miss")),
        (answer.get("graphdb_cache_hit"), answer.get("graphdb_cache_miss")),
    ):
        hits, misses = _number(hit), _number(miss)
        if hits is not None and misses is not None:
            return int(hits), int(misses)
    return None


def page_cache(record: Mapping[str, Any]) -> dict[str, Any]:
    """The page cache's hits and misses between the window's start and the drain's end: `status`
    (`measured` or `no data`: no view, or a negative delta), `hits` and `misses`."""
    server = record.get("server")
    server = server if isinstance(server, Mapping) else {}
    before = _cache_counts(server.get("structures_at_the_window_start"))
    after = _cache_counts(server.get("structures_at_the_drain_end"))
    none = {"status": NO_DATA, "hits": None, "misses": None}
    if before is None or after is None:
        return none
    hits, misses = after[0] - before[0], after[1] - before[1]
    if hits < 0 or misses < 0:
        return none
    return {"status": "measured", "hits": hits, "misses": misses}


def deadlock_alarms(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each deadlock alarm that the checks of `record` found: its `module` and the time `at`."""
    return [
        {"module": check.get("module"), "at": check.get("at")}
        for check in record.get("failed_checks") or []
        if check.get("check") == DEADLOCK_ALARM
    ]


def _is_heartbeat(span: Span) -> bool:
    attributes = span.attributes
    return (
        bool(attributes.get("store.request"))
        and attributes.get("store.kind") == store_requests.WRITE
        and attributes.get("store.outcome") == store_requests.ACCEPTED
        and category_of(store_requests.WRITE, str(attributes.get("store.text") or ""))
        == HEARTBEAT_CATEGORY
    )


def dead_heartbeats(spans: Sequence[Span], age_seconds: float) -> list[dict[str, Any]]:
    """The participants of `spans` whose last heartbeat write started more than `age_seconds`
    before the start of their last other span, by participant: the `participant`, the time of its
    `last_heartbeat`, that of its `last_other_span` and the `gap_seconds` between them."""
    beats: dict[str, datetime] = {}
    others: dict[str, datetime] = {}
    for span in spans:
        target = beats if _is_heartbeat(span) else others
        if span.participant not in target or span.start > target[span.participant]:
            target[span.participant] = span.start
    found = []
    for participant in sorted(beats):
        last = others.get(participant)
        if last is None:
            continue
        gap = (last - beats[participant]).total_seconds()
        if gap > age_seconds:
            found.append(
                {
                    "participant": participant,
                    "last_heartbeat": beats[participant].isoformat(),
                    "last_other_span": last.isoformat(),
                    "gap_seconds": gap,
                }
            )
    return found


def heartbeat_age(record: Mapping[str, Any]) -> float:
    """The live-heartbeat age of the record's run settings, in seconds (the middleware's
    `MAX_HEARTBEAT_AGE_SECONDS` where the record holds none)."""
    settings = record.get("run_settings")
    found = (
        _number(settings.get("live_heartbeat_age_seconds"))
        if isinstance(settings, Mapping)
        else None
    )
    return MAX_HEARTBEAT_AGE_SECONDS if found is None else found


def points_of(
    record: Mapping[str, Any], spans: Sequence[Span] | None
) -> dict[str, Any]:
    """What the summary of the run of `record` keeps for the report points (`spans`: its spans,
    or None for a run that has none)."""
    window = record.get("window") or (record.get("measures") or {}).get("window") or {}
    start, end = _time(window.get("start")), _time(window.get("end"))
    return {
        "window": None
        if start is None or end is None
        else {"start": start.isoformat(), "end": end.isoformat()},
        "hour": None if start is None else start.astimezone(UTC).hour,
        "other_activity": other_activity(record),
        "page_cache": page_cache(record),
        "deadlock_alarms": deadlock_alarms(record),
        "dead_heartbeats": None
        if not spans
        else dead_heartbeats(spans, heartbeat_age(record)),
    }


# --- The tables ---


@dataclass(frozen=True)
class Entry:
    """A run of a configuration as the tables of the report points read it."""

    configuration: int | None
    prefill: str | None
    summary: RunSummary
    points: Mapping[str, Any] | None
    status: str


def _where(entry: Entry) -> dict[str, Any]:
    summary = entry.summary
    return {
        "configuration": entry.configuration,
        "run_kind": summary.run_kind,
        "repetition": summary.repetition,
        "run": summary.run_id,
    }


MARKS_COLUMNS = (
    "configuration",
    "run_kind",
    "repetition",
    "run",
    "status",
    "window_start",
    "hour",
    "live_check",
    "live_checks",
    "other_activity",
    "other_activity_why",
    "deadlock_alarms",
    "dead_heartbeats",
)


def marks_table(
    entries: Iterable[Entry], checks: Sequence[LiveCheck] | None, now: datetime
) -> pd.DataFrame:
    """The marks of each run: the hour of its window's start, whether it overlaps a live check
    (`checks`: None for no log) and whether the server watch shows other activity, how many
    deadlock alarms it had, and how many dead heartbeats (`no data` for a run without spans)."""
    rows = []
    for entry in entries:
        points = entry.points or {}
        window = points.get("window")
        names: list[str] = []
        if checks is None or window is None:
            live = NO_DATA
        else:
            names = overlapping(
                checks,
                datetime.fromisoformat(window["start"]),
                datetime.fromisoformat(window["end"]),
                now,
            )
            live = OVERLAP if names else NO_OVERLAP
        activity = points.get("other_activity") or {"status": NO_DATA, "why": []}
        dead = points.get("dead_heartbeats")
        rows.append(
            {
                **_where(entry),
                "status": entry.status,
                "window_start": None if window is None else window["start"],
                "hour": points.get("hour"),
                "live_check": live,
                "live_checks": "; ".join(dict.fromkeys(names)),
                "other_activity": activity["status"],
                "other_activity_why": "; ".join(activity["why"]),
                "deadlock_alarms": len(points.get("deadlock_alarms") or []),
                "dead_heartbeats": NO_DATA if dead is None else len(dead),
            }
        )
    return pd.DataFrame(rows, columns=list(MARKS_COLUMNS))


HOURS_COLUMNS = (
    "configuration",
    "run_kind",
    "hour",
    "runs",
    "transports_ended_per_minute",
    "completed_writes_per_minute",
    "commit_p95",
    "store_request_p95",
)


def _middle(values: Iterable[float | None]) -> float | None:
    return quantile([v for v in values if v is not None], 0.5)


def _pooled_p95(values: Iterable[Sequence[float] | None]) -> float | None:
    return quantile([x for v in values for x in v or ()], 0.95)


def hours_table(entries: Iterable[Entry], kind_order: Sequence[str]) -> pd.DataFrame:
    """For each configuration, run kind and hour of day (UTC, the hour of the window's start)
    the valid runs of that hour: how many, the median of their transports ended per minute and
    of their completed writes per minute, and the p95 of their pooled commit latencies and of
    their pooled store-request latencies."""
    groups: dict[tuple[int, str, int], list[RunSummary]] = defaultdict(list)
    for entry in entries:
        hour = (entry.points or {}).get("hour")
        if (
            entry.status == report_charts.VALID
            and hour is not None
            and entry.configuration is not None
        ):
            groups[(entry.configuration, entry.summary.run_kind, hour)].append(
                entry.summary
            )
    rows = []
    for (number, kind, hour), summaries in sorted(
        groups.items(), key=lambda g: (g[0][0], kind_order.index(g[0][1]), g[0][2])
    ):
        rows.append(
            {
                "configuration": number,
                "run_kind": kind,
                "hour": hour,
                "runs": len(summaries),
                "transports_ended_per_minute": _middle(
                    s.transports_ended_per_minute for s in summaries
                ),
                "completed_writes_per_minute": _middle(
                    s.completed_writes_per_minute for s in summaries
                ),
                "commit_p95": _pooled_p95(s.commit_latencies for s in summaries),
                "store_request_p95": _pooled_p95(
                    s.store_request_latencies for s in summaries
                ),
            }
        )
    return pd.DataFrame(rows, columns=list(HOURS_COLUMNS))


PAGE_CACHE_COLUMNS = (
    "prefill",
    "run_kind",
    "runs",
    "runs_without_data",
    "hits",
    "misses",
    "hit_share",
)


def page_cache_table(
    entries: Iterable[Entry],
    kind_order: Sequence[str],
    size_of: Mapping[str, float | None],
) -> pd.DataFrame:
    """For each prefill and run kind, the hits and misses of the global page cache summed over the
    valid runs with data, the runs that gave data and those that did not, and the hit share
    (`no data` where no run gave data). `size_of` gives the size of each prefill in statements,
    which the rows are sorted by."""
    total: dict[tuple[str, str], dict[str, int]] = {}
    for entry in entries:
        if entry.status != report_charts.VALID or entry.prefill is None:
            continue
        mine = total.setdefault(
            (entry.prefill, entry.summary.run_kind),
            {"runs": 0, "without": 0, "hits": 0, "misses": 0},
        )
        cache = (entry.points or {}).get("page_cache") or {}
        if cache.get("status") == "measured":
            mine["runs"] += 1
            mine["hits"] += cache["hits"]
            mine["misses"] += cache["misses"]
        else:
            mine["without"] += 1
    rows = []
    for (prefill, kind), mine in sorted(
        total.items(),
        key=lambda t: (
            size_of.get(t[0][0]) is None,
            size_of.get(t[0][0]) or 0.0,
            t[0][0],
            kind_order.index(t[0][1]),
        ),
    ):
        reads = mine["hits"] + mine["misses"]
        rows.append(
            {
                "prefill": prefill,
                "run_kind": kind,
                "runs": mine["runs"],
                "runs_without_data": mine["without"],
                "hits": mine["hits"],
                "misses": mine["misses"],
                "hit_share": mine["hits"] / reads if reads else NO_DATA,
            }
        )
    return pd.DataFrame(rows, columns=list(PAGE_CACHE_COLUMNS))


DEADLOCK_COLUMNS = (
    "configuration",
    "run_kind",
    "repetition",
    "run",
    "alarms",
    "modules",
    "first_at",
)


def deadlock_table(entries: Iterable[Entry]) -> pd.DataFrame:
    """The runs with a deadlock alarm, apart: how many alarms each had, in which modules, and the
    time of the first."""
    rows = []
    for entry in entries:
        alarms = (entry.points or {}).get("deadlock_alarms") or []
        if not alarms:
            continue
        rows.append(
            {
                **_where(entry),
                "alarms": len(alarms),
                "modules": ", ".join(sorted({str(a["module"]) for a in alarms})),
                "first_at": min(str(a["at"]) for a in alarms),
            }
        )
    return pd.DataFrame(rows, columns=list(DEADLOCK_COLUMNS))


def deadlock_counts(entries: Sequence[Entry]) -> pd.DataFrame:
    """For each configuration, how many of its runs had a deadlock alarm, and how many runs it
    has."""
    runs: dict[int, int] = defaultdict(int)
    alarmed: dict[int, int] = defaultdict(int)
    for entry in entries:
        if entry.configuration is None:
            continue
        runs[entry.configuration] += 1
        if (entry.points or {}).get("deadlock_alarms"):
            alarmed[entry.configuration] += 1
    return pd.DataFrame(
        [
            {"configuration": n, "runs_with_alarm": alarmed[n], "runs": runs[n]}
            for n in sorted(runs)
        ],
        columns=["configuration", "runs_with_alarm", "runs"],
    )


DEAD_HEARTBEAT_COLUMNS = (
    "configuration",
    "run_kind",
    "repetition",
    "run",
    "participant",
    "last_heartbeat",
    "last_other_span",
    "gap_seconds",
)


def dead_heartbeats_table(entries: Iterable[Entry]) -> pd.DataFrame:
    """Each dead heartbeat of each run."""
    rows = [
        {**_where(entry), **dead}
        for entry in entries
        for dead in (entry.points or {}).get("dead_heartbeats") or []
    ]
    return pd.DataFrame(rows, columns=list(DEAD_HEARTBEAT_COLUMNS))
