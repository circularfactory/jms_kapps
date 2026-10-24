"""The steps of a run (`report`): where its time goes, step by step, from the spans its participants
wrote. Iteration one of the measurement harness. Every name here is interim.

**A step** is keyed by its role (`WMS` or `module`) and its name.

- A span that is not a store request is a step of its own name. A commit of the object-graph
  mapper is split by its status, `kapps_ogm.OGM.commit (accepted)` or `(error)`. An HTTP call
  that a participant makes to a workflow of a peer is named after the call and the workflow's
  endpoint (`HTTP call: reserve`), as the client span's own name is only the HTTP method.
- Every store request's span has the same name, so a store request is keyed by its kind and a
  category taken from the text of its query: `store read: <category>` or
  `store write: <category>` (`READ_CATEGORIES`, `WRITE_CATEGORIES`). A query that matches no
  category is `store read: other read` or `store write: other write`, never an error. A store
  request that the store did not accept is a step of its own, with ` (not accepted)` after it.
- One step has no span and is measured from the spans around it: `GAP_STEP`, the time from the end
  of the WMS's read of all boxes to its next Reserve call, which is the WMS's client time after
  that read. Only the records of the "before" series have it: the WMS now reads no
  box to name one, and asks about the one box that comes back in an `ASK` of its own category
  (`HELD_BOX_ASK`); the old records still read and sort as before.

**The role** of a span is told by its participant: the WMS is the participant of the record whose
command takes the WMS's strategy (`WMS_FLAG`), and every other participant is a module. The WMS's
IRI, which the category of a possession read needs, is the resource of that participant in the
record, not a fixed value. A record without participants has no WMS: all its spans are modules'
and it has no gap step.

**The window.** A span counts when it started in the run's measured window (`record["window"]`);
the gap step counts by the start of the read it follows.

**What is kept.** For each step, the durations in seconds of its in-window spans, rounded to the
microsecond, and, for a store request, the size of the store's answer where the span records one.
`steps_of` returns them as `RunSteps`; the run's summary holds them (`report_summary`), so that the
runs of a configuration can be pooled and the spans are read once. The table pools them over the
valid runs of a configuration (`steps_table`).

**The table** has one row for each configuration, role and step: the runs that have the step, its
spans in the window, its steps per minute (its spans over the minutes of the windows of the runs
pooled), the median and the p95 of its seconds (`stats.quantile`, as the rest of the report), and
the median size of the store's answer in bytes where the spans record one.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from jms_kapps.evaluation import store_requests
from jms_kapps.evaluation.observation import Span, read_spans
from jms_kapps.evaluation.stats import quantile
from jms_kapps.evaluation.wrapper import COMMIT_SPAN
from jms_kapps.uc2.vocabulary import local_name

WMS_ROLE = "WMS"
MODULE_ROLE = "module"

# The folder of the spans in a run's folder.
SPANS_FOLDER = "spans"

# The command-line flag only the WMS's process takes, by which its participant is found.
WMS_FLAG = "--wms-strategy"

OTHER_READ = "other read"
OTHER_WRITE = "other write"

# A category matches a query's text (spaces collapsed) and the WMS's IRI, or None.
Matcher = Callable[[str, str | None], bool]


def _has(*parts: str) -> Matcher:
    return lambda text, wms: all(part in text for part in parts)


def _pattern(pattern: str) -> Matcher:
    found = re.compile(pattern)
    return lambda text, wms: found.search(text) is not None


def _starts(prefix: str) -> Matcher:
    return lambda text, wms: text.startswith(prefix)


def _held_by_module(text: str, wms: str | None) -> bool:
    return _has("hasPossessedWorkpiece> ?possession", "SELECT DISTINCT ?box")(text, wms)


def _held_by_wms(text: str, wms: str | None) -> bool:
    return wms is not None and _held_by_module(text, wms) and f"{wms}>" in text


def _any(*matchers: Matcher) -> Matcher:
    return lambda text, wms: any(m(text, wms) for m in matchers)


def _all(*matchers: Matcher) -> Matcher:
    return lambda text, wms: all(m(text, wms) for m in matchers)


ALL_BOXES = "all boxes (WMS next_box)"
"""The WMS's read of every box, which numbered the next box at each spawn: only in the records of
the "before" series, as the WMS reads no box to name one."""

HELD_BOX_ASK = "possession: ASK for one box (WMS return)"
"""The WMS's `ASK` whether it holds the one box that came back (`uc2.discovery.holds_query`), in
place of its read of every box it holds (interim)."""

# The categories of a read, in the order they are tried: the first that matches is the category.
# Adding a category is one line.
READ_CATEGORIES: tuple[tuple[str, Matcher], ...] = (
    (
        ALL_BOXES,
        _pattern(r"SELECT \?box WHERE \{ \?box a <[^>]*#Box> \}"),
    ),
    ("possession: boxes held by the WMS", _held_by_wms),
    (
        HELD_BOX_ASK,
        _all(
            _starts("ASK { <"),
            _has("hasPossessedWorkpiece> ?possession", "hasPossessor> ?possession"),
        ),
    ),
    ("possession: boxes held by a module", _held_by_module),
    (
        "possession: state lookups",
        _any(
            _has("SELECT DISTINCT ?state"),
            _pattern(r"SELECT \?ps WHERE"),
            _pattern(r"SELECT \?a WHERE"),
        ),
    ),
    ("discovery: live workflow (heartbeat)", _has("?heartbeat")),
    (
        "discovery: service/capability",
        _any(
            _has("SELECT DISTINCT ?svc ?addr"),
            _has("SELECT ?capability"),
            _pattern(r"SELECT \?resource WHERE"),
        ),
    ),
    (
        "topology/connections",
        _any(
            _has("SELECT DISTINCT ?module ?class ?source"),
            _has("SELECT DISTINCT ?source ?target ?direction"),
        ),
    ),
    ("OGM fetch (s p o)", _starts("SELECT ?s ?p ?o WHERE {BIND")),
    ("OGM existence ASK", _starts("ASK WHERE { {<")),
    (
        "OGM schema/class",
        _any(
            _has("SELECT DISTINCT ?obj"),
            _has("SELECT ?property FROM"),
            _has("SELECT ?class FROM"),
        ),
    ),
    ("receipt ASK", _starts("ASK { GRAPH <urn:x-kapps:write-receipts>")),
    ("issuer/status", _has("?issuer")),
)

# The categories of a write, likewise.
WRITE_CATEGORIES: tuple[tuple[str, Matcher], ...] = (
    (
        "receipt removal",
        _starts("DELETE WHERE { GRAPH <urn:x-kapps:write-receipts>"),
    ),
    ("heartbeat update", _has("hasLastHeartbeat")),
    # A checked write is an update that inserts a receipt (`store_requests.receipt_written`).
    (
        "checked write (commit body)",
        lambda text, wms: store_requests.receipt_written(text) is not None,
    ),
)


# The step with no span, and the span of a Reserve call that ends it.
GAP_STEP = "all-boxes read end -> next Reserve call"
RESERVE_URL = "reserve/execute"

NOT_ACCEPTED = " (not accepted)"
_SPACES = re.compile(r"\s+")
_ENDPOINT = re.compile(r"/workflows/[^/]*/([^/?#]+)")


def category_of(kind: str, text: str, wms: str | None = None) -> str:
    """The category of a store request of `kind` (`read` or `write`) with the query `text`; the
    WMS's IRI `wms` tells a possession read of the WMS from a module's. A query that matches no
    category is `OTHER_READ` or `OTHER_WRITE`."""
    query = _SPACES.sub(" ", text).strip()
    table, other = (
        (READ_CATEGORIES, OTHER_READ)
        if kind == store_requests.READ
        else (WRITE_CATEGORIES, OTHER_WRITE)
    )
    return next((name for name, found in table if found(query, wms)), other)


@dataclass
class StepSpans:
    """What is kept of one step of one run: the seconds of its spans in the window, and the sizes
    of the store's answers where the spans record them."""

    seconds: list[float] = field(default_factory=list)
    response_bytes: list[int] = field(default_factory=list)


@dataclass
class RunSteps:
    """The steps of one run: the minutes of its window, and its steps by role and name."""

    minutes: float
    steps: dict[tuple[str, str], StepSpans] = field(default_factory=dict)

    def add(
        self, role: str, step: str, seconds: float, size: int | None = None
    ) -> None:
        found = self.steps.setdefault((role, step), StepSpans())
        found.seconds.append(round(seconds, 6))
        if size is not None:
            found.response_bytes.append(size)

    def to_json(self) -> dict[str, Any]:
        return {
            "minutes": self.minutes,
            "steps": [
                {
                    "role": role,
                    "step": step,
                    "seconds": found.seconds,
                    "response_bytes": found.response_bytes,
                }
                for (role, step), found in sorted(self.steps.items())
            ],
        }

    @staticmethod
    def from_json(found: Mapping[str, Any]) -> RunSteps:
        return RunSteps(
            float(found["minutes"]),
            {
                (str(s["role"]), str(s["step"])): StepSpans(
                    [float(v) for v in s["seconds"]],
                    [int(v) for v in s["response_bytes"]],
                )
                for s in found["steps"]
            },
        )


def wms_of(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """The WMS of `record`, found by its role: the local name its spans carry as their participant
    and its IRI, from the participant of the record whose command takes `WMS_FLAG`; None where
    the record has none."""
    for participant in record.get("participants") or []:
        if WMS_FLAG in (participant.get("command") or []):
            iri = str(participant["resource"])
            return local_name(iri), iri
    return None


def steps_of(record: Mapping[str, Any], spans: Sequence[Span]) -> RunSteps | None:
    """The steps of the run of `record` from its `spans` (in the order they started), over the
    run's measured window; None for a record without a window."""
    window = record.get("window")
    if not window:
        return None
    start = datetime.fromisoformat(window["start"])
    end = datetime.fromisoformat(window["end"])
    found = RunSteps((end - start).total_seconds() / 60)
    wms = wms_of(record)
    wms_name, wms_iri = wms if wms else (None, None)
    reserves: list[Span] = []
    for span in spans:
        if span.participant == wms_name and _is_reserve(span):
            reserves.append(span)
    for span in spans:
        if not start <= span.start < end:
            continue
        role = WMS_ROLE if span.participant == wms_name else MODULE_ROLE
        step, size = _step_of(span, wms_iri)
        if step is not None:
            found.add(role, step, span.seconds, size)
        if role == WMS_ROLE and step == f"store {store_requests.READ}: {ALL_BOXES}":
            after = next((r for r in reserves if r.start >= span.end), None)
            if after is not None:
                found.add(WMS_ROLE, GAP_STEP, (after.start - span.end).total_seconds())
    return found


def _is_reserve(span: Span) -> bool:
    return span.kind == "CLIENT" and RESERVE_URL in str(
        span.attributes.get("http.url") or ""
    )


def _step_of(span: Span, wms_iri: str | None) -> tuple[str | None, int | None]:
    """The step name of `span` and, for a store request, the size of the store's answer."""
    attributes = span.attributes
    if attributes.get("store.request"):
        kind = attributes.get("store.kind")
        if kind not in (store_requests.READ, store_requests.WRITE):
            return None, None
        category = category_of(kind, str(attributes.get("store.text") or ""), wms_iri)
        step = f"store {kind}: {category}"
        if attributes.get("store.outcome") != store_requests.ACCEPTED:
            step += NOT_ACCEPTED
        size = attributes.get("store.response_bytes")
        return step, None if size is None else int(size)
    if span.name == COMMIT_SPAN:
        return (
            f"{span.name} ({'error' if span.status == 'ERROR' else 'accepted'})",
            None,
        )
    if span.kind == "CLIENT":
        endpoint = _ENDPOINT.search(str(attributes.get("http.url") or ""))
        if endpoint:
            return f"HTTP call: {endpoint[1]}", None
    return span.name, None


def read_run_spans(run_folder: Path) -> list[Span] | None:
    """The spans in `run_folder`, in the order they started; None for a run that has none (a run
    without the wrapper) or whose spans cannot be read."""
    folder = run_folder / SPANS_FOLDER
    if not folder.is_dir():
        return None
    try:
        return read_spans(folder) or None
    except (OSError, ValueError):
        return None


def read_steps(record: Mapping[str, Any], run_folder: Path) -> RunSteps | None:
    """The steps of the run of `record` from the spans in `run_folder`; None for a run that
    has no spans (a run without the wrapper)."""
    spans = read_run_spans(run_folder)
    return None if spans is None else steps_of(record, spans)


STEPS_COLUMNS = (
    "configuration",
    "role",
    "step",
    "runs",
    "steps",
    "steps_per_minute",
    "median_seconds",
    "p95_seconds",
    "median_response_bytes",
)


def steps_table(pooled: Iterable[tuple[int, RunSteps]]) -> pd.DataFrame:
    """The steps table of `pooled`, the steps of each valid run with the number of its
    configuration: one row for each configuration, role and step, over the runs pooled."""
    seconds: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    sizes: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    runs: dict[tuple[int, str, str], int] = defaultdict(int)
    minutes: dict[int, float] = defaultdict(float)
    for number, steps in pooled:
        minutes[number] += steps.minutes
        for (role, step), found in steps.steps.items():
            key = (number, role, step)
            seconds[key].extend(found.seconds)
            sizes[key].extend(found.response_bytes)
            runs[key] += 1
    rows = []
    for key in sorted(seconds):
        number, role, step = key
        values = seconds[key]
        rows.append(
            {
                "configuration": number,
                "role": role,
                "step": step,
                "runs": runs[key],
                "steps": len(values),
                "steps_per_minute": len(values) / minutes[number]
                if minutes[number]
                else None,
                "median_seconds": quantile(values, 0.5),
                "p95_seconds": quantile(values, 0.95),
                "median_response_bytes": quantile(sizes[key], 0.5)
                if sizes[key]
                else None,
            }
        )
    return pd.DataFrame(rows, columns=list(STEPS_COLUMNS))
