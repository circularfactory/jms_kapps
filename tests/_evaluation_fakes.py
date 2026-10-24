"""Stand-ins for what the measurement harness reaches, so that its tests reach no store, no
WarehouseManagementSystem and no process: a GraphDB's REST API in memory, the graph of a run, the
supervisor of the run core, the WMS's spawn rate workflow and a clock.

Each one writes what it is asked into one shared list of events, in order, so that a test reads
the order of the harness's actions across all of them.

For the observation, the clock does what a test schedules once its time has come,
the graph shows a transport ended from the time a test gives it, and the supervisor writes the
spans that a test gives a participant into the file its wrapper would write them to, and a
participant's lines of output at the times a test gives them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
from kapps_triplestore_interface import IRI
from rdflib import Graph

from jms_kapps.common.services import Service
from jms_kapps.evaluation.bench import GraphDBBenchGraph, TransportEnd
from jms_kapps.evaluation.store import REP, GraphDBServer
from jms_kapps.uc2.topology import Topology
from jms_kapps.uc2.vocabulary import FCI_NS as FCI
from jms_kapps.uc2.vocabulary import WMS

# The login of the stand-in store. Neither may reach a record.
USERNAME = "harness-user-x7"
PASSWORD = "harness-secret-q9z"

# What the stand-in WarehouseManagementSystem's spawn rate workflow is reached at.
SPAWN_RATE_ENDPOINT = "http://127.0.0.1:9999/workflows/set_spawn_rate/execute"


def other_repositories_named(requests: Sequence[str], repository: str) -> list[str]:
    """The requests of `requests` (`METHOD path`) that name a repository other than `repository`,
    by the views and endpoints a run reaches: the REST views of a repository, its monitoring view
    and the store's own endpoint."""
    others = []
    for request in requests:
        _, path = request.split(" ", 1)
        parts = path.split("/")
        if parts[:3] == ["rest", "monitor", "repository"]:
            name = parts[3]
        elif parts[:2] == ["rest", "repositories"] and len(parts) > 2:
            name = parts[2]
        elif parts[:1] == ["repositories"] and len(parts) > 1:
            name = parts[1]
        else:
            continue
        if name != repository:
            others.append(request)
    return others


class Events(list):
    """What happened, in order: each event a tuple whose first item says what it was."""

    def index_of(self, *event: object) -> int:
        """Where the first event that begins with `event` is."""
        for at, happened in enumerate(self):
            if happened[: len(event)] == event:
                return at
        raise AssertionError(f"no event {event!r} in {list(self)!r}")

    def between(self, first: tuple, last: tuple) -> list[tuple]:
        """The events after the first that begins with `first`, up to the first that begins with
        `last`, both left out."""
        return list(self[self.index_of(*first) + 1 : self.index_of(*last)])


class FakeGraphDB:
    """A GraphDB's REST API in memory, as the harness uses it: the login, the repositories, and
    the views the Workbench shows. Every request it is sent is kept, by method and path.

    Args:
        ruleset: The ruleset it answers for every repository it holds.
        repositories: The repositories it holds already, besides those the harness creates.
        events: Where it writes each repository it creates or deletes.
        lose_create_answer: Whether its answer to a creation is lost, after it created the
            repository.
        on_delete: What it does before it deletes a repository, as a test's signal. Where it
            returns a response, that is the store's answer, and the repository stays.
        on_list: What it does before it lists its repositories; where it returns a response,
            that is the store's answer.
        states: The state it lists a repository in, RUNNING for the others.
        refusing: The paths it refuses with the status given, by GET path (or `*` and its end),
            after the login
            (a refused or lost watch read is kept and the run goes on).
    """

    def __init__(
        self,
        *,
        ruleset: str = "rdfsplus-optimized",
        repositories: Iterable[str] = ("jms-kapps", "kapps-demo", "Tests"),
        events: Events | None = None,
        lose_create_answer: bool = False,
        on_delete: Callable[[], object] | None = None,
        on_list: Callable[[], object] | None = None,
        refusing: Mapping[str, int] | None = None,
        states: Mapping[str, str] | None = None,
    ) -> None:
        self.states = dict(states or {})
        self.refusing = dict(refusing or {})
        self.lose_create_answer = lose_create_answer
        self.on_delete = on_delete
        self.on_list = on_list
        self.ruleset = ruleset
        self.repositories: dict[str, str] = {name: "" for name in repositories}
        self.requests: list[str] = []
        self.logins: list[dict] = []
        self.events = events if events is not None else Events()

    def server(self) -> GraphDBServer:
        """The harness's client of this store, logged in as `USERNAME` with `PASSWORD`."""
        client = httpx.Client(
            transport=httpx.MockTransport(self.handle), base_url="http://graphdb.test"
        )
        return GraphDBServer(
            "http://graphdb.test", username=USERNAME, password=PASSWORD, client=client
        )

    def created(self) -> list[str]:
        """The repositories it holds that it did not hold from the start."""
        return [name for name, config in self.repositories.items() if config]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        self.requests.append(f"{request.method} {path}")
        parts = path.split("/")
        if (request.method, path) == ("POST", "rest/login"):
            self.logins.append(json.loads(request.content))
            return httpx.Response(200, headers={"Authorization": "GDB fake-token"})
        if request.headers.get("Authorization") != "GDB fake-token":
            return httpx.Response(401, text="Unauthorized")
        for refused, status in self.refusing.items():
            # A path, or "*" and the end of one.
            if request.method == "GET" and (
                path == refused or (refused[0] == "*" and path.endswith(refused[1:]))
            ):
                return httpx.Response(status, text="refused by the store")
        if (request.method, path) == ("GET", "rest/info/version"):
            return httpx.Response(
                200, json={"productVersion": "10.8.5", "productType": "enterprise"}
            )
        if (request.method, path) == ("GET", "rest/monitor/infrastructure"):
            return httpx.Response(
                200, json={"heapMemoryUsage": {"used": 1000, "max": 8000}, "cpuLoad": 3}
            )
        if (request.method, path) == ("GET", "rest/monitor/structures"):
            return httpx.Response(
                200, json={"cache": {"hit": 90, "miss": 10}, "entityPool": 5}
            )
        if (request.method, path) == ("GET", "rest/graphdb-settings/license"):
            return httpx.Response(
                200, json={"productType": "enterprise", "maxCpuCores": 8}
            )
        if (request.method, path) == ("GET", "rest/repositories"):
            answer = None if self.on_list is None else self.on_list()
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(
                200,
                json=[
                    {"id": name, "state": self.states.get(name, "RUNNING")}
                    for name in self.repositories
                ],
            )
        if (
            request.method == "GET"
            and parts[:2] == ["rest", "repositories"]
            and len(parts) == 4
            and parts[3] == "size"
            and parts[2] in self.repositories
        ):
            self.events.append(("size", parts[2]))
            return httpx.Response(200, json={"explicit": 7, "inferred": 3, "total": 10})
        if (request.method, path) == ("POST", "rest/repositories"):
            return self._create(request)
        if parts[:2] == ["rest", "repositories"] and len(parts) == 3:
            name = parts[2]
            if name not in self.repositories:
                return httpx.Response(404, text=f"No repository {name}")
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "id": name,
                        "params": {
                            "ruleset": {"name": "ruleset", "value": self.ruleset},
                        },
                    },
                )
            if request.method == "DELETE":
                if self.on_delete is not None:
                    answer = self.on_delete()
                    if isinstance(answer, httpx.Response):
                        return answer
                del self.repositories[name]
                self.events.append(("repository deleted", name))
                return httpx.Response(200)
        if parts[:3] == ["rest", "monitor", "repository"] and request.method == "GET":
            return httpx.Response(200, json={"activeTransactions": 0})
        return httpx.Response(404, text="Not found")

    def _create(self, request: httpx.Request) -> httpx.Response:
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + request.headers["Content-Type"].encode()
            + b"\r\n\r\n"
            + request.content
        )
        [config] = [
            part.get_content()
            for part in message.iter_parts()
            if part.get_param("name", header="content-disposition") == "config"
        ]
        if isinstance(config, bytes):
            config = config.decode("utf-8")
        [name] = [
            str(o)
            for o in Graph()
            .parse(data=config, format="turtle")
            .objects(None, REP.repositoryID)
        ]
        if name in self.repositories:
            return httpx.Response(400, text=f"Repository {name} already exists.")
        self.repositories[name] = config
        self.events.append(("repository created", name))
        if self.lose_create_answer:
            raise httpx.ReadTimeout("the answer was lost", request=request)
        return httpx.Response(201)


class FakeClock:
    """A clock whose time moves only when the harness sleeps. What a test schedules with `at`
    happens once a sleep has reached its time."""

    def __init__(self, events: Events) -> None:
        self.events = events
        self.seconds = 0.0
        self.start = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        self._due: list[tuple[float, Callable[[], object]]] = []

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.seconds)

    def at_seconds(self, seconds: float) -> datetime:
        """The time of the clock at `seconds`."""
        return self.start + timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self.seconds

    def at(self, seconds: float, action: Callable[[], object]) -> None:
        """Do `action` once a sleep has brought the clock to `seconds`."""
        self._due.append((seconds, action))
        self._due.sort(key=lambda due: due[0])

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", seconds))
        self.seconds += seconds
        while self._due and self._due[0][0] <= self.seconds:
            _, action = self._due.pop(0)
            action()


class FakeDB:
    """The client of a run's repository, as the bench graph's seed writes to it: every write is
    an event."""

    def __init__(self, events: Events) -> None:
        self.events = events
        self.dataset = "eval-fake"

    def graph_add(self, content: str, **_: object) -> bool:
        self.events.append(("write", "import", content))
        return True

    def query(self, query: str, *, update: bool = False, **_: object) -> object:
        self.events.append(("write" if update else "read", "query", query))
        return True if update else {"results": {"bindings": []}}


class FakeBenchGraph(GraphDBBenchGraph):
    """The bench graph with its real seed, the reset and the prefill, written to a `FakeDB`, and
    every read answered from what a test gives it. Each read is an event.

    Args:
        prefill: The prefill file the seed loads, or None.
        ends: The run's transports at the drain's end.
        open_reads: How many reads of the run's transports answer one more running transport,
            before they answer `ends`; always if None.
        registered: The resources whose service is registered after the stop.
        clock: The run's clock, which `ended_at` is read by.
        ended_at: From when, in the clock's seconds, the graph shows each transport of `ends`
            ended, by its name: before that, `running`. A transport not named is ended from the
            start.
    """

    def __init__(
        self,
        events: Events,
        *,
        prefill: Path | None = None,
        with_shapes: bool = True,
        ends: list[TransportEnd] | None = None,
        open_reads: int | None = 0,
        registered: list[str] | None = None,
        clock: FakeClock | None = None,
        ended_at: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__(
            FakeDB(events),  # type: ignore[arg-type]
            prefill=prefill,
            with_shapes=with_shapes,
            seed_uc2=self._reset,
        )
        self.events = events
        self._ends = ends or []
        self._open_reads = open_reads
        self._registered = registered or []
        self._clock = clock
        self._ended_at = dict(ended_at or {})

    def _reset(self, db: object, topology: Topology, **options: object) -> None:
        self.events.append(("write", "reset", topology, options))

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        self.events.append(("read", "services"))
        return []

    def spawn_rate_endpoint(self) -> str:
        self.events.append(("read", "spawn rate endpoint"))
        return SPAWN_RATE_ENDPOINT

    def _shown(self) -> list[TransportEnd]:
        """The transports of `ends` as the graph shows them now."""
        now = self._clock.seconds if self._clock is not None else 0.0
        return [
            end
            if self._ended_at.get(end.transport, 0.0) <= now
            else TransportEnd(end.transport, "running", end.place, None)
            for end in self._ends
        ]

    def transports_named(self, names: Sequence[str]) -> list[TransportEnd]:
        self.events.append(("read", "transports named", tuple(names)))
        shown = [end for end in self._shown() if end.transport in names]
        if self._open_reads is None or self._open_reads > 0:
            if self._open_reads is not None:
                self._open_reads -= 1
            if "Transport99" in names:
                shown.append(TransportEnd("Transport99", "running", "Module3", None))
            return shown
        return shown

    def ended_transports_named(self, names: Sequence[str]) -> list[TransportEnd]:
        self.events.append(("read", "ended transports named", tuple(names)))
        return [
            end
            for end in self._shown()
            if end.transport in names and end.status in ("done", "failed")
        ]

    def registered_services(self) -> list[str]:
        self.events.append(("read", "registered services"))
        return list(self._registered)


class FakeSupervisor:
    """The run core's supervisor, with no process: each spawn, the readiness gate and the stop is
    an event. The WarehouseManagementSystem's process writes `wms_output` once it is spawned, as
    its process would, through the supervisor's echo.

    Args:
        echo: Where the supervisor writes command lines and each process's output.
        not_ready: Why the readiness gate fails, or None where every process turns live.
        spans: The spans each participant's wrapper writes, by its resource's local name, each
            as a line of the spans' file: written into the file its command names when it is
            spawned.
        clock: The run's clock, by which `output_at` is written.
        output_at: Lines of output: each with the clock's seconds at which it arrives, or None
            for one written as the participant starts, the local name of its participant's
            resource, and the line.
    """

    def __init__(
        self,
        events: Events,
        echo: Callable[[str], None],
        *,
        wms_output: Iterable[str] = (),
        not_ready: str | None = None,
        spans: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
        clock: FakeClock | None = None,
        output_at: Iterable[tuple[float | None, str, str]] = (),
    ) -> None:
        self.events = events
        self.echo = echo
        self.wms_output = list(wms_output)
        self.not_ready = not_ready
        self.spans = dict(spans or {})
        self.clock = clock
        self.output_at = list(output_at)
        self.spawned: list[tuple[IRI, str, list[str]]] = []

    def spawn(
        self, resource: IRI, name: str, command: list[str], *, workflows: object = ()
    ) -> None:
        self.events.append(("spawn", resource))
        self.spawned.append((resource, name, command))
        self.echo(" ".join(command))
        self.echo(f"{name}  INFO:started")
        if resource == WMS:
            for line in self.wms_output:
                self.echo(f"{name}  {line}")
        local = str(resource).rsplit("#", 1)[-1]
        if "--spans" in command:
            path = Path(command[command.index("--spans") + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "".join(json.dumps(span) + "\n" for span in self.spans.get(local, [])),
                encoding="utf-8",
            )
        for seconds, participant, line in self.output_at:
            if participant != local:
                continue
            if seconds is None:
                self.echo(f"{name}  {line}")
                continue
            assert self.clock is not None, "a later line needs the run's clock"
            self.clock.at(
                seconds, lambda name=name, line=line: self.echo(f"{name}  {line}")
            )

    def watch(self) -> None:
        self.events.append(("watch",))

    def wait_until_ready(self) -> None:
        self.events.append(("ready",))
        if self.not_ready is not None:
            from jms_kapps.common.supervision import NotReady

            raise NotReady(self.not_ready)

    def stop_all(self) -> None:
        self.events.append(("stop all",))

    def may_not_have_deregistered(self) -> set[IRI]:
        return set()


class FakeSpawnRate:
    """The WarehouseManagementSystem's spawn rate workflow: each rate set is an event, with the
    clock's time."""

    def __init__(self, events: Events, clock: FakeClock) -> None:
        self.events = events
        self.clock = clock

    def set(self, endpoint: str, boxes_per_minute: float) -> float:
        self.events.append(
            ("spawn rate", boxes_per_minute, self.clock.seconds, endpoint)
        )
        return boxes_per_minute


def nanoseconds_at(clock: FakeClock, seconds: float) -> int:
    """The clock's time at `seconds`, in nanoseconds since the epoch, as a span holds it."""
    return int(clock.start.timestamp()) * 1_000_000_000 + round(seconds * 1_000_000_000)


def store_span(
    clock: FakeClock,
    participant: str,
    at: float,
    seconds: float,
    *,
    kind: str = "write",
    text: str = "",
    outcome: str | None = "accepted",
    refusal: str | None = None,
    reason: str | None = None,
    writes: Sequence[tuple[str, str]] = (),
    receipt_written: str | None = None,
    receipt_asked: str | None = None,
    receipt_found: bool | None = None,
    error: str | None = None,
) -> dict[str, object]:
    """One line of a participant's spans' file: a store request's span, as the wrapper's hooks
    tag it, that starts at the clock's `at` and lasts `seconds`. `writes` are the statuses it
    writes, each an Operation's local name and a status. An `outcome` of None is a request that
    got no answer, and `error` the error its span ended with."""
    attributes: dict[str, object] = {
        "store.request": True,
        "store.kind": kind,
        "store.method": "POST",
        "store.path": "repositories/eval-fake/statements"
        if kind == "write"
        else "repositories/eval-fake",
        "store.request_bytes": len(text.encode()),
        "store.text": text,
    }
    if writes:
        attributes["store.operation"] = [f"{FCI}{operation}" for operation, _ in writes]
        attributes["store.box"] = [""] * len(writes)
        attributes["store.status_written"] = [status for _, status in writes]
    if receipt_written:
        attributes["store.receipt_written"] = receipt_written
    if receipt_asked:
        attributes["store.receipt_asked"] = receipt_asked
    if receipt_found is not None:
        attributes["store.receipt_found"] = receipt_found
    if outcome is not None:
        attributes["store.outcome"] = outcome
        attributes["store.status_code"] = 200 if outcome == "accepted" else 500
        attributes["store.response_bytes"] = 0
    if refusal is not None:
        attributes["store.refusal"] = refusal
        attributes["store.reason"] = reason
    return _span(clock, participant, "POST", at, seconds, attributes, error)


def commit_span(
    clock: FakeClock,
    participant: str,
    at: float,
    seconds: float,
    *,
    error: str | None = None,
) -> dict[str, object]:
    """One line of a participant's spans' file: a commit of the object-graph mapper."""
    return _span(clock, participant, "kapps_ogm.OGM.commit", at, seconds, {}, error)


def _span(
    clock: FakeClock,
    participant: str,
    name: str,
    at: float,
    seconds: float,
    attributes: dict[str, object],
    error: str | None,
) -> dict[str, object]:
    start = nanoseconds_at(clock, at)
    return {
        "participant": participant,
        "name": name,
        "kind": "CLIENT",
        "trace_id": "0" * 32,
        "span_id": f"{start:016x}"[-16:],
        "parent_id": None,
        "start": start,
        "end": start + round(seconds * 1_000_000_000),
        "status": "ERROR" if error else "UNSET",
        "status_description": None,
        "attributes": attributes,
        "events": [
            {
                "name": "exception",
                "time": start,
                "attributes": {
                    "exception.type": error.split(":")[0],
                    "exception.message": error,
                },
            }
        ]
        if error
        else [],
    }
