"""The results of an experiment's store folder: the index of its runs and the
results page. The commit time experiment script
(`commit_time/experiment.py`) calls these after the runs of one store; they read the
records the harness wrote and change none of them.

**The store folder** holds one folder per heartbeat level (`heartbeats-on`, `heartbeats-off`),
each with `runs/` (one folder per run and per replay, named by its run id). Beside them the
module writes:

- `arms.json`, **the index** (`write_index`): one entry per run and replay, in the order they were
  made, with the arm, the heartbeat level, the repetition, the pick seed, the run a replay
  replays, the start and the end time (the record's first and last action), the outcome and the
  folder of the record (`index_entry`).
- `results.json` and `results.md`, **the results page** (`write_results`): the store's
  version, edition, ruleset and heap; the client's CPU model, logical CPUs, memory and OS, and,
  for the local store, that its host is this client; the jms_kapps commit; per run the commits of
  the window admitted and not admitted by kind, the refusals of the window by kind, the spawns
  of the window offered and accepted, the checks, the client's CPU median and whether the
  server watch shows activity of other repositories (`run_facts`); per
  replay the replayed writes, the verified receipts, the late and the overtaking writes and the
  outcome mismatches (`replay_facts`); the order of each repetition's arms, as the index has
  them (`repetition_orders`); and the runs with a refused spawn and those without a spawn count.

**A late write** of a replay is a write of the window that left more than `LATE_SECONDS` after
its recorded offset; the page also gives the largest lateness.

**A refused spawn** is one the WarehouseManagementSystem was asked for in the window and did not
make: a run whose accepted spawns of the window are fewer than its offered ones has one. A run
whose record counts no spawns has no spawn count; neither is the steady load the run asked for.
"""

from __future__ import annotations

import json
import statistics
import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from jms_kapps.commit_time import client_load, store_requests

# The files of the results page into a store folder.
INDEX_FILE = "arms.json"
RESULTS_JSON = "results.json"
RESULTS_MARKDOWN = "results.md"

# The store whose host is the client itself: the local GraphDB of the compose file.
LOCAL_STORE = "local"

# A replayed write that left more than this many seconds after its recorded offset is late.
LATE_SECONDS = 0.1

# The record's file in a run's folder.
RECORD_FILE = "record.json"

# What a fact says where there is nothing to tell it by.
NO_DATA = "no data"
# The other-activity facts of a run.
ACTIVITY = "activity"
NONE = "none"
# The monitoring figures of a repository: counters that only rise, and gauges.
COUNTERS = ("epoolReads", "epoolWrites")
GAUGES = ("openConnections", "activeTransactions")


def _answer_or_none(view: Any) -> Mapping[str, Any] | None:
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
        before, after = _answer_or_none(start.get(name)), _answer_or_none(end.get(name))
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


def client_cpu_median(record: Mapping[str, Any]) -> float | None:
    """The median CPU use of the client host in the window, in percent, from the samples of
    `record`; None where the record holds no sample, none lies in the window or none holds a
    CPU figure, or the host had no `/proc`."""
    load = record.get("client_load")
    if not isinstance(load, Mapping) or load.get("status") == client_load.NOT_AVAILABLE:
        return None
    window = record.get("window") or (record.get("measures") or {}).get("window") or {}
    if window.get("start") is None or window.get("end") is None:
        return None
    start = datetime.fromisoformat(window["start"])
    end = datetime.fromisoformat(window["end"])
    cpu = [
        float(sample["cpu_percent"])
        for sample in load.get("samples") or []
        if start <= datetime.fromisoformat(sample["at"]) <= end
        and sample.get("cpu_percent") is not None
    ]
    return statistics.median(cpu) if cpu else None


# --- One run, one replay ------------------------------------------------------------------------


def run_facts(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the results page says of one run of use case 2 (or of a replay, which has no commits):
    its outcome and validity, the checks made and failed, the commits of the window admitted and
    not admitted by kind, the refusals of the window by kind, the requests of the window that got
    no answer, the spawns of the window offered and accepted, the client's CPU median and the
    other activity on the server."""
    measures = record.get("measures") or {}
    admitted: dict[str, int] = {}
    not_admitted: dict[str, int] = {}
    for commit in measures.get("commits") or []:
        if not commit.get("window"):
            continue
        kind = str(commit.get("kind") or "kind not recorded")
        if commit.get("outcome") == store_requests.ACCEPTED:
            admitted[kind] = admitted.get(kind, 0) + 1
        else:
            not_admitted[kind] = not_admitted.get(kind, 0) + 1
    refusals: dict[str, int] = {}
    for request in measures.get("store_requests") or []:
        if not request.get("window"):
            continue
        if store_requests.outcome_of(request) != store_requests.REFUSED:
            continue
        kind = str(request.get("refusal") or store_requests.OTHER_REFUSAL)
        refusals[kind] = refusals.get(kind, 0) + 1
    failed_checks = []
    for check in record.get("failed_checks") or []:
        failed_checks.append(str(check.get("check")))
    return {
        "run_id": record.get("run_id"),
        "run_kind": record.get("run_kind"),
        "outcome": record.get("outcome"),
        "valid": record.get("valid"),
        "checks_made": list(record.get("checks_made") or []),
        "failed_checks": failed_checks,
        "commits_admitted_in_window": admitted,
        "commits_not_admitted_in_window": not_admitted,
        "refusals_in_window": refusals,
        "timed_out_in_window": measures.get("timed_out_in_window"),
        "connection_dropped_in_window": measures.get("connection_dropped_in_window"),
        "spawns_offered_in_window": measures.get("spawns_offered_in_window"),
        "spawns_accepted_in_window": measures.get("spawns_accepted_in_window"),
        "client_cpu_median_percent": client_cpu_median(record),
        "other_activity": other_activity(record)["status"],
    }


def replay_facts(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the results page says of one writes-only replay: the run it replays, its outcome and
    validity, the writes it replayed before and in the window, the receipts it verified, its late
    writes (`LATE_SECONDS`) and the largest lateness, the writes that overtook their
    participant's previous write, the outcome mismatches with the writes that differ, and whether
    the server watch shows activity of other repositories."""
    replay = record.get("replay") or {}
    requests = (record.get("measures") or {}).get("store_requests") or []
    late = 0
    largest_lateness: float | None = None
    overtaking = 0
    for request in requests:
        lateness = request.get("late_seconds")
        if lateness is not None:
            if lateness > LATE_SECONDS:
                late += 1
            if largest_lateness is None or lateness > largest_lateness:
                largest_lateness = float(lateness)
        if request.get("overtook_previous"):
            overtaking += 1
    mismatches = record.get("outcome_mismatches") or {}
    return {
        "run_id": record.get("run_id"),
        "replayed_run": (record.get("replayed_run") or {}).get("run_id"),
        "mode": replay.get("mode"),
        "outcome": record.get("outcome"),
        "valid": record.get("valid"),
        "writes_before_the_window": replay.get("before_the_window"),
        "writes_in_the_window": replay.get("in_the_window"),
        "verified_receipts": replay.get("verifications"),
        "late_writes": late,
        "largest_lateness_seconds": largest_lateness,
        "overtaking_writes": overtaking,
        "mismatches": mismatches.get("count"),
        "not_comparable": mismatches.get("not_comparable"),
        "mismatched_writes": list(mismatches.get("writes") or []),
        "other_activity": other_activity(record)["status"],
    }


# --- The index ----------------------------------------------------------------------------------


def index_entry(
    record: Mapping[str, Any],
    *,
    folder: Path,
    store_folder: Path,
    arm: str,
    heartbeats: str,
    repetition: int,
) -> dict[str, Any]:
    """The index's entry of the run or replay whose `record` is in `folder`, below
    `store_folder`: its arm, heartbeat level and repetition as the experiment made it, its pick
    seed (the run settings', else the one in its configuration), the run it replays, its start
    and end time (its first and last action), its outcome and its folder."""
    actions = record.get("actions") or []
    pick_seed = (record.get("run_settings") or {}).get("wms_pick_seed")
    if pick_seed is None:
        try:
            configuration = tomllib.loads(str(record.get("configuration") or ""))
        except tomllib.TOMLDecodeError:
            configuration = {}
        pick_seed = configuration.get("wms_pick_seed")
    return {
        "run_id": record.get("run_id"),
        "run_kind": record.get("run_kind"),
        "arm": arm,
        "heartbeats": heartbeats,
        "repetition": repetition,
        "pick_seed": pick_seed,
        "replayed_run": (record.get("replayed_run") or {}).get("run_id"),
        "started_at": actions[0].get("at") if actions else None,
        "ended_at": actions[-1].get("at") if actions else None,
        "outcome": record.get("outcome"),
        "folder": str(folder.relative_to(store_folder)),
    }


def write_index(store_folder: Path, entries: Sequence[Mapping[str, Any]]) -> Path:
    """Write the index `entries` into `store_folder` as `arms.json`; its path."""
    path = store_folder / INDEX_FILE
    path.write_text(
        json.dumps(list(entries), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# --- The results page -------------------------------------------------------------------------


def _answer(view: Any) -> Mapping[str, Any]:
    """The answer of a server view the record keeps, or an empty one."""
    if isinstance(view, Mapping) and isinstance(view.get("answer"), Mapping):
        return view["answer"]
    return {}


def store_facts(record: Mapping[str, Any]) -> dict[str, Any]:
    """The store as `record` saw it at its start: its URL, version, edition, ruleset and heap."""
    server = record.get("server") or {}
    version = _answer(server.get("version"))
    license_view = _answer(server.get("license"))
    resources = _answer(server.get("resources_at_the_start"))
    heap = resources.get("heapMemoryUsage") or {}
    return {
        "url": server.get("url"),
        "version": version.get("productVersion"),
        "edition": license_view.get("product") or version.get("productType"),
        "licensed_cores": license_view.get("maxCpuCores"),
        "ruleset": (server.get("repository_config") or {}).get("ruleset"),
        "heap_max_bytes": heap.get("max"),
    }


def client_facts(record: Mapping[str, Any], *, store: str) -> dict[str, Any]:
    """The client host as `record` saw it: its CPU model, logical CPUs, memory and OS, and
    whether the store runs on it (the local store does)."""
    client = record.get("client") or {}
    software = record.get("software") or {}
    return {
        "cpu": client.get("cpu"),
        "logical_cpus": client.get("logical_cpus"),
        "memory_bytes": client.get("memory_bytes"),
        "os": (software.get("os") or {}).get("platform"),
        "store_on_this_host": store == LOCAL_STORE,
    }


def repetition_orders(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The arms of each heartbeat level's repetition in the order the index `entries` has them,
    which is the order they ran in: one item per level and repetition, in the order each began."""
    orders: dict[tuple[str, int], list[str]] = {}
    for entry in entries:
        key = (str(entry["heartbeats"]), int(entry["repetition"]))
        orders.setdefault(key, []).append(str(entry["arm"]))
    found = []
    for (heartbeats, repetition), arms in orders.items():
        found.append({"heartbeats": heartbeats, "repetition": repetition, "arms": arms})
    return found


def results_facts(
    store_folder: Path, *, store: str, entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The facts of the results page of `store_folder` (see the module), from the records the
    index `entries` name. Each record is read once."""
    store_seen: dict[str, Any] | None = None
    client_seen: dict[str, Any] | None = None
    jms_kapps: dict[str, Any] | None = None
    runs: list[dict[str, Any]] = []
    replays: list[dict[str, Any]] = []
    for entry in entries:
        folder = store_folder / str(entry["folder"])
        record = json.loads((folder / RECORD_FILE).read_text(encoding="utf-8"))
        if store_seen is None and record.get("server", {}).get("version"):
            store_seen = store_facts(record)
        if client_seen is None and record.get("client"):
            client_seen = client_facts(record, store=store)
        if jms_kapps is None and (record.get("software") or {}).get("jms-kapps"):
            jms_kapps = dict(record["software"]["jms-kapps"])
        where = {
            "arm": entry["arm"],
            "heartbeats": entry["heartbeats"],
            "repetition": entry["repetition"],
        }
        if record.get("run_kind") == "baseline":
            replays.append(where | replay_facts(record))
        else:
            runs.append(where | run_facts(record))
    mismatching = []
    for replay in replays:
        if replay["mismatches"]:
            mismatching.append(replay["run_id"])
    refused_spawns = []
    without_spawn_counts = []
    for run in runs:
        offered = run["spawns_offered_in_window"]
        accepted = run["spawns_accepted_in_window"]
        if offered is None or accepted is None:
            without_spawn_counts.append(run["run_id"])
        elif accepted < offered:
            refused_spawns.append(run["run_id"])
    return {
        "store": store,
        "server": store_seen,
        "client": client_seen,
        "jms_kapps": jms_kapps,
        "repetition_orders": repetition_orders(entries),
        "runs": runs,
        "replays": replays,
        "replays_with_mismatches": mismatching,
        "runs_with_refused_spawns": refused_spawns,
        "runs_without_spawn_counts": without_spawn_counts,
    }


def _counts(found: Mapping[str, int]) -> str:
    if not found:
        return "none"
    parts = []
    for kind, count in found.items():
        parts.append(f"{kind} {count}")
    return ", ".join(parts)


def _shown(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def results_markdown(facts: Mapping[str, Any]) -> str:
    """The results page, for a person to read."""
    server = facts["server"] or {}
    client = facts["client"] or {}
    jms_kapps = facts["jms_kapps"] or {}
    heap = server.get("heap_max_bytes")
    memory = client.get("memory_bytes")
    lines = [
        f"# Results of the store `{facts['store']}`",
        "",
        "## The store",
        "",
        f"- URL: {_shown(server.get('url'))}",
        (
            f"- GraphDB {_shown(server.get('version'))}, {_shown(server.get('edition'))}, "
            f"licensed cores {_shown(server.get('licensed_cores'))}"
        ),
        f"- ruleset: {_shown(server.get('ruleset'))}",
        "- heap: "
        + ("-" if heap is None else f"{heap / 1024**3:.1f} GiB ({heap} bytes)"),
        "",
        "## The client",
        "",
        f"- CPU: {_shown(client.get('cpu'))}, {_shown(client.get('logical_cpus'))} logical CPUs",
        "- memory: " + ("-" if memory is None else f"{memory / 1024**3:.1f} GiB"),
        f"- OS: {_shown(client.get('os'))}",
        "- the store runs on this client: "
        + ("yes" if client.get("store_on_this_host") else "no"),
        f"- jms_kapps commit: {_shown(jms_kapps.get('commit'))}"
        + (" (modified)" if jms_kapps.get("modified") else ""),
        "",
        "## Replays with outcome mismatches",
        "",
    ]
    if facts["replays_with_mismatches"]:
        for run_id in facts["replays_with_mismatches"]:
            lines.append(f"- {run_id}: invalid, left out of the figures")
    else:
        lines.append("None.")
    lines += ["", "## Runs with refused spawns", ""]
    for run in facts["runs"]:
        if run["run_id"] in facts["runs_with_refused_spawns"]:
            lines.append(
                f"- {run['run_id']}: {run['spawns_accepted_in_window']} of "
                f"{run['spawns_offered_in_window']} spawns of the window accepted"
            )
        elif run["run_id"] in facts["runs_without_spawn_counts"]:
            lines.append(f"- {run['run_id']}: the record counts no spawns")
    if not facts["runs_with_refused_spawns"] and not facts["runs_without_spawn_counts"]:
        lines.append("None.")
    lines += ["", "## The order of each repetition", ""]
    for order in facts["repetition_orders"]:
        lines.append(
            f"- heartbeats {order['heartbeats']}, repetition {order['repetition']}: "
            + ", ".join(order["arms"])
        )
    lines += [
        "",
        "## The runs",
        "",
        (
            "| Run | Arm | Heartbeats | Rep. | Outcome | Valid | Failed checks | Admitted in "
            "window | Not admitted | Refusals in window | Spawns accepted / offered | Client CPU "
            "median % | Other activity |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in facts["runs"]:
        lines.append(
            f"| {run['run_id']} | {run['arm']} | {run['heartbeats']} | {run['repetition']} "
            f"| {run['outcome']} | {_shown(run['valid'])} "
            f"| {', '.join(run['failed_checks']) or 'none'} "
            f"| {_counts(run['commits_admitted_in_window'])} "
            f"| {_counts(run['commits_not_admitted_in_window'])} "
            f"| {_counts(run['refusals_in_window'])} "
            f"| {_shown(run['spawns_accepted_in_window'])} / "
            f"{_shown(run['spawns_offered_in_window'])} "
            f"| {_shown(run['client_cpu_median_percent'])} "
            f"| {run['other_activity']} |"
        )
    lines += [
        "",
        "## The writes-only replays",
        "",
        (
            "| Replay | Replays | Heartbeats | Rep. | Outcome | Valid | Writes before / in the "
            "window | Verified receipts | Late writes | Largest lateness (s) | Overtaking | "
            "Mismatches | Other activity |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for replay in facts["replays"]:
        lines.append(
            f"| {replay['run_id']} | {replay['replayed_run']} | {replay['heartbeats']} "
            f"| {replay['repetition']} | {replay['outcome']} | {_shown(replay['valid'])} "
            f"| {_shown(replay['writes_before_the_window'])} / "
            f"{_shown(replay['writes_in_the_window'])} "
            f"| {_shown(replay['verified_receipts'])} | {replay['late_writes']} "
            f"| {_shown(replay['largest_lateness_seconds'])} "
            f"| {replay['overtaking_writes']} | {_shown(replay['mismatches'])} "
            f"| {replay['other_activity']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_results(
    store_folder: Path, *, store: str, entries: Sequence[Mapping[str, Any]]
) -> tuple[Path, Path]:
    """Write the results page of `store_folder` as `results.json` and `results.md`; their
    paths."""
    facts = results_facts(store_folder, store=store, entries=entries)
    as_json = store_folder / RESULTS_JSON
    as_markdown = store_folder / RESULTS_MARKDOWN
    as_json.write_text(
        json.dumps(facts, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    as_markdown.write_text(results_markdown(facts), encoding="utf-8")
    return as_json, as_markdown
