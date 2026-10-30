"""What the Launcher does, without a route or a page: launch a topology, stop processes, arm faults.

The Launcher runs on the run core (`run_core`), which the bench launcher shares. A launch stops
whatever runs, asks the one live-instance guard, clears the dataset, seeds the topology, and
starts the participants of every run, one FlexConveyorModule process for each module and the
WarehouseManagementSystem's, through a fresh `Supervisor`, and beside them the monitor. The monitor
is the Launcher's own: a run of the run core without the Launcher starts none, so its resource is
reserved here (`launchable`), and the Launcher's graph seeds it. `api.py` serves this over HTTP,
and `static/` is the page that calls it.

**A launch names the strategy of the modules and the strategy of the WarehouseManagementSystem**,
or runs with the Launcher's own, `jms-uc2 --module-strategy` and `--wms-strategy`, `real` unless
they name others (`strategies`). The Launcher finds the strategies of each kind once,
when it is made, and refuses a launch that names one it did not find before it stops anything. The
run core puts `--module-strategy <name>` on the command line of every module process and
`--wms-strategy <name>` on the WarehouseManagementSystem's (`run_core.participants_of`), and each
process loads its strategy by that name at its start.

**A launch says whether it collects history**, the launch option `collect_history`, or runs with
the Launcher's own, `jms-uc2 --collect-history`, off unless it is given. A launch with it has its
reset turn on the store's history collection, so that past possessions can be read; one without it
leaves the collection as it is, on where an earlier launch turned it on. The state says whether
the launch that runs was launched with it.

**A launch says whether its monitor collects the refusals**, the launch option
`collect_refusals`, off unless it is given. The monitor then starts in "graph + streams", which
follows each participant's handover event stream; without it the monitor starts in "graph only",
which reads the knowledge graph and nothing else. The state says whether the launch that runs was
launched with it, and the page's checkbox starts at that.

**A launch names the deadlock alarm time of its modules**, or runs with the Launcher's own,
`jms-uc2 --deadlock-alarm-seconds`, 300 s unless it is given: how long a box a module holds may
wait for a Reserve before the module raises the deadlock alarm for it. The run core puts
`--deadlock-alarm-seconds <seconds>` on the command line of every module process. The page's
field "deadlock alarm (s)" starts at the Launcher's own; the API refuses a value that is not a
positive number, before anything is stopped or cleared.

**Clear stops every process, then empties the dataset, graph by graph**, and says how many
statements the dataset holds then: 0 where it is empty. It asks the one live-instance guard as
a launch does, after this Launcher's processes have stopped, so it clears nothing while another
launcher's process, or a process run on its own, is live there. A clear of the whole dataset in
one request is refused while the store collects history, so each graph is cleared on its own
(`triplestore.clear_graph_by_graph`). From the clear on, no topology shows as seeded, and the next
launch loads the ontologies, the shapes and a topology again.

**Each refusal of the Launcher has a class of its own**: its own, `NoProcess` and
`NotReachable`; the load check's, `TopologyRefused` (`launchable`); and those it passes on from
the run core and the strategies, `LiveInstanceError`, `LaunchFailed`, `ClearFailed` and
`UnknownStrategy`. Its routes catch only refusals, so any other error, as a bug's, goes through
them as it is (`api.py`).

The graph and the modules' fault switches are reached through two small ports, `LauncherGraph`, the
run core's `RunGraph` with the clear beside it, and `FaultSwitches`, so that a test can replace
both.

**The Launcher holds each module's two faults.** It writes each switch, and it supervises the
processes that reset a module to off, so it knows what it set and when the value is gone. The page
therefore shows what the Launcher commanded, and no read of the page reaches out to a module. A
switch goes to the address the supervisor read when the module turned live, not to one read from
the graph now: a module run by hand on the same resource can win the graph's answer, and the switch
then works while the graph is away. A write through a module's own `POST /faults/{name}` is not
seen here.

**Nor does a read of the page reach the graph.** The supervisor reads the services in its own
watch, each with the address it keeps from the read that found its process live. `state()` renders
what the supervisor last read. While the graph cannot be read, a state that rests on it is shown as
unconfirmed, with how long ago it was last confirmed, and the page keeps answering. The list of the
boxes on the station is read the same way, in the background, by its one SPARQL query
(`boxes.StationBoxes`), and `state()` renders the last read with the query's text.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import fields
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, get_type_hints
from urllib.parse import urlencode, urljoin

import httpx
from kapps_triplestore_interface import IRI, TripleStore
from pydantic import (
    BaseModel,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from typing_extensions import TypedDict

from jms_kapps.common.passthrough import process_of, process_path
from jms_kapps.common.supervision import (
    STOP_TIMEOUT_SECONDS,
    ChildSnapshot,
    LaunchFailed,
    Supervisor,
    refuse_if_live,
)
from jms_kapps.common.triplestore import ClearFailed, clear_graph_by_graph
from jms_kapps.uc2 import run_core
from jms_kapps.uc2.launcher.boxes import BoxesOnStation, StationBoxes
from jms_kapps.uc2.module import DEFAULT_DEADLOCK_ALARM_SECONDS, Fault, faults_off
from jms_kapps.uc2.monitor.memory import GRAPH_AND_STREAMS, GRAPH_ONLY
from jms_kapps.uc2.process import set_fault_switch
from jms_kapps.uc2.run_core import Participant, RunGraph, participants_of, start_run
from jms_kapps.uc2.seed import DEFAULT_TRANSIT_SECONDS
from jms_kapps.uc2.strategies import REAL, ModuleStrategy, WmsStrategy, chosen, found
from jms_kapps.uc2.topology import Topology, TopologyRefused
from jms_kapps.uc2.vocabulary import FCI_NS, MONITOR, WMS
from jms_kapps.uc2.wms_dashboard import TAB as WMS_TAB

# What the page calls the monitor's process, and the label its resource is seeded with.
MONITOR_NAME = "Monitor"

Kind = Literal["module", "wms", "monitor"]

# The kind of the WarehouseManagementSystem's process and the monitor's, by their resources: every
# other process the Launcher starts is a module's.
_KIND_BY_RESOURCE: dict[IRI, Kind] = {MONITOR: "monitor", WMS: "wms"}


def _kind_of(resource: IRI) -> Kind:
    """The kind of the Launcher's process for `resource`: every process it starts is a module's,
    but the WarehouseManagementSystem's and the monitor's.

    This is the one place that decides a kind. What a process of each kind offers the page is in
    one table, `_OFFERS`, and the state and the page read the result: no other place compares a
    kind with a kind's name.
    """
    return _KIND_BY_RESOURCE.get(resource, "module")


def _echo_to_stdout(text: str) -> None:
    """Print `text` on stdout, as the supervisor's echo does by default: a terminal that has
    closed takes no line, and the launch goes on."""
    with contextlib.suppress(OSError):
        print(text, flush=True)


class Link(BaseModel):
    """A page of a process, where the Launcher page may send a person.

    `href` is relative to the Launcher page, and goes through the Launcher's own port: see
    `process_path`.
    """

    label: str
    href: str


class ProcessState(BaseModel):
    """One process, as the page shows it: the supervisor's snapshot of it, `child`, and what the
    page needs besides.

    The page reads one object for each process, the snapshot's fields beside the page's own: so the
    snapshot's fields are written out flat, and read back from flat, as FastAPI reads back every
    answer it sends. A snapshot's `stoppable` is whether the page may offer a stop: the process
    still runs, and no stop has reached it. A process that crashed reads `failed` and has exited, so
    the page may not stop it, whether or not a stop came after the crash; one that reads `failed`
    because its service stopped being live may still run, and may be stopped.

    `faults` holds each of a module's two faults, armed or not, as the Launcher commanded them,
    and is None for the WarehouseManagementSystem and the monitor, which have none. `state_note`
    is what the page says of the state to a person who asks: why a killed process was killed, and
    why a failed one failed. It is None where the state says enough by itself. `fault_switchable`
    is whether the page may offer the fault switches: the process is a module, and the supervisor
    holds the address of its live service, which a switch is sent to.
    """

    child: ChildSnapshot
    kind: Kind
    links: list[Link]
    faults: dict[Fault, bool] | None
    state_note: str | None
    fault_switchable: bool

    @model_validator(mode="before")
    @classmethod
    def _snapshot_from_flat(cls, data: Any) -> Any:
        """The snapshot's fields, read from beside the page's own into `child`."""
        if not isinstance(data, dict) or "child" in data:
            return data
        snapshot = {f.name for f in fields(ChildSnapshot)}
        return {"child": {k: v for k, v in data.items() if k in snapshot}} | {
            k: v for k, v in data.items() if k not in snapshot
        }

    @model_serializer(mode="wrap")
    def _flat(self, handler: SerializerFunctionWrapHandler) -> ProcessStateJSON:
        """The snapshot's fields beside the page's own, in one object."""
        own = handler(self)
        return own.pop("child") | own


# The flat object the page reads for each process, as the Launcher's API docs show it: each field
# of the snapshot, an IRI as the string it is sent as, beside each of the page's own. It is built
# from the two classes, so that no field is written out twice. A link stands in it as the dict
# it already is when the serializer returns: Pydantic checks that return against this type, and
# the class `Link` would make it warn once for each link. The dict type carries the class's name
# and fields, so the docs show the same link.
if TYPE_CHECKING:
    ProcessStateJSON = dict[str, Any]
else:
    # The fields, not `get_type_hints(Link)`: that also resolves the annotations BaseModel itself
    # carries, and a Pydantic release that names them only for type checkers breaks the import.
    LinkJSON = TypedDict(
        "Link", {name: field.annotation for name, field in Link.model_fields.items()}
    )
    LinkJSON.__doc__ = Link.__doc__
    ProcessStateJSON = TypedDict(
        "ProcessStateJSON",
        {
            name: str if hint is IRI else hint
            for name, hint in get_type_hints(ChildSnapshot).items()
        }
        | {
            name: list[LinkJSON] if name == "links" else field.annotation
            for name, field in ProcessState.model_fields.items()
            if name != "child"
        },
    )
    ProcessState.model_rebuild()


class LauncherState(BaseModel):
    """Everything the Launcher page shows. `dataset` is the dataset the Launcher clears and seeds,
    and `store_url` the URL of its store, which the environment names, or "" where it names none:
    the page shows "dataset <dataset> on <store_url>". `default_transit_time` is the value of
    the page's
    transit time field before a person changes it: `jms-uc2 --transit-time`. `monitor` is the monitor's page, and `wms` the
    WarehouseManagementSystem's dashboard, where a person spawns boxes, while each has one.
    `module_strategy` and `wms_strategy` name the strategies in force: those the topology that
    runs was launched with, or None while none was. `boxes` is the list of the boxes on the
    station as its background read last found it, with the text of its query, or None for a
    Launcher that lists none. `collect_history` is whether the topology that runs was launched
    with the option `collect_history`, so that its reset turned on the store's history collection,
    or None while none was. `default_deadlock_alarm_seconds` is the value of the page's field
    "deadlock alarm (s)" before a person changes it: `jms-uc2 --deadlock-alarm-seconds`, 300 s by
    default. `collect_refusals` is whether the topology that runs was launched with the option
    `collect_refusals`, so that its monitor started in "graph + streams", or None while none was."""

    dataset: str
    store_url: str
    default_transit_time: float
    default_deadlock_alarm_seconds: float
    topology: Topology | None
    topology_name: str | None
    module_strategy: str | None
    wms_strategy: str | None
    collect_history: bool | None
    collect_refusals: bool | None
    monitor: str | None
    wms: str | None
    processes: list[ProcessState]
    heartbeat_error: str | None
    boxes: BoxesOnStation | None


class StrategyChoice(BaseModel):
    """One strategy, as a picker offers it: by its `label`, choosing its `name`."""

    name: str
    label: str


class StrategyChoices(BaseModel):
    """The strategies a launch may name, by kind: `module` the module strategies, and `wms` the
    WMS strategies, each in the order they were found."""

    module: list[StrategyChoice]
    wms: list[StrategyChoice]


class Cleared(BaseModel):
    """What a clear left: the `dataset` it emptied, and how many `statements` it holds then, 0
    where it is empty, as the page says it: "<dataset> is empty: 0 statements"."""

    dataset: str
    statements: int


class NotReachable(RuntimeError):
    """The process has no live service to send a command to, or it does not take the command."""


class NoProcess(LookupError):
    """A command names no process that it can act on: a stop, none of the current launch; a fault
    switch, no module of the running topology. A refusal of the Launcher's own, so that its
    routes can tell it from any other error, as a bug's `KeyError`."""


class LauncherGraph(RunGraph, Protocol):
    """What the Launcher reads from and writes to the graph: a run's (`RunGraph`), and the clear
    of its Clear. `TripleStoreGraph` is the real one."""

    def clear(self) -> int:
        """Clear every graph of the dataset, one by one, and return how many statements it
        holds then (`triplestore.clear_graph_by_graph`).

        Raises:
            ClearFailed: If the store did not list or clear a graph, or did not say how many
                statements are left, naming why.
        """
        ...


class TripleStoreGraph(run_core.TripleStoreGraph):
    """The Launcher's graph: the run core's, whose seed writes the monitor's resource beside every
    topology, because the Launcher starts the monitor beside every topology, and whose clear
    empties the dataset graph by graph."""

    def __init__(self, db: TripleStore) -> None:
        super().__init__(db, resources={MONITOR: MONITOR_NAME})

    def clear(self) -> int:
        return clear_graph_by_graph(self.db)


class FaultSwitches(Protocol):
    """Arms a fault of the module whose service is at an address. `HTTPFaultSwitches` is real.

    Writing is the whole of the port. Reading a switch back is deliberately absent: the Launcher
    is the only writer, so it already knows every value, and a read here would be a blocking call
    made once per module on the page's poll.
    """

    def set(self, address: str, name: Fault, armed: bool) -> None:
        """Arm or disarm the fault `name`.

        Raises:
            NotReachable: If the module cannot be reached, or does not take the switch.
        """
        ...


class HTTPFaultSwitches:
    """Each module's own `POST /faults/{name}` route."""

    def set(self, address: str, name: Fault, armed: bool) -> None:
        try:
            set_fault_switch(address, name, armed)
        except httpx.HTTPError as error:
            raise NotReachable(
                f"the module at {address} did not take the switch: {error}"
            ) from error


def launchable(topology: Topology) -> Topology:
    """`topology`, if the Launcher can launch it: no module takes the monitor's resource, which the
    Launcher seeds and starts beside every topology. Every route of the Launcher that reads a
    topology asks this after the load check, and so does every launch.

    Raises:
        TopologyRefused: If a module takes the monitor's resource, naming the module.
    """
    for module in topology.modules:
        if module.resource == MONITOR:
            raise TopologyRefused(
                f"{module.id} is the monitor's resource, which the Launcher seeds and starts "
                "beside every topology, so no module can take that id"
            )
    return topology


# Why a killed process is not a stopped one, for the page to show beside its state.
_KILLED = (
    "It did not stop within the stop timeout and was killed. A kill skips the deregistration, "
    "so its service keeps its address in the knowledge graph and counts as live until its last "
    "heartbeat is stale."
)


def _state_note(child: ChildSnapshot) -> str | None:
    """What the page says of the state of `child` to a person who asks: why a killed process is
    not a stopped one, and why a failed one failed, as the line printed of its turn says. None
    where the state says enough by itself."""
    if child.state == "killed":
        return _KILLED
    if child.state == "failed" and child.why_failed is not None:
        return child.why_failed[0].upper() + child.why_failed[1:] + "."
    return None


class _Page(NamedTuple):
    """A page of a process: the `label` the page links it by, and its `path` below the process's
    own (`process_path`)."""

    label: str
    path: str


class _Offers(NamedTuple):
    """What a process of one kind offers the page once its service is live: its `pages`, in the
    order the page lists them, and whether it has the `faults` a person arms."""

    pages: tuple[_Page, ...]
    faults: bool


# The tab of the dashboard that lists the process's newest log records, with a level filter. It
# is the library's documented public tab name (`dashboard?tab=<name>`). The Launcher only names
# it in its "logs" link and defines no tab of its own.
_LOGS_TAB = "Logs"

# The labels of the two pages the page's header opens as well: the monitor's own page, and the
# WarehouseManagementSystem's dashboard (`LauncherState`).
_OPEN = "open"
_DASHBOARD = "dashboard"

# A module's and the WarehouseManagementSystem's API docs list their workflows. Their middleware
# serves its state dashboard at `/dashboard`: the page of what that instance holds, which the
# middleware library renders, with the WMS's own boxes on the WMS's. "logs" opens that dashboard on
# its Logs tab: the process's whole log.
_DOCS = _Page("docs", "docs")
_ACTIVITY = _Page("activity", "activity")
_LOGS = _Page("logs", "dashboard?" + urlencode({"tab": _LOGS_TAB}))

# What a process of each kind offers the page: the one table of what a kind has (`_kind_of`).
_OFFERS: dict[Kind, _Offers] = {
    "module": _Offers(
        pages=(_DOCS, _ACTIVITY, _Page(_DASHBOARD, "dashboard"), _LOGS), faults=True
    ),
    # The WMS's dashboard opens on the WMS's own tab, where a person spawns boxes.
    "wms": _Offers(
        pages=(
            _DOCS,
            _ACTIVITY,
            _Page(_DASHBOARD, "dashboard?" + urlencode({"tab": WMS_TAB})),
            _LOGS,
        ),
        faults=False,
    ),
    # The monitor's page is its own, and its first link opens it. It serves no dashboard.
    "monitor": _Offers(pages=(_Page(_OPEN, ""), _ACTIVITY), faults=False),
}


def _href(resource: IRI, page: _Page) -> str:
    """Where the Launcher passes `page` of the process of `resource` through, relative to the
    Launcher page: the one place that makes the page's links and the terminal's block."""
    return process_path(resource) + page.path


def _links(offers: _Offers, resource: IRI, live: bool) -> list[Link]:
    """Where the page may send a person, for the process of `resource`: the pages it `offers`,
    and nowhere unless its service is `live`."""
    if not live:
        return []
    return [Link(label=page.label, href=_href(resource, page)) for page in offers.pages]


def _process(child: ChildSnapshot, armed: dict[IRI, dict[Fault, bool]]) -> ProcessState:
    """How the page shows `child`, with the faults the Launcher armed."""
    kind = _kind_of(child.resource)
    offers = _OFFERS[kind]
    return ProcessState(
        child=child,
        kind=kind,
        links=_links(offers, child.resource, live=child.address is not None),
        faults=(
            faults_off() | armed.get(child.resource, {}) if offers.faults else None
        ),
        state_note=_state_note(child),
        fault_switchable=offers.faults and child.address is not None,
    )


def _page(processes: list[ProcessState], kind: Kind, label: str) -> str | None:
    """The page that the row of the process of `kind` links by `label`, or None while it links
    none, as before its service is live: the page's header opens what that row links, so the two
    never differ. The page is found by its label, wherever the row lists it."""
    return next(
        (
            link.href
            for process in processes
            if process.kind == kind
            for link in process.links
            if link.label == label
        ),
        None,
    )


class Launcher:
    """The processes of one use case 2 runtime, and the topology they were launched from.

    One launch, stop-all or clear runs at a time. The page's reads and single commands do not wait
    for one: they act on whichever supervisor and topology are current when they start.

    The armed faults are held here rather than read back from the modules, because this object is
    the only thing that arms one. A launch starts fresh processes and a stop ends one, and both
    forget what they were told.

    Args:
        graph: The graph the Launcher seeds, reads and clears: its `TripleStoreGraph` seeds the
            monitor's resource too.
        dataset: The dataset's name, for the page and the guard's refusal.
        module_command: The command line of the process for one module.
        wms_command: The command line of the WarehouseManagementSystem's process.
        monitor_command: The command line of the monitor's process.
        transit_seconds: The transit time of a launch that names none: the default of the page's
            field, `jms-uc2 --transit-time`.
        faults: The modules' fault switches.
        echo: Where command lines and the children's output go. The Launcher's stdout if None.
        page_address: The address the Launcher page is served at. Given, each launch ends by
            echoing `address_block`; left out, none does.
        stop_timeout: How long a stop waits before it kills a process.
        module_strategies: The module strategies a launch may name, by name: those registered
            in their entry-point group if None (`strategies.found`).
        wms_strategies: The WMS strategies a launch may name, by name: likewise.
        module_strategy: The module strategy of a launch that names none: `jms-uc2
            --module-strategy`.
        wms_strategy: The WMS strategy of a launch that names none: `jms-uc2 --wms-strategy`.
        collect_history: Whether a launch that names none collects history: `jms-uc2
            --collect-history`.
        deadlock_alarm_seconds: The deadlock alarm time of a launch that names none: `jms-uc2
            --deadlock-alarm-seconds`, and the default of the page's field.
        boxes: The list of the boxes on the station, which the Launcher's app reads in the
            background while it serves (`api.create_app`). None lists no boxes: a test's Launcher.
    """

    def __init__(
        self,
        *,
        graph: LauncherGraph,
        dataset: str,
        module_command: Callable[[IRI], list[str]],
        wms_command: list[str],
        monitor_command: list[str],
        transit_seconds: float = DEFAULT_TRANSIT_SECONDS,
        faults: FaultSwitches | None = None,
        echo: Callable[[str], None] | None = None,
        page_address: str | None = None,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
        module_strategies: Mapping[str, ModuleStrategy] | None = None,
        wms_strategies: Mapping[str, WmsStrategy] | None = None,
        module_strategy: str = REAL,
        wms_strategy: str = REAL,
        collect_history: bool = False,
        deadlock_alarm_seconds: float = DEFAULT_DEADLOCK_ALARM_SECONDS,
        boxes: StationBoxes | None = None,
    ) -> None:
        self._graph = graph
        self.boxes = boxes
        self._deadlock_alarm_seconds = deadlock_alarm_seconds
        self._default_strategies = (module_strategy, wms_strategy)
        self._collect_history = collect_history
        self._module_strategies = (
            found(ModuleStrategy) if module_strategies is None else module_strategies
        )
        self._wms_strategies = (
            found(WmsStrategy) if wms_strategies is None else wms_strategies
        )
        self._dataset = dataset
        self._module_command = module_command
        self._wms_command = wms_command
        self._monitor_command = monitor_command
        self._transit_seconds = transit_seconds
        self._faults = faults if faults is not None else HTTPFaultSwitches()
        self._echo = echo
        self._page_address = page_address
        self._stop_timeout = stop_timeout
        self._supervisor: Supervisor | None = None
        self._topology: Topology | None = None
        self._topology_name: str | None = None
        # The strategies the topology that runs was launched with: (module, WMS).
        self._in_force: tuple[str, str] | None = None
        # Whether the topology that runs was launched with the option `collect_history`.
        self._collecting: bool | None = None
        # Whether the topology that runs was launched with the option `collect_refusals`.
        self._refusals_collected: bool | None = None
        # What each module was told, by fault; a fault never switched is off.
        self._armed: dict[IRI, dict[Fault, bool]] = {}
        # `_launching` serializes launches; `_current` guards the seven fields above.
        self._launching = threading.Lock()
        self._current = threading.Lock()

    def _now(self) -> tuple[Supervisor | None, Topology | None]:
        with self._current:
            return self._supervisor, self._topology

    def launch(
        self,
        topology: Topology,
        *,
        name: str | None = None,
        force: bool = False,
        transit_seconds: float | None = None,
        module_strategy: str | None = None,
        wms_strategy: str | None = None,
        collect_history: bool | None = None,
        deadlock_alarm_seconds: float | None = None,
        collect_refusals: bool = False,
    ) -> None:
        """Stop what runs, clear the dataset, seed `topology`, and start its processes: the
        run core's participants of `topology`, and the monitor.

        A clear cannot be undone, so it clears only while no other process's service is live
        there: the run core's live-instance guard asks the graph after this Launcher's own
        processes have stopped. A process that stops when asked deregisters its service first. One
        that was killed, or that had exited on its own, may have left its service live until its
        last heartbeat is `MAX_HEARTBEAT_AGE_SECONDS` old. That is this Launcher's own dead
        process, and the seed clears what it left, so the guard leaves it out. Any other live
        service refuses the launch: another launcher's, or that of a module run on its own from its
        printed command line, even on a resource this Launcher stopped cleanly.

        Args:
            topology: The modules to seed and start.
            name: What to call it on the page. The shipped topology's name, a path, or what the
                page calls a topology edited on it.
            force: Clear the dataset even while another process's service there is live.
            transit_seconds: The transit time of every module that has none of its own in
                `topology`. The Launcher's default if None. 0 is allowed.
            module_strategy: The name of the module strategy every module process runs with,
                `--module-strategy` on its command line. The Launcher's default if None.
            wms_strategy: The name of the WMS strategy the WarehouseManagementSystem's process
                runs with, `--wms-strategy` on its command line. The Launcher's default if None.
            collect_history: Whether the reset turns on the store's history collection, after it
                loaded the ontology; without it, the collection stays as it is. The Launcher's
                default if None.
            deadlock_alarm_seconds: How long a box a module holds may wait for a Reserve before
                the module raises the deadlock alarm for it, `--deadlock-alarm-seconds` on every
                module's command line: a positive number of seconds. The Launcher's default if
                None.
            collect_refusals: Whether the monitor starts in "graph + streams", so that it
                collects the participants' refusals from their handover event streams, or in
                "graph only", which reads the knowledge graph and nothing else. Off if left out.

        Raises:
            TopologyRefused: If a module of `topology` takes the monitor's resource
                (`launchable`). Nothing is stopped or cleared then.
            UnknownStrategy: If this Launcher found no strategy of its kind by
                `module_strategy` or `wms_strategy`, naming it and the strategies of its kind.
                Nothing is stopped or cleared then.
            LiveInstanceError: If another process's service is live and `force` is false, naming
                each live resource. Nothing is cleared then, and this Launcher's processes stay
                stopped.
            LaunchFailed: If the graph cannot say which services are live, or seeding or starting
                a process failed, naming why. Nothing runs then.
        """
        launchable(topology)
        default_module, default_wms = self._default_strategies
        module_strategy = default_module if module_strategy is None else module_strategy
        wms_strategy = default_wms if wms_strategy is None else wms_strategy
        if collect_history is None:
            collect_history = self._collect_history
        if deadlock_alarm_seconds is None:
            deadlock_alarm_seconds = self._deadlock_alarm_seconds
        chosen(ModuleStrategy, module_strategy, self._module_strategies)
        chosen(WmsStrategy, wms_strategy, self._wms_strategies)
        with self._launching:
            old = self._stop_then_guard(force=force)
            supervisor = Supervisor(
                self._graph.services,
                service_of=self._graph.service_of,
                echo=self._echo,
                stop_timeout=self._stop_timeout,
            )
            with self._current:
                # Current before the first spawn, so that a stop reaches every child it starts.
                self._supervisor, self._topology = supervisor, None
                self._topology_name = None
                self._in_force = None
                self._collecting = None
                self._refusals_collected = None
                # New processes, each with its fault off, whatever the old ones were told.
                self._armed.clear()
            try:
                start_run(
                    self._graph,
                    supervisor,
                    topology,
                    self._participants(
                        topology,
                        module_strategy,
                        wms_strategy,
                        deadlock_alarm_seconds,
                        collect_refusals,
                    ),
                    transit_seconds=(
                        self._transit_seconds
                        if transit_seconds is None
                        else transit_seconds
                    ),
                    collect_history=collect_history,
                )
            except LaunchFailed:
                if old is not None:
                    with self._current:
                        self._supervisor = old  # its stopped children stay on the page
                raise
            with self._current:
                self._topology, self._topology_name = topology, name
                self._in_force = (module_strategy, wms_strategy)
                self._collecting = collect_history
                self._refusals_collected = collect_refusals
        block = self.address_block()
        if block is not None:
            (self._echo or _echo_to_stdout)(block)

    def address_block(self) -> str | None:
        """The pages of the run at the addresses that stay valid, all through the Launcher's own
        port, set apart from the processes' lines by a blank line before and after; None without
        a `page_address`.

        Each process takes a new port at each launch, so the direct address it prints stops
        working at the next one; these do not. The paths come from the table of the page's links
        (`_OFFERS`, `_href`), so that the block and the page never differ.
        """
        if self._page_address is None:
            return None
        base = self._page_address

        def at(resource: IRI, kind: Kind, label: str) -> str:
            page = next(p for p in _OFFERS[kind].pages if p.label == label)
            return urljoin(base, _href(resource, page))

        module_pages = ", ".join(page.path for page in _OFFERS["module"].pages)
        rows = [
            ("Launcher page", base),
            ("WMS dashboard", at(WMS, "wms", _DASHBOARD)),
            ("Monitor", at(MONITOR, "monitor", _OPEN)),
            (
                "A module's pages",
                urljoin(base, process_path(IRI(FCI_NS + "ID")).replace("ID", "<id>"))
                + f"  then one of: {module_pages}",
            ),
        ]
        width = max(len(name) for name, _ in rows)
        lines = [f"  {name.ljust(width)}  {address}" for name, address in rows]
        return (
            "\n"
            + "\n".join(["Pages of the run, through the Launcher's port:", *lines])
            + "\n"
        )

    def _stop_then_guard(self, *, force: bool) -> Supervisor | None:
        """Stop this Launcher's processes, then ask the live-instance guard whether the
        dataset may be cleared: what a launch and a clear both do first, under `_launching`.
        Returns the supervisor of the processes it stopped, None where none was started.

        The guard leaves out this Launcher's own processes that may not have deregistered, a
        killed one or one that had exited on its own, since what they left is cleared.

        Raises:
            LiveInstanceError: If another process's service is live and `force` is false.
            LaunchFailed: If the graph cannot say which services are live.
        """
        old, _ = self._now()
        left_behind: set[IRI] = set()
        if old is not None:
            old.stop_all()
            left_behind = old.may_not_have_deregistered()
            with self._current:
                # Its processes are gone, and with them every fault they were told.
                self._armed.clear()
        refuse_if_live(
            self._graph.services,
            dataset=self._dataset,
            leave_out=left_behind,
            force=force,
        )
        return old

    def clear(self) -> Cleared:
        """Stop every process, then clear every graph of the dataset, one by one, and say how
        many statements it holds then.

        The stop and the guard are a launch's: this Launcher's processes stop first and deregister
        as they go, and the clear goes on only while no other process's service is live in the
        dataset. A clear cannot be undone, and the page has no way to force one. From the clear
        on, no topology shows as seeded, nor the strategies and the history option it was
        launched with, unless the store refused before it cleared any graph; the processes stay
        on the page, stopped. The next launch seeds the ontologies, the shapes and its topology
        again.

        Raises:
            LiveInstanceError: If another process's service is live, naming each live resource.
                Nothing is cleared then, and this Launcher's processes stay stopped.
            ClearFailed: If the graph cannot say which services are live, and nothing is cleared;
                or if the store did not list or clear a graph, or did not say how many statements
                are left, naming why.
        """
        with self._launching:
            try:
                self._stop_then_guard(force=False)
            except LaunchFailed as error:
                raise ClearFailed(str(error), nothing_cleared=True) from error
            cleared_any = True
            try:
                statements = self._graph.clear()
            except ClearFailed as error:
                cleared_any = not error.nothing_cleared
                raise
            finally:
                if cleared_any:
                    # No topology is seeded whole any more: none shows on the page, nor the
                    # strategies and the history option it was launched with.
                    with self._current:
                        self._topology, self._topology_name = None, None
                        self._in_force = None
                        self._collecting = None
                        self._refusals_collected = None
        return Cleared(dataset=self._dataset, statements=statements)

    def _participants(
        self,
        topology: Topology,
        module_strategy: str,
        wms_strategy: str,
        deadlock_alarm_seconds: float,
        collect_refusals: bool,
    ) -> list[Participant]:
        """The processes of a launch of `topology`: the run core's participants, then the
        monitor, which only the Launcher starts, in "graph + streams" with `collect_refusals`
        and in "graph only" without it."""
        return [
            *participants_of(
                topology,
                module_command=self._module_command,
                wms_command=self._wms_command,
                module_strategy=module_strategy,
                wms_strategy=wms_strategy,
                deadlock_alarm_seconds=deadlock_alarm_seconds,
            ),
            Participant(
                MONITOR,
                MONITOR_NAME,
                [
                    *self._monitor_command,
                    "--mode",
                    GRAPH_AND_STREAMS if collect_refusals else GRAPH_ONLY,
                ],
            ),
        ]

    def stop(self, resource: IRI) -> None:
        """Stop one process, so that its service deregisters.

        Raises:
            NoProcess: If no process of the current launch runs for `resource`.
        """
        supervisor, _ = self._now()
        if supervisor is None or resource not in {
            child.resource for child in supervisor.snapshot()
        }:
            raise NoProcess(f"no process for {resource}")
        supervisor.stop(resource)
        with self._current:
            self._armed.pop(IRI(resource), None)

    def begin_stop(self) -> None:
        """Mark the stop of every process as begun, the moment the first stop signal arrives and
        before `stop_all` asks any: a process that exits with code 0 from then on has stopped, not
        failed (`Supervisor.begin_stop`).

        It takes no lock, so it is safe in a signal handler: the main thread may hold one when the
        signal arrives. The current supervisor is read as one reference.
        """
        supervisor = self._supervisor
        if supervisor is not None:
            supervisor.begin_stop()

    def stop_all(self) -> None:
        """Stop every process, so that every service deregisters."""
        with self._launching:
            supervisor, _ = self._now()
            if supervisor is not None:
                supervisor.stop_all()
            with self._current:
                self._armed.clear()

    def set_fault(self, resource: IRI, name: Fault, armed: bool) -> None:
        """Arm or disarm the fault `name` of one module, at the address the supervisor holds.

        Raises:
            NoProcess: If `resource` is not a module of the running topology.
            NotReachable: If the module has no live service, or does not take the switch. What
                the Launcher held for it stays as it was.
        """
        supervisor, topology = self._now()
        if (
            supervisor is None
            or topology is None
            or resource not in {m.resource for m in topology.modules}
        ):
            raise NoProcess(f"no module {resource}")
        address = next(
            (c.address for c in supervisor.snapshot() if c.resource == resource), None
        )
        if address is None:
            raise NotReachable(f"{resource} has no live service")
        self._faults.set(address, name, armed)
        # Only after the module has taken it: a switch that was refused was never set.
        with self._current:
            self._armed.setdefault(resource, {})[name] = armed

    def strategies(self) -> StrategyChoices:
        """The strategies a launch may name, of each kind, as this Launcher found them. Like
        `state()`, it calls neither a process nor the graph."""
        return StrategyChoices(
            module=[
                StrategyChoice(name=name, label=strategy.label)
                for name, strategy in self._module_strategies.items()
            ],
            wms=[
                StrategyChoice(name=name, label=strategy.label)
                for name, strategy in self._wms_strategies.items()
            ],
        )

    def process(self, id: str) -> ChildSnapshot | None:
        """The process whose id in the Launcher's paths is `id` (see `process_id`), as the
        supervisor holds it now, or None where no process of the current launch has that id.
        Like `state()`, it calls neither a process nor the graph.
        """
        supervisor, _ = self._now()
        children = supervisor.snapshot() if supervisor is not None else []
        return process_of(children, id)

    def state(self) -> LauncherState:
        """Everything the page shows.

        It renders what this process already holds, and calls neither a module nor the graph, so
        a poll answers at once whatever else is away. The addresses are the supervisor's, kept
        for live processes only: a killed one can leave its address behind.
        """
        supervisor, topology = self._now()
        children = supervisor.snapshot() if supervisor is not None else []
        with self._current:
            armed, name = dict(self._armed), self._topology_name
            module_strategy, wms_strategy = self._in_force or (None, None)
            collect_history = self._collecting
            collect_refusals = self._refusals_collected
        processes = [_process(child, armed) for child in children]
        return LauncherState(
            dataset=self._dataset,
            store_url=os.environ.get("TRIPLESTORE_URL", ""),
            default_transit_time=self._transit_seconds,
            default_deadlock_alarm_seconds=self._deadlock_alarm_seconds,
            topology=topology,
            topology_name=name,
            module_strategy=module_strategy,
            wms_strategy=wms_strategy,
            collect_history=collect_history,
            collect_refusals=collect_refusals,
            monitor=_page(processes, "monitor", _OPEN),
            wms=_page(processes, "wms", _DASHBOARD),
            processes=processes,
            heartbeat_error=supervisor.heartbeat_error() if supervisor else None,
            boxes=self.boxes.last() if self.boxes is not None else None,
        )
