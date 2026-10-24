"""One UC2 run of the bench launcher, against stand-ins only.

The run gets a stand-in of GraphDB's REST API, a bench graph whose seed is the real one, writing
to a stand-in client, and whose reads answer what a test gives it, the run core's real start with
a stand-in supervisor that starts no process, a stand-in of the WarehouseManagementSystem's spawn
rate workflow, and a clock that moves only when the run waits. Each of them writes what it is asked
into one list of events, in order. Nothing reaches a store, a process or a network.
"""

from __future__ import annotations

import json
import os
import signal
import tomllib
from dataclasses import replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import pytest
from _evaluation_approvals import TWO_HEAVY_SPEC, covered_for
from _evaluation_fakes import (
    PASSWORD,
    SPAWN_RATE_ENDPOINT,
    USERNAME,
    Events,
    FakeBenchGraph,
    FakeClock,
    FakeGraphDB,
    FakeSpawnRate,
    FakeSupervisor,
    other_repositories_named,
)
from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.exceptions import (
    AuthenticationError,
    ShaclValidationError,
)

from jms_kapps.evaluation import client_load
from jms_kapps.evaluation.approval import Covered, HeavyRunRefused
from jms_kapps.evaluation.bench import READ_NAMES, GraphDBBenchGraph, TransportEnd, run
from jms_kapps.evaluation.configuration import (
    DEFAULT_BASE_SEED,
    KEYS,
    Configuration,
    pick_seed,
    read_configuration,
    resolve,
)
from jms_kapps.evaluation.prefill import transactions, write_prefill
from jms_kapps.evaluation.record import INDEX_SOURCE, ITERATION
from jms_kapps.evaluation.seed import PREFILL_TRANSACTION_STATEMENTS, PrefillNotLoaded
from jms_kapps.evaluation.wrapper import wrapped
from jms_kapps.uc2.history import ENABLE_HISTORY
from jms_kapps.uc2.process import MODULE_WORKFLOWS, module_command
from jms_kapps.uc2.run_core import Participant, participants_of
from jms_kapps.uc2.strategies import REAL_MODULE_STRATEGY, REAL_WMS_STRATEGY
from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.transport import status_record
from jms_kapps.uc2.vocabulary import FCI_NS, WMS, local_name
from jms_kapps.uc2.wms import (
    SpawnBoxResult,
    refused_record,
    seeded_choice,
    spawned_record,
)
from jms_kapps.uc2.wms_process import WMS_WORKFLOWS, wms_command, wms_parser

CROSS = Configuration(
    topology="cross",
    prefill=None,
    module_strategy="real",
    wms_strategy="real",
    shacl=True,
    collect_history=False,
    boxes_per_minute=6.0,
    warm_up_seconds=0.0,
    window_seconds=60.0,
    drain_bound_seconds=120.0,
    matcher_interval_seconds=2.0,
    transit_seconds=3.0,
    retry_seconds=1.0,
    wrapper=True,
    heavy=False,
)

# A watch interval no test run reaches: only the watch's first sample is taken.
NO_WATCH_INTERVAL = 1e9

STRATEGIES = {
    "module_strategies": {"real": REAL_MODULE_STRATEGY},
    "wms_strategies": {"real": REAL_WMS_STRATEGY},
}


def wms_line(record: str) -> str:
    """A record of the WarehouseManagementSystem, as its process writes it to its output."""
    return f"INFO:jms_kapps.uc2.wms:{record}"


def queued_line(transport: str) -> str:
    """The record of the WarehouseManagementSystem that says the transport is queued."""
    return wms_line(
        status_record(WMS, IRI(transport, base=FCI_NS), OperationStatus.QUEUED)
    )


class Bench:
    """One run of the bench launcher against stand-ins, and what each saw."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        store: FakeGraphDB | None = None,
        wms_output: list[str] | None = None,
        not_ready: str | None = None,
        failing_rate: bool = False,
        spans: dict[str, list[dict]] | None = None,
        output_at: list[tuple[float, str, str]] | None = None,
        queued: list[str] | None = None,
        **graph: object,
    ) -> None:
        self.events = Events()
        self.runs = tmp_path / "runs"
        self.store = store if store is not None else FakeGraphDB()
        self.store.events = self.events
        self.server = self.store.server()
        self.clock = FakeClock(self.events)
        self.spawn_rate = FakeSpawnRate(self.events, self.clock)
        if failing_rate:
            self.spawn_rate.set = self._failing_set  # type: ignore[method-assign]
        self.graph_options = graph
        # The WarehouseManagementSystem logs each transport it opens as queued: by default those
        # of the graph's `ends` and the one that stays open.
        ends: list[TransportEnd] = graph.get("ends") or []  # type: ignore[assignment]
        self.queued = (
            queued
            if queued is not None
            else [end.transport for end in ends] + ["Transport99"]
        )
        self.wms_output = [queued_line(name) for name in self.queued] + (
            wms_output or []
        )
        self.not_ready = not_ready
        self.spans = spans or {}
        self.output_at = output_at or []
        self.graphs: list[tuple[str, Path | None, bool]] = []
        self.supervisors: list[FakeSupervisor] = []
        self.printed: list[str] = []

    def _failing_set(self, endpoint: str, boxes_per_minute: float) -> float:
        self.events.append(
            ("spawn rate", boxes_per_minute, self.clock.seconds, endpoint)
        )
        if boxes_per_minute:
            raise RuntimeError("the WMS did not answer")
        return boxes_per_minute

    def graph_for(
        self, repository: str, prefill: Path | None, with_shapes: bool
    ) -> FakeBenchGraph:
        self.graphs.append((repository, prefill, with_shapes))
        return FakeBenchGraph(
            self.events,
            prefill=prefill,
            with_shapes=with_shapes,
            clock=self.clock,
            **self.graph_options,  # type: ignore[arg-type]
        )

    def supervisor_for(self, services: object, echo) -> FakeSupervisor:
        supervisor = FakeSupervisor(
            self.events,
            echo,
            wms_output=self.wms_output,
            not_ready=self.not_ready,
            spans=self.spans,
            clock=self.clock,
            output_at=self.output_at,
        )
        self.supervisors.append(supervisor)
        return supervisor

    def run(self, configuration: Configuration = CROSS, **options: object) -> dict:
        # Out of the server watch's interval, the tests of the other steps see its first sample
        # only: the sleeps of a run stay whole. The tests of the watch set their own.
        options.setdefault("watch_interval_seconds", NO_WATCH_INTERVAL)
        return run(
            configuration,
            runs=self.runs,
            server=self.server,
            graph_for=self.graph_for,
            spawn_rate=self.spawn_rate,
            supervisor_for=self.supervisor_for,  # type: ignore[arg-type]
            clock=self.clock,
            echo=self.printed.append,
            **options,  # type: ignore[arg-type]
        )

    def folder(self, record: dict) -> Path:
        return self.runs / record["run_id"]


def test_the_spawn_rate_is_set_at_the_window_s_start_and_to_0_at_its_end(tmp_path):
    bench = Bench(tmp_path, open_reads=3)

    record = bench.run()

    rates = [e for e in bench.events if e[0] == "spawn rate"]
    start = rates[0][2]
    assert rates == [
        ("spawn rate", 6.0, start, SPAWN_RATE_ENDPOINT),
        ("spawn rate", 0, start + CROSS.window_seconds, SPAWN_RATE_ENDPOINT),
    ]
    assert record["outcome"] == "completed"
    set_actions = [a for a in record["actions"] if a["action"] == "spawn rate set"]
    assert [a["boxes_per_minute"] for a in set_actions] == [6.0, 0]
    assert record["window"] == {
        "start": set_actions[0]["at"],
        "end": set_actions[1]["at"],
    }


def test_nothing_is_written_to_the_graph_from_the_window_s_start_to_the_drain_s_end(
    tmp_path,
):
    """The drain reads the graph until no transport is open; the stop comes after it."""
    bench = Bench(tmp_path, open_reads=3)

    bench.run()

    during = bench.events.between(("spawn rate", 6.0), ("stop all",))
    assert [e for e in during if e[0] == "write"] == []
    assert ("read", "transports named", ("Transport99",)) in during
    assert [e for e in bench.events if e[0] == "write"], (
        "the seed writes, before the window"
    )


def test_the_drain_waits_until_no_transport_is_open_reading_every_two_seconds(tmp_path):
    bench = Bench(tmp_path, open_reads=3)

    record = bench.run()

    after_window = bench.events.between(("spawn rate", 0), ("stop all",))
    # Then one wait for the log's last lines before "none is open" is final.
    assert [e for e in after_window if e[0] == "sleep"] == [("sleep", 2.0)] * 3 + [
        ("sleep", 1.0)
    ]
    [drained] = [a for a in record["actions"] if a["action"] == "drain ended"]
    assert drained["open_transports"] == 0


def test_the_drain_ends_at_its_bound_with_the_transports_still_open(tmp_path):
    bench = Bench(tmp_path, open_reads=None)

    record = bench.run()

    after_window = bench.events.between(("spawn rate", 0), ("stop all",))
    assert (
        sum(e[1] for e in after_window if e[0] == "sleep") == CROSS.drain_bound_seconds
    )
    [drained] = [a for a in record["actions"] if a["action"] == "drain ended"]
    assert drained["open_transports"] == 1
    assert record["transports"]["open"] == [
        {"transport": "Transport99", "status": "running", "place": "Module3"}
    ]


def test_the_prefill_loads_after_the_reset_and_before_the_first_participant_starts(
    tmp_path,
):
    """The prefill generator's file, loaded in its transactions of whole boxes, between the reset
    and the history collection, before the first participant."""
    prefill = tmp_path / "prefill.ttl"
    write_prefill(prefill, topology="cross", size=25_000, seed=7)
    bench = Bench(tmp_path)

    record = bench.run(
        Configuration(**(vars(CROSS) | {"prefill": prefill, "collect_history": True}))
    )

    expected = list(transactions(prefill, statements=PREFILL_TRANSACTION_STATEMENTS))
    assert len(expected) == 3
    imports = [e for e in bench.events if e[:2] == ("write", "import")]
    assert [e[2] for e in imports] == expected
    reset = bench.events.index_of("write", "reset")
    first = bench.events.index(imports[0])
    last = bench.events.index(imports[-1])
    history = bench.events.index_of("write", "query", ENABLE_HISTORY)
    first_spawn = bench.events.index_of("spawn")
    assert reset < first <= last < history < first_spawn
    # The reset itself leaves the history alone: it is turned on after the prefill.
    assert bench.events[reset][3]["collect_history"] is False
    header = prefill.read_text().splitlines()[: len(record["prefill"]["header"])]
    assert record["prefill"]["header"] == header
    assert "# seed: 7" in header
    assert len(record["prefill"]["sha256"]) == 64
    assert record["run_settings"]["prefill_transaction_statements"] == 10_000


def test_the_run_starts_the_modules_and_the_wms_on_its_own_repository_and_no_monitor(
    tmp_path,
):
    bench = Bench(tmp_path)

    record = bench.run()

    repository = record["repository"]
    assert repository.startswith("eval-") and repository.endswith(record["run_id"])
    assert bench.graphs == [(repository, None, True)]
    [supervisor] = bench.supervisors
    cross = shipped_topology("cross")
    assert [r for r, _, _ in supervisor.spawned] == [
        *(m.resource for m in cross.modules),
        WMS,
    ]
    for _, _, command in supervisor.spawned:
        assert command[command.index("--dataset") + 1] == repository
        assert command[1:3] == ["-m", "jms_kapps.evaluation.wrapper"]
    # The run core's command lines, with every run setting at its default, each through the
    # wrapper, which writes its spans into the run's folder.
    spans = bench.folder(record) / "spans"
    assert participants_of(
        cross,
        module_command=lambda resource: wrapped(
            module_command(resource, repository),
            spans / f"{local_name(resource)}.jsonl",
        ),
        wms_command=wrapped(
            wms_command(repository, pick_seed=pick_seed(DEFAULT_BASE_SEED, 1)),
            spans / "WarehouseManagementSystem1.jsonl",
        ),
    ) == [
        Participant(r, n, c, w)
        for (r, n, c), w in zip(
            supervisor.spawned,
            [*(MODULE_WORKFLOWS for _ in cross.modules), WMS_WORKFLOWS],
            strict=True,
        )
    ]
    assert [p["command"] for p in record["participants"]] == [
        c for _, _, c in supervisor.spawned
    ]
    assert bench.events.index_of("ready") < bench.events.index_of("spawn rate")


def test_each_participant_s_output_is_kept_in_the_run_s_folder(tmp_path):
    """Each line stamped with the time it arrived, the time and two spaces before it."""
    bench = Bench(tmp_path, queued=[], wms_output=["INFO:something the WMS said"])

    record = bench.run()

    output = bench.folder(record) / "output"
    started = bench.clock.start.isoformat()
    assert (output / "WarehouseManagementSystem1.log").read_text().splitlines() == [
        f"{started}  INFO:started",
        f"{started}  INFO:something the WMS said",
    ]
    assert (output / "Module1.log").read_text().splitlines() == [
        f"{started}  INFO:started"
    ]
    harness = (output / "harness.log").read_text()
    assert "--wms-strategy real" in harness and "spawn rate set" in harness


@pytest.mark.parametrize(
    ("failure", "options"),
    [
        ("a participant did not turn live", {"not_ready": "Module 1 is failed"}),
        ("the WMS did not answer", {"failing_rate": True}),
        (
            "the store answers another ruleset",
            {"store": FakeGraphDB(ruleset="owl-max")},
        ),
    ],
)
def test_the_repository_is_deleted_after_a_failed_run(tmp_path, failure, options):
    bench = Bench(tmp_path, **options)

    record = bench.run()

    assert record["outcome"].startswith("failed")
    assert bench.store.created() == []
    assert ("repository deleted", record["repository"]) in bench.events
    assert [a["action"] for a in record["actions"]][-2:] == [
        "repository deleted",
        "run ended",
    ]
    assert json.loads((bench.folder(record) / "record.json").read_text()) == record


def test_a_failed_window_still_sets_the_spawn_rate_to_0(tmp_path):
    bench = Bench(tmp_path, failing_rate=True)

    bench.run()

    assert [e[1] for e in bench.events if e[0] == "spawn rate"] == [6.0, 0]


def test_the_record_holds_each_transport_end_with_place_and_reason_and_each_refused_spawn(
    tmp_path,
):
    refused = SpawnBoxResult(
        accepted=False,
        reason="Module1 refused to reserve: occupied",
        origin="http://w3id.org/circularfactory/FlexConveyorInstances#Module1",
        destination="http://w3id.org/circularfactory/FlexConveyorInstances#Module4",
    )
    busy = SpawnBoxResult(accepted=False, reason="every origin has a spawn under way")
    bench = Bench(
        tmp_path,
        ends=[
            TransportEnd("Transport1001", "done", "Module4", None),
            TransportEnd(
                "Transport1002", "failed", "Module3", "the system is stopping"
            ),
            TransportEnd(
                "Transport1003", "failed", "Module5", "Module4 is not reachable"
            ),
            TransportEnd("Transport1004", "queued", "Module2", None),
            TransportEnd("Transport1005", "done", "Module5", None),
        ],
        open_reads=0,
        wms_output=[
            wms_line(spawned_record(WMS, *_three_modules())),
            wms_line(refused_record(WMS, refused)),
            wms_line(refused_record(WMS, busy)),
            wms_line(refused_record(WMS, busy)),
            "WARNING:jms_kapps.uc2.wms:a warning, which is no spawn",
        ],
    )

    record = bench.run(Configuration(**(vars(CROSS) | {"drain_bound_seconds": 0.0})))

    assert record["transports"] == {
        "queued_by_the_wms": 6,
        "queued_not_in_the_graph": ["Transport99"],
        "done": [
            {"transport": "Transport1001", "place": "Module4"},
            {"transport": "Transport1005", "place": "Module5"},
        ],
        "failed": [
            {
                "transport": "Transport1002",
                "place": "Module3",
                "reason": "the system is stopping",
            },
            {
                "transport": "Transport1003",
                "place": "Module5",
                "reason": "Module4 is not reachable",
            },
        ],
        "open": [
            {"transport": "Transport1004", "status": "queued", "place": "Module2"}
        ],
    }
    # The run's transports only: those the WarehouseManagementSystem's log named.
    names = {e[2] for e in bench.events if e[:2] == ("read", "transports named")}
    assert all(set(read) <= set(bench.queued) for read in names)
    assert record["spawns"] == {
        "spawned": 1,
        "refused": [
            {
                "reason": "Module1 refused to reserve: occupied",
                "origin": "Module1",
                "destination": "Module4",
            },
            {
                "reason": "every origin has a spawn under way",
                "origin": None,
                "destination": None,
            },
            {
                "reason": "every origin has a spawn under way",
                "origin": None,
                "destination": None,
            },
        ],
        "refused_by_reason": {
            "every origin has a spawn under way": 2,
            "Module1 refused to reserve: occupied": 1,
        },
    }


def _three_modules():
    from jms_kapps.uc2.vocabulary import module_iri

    return module_iri(7), module_iri(1), module_iri(4)


def test_the_record_states_iteration_one_and_its_configuration_runs_again(tmp_path):
    bench = Bench(tmp_path)

    record = bench.run()

    assert record["run_kind"] == "uc2"
    assert record["iteration"] == ITERATION == "iteration one"
    on_disk = (bench.folder(record) / "record.json").read_text()
    assert "iteration one" in on_disk
    again = resolve(
        tomllib.loads(json.loads(on_disk)["configuration"]),
        base=tmp_path,
        **STRATEGIES,
    )
    # The one value the run fills in: its pick seed.
    assert again == replace(CROSS, wms_pick_seed=pick_seed(DEFAULT_BASE_SEED, 1))
    beside = bench.folder(record) / "configuration.toml"
    assert beside.read_text() == record["configuration"]
    assert read_configuration(beside, **STRATEGIES) == again


def test_the_record_holds_the_facts_and_the_run_settings(tmp_path):
    bench = Bench(tmp_path)

    record = bench.run()

    # The transit time and the retry wait are keys of the configuration now.
    assert "transit_seconds" not in record["run_settings"]
    assert "retry_seconds" not in record["run_settings"]
    assert record["run_settings"]["wms_pick_seed"] == pick_seed(DEFAULT_BASE_SEED, 1)
    assert record["run_settings"]["deadlock_alarm_seconds"] == 300.0
    assert record["run_settings"]["heartbeat_interval_seconds"] == 30.0
    assert record["run_settings"]["activity_feed"] is True
    assert record["software"]["jms-kapps"]["version"]
    # The middleware from a git install has a commit; from a package index it has a version only.
    middleware = record["software"]["kapps-semantic-middleware"]
    assert middleware["version"] == metadata.version("kapps-semantic-middleware")
    assert middleware["commit"] or middleware["source"] == INDEX_SOURCE
    assert record["software"]["python"]["version"]
    assert record["client"]["logical_cpus"] and record["client"]["round_trip_ms"]
    server = record["server"]
    assert server["url"] == "http://graphdb.test"
    assert server["version"]["answer"]["productVersion"] == "10.8.5"
    assert server["repository_config"]["ruleset"] == "rdfsplus-optimized"
    for view in (
        "repositories_at_the_start",
        "resources_at_the_start",
        "repositories_at_the_window_start",
        "resources_at_the_window_start",
        "resources_at_the_drain_end",
        "repository_monitor_at_the_window_start",
        "repository_monitor_at_the_drain_end",
        "repositories_at_the_drain_end",
    ):
        assert server[view]["status"] == 200, view
    assert record["registered_services_after_the_stop"] == []


def test_the_record_of_the_server_requests_shows_that_only_monitoring_reads_name_another_repository(
    tmp_path,
):
    bench = Bench(tmp_path)

    record = bench.run()

    requests = record["server"]["requests"]
    assert requests == bench.store.requests
    others = other_repositories_named(requests, record["repository"])
    assert {r.split("/")[-1] for r in others} == {"jms-kapps", "kapps-demo", "Tests"}
    assert all(r.startswith("GET rest/monitor/repository/") for r in others)
    # The run's own repository is created, sized and deleted, and no other is.
    assert "POST rest/repositories" in requests
    assert f"DELETE rest/repositories/{record['repository']}" in requests
    assert [r for r in requests if r.startswith(("POST", "DELETE", "PUT"))] == [
        "POST rest/login",
        "POST rest/repositories",
        f"DELETE rest/repositories/{record['repository']}",
    ]


def test_neither_the_record_nor_its_configuration_holds_a_secret(tmp_path):
    bench = Bench(
        tmp_path,
        store=FakeGraphDB(),
    )
    bench.server.url = f"http://{USERNAME}:{PASSWORD}@graphdb.test"

    record = bench.run()

    written = (bench.folder(record) / "record.json").read_text()
    for secret in (PASSWORD, USERNAME):
        assert secret not in written
        assert secret not in record["configuration"]
    assert bench.store.logins == [{"username": USERNAME, "password": PASSWORD}]


def test_without_the_approval_switch_a_heavy_run_is_refused_before_anything_is_sent(
    tmp_path,
):
    bench = Bench(tmp_path)

    with pytest.raises(HeavyRunRefused, match="any person may approve"):
        bench.run(Configuration(**(vars(CROSS) | {"heavy": True})))

    assert bench.store.requests == []
    assert bench.events == []
    assert not bench.runs.exists()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (
            datetime(2026, 10, 11, 12, 0, 1, tzinfo=UTC),
            datetime(2026, 10, 12, tzinfo=UTC),
        ),
        (datetime(2026, 10, 10, tzinfo=UTC), datetime(2026, 10, 11, 12, 0, tzinfo=UTC)),
    ],
    ids=["before the period", "at its end"],
)
def test_a_heavy_run_outside_the_approval_s_period_is_refused_before_anything_is_sent(
    tmp_path, start, end
):
    """The fake clock starts at 2026-10-11 12:00 UTC; the period starts after it, or ends at it
    (the end is not in the period). The record is covered at the period's start, as the
    command does at its own time."""
    bench = Bench(tmp_path)
    covered, configuration = covered_for(tmp_path, start=start, end=end)

    with pytest.raises(HeavyRunRefused, match="outside the period"):
        bench.run(configuration, approval=covered)

    assert bench.store.requests == []
    assert bench.events == []
    assert not bench.runs.exists()


def test_a_heavy_run_given_a_raw_approval_is_refused_before_anything_is_sent(tmp_path):
    bench = Bench(tmp_path)
    covered, configuration = covered_for(tmp_path)

    with pytest.raises(HeavyRunRefused, match="nothing has checked"):
        bench.run(configuration, approval=covered.approval)  # type: ignore[arg-type]

    assert bench.store.requests == [] and not bench.runs.exists()


def test_a_covered_record_cannot_be_built_by_hand(tmp_path):
    covered, _ = covered_for(tmp_path)

    with pytest.raises(HeavyRunRefused, match="made by approval.cover only"):
        Covered(
            approval=covered.approval,
            spec_key=covered.spec_key,
            configuration_key=covered.configuration_key,
            fingerprint=covered.fingerprint,
        )


def test_a_heavy_run_of_another_configuration_than_the_covered_one_is_refused(tmp_path):
    """Configuration 1 of the spec is covered; the run is configuration 2 of it."""
    bench = Bench(tmp_path)
    covered, _ = covered_for(tmp_path, number=1)
    _, other = covered_for(tmp_path, number=2)

    with pytest.raises(HeavyRunRefused, match="another configuration"):
        bench.run(other, approval=covered)

    assert bench.store.requests == [] and not bench.runs.exists()


def test_a_heavy_run_given_the_covered_record_of_another_spec_is_refused(tmp_path):
    """The other spec has its own approval and covers its configuration 1, which is not this one:
    a covered record names one configuration, whatever spec it comes from."""
    bench = Bench(tmp_path)
    other_text = TWO_HEAVY_SPEC.replace("window_seconds = 60", "window_seconds = 90")
    covered, _ = covered_for(tmp_path, spec_text=other_text, name="other.toml")
    _, configuration = covered_for(tmp_path, number=1)

    with pytest.raises(HeavyRunRefused, match="another configuration"):
        bench.run(configuration, approval=covered)

    assert bench.store.requests == [] and not bench.runs.exists()


def test_with_an_approval_the_record_of_a_heavy_run_keeps_the_whole_approval(tmp_path):
    """Against stand-ins only: no test gives an approval to a run that reaches a store."""
    bench = Bench(tmp_path)
    covered, configuration = covered_for(
        tmp_path,
        start=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        end=datetime(2026, 10, 12, tzinfo=UTC),
        approver="Test Person",
        text="approved with the IAM's admins",
    )

    record = bench.run(configuration, approval=covered)

    assert record["outcome"] == "completed"
    assert record["heavy_run_approval"] == {
        "spec_key": covered.spec_key,
        "start": "2026-10-11T12:00:00+00:00",
        "end": "2026-10-12T00:00:00+00:00",
        "approver": "Test Person",
        "text": "approved with the IAM's admins",
    }
    written = json.loads((bench.folder(record) / "record.json").read_text())
    assert written["heavy_run_approval"] == record["heavy_run_approval"]


def test_a_run_at_normal_load_needs_no_approval_and_records_none(tmp_path):
    record = Bench(tmp_path).run()

    assert record["heavy_run_approval"] is None


class AnsweringDB:
    """The client of a run's repository, answering each read with the rows a test gives it, by a
    piece of the query's text. Every query is kept, with whether it was an update."""

    def __init__(self, answers: dict[str, list[dict]]) -> None:
        self.answers = answers
        self.queries: list[tuple[str, bool]] = []
        self.dataset = "eval-fake"

    def query(self, query: str, *, update: bool = False, **_: object) -> object:
        self.queries.append((query, update))
        for piece, rows in self.answers.items():
            if piece in query:
                return {"results": {"bindings": rows}}
        return {"results": {"bindings": []}}

    def graph_add(self, content: str, **_: object) -> bool:
        raise ShaclValidationError("refused (400)", report="the report", status=400)


def term(value: str, kind: str = "literal") -> dict:
    return {"type": kind, "value": value}


FCI = "http://w3id.org/circularfactory/FlexConveyorInstances#"


def test_the_bench_graph_reads_the_transports_it_is_named_and_no_others():
    db = AnsweringDB(
        {
            "VALUES": [
                {
                    "transport": term(FCI + "T-b", "uri"),
                    "status": term("failed"),
                    "place": term(FCI + "Module3", "uri"),
                    "reason": term("the system is stopping"),
                },
                {
                    "transport": term(FCI + "Box1001_transport", "uri"),
                    "status": term("done"),
                    "place": term(FCI + "Module4", "uri"),
                },
                {
                    "transport": term(FCI + "T-c", "uri"),
                    "status": term("running"),
                    "place": term(FCI + "Module2", "uri"),
                },
            ],
        }
    )
    graph = GraphDBBenchGraph(db, prefill=None)  # type: ignore[arg-type]

    names = ["Box1001_transport", "T-b", "T-c"]
    # In the order of the names, not of the answer.
    assert graph.transports_named(names) == [
        TransportEnd("Box1001_transport", "done", "Module4", None),
        TransportEnd("T-b", "failed", "Module3", "the system is stopping"),
        TransportEnd("T-c", "running", "Module2", None),
    ]
    [read] = [q for q, _ in db.queries]
    assert (
        f"VALUES ?transport {{ <{FCI}Box1001_transport> <{FCI}T-b> <{FCI}T-c> }}"
        in read
    )
    assert "FILTER (STR(?status) IN" not in read
    assert all(not update for _, update in db.queries)


def test_the_matcher_s_read_filters_the_ended_among_the_named_ones_only():
    db = AnsweringDB({})
    graph = GraphDBBenchGraph(db, prefill=None)  # type: ignore[arg-type]

    graph.ended_transports_named(["T-a"])

    [read] = [q for q, _ in db.queries]
    assert f"VALUES ?transport {{ <{FCI}T-a> }}" in read
    assert 'FILTER (STR(?status) IN ("done", "failed"))' in read


def test_a_read_that_names_no_transport_reads_nothing():
    db = AnsweringDB({})
    graph = GraphDBBenchGraph(db, prefill=None)  # type: ignore[arg-type]

    assert graph.transports_named([]) == []
    assert graph.ended_transports_named([]) == []
    assert db.queries == []


def test_a_run_with_many_transports_reads_them_in_steps_of_a_bounded_text():
    db = AnsweringDB({})
    graph = GraphDBBenchGraph(db, prefill=None)  # type: ignore[arg-type]
    names = [f"3f2b8c1e-0000-4000-8000-{n:012d}_transport" for n in range(450)]

    graph.transports_named(names)
    graph.ended_transports_named(names)

    assert len(db.queries) == 6
    assert max(len(q) for q, _ in db.queries) < 40_000
    for query, _ in db.queries:
        # Not a read of every transport or box of the store.
        assert query.count("VALUES") == 1
        assert query.count("<" + FCI) <= READ_NAMES
    read = {n for q, _ in db.queries[:3] for n in names if f"<{FCI}{n}>" in q}
    assert read == set(names)


def test_the_bench_graph_reads_the_services_still_registered():
    db = AnsweringDB(
        {
            "isServiceOf": [
                {
                    "service": FCI + "S1",
                    "resource": FCI + "Module1",
                    "address": "http://h:1",
                },
                {"service": FCI + "S2", "resource": FCI + "Module2"},
            ]
        }
    )
    graph = GraphDBBenchGraph(db, prefill=None)  # type: ignore[arg-type]

    assert graph.registered_services() == ["Module1"]


def test_a_prefill_transaction_the_store_refuses_ends_the_seed(tmp_path):
    prefill = tmp_path / "prefill.ttl"
    prefill.write_text("<urn:a> <urn:b> <urn:c> .\n", encoding="utf-8")
    reset: list[object] = []
    graph = GraphDBBenchGraph(
        AnsweringDB({}),  # type: ignore[arg-type]
        prefill=prefill,
        seed_uc2=lambda *a, **k: reset.append(a),
    )

    with pytest.raises(
        PrefillNotLoaded, match="transaction 1 of the prefill"
    ) as caught:
        graph.seed(shipped_topology("cross"), 0, collect_history=False)
    assert "the report" in str(caught.value)
    assert str(prefill) in str(caught.value)
    assert isinstance(caught.value.__cause__, ShaclValidationError)
    assert reset


def test_another_interface_error_of_a_prefill_write_reaches_the_caller(tmp_path):
    prefill = tmp_path / "prefill.ttl"
    prefill.write_text("<urn:a> <urn:b> <urn:c> .\n", encoding="utf-8")

    class Unauthorised(AnsweringDB):
        def graph_add(self, content: str, **_: object) -> bool:
            raise AuthenticationError("no login", status=401)

    graph = GraphDBBenchGraph(
        Unauthorised({}),  # type: ignore[arg-type]
        prefill=prefill,
        seed_uc2=lambda *a, **k: None,
    )

    with pytest.raises(AuthenticationError):
        graph.seed(shipped_topology("cross"), 0, collect_history=False)


@pytest.mark.posix(reason="sends SIGTERM to this process")
def test_a_stop_signal_ends_the_run_as_interrupted_and_its_repository_is_deleted(
    tmp_path,
):
    """SIGTERM, as `kill` sends it, during the window: the spawn rate goes to 0, the participants
    stop, and the repository is deleted. A second stop signal while the run ends does not cut the
    end short."""
    store = FakeGraphDB(on_delete=lambda: os.kill(os.getpid(), signal.SIGINT))
    bench = Bench(tmp_path, store=store)
    bench.clock.at(
        CROSS.window_seconds / 2, lambda: os.kill(os.getpid(), signal.SIGTERM)
    )

    record = bench.run()

    assert record["outcome"] == "interrupted"
    assert [e[1] for e in bench.events if e[0] == "spawn rate"] == [6.0, 0]
    assert ("stop all",) in bench.events
    assert store.created() == []
    assert [a["action"] for a in record["actions"]][-2:] == [
        "repository deleted",
        "run ended",
    ]


# The warm-up: the spawn rate is on before the measured window, which starts when
# the warm-up ends. A warm-up of 0 is the run above, unchanged.
WARM_UP = Configuration(**(vars(CROSS) | {"warm_up_seconds": 30.0}))


def test_a_warm_up_sets_the_spawn_rate_at_its_start_and_the_window_starts_at_its_end(
    tmp_path,
):
    bench = Bench(tmp_path, open_reads=3)

    record = bench.run(WARM_UP)

    rates = [e for e in bench.events if e[0] == "spawn rate"]
    start = rates[0][2]
    assert rates == [
        ("spawn rate", 6.0, start, SPAWN_RATE_ENDPOINT),
        ("spawn rate", 0, start + 30.0 + 60.0, SPAWN_RATE_ENDPOINT),
    ]
    during = bench.events.between(("spawn rate", 6.0), ("spawn rate", 0))
    # The run waits through the completion matcher, which reads every 2 s.
    assert [e for e in during if e[0] == "sleep"] == [("sleep", 2.0)] * 45
    assert [e for e in during if e[:2] == ("read", "ended transports named")] == [
        ("read", "ended transports named", ("Transport99",))
    ] * 45
    actions = {a["action"]: a for a in record["actions"]}
    assert [a["action"] for a in record["actions"]].count("spawn rate set") == 2
    began = next(a for a in record["actions"] if a["action"] == "spawn rate set")
    ended = actions["warm-up ended"]
    assert ended["seconds"] - began["seconds"] == 30.0
    assert record["warm_up"] == {"start": began["at"], "end": ended["at"]}
    assert record["window"]["start"] == ended["at"]
    stopped = [a for a in record["actions"] if a["action"] == "spawn rate set"][1]
    assert record["window"]["end"] == stopped["at"]
    assert stopped["seconds"] - ended["seconds"] == CROSS.window_seconds
    assert record["outcome"] == "completed"


def test_nothing_is_written_to_the_graph_from_the_warm_up_s_start_to_the_drain_s_end(
    tmp_path,
):
    bench = Bench(tmp_path, open_reads=3)

    bench.run(WARM_UP)

    during = bench.events.between(("spawn rate", 6.0), ("stop all",))
    assert sum(e[1] for e in during if e[0] == "sleep") >= 30.0 + CROSS.window_seconds
    assert [e for e in during if e[0] == "write"] == []
    assert ("read", "transports named", ("Transport99",)) in during


def test_the_server_s_views_at_the_window_s_start_are_read_when_the_warm_up_ends(
    tmp_path,
):
    """Not at the warm-up's start: the views say what the server held when the measured window
    began."""
    bench = Bench(tmp_path)
    reads: list[float] = []
    views = bench.server.repository_monitor

    def repository_monitor(repository: str):
        if repository.startswith("eval-"):
            reads.append(bench.clock.seconds)
        return views(repository)

    bench.server.repository_monitor = repository_monitor  # type: ignore[method-assign]

    bench.run(WARM_UP)

    start = next(e for e in bench.events if e[0] == "spawn rate")[2]
    assert reads[0] == start + 30.0


def test_the_record_holds_the_warm_up_and_the_matcher_interval(tmp_path):
    bench = Bench(tmp_path)

    record = bench.run(
        Configuration(**(vars(WARM_UP) | {"matcher_interval_seconds": 2.5}))
    )

    configuration = tomllib.loads(record["configuration"])
    assert configuration["warm_up_seconds"] == 30.0
    assert configuration["matcher_interval_seconds"] == 2.5
    assert record["warm_up"]["start"] < record["warm_up"]["end"]


def test_a_run_without_a_warm_up_has_none_in_its_record_and_runs_as_before(tmp_path):
    bench = Bench(tmp_path, open_reads=1)

    record = bench.run()

    assert record["warm_up"] is None
    assert "warm-up ended" not in [a["action"] for a in record["actions"]]
    during = bench.events.between(("spawn rate", 6.0), ("spawn rate", 0))
    assert [e for e in during if e[0] == "sleep"] == [("sleep", 2.0)] * 30
    assert tomllib.loads(record["configuration"])["warm_up_seconds"] == 0.0


@pytest.mark.parametrize("shacl", [True, False], ids=["shacl-on", "shacl-off"])
def test_the_seed_sets_the_shapes_only_where_the_configuration_has_shacl(
    tmp_path, shacl
):
    """The SHACL-off twin has no SHACL validation, and refuses shapes: its seed skips them
    (`with_shapes`). The run with SHACL on seeds them, as every demo does."""
    bench = Bench(tmp_path)

    bench.run(Configuration(**(vars(CROSS) | {"shacl": shacl})))

    [graph] = bench.graphs
    assert graph[2] is shacl
    [reset] = [e for e in bench.events if e[:2] == ("write", "reset")]
    assert reset[3]["with_shapes"] is shacl


def _fake_proc(folder: Path, *, available: bool = True) -> Path:
    """A folder that stands in for `/proc`, or an empty one for a host without it."""
    folder.mkdir()
    if available:
        (folder / "stat").write_text("cpu  10 0 10 80 0 0 0 0 0 0\n", encoding="utf-8")
        (folder / "meminfo").write_text(
            "MemTotal: 8000 kB\nMemAvailable: 2000 kB\n", encoding="utf-8"
        )
    return folder


def test_the_record_holds_the_client_host_s_load_and_its_interval_as_a_run_setting(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(client_load, "PROC", _fake_proc(tmp_path / "proc"))
    bench = Bench(tmp_path)

    record = bench.run()

    assert record["run_settings"]["client_load_interval_seconds"] == 1.0
    load = record["client_load"]
    assert load["status"] == "sampled"
    assert load["interval_seconds"] == 1.0
    assert load["memory_total_bytes"] == 8000 * 1024
    assert load["samples"]
    assert {s["memory_used_bytes"] for s in load["samples"]} == {6000 * 1024}
    assert all(s["at"] for s in load["samples"])
    assert json.loads((bench.folder(record) / "record.json").read_text()) == record


def test_a_host_without_the_figures_has_a_record_that_says_not_available(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        client_load, "PROC", _fake_proc(tmp_path / "proc", available=False)
    )

    record = Bench(tmp_path).run()

    assert record["outcome"] == "completed"
    assert record["client_load"]["status"] == "not available"
    assert record["client_load"]["samples"] == []


def test_a_failed_run_keeps_the_load_it_sampled(tmp_path, monkeypatch):
    monkeypatch.setattr(client_load, "PROC", _fake_proc(tmp_path / "proc"))
    bench = Bench(tmp_path, failing_rate=True)

    record = bench.run()

    assert record["outcome"].startswith("failed")
    assert record["client_load"]["status"] == "sampled"
    assert record["client_load"]["samples"]


def test_a_run_that_failed_before_the_load_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(client_load, "PROC", _fake_proc(tmp_path / "proc"))
    bench = Bench(tmp_path, store=FakeGraphDB(ruleset="owl-max"))

    record = bench.run()

    assert record["outcome"].startswith("failed")
    assert record["client_load"] is None


def _commands(bench: Bench) -> tuple[list[list[str]], list[str]]:
    """The command lines of the modules, and the WarehouseManagementSystem's, of a run."""
    [supervisor] = bench.supervisors
    modules = [c for r, _, c in supervisor.spawned if r != WMS]
    [wms] = [c for r, _, c in supervisor.spawned if r == WMS]
    return modules, wms


def _option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def test_the_transit_time_and_the_retry_wait_of_the_configuration_are_the_participants(
    tmp_path,
):
    """The seed of the topology takes the transit time, every module's command line
    the retry wait, and the record holds both under the configuration."""
    bench = Bench(tmp_path)
    configuration = Configuration(
        **(vars(CROSS) | {"transit_seconds": 2.5, "retry_seconds": 0.25})
    )

    record = bench.run(configuration)

    [reset] = [e for e in bench.events if e[:2] == ("write", "reset")]
    assert reset[3]["transit_seconds"] == 2.5
    modules, _ = _commands(bench)
    assert len(modules) == 5
    assert all(_option(c, "--retry-time") == "0.25" for c in modules)
    saved = tomllib.loads(record["configuration"])
    assert (saved["transit_seconds"], saved["retry_seconds"]) == (2.5, 0.25)


def test_a_run_without_a_pick_seed_takes_repetition_1_of_the_default_base_seed(
    tmp_path,
):
    bench = Bench(tmp_path)
    seed = pick_seed(DEFAULT_BASE_SEED, 1)

    record = bench.run(CROSS)

    assert record["run_settings"]["wms_pick_seed"] == seed
    assert tomllib.loads(record["configuration"])["wms_pick_seed"] == seed
    _, wms = _commands(bench)
    assert _option(wms, "--pick-seed") == str(seed)


def test_a_run_takes_the_pick_seed_of_its_configuration_and_the_key_stays_as_it_was(
    tmp_path,
):
    bench = Bench(tmp_path)
    configuration = replace(CROSS, wms_pick_seed=12345)

    record = bench.run(configuration)

    assert record["run_settings"]["wms_pick_seed"] == 12345
    _, wms = _commands(bench)
    assert _option(wms, "--pick-seed") == "12345"
    # The seed is a run setting beside the configuration's keys, not one of them.
    saved = tomllib.loads(record["configuration"])
    assert "wms_pick_seed" not in KEYS and saved["wms_pick_seed"] == 12345


def test_a_saved_configuration_run_again_makes_the_same_picks(tmp_path):
    """The WMS process of the second run is given the first run's seed, and its generator makes
    the picks of the first run's."""
    first = Bench(tmp_path / "first")
    record = first.run(replace(CROSS, wms_pick_seed=987654321))
    saved = read_configuration(
        first.folder(record) / "configuration.toml", **STRATEGIES
    )
    again = Bench(tmp_path / "again")

    second = again.run(saved)

    assert second["run_settings"]["wms_pick_seed"] == 987654321
    seeds = []
    for made in (first, again):
        _, command = _commands(made)
        # Behind the wrapper's `--`: `-m jms_kapps.uc2.wms_process`, then the process's options.
        arguments = wms_parser().parse_args(command[command.index("--") + 3 :])
        seeds.append(arguments.pick_seed)
    assert seeds == [987654321, 987654321]
    options = list(range(30))
    picks = [[seeded_choice(seed)(options) for _ in range(50)] for seed in seeds]
    assert picks[0] == picks[1]
