"""The tidy per-commit table and the replayed-writes table of the measurement harness, read from
recorded run folders. The commit time figure script (`commit_time/figure.py`)
reads them; each figure is a function of the two tables.

**The runs.** `read_runs` takes folders: an experiment's store folders
(`<store>/heartbeats-<level>/runs/<run id>/`), a bare runs folder, or a single run folder, and finds
every `record.json` below them. Each record gets its labels:

- **the store**: the name of a folder between the input folder and the run folder that is one of
  `STORE_FOLDERS` (`kit`, `local`), else the host of the record's server;
- **the heartbeat level**: `off` where the record's run settings hold a heartbeat interval of null
  or 0, `on` where they hold another; else the name of a folder `heartbeats-on` or
  `heartbeats-off` above the run; else `on`, as every run before the heartbeat level was a
  setting ran with heartbeats on. A writes-only replay takes the level of the run it replays;
- **the arm**: a run of use case 2 is KAPPS with SHACL on or off, by its configuration's `shacl`;
  a baseline whose replay mode is "writes only" is the writes-only arm.

A run counts when it completed and is valid, as the harness's table counts it. Left out, each with
why: a record that cannot be read, a run of another kind, an all-requests baseline, a run that does
not count, a writes-only replay whose replayed run is no SHACL-on run among the inputs, and every
valid writes-only replay of a run but the earliest by its start (its record's first action).

**A commit** is one span of the object-graph mapper's commit (`wrapper.COMMIT_SPAN`). Its time
is the span's duration; it is admitted when the span ended without an error; it is in the window
when it started in the run's measured window. Its store requests are the store-request spans below
it, among the spans of its own participant, as the record's commit link is made: a commit inside
another commit is not walked into, its requests are its own.

**The phases.** Each store request inside a commit goes to exactly one phase, by the first rule
that matches (`request_phase`): the checked write (an update that inserts a receipt) → `update`; a
receipt query → `receipt_ask`; a receipt removal → `receipt_removal`; a read below the class-spec
derivation, or a read of the schema/class category directly below the commit → `class_query`; a
read below a fetch → `fetch_reads`; any other request → `other_requests`. A request's phase takes its
whole duration. Every other span below the commit gives its own time, its duration minus that of
its children, to one phase (`client_phase`): `class_derivation` where it or a span above it (below
the commit) is the class-spec derivation, else `fetch_client` where one is a fetch, else `diff`
where one is the diff, else `client_remainder`; the commit's own time is `client_remainder`. So a
fetch inside a fetch counts once, under the outer one, and a class-spec derivation inside a fetch
is class derivation, not fetch client. The ten parts are in whole microseconds, as the spans are,
and sum to the commit's time exactly; none is below 0 where no span's children overlap in time
(the own-time rule, in place of "the class-spec span minus its class-query reads",
which counts a class spec inside a fetch twice).

**The kind** of a commit, by its first checked write: `heartbeat` where the write names the
heartbeat predicate in either spelling, else `status: ` and the statuses it sets, else
`other update`; `no write` for a commit without a checked write. This is the rule the record's
`kind` follows where the record has it.

**The writes-only time of a commit** is the time of its replayed checked write: the replayed
write whose `commit` is the commit's span id and that inserts a receipt (the receipt's removal
carries the same commit and is not it; a heartbeat's checked write is categorised a heartbeat
update, so the pairing reads the receipt, not the category). A replay record without `commit`
pairs by the participant and the recorded send time, which both records copy from the same record.
Where a commit has several checked writes, their latencies are summed and the first's send time
is kept. The seconds since the window's start are on the KAPPS run's window and wall clock, for its
commits and for the replayed writes alike.
"""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from jms_kapps.commit_time import store_requests
from jms_kapps.commit_time.observation import Span, Window, store_request_records
from jms_kapps.commit_time.report_steps import (
    MODULE_ROLE,
    WMS_ROLE,
    category_of,
    read_run_spans,
    wms_of,
)
from jms_kapps.commit_time.summary import repetition_of, summary_of
from jms_kapps.commit_time.wrapper import COMMIT_SPAN, span_name

# The arms of the commit time figures.
KAPPS_SHACL_ON = "kapps-shacl-on"
KAPPS_SHACL_OFF = "kapps-shacl-off"
WRITES_ONLY = "writes-only"

# The names of the store folders of the experiment, and of its heartbeat folders.
STORE_FOLDERS = ("kit", "local")
HEARTBEATS_ON = "on"
HEARTBEATS_OFF = "off"
HEARTBEAT_FOLDERS = {"heartbeats-on": HEARTBEATS_ON, "heartbeats-off": HEARTBEATS_OFF}

# The replay mode of a writes-only baseline, as its record's `replay.mode` says it.
WRITES_ONLY_MODE = "writes only"

# The mapper's spans below a commit that name a phase.
CLASS_SPEC_SPAN = span_name("OGM", "get_class_spec")
FETCH_SPAN = span_name("OGM", "fetch")
DIFF_SPAN = span_name("Node", "diff")

# The categories of the steps (`report_steps.READ_CATEGORIES`, `WRITE_CATEGORIES`) that name a
# phase.
RECEIPT_ASK_CATEGORY = "receipt ASK"
RECEIPT_REMOVAL_CATEGORY = "receipt removal"
SCHEMA_CATEGORY = "OGM schema/class"

# The two spellings of the heartbeat predicate in the records: the old and the current one. The
# old is built from fragments, as the check for old property names bans it as a bare word.
HEARTBEAT_PREDICATES = ("last" + "Heartbeat", "hasLastHeartbeat")

# The kinds of a commit.
HEARTBEAT_KIND = "heartbeat"
STATUS_KIND = "status: "
OTHER_UPDATE_KIND = "other update"
NO_WRITE_KIND = "no write"

# The phases of a commit, in the order of the table's columns: six of its store requests, four of
# its client's own time.
UPDATE = "update"
RECEIPT_ASK = "receipt_ask"
RECEIPT_REMOVAL = "receipt_removal"
CLASS_QUERY = "class_query"
FETCH_READS = "fetch_reads"
OTHER_REQUESTS = "other_requests"
CLASS_DERIVATION = "class_derivation"
FETCH_CLIENT = "fetch_client"
DIFF = "diff"
CLIENT_REMAINDER = "client_remainder"
PHASES = (
    CLASS_QUERY,
    CLASS_DERIVATION,
    FETCH_READS,
    FETCH_CLIENT,
    DIFF,
    UPDATE,
    RECEIPT_ASK,
    RECEIPT_REMOVAL,
    OTHER_REQUESTS,
    CLIENT_REMAINDER,
)


def phase_column(phase: str) -> str:
    """The column of the per-commit table that holds the seconds of `phase`."""
    return f"phase_{phase}_seconds"


COMMIT_COLUMNS = (
    "store",
    "heartbeats",
    "arm",
    "run_id",
    "repetition",
    "pick_seed",
    "participant",
    "role",
    "commit_id",
    "at",
    "t",
    "seconds",
    "outcome",
    "admitted",
    "in_window",
    "kind",
    "update_at",
    "update_seconds",
    "update_outcome",
    "update_refusal",
    "update_applied",
    "checked_writes",
    "reads_in_commit",
    "replay_run_id",
    "replayed_update_seconds",
    "replayed_update_late_seconds",
    "replayed_outcome",
    "outcome_match",
    "difference_seconds",
    *(phase_column(phase) for phase in PHASES),
)

REPLAYED_WRITE_COLUMNS = (
    "store",
    "heartbeats",
    "repetition",
    "replay_run_id",
    "replayed_run_id",
    "participant",
    "commit",
    "category",
    "recorded_at",
    "t",
    "recorded_offset_seconds",
    "sent_offset_seconds",
    "late_seconds",
    "seconds",
    "status_code",
    "outcome",
    "refusal",
    "applied",
    "recorded_outcome",
    "recorded_applied",
    "outcome_match",
    "overtook_previous",
)

_MICROSECOND = timedelta(microseconds=1)


@dataclass
class Run:
    """One recorded run that counts, with its labels: a KAPPS run or a writes-only replay."""

    folder: Path
    record: dict[str, Any]
    store: str
    heartbeats: str
    arm: str
    repetition: int
    pick_seed: int | None

    @property
    def run_id(self) -> str:
        return str(self.record["run_id"])


@dataclass
class Runs:
    """The runs of the input folders: the KAPPS runs that count, the writes-only replay that
    counts for each SHACL-on run, by the run's id, and every record left out with why."""

    kapps: list[Run] = field(default_factory=list)
    replays: dict[str, Run] = field(default_factory=dict)
    left_out: list[dict[str, str]] = field(default_factory=list)


def read_runs(folders: Sequence[Path]) -> Runs:
    """The runs below `folders`, labelled, with those left out and why (see the module)."""
    runs = Runs()
    replays: list[Run] = []
    for folder in folders:
        for path in sorted(folder.rglob("record.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                run_id = str(record["run_id"])
            except (OSError, ValueError, TypeError, KeyError):
                runs.left_out.append(
                    _left_out(path.parent.name, "the record cannot be read")
                )
                continue
            arm, why = arm_of(record)
            if arm is None:
                runs.left_out.append(_left_out(run_id, why))
                continue
            summary = summary_of(record)
            if not summary.counts:
                runs.left_out.append(_left_out(run_id, summary.why_left_out() or ""))
                continue
            run = Run(
                folder=path.parent,
                record=record,
                store=store_of(path.parent, folder, record),
                heartbeats=heartbeats_of(path.parent, folder, record),
                arm=arm,
                repetition=repetition_of(summary),
                pick_seed=(record.get("run_settings") or {}).get("wms_pick_seed"),
            )
            if arm == WRITES_ONLY:
                replays.append(run)
            else:
                runs.kapps.append(run)
    by_id = {run.run_id: run for run in runs.kapps}
    # The earliest replay first, by when it began.
    for replay in sorted(replays, key=started):
        replayed = str((replay.record.get("replayed_run") or {}).get("run_id"))
        replayed_run = by_id.get(replayed)
        if replayed_run is None or replayed_run.arm != KAPPS_SHACL_ON:
            runs.left_out.append(
                _left_out(
                    replay.run_id,
                    f"its replayed run {replayed} is no SHACL-on run among the inputs",
                )
            )
            continue
        if replayed in runs.replays:
            runs.left_out.append(
                _left_out(
                    replay.run_id,
                    f"a later writes-only replay of {replayed}: the earliest, "
                    f"{runs.replays[replayed].run_id}, counts",
                )
            )
            continue
        # A replay is of its run's heartbeat level and repetition.
        replay.heartbeats = replayed_run.heartbeats
        replay.repetition = replayed_run.repetition
        replay.pick_seed = replayed_run.pick_seed
        runs.replays[replayed] = replay
    return runs


def started(run: Run) -> str:
    """When `run` began, as its record's first action says, else its run id, which begins with
    its start time: both sort by time."""
    actions = run.record.get("actions") or []
    return str(actions[0]["at"]) if actions else run.run_id


def _left_out(run_id: str, why: str) -> dict[str, str]:
    return {"run_id": run_id, "why": why}


def arm_of(record: Mapping[str, Any]) -> tuple[str | None, str]:
    """The arm of `record`, or None with why it has none."""
    kind = record.get("run_kind")
    if kind == "uc2":
        try:
            shacl = tomllib.loads(str(record.get("configuration") or "")).get("shacl")
        except tomllib.TOMLDecodeError:
            return None, "its configuration cannot be read"
        return (KAPPS_SHACL_ON if shacl else KAPPS_SHACL_OFF), ""
    if kind == "baseline":
        mode = (record.get("replay") or {}).get("mode")
        if mode == WRITES_ONLY_MODE:
            return WRITES_ONLY, ""
        return None, "an all-requests baseline, no writes-only replay"
    return None, f"a run of the kind {kind}, no arm of the figures"


def store_of(run_folder: Path, input_folder: Path, record: Mapping[str, Any]) -> str:
    """The store of the run in `run_folder`, found below `input_folder` (see the module)."""
    for name in _folder_names(run_folder, input_folder):
        if name in STORE_FOLDERS:
            return name
    url = str((record.get("server") or {}).get("url") or "")
    return urlparse(url).hostname or "unknown store"


def heartbeats_of(
    run_folder: Path, input_folder: Path, record: Mapping[str, Any]
) -> str:
    """The heartbeat level of the run in `run_folder`, found below `input_folder` (see the
    module)."""
    settings = record.get("run_settings") or {}
    if "heartbeat_interval_seconds" in settings:
        interval = settings["heartbeat_interval_seconds"]
        return HEARTBEATS_OFF if not interval else HEARTBEATS_ON
    for name in _folder_names(run_folder, input_folder):
        if name in HEARTBEAT_FOLDERS:
            return HEARTBEAT_FOLDERS[name]
    return HEARTBEATS_ON


def _folder_names(run_folder: Path, input_folder: Path) -> list[str]:
    """The names of the folders from `input_folder` down to `run_folder`."""
    return [input_folder.name, *run_folder.relative_to(input_folder).parts]


def commit_kind(checked_write: Mapping[str, Any] | None) -> str:
    """The kind of a commit by its first checked write, a store request as the record holds it
    (None for a commit without one, whose kind is `NO_WRITE_KIND`); see the module."""
    if checked_write is None:
        return NO_WRITE_KIND
    text = str(checked_write.get("text") or "")
    if any(predicate in text for predicate in HEARTBEAT_PREDICATES):
        return HEARTBEAT_KIND
    statuses = [
        w["status"] for w in checked_write.get("writes") or [] if w.get("status")
    ]
    if statuses:
        return STATUS_KIND + ", ".join(statuses)
    return OTHER_UPDATE_KIND


def request_phase(
    kind: str, text: str, above: Sequence[str], directly_below: bool
) -> str:
    """The phase of a store request of `kind` with the query `text` inside a commit: `above` are
    the names of the spans between the commit and the request, `directly_below` whether there is
    none (see the module)."""
    if kind == store_requests.WRITE and store_requests.receipt_written(text):
        return UPDATE
    category = category_of(kind, text)
    if category == RECEIPT_ASK_CATEGORY:
        return RECEIPT_ASK
    if category == RECEIPT_REMOVAL_CATEGORY:
        return RECEIPT_REMOVAL
    if kind == store_requests.READ:
        if CLASS_SPEC_SPAN in above or (directly_below and category == SCHEMA_CATEGORY):
            return CLASS_QUERY
        if FETCH_SPAN in above:
            return FETCH_READS
    return OTHER_REQUESTS


def client_phase(names: Sequence[str]) -> str:
    """The phase of the own time of a span below a commit that is no store request: `names` are
    the names of the spans from the one below the commit down to the span itself."""
    if CLASS_SPEC_SPAN in names:
        return CLASS_DERIVATION
    if FETCH_SPAN in names:
        return FETCH_CLIENT
    if DIFF_SPAN in names:
        return DIFF
    return CLIENT_REMAINDER


@dataclass
class CommitParts:
    """What the walk below one commit finds: the microseconds of each phase, its checked writes
    and the number of its reads."""

    microseconds: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(PHASES, 0)
    )
    checked_writes: list[Span] = field(default_factory=list)
    reads: int = 0


def commit_parts(
    commit: Span, children: Mapping[tuple[str, str | None], list[Span]]
) -> CommitParts:
    """The phases, the checked writes and the reads of `commit`; `children` are the spans by
    their participant and their parent's id."""
    parts = CommitParts()
    _walk(commit, [], children, parts)
    return parts


def _walk(
    span: Span,
    names: list[str],
    children: Mapping[tuple[str, str | None], list[Span]],
    parts: CommitParts,
) -> None:
    """Give the time of `span`, below the commit along `names`, and of the spans below it to their
    phases; `names` is empty for the commit itself."""
    below = children.get((span.participant, span.span_id), []) if span.span_id else []
    own = _microseconds(span) - sum(_microseconds(child) for child in below)
    parts.microseconds[client_phase(names) if names else CLIENT_REMAINDER] += own
    for child in below:
        if child.attributes.get("store.request"):
            kind = str(child.attributes.get("store.kind"))
            text = str(child.attributes.get("store.text") or "")
            phase = request_phase(kind, text, names, directly_below=not names)
            parts.microseconds[phase] += _microseconds(child)
            if phase == UPDATE:
                parts.checked_writes.append(child)
            if kind == store_requests.READ:
                parts.reads += 1
        elif child.name == COMMIT_SPAN:
            # A commit inside a commit is a commit of its own: its time is the outer's client
            # remainder, its requests its own.
            parts.microseconds[CLIENT_REMAINDER] += _microseconds(child)
        else:
            _walk(child, [*names, child.name], children, parts)


def _microseconds(span: Span) -> int:
    return (span.end - span.start) // _MICROSECOND


def commit_rows(run: Run, replay: Run | None) -> list[dict[str, Any]] | None:
    """The rows of the per-commit table of `run`, one for each commit, joined with `replay`, its
    writes-only replay, where there is one; None for a run without spans."""
    spans = read_run_spans(run.folder)
    if spans is None:
        return None
    window = Window(
        datetime.fromisoformat(run.record["window"]["start"]),
        datetime.fromisoformat(run.record["window"]["end"]),
    )
    # The store requests as the record holds them, by the span each was made from.
    request_spans = [span for span in spans if span.attributes.get("store.request")]
    requests = {
        (span.participant, span.span_id): request
        for span, request in zip(
            request_spans, store_request_records(spans, window), strict=True
        )
    }
    children: dict[tuple[str, str | None], list[Span]] = defaultdict(list)
    for span in spans:
        children[(span.participant, span.parent_id)].append(span)
    wms = wms_of(run.record)
    wms_name = wms[0] if wms else None
    by_commit, by_send_time = replayed_updates(replay)
    rows = []
    for span in spans:
        if span.name != COMMIT_SPAN:
            continue
        parts = commit_parts(span, children)
        checked = [
            requests[(write.participant, write.span_id)]
            for write in parts.checked_writes
        ]
        first = checked[0] if checked else None
        replayed = by_commit.get((span.participant, str(span.span_id)))
        if replayed is None and checked:
            replayed = [
                found
                for write in checked
                for found in by_send_time.get((span.participant, str(write["at"])), [])
            ]
        replayed_seconds = (
            round(sum(float(write["seconds"]) for write in replayed), 6)
            if replayed
            else None
        )
        seconds = _microseconds(span) / 1e6
        admitted = span.status != "ERROR"
        rows.append(
            {
                "store": run.store,
                "heartbeats": run.heartbeats,
                "arm": run.arm,
                "run_id": run.run_id,
                "repetition": run.repetition,
                "pick_seed": run.pick_seed,
                "participant": span.participant,
                "role": WMS_ROLE if span.participant == wms_name else MODULE_ROLE,
                "commit_id": span.span_id,
                "at": span.start.isoformat(),
                "t": (span.start - window.start).total_seconds(),
                "seconds": seconds,
                "outcome": "accepted"
                if admitted
                else (span.error() or "error").split(":", 1)[0],
                "admitted": admitted,
                "in_window": window.holds(span.start),
                "kind": commit_kind(first),
                "update_at": first["at"] if first else None,
                "update_seconds": round(sum(float(w["seconds"]) for w in checked), 6)
                if checked
                else None,
                "update_outcome": first["outcome"] if first else None,
                "update_refusal": first["refusal"] if first else None,
                "update_applied": first["applied"] if first else None,
                "checked_writes": len(checked),
                "reads_in_commit": parts.reads,
                "replay_run_id": replay.run_id if replay and replayed else None,
                "replayed_update_seconds": replayed_seconds,
                "replayed_update_late_seconds": replayed[0].get("late_seconds")
                if replayed
                else None,
                "replayed_outcome": replayed[0].get("outcome") if replayed else None,
                "outcome_match": all(w.get("outcome_match") is True for w in replayed)
                if replayed
                else None,
                "difference_seconds": round(seconds - replayed_seconds, 6)
                if replayed_seconds is not None
                else None,
                **{
                    phase_column(phase): parts.microseconds[phase] / 1e6
                    for phase in PHASES
                },
            }
        )
    return rows


def replayed_updates(
    replay: Run | None,
) -> tuple[
    dict[tuple[str, str], list[Mapping[str, Any]]],
    dict[tuple[str, str], list[Mapping[str, Any]]],
]:
    """The replayed checked writes of `replay`, the writes that insert a receipt: by their
    participant and their commit's span id, and, for those without a commit, by their participant
    and their recorded send time. Empty for no replay."""
    by_commit: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    by_send_time: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    if replay is None:
        return by_commit, by_send_time
    for write in (replay.record.get("measures") or {}).get("store_requests") or []:
        if not store_requests.receipt_written(write.get("text")):
            continue
        participant = str(write.get("participant"))
        if write.get("commit"):
            by_commit[(participant, str(write["commit"]))].append(write)
        else:
            by_send_time[(participant, str(write.get("recorded_at")))].append(write)
    return by_commit, by_send_time


def replayed_write_rows(replay: Run, run: Run) -> list[dict[str, Any]]:
    """The rows of the replayed-writes table of `replay`, the writes-only replay of `run`, one
    for each replayed write."""
    window_start = datetime.fromisoformat(run.record["window"]["start"])
    rows = []
    for write in (replay.record.get("measures") or {}).get("store_requests") or []:
        recorded_at = write.get("recorded_at")
        offset = write.get("offset_seconds")
        late = write.get("late_seconds")
        rows.append(
            {
                "store": replay.store,
                "heartbeats": replay.heartbeats,
                "repetition": replay.repetition,
                "replay_run_id": replay.run_id,
                "replayed_run_id": run.run_id,
                "participant": write.get("participant"),
                "commit": write.get("commit"),
                "category": write.get("category"),
                "recorded_at": recorded_at,
                "t": (
                    datetime.fromisoformat(recorded_at) - window_start
                ).total_seconds()
                if recorded_at
                else None,
                "recorded_offset_seconds": offset,
                "sent_offset_seconds": offset + late
                if offset is not None and late is not None
                else None,
                "late_seconds": late,
                "seconds": write.get("seconds"),
                "status_code": write.get("status_code"),
                "outcome": write.get("outcome"),
                "refusal": write.get("refusal"),
                "applied": write.get("applied"),
                "recorded_outcome": write.get("recorded_outcome"),
                "recorded_applied": write.get("recorded_applied"),
                "outcome_match": write.get("outcome_match"),
                "overtook_previous": write.get("overtook_previous"),
            }
        )
    return rows


def table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> pd.DataFrame:
    """The rows as a table with `columns`, in their order; an empty table has the columns too."""
    return pd.DataFrame(list(rows), columns=list(columns))
