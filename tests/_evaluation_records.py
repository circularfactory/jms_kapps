"""Fabricated records of the measurement harness, in the form part 1 and part 2 write them
(`bench.run`, `observation.observe`), with the numbers a test chooses: for the table of a spec and
the ramp's broken rule, which read records and nothing else. No store, no run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from jms_kapps.evaluation.spec import SpecConfiguration

WINDOW = {
    "start": "2026-10-12T10:01:00+00:00",
    "end": "2026-10-12T10:06:00+00:00",
    "minutes": 5.0,
}


# The participants of a record: a module, and the WMS, whose command takes the WMS's strategy.
WMS_IRI = "http://example.org/stations#TheWarehouse7"
PARTICIPANTS = [
    {
        "name": "Module1",
        "resource": "http://example.org/stations#Module1",
        "command": ["python", "-m", "module", "--module-strategy", "real"],
    },
    {
        "name": "WMS",
        "resource": WMS_IRI,
        "command": ["python", "-m", "wms", "--wms-strategy", "real"],
    },
]


def request(
    seconds: float,
    *,
    outcome: str = "accepted",
    kind: str = "write",
    window: bool = True,
    at: str = "2026-10-12T10:02:00+00:00",
    reason: str | None = None,
    participant: str = "Module1",
) -> dict[str, Any]:
    """One store request as the record's measures hold it."""
    return {
        "participant": participant,
        "at": at,
        "seconds": seconds,
        "method": "POST",
        "path": "repositories/eval-x/statements",
        "kind": kind,
        "outcome": outcome,
        "refusal": None,
        "reason": reason,
        "writes": [],
        "applied": True if kind == "write" and outcome == "accepted" else None,
        "text": "INSERT DATA { }",
        "window": window,
    }


def heap_refusal(at: str, in_window: bool) -> dict[str, Any]:
    """One refusal for want of heap, as the record's measures list it."""
    return {
        "participant": "Module2",
        "at": at,
        "reason": "Insufficient free Heap Memory 249Mb for group by and distinct",
        "window": in_window,
    }


def record(
    run_id: str,
    configuration: SpecConfiguration | None = None,
    *,
    repetition: int = 1,
    run_kind: str = "uc2",
    outcome: str = "completed",
    valid: bool | None = True,
    failed_checks: Sequence[Mapping[str, Any]] = (),
    commit_latencies: Sequence[float] = (),
    requests: Sequence[Mapping[str, Any]] = (),
    completed_writes_per_minute: float = 0.0,
    transports_ended_per_minute: float = 0.0,
    store_requests_in_window: int | None = None,
    refused_in_window: int = 0,
    timed_out_in_window: int = 0,
    heap_refusals_in_window: int = 0,
    heap_refusals_before_the_load: int = 0,
    heap_refusals_in_the_drain: int = 0,
    stuck: Sequence[Mapping[str, Any]] = (),
    failed: Sequence[Mapping[str, Any]] = (),
    refused_spawns: Sequence[Mapping[str, Any]] = (),
    matcher_reads: int = 150,
    matcher_seconds: float = 3.0,
    replays: str | None = None,
    participants: Sequence[Mapping[str, Any]] | None = None,
    server: Mapping[str, Any] | None = None,
    highest_box_before_the_window: int | None = None,
    client_load: Mapping[str, Any] | None = None,
    pass_number: int | None = None,
    round_number: int | None = None,
    window: Mapping[str, Any] | None = None,
    spawns_accepted: int | None = None,
    spawns_refused: int = 0,
) -> dict[str, Any]:
    """A record of one run with the numbers given; a run without the wrapper holds only the
    measures that need none, and a baseline those of its store requests."""
    in_window = [r for r in requests if r["window"]]
    moment = WINDOW if window is None else dict(window)
    measures: dict[str, Any] | None = {
        "window": moment,
        "transports_ended_in_window": round(transports_ended_per_minute * 5),
        "transports_ended_per_minute": transports_ended_per_minute,
    }
    store_measures = {
        "store_requests_in_window": len(in_window)
        if store_requests_in_window is None
        else store_requests_in_window,
        "refused_in_window": refused_in_window,
        "timed_out_in_window": timed_out_in_window,
        "refused_or_unanswered_share": None,
        "completed_writes_in_window": round(completed_writes_per_minute * 5),
        "completed_writes_per_minute": completed_writes_per_minute,
        "heap_refusals": [
            heap_refusal(at, in_window)
            for count, at, in_window in (
                (heap_refusals_before_the_load, "2026-10-12T10:00:30+00:00", False),
                (heap_refusals_in_window, "2026-10-12T10:03:00+00:00", True),
                (heap_refusals_in_the_drain, "2026-10-12T10:07:00+00:00", False),
            )
            for _ in range(count)
        ],
        "heap_refusals_in_window": heap_refusals_in_window,
        "store_requests": list(requests),
    }
    if run_kind == "uc2":
        assert measures is not None
        measures |= store_measures | {
            "commit_latencies_seconds": list(commit_latencies),
            "commits": [],
        }
    elif run_kind == "baseline":
        measures = {"window": moment} | store_measures
    if spawns_accepted is not None and measures is not None:
        # The spawns of the window; a record of an earlier run holds none.
        offered = spawns_accepted + spawns_refused
        measures |= {
            "spawns_offered_in_window": offered,
            "spawns_accepted_in_window": spawns_accepted,
            "spawns_accepted_share": spawns_accepted / offered if offered else None,
        }
    if outcome != "completed":
        measures, valid = None, None
    found = {
        "run_kind": run_kind,
        "iteration": "iteration one",
        "run_id": run_id,
        "repository": f"eval-{run_id}",
        "outcome": outcome,
        "sweep": None
        if configuration is None
        else {
            "spec": "spec.toml",
            "configuration": configuration.number,
            "key": configuration.key,
            "repetition": repetition,
            "repetitions": configuration.repetitions,
            # A run under the precision rule says its round; one made before
            # the rounds says its pass.
            **({} if pass_number is None else {"pass": pass_number}),
            **({} if round_number is None else {"round": round_number}),
        },
        "replayed_run": None if replays is None else {"run_id": replays},
        # A record of the first iteration holds the highest box before the window; one of today
        # holds the number of transports the WarehouseManagementSystem queued.
        "transports": {
            **(
                {"queued_by_the_wms": 0}
                if highest_box_before_the_window is None
                else {"highest_box_before_the_window": highest_box_before_the_window}
            ),
            "done": [],
            "failed": list(failed),
            "open": [],
        },
        "spawns": {
            "spawned": 10,
            "refused": list(refused_spawns),
            "refused_by_reason": {},
        },
        "matcher": None
        if run_kind == "baseline"
        else {
            "interval_seconds": 2.0,
            "read_count": matcher_reads,
            "read_seconds": matcher_seconds,
            "reads": [],
            "ends": [],
        },
        "measures": measures,
        "valid": valid,
        "failed_checks": list(failed_checks),
        "checks_made": [],
        "stuck": list(stuck),
        "participants": PARTICIPANTS if participants is None else list(participants),
        "warm_up": None,
        "window": {"start": moment["start"], "end": moment["end"]},
        # A record of the "before" series has no key at all.
        **({} if client_load is None else {"client_load": dict(client_load)}),
    }
    if server is not None:
        found["server"] = dict(server)
    return found


def client_sample(
    at: str, cpu_percent: float | None, memory_gib: float
) -> dict[str, Any]:
    """One sample of the client host's load, as the record's `client_load` holds it."""
    return {
        "at": at,
        "cpu_percent": cpu_percent,
        "memory_used_bytes": round(memory_gib * 1024**3),
    }


def sampled_load(
    *samples: Mapping[str, Any], status: str = "sampled"
) -> dict[str, Any]:
    """The `client_load` of a record with the samples given."""
    return {
        "status": status,
        "interval_seconds": 1.0,
        "memory_total_bytes": 64 * 1024**3,
        "samples": list(samples),
    }


def server_watch(samples: int = 3) -> dict[str, Any]:
    """What a record of the server watch holds of the server: the watch with
    `samples` samples, the counters of every repository at the start and the end, the page cache,
    the license and the statement counts. A record of the "before" series has none of it."""
    view = {"request": "GET rest/monitor/infrastructure", "status": 200}
    monitors = {
        name: {"request": f"GET rest/monitor/repository/{name}", "status": 200}
        for name in ("jms-kapps", "kapps-demo", "Tests")
    }
    return {
        "url": "http://graphdb.test",
        "watch": {
            "interval_seconds": 5.0,
            "sample_count": samples,
            "samples": [
                {
                    "at": f"2026-10-12T10:01:{5 * n:02d}+00:00",
                    "resources": view | {"answer": {"cpuLoad": 3 + n}},
                    "round_trip": {
                        "request": "GET rest/info/version",
                        "status": 200,
                        "milliseconds": 3.5,
                    },
                    "seconds": 0.01,
                }
                for n in range(samples)
            ],
        },
        "repository_monitors_at_the_start": monitors,
        "repository_monitors_at_the_drain_end": monitors,
        "structures_at_the_window_start": view | {"answer": {"cache": {"hit": 1}}},
        "structures_at_the_drain_end": view | {"answer": {"cache": {"hit": 2}}},
        "license": view | {"answer": {"maxCpuCores": 8}},
        "statements_before_the_participants": view | {"answer": {"explicit": 1}},
        "statements_after_the_participants": view | {"answer": {"explicit": 2}},
    }


def write(runs: Path, *records: Mapping[str, Any]) -> None:
    """Write each record into a folder of its own below `runs`, named by its run id."""
    for found in records:
        folder = runs / str(found["run_id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "record.json").write_text(json.dumps(found), encoding="utf-8")


_START_NS = int(datetime.fromisoformat(WINDOW["start"]).timestamp()) * 1_000_000_000


def span(
    participant: str,
    name: str,
    at: float,
    seconds: float,
    *,
    kind: str = "INTERNAL",
    status: str = "UNSET",
    **attributes: Any,
) -> dict[str, Any]:
    """One line of a participant's spans file: it started `at` seconds after the window's start
    (a negative number is before it) and lasted `seconds`."""
    start = _START_NS + round(at * 1e9)
    return {
        "participant": participant,
        "name": name,
        "kind": kind,
        "span_id": None,
        "parent_id": None,
        "start": start,
        "end": start + round(seconds * 1e9),
        "status": status,
        "attributes": attributes,
        "events": [],
    }


def store_span(
    participant: str,
    text: str,
    at: float,
    seconds: float,
    *,
    kind: str = "read",
    outcome: str = "accepted",
    response_bytes: int | None = None,
) -> dict[str, Any]:
    """The span of one store request (all of them have the same name, as the wrapper's do)."""
    attributes: dict[str, Any] = {
        "store.request": True,
        "store.kind": kind,
        "store.text": text,
        "store.outcome": outcome,
    }
    if response_bytes is not None:
        attributes["store.response_bytes"] = response_bytes
    return span(participant, "POST", at, seconds, kind="CLIENT", **attributes)


def write_spans(runs: Path, run_id: str, *spans: Mapping[str, Any]) -> None:
    """Write the spans into the run's folder, one file for each participant."""
    folder = runs / run_id / "spans"
    folder.mkdir(parents=True, exist_ok=True)
    for found in spans:
        with (folder / f"{found['participant']}.jsonl").open(
            "a", encoding="utf-8"
        ) as out:
            out.write(json.dumps(found) + "\n")
