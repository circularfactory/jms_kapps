"""The one reader of a participant's service in the graph: its address, its heartbeat, and the
workflows it registered.

A middleware instance in resource mode registers a service for its resource, with the address it
serves on, starts writing a heartbeat into it, and then registers each of its workflows on it, each
with its class and its endpoint. As it stops, it deregisters: it removes the address and every
endpoint, and keeps the rest. So a service is live while it has an address and a heartbeat younger
than `MAX_HEARTBEAT_AGE_SECONDS`, judged against this reader's clock: a process killed before it
deregistered leaves its service live until the heartbeat is that old. A resource may have several
services, one for each middleware instance bound to it. Of several live ones, a reader takes the
one whose heartbeat is newest: of a killed instance and its restart, the restart.

Discovery, the live-instance guard, the supervisor and its readiness gate read a service only here
(`services`), so what counts as live is written once. The supervisor also reads one service that
is not live here (`reader_of_one`), to say why its process stopped being ready. A read that finds a
service by something other than its resource, as use case 1 finds its detector by a capability,
takes the same rule from `live_pattern`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI
from rdflib.namespace import XSD

from jms_kapps.common.heartbeats import live_heartbeat_age
from jms_kapps.common.triplestore import Queryable, bindings

# How old a heartbeat may be for its service to count as live: the middleware's own staleness
# threshold, 90 s, unless the process was started with `JMS_KAPPS_LIVE_HEARTBEAT_AGE_SECONDS`
# (`common.heartbeats`), as a harness run with its heartbeats off starts each participant. Read
# once, here, so that every reader of this process judges by the same age.
MAX_HEARTBEAT_AGE_SECONDS = live_heartbeat_age(os.environ)


@dataclass(frozen=True)
class Service:
    """One service, as `services` reads it.

    `address` is None once the service deregistered, and `heartbeat` None before its first
    heartbeat; a live service has both. `workflows` holds the endpoint of each workflow the service
    registered, by its class, of the classes the read asked for, and of no other.
    """

    iri: IRI
    resource: IRI
    address: str | None
    heartbeat: datetime | None
    workflows: Mapping[IRI, str] = field(default_factory=dict)


# What reads the live services, each with its workflows of the classes it is given: `services`
# over one store (`reader`), or a test's stand-in.
ServiceReader = Callable[[Collection[IRI]], Iterable[Service]]

# What reads every service of one resource, live or not, each with its workflows of the classes it
# is given: `services` narrowed to the resource with `live_only` False (`reader_of_one`), or a
# test's stand-in. What a supervisor reads to say why a process stopped being ready.
OneServiceReader = Callable[[IRI, Collection[IRI]], Iterable[Service]]


def live_pattern(
    service: str = "service", max_age_seconds: float = MAX_HEARTBEAT_AGE_SECONDS
) -> str:
    """The SPARQL pattern that binds `?address` and `?heartbeat` of `?{service}`, if it is live
    now: it has an address and a heartbeat younger than `max_age_seconds`."""
    cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
    return f"""
        ?{service} <{SVC.hasAddress}> ?address ;
                   <{SVC.hasLastHeartbeat}> ?heartbeat .
        FILTER (?heartbeat >= "{cutoff.isoformat()}"^^<{XSD.dateTime}>)
    """


def services(
    db: Queryable,
    *,
    resource: IRI | None = None,
    workflows: Collection[IRI] = (),
    capability_linked: bool = True,
    live_only: bool = True,
    max_age_seconds: float = MAX_HEARTBEAT_AGE_SECONDS,
) -> list[Service]:
    """The live services, newest heartbeat first, in one read.

    Args:
        resource: Read only the services of this resource; every resource's if None.
        workflows: The classes of the workflows to read with each service. A service that has not
            registered one of them yet, or has deregistered, has no entry for it.
        capability_linked: Whether a workflow counts only once a capability links to it. The
            middleware writes that link last, and a caller that resolves a capability reaches the
            workflow through it, so a readiness gate asks for it. False is for a caller that calls
            the workflow's endpoint directly.
        live_only: False reads every service, live or not, with its address and heartbeat where
            it has them: what a check of a run reads after the run has stopped.
        max_age_seconds: How old a heartbeat may be for its service to count as live.
    """
    if live_only:
        found = live_pattern("service", max_age_seconds)
    else:
        found = f"""
            OPTIONAL {{ ?service <{SVC.hasAddress}> ?address }}
            OPTIONAL {{ ?service <{SVC.hasLastHeartbeat}> ?heartbeat }}
        """
    of_resource = f"VALUES ?resource {{ <{resource}> }}" if resource is not None else ""
    registered = ""
    if workflows:
        link = (
            f"?capability <{SVC.realizedByWorkflow}> ?workflow ."
            if capability_linked
            else ""
        )
        classes = " ".join(f"<{workflow_class}>" for workflow_class in workflows)
        registered = f"""
            OPTIONAL {{
                VALUES ?class {{ {classes} }}
                ?workflow <{SVC.isWorkflowOf}> ?service ;
                          a ?class ;
                          <{SVC.hasEndpoint}> ?endpoint .
                {link}
            }}
        """
    rows = bindings(
        db,
        f"""
        SELECT ?service ?resource ?address ?heartbeat ?class ?endpoint WHERE {{
            {of_resource}
            ?service <{SVC.isServiceOf}> ?resource .
            {found}
            {registered}
        }} ORDER BY DESC(?heartbeat) ?service
        """,
    )
    # One row for each workflow read, and the rows of one service one after the other in the
    # order of its heartbeat, so the first row of a service says where it is in the answer.
    first: dict[IRI, dict] = {}
    endpoints: dict[IRI, dict[IRI, str]] = {}
    for row in rows:
        iri = IRI(str(row["service"]))
        first.setdefault(iri, row)
        found_here = endpoints.setdefault(iri, {})
        if "class" in row and "endpoint" in row:
            found_here.setdefault(IRI(str(row["class"])), str(row["endpoint"]))
    return [
        Service(
            iri=iri,
            resource=IRI(str(row["resource"])),
            address=None if row.get("address") is None else str(row["address"]),
            heartbeat=(
                row["heartbeat"] if isinstance(row.get("heartbeat"), datetime) else None
            ),
            workflows=endpoints[iri],
        )
        for iri, row in first.items()
    ]


def reader(db: Queryable) -> ServiceReader:
    """The live services of `db`, each with its workflows of the classes given: what a supervisor
    and the live-instance guard read."""
    return lambda workflows: services(db, workflows=workflows)


def reader_of_one(db: Queryable) -> OneServiceReader:
    """Every service of one resource in `db`, live or not, each with its workflows of the classes
    given: what a supervisor reads of a process that stopped being ready."""
    return lambda resource, workflows: services(
        db, resource=resource, workflows=workflows, live_only=False
    )


def live_resources(
    db: Queryable, max_age_seconds: float = MAX_HEARTBEAT_AGE_SECONDS
) -> set[IRI]:
    """The resources that have a live service.

    A service that deregistered has no address any more, so it does not count, whatever its last
    heartbeat says.
    """
    return {s.resource for s in services(db, max_age_seconds=max_age_seconds)}
