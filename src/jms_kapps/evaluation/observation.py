"""What the harness reads of a run once its participants have stopped: the measures of its record
and the checks that make the run invalid. It reads the spans that the wrapper wrote for each
participant (`instrumentation`), the completion matcher's reads (`matcher`), the graph's transports
after the drain, and each participant's output, each line stamped with the time it arrived.

**The measures.** Every store request of every participant, with its time, its latency, its size
and its outcome: accepted; refused, with the store's reason; timed out or connection dropped, with
the error of its client (`store_requests.unanswered_outcome`; the two apart since 2026-10-14,
jms_kapps 109: until then both were timed out). Each commit of the
object-graph mapper, with its latency and its outcome. Then, over the measured window only, from
the warm-up's end to the window's end: the latencies of the commits that the store admitted; the
completed writes per minute, the writes the store applied, each checked write counted once, by its
update and not by the removal of its receipt; the store requests that the store refused, that timed
out and whose connection dropped, and the share of the three together (refused or unanswered); the
refusals for want of heap; and the transports ended per minute, the ends
the matcher saw in the window. The record keeps every store
request with its time, those of the participants' start and of the warm-up too, because the
store-only baseline replays them; only the latencies, rates and shares count the window alone. A
run without the wrapper has no spans: its measures are those that need none, the transports ended
per minute, beside the matcher's reads, the graph's transports after the drain and the refused
spawns that the record holds anyway.

**The checks.** A run that fails one is invalid, and its record names the check:

- `one accepted done or failed`: each Operation that the graph shows ended after the drain has
  exactly one accepted write of `done` or `failed`;
- `at most one accepted running`: and at most one of `running`;
- `no done before its running`: and no accepted `done` before an accepted `running`. An Operation
  may have no accepted `running` at all, as "at most one" allows: a transport that the
  WarehouseManagementSystem ends `failed` while it is still `queued` has none, and a `running`
  whose answer timed out is not known to be accepted. Either is valid.
- `the matcher saw the graph's ends`: the transports that the matcher saw end are the transports
  that the graph shows ended after the drain. The matcher reads once more after the graph's last
  read, at the drain's end, so a transport that the graph read showed still open and that ended
  just after it is seen ended by the matcher alone, and is no failure.
- `deadlock alarm`: a module raised a deadlock alarm between the load's start and the drain's
  end: a line of its output whose log message starts with "possible deadlock:", after the level
  and the logger that the participants' log format writes before it. The check names the module
  and the line as it was written, which names the modules asked; nothing after "possible
  deadlock:" is read. A line that arrived before the load's start or after the drain's end does
  not count.

An accepted write is one that the store accepted and applied: a checked write whose receipt the
store did not find wrote nothing (`store_requests`). A write that the store refused, and that its
participant sent again, counts once, as the accepted one. The first three checks need the spans,
so a run without the wrapper makes only the last two; the record lists the checks it made. An
Operation still open after the drain counts as stuck, with its place, and that alone does not make
a run invalid.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kapps_semantic_middleware.vocabulary import OperationStatus

from jms_kapps.evaluation import store_requests
from jms_kapps.evaluation.record import spawns_in_window
from jms_kapps.evaluation.wrapper import COMMIT_SPAN
from jms_kapps.uc2.module import POSSIBLE_DEADLOCK
from jms_kapps.uc2.vocabulary import WMS, local_name

# The names of the checks.
ONE_END = "one accepted done or failed"
ONE_RUNNING = "at most one accepted running"
DONE_AFTER_RUNNING = "no done before its running"
MATCHER_SAW_THE_ENDS = "the matcher saw the graph's ends"
DEADLOCK_ALARM = "deadlock alarm"

# The checks of a run with the wrapper, and of one without it.
WRAPPED_CHECKS = (
    ONE_END,
    ONE_RUNNING,
    DONE_AFTER_RUNNING,
    MATCHER_SAW_THE_ENDS,
    DEADLOCK_ALARM,
)
UNWRAPPED_CHECKS = (MATCHER_SAW_THE_ENDS, DEADLOCK_ALARM)

# A line of a participant's output that holds a deadlock alarm: the level and the logger that
# Python's default log format writes, each followed by a colon, and then the alarm.
_ALARM = re.compile(rf"^[A-Z]+:[^:\s]*:{re.escape(POSSIBLE_DEADLOCK)}")

# The statuses that end a transport.
_ENDS = (OperationStatus.DONE, OperationStatus.FAILED)

# A stamped line of output: the time it arrived and the line.
Stamped = tuple[datetime | None, str]


@dataclass(frozen=True)
class Span:
    """One span that a participant's wrapper wrote."""

    participant: str
    name: str
    kind: str
    span_id: str | None
    parent_id: str | None
    start: datetime
    end: datetime
    status: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    events: Sequence[Mapping[str, Any]] = ()

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def error(self) -> str | None:
        """The error the span ended with, as its type and message, or None."""
        for event in self.events:
            if event.get("name") == "exception":
                attributes = event.get("attributes", {})
                kind = str(attributes.get("exception.type", "")).rsplit(".", 1)[-1]
                message = attributes.get("exception.message")
                return f"{kind}: {message}" if message else kind or None
        return None


def read_spans(folder: Path) -> list[Span]:
    """Every span in the spans' files of `folder`, one file for each participant, in the order
    they started. A line that is no span, as the last line of a participant that was killed while
    it wrote it, is left out."""
    spans = []
    for path in sorted(folder.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
                spans.append(
                    Span(
                        participant=str(raw.get("participant") or path.stem),
                        name=str(raw["name"]),
                        kind=str(raw.get("kind", "INTERNAL")),
                        span_id=raw.get("span_id"),
                        parent_id=raw.get("parent_id"),
                        start=nanoseconds(int(raw["start"])),
                        end=nanoseconds(int(raw["end"])),
                        status=str(raw.get("status", "UNSET")),
                        attributes=dict(raw.get("attributes") or {}),
                        events=list(raw.get("events") or []),
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue
    return sorted(spans, key=lambda span: span.start)


def nanoseconds(since_epoch: int) -> datetime:
    """The time of `since_epoch` nanoseconds since the epoch, in UTC, to the microsecond."""
    seconds, rest = divmod(since_epoch, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=rest // 1000)


@dataclass(frozen=True)
class Window:
    """The measured window of a run: from the warm-up's end, or the load's start without a
    warm-up, to the window's end."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def holds(self, at: datetime) -> bool:
        return self.start <= at < self.end

    def record(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "minutes": self.minutes,
        }


def store_request_records(
    spans: Iterable[Span], window: Window
) -> list[dict[str, Any]]:
    """Every store request of `spans`, in the order they started, as the record holds them: who
    sent it, when, its latency in seconds, its method, path and kind, the size of its body and of
    the store's answer, its outcome, the kind of a refusal and the store's reason, the statuses
    it writes, whether the store applied it, its text, and whether it started in the window."""
    spans = list(spans)
    found: dict[tuple[str, str], bool | None] = {}
    for span in spans:
        asked = span.attributes.get("store.receipt_asked")
        if asked:
            found[(span.participant, str(asked))] = span.attributes.get(
                "store.receipt_found"
            )
    records = []
    for span in spans:
        attributes = span.attributes
        if not attributes.get("store.request"):
            continue
        kind = str(attributes.get("store.kind", store_requests.OTHER))
        reason = attributes.get("store.reason")
        answered = attributes.get("store.outcome")
        if answered:
            outcome = str(answered)
        else:
            # No answer: timed out or connection dropped, by the error (jms_kapps 109).
            reason = span.error() or span.attributes.get("error.type")
            outcome = store_requests.unanswered_outcome(
                None if reason is None else str(reason)
            )
        records.append(
            {
                "participant": span.participant,
                "at": span.start.isoformat(),
                "seconds": round(span.seconds, 6),
                "method": attributes.get("store.method"),
                "path": attributes.get("store.path"),
                "kind": kind,
                "request_bytes": attributes.get("store.request_bytes"),
                "response_bytes": attributes.get("store.response_bytes"),
                "outcome": outcome,
                "refusal": attributes.get("store.refusal"),
                "reason": reason,
                "writes": _writes(attributes),
                "applied": _applied(span, outcome, kind, found),
                "text": attributes.get("store.text"),
                "window": window.holds(span.start),
            }
        )
    return records


def _writes(attributes: Mapping[str, Any]) -> list[dict[str, str | None]]:
    operations = list(attributes.get("store.operation") or [])
    boxes = list(attributes.get("store.box") or [""] * len(operations))
    statuses = list(attributes.get("store.status_written") or [])
    return [
        {
            "operation": local_name(operation),
            "box": local_name(box) if box else None,
            "status": status,
        }
        for operation, box, status in zip(operations, boxes, statuses, strict=False)
    ]


def _applied(
    span: Span,
    outcome: str,
    kind: str,
    found: Mapping[tuple[str, str], bool | None],
) -> bool | None:
    """Whether the store applied the write of `span` (`store_requests.applied`), by the receipt
    its participant asked for, where it asked."""
    receipt = span.attributes.get("store.receipt_written")
    return store_requests.applied(
        kind,
        outcome,
        str(receipt) if receipt else None,
        found.get((span.participant, str(receipt))) if receipt else None,
    )


def commit_records(spans: Iterable[Span], window: Window) -> list[dict[str, Any]]:
    """Each commit of the object-graph mapper in `spans`, in the order they started: who made
    it, when, its latency in seconds, its outcome (accepted, or the error it ended with), and
    whether it started in the window."""
    return [
        {
            "participant": span.participant,
            "at": span.start.isoformat(),
            "seconds": round(span.seconds, 6),
            "outcome": "accepted"
            if span.status != "ERROR"
            else (span.error() or "error").split(":", 1)[0],
            "window": window.holds(span.start),
        }
        for span in spans
        if span.name == COMMIT_SPAN
    ]


def measures(
    window: Window,
    matcher_ends: Sequence[Mapping[str, Any]],
    *,
    requests: Sequence[Mapping[str, Any]] | None,
    commits: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """The measures of a run: those that need the wrapper only where `requests` and `commits`
    come from its spans, and not for a run without it (None)."""
    ended = [
        end
        for end in matcher_ends
        if window.holds(datetime.fromisoformat(end["seen_at"]))
    ]
    found: dict[str, Any] = {
        "window": window.record(),
        "transports_ended_in_window": len(ended),
        "transports_ended_per_minute": len(ended) / window.minutes,
    }
    if requests is None or commits is None:
        return found
    return (
        found
        | request_measures(window, requests)
        | {
            "commit_latencies_seconds": [
                c["seconds"]
                for c in commits
                if c["window"] and c["outcome"] == "accepted"
            ],
            "commits": list(commits),
            "store_requests": list(requests),
        }
    )


def request_measures(
    window: Window, requests: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The measures of `requests`, store requests as the record holds them, over the window
    alone: how many there were, how many the store refused, how many timed out and how many
    lost their connection, and the share of the three, refused or unanswered (jms_kapps 109;
    until then `refused_or_timed_out_share`); the completed writes, each checked write counted
    once, by its update and not
    by the removal of its receipt, and their number per minute of `window`; and the refusals for
    want of heap, each with whether it came in the window. A UC2 run's measures hold them, and
    so do the store-only baseline's (`baseline`)."""
    in_window = [request for request in requests if request["window"]]
    outcomes = [store_requests.outcome_of(r) for r in in_window]
    refused = outcomes.count(store_requests.REFUSED)
    timed_out = outcomes.count(store_requests.TIMED_OUT)
    dropped = outcomes.count(store_requests.CONNECTION_DROPPED)
    written = [
        r
        for r in in_window
        if r["kind"] == store_requests.WRITE
        and r["applied"] is True
        and not store_requests.removes_receipt(r["text"])
    ]
    heap = [
        {key: request[key] for key in ("participant", "at", "reason", "window")}
        for request in requests
        if request["refusal"] == store_requests.HEAP
    ]
    return {
        "store_requests_in_window": len(in_window),
        "refused_in_window": refused,
        "timed_out_in_window": timed_out,
        "connection_dropped_in_window": dropped,
        "refused_or_unanswered_share": (
            (refused + timed_out + dropped) / len(in_window) if in_window else None
        ),
        "completed_writes_in_window": len(written),
        "completed_writes_per_minute": len(written) / window.minutes,
        "heap_refusals": heap,
        "heap_refusals_in_window": sum(refusal["window"] for refusal in heap),
    }


def status_write_checks(
    graph_ends: Mapping[str, Any], requests: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The checks of the statuses written for each Operation that the graph shows ended after the
    drain, `graph_ends` as the record holds the run's transports: each check that fails, with the
    Operation and its accepted writes of a status."""
    accepted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in requests:
        if request["outcome"] != store_requests.ACCEPTED or request["applied"] is False:
            continue
        answered = datetime.fromisoformat(request["at"]) + timedelta(
            seconds=request["seconds"]
        )
        for write in request["writes"]:
            accepted[write["operation"]].append(
                {
                    "status": write["status"],
                    "participant": request["participant"],
                    "at": request["at"],
                    "answered": answered,
                }
            )
    ended = [t["transport"] for t in (*graph_ends["done"], *graph_ends["failed"])]
    failed = []
    for transport in ended:
        writes = sorted(accepted.get(transport, []), key=lambda w: w["answered"])
        shown = [
            {key: write[key] for key in ("status", "participant", "at")}
            for write in writes
        ]
        ends = [w for w in writes if w["status"] in _ENDS]
        running = [w for w in writes if w["status"] == OperationStatus.RUNNING]
        done = [w for w in writes if w["status"] == OperationStatus.DONE]
        if len(ends) != 1:
            failed.append({"check": ONE_END, "transport": transport, "accepted": shown})
        if len(running) > 1:
            failed.append(
                {"check": ONE_RUNNING, "transport": transport, "accepted": shown}
            )
        if done and running and done[0]["answered"] < running[-1]["answered"]:
            failed.append(
                {"check": DONE_AFTER_RUNNING, "transport": transport, "accepted": shown}
            )
    return failed


def matcher_check(
    matcher: Mapping[str, Any], graph_ends: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Whether the transports that the matcher saw end are those that the graph shows ended
    after the drain: none where they are, else one failed check that names those it saw alone
    and those the graph shows alone. A transport that the graph's read showed open and that the
    matcher's last read, which comes after it, saw ended, is no failure."""
    seen = {end["transport"]: end for end in matcher["ends"]}
    ended = {t["transport"] for t in (*graph_ends["done"], *graph_ends["failed"])}
    still_open = {t["transport"] for t in graph_ends["open"]}
    last_read = matcher["reads"][-1]["at"] if matcher["reads"] else None
    seen_alone = sorted(
        transport
        for transport, end in seen.items()
        if transport not in ended
        and not (transport in still_open and end["seen_at"] == last_read)
    )
    graph_alone = sorted(ended - set(seen))
    if not seen_alone and not graph_alone:
        return []
    return [
        {
            "check": MATCHER_SAW_THE_ENDS,
            "seen_by_the_matcher_alone": seen_alone,
            "shown_by_the_graph_alone": graph_alone,
        }
    ]


def deadlock_alarms(
    outputs: Mapping[str, Sequence[Stamped]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Each deadlock alarm in `outputs`, each participant's stamped output by its resource's
    local name, that arrived from `start`, the load's start, to `end`, the drain's end: a failed
    check each, with the module, the line as it was written, and when it arrived."""
    found = []
    for module, lines in outputs.items():
        for arrived, line in lines:
            if arrived is not None and start <= arrived <= end and _ALARM.match(line):
                found.append(
                    {
                        "check": DEADLOCK_ALARM,
                        "module": module,
                        "line": line,
                        "at": arrived.isoformat(),
                    }
                )
    return sorted(found, key=lambda alarm: alarm["at"])


def observe(
    record: Mapping[str, Any],
    *,
    spans: Path | None,
    outputs: Mapping[str, Sequence[Stamped]],
) -> dict[str, Any]:
    """What a completed run's record gains from its observation: its measures, whether it is
    valid, the checks it failed and those it made, and its stuck Operations.

    Args:
        record: The run's record, with its warm-up, its window, its actions, its transports
            after the drain and its matcher.
        spans: The folder of the participants' spans, or None for a run without the wrapper.
        outputs: Each participant's stamped output, by its resource's local name.
    """
    window = Window(
        datetime.fromisoformat(record["window"]["start"]),
        datetime.fromisoformat(record["window"]["end"]),
    )
    load_start = datetime.fromisoformat(
        (record["warm_up"] or record["window"])["start"]
    )
    [drained] = [a for a in record["actions"] if a["action"] == "drain ended"]
    graph_ends = record["transports"]
    matcher = record["matcher"]
    failed: list[dict[str, Any]] = []
    made: tuple[str, ...]
    if spans is not None:
        every_span = read_spans(spans)
        requests = store_request_records(every_span, window)
        commits = commit_records(every_span, window)
        found = measures(window, matcher["ends"], requests=requests, commits=commits)
        failed += status_write_checks(graph_ends, requests)
        made = WRAPPED_CHECKS
    else:
        found = measures(window, matcher["ends"], requests=None, commits=None)
        made = UNWRAPPED_CHECKS
    found |= spawns_in_window(
        outputs.get(local_name(WMS), ()), window.start, window.end
    )
    failed += matcher_check(matcher, graph_ends)
    failed += deadlock_alarms(
        outputs, load_start, datetime.fromisoformat(drained["at"])
    )
    return {
        "measures": found,
        "valid": not failed,
        "failed_checks": failed,
        "checks_made": list(made),
        "stuck": [
            {"transport": t["transport"], "status": t["status"], "place": t["place"]}
            for t in graph_ends["open"]
        ],
    }
