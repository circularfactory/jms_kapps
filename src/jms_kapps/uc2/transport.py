"""The job of carrying one box, as the graph records it: one BoxTransportOperation for each box.

The transport names the box, its origin and its destination, and the box links back to its
transport (`fc:hasTransport`): the opening writes both, so a party that holds a box finds the
transport through the box, never from the box's name. Its status is the middleware's
`svc:hasOperationStatus`, and it says where the journey is:

- `queued` from the moment it is opened, in the commit that creates the box on its origin, until
  the origin is notified;
- `running` once the origin was notified and carries the box on;
- `done` once the box is back in the WarehouseManagementSystem's possession, written in the same
  commit that makes it the possessor;
- `failed` when the party that cannot go on ends it: on the station the module it is assigned
  to, or the WarehouseManagementSystem when it cannot notify the origin. Its
  `svc:hasExecutionResult` says why.


**A transport is assigned to one module at a time**: the one that carries the box on now. Core's
`cfc:executedOn` names it, and `cfc:implementsCapability` names that module's
`:BoxTransportCapability`. The WarehouseManagementSystem opens the transport assigned to the
origin, on which it creates the box. At each hop both move to the next module in the entry commit
of the handover that passes the box, so the assigned module is also the box's possessor, and only
it writes the transport on the station: a module that has just passed both on and then cannot
reach the receiving module writes nothing, and the receiving module ends the transport. The
WarehouseManagementSystem writes it only in the create that opens it and when it cannot notify
the origin. The box has no state of its own.

Every write of a transport is a commit of the middleware: one `mw.create()` opens it together with
the box and the box's first possession, by the origin, each change of status is a `mw.commit()`,
and each reassignment and the last status, `done`, join the entry commit of a handover.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kapps_ogm import Node, Update
from kapps_ogm.utils.class_scope import ClassScope
from kapps_semantic_middleware.operation import operation as operation_scope
from kapps_semantic_middleware.possession import workpiece
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc2.vocabulary import FC, transport_iri

# Typed as the OGM's scope takes them: a list is invariant, so a list of IRIs alone is refused.
_TRANSPORT_CHAINS: list[list[str | IRI]] = [
    [FC.hasBox],
    [FC.hasOrigin],
    [FC.hasDestination],
    [SVC.hasOperationStatus],
    [CFC.executedOn],
    [CFC.implementsCapability],
]

# What a module reads of a transport it is notified of: the scope it gives the middleware's
# intake, which reads the transport once with it.
TRANSPORT_SCOPE = ClassScope.from_property_chains(_TRANSPORT_CHAINS)

# What the WarehouseManagementSystem reads of a transport that ended: the same, and why it failed.
ENDED_TRANSPORT_SCOPE = ClassScope.from_property_chains(
    [*_TRANSPORT_CHAINS, [SVC.hasExecutionResult]]
)

# What a module reads of a box it holds: the box with its transport, in one fetch through the
# box's link to it, and of the transport what the transport scope reads.
BOX_SCOPE = ClassScope.from_property_chains(
    [[FC.hasTransport, *chain] for chain in _TRANSPORT_CHAINS]
)


@dataclass(frozen=True)
class Transport:
    """One transport as the graph holds it.

    `assigned` is the module it is assigned to (`cfc:executedOn`), and `result` why it failed.
    """

    operation: IRI
    box: IRI
    origin: IRI
    destination: IRI
    status: str
    assigned: IRI | None = None
    result: str | None = None


def transport_from(node: Node) -> Transport:
    """The transport that the middleware's intake hands a party, as the domain code takes it.

    The intake reads the transport once, under the party's class scope, into a node: a linked
    value is a node with an `id`, and a literal is the value itself.
    """

    def first(prop: IRI) -> object | None:
        values = [getattr(v, "id", v) for v in (node.data or {}).get(prop, [])]
        return values[0] if values else None

    assigned, result = first(CFC.executedOn), first(SVC.hasExecutionResult)
    return Transport(
        operation=IRI(str(node.id)),
        box=IRI(str(first(FC.hasBox))),
        origin=IRI(str(first(FC.hasOrigin))),
        destination=IRI(str(first(FC.hasDestination))),
        status=str(first(SVC.hasOperationStatus)),
        assigned=IRI(str(assigned)) if assigned is not None else None,
        result=str(result) if result is not None else None,
    )


def transport_of_box(node: Node) -> Transport | None:
    """The transport of the box that `node` is, read under `BOX_SCOPE`, or None if the box links
    to none.

    A box has one transport. The dataset's reasoner infers the box's link from the
    transport's `fc:hasBox` as well as reading the written one, and the two are one value.
    """
    linked = (node.data or {}).get(FC.hasTransport, [])
    return transport_from(linked[0]) if linked else None


def operation_status(db: Queryable, operation: IRI) -> str | None:
    """The `svc:hasOperationStatus` of `operation`, or None if it has none."""
    rows = bindings(
        db,
        f"SELECT ?status WHERE {{ <{operation}> <{SVC.hasOperationStatus}> ?status }} LIMIT 1",
    )
    return str(rows[0]["status"]) if rows else None


def execution_result(db: Queryable, operation: IRI) -> str | None:
    """The `svc:hasExecutionResult` of `operation`: why it failed. None if it has none."""
    rows = bindings(
        db,
        f"SELECT ?result WHERE {{ <{operation}> <{SVC.hasExecutionResult}> ?result }} LIMIT 1",
    )
    return str(rows[0]["result"]) if rows else None


def boxes(db: Queryable) -> list[IRI]:
    """Every box in the graph: a read whose answer grows with the store, which no participant
    makes; a test checks the graph with it."""
    rows = bindings(db, f"SELECT ?box WHERE {{ ?box a <{FC.Box}> }}")
    return [IRI(str(row["box"])) for row in rows]


def failure(reason: str, report: str | None = None) -> str:
    """What a failed transport records as its result: the reason, then the store's validation
    report on the lines after it, verbatim, when a refusal by the store is the reason."""
    return f"{reason}\n{report}" if report else reason


def reason_and_report(result: str) -> tuple[str, str | None]:
    """The reason and the report of a failed transport's result, as `failure` wrote them."""
    reason, _, report = result.partition("\n")
    return reason, report or None


def status_record(resource: IRI, operation: IRI, status: str) -> str:
    """What `resource` logs once its commit of `status` for `operation` was admitted.

    Each party that changes a transport's status logs it, so the log shows the journey's
    transitions in the order the store admitted them, and who made each. The tests match the
    record whole, so every party builds it here.
    """
    return f"{resource.fragment}: {operation.fragment} is {status}"


def reassignment(operation: IRI, module: IRI, capability: IRI) -> list[Update]:
    """The change that assigns the transport `operation` to `module`, whose BoxTransportCapability
    is `capability`: for the entry commit of the handover that passes the box to `module`."""
    return [
        Update(
            instance_iri=operation,
            data={
                str(CFC.executedOn): [{"id": str(module)}],
                str(CFC.implementsCapability): [{"id": str(capability)}],
            },
        )
    ]


class Commits(Protocol):
    """The middleware's writes that the records use: `SemanticMiddleware` is the real one."""

    def create(self, cls: str, scope: object, instance_iri: str | None = None): ...

    def commit(
        self, *instances: str, class_scope=None, entry=(), exit=(), error=()
    ): ...


class TransportRecords:
    """The writes of a box's transport, each a commit of the middleware `mw`.

    A write the store refuses raises the triplestore interface's `ShaclValidationError`, which the
    middleware passes on unchanged; the caller catches that type.
    """

    def __init__(self, mw: Commits) -> None:
        self.mw = mw

    def open(
        self,
        box: IRI,
        *,
        origin: IRI,
        destination: IRI,
        capability: IRI,
        possessor: IRI,
    ) -> IRI:
        """Open the transport of a new `box`, `queued`, in one commit with the box itself and its
        first possession by `possessor`, and return the transport's IRI.

        The opening chooses the transport's IRI, and writes the link both ways: the transport
        names its box (`fc:hasBox`), and the box its transport (`fc:hasTransport`). So a party
        that holds the box finds the transport through the box, and needs no rule that names a
        transport after its box.

        The transport is assigned to `origin`, and implements `capability`, the origin's
        BoxTransportCapability. The box points at the possessor's one possession state, which
        the same commit creates when the possessor has none yet.

        Raises:
            kapps_triplestore_interface.PreconditionFailedError: If the box or its transport
                is in the graph already (from the object-graph mapper's checked write), and
                nothing is written.
            kapps_triplestore_interface.ShaclValidationError: If the store refuses the commit,
                with its validation report, and nothing is written.
        """
        operation = transport_iri(box)
        with self.mw.create(
            str(FC.BoxTransportOperation),
            operation_scope(ClassScope(), capability=str(capability)),
            instance_iri=str(operation),
        ) as opened:
            with self.mw.create(
                str(FC.Box),
                workpiece(ClassScope(), possessor=str(possessor)),
                instance_iri=str(box),
            ) as created:
                created.update({str(FC.hasTransport): [opened.iri]})
            opened.update(
                {
                    str(FC.hasBox): [created.iri],
                    str(FC.hasOrigin): [origin],
                    str(FC.hasDestination): [destination],
                    str(SVC.hasOperationStatus): [OperationStatus.QUEUED],
                    str(CFC.executedOn): [origin],
                }
            )
        return operation

    def start(self, operation: IRI) -> None:
        """Set the transport `operation` `running`: its origin carries the box on.

        Raises:
            kapps_triplestore_interface.ShaclValidationError: If the store refuses the commit, as
                it does while no module holds the box, and nothing is written.
        """
        self._set(operation, {str(SVC.hasOperationStatus): [OperationStatus.RUNNING]})

    def complete(self, operation: IRI) -> None:
        """Set the transport `operation` `done` by itself, without the handover that gives its box
        back: the write of the fault `complete-without-releasing`. The proper end is the last
        handover, whose entry sets the transport `done` together with the box's return.

        Raises:
            kapps_triplestore_interface.ShaclValidationError: If the store refuses the commit, as
                it does while a module holds the box, and nothing is written.
        """
        self._set(operation, {str(SVC.hasOperationStatus): [OperationStatus.DONE]})

    def fail(self, operation: IRI, reason: str, report: str | None = None) -> None:
        """End the transport `operation` as `failed`, with why as its result."""
        self._set(
            operation,
            {
                str(SVC.hasOperationStatus): [OperationStatus.FAILED],
                str(SVC.hasExecutionResult): [failure(reason, report)],
            },
        )

    def _set(self, operation: IRI, data: dict[str, list]) -> None:
        with self.mw.commit(entry=[Update(instance_iri=operation, data=data)]):
            pass
