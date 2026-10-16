"""The ontology smoke test of use case 1: the file declares the terms the decision tree needs.

It parses `uc1/ontology/integration_usecase.ttl` offline and checks the ten properties of the
effective values (who holds each, its range, its unit, and that each is functional), the retired
names, the superclasses, the Screw type `:M4`, the decision snapshot, and the outcome vocabulary. `test_uc1_ontology_live.py` loads
the same file into a live GraphDB and hydrates it through the object-graph mapper.
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.vocabulary import CFC, CORE_NS, SVC
from rdflib import Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, XSD

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PLACEMENT, PROV, TYPE_LEVEL

FORCE = "Unit: N."
TORQUE = "Unit: N·m."
POSITION = "Unit: mm along the y axis of the robot frame."
ANGLE = "Unit: degrees."

TYPE_HOLDERS = {"ScrewType", "Screw", "DecisionSnapshot"}
PLACEMENT_HOLDERS = {"Screw", "DecisionSnapshot"}

# Each property: the individuals that may hold it, and its unit.
PROPERTIES = {
    "hasMinApproachAxialForce": (TYPE_HOLDERS, FORCE),
    "hasMinCounterTorque": (TYPE_HOLDERS, TORQUE),
    "hasMinPreloadAxialForce": (TYPE_HOLDERS, FORCE),
    "hasMaxLooseTorque": (TYPE_HOLDERS, TORQUE),
    "hasMinLooseningAxialForce": (TYPE_HOLDERS, FORCE),
    "hasMaxDynamicTorque": (TYPE_HOLDERS, TORQUE),
    "hasPreparationTurningAngle": (TYPE_HOLDERS, ANGLE),
    "hasMinApproachPosition": (PLACEMENT_HOLDERS, POSITION),
    "hasMinScrewHeadPosition": (PLACEMENT_HOLDERS, POSITION),
    "hasMaxScrewHeadPosition": (PLACEMENT_HOLDERS, POSITION),
}

RETIRED = [
    "hasLowerTighteningTorque",
    "hasUpperDynamicLoseningTorque",
    "hasLowerAxialForce",
    "hasAxialForceApproach",
    "hasPositionOnApproach",
    "hasSuccessStatus",
    "M4_Screw",
    "screw",
    "unscrewingOperation",
    "TimeSeriesData",
    "hasJSONEncodedTimeSeriesData",
    "hasUnscrewingTorqueTimeSeriesData",
    "hasAxialForceTimeSeriesData",
    "hasRobotPositionTimeSeriesData",
]

# Each unscrewing outcome, the Screw conditions it indicates, and the category each requires.
OUTCOMES = {
    "ScrewIntact": {"Intact"},
    "MissingScrew": {"MissingScrewMode"},
    "MissingHead": {"MissingHeadMode"},
    "HeadOccluded": {"HeadOccludedMode"},
    "RoundedHeadOrWrongType": {"RoundedHeadMode", "WrongTypeMode"},
    "StuckAtBreakaway": {"StuckScrewMode"},
    "LooseAnchor": {"LooseAnchorMode"},
    "ThreadFault": {"StuckScrewMode"},
}
CATEGORY_OF_CONDITION = {
    "Intact": "ProceedNormally",
    "MissingScrewMode": "ProceedNormally",
    "MissingHeadMode": "AdditionalTools",
    "HeadOccludedMode": "AdditionalTools",
    "RoundedHeadMode": "AdditionalTools",
    "WrongTypeMode": "AdditionalTools",
    "StuckScrewMode": "Destructive",
    "LooseAnchorMode": "Destructive",
}


def term(local_name: str) -> URIRef:
    return URIRef(JMS_NS + local_name)


def local(node: object) -> str:
    return str(node).removeprefix(JMS_NS)


def core(name: str) -> URIRef:
    """A Core term, built from the namespace the middleware exports. `CFC` names only the terms
    its own code uses, and Core's product axis is not among them, so no Core IRI is spelled here.
    """
    return URIRef(CORE_NS + name)


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph().parse(
        data=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        format="turtle",
    )


def union_domain(graph: Graph, prop: URIRef) -> set[str]:
    """The classes of the one `rdfs:domain` of `prop`, which is an `owl:unionOf`."""
    domains = list(graph.objects(prop, RDFS.domain))
    assert len(domains) == 1, f"{local(prop)} has {len(domains)} domains"
    members = graph.value(domains[0], OWL.unionOf)
    assert members is not None, f"the domain of {local(prop)} is not a union"
    return {local(member) for member in Collection(graph, members)}


def objects_of(graph: Graph, subject: str, prop: str) -> set[str]:
    return {local(o) for o in graph.objects(term(subject), term(prop))}


@pytest.mark.parametrize("name", PROPERTIES)
def test_each_property_has_its_holders_its_range_and_its_unit(
    graph: Graph, name: str
) -> None:
    holders, unit = PROPERTIES[name]
    prop = term(name)

    assert (prop, RDF.type, OWL.DatatypeProperty) in graph
    assert union_domain(graph, prop) == holders
    assert set(graph.objects(prop, RDFS.range)) == {XSD.double}
    comments = [str(c) for c in graph.objects(prop, RDFS.comment)]
    assert len(comments) == 1
    assert comments[0].endswith(unit), comments[0]


def test_exactly_the_ten_and_two_prov_terms_are_functional(graph: Graph) -> None:
    """The two PROV terms: a snapshot comes from one classification, which ends once
    (jms_kapps 59)."""
    functional = {local(p) for p in graph.subjects(RDF.type, OWL.FunctionalProperty)}

    assert functional == {*PROPERTIES, str(PROV.wasGeneratedBy), str(PROV.endedAtTime)}


def test_the_double_valued_properties_are_the_ten_and_the_failure_position(
    graph: Graph,
) -> None:
    doubles = {local(p) for p in graph.subjects(RDFS.range, XSD.double)}

    assert doubles == {*PROPERTIES, "hasMeasuredApproachEndPosition"}


def test_a_failed_operation_records_its_measured_approach_end_position(
    graph: Graph,
) -> None:
    prop = term("hasMeasuredApproachEndPosition")

    assert (prop, RDF.type, OWL.DatatypeProperty) in graph
    assert set(graph.objects(prop, RDFS.domain)) == {term("UnscrewingOperation")}
    assert set(graph.objects(prop, RDFS.range)) == {XSD.double}
    (comment,) = [str(c) for c in graph.objects(prop, RDFS.comment)]
    assert "only when the outcome is a failure" in comment
    assert "failure information" in comment
    assert comment.endswith(POSITION)


def test_the_retired_names_are_gone(graph: Graph) -> None:
    mentioned = {local(node) for triple in graph for node in triple}

    assert mentioned.isdisjoint(RETIRED)


def test_each_class_sits_directly_under_its_core_class(graph: Graph) -> None:
    """Directly, as use case 2's BoxTransportOperation is: the object-graph mapper finds a property
    of a class only through its domain, so only a direct triple lets an unscrewing operation name
    the Capability it implements. A Screw is a `cfc:Workpiece`, the common superclass of
    `cfc:Assembly` and `cfc:Component`, which says what Figure 7 of the paper draws and leaves the
    choice between the two to Milestone 2. A Screw type has no superclass."""
    superclasses = {
        cls: set(graph.objects(term(cls), RDFS.subClassOf))
        for cls in ("UnscrewingOperation", "Screw", "ScrewType", "AngleGrinder")
    }

    assert superclasses == {
        "UnscrewingOperation": {URIRef(str(CFC.Operation))},
        "Screw": {core("Workpiece")},
        "ScrewType": set(),
        "AngleGrinder": {core("Assembly"), core("Product")},
    }
    # The artifact's undeclared `:Product` is gone, and nothing names it any more.
    assert (term("Product"), None, None) not in graph
    assert (None, None, term("Product")) not in graph


def test_the_planner_and_its_product_are_core_classes_by_one_triple(
    graph: Graph,
) -> None:
    """Each by one direct `rdfs:subClassOf` triple, because the middleware checks that one triple
    when it registers the service and does not follow a chain. The product is a `cfc:Assembly`, so
    it names its Screws with Core's `cfc:containsWorkpiece` and this file declares no link of its
    own."""
    superclasses = {
        cls: set(graph.objects(term(cls), RDFS.subClassOf))
        for cls in (
            "AngleGrinder",
            "Planner",
            "PlannerService",
            "StartUnscrewingWorkflow",
            "StartUnscrewingCapability",
        )
    }

    # The middleware's vocabulary names only the Core terms it uses itself, and `cfc:Assembly` is
    # not one of them. Its namespace comes from the middleware all the same, so this file spells
    # no Core IRI of its own and the two cannot drift apart.
    assert superclasses == {
        "AngleGrinder": {core("Assembly"), core("Product")},
        "Planner": {URIRef(str(CFC.Resource))},
        "PlannerService": {URIRef(str(SVC.Service))},
        "StartUnscrewingWorkflow": {URIRef(str(SVC.Workflow))},
        "StartUnscrewingCapability": {URIRef(str(CFC.Capability))},
    }
    assert (term("Planner1"), RDF.type, term("Planner")) in graph


def test_the_product_is_both_an_assembly_and_a_product(graph: Graph) -> None:
    """It needs both, and Core keeps them on separate branches. `cfc:Assembly` carries
    `cfc:containsWorkpiece`, which names the grinder's Screws; `cfc:Product` carries
    `cfc:hasProductType`, which names its model. Neither follows from the other."""
    assert (term("AngleGrinder"), RDF.type, OWL.Class) in graph
    assert (term("AngleGrinder"), RDFS.subClassOf, core("Assembly")) in graph
    assert (term("AngleGrinder"), RDFS.subClassOf, core("Product")) in graph


def test_the_artifacts_product_class_is_gone(graph: Graph) -> None:
    """`:Screw rdfs:subClassOf :Product` named a class that nothing declared, here and in the
    published artifact alike. `cfc:Product` is Core's and is a different term."""
    mine = term("Product")

    assert [triple for triple in graph if mine in triple] == []


def test_the_design_names_the_screw_type_on_the_product_type(graph: Graph) -> None:
    """The design: a grinder of this model is built with this Screw type. It is what the detector
    compares against when a recovered Screw carries no record of its own."""
    assert (term("hasExpectedScrewType"), RDF.type, OWL.ObjectProperty) in graph
    assert set(graph.objects(term("hasExpectedScrewType"), RDFS.domain)) == {
        core("ProductType")
    }
    assert set(graph.objects(term("hasExpectedScrewType"), RDFS.range)) == {
        term("ScrewType")
    }


# What a product type that carries the design property would be entailed to be. The claim of
# `:hasExpectedScrewType` is that `:Screw` is not among them.
PRODUCT_TYPE = term("ProbeProductType")
SCREW_TYPE = term("M4")

# The ways the file could be written that would break the claim. Each is applied to the ontology on
# its own, and the test below must then see the entailment it denies.
BREAKING = {
    "a sub-property of the record property": (
        term("hasExpectedScrewType"),
        RDFS.subPropertyOf,
        term("hasScrewType"),
    ),
    "equivalent to the record property": (
        term("hasExpectedScrewType"),
        OWL.equivalentProperty,
        term("hasScrewType"),
    ),
    "a product type declared to be a Screw": (
        URIRef(CORE_NS + "ProductType"),
        RDFS.subClassOf,
        term("Screw"),
    ),
}


def entailed(graph: Graph, extra: tuple | None = None) -> Graph:
    """The ontology, the design statement and the entailments that could make its subject a Screw.

    The rules are RDFS 2, 3, 5, 7, 9 and 11, and OWL's equivalent and inverse properties: enough
    for this claim, and not a reasoner. `extra` is one triple that breaks the model on purpose.
    """
    closed = Graph()
    for triple in graph:
        closed.add(triple)
    for triple in Graph().parse(
        data=read_asset("kapps_semantic_middleware", "ontology/core.ttl"),
        format="turtle",
    ):
        closed.add(triple)
    if extra is not None:
        closed.add(extra)
    closed.add((PRODUCT_TYPE, term("hasExpectedScrewType"), SCREW_TYPE))

    size = -1
    while len(closed) != size:
        size = len(closed)
        for relation in (RDFS.subPropertyOf, RDFS.subClassOf):  # rdfs5, rdfs11
            for a, _, b in list(closed.triples((None, relation, None))):
                for _, _, c in list(closed.triples((b, relation, None))):
                    closed.add((a, relation, c))
        for a, _, b in list(closed.triples((None, OWL.equivalentProperty, None))):
            closed.add((a, RDFS.subPropertyOf, b))
            closed.add((b, RDFS.subPropertyOf, a))
        for prop, _, parent in list(closed.triples((None, RDFS.subPropertyOf, None))):
            for s, _, o in list(closed.triples((None, prop, None))):  # rdfs7
                closed.add((s, parent, o))
        for prop, _, inverse in list(closed.triples((None, OWL.inverseOf, None))):
            for s, _, o in list(closed.triples((None, prop, None))):
                closed.add((o, inverse, s))
        for prop, _, cls in list(closed.triples((None, RDFS.domain, None))):  # rdfs2
            for s, _, _o in list(closed.triples((None, prop, None))):
                closed.add((s, RDF.type, cls))
        for prop, _, cls in list(closed.triples((None, RDFS.range, None))):  # rdfs3
            for _s, _, o in list(closed.triples((None, prop, None))):
                closed.add((o, RDF.type, cls))
        for cls, _, parent in list(
            closed.triples((None, RDFS.subClassOf, None))
        ):  # rdfs9
            for s, _, _o in list(closed.triples((None, RDF.type, cls))):
                closed.add((s, RDF.type, parent))
    return closed


def test_the_design_property_does_not_make_a_product_type_a_screw(graph: Graph) -> None:
    """Why `:hasExpectedScrewType` exists instead of a second use of `:hasScrewType`. RDFS reads
    `rdfs:domain` as an inference rule: from `p rdfs:domain C` and `x p y` follows `x rdf:type C`.
    `:hasScrewType` has the domain `:Screw`, so carrying it would make a product type a Screw.

    This asserts the design statement about a product type, closes the graph under the rules that
    could produce that type, and checks it is absent. The counterfactual is the proof that the
    closure can produce it at all.
    """
    closed = entailed(graph)
    assert (PRODUCT_TYPE, RDF.type, URIRef(CORE_NS + "ProductType")) in closed, (
        "the closure does not even derive the type the design property's domain gives"
    )

    assert (PRODUCT_TYPE, RDF.type, term("Screw")) not in closed

    counterfactual = entailed(graph, (PRODUCT_TYPE, term("hasScrewType"), SCREW_TYPE))
    assert (PRODUCT_TYPE, RDF.type, term("Screw")) in counterfactual, (
        "the record property would not make it a Screw either, so this proves nothing"
    )


@pytest.mark.parametrize("why", sorted(BREAKING))
def test_the_claim_is_checked_and_not_assumed(graph: Graph, why: str) -> None:
    """The test above must fail on a file that breaks the claim. Each of these three ways of
    writing the ontology does make a product type a Screw, and each one must be caught."""
    closed = entailed(graph, BREAKING[why])

    assert (PRODUCT_TYPE, RDF.type, term("Screw")) in closed, (
        f"{why}: the model is broken and the check does not see it"
    )


def test_an_operation_names_its_workpiece_and_a_screw_names_its_operation(
    graph: Graph,
) -> None:
    """Figure 7 of the paper draws both. `:hasWorkpiece` is the step from the operation to the
    product, from which `cfc:hasProductType` reaches the model and `:hasExpectedScrewType` the
    Screw type the design calls for. `:hasOperation` is the back-link that gives §5.1 the
    bidirectional traceability it claims for R4. `:hasOperation` is the `owl:inverseOf`
    `:hasScrew`, so a repository on rdfsplus-optimized infers the back-link in the operation's own
    commit; the planner also writes it explicitly (Etienne, 2026-10-15, MR jms_kapps!113)."""
    assert (term("hasWorkpiece"), RDF.type, OWL.ObjectProperty) in graph
    assert set(graph.objects(term("hasWorkpiece"), RDFS.domain)) == {
        term("UnscrewingOperation")
    }
    assert set(graph.objects(term("hasWorkpiece"), RDFS.range)) == {core("Workpiece")}

    assert (term("hasOperation"), RDF.type, OWL.ObjectProperty) in graph
    assert set(graph.objects(term("hasOperation"), RDFS.domain)) == {term("Screw")}
    assert set(graph.objects(term("hasOperation"), RDFS.range)) == {
        term("UnscrewingOperation")
    }

    assert (term("hasOperation"), OWL.inverseOf, term("hasScrew")) in graph


def test_a_screw_names_its_screw_type(graph: Graph) -> None:
    for cls in ("Screw", "ScrewType", "DecisionSnapshot", "UnscrewingOperation"):
        assert (term(cls), RDF.type, OWL.Class) in graph
    assert set(graph.objects(term("hasScrewType"), RDFS.domain)) == {term("Screw")}
    assert set(graph.objects(term("hasScrewType"), RDFS.range)) == {term("ScrewType")}
    assert set(graph.objects(term("hasScrew"), RDFS.range)) == {term("Screw")}


def test_m4_is_a_screw_type_with_no_values(graph: Graph) -> None:
    assert (term("M4"), RDF.type, term("ScrewType")) in graph
    held = {local(p) for p in graph.predicates(term("M4"))}

    assert held.isdisjoint(PROPERTIES)


def test_an_operation_links_to_its_decision_snapshot(graph: Graph) -> None:
    assert (term("usedValues"), RDF.type, OWL.ObjectProperty) in graph
    assert set(graph.objects(term("usedValues"), RDFS.domain)) == {
        term("UnscrewingOperation")
    }
    assert set(graph.objects(term("usedValues"), RDFS.range)) == {
        term("DecisionSnapshot")
    }


def test_an_operation_holds_one_reference_to_its_curves(graph: Graph) -> None:
    prop = term("hasTimeSeriesReference")

    assert (prop, RDF.type, OWL.DatatypeProperty) in graph
    assert set(graph.objects(prop, RDFS.domain)) == {term("UnscrewingOperation")}
    assert set(graph.objects(prop, RDFS.range)) == {XSD.string}
    (comment,) = [str(c) for c in graph.objects(prop, RDFS.comment)]
    assert "influxdb:///<database>/<table>?frame=<id>" in comment


def test_the_outcome_vocabulary(graph: Graph) -> None:
    for prop, domain, range_ in (
        ("hasOutcome", "UnscrewingOperation", "UnscrewingOutcome"),
        ("indicates", "UnscrewingOutcome", "ScrewCondition"),
        ("requiresDisassembly", "ScrewCondition", "DisassemblyCategory"),
    ):
        assert (term(prop), RDF.type, OWL.ObjectProperty) in graph
        assert set(graph.objects(term(prop), RDFS.domain)) == {term(domain)}
        assert set(graph.objects(term(prop), RDFS.range)) == {term(range_)}

    outcomes = {local(s) for s in graph.subjects(RDF.type, term("UnscrewingOutcome"))}
    assert outcomes == set(OUTCOMES)
    for outcome, conditions in OUTCOMES.items():
        assert objects_of(graph, outcome, "indicates") == conditions

    conditions = {local(s) for s in graph.subjects(RDF.type, term("ScrewCondition"))}
    assert conditions == set(CATEGORY_OF_CONDITION)
    for condition, category in CATEGORY_OF_CONDITION.items():
        assert objects_of(graph, condition, "requiresDisassembly") == {category}

    categories = {
        local(s) for s in graph.subjects(RDF.type, term("DisassemblyCategory"))
    }
    assert categories == {"ProceedNormally", "AdditionalTools", "Destructive"}


def test_the_vocabulary_module_matches_the_file(graph: Graph) -> None:
    declared = {local(s) for s in graph.subjects(RDF.type, None)}
    in_module = {
        local(value) for name, value in vars(JMS).items() if not name.startswith("_")
    }

    assert in_module <= declared
    assert {local(p) for p in TYPE_LEVEL} == {
        name for name, (holders, _) in PROPERTIES.items() if holders == TYPE_HOLDERS
    }
    assert {local(p) for p in PLACEMENT} == {
        name
        for name, (holders, _) in PROPERTIES.items()
        if holders == PLACEMENT_HOLDERS
    }
