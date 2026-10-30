"""The `fc:` terms use case 2 reads and writes, the IRIs of its individuals, and how a page
names an IRI for a person: by its prefix or by its local name.

Middleware vocabulary (`cfc:`, `svc:`, `mes:`) is imported from the middleware, never spelled
here, so the two cannot drift apart.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from kapps_semantic_middleware.vocabulary import (
    CORE_NS,
    MES_NS,
    OWL_NS,
    RDF_NS,
    RDFS_NS,
    SVC_NS,
)
from kapps_triplestore_interface import IRI
from rdflib import Literal
from rdflib.namespace import SH, XSD

FC_NS = "http://w3id.org/circularfactory/FlexConveyor#"
FCI_NS = "http://w3id.org/circularfactory/FlexConveyorInstances#"


class FC:
    """Terms from `ontology/flexconveyor_usecase.ttl`, and the shapes a validation report names.

    The shapes are in `shapes/flexconveyor_usecase_shapes.ttl`, in the same namespace.
    """

    # Classes
    FlexConveyorModule = IRI("FlexConveyorModule", base=FC_NS)
    EntryModule = IRI("EntryModule", base=FC_NS)
    ExitModule = IRI("ExitModule", base=FC_NS)
    WarehouseManagementSystem = IRI("WarehouseManagementSystem", base=FC_NS)
    Box = IRI("Box", base=FC_NS)
    Connection = IRI("Connection", base=FC_NS)
    BoxTransportOperation = IRI("BoxTransportOperation", base=FC_NS)
    FlexConveyorModuleService = IRI("FlexConveyorModuleService", base=FC_NS)
    WarehouseManagementSystemService = IRI(
        "WarehouseManagementSystemService", base=FC_NS
    )

    ReserveWorkflow = IRI("ReserveWorkflow", base=FC_NS)
    ReceiveWorkflow = IRI("ReceiveWorkflow", base=FC_NS)
    SpawnBoxWorkflow = IRI("SpawnBoxWorkflow", base=FC_NS)
    SetSpawnRateWorkflow = IRI("SetSpawnRateWorkflow", base=FC_NS)
    BoxTransportWorkflow = IRI("BoxTransportWorkflow", base=FC_NS)
    ReserveCapability = IRI("ReserveCapability", base=FC_NS)
    ReceiveCapability = IRI("ReceiveCapability", base=FC_NS)
    SpawnBoxCapability = IRI("SpawnBoxCapability", base=FC_NS)
    SetSpawnRateCapability = IRI("SetSpawnRateCapability", base=FC_NS)
    BoxTransportCapability = IRI("BoxTransportCapability", base=FC_NS)

    # Object properties
    hasConnection = IRI("hasConnection", base=FC_NS)
    connectsTo = IRI("connectsTo", base=FC_NS)
    hasOrigin = IRI("hasOrigin", base=FC_NS)
    hasDestination = IRI("hasDestination", base=FC_NS)
    hasDirection = IRI("hasDirection", base=FC_NS)
    hasBox = IRI("hasBox", base=FC_NS)
    hasTransport = IRI("hasTransport", base=FC_NS)

    # Datatype properties
    onPort = IRI("onPort", base=FC_NS)
    hasHandoverEventStream = IRI("hasHandoverEventStream", base=FC_NS)
    hasTransitTime = IRI("hasTransitTime", base=FC_NS)

    # Individuals
    North = IRI("North", base=FC_NS)
    East = IRI("East", base=FC_NS)
    South = IRI("South", base=FC_NS)
    West = IRI("West", base=FC_NS)

    # Shapes
    FlexConveyorModulePossessionShape = IRI(
        "FlexConveyorModulePossessionShape", base=FC_NS
    )
    BoxShape = IRI("BoxShape", base=FC_NS)
    BoxHasOnePossessorShape = IRI("BoxHasOnePossessorShape", base=FC_NS)
    RunningTransportBoxPossessedShape = IRI(
        "RunningTransportBoxPossessedShape", base=FC_NS
    )
    DoneTransportBoxReleasedShape = IRI("DoneTransportBoxReleasedShape", base=FC_NS)
    BoxOnModuleHasTransportShape = IRI("BoxOnModuleHasTransportShape", base=FC_NS)


def local_name(iri: str) -> str:
    """The part of `iri` after its last `#` or `/`, or `iri` itself if that is empty.

    For a person to read only: a local name is never compared, and never turned back into an
    IRI.
    """
    return re.split(r"[#/]", str(iri))[-1] or str(iri)


# The vocabularies whose terms a person reads by prefix in the store's workbench, such as
# `fc:BoxShape` or `sh:MaxCountConstraintComponent`. The use case's individuals are not among them:
# a page names them by local name, such as `Module3`, as it names them everywhere else. The
# monitor's refusal cards show the middleware's short form, the local name, not these prefixes.
PREFIXES = {
    "fc": FC_NS,
    "cfc": CORE_NS,
    "svc": SVC_NS,
    "mes": MES_NS,
    "sh": str(SH),
    "rdf": RDF_NS,
    "rdfs": RDFS_NS,
    "owl": OWL_NS,
    "xsd": str(XSD),
}


def module_iri(n: int) -> IRI:
    """The IRI of FlexConveyorModule `n`."""
    return IRI(f"Module{n}", base=FCI_NS)


def connection_iri(module_id: str, direction: str) -> IRI:
    """The IRI of the connection of the module `module_id` towards `direction`.

    A module has at most one connection in each direction, so the direction names it.
    """
    return IRI(f"{module_id}_connection_{direction}", base=FCI_NS)


def transit_time_literal(seconds: float) -> Literal:
    """A transit time as the graph holds it: an `xsd:decimal` of `seconds`."""
    return Literal(Decimal(str(seconds)))


def box_iri(n: int) -> IRI:
    """The IRI of Box `n`: a box named by a number, as a seed or a test names the boxes it places
    itself. The WarehouseManagementSystem names each box it spawns by a UUID (`new_box_iri`).
    """
    return IRI(f"Box{n}", base=FCI_NS)


UUID_BOX_PREFIX = "Box-"
"""What the local name of a box named by a UUID starts with, before its UUID."""


def uuid_box_iri(box_id: uuid.UUID) -> IRI:
    """The IRI of the box named by the UUID `box_id`: `Box-<UUID>`, in the instances'
    namespace, as the WarehouseManagementSystem and the prefill name their boxes."""
    return IRI(f"{UUID_BOX_PREFIX}{box_id}", base=FCI_NS)


def new_box_iri() -> IRI:
    """The IRI of a new box, named by a new random UUID (version 4): how the
    WarehouseManagementSystem names each box it spawns, so that more than one middleware can
    create boxes without a shared number, and without reading the boxes in the graph.
    """
    return uuid_box_iri(uuid.uuid4())


def transport_iri(box: IRI) -> IRI:
    """The IRI that the WarehouseManagementSystem's opening and the seed give the transport they
    create for `box`.

    Only they call it, to choose the IRI of what they create. No party finds a transport by it:
    a box links to its transport by `fc:hasTransport`, so a party that holds a box reads the
    transport through the box.
    """
    return IRI(f"{box.fragment}_transport", base=FCI_NS)


# The WarehouseManagementSystem. Every run seeds it and starts its process beside the topology
# (`run_core`).
WMS = IRI("WarehouseManagementSystem1", base=FCI_NS)

# The monitor, whose own code writes nothing. A plain `cfc:Resource` behind a plain `svc:Service`,
# as the use case's ontology names no class for it. Only the demo Launcher seeds it and starts it.
MONITOR = IRI("Monitor1", base=FCI_NS)
