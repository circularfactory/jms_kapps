"""What the planner reads from the graph: the Screws it offers, and an operation's outcome.

They are here, apart from the planner's own logic, so that a press is driven by what it is given
and reads nothing itself. The planner's process gives them to it; a test gives it fakes.

**The planner finds its grinders in the graph with one SPARQL query**, because the object-graph
mapper cannot enumerate a class: `OGM.fetch` takes one `instance_iri`. Everything else it reads
stays an object-graph projection: reads go through one projection, not hand-written SPARQL.
Neither writes anything.

**The planner reads each grinder's Screws with the OGM projections**, and is not told which
grinders it works on: it finds them itself. Every Screw the grinder contains is
offered — until it has an operation. The projection types each one by the range of
`cfc:containsWorkpiece`, which is `cfc:Workpiece`, and not by its own class, so a Screw and any
other contained workpiece cannot be told apart here. UC1's grinders hold one Screw and nothing
else (one Screw per grinder), so the list is exactly its Screws. A grinder with
mixed contents would need the instance's own class, which the mapper does not give through a
projection.

**A Screw that holds `:hasOperation` is no longer offered.** The planner writes that back-link at
each press, in the operation's own commit (see `planner.dispatch`), whatever the operation's
status, so it is the one fact that says "this Screw is taken". The offer therefore leaves it out,
and a press on it is refused. This is read per Screw, not in one chain from the grinder: the mapper types a nested
object by the property's range, `cfc:Workpiece`, which has no `:hasOperation` — measured, see
below. So the offer needs one fetch per Screw in addition to the one per grinder.

**One fetch per Screw is what the mapper can do.** A single chain `[[cfc:containsWorkpiece,
uns:hasOperation]]` would have the mapper type the nested object by the *range* of
`cfc:containsWorkpiece`, which is `cfc:Workpiece`, and that class has no `:hasOperation` — the
mapper then returns nothing for the property. This was measured in development, which is why the
offer does one projection from each Screw instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kapps_ogm import OGM, ClassScope
from kapps_semantic_middleware.vocabulary import CORE_NS, SVC
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.vocabulary import JMS

logger = logging.getLogger(__name__)

# The one query that lists the grinders the planner works on. The mapper cannot enumerate a
# class, so this is SPARQL — the one SPARQL read of the planner.
GRINDERS_QUERY = (
    f"SELECT ?grinder WHERE {{ ?grinder a <{JMS.AngleGrinder}> }} ORDER BY ?grinder"
)

# Core's composition property: an Assembly contains its Workpieces. The middleware's vocabulary
# names only the Core terms its own code uses, and this is not one of them, so it is built from the
# namespace the middleware exports rather than spelled out here.
CONTAINS_WORKPIECE = IRI("containsWorkpiece", base=CORE_NS)

# The one chain each read needs.
CONTENTS_SCOPE = ClassScope.from_property_chains([[CONTAINS_WORKPIECE]])
OUTCOME_SCOPE = ClassScope.from_property_chains([[JMS.hasDetectionResult]])
FAILURE_SCOPE = ClassScope.from_property_chains([[SVC.hasExecutionResult]])
OPERATION_LINK_SCOPE = ClassScope.from_property_chains([[JMS.hasOperation]])


@dataclass(frozen=True)
class ScrewOnProduct:
    """One Screw the planner offers, and the product it sits in.

    The product is what the operation names as its `:concernsWorkpiece`, and the step from which the
    detector reaches the Screw type the design expects.
    """

    screw: IRI
    product: IRI


def read_grinders(db: Queryable) -> list[IRI]:
    """The grinders in the graph, ordered by their IRI.

    The planner needs no `--product`: it reads what it works on from the graph itself. This read
    skips the object-graph mapper because `OGM.fetch` takes one `instance_iri` and cannot enumerate
    a class. An empty graph gives an empty list; it does not raise.
    """
    logger.info(
        "Planner reads the grinders with one SPARQL query, because the mapper cannot list a "
        "class: %s",
        GRINDERS_QUERY,
    )
    return [
        IRI(str(binding["grinder"]))
        for binding in bindings(db, GRINDERS_QUERY)
        if binding.get("grinder") is not None
    ]


def read_screws(ogm: OGM, product: IRI) -> list[ScrewOnProduct]:
    """The Screws that `product` contains, with that product, ordered by the Screw's IRI.

    The mapper gives them in no order of its own, so they are sorted: the select offers the same
    Screws in the same order on every start, which is what makes a demo reproducible.

    A product the graph does not hold raises `OGMNotFoundError`, which is what should happen at a
    start: a planner told to work on a product that is not there must not come up offering nothing.
    """
    log_scope(
        logger,
        "Planner",
        "reads",
        product,
        "CONTENTS_SCOPE",
        CONTENTS_SCOPE,
        "it wants the workpieces that the product contains (cfc:containsWorkpiece)",
    )
    node = ogm.fetch(instance_iri=product, class_scope=CONTENTS_SCOPE)
    contained = (node.data or {}).get(CONTAINS_WORKPIECE) or []
    screws = sorted(
        {IRI(str(getattr(value, "id", value))) for value in contained}, key=str
    )
    return [ScrewOnProduct(screw=screw, product=product) for screw in screws]


def read_has_operation(ogm: OGM, screw: IRI) -> bool:
    """Whether `screw` holds at least one `:hasOperation`.

    The planner writes the back-link at each press, whatever the operation's status, so this is
    the one fact that says a Screw is taken. One fetch, with `OPERATION_LINK_SCOPE`: the mapper
    types a nested object by the property's range, and one chain from the product would fail,
    which is why the offer reads per Screw rather than per product.
    """
    log_scope(
        logger,
        "Planner",
        "reads",
        screw,
        "OPERATION_LINK_SCOPE",
        OPERATION_LINK_SCOPE,
        "it wants to know whether an operation was opened for this Screw (hasOperation)",
    )
    node = ogm.fetch(instance_iri=screw, class_scope=OPERATION_LINK_SCOPE)
    return bool((node.data or {}).get(JMS.hasOperation))


def read_offer(ogm: OGM) -> list[ScrewOnProduct]:
    """The Screws of the grinders, in order, that have no operation yet.

    The grinders are found with the one SPARQL query (`read_grinders`), then each one's Screws
    are read with the OGM projections. A Screw that holds `:hasOperation` is left out, because a
    press would be refused on it anyway: the offer is what a press may take. A grinder the graph
    does not hold raises, which is what should happen at a start. An empty graph gives an empty
    offer; it does not raise.
    """
    offer = []
    for product in read_grinders(ogm.db):
        for entry in read_screws(ogm, product):
            if not read_has_operation(ogm, entry.screw):
                offer.append(entry)
    return offer


def read_outcome(ogm: OGM, operation: IRI) -> str | None:
    """The outcome the detector wrote on `operation`, by its local name, or None when it holds
    none.

    The local name is what the planner's dashboard shows, for example `ScrewIntact`. An operation
    that ended `done` holds exactly one, and so does one that ended `failed` with a failure outcome,
    such as `MissingScrew`; one that failed for another reason holds none. A second would be a fault
    of the detector, and the first the mapper returns is taken rather than refusing, because the
    planner only displays it.
    """
    log_scope(
        logger,
        "Planner",
        "reads",
        operation,
        "OUTCOME_SCOPE",
        OUTCOME_SCOPE,
        "it wants the outcome that the detector committed (hasDetectionResult)",
    )
    node = ogm.fetch(instance_iri=operation, class_scope=OUTCOME_SCOPE)
    values = (node.data or {}).get(JMS.hasDetectionResult) or []
    if not values:
        return None
    first = values[0]
    return str(getattr(first, "id", first)).rsplit("#", 1)[-1]


def read_failure_reason(ogm: OGM, operation: IRI) -> str | None:
    """Why `operation` ended `failed`: its `svc:hasExecutionResult`, or None when it holds none.

    The screwing resource and the detector each write the reason there when they end an operation
    `failed`. Several reasons are joined with "; ", because the planner only displays them.
    """
    log_scope(
        logger,
        "Planner",
        "reads",
        operation,
        "FAILURE_SCOPE",
        FAILURE_SCOPE,
        "it wants the reason of the failure (hasExecutionResult)",
    )
    node = ogm.fetch(instance_iri=operation, class_scope=FAILURE_SCOPE)
    values = (node.data or {}).get(SVC.hasExecutionResult) or []
    return "; ".join(str(value) for value in values) or None
