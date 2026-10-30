"""What a module registers in the graph as it comes online, and what its stop removes, read from a
live store.

Each test seeds Module 1, connected east to Module 2, and serves the WarehouseManagementSystem and
both modules in this process (`_participants`), each built as its process builds it: the module's
middleware registers its service and its workflows as it starts, and deregisters as it stops. What
the tests read is the graph, through the one reader of a service (`common.services`).

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

from functools import partial

import pytest
from _participants import M1, M2, participants
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service, live_resources, services
from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc2.discovery import capability
from jms_kapps.uc2.vocabulary import FC, FC_NS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# The workflows a module registers, each with the capability it realizes.
WORKFLOWS = {
    FC.ReserveWorkflow: FC.ReserveCapability,
    FC.ReceiveWorkflow: FC.ReceiveCapability,
    FC.BoxTransportWorkflow: FC.BoxTransportCapability,
}

# The term the UC2 ontology once had for a Convey workflow, taken out since.
CONVEY_WORKFLOW = IRI("ConveyWorkflow", base=FC_NS)

# A module registers its workflows just after its server has started; a shared store is slower.
REGISTERED_WITHIN_SECONDS = 60.0


def service_of(db: Queryable, module: IRI, *, live_only: bool = True) -> Service:
    """The one service of `module`, with the endpoint of each of its workflows of the classes a
    module registers, and of a Convey."""
    (service,) = services(
        db,
        resource=module,
        workflows=(*WORKFLOWS, CONVEY_WORKFLOW),
        live_only=live_only,
    )
    return service


def registered(db: Queryable, module: IRI) -> bool:
    """Whether `module`'s live service has registered every workflow a module registers."""
    found = services(db, resource=module, workflows=tuple(WORKFLOWS))
    return bool(found) and set(WORKFLOWS) <= set(found[0].workflows)


def workflow_classes(db: Queryable, service: IRI) -> set[str]:
    """The classes, of those a module registers and a Convey, of the workflows that `service` has
    in the graph, with an endpoint or without."""
    classes = " ".join(f"<{c}>" for c in (*WORKFLOWS, CONVEY_WORKFLOW))
    rows = bindings(
        db,
        f"""
        SELECT DISTINCT ?class WHERE {{
            VALUES ?class {{ {classes} }}
            ?workflow <{SVC.isWorkflowOf}> <{service}> ; a ?class .
        }}
        """,
    )
    return {str(row["class"]) for row in rows}


def test_a_module_that_comes_online_registers_reserve_receive_and_its_box_transport_workflow(
    dataset,
):
    """A module's workflows are each instantiated when the module comes online, and its middleware
    registers them in the graph. A module registers three: Reserve, Receive and
    the BoxTransportWorkflow, each realizing its capability, which the module provides. No module
    registers a Convey: a module's convey is its own move. Each endpoint lies
    under the address of the module's live service, where a peer calls it."""
    with participants(dataset) as party:
        for module in (M1, M2):
            eventually(
                partial(registered, party.db, module), timeout=REGISTERED_WITHIN_SECONDS
            )
            service = service_of(party.db, module)

            assert service.address is not None
            assert set(service.workflows) == set(WORKFLOWS), service
            for endpoint in service.workflows.values():
                assert endpoint.startswith(f"{service.address.rstrip('/')}/"), service
            for capability_class in WORKFLOWS.values():
                assert capability(party.db, module, capability_class) is not None, (
                    f"{module.fragment} provides no {capability_class.fragment}"
                )


def test_a_module_that_stops_removes_its_address_and_keeps_its_service_and_workflows(
    dataset,
):
    """On shutdown, the middleware removes the address of the service, so that no workflow of the
    service can be called, and keeps the service and its workflows in the graph. The block's end stops every party as a stop signal stops a
    process: its server shuts down, and its middleware deregisters on the way out. Each endpoint
    goes with the address."""
    with participants(dataset) as party:
        db = party.db
        for module in (M1, M2):
            eventually(
                partial(registered, db, module), timeout=REGISTERED_WITHIN_SECONDS
            )
        online = {module: service_of(db, module).iri for module in (M1, M2)}

    for module in (M1, M2):
        service = service_of(db, module, live_only=False)

        assert service.iri == online[module]
        assert service.address is None, service
        assert service.workflows == {}, service
        assert workflow_classes(db, service.iri) == {str(w) for w in WORKFLOWS}
    assert live_resources(db) & {M1, M2} == set()
