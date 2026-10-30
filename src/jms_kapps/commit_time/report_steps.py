"""The categories of a run's store requests and the roles of its participants, as the per-commit
table (`commit_table`) and the writes-only replay (`baseline`) read them.

**A store request's category** is taken from the text of its query (`category_of`): the reads by
`READ_CATEGORIES`, the writes by `WRITE_CATEGORIES`. A query that matches no category is
`other read` or `other write`, never an error.

**The role** of a span is told by its participant: the WMS is the participant of the record whose
command takes the WMS's strategy (`WMS_FLAG`, `wms_of`), and every other participant is a module.
The WMS's IRI, which the category of a possession read needs, is the resource of that participant
in the record, not a fixed value. A record without participants has no WMS: all its spans are
modules'.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from jms_kapps.commit_time import store_requests
from jms_kapps.commit_time.observation import Span, read_spans
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
place of its read of every box it holds."""

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
    ("heartbeat update", lambda text, wms: store_requests.names_heartbeat(text)),
    # A checked write is an update that inserts a receipt (`store_requests.receipt_written`).
    (
        "checked write (commit body)",
        lambda text, wms: store_requests.receipt_written(text) is not None,
    ),
)


_SPACES = re.compile(r"\s+")


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


def wms_of(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """The WMS of `record`, found by its role: the local name its spans carry as their participant
    and its IRI, from the participant of the record whose command takes `WMS_FLAG`; None where
    the record has none."""
    for participant in record.get("participants") or []:
        if WMS_FLAG in (participant.get("command") or []):
            iri = str(participant["resource"])
            return local_name(iri), iri
    return None


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
