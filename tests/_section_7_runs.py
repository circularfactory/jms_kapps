"""Fabricated run folders for the per-commit table and the §7 figures: a KAPPS run with a commit of
each kind and shape, its spans with their ids, and its writes-only replay, in the form the harness
writes them (`observation`, `baseline`), with the keys of the §7 spec. No store, no run.

The commits of `kapps_spans`, each with the time its phases take (in seconds):

- `c1`, Module1, 10 s into the window, 1 s, a status write `running`: a schema read directly below
  the commit (class query 0.01), a class-spec derivation of 0.1 with a read of 0.02 (class query
  0.02, class derivation 0.08), a fetch of 0.3 with a read of 0.05 and a fetch inside it of 0.1
  with a read of 0.04 (fetch reads 0.09, fetch client 0.21), a diff of 0.05, the checked write of
  0.2, its receipt query of 0.03, its receipt removal of 0.02 and another read of 0.01 (other
  requests); the client remainder is the rest, 0.28.
- `c2`, Module1, 20 s, 0.5 s, a heartbeat in the old spelling, with a checked write of 0.1.
- `c3`, the WMS, 30 s, 0.4 s, not admitted (its span ended with an error).
- `c4`, Module1, 5 s before the window, 0.3 s, a status write `queued`.
- `c5`, Module1, 40 s, 0.2 s, no write.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from _evaluation_records import WINDOW, record, span, store_span, write, write_spans

from jms_kapps.evaluation.store_requests import RECEIPT_GRAPH, RECEIPT_PREDICATE
from jms_kapps.evaluation.wrapper import COMMIT_SPAN

SERVER = {"url": "https://graphdb.test"}
# The WMS's participant, as its spans name it (`report_steps.wms_of`).
WMS = "TheWarehouse7"
OPERATION = "http://example.org/stations#Op1"


def _with_ids(
    found: dict[str, Any], span_id: str, parent_id: str | None
) -> dict[str, Any]:
    return found | {"span_id": span_id, "parent_id": parent_id}


def _checked_write(
    participant: str,
    receipt: str,
    at: float,
    seconds: float,
    *,
    heartbeat: str | None = None,
) -> dict[str, Any]:
    body = f'<{OPERATION}> <http://example.org/s#hasOperationStatus> "running" .'
    if heartbeat:
        body = f'<http://example.org/s#Svc> <http://example.org/s#{heartbeat}> "now" .'
    text = f"INSERT DATA {{ {body} <{receipt}> <{RECEIPT_PREDICATE}> true }}"
    found = store_span(participant, text, at, seconds, kind="write")
    if not heartbeat:
        found["attributes"] |= {
            "store.operation": [OPERATION],
            "store.status_written": ["running"],
        }
    found["attributes"]["store.receipt_written"] = receipt
    return found


def _receipt_ask(
    participant: str, receipt: str, at: float, seconds: float
) -> dict[str, Any]:
    text = f"ASK {{ GRAPH <{RECEIPT_GRAPH}> {{ <{receipt}> ?p ?o }} }}"
    found = store_span(participant, text, at, seconds)
    found["attributes"] |= {"store.receipt_asked": receipt, "store.receipt_found": True}
    return found


def _receipt_removal(
    participant: str, receipt: str, at: float, seconds: float
) -> dict[str, Any]:
    text = f"DELETE WHERE {{ GRAPH <{RECEIPT_GRAPH}> {{ <{receipt}> ?p ?o }} }}"
    return store_span(participant, text, at, seconds, kind="write")


def kapps_spans(prefix: str = "") -> list[dict[str, Any]]:
    """The spans of the commits above; `prefix` before every span id, so that two runs' spans
    differ."""

    def ids(
        found: dict[str, Any], span_id: str, parent_id: str | None
    ) -> dict[str, Any]:
        return _with_ids(
            found, prefix + span_id, None if parent_id is None else prefix + parent_id
        )

    m = "Module1"
    return [
        ids(span(m, COMMIT_SPAN, 10.0, 1.0), "c1", None),
        ids(
            store_span(m, "SELECT ?class FROM <urn:g> WHERE { }", 10.0, 0.01),
            "r1",
            "c1",
        ),
        ids(span(m, "kapps_ogm.OGM.get_class_spec", 10.01, 0.1), "g1", "c1"),
        ids(
            store_span(m, "SELECT ?property FROM <urn:g> WHERE { }", 10.02, 0.02),
            "r2",
            "g1",
        ),
        ids(span(m, "kapps_ogm.OGM.fetch", 10.11, 0.3), "f1", "c1"),
        ids(
            store_span(m, "SELECT ?s ?p ?o WHERE {BIND(<x> AS ?s)}", 10.12, 0.05),
            "r3",
            "f1",
        ),
        ids(span(m, "kapps_ogm.OGM.fetch", 10.2, 0.1), "f2", "f1"),
        ids(
            store_span(m, "SELECT ?s ?p ?o WHERE {BIND(<y> AS ?s)}", 10.21, 0.04),
            "r4",
            "f2",
        ),
        ids(span(m, "kapps_ogm.Node.diff", 10.41, 0.05), "d1", "c1"),
        ids(_checked_write(m, "urn:receipt:1", 10.46, 0.2), "w1", "c1"),
        ids(_receipt_ask(m, "urn:receipt:1", 10.66, 0.03), "a1", "c1"),
        ids(_receipt_removal(m, "urn:receipt:1", 10.69, 0.02), "x1", "c1"),
        ids(store_span(m, "SELECT ?x WHERE { ?x ?y ?z }", 10.71, 0.01), "o1", "c1"),
        ids(span(m, COMMIT_SPAN, 20.0, 0.5), "c2", None),
        ids(
            _checked_write(
                m, "urn:receipt:2", 20.1, 0.1, heartbeat="last" + "Heartbeat"
            ),
            "w2",
            "c2",
        ),
        ids(span(WMS, COMMIT_SPAN, 30.0, 0.4, status="ERROR"), "c3", None),
        ids(span(m, COMMIT_SPAN, -5.0, 0.3), "c4", None),
        ids(_checked_write(m, "urn:receipt:4", -4.9, 0.1), "w4", "c4"),
        ids(span(m, COMMIT_SPAN, 40.0, 0.2), "c5", None),
    ]


def kapps_record(
    run_id: str,
    *,
    shacl: bool = True,
    repetition: int = 1,
    heartbeat_interval: float | None | str = 30.0,
    **more: Any,
) -> dict[str, Any]:
    """The record of a KAPPS run; `heartbeat_interval` "absent" leaves the run setting out, as
    a record made before it was one."""
    found = record(run_id, server=SERVER, **more)
    found["configuration"] = (
        f'topology = "cross"\nshacl = {"true" if shacl else "false"}\n'
    )
    found["sweep"] = {
        "spec": "spec.toml",
        "configuration": 1 if shacl else 2,
        "repetition": repetition,
    }
    found["run_settings"] = {"wms_pick_seed": repetition}
    if heartbeat_interval != "absent":
        found["run_settings"]["heartbeat_interval_seconds"] = heartbeat_interval
    return found


def replayed_write(
    seconds: float,
    *,
    commit: str | None,
    recorded_at: str,
    text: str,
    category: str = "checked write (commit body)",
    offset: float | None = 10.46,
    late: float | None = 0.001,
    match: bool | None = True,
    overtook: bool = False,
) -> dict[str, Any]:
    """One replayed write as a writes-only replay's record holds it."""
    return {
        "participant": "Module1",
        "at": recorded_at,
        "seconds": seconds,
        "kind": "write",
        "outcome": "accepted",
        "refusal": None,
        "applied": True,
        "text": text,
        "window": offset is not None,
        "offset_seconds": offset,
        "late_seconds": late,
        "recorded_at": recorded_at,
        "span_id": None,
        "commit": commit,
        "category": category,
        "status_code": 204,
        "recorded_outcome": "accepted",
        "recorded_applied": True,
        "outcome_match": match,
        "overtook_previous": overtook,
    }


def at(seconds: float) -> str:
    """The time `seconds` after the window's start, as the record writes it."""
    return (
        datetime.fromisoformat(WINDOW["start"]) + timedelta(seconds=seconds)
    ).isoformat()


def replay_record(
    run_id: str,
    replayed: str,
    writes: Sequence[Mapping[str, Any]],
    *,
    valid: bool = True,
    mode: str = "writes only",
) -> dict[str, Any]:
    """The record of a writes-only replay of `replayed`."""
    found = record(
        run_id,
        run_kind="baseline",
        replays=replayed,
        requests=list(writes),
        server=SERVER,
        valid=valid,
        failed_checks=[] if valid else [{"check": "outcome mismatches", "count": 1}],
    )
    found["replay"] = {"mode": mode}
    return found


def replay_writes(
    prefix: str = "", *, with_commit: bool = True
) -> list[dict[str, Any]]:
    """The replayed writes of the run of `kapps_spans`: c1's checked write (0.15 s, it overtook
    the write before it) and its receipt removal (same commit, 0.01 s), c4's checked write before
    the window; c2's heartbeat is recorded after the window's end and has no replayed write."""
    checked = "INSERT DATA {{ <{r}> <{p}> true }}"
    return [
        replayed_write(
            0.15,
            commit=prefix + "c1" if with_commit else None,
            recorded_at=at(10.46),
            text=checked.format(r="urn:receipt:1", p=RECEIPT_PREDICATE),
            overtook=True,
        ),
        replayed_write(
            0.01,
            commit=prefix + "c1" if with_commit else None,
            recorded_at=at(10.69),
            text=f"DELETE WHERE {{ GRAPH <{RECEIPT_GRAPH}> {{ <urn:receipt:1> ?p ?o }} }}",
            category="receipt removal",
            offset=10.69,
        ),
        replayed_write(
            0.05,
            commit=prefix + "c4" if with_commit else None,
            recorded_at=at(-4.9),
            text=checked.format(r="urn:receipt:4", p=RECEIPT_PREDICATE),
            offset=None,
            late=None,
        ),
    ]


def write_run(
    runs: Path, found: Mapping[str, Any], spans: Sequence[Mapping[str, Any]] | None
) -> Path:
    """Write the record and its spans, if any, into `runs`; the run's folder."""
    write(runs, found)
    if spans is not None:
        write_spans(runs, str(found["run_id"]), *spans)
    return runs / str(found["run_id"])


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
