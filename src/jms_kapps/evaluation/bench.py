"""The bench launcher: one UC2 run of one configuration, on its own repository, with its record.

A run goes through these steps, in order, and records each action it takes with its time:

1. **Before anything.** A heavy run without an approval that covers it, now, is refused
   (`approval`); the record keeps the whole approval. The run takes
   its id, and its folder below the runs folder.
2. **The facts before the run.** The client's facts, the network's round trip, the software's
   versions and commits, and the server's version, repositories, resource view, license, and the
   query and transaction view of every repository, the protected ones included (`record`,
   `store`; the counters added on 2026-10-12, jms_kapps 95). This is the run's start: the moment
   before the harness creates its repository.
3. **The store.** It creates its repository, `eval-` plus the run id, from jms_kapps' repository
   config, with SHACL on or as the twin, and refuses a ruleset other than `rdfsplus-optimized`
   (`store`).
4. **The start, on the run core.** The live-instance guard, then the run core's start: its reset
   of the repository and the seed of the topology, through the bench graph, which then loads the
   prefill file and only then turns on the history collection, if the configuration collects
   history, so that the prefill is no history; then one process for each module and the
   WarehouseManagementSystem's, with the configuration's strategies, and no monitor. Between the
   prefill's load and the first process, it reads the statement counts of its repository, where
   no participant writes (jms_kapps 95). It waits at the readiness gate. Each participant's output
   goes to its own file in the run's folder.
5. **The warm-up and the window.** It reads the WarehouseManagementSystem's spawn rate workflow from
   the graph, and then sets the spawn rate through that workflow, the WarehouseManagementSystem's REST interface, at the warm-up's start,
   and to 0 at the window's end. Load enters only there. The measured window starts when the
   warm-up ends; the server's views are read just before it starts. A warm-up of 0 is a run
   without one: the views are read, and the window starts as the rate is set (the warm-up added on
   2026-10-11, jms_kapps 78). From the load's start the completion matcher reads the run's ended
   transports at the configuration's interval, until the drain's end (`matcher`; added on
   2026-10-12, jms_kapps 48). The run's transports are the ones that the WarehouseManagementSystem's
   output names: right after the store admitted the create of a box, it logs that the box's
   transport is `queued`, and the harness collects those lines as the run goes on
   (`own_transports`; changed on 2026-10-12, jms_kapps 90, from telling them by a box number above
   the highest before the warm-up, which read every box transport of the store at every read).
   Every read of the harness names the transports it asks for, in steps of at most `READ_NAMES`.
   The server watch starts with the load and samples the resource view and the round trip every
   `WATCH_INTERVAL_SECONDS` through the warm-up, the window and the drain (`watch`; jms_kapps 95).
   The window's start also reads the global page cache.
6. **The drain.** It reads the run's transports every `DRAIN_POLL_SECONDS` until none is open, or
   the drain bound has passed. The run's transports are those the WarehouseManagementSystem's log
   named as queued: the prefill's past journeys are none of them, and the warm-up's are. Then it
   reads each of the run's transports, done with its place, failed
   with its place and why, or still open with its place, the matcher reads a last time, and the
   server's views are read again, with the query view of every repository. From the warm-up's
   start to the drain's end the harness writes nothing to the graph: it only reads.
7. **The stop.** It stops the participants in order (`Supervisor.stop_all`), and reads which
   services are still registered.
8. **The end, also after a failure.** It stops whatever still runs, reads the statement counts
   again where no participant writes, deletes its repository and no other, counts the spawns the WarehouseManagementSystem refused from its output, and, for a run
   that completed, adds the observation's measures and checks (`observation`); then it writes the
   record (`record.write_record`). A stop signal, Ctrl+C, SIGTERM or a closing terminal's SIGHUP,
   ends the run there too, as interrupted; a stop signal while the run ends does not cut it short.

**The deletion of the repository** (Sören's decision of 2026-10-14 as the product owner of the
tests, jms_kapps 116; interim names, jms_kapps 83). The outcome says how the measurement ended, and
only that: a run whose steps completed is `completed`, and gets its observation, whatever the
deletion did; a failed, over-time or interrupted run keeps its outcome. The deletion is a fact of
its own in the record, `repository_deletion`: `deleted`, `why` it was not, and `answer_lost`, why
the deletion got no usable answer, where it got none; it is null where the run created no
repository. Where the deletion gets no usable answer, since the request did not reach the store,
no answer came in the request's time, the connection dropped, a gateway in front of the store
answered 502, 503 or 504 in its place, or an answer arrived but could not be read (the record
says so, not that the answer was lost), one list of the server's repositories, by a request that
names none, says whether the store deleted it (`GraphDBServer.deleted_after_a_lost_answer`);
where that list fails too, the repository counts as not deleted, with both whys. Any other
answer is a refusal, with no list. A repository that the store did not delete is the action "repository not deleted", with why,
and the last line of `jms-eval-uc2` and the line that `jms-eval-sweep` prints after the run name
it (`outcome_line`), so that a person deletes it. (Changed on 2026-10-14, jms_kapps 116: until then
a repository that the store did not delete added "; the repository was not deleted" to the
outcome, so a completed run had no observation, and no reader took it as completed.)

**The run limit** (added on 2026-10-13, jms_kapps 106, decided by Sören on jms_kapps 85; interim
names, jms_kapps 83). Each step above has a limit of its own, and the whole run has one too: 30
minutes by default (`run_limit.RUN_LIMIT_SECONDS`), a run setting outside the configuration's key,
which the record keeps as `run_settings.run_limit_seconds`. The time counts from the run's start,
where step 2 reads the facts, to the start of step 7: the stop of the participants and the end are
outside the limit. A run older than its limit ends through step 8, as a stop signal ends it: the
participants stop, the repository is deleted and the record is written. Its outcome is `failed
(over time)`, with the limit and the step (2 to 6) at which the limit came, by the step's name
above, and its actions hold `run limit reached` (`run_limit`).

**The observation** (added on 2026-10-12, jms_kapps 48). With the configuration's `wrapper`, each
participant starts through the wrapper (`wrapper`), whose spans go into `spans/` in the run's
folder, one file for each participant; without it, the participants start as they are, and the
record's run kind is `uc2-unwrapped`. Either way each line of a participant's output is stamped
with the time it arrived, so that a deadlock alarm is dated.

Each run setting stays at the default of today's use case 2, and the record holds each with its
value: none is swept (decided by Etienne on 2026-10-11), except two that are keys of the
configuration now, the transit time and the retry wait, and one that the configuration gives or
the run fills in, the WarehouseManagementSystem's pick seed (added on 2026-10-12, jms_kapps 93,
decided by Sören on jms_kapps 85; interim names, jms_kapps 83). A run takes the seed of its
configuration, or else repetition 1 of the default base seed (`configuration.pick_seed`), seeds
the WarehouseManagementSystem's picks with it, and writes it to its record and to the saved
configuration, so that this configuration run again makes the same picks. Everything the run reaches outside itself
comes through a port, so that a test runs it against stand-ins: the server (`GraphDBServer`), the
graph (`BenchGraph`), the run core's supervisor, the spawn rate (`SpawnRate`) and the clock
(`Clock`).
"""

from __future__ import annotations

import inspect
import secrets
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Protocol

import httpx
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.services import (
    MAX_HEARTBEAT_AGE_SECONDS,
    Service,
    ServiceReader,
    services,
)
from jms_kapps.common.supervision import (
    START_TIMEOUT_SECONDS,
    STOP_TIMEOUT_SECONDS,
    Supervisor,
    refuse_if_live,
)
from jms_kapps.common.triplestore import bindings
from jms_kapps.evaluation import wrapper
from jms_kapps.evaluation.approval import Covered, covered_run
from jms_kapps.evaluation.client_load import (
    CLIENT_LOAD_INTERVAL_SECONDS,
    ClientLoadSampler,
)
from jms_kapps.evaluation.configuration import (
    DEFAULT_BASE_SEED,
    Configuration,
    pick_seed,
)
from jms_kapps.evaluation.matcher import CompletionMatcher
from jms_kapps.evaluation.observation import Stamped, observe
from jms_kapps.evaluation.own_transports import OwnTransports
from jms_kapps.evaluation.record import (
    ITERATION,
    RUN_KIND,
    UNWRAPPED_RUN_KIND,
    client_facts,
    prefill_facts,
    software_facts,
    spawns,
    write_record,
)
from jms_kapps.evaluation.run_limit import RUN_LIMIT_SECONDS, RunLimit, run_and_end
from jms_kapps.evaluation.seed import (
    PREFILL_TRANSACTION_STATEMENTS,
    SeedUC2,
    seed_with_prefill,
)
from jms_kapps.evaluation.store import (
    AnswerLost,
    GraphDBServer,
    StoreRefused,
    create_run_repository,
    public_url,
    repository_name,
)
from jms_kapps.evaluation.watch import (
    WATCH_INTERVAL_SECONDS,
    ServerWatch,
    WatchedClock,
    kept,
)
from jms_kapps.uc2 import run_core
from jms_kapps.uc2.discovery import workflow_endpoint
from jms_kapps.uc2.module import DEFAULT_DEADLOCK_ALARM_SECONDS, DEFAULT_RETRY_SECONDS
from jms_kapps.uc2.process import STEP_TIMEOUT_SECONDS, module_command
from jms_kapps.uc2.run_core import Participant, participants_of, start_run
from jms_kapps.uc2.seed import DEFAULT_TRANSIT_SECONDS, seed_topology
from jms_kapps.uc2.topology import Topology, load_topology
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, local_name
from jms_kapps.uc2.wms_process import wms_command

# How often the drain reads whether a transport is still open, in seconds.
DRAIN_POLL_SECONDS = 2.0

# How long the end of a run waits, once every participant has stopped, for the last lines of
# their output to reach their files, in seconds.
OUTPUT_SETTLE_SECONDS = 1.0

# The transit time and the retry wait that every run of the "before" series took, which its
# records hold under `run_settings` and not in their configuration: a record of that series that
# says neither is replayed with these (`baseline`). A run of the "after" series takes both from its
# configuration.
TRANSIT_SECONDS = DEFAULT_TRANSIT_SECONDS
RETRY_SECONDS = DEFAULT_RETRY_SECONDS

# The run settings of use case 2, each at today's default: none is swept.
DEADLOCK_ALARM_SECONDS = DEFAULT_DEADLOCK_ALARM_SECONDS

# Every participant of use case 2 runs its middleware with the activity feed on, and with the
# middleware's own heartbeat interval: both are set where the participants are built, not by a run.
ACTIVITY_FEED = True
HEARTBEAT_INTERVAL_SECONDS = (
    inspect.signature(SemanticMiddleware).parameters["heartbeat_interval"].default
)

# The statuses of a transport that has ended.
ENDED = (OperationStatus.DONE, OperationStatus.FAILED)

# The outcome of a run that a stop signal ended: Ctrl+C, SIGTERM or a closing terminal's SIGHUP.
INTERRUPTED = "interrupted"


def run_settings(
    watch_interval_seconds: float = WATCH_INTERVAL_SECONDS,
    *,
    wms_pick_seed: int | None = None,
    run_limit_seconds: float = RUN_LIMIT_SECONDS,
) -> dict[str, Any]:
    """Each run setting of use case 2 with the value a run takes. The transit time and the retry
    wait are no run settings any more: they are keys of the configuration (jms_kapps 93)."""
    return {
        "server_watch_interval_seconds": watch_interval_seconds,
        "deadlock_alarm_seconds": DEADLOCK_ALARM_SECONDS,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "live_heartbeat_age_seconds": MAX_HEARTBEAT_AGE_SECONDS,
        "activity_feed": ACTIVITY_FEED,
        "step_timeout_seconds": STEP_TIMEOUT_SECONDS,
        "start_timeout_seconds": START_TIMEOUT_SECONDS,
        "stop_timeout_seconds": STOP_TIMEOUT_SECONDS,
        "drain_poll_seconds": DRAIN_POLL_SECONDS,
        "prefill_transaction_statements": PREFILL_TRANSACTION_STATEMENTS,
        # The seed of the generator the WarehouseManagementSystem picks an origin and a
        # destination with: the run's, from its configuration (jms_kapps 93).
        "wms_pick_seed": wms_pick_seed,
        # How often the client host's CPU and memory use is sampled (jms_kapps 91, interim).
        "client_load_interval_seconds": CLIENT_LOAD_INTERVAL_SECONDS,
        # The limit of the whole run (`run_limit`; jms_kapps 106, interim).
        "run_limit_seconds": run_limit_seconds,
    }


@dataclass(frozen=True)
class TransportEnd:
    """One transport as the graph holds it: its local name, its status, the module it is assigned
    to, its place, and why it failed, its reason, where it has one."""

    transport: str
    status: str
    place: str | None
    reason: str | None


class BenchGraph(run_core.RunGraph, Protocol):
    """What a run reads from and writes to the graph of its repository: the run core's port, with
    the reads of the window and the drain beside it. Only `seed` writes."""

    def spawn_rate_endpoint(self) -> str:
        """Where the WarehouseManagementSystem's live service takes its spawn rate."""
        ...

    def transports_named(self, names: Sequence[str]) -> list[TransportEnd]:
        """The transports whose local names are `names`, in the order of `names`: those the graph
        holds. A read names at most `READ_NAMES` of them, so a run with many transports reads in
        steps, and none reads every transport of the store."""
        ...

    def ended_transports_named(self, names: Sequence[str]) -> list[TransportEnd]:
        """The transports among `names`, as `transports_named` reads them, that have ended, `done`
        or `failed`: the completion matcher's read."""
        ...

    def registered_services(self) -> list[str]:
        """The resources, by local name, whose service still has an address: a service that has
        not deregistered."""
        ...


# The most transports one read names. A read's text is about 100 bytes for each, so it stays near
# 20 KB however many transports a run has; a run with more reads in steps.
READ_NAMES = 200


class GraphDBBenchGraph(run_core.TripleStoreGraph):
    """A run's graph on its repository of a GraphDB.

    Its seed is the run core's reset and seed of the topology, then the prefill file, and only
    then the history collection, if the run collects history (`seed.seed_with_prefill`). It seeds
    no resource beside the topology: a run of the bench launcher starts no monitor.

    Args:
        db: The client of the run's repository.
        prefill: The prefill file, in Turtle, or None for a run without one.
        with_shapes: Whether the reset sets use case 2's shapes: the configuration's `shacl`.
            The SHACL-off twin has no SHACL validation, and refuses them (added on 2026-10-11,
            jms_kapps 62).
        seed_uc2: What resets the repository and seeds the topology: `seed_topology`.
    """

    def __init__(
        self,
        db: TripleStore,
        *,
        prefill: Path | None,
        with_shapes: bool = True,
        seed_uc2: SeedUC2 = seed_topology,
    ) -> None:
        super().__init__(db)
        self._prefill = prefill
        self._with_shapes = with_shapes
        self._seed_uc2 = seed_uc2

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        seed_with_prefill(
            self.db,
            topology,
            transit_seconds=transit_seconds,
            prefill=self._prefill,
            collect_history=collect_history,
            with_shapes=self._with_shapes,
            seed_uc2=self._seed_uc2,
        )

    def spawn_rate_endpoint(self) -> str:
        endpoint = workflow_endpoint(self.db, WMS, FC.SetSpawnRateWorkflow)
        if endpoint is None:
            raise RuntimeError(
                "the WarehouseManagementSystem's live service has no spawn rate workflow"
            )
        return endpoint

    def transports_named(self, names: Sequence[str]) -> list[TransportEnd]:
        return self._transports(names, ended_only=False)

    def ended_transports_named(self, names: Sequence[str]) -> list[TransportEnd]:
        return self._transports(names, ended_only=True)

    def _transports(
        self, names: Sequence[str], *, ended_only: bool
    ) -> list[TransportEnd]:
        ended = ", ".join(f'"{status}"' for status in ENDED)
        position = {name: index for index, name in enumerate(names)}
        found: list[TransportEnd] = []
        for start in range(0, len(names), READ_NAMES):
            listed = " ".join(
                f"<{FCI_NS}{name}>" for name in names[start : start + READ_NAMES]
            )
            rows = bindings(
                self.db,
                f"SELECT ?transport ?status ?place ?reason WHERE {{ "
                f"VALUES ?transport {{ {listed} }} "
                f"?transport a <{FC.BoxTransportOperation}> ; "
                f"<{SVC.operationStatus}> ?status . "
                + (f"FILTER (STR(?status) IN ({ended})) " if ended_only else "")
                + f"OPTIONAL {{ ?transport <{CFC.executedOn}> ?place }} "
                f"OPTIONAL {{ ?transport <{SVC.executionResult}> ?reason }} "
                f"}}",
                convert=False,
            )
            found.extend(
                TransportEnd(
                    transport=local_name(row["transport"]["value"]),
                    status=row["status"]["value"],
                    place=local_name(row["place"]["value"]) if "place" in row else None,
                    reason=row["reason"]["value"] if "reason" in row else None,
                )
                for row in rows
            )
        return sorted(found, key=lambda end: position.get(end.transport, len(position)))

    def registered_services(self) -> list[str]:
        return sorted(
            local_name(service.resource)
            for service in services(self.db, live_only=False)
            if service.address is not None
        )


class SpawnRate(Protocol):
    """Sets the WarehouseManagementSystem's spawn rate through its workflow. `HTTPSpawnRate` is the
    real one."""

    def set(self, endpoint: str, boxes_per_minute: float) -> float:
        """Set the rate at `endpoint`, and return the rate in force, as the workflow answered."""
        ...


class HTTPSpawnRate:
    """The WarehouseManagementSystem's workflow `set_spawn_rate`, over HTTP."""

    def set(self, endpoint: str, boxes_per_minute: float) -> float:
        response = httpx.post(
            endpoint, json={"boxes_per_minute": boxes_per_minute}, timeout=30
        )
        response.raise_for_status()
        return float(response.json()["boxes_per_minute"])


class Clock(Protocol):
    """The time of a run: the wall clock, a monotonic clock, and a wait."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """This machine's clocks."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class Actions:
    """The actions a run took, in order, each with its wall-clock time and its seconds since the
    run began, printed as they happen."""

    def __init__(self, clock: Clock, echo: Callable[[str], None]) -> None:
        self._clock = clock
        self._echo = echo
        self._began = clock.monotonic()
        self.taken: list[dict[str, Any]] = []

    def add(self, action: str, **details: Any) -> dict[str, Any]:
        taken = {
            "action": action,
            "at": self._clock.now().isoformat(),
            "seconds": round(self._clock.monotonic() - self._began, 3),
            **details,
        }
        self.taken.append(taken)
        said = ", ".join(f"{key} {value}" for key, value in details.items())
        self._echo(
            f"[{taken['seconds']:9.1f} s] {action}" + (f": {said}" if said else "")
        )
        return taken


class ParticipantOutputs:
    """Where the supervisor's echo goes: each participant's output into a file of its own in the
    run's folder, named after its resource, and every other line, as the command lines and the
    supervisor's notices, into `harness.log` and onto `echo`.

    The supervisor writes a participant's line as its name, two spaces and the line. Its file
    holds each line stamped with the time it arrived, by `now`: the time, two spaces and the line
    (added on 2026-10-12, jms_kapps 48, so that a deadlock alarm is dated).
    """

    def __init__(
        self,
        folder: Path,
        echo: Callable[[str], None],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self._echo = echo
        self._now = now
        self._lock = threading.Lock()
        self._files: dict[str, IO[str]] = {}
        self._paths: dict[IRI, Path] = {}
        self._read_to: dict[IRI, int] = {}
        self._own = (folder / "harness.log").open("a", encoding="utf-8")

    def add(self, participants: Sequence[Participant]) -> None:
        """Open a file for each of `participants`."""
        with self._lock:
            for participant in participants:
                path = self.folder / f"{local_name(participant.resource)}.log"
                self._paths[participant.resource] = path
                self._files[participant.name] = path.open("a", encoding="utf-8")

    def echo(self, line: str) -> None:
        with self._lock:
            if self._own.closed:
                # A line that comes after the run's end, as a process's last one, goes to the
                # terminal only.
                self._echo(line)
                return
            for name, file in self._files.items():
                if line.startswith(f"{name}  "):
                    file.write(f"{self._now().isoformat()}  {line[len(name) + 2 :]}\n")
                    file.flush()
                    return
            self._own.write(line + "\n")
            self._own.flush()
        self._echo(line)

    def lines_of(self, resource: IRI) -> list[str]:
        """What the participant of `resource` wrote, line by line, without the stamps: none where
        it has no file."""
        return [line for _, line in self.stamped_lines_of(resource)]

    def new_lines_of(self, resource: IRI) -> list[str]:
        """The complete lines the participant of `resource` has written since the last call
        for it, without the stamps: the first call returns them all. The file is read from where
        the last call stopped, so a long run does not read its start again (jms_kapps 90)."""
        path = self._paths.get(resource)
        if path is None or not path.is_file():
            return []
        with self._lock:
            with path.open("rb") as file:
                file.seek(self._read_to.get(resource, 0))
                chunk = file.read()
            complete = chunk[: chunk.rfind(b"\n") + 1]
            self._read_to[resource] = self._read_to.get(resource, 0) + len(complete)
        return [_unstamped(raw)[1] for raw in complete.decode("utf-8").splitlines()]

    def stamped_lines_of(self, resource: IRI) -> list[Stamped]:
        """What the participant of `resource` wrote, line by line, each with the time it arrived:
        none where it has no file, and no time for a line without a stamp."""
        path = self._paths.get(resource)
        if path is None or not path.is_file():
            return []
        return [
            _unstamped(raw) for raw in path.read_text(encoding="utf-8").splitlines()
        ]

    def stamped(self) -> dict[str, list[Stamped]]:
        """Each participant's stamped output, by its resource's local name."""
        return {
            local_name(resource): self.stamped_lines_of(resource)
            for resource in self._paths
        }

    def close(self) -> None:
        with self._lock:
            for file in (*self._files.values(), self._own):
                file.close()
            self._files.clear()


def _unstamped(raw: str) -> Stamped:
    """A line of a participant's file as its time and the line; no time where it has no stamp."""
    stamp, separator, line = raw.partition("  ")
    if separator:
        try:
            return datetime.fromisoformat(stamp), line
        except ValueError:
            pass
    return None, raw


# What starts the run core's supervisor for a run: its reader of the services, and the echo of
# its processes' output.
SupervisorFor = Callable[[ServiceReader, Callable[[str], None]], Supervisor]


def new_supervisor(services: ServiceReader, echo: Callable[[str], None]) -> Supervisor:
    """The run core's supervisor of a run."""
    return Supervisor(services, echo=echo)


def new_run_id(now: datetime) -> str:
    """A run id: the run's start in UTC, to the second, and four random hex digits."""
    return f"{now.astimezone(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def run(
    configuration: Configuration,
    *,
    runs: Path,
    server: GraphDBServer,
    graph_for: Callable[[str, Path | None, bool], BenchGraph],
    spawn_rate: SpawnRate,
    supervisor_for: SupervisorFor = new_supervisor,
    clock: Clock | None = None,
    approval: Covered | None = None,
    echo: Callable[[str], None] = print,
    sweep: Mapping[str, Any] | None = None,
    watch_interval_seconds: float = WATCH_INTERVAL_SECONDS,
    run_limit_seconds: float = RUN_LIMIT_SECONDS,
) -> dict[str, Any]:
    """Make one UC2 run of `configuration`, on a repository of its own, and write its record.
    Returns the record, which is in `record.json` in the run's folder too.

    Args:
        runs: The folder in which the run makes its own, named by its run id.
        server: The configured GraphDB's REST API.
        graph_for: The run's graph on a repository, by its name, with the prefill file it loads
            and whether its reset sets the shapes, the configuration's `shacl`.
        spawn_rate: Sets the WarehouseManagementSystem's spawn rate.
        supervisor_for: Starts the run core's supervisor: `new_supervisor`.
        clock: The run's time: this machine's if None.
        approval: The covered record (`approval.cover`) of a heavy run, or None where it is off.
            A heavy run takes nothing else: the run checks that it names this configuration, and
            its period at the run's start.
        echo: Where the run's actions and the supervisor's notices are printed.
        sweep: Which run of a sweep this is, for its record (`jms-eval-sweep`): its spec, its
            configuration's number and key, and its repetition. None for a run of its own.
        watch_interval_seconds: The seconds from one sample of the server watch to the next: a
            run setting (`watch`).
        run_limit_seconds: The limit of the whole run, in seconds of `clock`: a run setting
            (`run_limit`). A run older than it ends as failed (over time).

    Raises:
        HeavyRunRefused: If the run is heavy and `approval` is not the covered record of this
            configuration, or its period is not the run's start. Nothing is created, and no record is written.
        WrapperUnavailable: If the configuration has the wrapper on and OpenTelemetry, the extra
            `evaluation`, is not installed. Nothing is created, and no record is written.
    """
    clock = clock if clock is not None else SystemClock()
    approval_record = covered_run(
        configuration.heavy, approval, configuration, now=clock.now()
    )
    # The run's pick seed: its configuration's, else repetition 1 of the default base seed. Either
    # way the saved configuration and the record say it.
    configuration = replace(
        configuration,
        wms_pick_seed=pick_seed(DEFAULT_BASE_SEED, 1)
        if configuration.wms_pick_seed is None
        else configuration.wms_pick_seed,
    )
    assert configuration.wms_pick_seed is not None
    if configuration.wrapper:
        wrapper.available()
    # The run's waits take the server watch's samples (`watch`).
    waiting = WatchedClock(clock)
    watch = ServerWatch(server, interval=watch_interval_seconds, clock=clock)
    waiting.watch = watch
    run_id = new_run_id(clock.now())
    repository = repository_name(run_id)
    folder = runs / run_id
    outputs = ParticipantOutputs(folder / "output", echo, clock.now)
    spans = folder / "spans" if configuration.wrapper else None
    configuration_toml = configuration.to_toml()
    # The configuration as a file of its own too, which `jms-eval-uc2` runs again.
    (folder / "configuration.toml").write_text(configuration_toml, encoding="utf-8")
    actions = Actions(clock, outputs.echo)
    record: dict[str, Any] = {
        "run_kind": RUN_KIND if configuration.wrapper else UNWRAPPED_RUN_KIND,
        "iteration": ITERATION,
        "run_id": run_id,
        "repository": repository,
        "outcome": None,
        "configuration": configuration_toml,
        "sweep": None if sweep is None else dict(sweep),
        "heavy_run_approval": approval_record,
        "actions": actions.taken,
        "warm_up": None,
        "window": None,
        "run_settings": run_settings(
            watch_interval_seconds,
            wms_pick_seed=configuration.wms_pick_seed,
            run_limit_seconds=run_limit_seconds,
        ),
        "prefill": None,
        "client": None,
        "client_load": None,
        "software": None,
        "server": {"url": public_url(server.url)},
        "participants": [],
        "transports": None,
        "spawns": None,
        "registered_services_after_the_stop": None,
        "matcher": None,
        "measures": None,
        "valid": None,
        "failed_checks": None,
        "checks_made": None,
        "stuck": None,
        "repository_deletion": None,
    }
    actions.add("run began", run_id=run_id, repository=repository)
    supervisor: Supervisor | None = None
    matcher: CompletionMatcher | None = None
    client_load = ClientLoadSampler(clock, interval=CLIENT_LOAD_INTERVAL_SECONDS)
    limit = RunLimit(run_limit_seconds, clock)

    def steps() -> None:
        """Steps 2 to 7. Steps 2 to 6 are each marked on the run limit, and the limit stops
        before step 7: the stop of the participants belongs to the run's end, outside the
        limit."""
        nonlocal supervisor, matcher
        topology = load_topology(configuration.topology)
        record["prefill"] = prefill_facts(configuration.prefill)
        record["client"] = client_facts(server.round_trip_ms())
        record["software"] = software_facts()
        record["server"] |= server_facts_before_the_run(server)

        limit.at("the store")
        record["server"]["repository_config"] = create_run_repository(
            server, repository, shacl=configuration.shacl
        )
        actions.add("repository created", shacl=configuration.shacl)

        limit.at("the start")
        graph = graph_for(repository, configuration.prefill, configuration.shacl)
        participants = participants_of(
            topology,
            module_command=lambda resource: _observed(
                module_command(
                    resource,
                    repository,
                    retry_seconds=configuration.retry_seconds,
                ),
                resource,
                spans,
            ),
            wms_command=_observed(
                wms_command(repository, pick_seed=configuration.wms_pick_seed),
                WMS,
                spans,
            ),
            module_strategy=configuration.module_strategy,
            wms_strategy=configuration.wms_strategy,
            deadlock_alarm_seconds=DEADLOCK_ALARM_SECONDS,
        )
        record["participants"] = [
            {"name": p.name, "resource": str(p.resource), "command": p.command}
            for p in participants
        ]
        outputs.add(participants)
        supervisor = supervisor_for(graph.services, outputs.echo)
        refuse_if_live(graph.services, dataset=repository)
        actions.add("seed began")
        start_run(
            _WithStatementCounts(
                graph,
                lambda: read_statement_counts(
                    record,
                    STATEMENTS_BEFORE_THE_PARTICIPANTS,
                    repository,
                    server,
                    actions,
                ),
            ),
            supervisor,
            topology,
            participants,
            transit_seconds=configuration.transit_seconds,
            collect_history=configuration.collect_history,
        )
        actions.add("participants started", participants=len(participants))
        supervisor.wait_until_ready()
        actions.add("participants ready")

        limit.at("the warm-up and the window")
        endpoint = graph.spawn_rate_endpoint()
        own = OwnTransports(lambda: outputs.new_lines_of(WMS))
        matcher = CompletionMatcher(
            _ended_reader(graph, own),
            interval=configuration.matcher_interval_seconds,
            clock=waiting,
        )
        client_load.start()
        _window(
            configuration,
            endpoint,
            spawn_rate,
            matcher,
            actions,
            record,
            lambda: server_views(server, repository, "window_start"),
            watch,
        )
        limit.at("the drain")
        still_open = _drain(
            graph, own, configuration.drain_bound_seconds, matcher, clock
        )
        watch.stop()
        client_load.stop()
        actions.add("drain ended", open_transports=len(still_open))
        own.refresh()
        record["transports"] = _transports(own.names, graph.transports_named(own.names))
        # The matcher's last read, after the graph's: what ends in between, the matcher
        # alone sees (`observation.matcher_check`).
        matcher.read()
        record["server"] |= server_views(
            server, repository, "drain_end", every_repository=True
        )

        limit.stop()
        supervisor.stop_all()
        actions.add("participants stopped")
        record["registered_services_after_the_stop"] = graph.registered_services()

    def end() -> None:
        """Step 8, also after a failure, a stop signal or the limit."""
        watch.stop()
        record["server"]["watch"] = watch.record()
        client_load.stop()
        if client_load.started:
            record["client_load"] = client_load.record()
        try:
            _end(
                record,
                repository,
                server,
                supervisor,
                outputs,
                actions,
                clock,
                matcher=matcher,
                spans=spans,
            )
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


# The keys under which a record holds the run repository's statement counts: read after the prefill
# load and before the participants start, and after they stop and before the repository is
# deleted, where no participant writes and no transaction is open (GDB-12163: 10.8.5 reports a
# wrong size inside an open transaction). Interim names (jms_kapps 83).
STATEMENTS_BEFORE_THE_PARTICIPANTS = "statements_before_the_participants"
STATEMENTS_AFTER_THE_PARTICIPANTS = "statements_after_the_participants"


def server_facts_before_the_run(server: GraphDBServer) -> dict[str, Any]:
    """What a record holds of the server at the run's start, the moment before the harness
    creates the run's repository: its version, its repositories and their states, its resources,
    its license (once per run: the edition and the licensed cores), and the monitoring view of
    every repository of the server, the protected ones included. The store-only baseline reads
    them too (`baseline`)."""
    repositories = server.repositories()
    return {
        "version": server.version(),
        "repositories_at_the_start": repositories,
        "resources_at_the_start": server.resources(),
        "license": kept("GET rest/graphdb-settings/license", server.license),
        "repository_monitors_at_the_start": kept(
            "GET rest/monitor/repository/<every repository>",
            lambda: server.repository_monitors(repositories),
        ),
    }


def server_views(
    server: GraphDBServer,
    repository: str,
    moment: str,
    *,
    every_repository: bool = False,
) -> dict[str, Any]:
    """What the server's Workbench shows at `moment` of the run, by the keys a record holds them
    under: its resources, its structures (the global page cache's hits and misses), the query
    and transaction view of the run's repository, and every repository of the server with its
    state; with `every_repository`, the query and transaction view of every repository too, the
    protected ones included. The store-only baseline reads them too (`baseline`)."""
    views = {
        f"resources_at_the_{moment}": server.resources(),
        f"structures_at_the_{moment}": server.structures(),
        f"repository_monitor_at_the_{moment}": server.repository_monitor(repository),
        f"repositories_at_the_{moment}": server.repositories(),
    }
    if every_repository:
        views[f"repository_monitors_at_the_{moment}"] = kept(
            "GET rest/monitor/repository/<every repository>",
            lambda: server.repository_monitors(views[f"repositories_at_the_{moment}"]),
        )
    return views


def read_statement_counts(
    record: dict[str, Any],
    key: str,
    repository: str,
    server: GraphDBServer,
    actions: Actions,
) -> None:
    """Read the statement counts of the run's repository into the record under `key`, and say so
    in the actions, so that their order shows where it was read. The caller reads them only
    where no participant writes. A refused read is kept with its status and the run goes on."""
    record["server"][key] = kept(
        f"GET rest/repositories/{repository}/size",
        lambda: server.statement_counts(repository),
    )
    actions.add("statement counts read", key=key)


class _WithStatementCounts:
    """The run core's graph, whose seed reads the statement counts once it has loaded the
    prefill: the participants start only after it."""

    def __init__(
        self, graph: run_core.RunGraph, after_the_seed: Callable[[], None]
    ) -> None:
        self._graph = graph
        self._after_the_seed = after_the_seed

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        self._graph.seed(topology, transit_seconds, collect_history=collect_history)
        self._after_the_seed()

    def services(self, workflows: Collection[IRI]) -> Iterable[Service]:
        return self._graph.services(workflows)


def _window(
    configuration: Configuration,
    endpoint: str,
    spawn_rate: SpawnRate,
    matcher: CompletionMatcher,
    actions: Actions,
    record: dict[str, Any],
    views_at_the_window_start: Callable[[], dict[str, Any]],
    watch: ServerWatch,
) -> None:
    """The warm-up and the window: the spawn rate set at the warm-up's start, the measured window
    from the warm-up's end, and the rate set to 0 at the window's end. The server's views at the
    window's start, `views_at_the_window_start`, are read just before the window starts. A warm-up
    of 0 is none: the views are read, and the window starts as the rate is set. The completion
    matcher starts as the rate is set, the load's start, and the run waits through it. Nothing in
    it writes to the graph. The server watch starts with the matcher, the load's start, and the
    run's waits take its samples."""
    try:
        if configuration.warm_up_seconds > 0:
            answered = spawn_rate.set(endpoint, configuration.boxes_per_minute)
            began = actions.add("spawn rate set", boxes_per_minute=answered)
            matcher.start()
            watch.start()
            record["warm_up"] = {"start": began["at"], "end": None}
            matcher.wait(configuration.warm_up_seconds)
            record["server"] |= views_at_the_window_start()
            started = actions.add("warm-up ended")
            record["warm_up"]["end"] = started["at"]
        else:
            record["server"] |= views_at_the_window_start()
            answered = spawn_rate.set(endpoint, configuration.boxes_per_minute)
            started = actions.add("spawn rate set", boxes_per_minute=answered)
            matcher.start()
            watch.start()
        record["window"] = {"start": started["at"], "end": None}
        matcher.wait(configuration.window_seconds)
    finally:
        # Also when the warm-up or the window is cut short, or its start failed, since the rate
        # may have been set all the same: the WarehouseManagementSystem spawns no more.
        answered = spawn_rate.set(endpoint, 0)
        ended = actions.add("spawn rate set", boxes_per_minute=answered)
        if record["window"] is not None:
            record["window"]["end"] = ended["at"]


def _ended_reader(
    graph: BenchGraph, own: OwnTransports
) -> Callable[[], list[TransportEnd]]:
    """The completion matcher's read: the run's transports that have ended, among those the
    WarehouseManagementSystem's log has named so far and that the matcher has not read ended
    yet. So a read names only the transports that can still end, and never one of the prefill's.
    An ended transport is read once; the matcher keeps the end it saw first."""
    read_ended: set[str] = set()

    def read() -> list[TransportEnd]:
        own.refresh()
        ended = graph.ended_transports_named(own.without(read_ended))
        read_ended.update(end.transport for end in ended)
        return ended

    return read


def _drain(
    graph: BenchGraph,
    own: OwnTransports,
    bound: float,
    matcher: CompletionMatcher,
    clock: Clock,
) -> list[TransportEnd]:
    """Wait until no transport of the run is open, or `bound` seconds have passed, reading the
    graph every `DRAIN_POLL_SECONDS`, and waiting through the completion matcher, which goes on
    reading at its interval. The run's transports are those that the WarehouseManagementSystem's
    log names (`own`); a read names those not seen ended yet. Returns those still open at the
    drain's end. The log is the authority for which transports exist, and a line reaches its file
    a moment after the participant writes it, so the drain waits `OUTPUT_SETTLE_SECONDS` once
    before it takes "none is open" as final."""
    began = clock.monotonic()
    ended: set[str] = set()
    settled = False
    while True:
        own.refresh()
        read = graph.transports_named(own.without(ended))
        ended.update(end.transport for end in read if end.status in ENDED)
        still_open = [transport for transport in read if transport.status not in ENDED]
        left = bound - (clock.monotonic() - began)
        if left <= 0 or (not still_open and settled):
            return still_open
        if not still_open:
            settled = True
            matcher.wait(min(OUTPUT_SETTLE_SECONDS, left))
            continue
        matcher.wait(min(DRAIN_POLL_SECONDS, left))


def _transports(queued: Sequence[str], ends: Sequence[TransportEnd]) -> dict[str, Any]:
    """What the record holds of the run's transports at the drain's end, those the
    WarehouseManagementSystem's log named, `queued` of them: each one done, with its place; each
    one failed, with its place and its reason; and each one still open, with its place, whose box
    counts as stuck.

    The warm-up's transports are the run's too. Telling the warm-up's from the window's is the
    measures' work, by the warm-up's end in the record. The record no longer holds the highest
    box before the window (`highest_box_before_the_window`, jms_kapps 90): the records of the
    first iteration do, and the report still reads them."""
    return {
        "queued_by_the_wms": len(queued),
        # A transport the log named that the graph does not hold: none in a sound run.
        "queued_not_in_the_graph": sorted(set(queued) - {t.transport for t in ends}),
        "done": [
            {"transport": t.transport, "place": t.place}
            for t in ends
            if t.status == OperationStatus.DONE
        ],
        "failed": [
            {"transport": t.transport, "place": t.place, "reason": t.reason}
            for t in ends
            if t.status == OperationStatus.FAILED
        ],
        "open": [
            {"transport": t.transport, "status": t.status, "place": t.place}
            for t in ends
            if t.status not in ENDED
        ],
    }


def _end(
    record: dict[str, Any],
    repository: str,
    server: GraphDBServer,
    supervisor: Supervisor | None,
    outputs: ParticipantOutputs,
    actions: Actions,
    clock: Clock,
    *,
    matcher: CompletionMatcher | None = None,
    spans: Path | None = None,
) -> None:
    """The end of every run, also after a failure: stop whatever runs, delete the run's
    repository, which only this run created, count the refused spawns, keep the matcher's reads,
    and, for a run that completed, add the observation's measures and checks (`observation`),
    from the participants' spans in `spans`, or from none for a run without the wrapper."""
    if supervisor is not None and not _stopped(actions.taken):
        try:
            supervisor.stop_all()
            actions.add("participants stopped")
        except Exception as error:  # noqa: BLE001 - the repository is deleted all the same
            actions.add("participants not stopped", why=str(error))
    if repository in server.created and (supervisor is None or _stopped(actions.taken)):
        # No participant writes now, so no transaction of theirs is open (GDB-12163).
        read_statement_counts(
            record, STATEMENTS_AFTER_THE_PARTICIPANTS, repository, server, actions
        )
    delete_run_repository(record, repository, server, actions)
    if supervisor is not None:
        # A participant's last lines are read from its pipe just after it exits.
        clock.sleep(OUTPUT_SETTLE_SECONDS)
    record["spawns"] = spawns(outputs.lines_of(WMS))
    if matcher is not None and matcher.started:
        record["matcher"] = matcher.record()
    if record["outcome"] == "completed":
        try:
            record |= observe(record, spans=spans, outputs=outputs.stamped())
        except Exception as error:  # noqa: BLE001 - the record says why it has no measures
            record["outcome"] = (
                f"failed: the observation of the run: {type(error).__name__}: {error}"
            )
    actions.add("run ended", outcome=record["outcome"])
    outputs.close()


def delete_run_repository(
    record: dict[str, Any], repository: str, server: GraphDBServer, actions: Actions
) -> None:
    """Delete the run's repository, which only this run created, and keep in `record` every
    request the harness sent to the server's REST API. The store-only baseline ends the same way
    (`baseline`).

    The deletion is a fact of its own in the record, `repository_deletion`: `deleted`, `why` it
    was not, and `answer_lost`, why the deletion got no usable answer, where it got none: the
    request did not reach the store, no answer came, or an answer came that could not be read
    (changed on 2026-10-15 after a review: until then this docstring said that
    `answer_lost` is why the store's answer was lost). A repository that the store does not
    delete is an action of its own too, with why. The outcome
    says nothing of the deletion: it says how the measurement ended, and only that (changed on
    2026-10-14, jms_kapps 116, decided by Sören as the product owner of the tests; until then a
    repository that the store did not delete added "; the repository was not deleted" to the
    outcome, and a completed run then had no observation).
    """
    if repository in server.created:
        deletion = _deletion(repository, server)
        record["repository_deletion"] = deletion
        if deletion["deleted"] and deletion["answer_lost"] is not None:
            actions.add("repository deleted", answer_lost=deletion["answer_lost"])
        elif deletion["deleted"]:
            actions.add("repository deleted")
        else:
            actions.add("repository not deleted", why=deletion["why"])
    record["server"]["requests"] = list(server.requests)


def outcome_line(program: str, record: Mapping[str, Any], runs: Path) -> str:
    """The line that tells how a run ended and where its record is below `runs`, and names its
    repository where the store did not delete it, so that a person deletes it: the last line of
    `jms-eval-uc2` and of `jms-eval-baseline`, and the line that `jms-eval-sweep` prints after
    each run, each with its command's name `program`. (The repository added on 2026-10-14,
    jms_kapps 116; interim name, jms_kapps 83.)"""
    return (
        f"{program}: {record['outcome']}. The record is at "
        f"{runs / record['run_id'] / 'record.json'}{_left_repository_suffix(record)}"
    )


def _left_repository_suffix(record: Mapping[str, Any]) -> str:
    """The end of `outcome_line` where the store did not delete the run's repository; empty
    where it was deleted, or where the run created none."""
    deletion = record.get("repository_deletion")
    if not isinstance(deletion, Mapping) or deletion.get("deleted"):
        return ""
    return f". The repository {record['repository']} was not deleted: delete it on the server"


# The errors of a request that did not reach the store: no connection, none in time, or no free
# connection of the client's in time (httpx). The deletion's record says so, not that an answer
# was lost; the listing decides all the same.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# The error of an answer that arrived but could not be read: httpx could not decode its body
# (`DecodingError`, a malformed encoding). The deletion's record says so, not that an answer was
# lost; the listing decides all the same.
_UNREADABLE = (httpx.DecodingError,)


def _deletion(repository: str, server: GraphDBServer) -> dict[str, Any]:
    """Delete the run's repository on `server`, and say how it went, as the record keeps it
    (`delete_run_repository`). Where the deletion gets no usable answer, since the request did not
    reach the store, the store's answer was lost (no answer in the request's time, a dropped
    connection), a gateway in front of the store answered 502, 503 or 504 in its place
    (`store.AnswerLost`), or an answer arrived but could not be read (`httpx.DecodingError`), one
    list of the server's repositories says whether it was deleted
    (`GraphDBServer.deleted_after_a_lost_answer`); where that list fails too, the repository
    counts as not deleted, with both whys. Any other answer is a refusal, with no list."""
    try:
        server.delete_repository(repository)
    except (httpx.HTTPError, AnswerLost) as error:
        said = str(error) or type(error).__name__
        if isinstance(error, _NOT_SENT):
            no_answer = f"the deletion request did not reach the store ({said})"
        elif isinstance(error, _UNREADABLE):
            no_answer = (
                f"an answer to the deletion arrived but could not be read ({said})"
            )
        else:
            no_answer = f"the store's answer to the deletion was lost ({said})"
        try:
            if server.deleted_after_a_lost_answer(repository):
                return {"deleted": True, "why": None, "answer_lost": said}
            why = f"{no_answer}, and the store still lists the repository"
        except StoreRefused as unlisted:
            why = f"{no_answer}, and {unlisted}"
        return {"deleted": False, "why": why, "answer_lost": said}
    except Exception as error:  # noqa: BLE001 - the record says why
        return {"deleted": False, "why": str(error), "answer_lost": None}
    return {"deleted": True, "why": None, "answer_lost": None}


def _observed(command: list[str], resource: IRI, spans: Path | None) -> list[str]:
    """The command line of the participant of `resource`: through the wrapper, which writes its
    spans into a file of `spans` named after the resource, or as it is where `spans` is None, for
    a run without the wrapper."""
    if spans is None:
        return command
    return wrapper.wrapped(command, spans / f"{local_name(resource)}.jsonl")


def _stopped(actions: Sequence[Mapping[str, Any]]) -> bool:
    return any(action["action"] == "participants stopped" for action in actions)
