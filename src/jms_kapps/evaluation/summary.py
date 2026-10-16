"""What the table of a spec (`table`) and the ramp's broken rule (`ramp`) read of one record: its
run, whether it counts, and its measures, without the text of its store requests. A record holds
every store request with its text, so a folder of records is read one record at a time, and only
its summary is kept.

**Which runs count.** A run counts in the statistics when it completed and its record does not say
it is invalid. A run that a check found invalid, a run that failed, and a run that a stop signal
ended are left out, and the table lists each with why.

**The run kinds.** A run of use case 2 is of the run kind `uc2`, with the wrapper, or
`uc2-unwrapped`, without it. A store-only baseline (jms_kapps 81) is of the run kind `baseline`:
it replays the store requests of one recorded run of use case 2, so it belongs to that run's
configuration. Its record names the run it replays in its field `replayed_run`, by that run's
`run_id`.

**The measures read.** Each latency, rate and share comes from the measured window alone, as the
record's measures hold it: the latencies of the commits the store admitted; the latencies of the
store requests that the store accepted, logins left out, since a baseline replays none; the
completed writes per minute and the transports ended per minute; and the store requests, those
refused, those that timed out and those whose connection dropped, and the three together (refused
or unanswered). A request that a record written before jms_kapps 109 says "timed out" is
classified by its error (`store_requests.outcome_of`), so a dropped connection of such a record
counts as connection dropped; no record file is changed. The refusals for want of heap count under
the load: in the warm-up, the
window and the drain. Such a refusal is a signal of the store at that load, wherever in the load
it comes; one before the load's start, as at the participants' start, is a signal of the store's
size, not of the load. A baseline has neither a warm-up nor a drain, and the requests it sends
before its window are no load, so its window alone counts. A measure the record does not hold is
None: a run without the wrapper has no commit and no store request, and a baseline has no
transport. Then each box still waiting after the drain (stuck), each failed box, each with its
place and reason, each spawn the WarehouseManagementSystem refused, with its reason and where it
was known, and the completion matcher's reads.

(Added on 2026-10-12, jms_kapps 49.)
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jms_kapps.evaluation import store_requests
from jms_kapps.evaluation.bench import INTERRUPTED

# The outcome of a run that completed.
COMPLETED = "completed"


@dataclass(frozen=True)
class RunSummary:
    """What the table reads of one record."""

    run_id: str
    run_kind: str
    outcome: str
    valid: bool | None
    failed_checks: Sequence[Mapping[str, Any]] = ()
    key: str | None = None
    configuration: int | None = None
    repetition: int | None = None
    # The pass of the precision rule that made the run, as a record made before the rounds says
    # (jms_kapps 101, interim name), or None.
    pass_number: int | None = None
    # The round of the precision rule that made the run, as its record says (jms_kapps 111,
    # interim name), or None for a run of fixed repetitions and a record made before the rounds.
    round_number: int | None = None
    replays: str | None = None
    # The file name of the spec the sweep named, and the transit time and the retry wait that the
    # record's `run_settings` hold, or None where it says none: a record of the "before" series
    # holds them there and not in its key (jms_kapps 105, interim names).
    spec_file: str | None = None
    transit_seconds: float | None = None
    retry_seconds: float | None = None
    # The numbers of a baseline's replay level (`replay_levels.level_record`), or None for a run
    # of use case 2 and for a baseline of an earlier record (jms_kapps 98, interim name).
    replay_level: Mapping[str, Any] | None = None
    commit_latencies: Sequence[float] | None = None
    store_request_latencies: Sequence[float] | None = None
    completed_writes_per_minute: float | None = None
    transports_ended_per_minute: float | None = None
    store_requests_in_window: int | None = None
    # The store requests of the window that the store refused, that timed out and whose
    # connection dropped, and the three together (jms_kapps 109, interim names; until then one
    # count, `refused_or_timed_out_in_window`, for the same sum).
    refused_in_window: int | None = None
    timed_out_in_window: int | None = None
    connection_dropped_in_window: int | None = None
    refused_or_unanswered_in_window: int | None = None
    # The spawns offered and accepted in the window, from the WarehouseManagementSystem's time-
    # stamped lines (jms_kapps 99, interim names), and the window's length, or None in a record
    # made before.
    spawns_offered_in_window: int | None = None
    spawns_accepted_in_window: int | None = None
    window_minutes: float | None = None
    heap_refusals_under_load: int | None = None
    # The CPU cores that the server's license view gives (`maxCpuCores`), as the record keeps the
    # view, or None where it has none, the read failed or the answer names no maximum (jms_kapps
    # 108, interim name).
    licensed_cores: int | None = None
    stuck: Sequence[Mapping[str, Any]] | None = None
    failed: Sequence[Mapping[str, Any]] | None = None
    refused_spawns: Sequence[Mapping[str, Any]] | None = None
    matcher: Mapping[str, Any] | None = field(default=None)

    @property
    def replay_factor(self) -> float | None:
        """The factor of the replay that a baseline's record says, or None where it says none:
        a run of use case 2, and a baseline made before the factor (a replay at 1)."""
        factor = None if self.replay_level is None else self.replay_level.get("factor")
        return None if factor is None else float(factor)

    @property
    def counts(self) -> bool:
        """Whether the run counts in the statistics: it completed, and is not invalid."""
        return self.outcome == COMPLETED and self.valid is not False

    def why_left_out(self) -> str | None:
        """Why the run is left out of the statistics, or None where it counts."""
        if self.outcome == INTERRUPTED:
            return (
                "interrupted: a stop signal ended it, and a later call makes it again"
            )
        if self.outcome != COMPLETED:
            return self.outcome
        if self.valid is False:
            checks = sorted({str(check.get("check")) for check in self.failed_checks})
            return f"invalid: it failed the checks {', '.join(checks)}"
        return None


def summary_of(record: Mapping[str, Any]) -> RunSummary:
    """The summary of `record`, a record as a run of the harness writes it."""
    sweep = record.get("sweep") or {}
    settings = record.get("run_settings")
    settings = settings if isinstance(settings, Mapping) else {}
    spec_named = sweep.get("spec")
    measures = record.get("measures") or {}
    transports = record.get("transports") or {}
    spawns = record.get("spawns") or {}
    matcher = record.get("matcher")
    requests = measures.get("store_requests")
    counts = _window_counts(measures)
    return RunSummary(
        run_id=str(record["run_id"]),
        run_kind=str(record["run_kind"]),
        outcome=str(record.get("outcome")),
        valid=record.get("valid"),
        failed_checks=list(record.get("failed_checks") or []),
        key=sweep.get("key"),
        configuration=sweep.get("configuration"),
        repetition=sweep.get("repetition"),
        pass_number=sweep.get("pass"),
        round_number=sweep.get("round"),
        replays=_replayed_run(record),
        spec_file=None if spec_named is None else Path(str(spec_named)).name,
        transit_seconds=settings.get("transit_seconds"),
        retry_seconds=settings.get("retry_seconds"),
        replay_level=_replay_level(record, counts),
        commit_latencies=_floats(measures.get("commit_latencies_seconds")),
        store_request_latencies=None
        if requests is None
        else [
            float(request["seconds"])
            for request in requests
            if request.get("window")
            and request.get("outcome") == store_requests.ACCEPTED
            and request.get("kind") != store_requests.LOGIN
        ],
        completed_writes_per_minute=measures.get("completed_writes_per_minute"),
        transports_ended_per_minute=measures.get("transports_ended_per_minute"),
        store_requests_in_window=measures.get("store_requests_in_window"),
        refused_in_window=None if counts is None else counts[0],
        timed_out_in_window=None if counts is None else counts[1],
        connection_dropped_in_window=None if counts is None else counts[2],
        refused_or_unanswered_in_window=None if counts is None else sum(counts),
        spawns_offered_in_window=measures.get("spawns_offered_in_window"),
        spawns_accepted_in_window=measures.get("spawns_accepted_in_window"),
        window_minutes=(measures.get("window") or {}).get("minutes"),
        heap_refusals_under_load=_heap_refusals_under_load(record, measures),
        licensed_cores=_licensed_cores(record),
        stuck=record.get("stuck"),
        failed=transports.get("failed"),
        refused_spawns=spawns.get("refused"),
        matcher=None
        if not matcher
        else {
            key: matcher.get(key)
            for key in ("interval_seconds", "read_count", "read_seconds")
        },
    )


def repetition_of(summary: RunSummary) -> int:
    """The repetition that the record of `summary` names, the first where it names none: a record
    made before the repetitions of a ramp or a replay (jms_kapps 114, interim name)."""
    return summary.repetition or 1


def repetitions_made(
    summaries: Iterable[RunSummary],
    *,
    run_kind: str,
    keys: Collection[str] | None = None,
) -> set[int]:
    """The repetitions (`repetition_of`) that the records among `summaries` of `run_kind`, and of a
    key among `keys` where it is given, show made: a record that a stop signal ended is none
    (jms_kapps 114, interim name)."""
    return {
        repetition_of(summary)
        for summary in summaries
        if summary.run_kind == run_kind
        and (keys is None or summary.key in keys)
        and summary.outcome != INTERRUPTED
    }


def read_summaries(runs: Path) -> list[RunSummary]:
    """The summary of each record below `runs`, one folder each, in the order of their folders.
    A record that is no JSON, or holds no run id or run kind, is none of them."""
    found = []
    for path in sorted(runs.glob("*/record.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            found.append(summary_of(record))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return found


def _heap_refusals_under_load(
    record: Mapping[str, Any], measures: Mapping[str, Any]
) -> int | None:
    """The refusals for want of heap under the load: in the warm-up, in the window and in the
    drain, from the window's end on. None where the measures hold none. A baseline has neither a
    warm-up nor a drain, and the requests it sends before its window are no load: its window
    alone counts."""
    refusals = measures.get("heap_refusals")
    if refusals is None:
        return None
    window = record.get("window") or measures.get("window") or {}
    warm_up = record.get("warm_up") or {}
    window_end = window.get("end")
    warm_up_start = warm_up.get("start")

    def under_load(refusal: Mapping[str, Any]) -> bool:
        if refusal.get("window"):
            return True
        at = datetime.fromisoformat(refusal["at"])
        if window_end is not None and at >= datetime.fromisoformat(window_end):
            return True
        return warm_up_start is not None and at >= datetime.fromisoformat(warm_up_start)

    return sum(1 for refusal in refusals if under_load(refusal))


def _window_counts(measures: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """The store requests of the window that the store refused, that timed out and whose
    connection dropped (jms_kapps 109), as the measures count them; None where they hold no
    count. A record written before counts a dropped connection as timed out and holds no
    `connection_dropped_in_window`: of its timeouts, those that its store requests show to be a
    dropped connection by their error (`store_requests.outcome_of`) count as connection dropped,
    so that the sum stays the one the record holds."""
    refused = measures.get("refused_in_window")
    timed_out = measures.get("timed_out_in_window")
    if refused is None or timed_out is None:
        return None
    dropped = measures.get("connection_dropped_in_window")
    if dropped is not None:
        return refused, timed_out, dropped
    dropped = sum(
        1
        for request in measures.get("store_requests") or ()
        if request.get("window")
        and request.get("outcome") == store_requests.TIMED_OUT
        and store_requests.outcome_of(request) == store_requests.CONNECTION_DROPPED
    )
    dropped = min(dropped, timed_out)
    return refused, timed_out - dropped, dropped


def _replay_level(
    record: Mapping[str, Any], counts: tuple[int, int, int] | None
) -> Mapping[str, Any] | None:
    """The replay level that a baseline's record holds, or None. A reader takes it as the record
    would say it now (jms_kapps 109): the timeouts and the dropped connections of its window
    apart, from `counts`, and each signal by its name now (`ramp.signal_name`); the record file
    keeps what it holds."""
    # Imported here: `ramp` reads the summaries of this module.
    from jms_kapps.evaluation.ramp import signal_name

    level = record.get("replay_level")
    if not isinstance(level, Mapping):
        return None
    shown = dict(level)
    if counts is not None:
        shown["timed_out_in_window"] = counts[1]
        shown["connection_dropped_in_window"] = counts[2]
    if isinstance(level.get("signals"), list):
        shown["signals"] = [
            {**signal, "signal": signal_name(str(signal.get("signal")))}
            if isinstance(signal, Mapping)
            else signal
            for signal in level["signals"]
        ]
    return shown


def _licensed_cores(record: Mapping[str, Any]) -> int | None:
    """The licensed CPU cores of the server's license view that `record` keeps whole
    (`server.license`, read once per run by the server watch): the answer's `maxCpuCores`. None
    where the record has no view, the read failed, or the answer names no maximum (jms_kapps
    108)."""
    server = record.get("server")
    view = server.get("license") if isinstance(server, Mapping) else None
    if not isinstance(view, Mapping) or view.get("status") != 200:
        return None
    answer = view.get("answer")
    cores = answer.get("maxCpuCores") if isinstance(answer, Mapping) else None
    return cores if isinstance(cores, int) and not isinstance(cores, bool) else None


def _replayed_run(record: Mapping[str, Any]) -> str | None:
    """The run id of the run that a baseline replays, or None."""
    replayed = record.get("replayed_run")
    run_id = replayed.get("run_id") if isinstance(replayed, Mapping) else None
    return None if run_id is None else str(run_id)


def _floats(values: Sequence[Any] | None) -> list[float] | None:
    return None if values is None else [float(value) for value in values]
