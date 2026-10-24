"""The run core: start the participants of a topology, wait until each is ready, and stop them.

The demo Launcher and the bench launcher both run on it. A run seeds its topology through its
graph, then starts one process for each module of the topology and one for the
WarehouseManagementSystem, the participants of every run (`participants_of`), in that order, under
one `Supervisor` (`start_run`). A caller adds participants of its own: the demo Launcher adds the
monitor, and a run without the Launcher starts none. The caller names the strategy of the modules
and the strategy of the WarehouseManagementSystem, `real` unless it names others, and the run core
puts them on the participants' command lines, `--module-strategy` and `--wms-strategy`. The run
core does not check the names: a caller checks them with `strategies.chosen` before the start, as
`jms-uc2` and the Launcher do, because the seed clears the dataset; a process given a name it
does not know ends at its start with the reason, and the readiness gate then raises `NotReady`.
The two options go on the command lines the run core builds. The caller names the deadlock alarm
time of the modules too, 300 s unless it names
another, and the run core puts it on every module's command line, `--deadlock-alarm-seconds`, as
it puts the strategies there; a caller checks that it is a positive number first, as `jms-uc2`
and the Launcher do. The caller also says
whether the seed turns on the store's history collection, the launch option `collect_history`,
off unless it says so. The rest is the supervisor's:

- **Before the start:** the one live-instance guard, the supervision's `refuse_if_live`, with
  the services the run's graph reads, because the seed clears the dataset.
- **Readiness:** `Supervisor.wait_until_ready`, the readiness gate. It lets the run go on once
  every participant's service is live and has registered every workflow the participant
  registers: a module's three, the WarehouseManagementSystem's two. So an order sent to the
  WarehouseManagementSystem the moment the gate opens finds its spawn workflow in the graph.
- **The stop:** `Supervisor.stop_all`, the ordered teardown. Every participant is asked to stop
  and deregisters its service as it goes, within one stop timeout; one still running then is
  killed, and `Supervisor.may_not_have_deregistered` names it.
- **A failed participant:** the supervisor prints one line when a participant turns `failed`, with
  its name and why, its exit code if its process exited, so a run without a page shows it too.

The graph is reached through one small port, `RunGraph`, so that a caller seeds what it needs and
a test can replace it. `TripleStoreGraph` is the real one. Nothing here imports the Launcher's page
or its API, and no participant here is the monitor.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import NamedTuple, Protocol

from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.services import Service, services
from jms_kapps.common.supervision import LaunchFailed, Supervisor
from jms_kapps.uc2.module import DEFAULT_DEADLOCK_ALARM_SECONDS
from jms_kapps.uc2.process import MODULE_WORKFLOWS
from jms_kapps.uc2.seed import seed_topology
from jms_kapps.uc2.strategies import REAL
from jms_kapps.uc2.topology import Topology
from jms_kapps.uc2.vocabulary import WMS
from jms_kapps.uc2.wms_process import WMS_WORKFLOWS

# What a page calls the WarehouseManagementSystem's process.
WMS_NAME = "Warehouse Management System"


class Participant(NamedTuple):
    """One process of a run: the resource it serves, what a person calls it, its command line,
    and the classes of the workflows it registers, without which it is not ready."""

    resource: IRI
    name: str
    command: list[str]
    workflows: tuple[IRI, ...] = ()


class RunGraph(Protocol):
    """What a run reads from and writes to the graph. `TripleStoreGraph` is the real one."""

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        """Clear the dataset, then seed use case 2's ontology, shapes and `topology`, each
        module with its own transit time, or else `transit_seconds`. With `collect_history`, the
        launch option, turn on the store's history collection after the ontology is loaded;
        without it, leave the collection as it is."""
        ...

    def services(self, workflows: Collection[IRI]) -> Iterable[Service]:
        """The live services, each with its workflows of the classes in `workflows`."""
        ...

    def service_of(
        self, resource: IRI, workflows: Collection[IRI]
    ) -> Iterable[Service]:
        """Every service of `resource`, live or not, newest heartbeat first, each with its
        workflows of the classes in `workflows`: what the supervisor reads of a process whose
        service stopped being ready."""
        ...


class TripleStoreGraph:
    """A run's graph on one dataset of a triple store.

    Args:
        db: The client of the dataset, which a seed clears.
        resources: The plain resources a seed writes beside the topology, each with its label,
            for the participants that the caller adds: the demo Launcher's monitor.
    """

    def __init__(
        self, db: TripleStore, *, resources: Mapping[IRI, str] | None = None
    ) -> None:
        self.db = db
        self._resources = dict(resources or {})

    def seed(
        self, topology: Topology, transit_seconds: float, *, collect_history: bool
    ) -> None:
        seed_topology(
            self.db,
            topology,
            transit_seconds=transit_seconds,
            resources=self._resources,
            collect_history=collect_history,
        )

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return services(self.db, workflows=workflows)

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return services(
            self.db, resource=resource, workflows=workflows, live_only=False
        )


def participants_of(
    topology: Topology,
    *,
    module_command: Callable[[IRI], list[str]],
    wms_command: list[str],
    module_strategy: str = REAL,
    wms_strategy: str = REAL,
    deadlock_alarm_seconds: float = DEFAULT_DEADLOCK_ALARM_SECONDS,
) -> list[Participant]:
    """The participants of every run of `topology`: a process for each of its modules, in the
    topology's order, then the WarehouseManagementSystem's, each with the workflows it registers.

    Each command line ends with the strategy its process runs with: `--module-strategy
    <module_strategy>` on every module's, `--wms-strategy <wms_strategy>` on the
    WarehouseManagementSystem's. Each process loads its strategy by that name at its start, and
    one that knows no strategy by it ends there, with the reason (`strategies`). Every module's
    ends with `--deadlock-alarm-seconds <deadlock_alarm_seconds>` after it.

    Args:
        module_command: The command line of the process for one module, by its resource:
            `process.module_command` for a module that runs.
        wms_command: The command line of the WarehouseManagementSystem's process:
            `wms_process.wms_command` for one that runs.
        module_strategy: The name of the module strategy every module runs with.
        wms_strategy: The name of the WMS strategy the WarehouseManagementSystem runs with.
        deadlock_alarm_seconds: How long a box a module holds may wait for a Reserve before the
            module raises the deadlock alarm for it, a positive number of seconds.
    """
    modules = [
        Participant(
            module.resource,
            module.name,
            [
                *module_command(module.resource),
                "--module-strategy",
                module_strategy,
                "--deadlock-alarm-seconds",
                str(float(deadlock_alarm_seconds)),
            ],
            MODULE_WORKFLOWS,
        )
        for module in topology.modules
    ]
    wms = Participant(
        WMS,
        WMS_NAME,
        [*wms_command, "--wms-strategy", wms_strategy],
        WMS_WORKFLOWS,
    )
    return [*modules, wms]


def start_run(
    graph: RunGraph,
    supervisor: Supervisor,
    topology: Topology,
    participants: Sequence[Participant],
    *,
    transit_seconds: float,
    collect_history: bool = False,
) -> None:
    """Seed `topology` through `graph`, then start each of `participants`, in their order, under
    `supervisor`, and watch them.

    The caller asks the live-instance guard first, because the seed clears the dataset, and
    makes `supervisor` the one its stops reach before this starts anything, so that a stop
    reaches every process this starts.

    Args:
        transit_seconds: The transit time of every module that has none of its own in
            `topology`. 0 is allowed.
        collect_history: Whether the seed turns on the store's history collection, the launch
            option; a seed without it leaves the collection as it is.

    Raises:
        LaunchFailed: If seeding or starting a process failed, naming why. Every process this
            started is stopped again then, and nothing runs.
    """
    try:
        graph.seed(topology, transit_seconds, collect_history=collect_history)
        for participant in participants:
            supervisor.spawn(
                participant.resource,
                participant.name,
                participant.command,
                workflows=participant.workflows,
            )
    except Exception as error:
        supervisor.stop_all()
        raise LaunchFailed(str(error)) from error
    supervisor.watch()
