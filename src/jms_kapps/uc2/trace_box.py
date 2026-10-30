"""`jms-uc2 trace-box`: every change of a box's possession, read back from the triple store's
history.

A handover overwrites the box's link to its possessor's possession state, so the knowledge graph
holds only the present possessor. The triple store's history holds every change, each with its
transaction and its time (`history`). This command shows one box's way through the station from
there. On a disposable dataset of the configured store it:

1. seeds the topology `line`, four modules one after another, and turns the dataset's history
   collection on;
2. starts the four modules and the WarehouseManagementSystem through the run core, without the
   Launcher, and waits until each is ready;
3. spawns one box through the WarehouseManagementSystem's spawn workflow, from the line's entry
   module to its exit module, with the normal transit time;
4. waits until the box's transport is done and the WarehouseManagementSystem has logged the box
   back, then stops every participant;
5. reads the change list of the box's possession link, of the transport's status and of its
   assignment (`cfc:executedOn`), and groups the changes by transaction: one row each.

It prints the rows as one table, with each time in UTC and in the server's local time, and writes
them as JSON (`--out`, `trace-<box>.json` in the current folder by default), with a cross-check:
the possessors in the order of their transactions, against the handovers the participants
logged: the WarehouseManagementSystem's spawn onto the origin, each module's convey to the next,
and the WarehouseManagementSystem's take-back. It exits 0 when the two agree, and 1 otherwise,
or when the store cannot be reached, the run does not start, or the transport does not end.

The history collection stays on (`history`), so the command takes only a name that starts with
`ogm-disposable-`. It works on a dataset whose collection is on already, and again on the same
dataset: the box's own name keeps the runs apart.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TextIO

import httpx
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from kapps_triplestore_interface.exceptions import TripleStoreInterfaceError

from jms_kapps.common.services import reader, reader_of_one
from jms_kapps.common.supervision import (
    LaunchFailed,
    LiveInstanceError,
    NotReady,
    Supervisor,
    refuse_if_live,
)
from jms_kapps.common.triplestore import Queryable, bindings, connect
from jms_kapps.uc2.discovery import workflow_endpoint
from jms_kapps.uc2.handshake import CONVEYED
from jms_kapps.uc2.history import Change, changes
from jms_kapps.uc2.process import module_command
from jms_kapps.uc2.run_core import (
    Participant,
    RunGraph,
    TripleStoreGraph,
    participants_of,
    start_run,
)
from jms_kapps.uc2.seed import DEFAULT_TRANSIT_SECONDS
from jms_kapps.uc2.topology import Topology, shipped_topology
from jms_kapps.uc2.transport import operation_status
from jms_kapps.uc2.vocabulary import FC, WMS
from jms_kapps.uc2.wms import SPAWNED, TAKEN_BACK
from jms_kapps.uc2.wms_process import wms_command

PROGRAM = "jms-uc2 trace-box"

# The datasets the command takes: the store helper's disposable ones.
DISPOSABLE_PREFIX = "ogm-disposable-"

# The shipped topology the command runs on.
TOPOLOGY = "line"

# How long the transport of the box may take, in seconds: four modules at a few seconds each,
# with room for a slow store.
TRANSPORT_TIMEOUT_SECONDS = 120.0

# How long the WarehouseManagementSystem may take to log the box back once its transport is done:
# the notification comes after the commit.
TAKE_BACK_TIMEOUT_SECONDS = 30.0

# The transport's statuses after which it changes no more.
ENDED = (OperationStatus.DONE, OperationStatus.FAILED)


class Transaction(NamedTuple):
    """One transaction of the history that changed the box's possession, or its transport's status
    or assignment: its time in UTC and in the server's local time, the box, and what it set. A
    member is None where the transaction did not set it."""

    transaction: IRI
    utc: datetime
    server_time: datetime
    box: IRI
    possessor: IRI | None
    status: str | None
    assigned: IRI | None


class CrossCheck(NamedTuple):
    """The possessors of the box in the order of their transactions, and in the order of the
    handovers the participants logged, by their fragments, and whether the two agree."""

    from_history: list[str]
    from_logs: list[str]
    agree: bool


@dataclass(frozen=True)
class Record:
    """What `--out` holds: the box, its transport, the dataset, the rows and the cross-check."""

    box: IRI
    transport: IRI
    dataset: str
    transactions: list[Transaction]
    cross_check: CrossCheck


def transactions(
    found: Iterable[Change], possessors: Mapping[str, IRI], box: IRI
) -> list[Transaction]:
    """The rows of the changes `found` of `box`'s possession and of its transport's status and
    assignment: one for each transaction, in the order of their time. `possessors` names the
    possessor of each possession state."""
    grouped: dict[IRI, list[Change]] = {}
    for change in sorted(
        found, key=lambda c: (c.timestamp.astimezone(UTC), c.transaction)
    ):
        grouped.setdefault(change.transaction, []).append(change)
    rows = []
    for transaction, made in grouped.items():
        set_to = {c.predicate: c.value for c in made if c.inserted}
        state = set_to.get(CFC.hasPossessedWorkpiece)
        assigned = set_to.get(CFC.executedOn)
        stamp = made[0].timestamp
        rows.append(
            Transaction(
                transaction,
                stamp.astimezone(UTC),
                stamp,
                box,
                None if state is None else possessors.get(state, IRI(state)),
                set_to.get(SVC.hasOperationStatus),
                None if assigned is None else IRI(assigned),
            )
        )
    return rows


def possessors_of(db: Queryable, states: Iterable[str]) -> dict[str, IRI]:
    """The possessor of each of the possession states `states`. A resource's link to its own
    possession state never changes, so the live data answers it."""
    wanted = sorted(set(states))
    if not wanted:
        return {}
    values = " ".join(f"<{state}>" for state in wanted)
    rows = bindings(
        db,
        f"SELECT ?possessor ?state WHERE {{ VALUES ?state {{ {values} }} "
        f"?possessor <{CFC.hasPossessor}> ?state }}",
        convert=False,
    )
    return {row["state"]["value"]: IRI(row["possessor"]["value"]) for row in rows}


def logged_possessors(lines: Iterable[str], box: IRI) -> list[str]:
    """The possessors of `box` in the order of the handovers the participants logged, by their
    fragments: the origin of its spawn, the receiver of each convey, from the possessor of the
    moment on, then the WarehouseManagementSystem that took it back. `lines` are the lines of
    every participant, each participant's in the order it wrote them."""
    origin: str | None = None
    conveyed: dict[str, list[str]] = {}
    taken_back: str | None = None
    for line in lines:
        if found := SPAWNED.search(line):
            if found["box"] == box.fragment:
                origin = found["origin"]
        elif found := CONVEYED.search(line):
            if found["box"] == box.fragment:
                conveyed.setdefault(found["resource"], []).append(found["receiver"])
        elif (found := TAKEN_BACK.search(line)) and found["box"] == box.fragment:
            taken_back = found["resource"]
    if origin is None:
        return []
    way = [origin]
    while conveyed.get(way[-1]):
        way.append(conveyed[way[-1]].pop(0))
    if taken_back is not None:
        way.append(taken_back)
    # A logged convey the way did not reach is a handover the history must show too.
    for receivers in conveyed.values():
        way.extend(f"(unreached) {receiver}" for receiver in receivers)
    return way


def cross_check(
    rows: Sequence[Transaction], lines: Iterable[str], box: IRI
) -> CrossCheck:
    """The possessors of the rows `rows`, in their order, against those of the handovers the
    participants logged in `lines`."""
    from_history = [row.possessor.fragment for row in rows if row.possessor is not None]
    from_logs = logged_possessors(lines, box)
    return CrossCheck(from_history, from_logs, from_history == from_logs)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _server(moment: datetime) -> str:
    return moment.isoformat(sep=" ", timespec="milliseconds")


def _name(iri: IRI | None) -> str:
    return "-" if iri is None else iri.fragment


def _transaction_id(transaction: IRI) -> str:
    return str(transaction).rsplit("/", 1)[-1]


COLUMNS = (
    "time (UTC)",
    "time (server)",
    "box",
    "possessor",
    "status",
    "assigned module",
    "transaction",
)


def table(rows: Sequence[Transaction]) -> str:
    """The rows as a table: a header, a rule, and one line for each transaction."""
    cells = [
        [
            _utc(row.utc),
            _server(row.server_time),
            row.box.fragment,
            _name(row.possessor),
            row.status or "-",
            _name(row.assigned),
            _transaction_id(row.transaction),
        ]
        for row in rows
    ]
    widths = [
        max(len(column), *(len(line[i]) for line in cells)) if cells else len(column)
        for i, column in enumerate(COLUMNS)
    ]

    def line(values: Sequence[str]) -> str:
        return "  ".join(
            v.ljust(w) for v, w in zip(values, widths, strict=True)
        ).rstrip()

    return "\n".join(
        [line(COLUMNS), line(["-" * w for w in widths]), *(line(c) for c in cells)]
    )


def record(
    *,
    box: IRI,
    transport: IRI,
    dataset: str,
    rows: Sequence[Transaction],
    check: CrossCheck,
) -> dict[str, Any]:
    """What `--out` holds, as JSON: `parse_record` reads it back."""

    def iri(value: IRI | None) -> str | None:
        return None if value is None else str(value)

    return {
        "box": str(box),
        "transport": str(transport),
        "dataset": dataset,
        "topology": TOPOLOGY,
        "transactions": [
            {
                "transaction": str(row.transaction),
                "utc": row.utc.isoformat(),
                "server_time": row.server_time.isoformat(),
                "box": str(row.box),
                "possessor": iri(row.possessor),
                "status": row.status,
                "assigned": iri(row.assigned),
            }
            for row in rows
        ],
        "cross_check": {
            "from_history": check.from_history,
            "from_logs": check.from_logs,
            "agree": check.agree,
        },
    }


def parse_record(data: Mapping[str, Any]) -> Record:
    """The record `data`, as `record` wrote it.

    Raises:
        ValueError: If it lacks a member or a time carries no offset from UTC.
    """
    try:

        def iri(value: str | None) -> IRI | None:
            return None if value is None else IRI(value)

        rows = []
        for row in data["transactions"]:
            utc = datetime.fromisoformat(row["utc"])
            server_time = datetime.fromisoformat(row["server_time"])
            if utc.tzinfo is None or server_time.tzinfo is None:
                raise ValueError(f"a time of {row['transaction']} carries no offset")
            rows.append(
                Transaction(
                    IRI(row["transaction"]),
                    utc.astimezone(UTC),
                    server_time,
                    IRI(row["box"]),
                    iri(row["possessor"]),
                    row["status"],
                    iri(row["assigned"]),
                )
            )
        check = data["cross_check"]
        return Record(
            IRI(data["box"]),
            IRI(data["transport"]),
            data["dataset"],
            rows,
            CrossCheck(
                list(check["from_history"]),
                list(check["from_logs"]),
                bool(check["agree"]),
            ),
        )
    except KeyError as missing:
        raise ValueError(f"the record lacks {missing}") from missing


def default_out(box: IRI) -> Path:
    """Where `--out` writes when it is not given: `trace-<box>.json` in the current folder."""
    return Path(f"trace-{box.fragment}.json")


def _lines(supervisor: Supervisor) -> list[str]:
    """Every line the participants wrote so far, each participant's in its order."""
    return [line for child in supervisor.snapshot() for line in child.output]


def _taken_back(lines: Iterable[str], box: IRI) -> bool:
    """Whether the WarehouseManagementSystem has logged `box` back in `lines`."""
    return any(
        (found := TAKEN_BACK.search(line)) is not None and found["box"] == box.fragment
        for line in lines
    )


def _wait(until: Callable[[], bool], timeout: float, interval: float) -> bool:
    """Whether `until` came true within `timeout` seconds, asked every `interval`."""
    deadline = time.monotonic() + timeout
    while True:
        if until():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


class TraceFailed(RuntimeError):
    """The run did not give a box to trace: why."""


def trace(
    topology: Topology,
    graph: RunGraph,
    supervisor: Supervisor,
    participants: Sequence[Participant],
    *,
    history: Queryable,
    spawn: Callable[[IRI, IRI], tuple[IRI, IRI]],
    status: Callable[[IRI], str | None],
    dataset: str,
    out_path: Path | None = None,
    transit_seconds: float = DEFAULT_TRANSIT_SECONDS,
    out: TextIO | None = None,
    interval: float = 0.5,
    transport_timeout: float = TRANSPORT_TIMEOUT_SECONDS,
    take_back_timeout: float = TAKE_BACK_TIMEOUT_SECONDS,
) -> int:
    """Run one box over `topology` and print its trace from the history to `out` (the current
    `sys.stdout` where None), and write it to
    `out_path`, `default_out` where None. Returns 0 when the history agrees with the logs, and 1
    otherwise.

    Args:
        graph: The run's graph, which the seed clears.
        supervisor: The supervisor of `participants`, the modules of `topology` and the WMS.
        history: Where the history is read: the run's dataset.
        spawn: Spawns one box from an origin to a destination, through the
            WarehouseManagementSystem; returns the box and its transport.
        status: The status of a transport, as the graph holds it now.
        dataset: The name of the run's dataset, for the record.

    Raises:
        LaunchFailed: If the run did not start.
        NotReady: If a participant did not get ready.
        TraceFailed: If the spawn was refused.
    """
    # Read at the call, not at the import: a default of `sys.stdout` binds the stream the module
    # saw first, past any later redirect such as pytest's capture.
    out = sys.stdout if out is None else out
    entry, exit_ = topology.modules[0].resource, topology.modules[-1].resource
    print(
        f"1. Seed the topology {TOPOLOGY} with the history collection on, and start "
        f"{len(participants)} participants",
        file=out,
    )
    start_run(
        graph,
        supervisor,
        topology,
        participants,
        transit_seconds=transit_seconds,
        collect_history=True,
    )
    try:
        supervisor.wait_until_ready()
        print(f"2. Spawn one box from {entry.fragment} to {exit_.fragment}", file=out)
        box, transport = spawn(entry, exit_)
        print(f"   {box.fragment}; wait until its transport ends", file=out)
        ended = _wait(lambda: status(transport) in ENDED, transport_timeout, interval)
        ending = status(transport)
        if ended and ending == OperationStatus.DONE:
            _wait(
                lambda: _taken_back(_lines(supervisor), box),
                take_back_timeout,
                interval,
            )
    finally:
        print("3. Stop every participant", file=out)
        supervisor.stop_all()
    lines = _lines(supervisor)

    print(
        "4. Read the change list of the box from the triple store's history", file=out
    )
    found = [
        *changes(history, box, CFC.hasPossessedWorkpiece),
        *changes(history, transport, SVC.hasOperationStatus),
        *changes(history, transport, CFC.executedOn),
    ]
    states = [
        c.value
        for c in found
        if c.predicate == CFC.hasPossessedWorkpiece and c.inserted
    ]
    rows = transactions(found, possessors_of(history, states), box)
    check = cross_check(rows, lines, box)
    print(file=out)
    print(table(rows), file=out)
    print(file=out)
    target = out_path if out_path is not None else default_out(box)
    target.write_text(
        json.dumps(
            record(
                box=box, transport=transport, dataset=dataset, rows=rows, check=check
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"possessors from the history: {', '.join(check.from_history)}", file=out)
    print(f"possessors from the logs:    {', '.join(check.from_logs)}", file=out)
    failed = []
    if not ended:
        failed.append(f"the transport did not end within {transport_timeout:g} s")
    elif ending != OperationStatus.DONE:
        failed.append(f"the transport is {ending}")
    print(
        "the history and the logs "
        + ("agree" if check.agree else "disagree")
        + f"; the trace is in {target}",
        file=out,
    )
    for why in failed:
        print(why, file=out)
    return 0 if check.agree and not failed else 1


def _spawn_through(db: Queryable) -> Callable[[IRI, IRI], tuple[IRI, IRI]]:
    """A spawn through the WarehouseManagementSystem's spawn workflow on `db`."""

    def spawn(origin: IRI, destination: IRI) -> tuple[IRI, IRI]:
        endpoint = workflow_endpoint(db, WMS, FC.SpawnBoxWorkflow)
        if endpoint is None:
            raise TraceFailed(
                "the WarehouseManagementSystem's spawn workflow is not live"
            )
        answer = httpx.post(
            endpoint,
            json={"origin": str(origin), "destination": str(destination)},
            timeout=60,
        ).json()
        if not answer.get("accepted"):
            raise TraceFailed(f"the spawn was refused: {answer.get('reason')}")
        return IRI(answer["box"]), IRI(answer["operation"])

    return spawn


def run(
    dataset: str,
    *,
    out_path: Path | None = None,
    transit_seconds: float = DEFAULT_TRANSIT_SECONDS,
) -> int:
    """Trace one box on `dataset` of the configured store. Returns the exit code of `trace`, or 1
    with the reason on stderr when the name is not a disposable one, the store cannot be reached,
    another run is live there, or the run does not give a box."""
    if not dataset.startswith(DISPOSABLE_PREFIX):
        print(
            f"{PROGRAM}: turns the dataset's history collection on for good, so it takes only a "
            f"name that starts with {DISPOSABLE_PREFIX}, not {dataset}",
            file=sys.stderr,
        )
        return 1
    try:
        db = connect(dataset)
        refuse_if_live(reader(db), dataset=dataset)
        topology = shipped_topology(TOPOLOGY)
        supervisor = Supervisor(
            reader(db), service_of=reader_of_one(db), echo=lambda _: None
        )
        return trace(
            topology,
            TripleStoreGraph(db),
            supervisor,
            participants_of(
                topology,
                module_command=lambda resource: module_command(str(resource), dataset),
                wms_command=wms_command(dataset),
            ),
            history=db,
            spawn=_spawn_through(db),
            status=lambda transport: operation_status(db, transport),
            dataset=dataset,
            out_path=out_path,
            transit_seconds=transit_seconds,
        )
    except (
        ValueError,
        OSError,
        httpx.HTTPError,
        TripleStoreInterfaceError,
        LiveInstanceError,
        LaunchFailed,
        NotReady,
        TraceFailed,
    ) as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1
