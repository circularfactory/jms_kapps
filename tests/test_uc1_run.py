"""Offline tests of the UC1 runtime: `run_core`, the `jms-uc1` entry point, and the new
`main()` function of the screwing resource.

No GraphDB, no InfluxDB, no network, no spawned servers. What `run_core` builds is checked
directly, on the command lines it returns; what `jms-uc1` does is checked with monkeypatched
connections and a fake supervisor; and the new `main()` function is checked by running
`--help`. These tests need none of the use case's machinery, only the names and signatures the
spec fixes.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from kapps_semantic_middleware.vocabulary import CORE_NS
from kapps_triplestore_interface import IRI
from rdflib import RDF, RDFS, Graph, Literal, URIRef

from jms_kapps.common.supervision import LaunchFailed, LiveInstanceError
from jms_kapps.uc1 import run_core
from jms_kapps.uc1.anomaly_detector import RESOURCE as ANOMALY_DETECTOR_RESOURCE
from jms_kapps.uc1.run_core import participants, start_run
from jms_kapps.uc1.screwing_resource import (
    RESOURCE as SCREWING_RESOURCE_RESOURCE,
)
from jms_kapps.uc1.screwing_resource import (
    replay_by_screw,
)
from jms_kapps.uc1.seed import RECORDING_OF, SCREW_2
from jms_kapps.uc1.vocabulary import JMS, JMS_NS


class FakeSupervisor:
    """A supervisor that records its calls, and can be told to fail on one.

    `spawn`, `watch` and `stop_all` record to one list, in the order they happen, with the
    arguments the spec's table fixes. `fail_on_spawn` makes the n-th `spawn` call raise, so
    `start_run` is checked against a supervisor that stops part-way.
    """

    def __init__(self, fail_on_spawn: int | None = None) -> None:
        self.calls: list[tuple] = []
        self._fail_on_spawn = fail_on_spawn

    def spawn(
        self,
        resource,
        name: str,
        command: list[str],
        *,
        workflows=(),
    ) -> None:
        if self._fail_on_spawn is not None and len(self.calls) >= self._fail_on_spawn:
            raise RuntimeError("spawn failed")
        self.calls.append(("spawn", resource, name, command, tuple(workflows)))

    def watch(self) -> None:
        self.calls.append(("watch",))

    def stop_all(self) -> None:
        self.calls.append(("stop_all",))


def test_participants_returns_four_in_run_order_with_the_tables_commands() -> None:
    """One participant per resource, in the order the spec's table fixes, and each command on its
    module with its arguments."""
    result = participants("ds")

    assert len(result) == 4
    assert [p.resource for p in result] == [
        ANOMALY_DETECTOR_RESOURCE,
        SCREWING_RESOURCE_RESOURCE,
        JMS.Learner1,
        JMS.Planner1,
    ]
    assert len({p.resource for p in result}) == 4
    expected_modules = {
        ANOMALY_DETECTOR_RESOURCE: "jms_kapps.uc1.anomaly_detector",
        SCREWING_RESOURCE_RESOURCE: "jms_kapps.uc1.screwing_resource",
        JMS.Learner1: "jms_kapps.uc1.learner_process",
        JMS.Planner1: "jms_kapps.uc1.planner_process",
    }
    for participant in result:
        assert participant.command[0] == sys.executable
        assert participant.command[1] == "-m"
        assert participant.command[2] == expected_modules[participant.resource]
        assert participant.command[3:5] == ["--dataset", "ds"]


def test_the_planner_command_has_no_product_flag() -> None:
    """The planner finds its grinders in the graph, so its command line holds no `--product`."""
    planner = next(p for p in participants("ds") if p.resource == JMS.Planner1)

    assert "--product" not in planner.command


def test_the_screwing_resource_command_has_no_recording_flag() -> None:
    """The screwing resource of a run replays by Screw; the demo's recordings live in the seed."""
    screwing = next(
        p for p in participants("ds") if p.resource == SCREWING_RESOURCE_RESOURCE
    )

    assert "--recording" not in screwing.command


def test_each_participant_names_its_service_class() -> None:
    """Each participant's `service` is the class its middleware registers, for the page's
    "Middleware" row."""
    by_resource = {p.resource: p.service for p in participants("ds")}

    assert by_resource == {
        ANOMALY_DETECTOR_RESOURCE: JMS.AnomalyDetectorService,
        SCREWING_RESOURCE_RESOURCE: JMS.ScrewingResourceService,
        JMS.Learner1: JMS.LearnerService,
        JMS.Planner1: JMS.PlannerService,
    }


def test_options_reach_the_command_lines() -> None:
    """The learn seconds and operation timeout land after their flags, as decimals."""
    result = participants("ds", learn_seconds=3, operation_timeout=7)

    learner = next(p for p in result if p.resource == JMS.Learner1)
    assert learner.command[learner.command.index("--learn-seconds") + 1] == "3.0"
    planner = next(p for p in result if p.resource == JMS.Planner1)
    assert planner.command[planner.command.index("--operation-timeout") + 1] == "7.0"


def test_start_run_seeds_then_spawns_in_order_then_watches() -> None:
    """The seed runs first, the participants are spawned in order with their workflows, and the
    supervisor watches once, with no stop."""
    supervisor = FakeSupervisor()

    start_run(
        lambda: supervisor.calls.append(("seed",)), supervisor, participants("ds")
    )

    assert [call[0] for call in supervisor.calls] == [
        "seed",
        "spawn",
        "spawn",
        "spawn",
        "spawn",
        "watch",
    ]
    assert [call[4] for call in supervisor.calls if call[0] == "spawn"] == [
        (JMS.ClassifyWorkflow,),
        (JMS.UnscrewingWorkflow,),
        (),
        (JMS.StartUnscrewingWorkflow,),
    ]
    assert ("stop_all",) not in supervisor.calls


def test_start_run_whose_seed_raises_wraps_and_stops() -> None:
    """A failed seed is a `LaunchFailed` with the message in it, nothing is spawned, and the
    supervisor is stopped."""
    supervisor = FakeSupervisor()

    def seed() -> None:
        raise RuntimeError("boom")

    with pytest.raises(LaunchFailed) as error:
        start_run(seed, supervisor, participants("ds"))

    assert "boom" in str(error.value)
    assert not any(call[0] == "spawn" for call in supervisor.calls)
    assert ("watch",) not in supervisor.calls
    assert supervisor.calls == [("stop_all",)]


def test_start_run_whose_second_spawn_raises_stops_everything() -> None:
    """A spawn that raises turns the launch into a `LaunchFailed` and stops the supervisor."""
    supervisor = FakeSupervisor(fail_on_spawn=2)

    with pytest.raises(LaunchFailed) as error:
        start_run(lambda: None, supervisor, participants("ds"))

    assert "spawn failed" in str(error.value)
    assert [call[0] for call in supervisor.calls] == [
        "spawn",
        "spawn",
        "stop_all",
    ]
    assert ("watch",) not in supervisor.calls


def test_replay_by_screw_replays_the_named_recording() -> None:
    """`replay_by_screw(RECORDING_OF)` replays `missing_screw` for `SCREW_2`, as its `source`
    says."""
    frame = replay_by_screw(RECORDING_OF)(SCREW_2)

    assert frame.source.endswith("/missing_screw")


def test_replay_by_screw_raises_for_an_unknown_screw() -> None:
    """A Screw that the demo names no recording for raises `ValueError`, and the operation then
    ends `failed` with that reason."""
    with pytest.raises(ValueError):
        replay_by_screw(RECORDING_OF)(IRI("UnknownScrew", base=JMS_NS))


@pytest.mark.parametrize(
    "module",
    ["jms_kapps.uc1.screwing_resource", "jms_kapps.uc1.anomaly_detector"],
)
def test_help_of_the_new_main_functions(module: str) -> None:
    """Each new `main()` runs with `--help`, and the screwing resource takes `--resource`."""
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0
    assert "--resource" in result.stdout
    if module == "jms_kapps.uc1.screwing_resource":
        assert "--recording" not in result.stdout


@pytest.mark.parametrize(
    "argv",
    [["--learn-seconds", "0"], ["--operation-timeout", "-1"]],
)
def test_invalid_options_fail_before_any_connection(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-positive or infinite time value is a parser error, before anything is read."""
    from jms_kapps.uc1 import __main__ as main_module

    def fail_connect(*args, **kwargs):
        raise AssertionError("connect must not be called")

    monkeypatch.setattr(main_module, "connect", fail_connect)

    with pytest.raises(SystemExit) as error:
        main_module.main(argv)

    assert error.value.code == 2


def test_main_refuses_a_live_instance_and_prints_the_force_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A live service turns `main` into exit code 1 and a message that names `--force`."""
    from jms_kapps.uc1 import __main__ as main_module

    monkeypatch.setattr(main_module, "connect", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "reader", lambda *args, **kwargs: object())

    def refuse(*args, **kwargs):
        raise LiveInstanceError("x is live.")

    monkeypatch.setattr(main_module, "refuse_if_live", refuse)

    code = main_module.main(["--page-port", "0"])

    assert code == 1
    assert "--force" in capsys.readouterr().err


def test_the_seed_writes_the_resources_the_ontology_does_not_declare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reset gets the screwing resource, a `cfc:Resource`, and the anomaly detector, an
    `:AnomalyDetector` (a `cfc:Resource` and a PROV agent, jms_kapps 59), each with its
    label, and the demo is written after it."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        run_core,
        "reset_uc1",
        lambda db, *, individuals="": calls.append(("reset", individuals)),
    )
    monkeypatch.setattr(run_core, "seed_demo", lambda ogm: calls.append(("demo",)))
    monkeypatch.setattr(run_core, "OGM", lambda db: db)

    run_core.seed(object())

    assert [call[0] for call in calls] == ["reset", "demo"]
    graph = Graph().parse(data=calls[0][1], format="turtle")
    for resource, label, resource_class in (
        (ANOMALY_DETECTOR_RESOURCE, "Anomaly detector", JMS.AnomalyDetector),
        (
            SCREWING_RESOURCE_RESOURCE,
            "Screwing resource",
            IRI("Resource", base=CORE_NS),
        ),
    ):
        assert (URIRef(str(resource)), RDF.type, URIRef(str(resource_class))) in graph
        assert (URIRef(str(resource)), RDFS.label, Literal(label, lang="en")) in graph


def test_main_seed_calls_the_seed_once_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main(["seed"])` seeds once, starts no supervisor and binds no socket, and its stdout names
    `Grinder099` and holds one command line without `--product`."""
    from jms_kapps.uc1 import __main__ as main_module

    calls: list[tuple] = []
    monkeypatch.setattr(main_module, "connect", lambda dataset: dataset)
    monkeypatch.setattr(
        main_module,
        "endpoint_for",
        lambda dataset: type("Endpoint", (), {"url": "http://store.test"})(),
    )
    monkeypatch.setattr(main_module, "reader", lambda *args, **kwargs: object())

    def refuse(*args, **kwargs):
        pass

    monkeypatch.setattr(main_module, "refuse_if_live", refuse)
    monkeypatch.setattr(
        main_module, "run_core_seed", lambda db: calls.append(("seed", db))
    )
    monkeypatch.setattr(
        main_module, "participants", lambda dataset: run_core.participants(dataset)
    )

    def fail_supervisor(*args, **kwargs):
        raise AssertionError("Supervisor must not be constructed")

    monkeypatch.setattr(main_module, "Supervisor", fail_supervisor)
    monkeypatch.setattr(
        main_module,
        "bind_socket",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("bind_socket must not be called")
        ),
    )

    code = main_module.main(["seed"])

    assert code == 0
    assert calls == [("seed", "jms-kapps")]  # connect returns the dataset
    out = capsys.readouterr().out
    assert "Grinder099" in out
    planner_lines = [
        line for line in out.splitlines() if "jms_kapps.uc1.planner_process" in line
    ]
    assert len(planner_lines) == 1
    assert "--product" not in planner_lines[0]


def test_main_seed_refuses_a_live_instance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A live service turns `main(["seed"])` into exit code 1 and a message that names
    `--force`, and the seed is not called."""
    from jms_kapps.uc1 import __main__ as main_module

    monkeypatch.setattr(main_module, "connect", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "reader", lambda *args, **kwargs: object())

    def refuse(*args, **kwargs):
        raise LiveInstanceError("x is live.")

    monkeypatch.setattr(main_module, "refuse_if_live", refuse)
    monkeypatch.setattr(
        main_module,
        "run_core_seed",
        lambda db: (_ for _ in ()).throw(AssertionError("seed must not be called")),
    )

    code = main_module.main(["seed"])

    assert code == 1
    assert "--force" in capsys.readouterr().err


def test_main_seed_passes_the_dataset_to_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main(["seed", "--dataset", "x"])` passes `x` to `connect`."""
    from jms_kapps.uc1 import __main__ as main_module

    seen: list[str] = []
    monkeypatch.setattr(main_module, "connect", lambda dataset: seen.append(dataset))
    monkeypatch.setattr(
        main_module,
        "endpoint_for",
        lambda dataset: type("Endpoint", (), {"url": "http://store.test"})(),
    )
    monkeypatch.setattr(main_module, "reader", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "refuse_if_live", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "run_core_seed", lambda db: None)
    monkeypatch.setattr(main_module, "participants", lambda dataset: [])

    code = main_module.main(["seed", "--dataset", "x"])

    assert code == 0
    assert seen == ["x"]
