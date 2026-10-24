"""The Launcher's HTTP surface: JSON routes over `backend.Launcher`, and the static page.

The backend answers in JSON only, and renders no HTML. The page in `static/` is plain HTML,
JavaScript and CSS, and learns everything it shows from these routes. The pattern is the
middleware's transferunits demo, `index.py` plus `templates/index.html`, with the two halves
kept apart.

**A launch names a shipped topology, or carries one edited on the page, which the server checks
itself.** Launching clears the dataset and seeds it again. A shipped topology is chosen by its
name. An edited one comes whole, as the body of the request: a module list, the one topology
format, which the shipped topologies have too (see `topology.py`); the server runs the load check
on it again and launches it only if it passes, because the page's own result is never taken as the
check. `/api/topology/check` runs the same check alone, so the page can show its reason while a
person edits. Anything but a module list, such as the short topology document this package had
before, fails the check with a reason that names the format. Either launch meets the same guards,
and the page has no way to force one. A topology file of one's own opens in the page's editor,
which launches it as an edited topology, or goes in at the command line, `jms-uc2 --topology
<path>`, where the person starting the run supplies it.

**The page's editor reads and writes a topology through these routes.** It starts from a shipped
topology, which `GET /api/topologies/<name>` gives as its module list, from an empty grid, or from
a topology file a person uploads, whose bytes `POST /api/topology/file` reads as the command line
reads a file, and lays out as `POST /api/topology/layout` lays out a module list. The page saves
what the editor holds as a topology file itself: a module list it writes, as below.
`POST /api/topology/layout` gives the modules of a module list and a cell on a grid for each, by
the one layout rule, `Topology.places`; `GET /api/topology/format` gives the namespaces and keys a
module list is written with, those of `topology.py`; and the load check above decides what may
launch. So the page carries no reader of the format, no layout rule and no load check of its own.
Once a topology is open, the editor keeps each module on its cell, and puts a module it adds on the
cell clicked: laying out again on each change would move a module that is not connected yet to a row
below, away from where a person put it. A launch of an edited topology names the topology its
editor started from, and the page calls the running topology by it: `cross (edited)`
(`edited_name`).

**A route answers with an error only for a refusal of the backend's own**, each of a class of its
own: a process or a module that a command names and that does not run (`NoProcess`), a module
that does not take a fault switch (`NotReachable`), a topology that loading refuses
(`TopologyRefused`) or a name that the package ships no topology by (`UnknownTopology`), a
strategy that the Launcher did not find (`UnknownStrategy`), and a launch or a clear that the
live-instance guard refuses (`LiveInstanceError`) or that fails (`LaunchFailed`, `ClearFailed`).
Any other error goes through the route as it is, and the server answers it as the fault it is: a
bug's `KeyError` or `ValueError` is not taken for a refusal that names a wrong cause.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.exceptions import InvalidIRIError
from pydantic import BaseModel

from jms_kapps.common.passthrough import Passthrough
from jms_kapps.common.supervision import LaunchFailed, LiveInstanceError
from jms_kapps.common.triplestore import ClearFailed
from jms_kapps.uc2.launcher.backend import (
    Cleared,
    Launcher,
    LauncherState,
    NoProcess,
    NotReachable,
    StrategyChoices,
    launchable,
)
from jms_kapps.uc2.module import Fault
from jms_kapps.uc2.strategies import REAL, UnknownStrategy
from jms_kapps.uc2.topology import (
    CONNECTION_KEY,
    CONNECTS_TO_KEY,
    DIRECTION_KEY,
    DIRECTIONS,
    LABEL_KEY,
    PORT_KEY,
    TRANSIT_TIME_KEY,
    TYPE_KEY,
    Direction,
    Module,
    Topology,
    TopologyRefused,
    UnknownTopology,
    edited_topology,
    parse_topology,
    parse_topology_file,
    shipped_topology,
    topology_names,
)
from jms_kapps.uc2.vocabulary import FC_NS, FCI_NS

STATIC = Path(__file__).parent / "static"


class LaunchCommand(BaseModel):
    """`name` is a shipped topology's. `transit_time` is the transit time in seconds of every
    module that has none of its own, taken as any JSON so that `transit_time_of`, not FastAPI's
    validation, says what is wrong with it; the Launcher's default if it is left out.
    `module_strategy` and `wms_strategy` name the strategies the modules and the
    WarehouseManagementSystem run with, `real` if they are left out. `collect_history` is whether
    the launch's reset turns on the store's history collection, off if it is left out.
    `deadlock_alarm_seconds` is how long a box a module holds may wait for a Reserve before the
    module raises the deadlock alarm for it, taken as any JSON as `transit_time` is, the Launcher's
    default if it is left out. `collect_refusals` is whether the launch's monitor starts in "graph +
    streams" and collects the refusals from the participants' handover event streams, off if it is
    left out: the monitor then starts in "graph only"."""

    name: str
    transit_time: Any = None
    module_strategy: str = REAL
    wms_strategy: str = REAL
    collect_history: bool = False
    deadlock_alarm_seconds: Any = None
    collect_refusals: bool = False


# A module list in a request's body, taken as any JSON, so that the load check, not FastAPI's own
# validation, says what is wrong with it.
ModuleListBody = Annotated[Any, Body()]


class TopologyCheck(BaseModel):
    """What the load check found: whether the topology passes, and why not where it fails."""

    passes: bool
    reason: str | None


class TopologyLayout(BaseModel):
    """What the page's editor opens a topology with: its modules, in the terms the page shows the
    running topology in, and the cell on a grid where the editor draws each, by the module's id,
    as `Topology.places` finds it: the one layout rule, by which the monitor draws too."""

    modules: list[Module]
    places: dict[str, tuple[int, int]]

    @classmethod
    def of(cls, topology: Topology) -> TopologyLayout:
        """The modules of `topology`, and the cell of each by the one layout rule."""
        return cls(modules=topology.modules, places=topology.places())


class ModuleListNames(BaseModel):
    """The names a module list is written with, as `topology.py` gives them, so that the page's
    editor writes a module list without a copy of its own: the namespace of the modules' IRIs, the
    namespace of their types and their connections' directions, and the key of each property."""

    modules: str = FCI_NS
    classes: str = FC_NS
    type: str = TYPE_KEY
    label: str = LABEL_KEY
    connection: str = CONNECTION_KEY
    connects_to: str = CONNECTS_TO_KEY
    direction: str = DIRECTION_KEY
    port: str = PORT_KEY
    transit_time: str = TRANSIT_TIME_KEY


class ProcessCommand(BaseModel):
    resource: str


class ModuleFaultCommand(BaseModel):
    module: str
    name: Fault
    armed: bool


def _resource(value: str) -> IRI:
    """The IRI a command names. One that is not an IRI names no process either."""
    try:
        return IRI(value)
    except InvalidIRIError:
        raise HTTPException(404, f"no process for {value}") from None


def edited_name(base: str | None) -> str:
    """What the page calls a running topology that was edited on it: the topology its editor
    started from, `base`, marked as edited, such as `cross (edited)`; or `edited` alone where the
    request names none. The editor remembers the shipped or uploaded topology it started from."""
    base = (base or "").strip()
    return f"{base} (edited)" if base else "edited"


def transit_time_of(value: object) -> float | None:
    """The launch's transit time that a request carries: None where it carries none, else a number
    of seconds, 0 or more. A string is read as a number, as a query parameter is one.

    Raises:
        HTTPException: 422, with a reason a person reads, if it is no number or is negative.
    """
    if value is None:
        return None
    number: float | None = None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            pass
    elif isinstance(value, int | float) and not isinstance(value, bool):
        number = float(value)
    if number is None or not math.isfinite(number) or number < 0:
        raise HTTPException(
            422,
            "The transit time must be a number of seconds, 0 or more, "
            f"not {json.dumps(value)}. Nothing was launched.",
        )
    return number


def deadlock_alarm_of(value: object) -> float | None:
    """The launch's deadlock alarm time that a request carries: None where it carries none, else
    a number of seconds above 0. A string is read as a number, as a query parameter is one.

    Raises:
        HTTPException: 422, with a reason a person reads, if it is no number or is not above 0.
    """
    if value is None:
        return None
    number: float | None = None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            pass
    elif isinstance(value, int | float) and not isinstance(value, bool):
        number = float(value)
    if number is None or not math.isfinite(number) or number <= 0:
        raise HTTPException(
            422,
            "The deadlock alarm time must be a number of seconds above 0, "
            f"not {json.dumps(value)}. Nothing was launched.",
        )
    return number


def _shipped_or_refused(name: str) -> Topology:
    """The shipped topology `name`, as a route reads it: refused with 404 where the package ships
    no topology by that name, and with 422 and the reason where loading refuses the one it ships
    (`shipped_topology`, the one check of a shipped name)."""
    try:
        return shipped_topology(name)
    except UnknownTopology as error:
        raise HTTPException(404, str(error)) from None
    except TopologyRefused as error:
        raise HTTPException(422, str(error)) from None


def _launch(
    launcher: Launcher,
    chosen: Topology,
    name: str,
    transit_seconds: float | None,
    module_strategy: str,
    wms_strategy: str,
    collect_history: bool,
    deadlock_alarm_seconds: float | None,
    collect_refusals: bool,
) -> LauncherState:
    """Launch `chosen`, which has passed the load check, under the guards of every launch from the
    page, and answer the state it leaves: see `POST /api/launch`."""
    try:
        launcher.launch(
            chosen,
            name=name,
            transit_seconds=transit_seconds,
            module_strategy=module_strategy,
            wms_strategy=wms_strategy,
            collect_history=collect_history,
            deadlock_alarm_seconds=deadlock_alarm_seconds,
            collect_refusals=collect_refusals,
        )
    except UnknownStrategy as error:
        raise HTTPException(422, str(error)) from None
    except LiveInstanceError as error:
        raise HTTPException(409, str(error)) from None
    except LaunchFailed as error:
        raise HTTPException(502, f"The launch failed: {error}") from None
    return launcher.state()


def create_app(launcher: Launcher) -> FastAPI:
    """The Launcher's app.

    A route that blocks is a plain function, so FastAPI runs it off the event loop. Every page of
    every process is under `/p/<id>/`, through the Launcher's own port: see `passthrough`. While
    the app serves, the Launcher reads the boxes on the station in the background
    (`boxes.StationBoxes`), whether a page is open or not, so that `GET /api/state` answers them
    from memory.
    """
    # Looked up at each request, as every route below looks up what it calls.
    passthrough = Passthrough(lambda id: launcher.process(id))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """The passthrough's pool of connections, and the background read of the boxes on the
        station, while the app serves."""
        boxes = launcher.boxes
        if boxes is not None:
            boxes.start()
        try:
            async with passthrough.lifespan(app):
                yield
        finally:
            if boxes is not None:
                boxes.stop()

    app = FastAPI(title="jms-uc2 Launcher", lifespan=lifespan)
    passthrough.mount(app)

    @app.get("/api/state")
    def state() -> LauncherState:
        return launcher.state()

    @app.post("/api/launch")
    def launch(command: LaunchCommand) -> LauncherState:
        """Clear the dataset, seed the shipped topology `name`, and restart every process.

        Refused with 409 Conflict while a service in the dataset that no process of this
        Launcher left behind is live. The conflict is with the dataset's state, and it ends
        when that service does, so the same request can succeed later. The page has no way to
        force a launch: forcing is for the person at the command line. Refused with 422, before
        anything is cleared, when loading the topology refuses it or the transit time is no number
        or is negative, and with 502 when seeding or starting a process failed; nothing runs then.
        `transit_time` is the transit time in seconds of every module that has none of its own in
        the topology, 0 allowed; left out, it is `jms-uc2 --transit-time`. `module_strategy` and
        `wms_strategy` name the strategy every module runs with and the one the
        WarehouseManagementSystem runs with, among `GET /api/strategies`, both `real` if left out;
        a name that none of its kind has is refused with 422, naming it and the strategies of its
        kind, before anything is stopped or cleared. `collect_history`, off if left out, has the
        launch's reset turn on the store's history collection, after it loaded the ontology, so
        that past possessions can be read; a launch without it leaves the collection as it is.
        `deadlock_alarm_seconds` is how long a box a module holds may wait for a Reserve before
        the module raises the deadlock alarm for it, a number of seconds above 0; left out, it is
        `jms-uc2 --deadlock-alarm-seconds`, 300 s by default; a value that is no number or is not
        above 0 is refused with 422, before anything is stopped or cleared. `collect_refusals`, off
        if left out, starts the launch's monitor in "graph + streams", which follows the
        participants' handover event streams and so collects their refusals; without it the
        monitor starts in "graph only".
        """
        transit_seconds = transit_time_of(command.transit_time)
        alarm_seconds = deadlock_alarm_of(command.deadlock_alarm_seconds)
        return _launch(
            launcher,
            _shipped_or_refused(command.name),
            command.name,
            transit_seconds,
            command.module_strategy,
            command.wms_strategy,
            command.collect_history,
            alarm_seconds,
            command.collect_refusals,
        )

    @app.post("/api/topology/check")
    def check(module_list: ModuleListBody) -> TopologyCheck:
        """Run the load check on the module list in the body, and seed nothing.

        Answers whether it passes, and the load check's reason where it does not. A body that is
        no module list fails the check too, with what is wrong with it and the format it needs.
        """
        try:
            launchable(edited_topology(module_list))
        except TopologyRefused as error:
            return TopologyCheck(passes=False, reason=str(error))
        return TopologyCheck(passes=True, reason=None)

    @app.post("/api/topology/launch")
    def launch_edited(
        module_list: ModuleListBody,
        base: str | None = None,
        transit_time: str | None = None,
        module_strategy: str = REAL,
        wms_strategy: str = REAL,
        collect_history: bool = False,
        deadlock_alarm_seconds: str | None = None,
        collect_refusals: bool = False,
    ) -> LauncherState:
        """Clear the dataset, seed the module list in the body, and restart every process, as a
        launch of a shipped topology does.

        The load check runs here again, whether or not the page ran it, and nothing the body says
        of its own result counts. Refused with 422 and the check's reason, before anything is
        stopped or cleared, when it fails. Refused with 409 and 502 as `POST /api/launch` is.
        `base`, a query parameter, names the topology the page's editor started from, and the
        page calls the running topology by it (`edited_name`). It names, and nothing is read
        from it: the body is the whole topology. `transit_time`, `module_strategy`,
        `wms_strategy`, `collect_history`, `deadlock_alarm_seconds` and `collect_refusals`, query
        parameters, are as in `POST /api/launch`.
        """
        transit_seconds = transit_time_of(transit_time)
        alarm_seconds = deadlock_alarm_of(deadlock_alarm_seconds)
        try:
            chosen = launchable(edited_topology(module_list))
        except TopologyRefused as error:
            raise HTTPException(422, str(error)) from None
        return _launch(
            launcher,
            chosen,
            edited_name(base),
            transit_seconds,
            module_strategy,
            wms_strategy,
            collect_history,
            alarm_seconds,
            collect_refusals,
        )

    @app.post("/api/topology/layout")
    def layout(module_list: ModuleListBody) -> TopologyLayout:
        """The modules of the module list in the body, and a cell on a grid for each, for the
        page's editor to draw. Seeds nothing, and runs no load check: a topology that fails it is
        one a person edits until it passes. Refused with 422 and the reason where the body is no
        module list that can be seeded.
        """
        try:
            topology = launchable(parse_topology(module_list))
        except TopologyRefused as error:
            raise HTTPException(422, str(error)) from None
        return TopologyLayout.of(topology)

    @app.post("/api/topology/file")
    async def topology_file_layout(request: Request) -> TopologyLayout:
        """The modules of a topology file, whose bytes are the body, and a cell on a grid for
        each, as `POST /api/topology/layout` gives them: the page's editor opens a file a person
        uploads with this. The server reads the file as `jms-uc2 --topology <path>` reads one, with
        `parse_topology_file`: JSON in UTF-8, a module list. Seeds nothing, and runs no load check.
        Refused with 422 and the reason where the file holds no module list that can be seeded.
        The body is taken as bytes, whatever its type, so FastAPI reads none of it.
        """
        try:
            topology = launchable(parse_topology_file(await request.body()))
        except TopologyRefused as error:
            raise HTTPException(422, str(error)) from None
        return TopologyLayout.of(topology)

    @app.post("/api/processes/stop")
    def stop(command: ProcessCommand) -> LauncherState:
        try:
            launcher.stop(_resource(command.resource))
        except NoProcess:
            raise HTTPException(404, f"no process for {command.resource}") from None
        return launcher.state()

    @app.post("/api/processes/stop-all")
    def stop_all() -> LauncherState:
        launcher.stop_all()
        return launcher.state()

    @app.post("/api/clear")
    def clear() -> Cleared:
        """Stop every process, then clear every graph of the dataset, one by one, and answer
        how many statements it holds then: the page says "<dataset> is empty: 0 statements".

        One request for each graph, never one for the whole dataset, which the store refuses
        while it collects history. The next launch loads everything again. Refused with 409
        Conflict, as a launch is, while a service in the dataset that no process of this
        Launcher left behind is live: nothing is cleared then, and this Launcher's processes stay
        stopped. The page has no way to force a clear. Refused with 502, with why, when the graph
        cannot say which services are live, and nothing is cleared; and when the store did not
        list or clear a graph, naming it, or did not say how many statements are left.
        """
        try:
            return launcher.clear()
        except LiveInstanceError as error:
            raise HTTPException(409, str(error)) from None
        except ClearFailed as error:
            raise HTTPException(502, str(error)) from None

    @app.post("/api/modules/faults")
    def fault(command: ModuleFaultCommand) -> LauncherState:
        """Arm or disarm one fault of one module, and hold what was commanded.

        Refused with 409 Conflict while the module has no live service, or does not take the
        switch; what the Launcher held stays as it was.
        """
        try:
            launcher.set_fault(_resource(command.module), command.name, command.armed)
        except NoProcess:
            raise HTTPException(404, f"no module {command.module}") from None
        except NotReachable as error:
            raise HTTPException(409, str(error)) from None
        return launcher.state()

    @app.get("/api/topologies")
    def topologies() -> list[str]:
        """The names a launch may give. The page's picker lists these and nothing else."""
        return topology_names()

    @app.get("/api/strategies")
    def strategies() -> StrategyChoices:
        """The strategies a launch may name, by kind: `module` and `wms`, each with its `name`
        and its `label`. The page's two pickers, "Module strategy" and "WMS strategy", list these
        by label and nothing else."""
        return launcher.strategies()

    @app.get("/api/topology/format")
    def module_list_names() -> ModuleListNames:
        """The names the page's editor writes a module list with."""
        return ModuleListNames()

    @app.get("/api/topologies/{name}")
    def shipped(name: str) -> list[dict[str, Any]]:
        """The shipped topology `name`, as a module list, which the page's editor starts from."""
        return _shipped_or_refused(name).module_list()

    @app.get("/api/directions")
    def directions() -> list[Direction]:
        """The directions a module may connect in, in the order the page gives them a column."""
        return list(DIRECTIONS)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        """No icon, and no 404 in the browser's console for the lack of one."""
        return Response(status_code=204)

    # Last, so that every route above takes precedence over a file of the same path.
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="page")
    return app
