"""Services as the one reader gives them, for a test's stand-in of the graph.

A stand-in says which resources are live, and where; this builds what the reader would have read
for each: a live service, with its address and a fresh heartbeat, and every workflow it was asked
for, as a service that has registered all of them.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime

from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service


def live_service(
    resource: IRI, address: str | None = None, workflows: Collection[IRI] = ()
) -> Service:
    """The live service of `resource` at `address`, which has registered every workflow of the
    classes in `workflows`. `address` None is a stand-in that says nothing of where it is."""
    return Service(
        iri=IRI(f"{resource}_service"),
        resource=IRI(resource),
        address=address,
        heartbeat=datetime.now(UTC),
        workflows={IRI(c): f"{address}/{IRI(c).fragment}" for c in workflows},
    )


def live_services(
    live: Collection[IRI],
    workflows: Collection[IRI] = (),
    addresses: Mapping[IRI, str] | None = None,
) -> list[Service]:
    """A live service for each resource in `live`, at its address in `addresses`, with every
    workflow of the classes in `workflows`."""
    addresses = addresses or {}
    return [live_service(r, addresses.get(r), workflows) for r in live]
