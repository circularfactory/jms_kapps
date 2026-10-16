"""`jms-eval-baseline`: the store-only baseline of one recorded UC2 run, on a repository of its own
of the configured GraphDB, with its record. Iteration one of the measurement harness.

    jms-eval-baseline <recorded run> [--runs <folder>] [--factor N]
                      [--heavy-run-approval <approval file> --spec <spec.toml>]

The baseline sends the store requests of a recorded run, whose record keeps their text (the
observation, part 2 of the harness), straight to the store: the same mix of writes and queries,
without KSM, the object-graph mapper or the triple-store interface on the way, at the same store
size, beside the network's round trip from the client host. It is a measurement store client, as
the seed is, and no participant: it starts none. Its numbers stand beside the UC2 run's (part 3).

A baseline run goes through these steps, in order, and records each action it takes with its time:

1. **Before anything** (`read_recorded_run`, `run_baseline`). It reads the recorded run's record,
   and refuses a run of the run kind `uc2-unwrapped`, which has no request text, any run kind but
   `uc2`, and a run that did not complete, which has no measures. It resolves the recorded run's
   configuration, refuses a prefill file whose digest is not the recorded one, and, if the
   recorded run was heavy, or the factor is above 1, refuses to start unless an approval covers it
   (`approval`, jms_kapps 96): the option `--heavy-run-approval` gives the approval file and
   `--spec` the spec, and the baseline runs only if the record of the replayed run holds a
   configuration key (a run made from a spec records it) that the approved spec lists, in the
   approval's period. A
   replayed run whose record holds no key is refused. A baseline sends the recorded load at its
   recorded rate, so it is as heavy as the run it replays, and at a factor above 1 heavier. The
   record keeps the whole approval.
2. **The facts before the run**, as part 1 keeps them: the client's facts with the network's round
   trip, the software's versions and commits, and the server's version, repositories and
   resource view (`record`, `store`).
3. **The store**, through part 1's store of a run: a repository of its own, `eval-` plus the run
   id, with the recorded run's SHACL setting (`store.create_run_repository`); then the reset and
   the seed of the recorded run's topology, its prefill file and its history collection, through
   the bench graph (`bench.GraphDBBenchGraph`, `seed`). This preparation uses the triple-store
   interface, and none of its requests is measured.
4. **The replay** (`replay`), which logs in with its own credentials and replays no recorded
   login: first the requests recorded before the window, in their order, each after the answer
   to the one before, not measured; then the server's views, and the window's requests, each at
   its recorded offset from the window's start, without waiting for an answer, until the
   window's end. The requests recorded after the window's end are not sent. Then it waits until
   each request of the window has its answer, or its error (the drain), and reads the server's
   views again. The measured window lasts as long as the recorded one.

   **Compressed in time** (`--factor`, jms_kapps 98, interim names, jms_kapps 83). With a factor
   above 1 the window's requests keep their order, and the offset of each, so each gap between
   two of them and the window itself, is divided by the factor: the same requests in a shorter
   time (`replay_levels`). A factor of 1, the default, is the replay as described above. Every
   factor above 1 is a heavy run and starts only under an approval that covers the replayed
   run's configuration (below). The record of a baseline holds `replay_level`: the factor, the
   offered and the achieved rate per minute, the refusals and the timeouts, and whether the level
   is broken, with the signals.
5. **The end, also after a failure or a stop signal.** It deletes its repository and no other
   (`bench.delete_run_repository`), and writes the record (`record.write_record`): the run kind
   `baseline`, the run it replays, and, for a completed run, its measures in the form of part
   2's (`observation`): every request it sent, those before the window too, as part 2 keeps
   them, so that a refusal among them shows; the window's alone count, each with its offset and
   how late it left. As for a UC2 run, by Sören's decision of 2026-10-14 as the product owner of
   the tests, the deletion is a fact of its own in the record (`repository_deletion`), and the
   outcome says nothing of it: a baseline or a replay level whose replay completed is
   `completed`, with its measures and its level, also where the store did not delete its
   repository; the last line names such a repository (changed on 2026-10-14, jms_kapps 116:
   until then "; the repository was not deleted" was added to the outcome, and such a baseline
   did not count).

**The run limit** (added on 2026-10-13, jms_kapps 106; interim names, jms_kapps 83). A baseline
run, at every factor, has the run limit of a UC2 run (`run_limit`): 30 minutes by default, a run
setting that the record keeps as `run_settings.run_limit_seconds`. The time counts from the facts
of step 2 to the start of step 5. A baseline older than its limit ends through step 5, as a stop
signal ends it, and its outcome is `failed (over time)`, with the limit and the step at which the
limit came: the facts before the run, the store or the replay.

**Where KSM, the object-graph mapper and the interface are.** The replay's own module (`replay`)
sends every replayed request with its own HTTP client, and imports none of them. This module
prepares the store through the bench graph, which uses the interface, as the ticket allows, and
reads the store's answers with part 2's readers (`store_requests`), so that a baseline's request
reads as a UC2 run's does; they read text only, and send nothing.

Every request a replay sends goes to the baseline's own repository, whatever repository the
recording names, through part 1's name guard. A recorded request's media type is not in its
record, so the baseline sends each as the triple-store interface sends it: a POST to a
repository as a SPARQL query, and a POST to its statements as a SPARQL update, each accepting
SPARQL's JSON results; any other request goes with its method and its text alone. A UC2 run's
participants reach the store with these two and their login only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jms_kapps.evaluation import store_requests
from jms_kapps.evaluation.approval import (
    APPROVAL_FORM,
    Covered,
    HeavyRunRefused,
    cover,
    covered_run,
    load_approval,
)
from jms_kapps.evaluation.bench import (
    INTERRUPTED,
    RETRY_SECONDS,
    STATEMENTS_AFTER_THE_PARTICIPANTS,
    STATEMENTS_BEFORE_THE_PARTICIPANTS,
    TRANSIT_SECONDS,
    Actions,
    Clock,
    SystemClock,
    delete_run_repository,
    new_run_id,
    outcome_line,
    read_statement_counts,
    server_facts_before_the_run,
    server_views,
)
from jms_kapps.evaluation.client_load import (
    CLIENT_LOAD_INTERVAL_SECONDS,
    ClientLoadSampler,
)
from jms_kapps.evaluation.configuration import (
    Configuration,
    ConfigurationRefused,
    resolve,
)
from jms_kapps.evaluation.observation import Window, request_measures
from jms_kapps.evaluation.record import (
    ITERATION,
    RUN_KIND,
    UNWRAPPED_RUN_KIND,
    client_facts,
    prefill_facts,
    software_facts,
    write_record,
)
from jms_kapps.evaluation.replay import (
    REQUEST_TIMEOUT_SECONDS,
    Replay,
    Request,
    Sent,
    missing_store_variables,
    store_login,
)
from jms_kapps.evaluation.replay_levels import (
    RECORD_KEY,
    compressed,
    is_heavy,
    level_record,
    why_heavy,
)
from jms_kapps.evaluation.run_limit import RUN_LIMIT_SECONDS, RunLimit, run_and_end
from jms_kapps.evaluation.run_uc2 import graph_for
from jms_kapps.evaluation.seed import PREFILL_TRANSACTION_STATEMENTS
from jms_kapps.evaluation.spec import valid_factor
from jms_kapps.evaluation.store import (
    GraphDBServer,
    create_run_repository,
    public_url,
    repository_name,
)
from jms_kapps.evaluation.watch import WATCH_INTERVAL_SECONDS, ServerWatch, WatchedClock
from jms_kapps.uc2.run_core import RunGraph
from jms_kapps.uc2.strategies import ModuleStrategy, WmsStrategy
from jms_kapps.uc2.topology import load_topology

PROGRAM = "jms-eval-baseline"

# The run kind of a baseline's record.
BASELINE_RUN_KIND = "baseline"

# What a replayed query or update accepts as its answer, as the triple-store interface asks.
SPARQL_RESULTS = "application/sparql-results+json"


class BaselineRefused(RuntimeError):
    """A recorded run that a baseline cannot replay: why, for a person to read. Nothing reached a
    store."""


@dataclass(frozen=True)
class RecordedRequest:
    """One recorded store request that a baseline sends again: as the record holds it, and as the
    replay sends it."""

    recorded: Mapping[str, Any]
    request: Request


@dataclass(frozen=True)
class RecordedRun:
    """A recorded UC2 run, read and checked, ready to replay.

    Args:
        path: Its record.
        record: The record, as written.
        configuration: Its configuration, resolved.
        prefill: The facts of its prefill file, as a record holds them, or None for a run
            without one (`record.prefill_facts`).
        before: Its requests recorded before the window, logins left out, in their order.
        window: Its requests recorded in the window, logins left out, each with its offset.
        logins: How many recorded logins are left out.
        after: How many requests recorded after the window's end are left out.
    """

    path: Path
    record: Mapping[str, Any]
    configuration: Configuration
    prefill: dict[str, Any] | None
    before: tuple[RecordedRequest, ...]
    window: tuple[RecordedRequest, ...]
    logins: int
    after: int

    @property
    def window_seconds(self) -> float:
        """How long the recorded window lasted, in seconds."""
        start, end = _recorded_window(self.record)
        return (end - start).total_seconds()


def read_recorded_run(
    path: Path,
    *,
    module_strategies: Mapping[str, ModuleStrategy] | None = None,
    wms_strategies: Mapping[str, WmsStrategy] | None = None,
) -> RecordedRun:
    """The recorded run at `path`, its folder or its `record.json`, read and checked.

    Raises:
        BaselineRefused: If the record cannot be read; if its run kind is `uc2-unwrapped`, which
            has no request text, or anything but `uc2`; if the run did not complete, so that its
            record has no store requests; or if its prefill file is not the one the run loaded.
        ConfigurationRefused: If its configuration does not resolve, as where a file it names is
            gone (`configuration.resolve`).
    """
    record_path = path / "record.json" if path.is_dir() else path
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BaselineRefused(
            f"the record {str(record_path)!r} cannot be read: {error}"
        ) from None
    if not isinstance(record, dict):
        raise BaselineRefused(f"{str(record_path)!r} holds no record of a run")
    kind = record.get("run_kind")
    if kind == UNWRAPPED_RUN_KIND:
        raise BaselineRefused(
            f"the run {record.get('run_id')} has the run kind {UNWRAPPED_RUN_KIND}: it ran "
            "without the wrapper, so its record holds no text of its store requests to send "
            "again. A baseline replays a run made with wrapper = true"
        )
    if kind != RUN_KIND:
        raise BaselineRefused(
            f"a baseline replays a run of the run kind {RUN_KIND}, and "
            f"{str(record_path)!r} has the run kind {kind!r}"
        )
    measures = record.get("measures")
    requests = measures.get("store_requests") if isinstance(measures, dict) else None
    if record.get("outcome") != "completed" or not isinstance(requests, list):
        raise BaselineRefused(
            f"the run {record.get('run_id')} did not complete ({record.get('outcome')}), so "
            "its record has no store requests to send again"
        )
    try:
        values = tomllib.loads(str(record["configuration"]))
        start, end = _recorded_window(record)
    except (KeyError, TypeError, ValueError) as error:
        raise BaselineRefused(
            f"the record {str(record_path)!r} has no configuration or no window to replay: "
            f"{type(error).__name__}: {error}"
        ) from None
    configuration = resolve(
        _with_the_run_settings_of_the_before_series(values, record),
        base=record_path.resolve().parent,
        module_strategies=module_strategies,
        wms_strategies=wms_strategies,
    )
    prefill = _checked_prefill(configuration, record)
    before: list[RecordedRequest] = []
    window: list[RecordedRequest] = []
    logins = after = 0
    for recorded in sorted(requests, key=lambda request: str(request.get("at"))):
        if (
            recorded.get("kind") == store_requests.LOGIN
            or str(recorded.get("path")).strip("/") == "rest/login"
        ):
            logins += 1
            continue
        at = datetime.fromisoformat(recorded["at"])
        if at >= end:
            after += 1
        elif at < start:
            before.append(_recorded_request(recorded, offset=None))
        else:
            offset = (at - start).total_seconds()
            window.append(_recorded_request(recorded, offset=offset))
    return RecordedRun(
        path=record_path,
        record=record,
        configuration=configuration,
        prefill=prefill,
        before=tuple(before),
        window=tuple(window),
        logins=logins,
        after=after,
    )


def _with_the_run_settings_of_the_before_series(
    values: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, Any]:
    """The configuration `values` of a record, with the transit time and the retry wait it lacks.
    A record of the "before" series holds them under `run_settings` and not in its configuration
    (jms_kapps 93), so they are read from there, and, where a record says neither, from the values
    every run of that series took (`bench.TRANSIT_SECONDS`, `bench.RETRY_SECONDS`). A record of the
    "after" series holds them in its configuration, which is left as it is."""
    settings = record.get("run_settings")
    settings = settings if isinstance(settings, Mapping) else {}
    return {
        "transit_seconds": settings.get("transit_seconds", TRANSIT_SECONDS),
        "retry_seconds": settings.get("retry_seconds", RETRY_SECONDS),
        **values,
    }


def _recorded_window(record: Mapping[str, Any]) -> tuple[datetime, datetime]:
    """The start and the end of the measured window of the recorded run `record`."""
    window = record["window"]
    return datetime.fromisoformat(window["start"]), datetime.fromisoformat(
        window["end"]
    )


def _checked_prefill(
    configuration: Configuration, record: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The facts of the recorded run's prefill file, once it is the one the run loaded, by its
    digest: a baseline meets the store at the recorded run's size.

    Raises:
        BaselineRefused: If the file's digest is not the one in the record.
    """
    found = prefill_facts(configuration.prefill)
    recorded = record.get("prefill")
    if (
        found is not None
        and isinstance(recorded, dict)
        and found["sha256"] != recorded.get("sha256")
    ):
        raise BaselineRefused(
            f"the prefill file {str(configuration.prefill)!r} is not the one the run "
            f"{record.get('run_id')} loaded: its digest is {found['sha256']}, the record's "
            f"{recorded.get('sha256')}"
        )
    return found


def _recorded_request(
    recorded: Mapping[str, Any], *, offset: float | None
) -> RecordedRequest:
    """`recorded`, a store request as part 2's record holds it, as the replay sends it again."""
    method = str(recorded.get("method") or "")
    path = str(recorded.get("path") or "")
    if not method or not path:
        raise BaselineRefused(
            f"the store request recorded at {recorded.get('at')} has no method or no path"
        )
    media_type, accept = as_the_interface_sends(method, path)
    text = recorded.get("text")
    return RecordedRequest(
        recorded=recorded,
        request=Request(
            method=method,
            path=path,
            body=text,
            media_type=media_type,
            accept=accept,
            offset_seconds=offset,
            keep_answer=store_requests.receipt_asked(text) is not None,
        ),
    )


def as_the_interface_sends(method: str, path: str) -> tuple[str | None, str | None]:
    """The media type and the accepted answer of the request `method` `path`, as the
    triple-store interface sends it: a POST to a repository is a SPARQL query, and a POST to its
    statements a SPARQL update, each accepting SPARQL's JSON results. None for anything else."""
    parts = path.strip("/").split("/")
    if method.upper() != "POST" or parts[0] != "repositories" or len(parts) < 2:
        return None, None
    if len(parts) == 2:
        return store_requests.SPARQL_QUERY, SPARQL_RESULTS
    if parts[2:] == ["statements"]:
        return store_requests.SPARQL_UPDATE, SPARQL_RESULTS
    return None, None


# What prepares the baseline's repository: its graph by the repository's name, with the prefill
# file it loads and whether its reset sets the shapes (`run_uc2.graph_for`).
GraphFor = Callable[[str, Path | None, bool], RunGraph]

# What sends the requests to the baseline's repository: the replay, by the repository's name and
# the clock.
ReplayFor = Callable[[str, Clock], Replay]


def run_baseline(
    recorded: RecordedRun,
    *,
    runs: Path,
    server: GraphDBServer,
    graph_for: GraphFor,
    replay_for: ReplayFor,
    clock: Clock | None = None,
    approval: Covered | None = None,
    factor: float = 1.0,
    sweep: Mapping[str, Any] | None = None,
    echo: Callable[[str], None] = print,
    watch_interval_seconds: float = WATCH_INTERVAL_SECONDS,
    run_limit_seconds: float = RUN_LIMIT_SECONDS,
) -> dict[str, Any]:
    """Make one baseline run of `recorded`, on a repository of its own, and write its record.
    Returns the record, which is in `record.json` in the run's folder too.

    Args:
        runs: The folder in which the run makes its own, named by its run id.
        server: The configured GraphDB's REST API.
        graph_for: Prepares the run's repository: part 1's bench graph.
        replay_for: The replay of the run's repository.
        clock: The run's time: this machine's if None.
        approval: The covered record (`approval.cover`) of a heavy replay, or None where it is
            off. A heavy replay takes nothing else: the run checks that it names the recorded
            run's configuration, and its period at the run's start. A replay is heavy where the
            recorded run was, and at every factor above 1.
        factor: The replay's factor (`replay_levels`): the window's offsets and length are
            divided by it. 1 is the recorded timeline.
        sweep: What the record says of the sweep that made the run (the spec, the level's key, the
            factor), or None for a run of the command.
        echo: Where the run's actions are printed.
        watch_interval_seconds: The seconds from one sample of the server watch to the next: a
            run setting (`watch`). The watch runs from the window's start to the drain's end, as
            a UC2 run's runs from the load's start to the drain's end.
        run_limit_seconds: The limit of the whole run, in seconds of `clock`, as a UC2 run's: a
            run setting (`run_limit`). A run older than it ends as failed (over time), at every
            factor.

    Raises:
        BaselineRefused: If `factor` is not a number from 1.
        HeavyRunRefused: If the recorded run was heavy or the factor is above 1, and `approval` is
            not the covered record of its configuration, or its period is not the run's start.
            Nothing is created, and no record is written.
    """
    if not valid_factor(factor):
        raise BaselineRefused(
            f"a replay factor is a number from 1, not {factor!r}: below 1 the replay would be "
            "slower than the recorded run"
        )
    configuration = recorded.configuration
    clock = clock if clock is not None else SystemClock()
    approval_record = covered_run(
        configuration.heavy or is_heavy(factor),
        approval,
        configuration,
        now=clock.now(),
        why=why_heavy(configuration.heavy, factor),
    )
    # The replay's waits take the server watch's samples (`watch`).
    waiting = WatchedClock(clock)
    watch = ServerWatch(server, interval=watch_interval_seconds, clock=clock)
    waiting.watch = watch
    run_id = new_run_id(clock.now())
    repository = repository_name(run_id)
    folder = runs / run_id
    folder.mkdir(parents=True, exist_ok=True)
    actions = Actions(clock, echo)
    replayed = recorded.record
    transit_seconds = configuration.transit_seconds
    record: dict[str, Any] = {
        "run_kind": BASELINE_RUN_KIND,
        "iteration": ITERATION,
        "run_id": run_id,
        "repository": repository,
        "replayed_run": {
            "run_id": replayed.get("run_id"),
            "record": str(recorded.path),
            "repository": replayed.get("repository"),
            "window": replayed.get("window"),
            "valid": replayed.get("valid"),
        },
        "outcome": None,
        "configuration": replayed.get("configuration"),
        "heavy_run_approval": approval_record,
        "sweep": None if sweep is None else dict(sweep),
        "actions": actions.taken,
        "window": None,
        "run_settings": {
            "server_watch_interval_seconds": watch_interval_seconds,
            "transit_seconds": transit_seconds,
            "prefill_transaction_statements": PREFILL_TRANSACTION_STATEMENTS,
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "client_load_interval_seconds": CLIENT_LOAD_INTERVAL_SECONDS,
            # The limit of the whole run (`run_limit`; jms_kapps 106, interim).
            "run_limit_seconds": run_limit_seconds,
        },
        "prefill": None,
        "client": None,
        "client_load": None,
        "software": None,
        "server": {"url": public_url(server.url)},
        "replay": None,
        RECORD_KEY: None,
        "measures": None,
        "repository_deletion": None,
    }
    actions.add("run began", run_id=run_id, repository=repository)
    replay: Replay | None = None
    client_load = ClientLoadSampler(clock, interval=CLIENT_LOAD_INTERVAL_SECONDS)
    limit = RunLimit(run_limit_seconds, clock)

    def steps() -> None:
        """Steps 2 to 4, each marked on the run limit."""
        nonlocal replay
        topology = load_topology(configuration.topology)
        record["prefill"] = recorded.prefill
        record["client"] = client_facts(server.round_trip_ms())
        record["software"] = software_facts()
        record["server"] |= server_facts_before_the_run(server)

        limit.at("the store")
        record["server"]["repository_config"] = create_run_repository(
            server, repository, shacl=configuration.shacl
        )
        actions.add("repository created", shacl=configuration.shacl)
        graph = graph_for(repository, configuration.prefill, configuration.shacl)
        graph.seed(
            topology,
            transit_seconds,
            collect_history=configuration.collect_history,
        )
        actions.add("store prepared")
        read_statement_counts(
            record, STATEMENTS_BEFORE_THE_PARTICIPANTS, repository, server, actions
        )

        limit.at("the replay")
        replay = replay_for(repository, waiting)
        replay.log_in()
        client_load.start()
        before = replay.send_in_order([r.request for r in recorded.before])
        actions.add("requests before the window sent", requests=len(before))
        record["server"] |= server_views(server, repository, "window_start")
        actions.add("window began", requests=len(recorded.window))
        watch.start()
        window_seconds = recorded.window_seconds / factor
        window = replay.send_at_offsets(
            compressed([r.request for r in recorded.window], factor),
            window_seconds,
        )
        # The measured window lasts as long as the recorded one, shortened by the factor,
        # also where the replay sent its last request later: its lateness says so.
        measured = Window(
            window.start,
            window.start + timedelta(seconds=window_seconds),
        )
        record["window"] = {
            "start": measured.start.isoformat(),
            "end": measured.end.isoformat(),
        }
        client_load.stop()
        actions.add("window ended")
        answered = window.answers()
        watch.stop()
        actions.add("drain ended")
        record["server"] |= server_views(
            server, repository, "drain_end", every_repository=True
        )
        # The replay has stopped writing: no transaction of its own is open (GDB-12163).
        read_statement_counts(
            record, STATEMENTS_AFTER_THE_PARTICIPANTS, repository, server, actions
        )
        record["replay"], record["measures"] = _measures(
            recorded, before, answered, measured
        )
        record[RECORD_KEY] = level_record(
            factor=factor,
            recorded_window_seconds=recorded.window_seconds,
            window_start=measured.start,
            window=answered,
            refused=record["measures"]["refused_in_window"],
            timed_out=record["measures"]["timed_out_in_window"],
            connection_dropped=record["measures"]["connection_dropped_in_window"],
            heap_refusals=record["measures"]["heap_refusals_in_window"],
        )

    def end() -> None:
        """Step 5, also after a failure, a stop signal or the limit."""
        watch.stop()
        record["server"]["watch"] = watch.record()
        client_load.stop()
        if client_load.started:
            record["client_load"] = client_load.record()
        try:
            if replay is not None:
                replay.close()
            delete_run_repository(record, repository, server, actions)
            actions.add("run ended", outcome=record["outcome"])
        finally:
            write_record(folder, record)

    # The first stop signal ends the run as interrupted, and the run limit as over time, unless
    # the run is ending already; no later one cuts the end short (`run_limit.run_and_end`).
    run_and_end(
        record,
        limit,
        steps,
        end,
        first_step="the facts before the run",
        interrupted=INTERRUPTED,
        add_action=actions.add,
    )
    return record


def _measures(
    recorded: RecordedRun,
    before: Sequence[Sent],
    answered: Sequence[Sent],
    window: Window,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the replay sent, and its measures in the form of part 2's: every request it sent,
    in the order it sent them, with when it left, its latency, its outcome and the store's reason
    of a refusal, the statuses it writes and whether the store applied them, its text, whether
    it is one of the window's, and for these its offset and how late it left; and over the
    window alone, the counts, the share, the rates and the refusals for want of heap
    (`request_measures`). The requests before the window are kept, as part 2 keeps those of the
    participants' start and of the warm-up, so that a refusal among them shows; none of them
    counts."""
    sent = [
        *(
            (request, answer, False)
            for request, answer in zip(recorded.before, before, strict=True)
        ),
        *(
            (request, answer, True)
            for request, answer in zip(recorded.window, answered, strict=True)
        ),
    ]
    found: dict[tuple[str, str], bool | None] = {}
    for request, answer, _ in sent:
        asked = store_requests.receipt_asked(request.request.body)
        if asked and _outcome(answer)[0] == store_requests.ACCEPTED:
            found[(str(request.recorded.get("participant")), asked)] = (
                store_requests.receipt_found(answer.answer or "")
            )
    requests = [
        _request_record(request, answer, found, in_window=in_window)
        for request, answer, in_window in sent
    ]
    replay = {
        "recorded_requests": len(recorded.before)
        + len(recorded.window)
        + recorded.logins
        + recorded.after,
        "logins_left_out": recorded.logins,
        "before_the_window": len(before),
        "in_the_window": len(answered),
        "after_the_window_left_out": recorded.after,
    }
    measures = (
        {"window": window.record()}
        | request_measures(window, requests)
        | {"store_requests": requests}
    )
    return replay, measures


def _outcome(sent: Sent) -> tuple[str, str | None, str | None]:
    """The outcome of a replayed request, as part 2 reads one (`store_requests.answer_of`):
    accepted, or refused with the kind of the refusal and the store's reason; where it got no
    answer, timed out or connection dropped by its error (`store_requests.unanswered_outcome`,
    jms_kapps 109), with the error."""
    if sent.status_code is None:
        return store_requests.unanswered_outcome(sent.error), None, sent.error
    return store_requests.answer_of(
        sent.status_code, sent.content_type, sent.answer or ""
    )


def _request_record(
    request: RecordedRequest,
    sent: Sent,
    found: Mapping[tuple[str, str], bool | None],
    *,
    in_window: bool,
) -> dict[str, Any]:
    """One request the replay sent, as part 2's measures hold a store request, with its offset
    and how late it left; both None for one before the window."""
    recorded = request.recorded
    participant = str(recorded.get("participant"))
    kind = str(recorded.get("kind", store_requests.OTHER))
    outcome, refusal, reason = _outcome(sent)
    receipt = store_requests.receipt_written(request.request.body)
    body = request.request.body
    return {
        "participant": participant,
        "at": sent.at.isoformat(),
        "seconds": round(sent.seconds, 6),
        "method": request.request.method,
        "path": sent.path,
        "kind": kind,
        "request_bytes": len(body.encode("utf-8")) if body is not None else 0,
        "response_bytes": sent.response_bytes,
        "outcome": outcome,
        "refusal": refusal,
        "reason": reason,
        "writes": list(recorded.get("writes") or []),
        "applied": store_requests.applied(
            kind,
            outcome,
            receipt,
            found.get((participant, receipt)) if receipt else None,
        ),
        "text": body,
        "window": in_window,
        "offset_seconds": sent.request.offset_seconds,
        "late_seconds": None
        if sent.late_seconds is None
        else round(sent.late_seconds, 6),
    }


def run_on_the_configured_store(
    recorded: RecordedRun,
    *,
    runs: Path,
    approval: Covered | None = None,
    factor: float = 1.0,
    sweep: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One baseline run of `recorded` (`run_baseline`) on the configured GraphDB, with clients of
    its own and the login of the environment, and its record, in a folder of its own below
    `runs`."""
    # One line for each HTTP request would bury the run's own lines; the record keeps every
    # request.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    login = store_login()
    server = GraphDBServer(login.url, username=login.username, password=login.password)
    try:
        return run_baseline(
            recorded,
            runs=runs,
            server=server,
            graph_for=graph_for,
            replay_for=lambda repository, clock: Replay(
                login, repository=repository, clock=clock
            ),
            approval=approval,
            factor=factor,
            sweep=sweep,
        )
    finally:
        server.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="The store-only baseline of one recorded UC2 run: its store requests sent "
        "again straight to the store, on a repository of its own of the configured GraphDB, "
        "with its record (the measurement harness, iteration one).",
    )
    parser.add_argument(
        "recorded",
        type=Path,
        help="The recorded UC2 run: its folder, or its record.json. It ran with the wrapper",
    )
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("runs"),
        help="The folder in which the baseline makes its own folder (default: runs)",
    )
    parser.add_argument(
        "--factor",
        type=float,
        default=1.0,
        help="Replay the run compressed in time by this factor: the same requests in the same "
        "order, each offset from the window's start divided by it (default: 1, the recorded "
        "timeline). A factor above 1 is a heavy run, and starts only under the approval of the "
        "replayed run's configuration",
    )
    parser.add_argument(
        "--heavy-run-approval",
        metavar="TEXT",
        default=None,
        type=Path,
        help="The approval of the baseline of a heavy run (heavy = true) or of a factor above 1, "
        "which any person may give, in agreement with the server's admins, for one spec and one period: "
        f"{APPROVAL_FORM}. It covers the run only if the run's record holds a configuration key "
        "of the spec given with --spec. Off by default; the record keeps it",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="The approved spec, for the baseline of a heavy run: the approval covers the "
        "configuration keys of this spec as written",
    )
    args = parser.parse_args(argv)
    if not valid_factor(args.factor):
        print(
            f"{PROGRAM}: --factor is a number from 1, not {args.factor!r}",
            file=sys.stderr,
        )
        return 2
    try:
        recorded = read_recorded_run(args.recorded)
        approval = load_approval(args.heavy_run_approval)
        covered = None
        if recorded.configuration.heavy or is_heavy(args.factor):
            key = (recorded.record.get("sweep") or {}).get("key")
            # The configuration is the one in the record's text: it must be the one its stored
            # key names in the approved spec.
            covered = cover(
                approval,
                spec=args.spec,
                key=None if key is None else str(key),
                configuration=recorded.configuration,
                why=why_heavy(recorded.configuration.heavy, args.factor),
            )
    except (BaselineRefused, ConfigurationRefused, HeavyRunRefused) as refused:
        print(f"{PROGRAM}: {refused}", file=sys.stderr)
        return 2
    missing = missing_store_variables()
    if missing:
        print(
            f"{PROGRAM}: no GraphDB is configured: set {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    record = run_on_the_configured_store(
        recorded, runs=args.runs, approval=covered, factor=args.factor
    )
    print(outcome_line(PROGRAM, record, args.runs))
    return 0 if record["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
