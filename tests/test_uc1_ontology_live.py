"""Use case 1's ontology on a live GraphDB, through the object-graph mapper and the middleware.

The ten properties of a Screw's effective values have a union as their domain, and each is
functional. The mapper finds such a property by a SPARQL query that uses the `owl:` and `rdf:`
prefixes without declaring them, so they must resolve in the dataset. These tests load the ontology with Core, seed mock
values (see `_uc1_mock.py`: none of them is measured), and check that:

- a Screw type, a Screw and a decision snapshot each load through a class scope with exactly
  their properties, each optional and single-valued;
- a Screw type and a Screw hydrate one value of each threshold they hold, also as a model;
- one value of each threshold is written and changed through the middleware, as the learner
  writes it, and a decision snapshot is written, as the detector writes it;
- a second value is refused before the write, and the error names the maximum;
- an unscrewing operation that the middleware creates under its operation scope keeps the
  Capability it implements, because the class is a `cfc:Operation`.

Each threshold is a single-valued property, so the model holds it as a list of at most one:
`[value]`, or `[]` without a value. The object-graph mapper gives every property a list, a
single-valued one too (the ticket "A single-valued property cannot hold its one value (34)" in
kapps_ogm).

No test loads a use case 1 class at the mapper's FULL level. Under `cfc:Operation` that load
recurses without end (the ticket "FULL hydration recurses on a cyclic range (35)" in kapps_ogm),
so every read goes through a class scope, as use case 2's do.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from _uc1_mock import ARBITRARY_OVERRIDE, ARBITRARY_PLACEMENT_VALUES, MOCK_M4_VALUES
from kapps_ogm import OGM, ClassScope, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.operation import operation as operation_scope
from kapps_semantic_middleware.vocabulary import CFC, CORE_NS
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import bindings, connect, read_asset, reset
from jms_kapps.uc1.effective_values import (
    UnresolvedValues,
    as_properties,
    read_effective_values,
)
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PLACEMENT, TYPE_LEVEL

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# The mapper's refusal of a second value of a single-valued property. It names the maximum.
OVER_THE_MAXIMUM = "at most 1 item"

# A Screw with no values of its own, and one with its placement values and one override.
BARE_SCREW = IRI("TestScrewWithoutValues", base=JMS_NS)
PLACED_SCREW = IRI("TestScrewWithPlacement", base=JMS_NS)
OPERATION = IRI("TestOperation", base=JMS_NS)
SNAPSHOT = IRI("TestDecisionSnapshot", base=JMS_NS)

# The effective values of the placed Screw: `:M4`'s, its placement values, and its one override.
PLACED_SCREW_VALUES = {
    **MOCK_M4_VALUES,
    **ARBITRARY_PLACEMENT_VALUES,
    **ARBITRARY_OVERRIDE,
}

# Who writes through the middleware here: a planner, built and never served, because a commit
# needs only its object-graph mapper. Neither IRI is declared; use case 1's participants are not
# built yet.
# The product the test operation works on, its product type, and the two Core links that join
# them. `CFC` names only the terms the middleware's own code uses, and Core's product axis is not
# among them, so these are built from the namespace the middleware exports.
GRINDER = IRI("TestAngleGrinder", base=JMS_NS)
PRODUCT_TYPE = IRI("TestAG1", base=JMS_NS)
CONTAINS_WORKPIECE = IRI("containsWorkpiece", base=CORE_NS)
HAS_PRODUCT_TYPE = IRI("hasProductType", base=CORE_NS)
CORE_PRODUCT_TYPE = IRI("ProductType", base=CORE_NS)

PLANNER = IRI("TestPlanner", base=JMS_NS)
PLANNER_SERVICE = IRI("TestPlannerService", base=JMS_NS)
# The Capability an unscrewing operation implements: the screwing resource's, which the
# middleware writes when that resource registers its workflow. None is registered here.
UNSCREWING_CAPABILITY = IRI("TestUnscrewingCapability", base=JMS_NS)

TEN = (*TYPE_LEVEL, *PLACEMENT)


def scope_of(properties: Iterable[IRI]) -> ClassScope:
    """The class scope that names each of `properties`, one level deep."""
    return ClassScope.from_property_chains([[prop] for prop in properties])


def turtle_values(subject: IRI, values: dict[IRI, float]) -> str:
    return "".join(
        f'<{subject}> <{prop}> "{value}"^^<http://www.w3.org/2001/XMLSchema#double> .\n'
        for prop, value in values.items()
    )


@pytest.fixture(scope="module")
def db(dataset):
    """The ontology, the mock values on `:M4` and on two test Screws, one value each, and a test
    operation with a decision snapshot of the placed Screw's values.

    The seed writes Turtle, not through the mapper, as a demonstration seed may. The seeded
    snapshot is what the read tests read; the detector's own write is tested below. Use case 1
    has no shapes yet, so the shapes graph is left empty.
    """
    db = connect(dataset)
    reset(
        db,
        ontology=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        shapes="",
    )
    db.graph_add(
        f"<{BARE_SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        f"<{PLACED_SCREW}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        + turtle_values(JMS.M4, MOCK_M4_VALUES)
        + turtle_values(
            PLACED_SCREW, {**ARBITRARY_PLACEMENT_VALUES, **ARBITRARY_OVERRIDE}
        )
        + f"<{SNAPSHOT}> a <{JMS.DecisionSnapshot}> .\n"
        + turtle_values(SNAPSHOT, PLACED_SCREW_VALUES)
        + f"<{OPERATION}> a <{JMS.UnscrewingOperation}> ; <{JMS.usedValues}> <{SNAPSHOT}> ;\n"
        f"    <{JMS.hasScrew}> <{PLACED_SCREW}> ; <{JMS.hasWorkpiece}> <{GRINDER}> .\n"
        f"<{PLACED_SCREW}> <{JMS.hasOperation}> <{OPERATION}> .\n"
        f"<{GRINDER}> a <{JMS.AngleGrinder}> ; <{CONTAINS_WORKPIECE}> <{PLACED_SCREW}> ;\n"
        f"    <{HAS_PRODUCT_TYPE}> <{PRODUCT_TYPE}> .\n"
        f"<{PRODUCT_TYPE}> a <{CORE_PRODUCT_TYPE}> ;"
        f" <{JMS.hasExpectedScrewType}> <{JMS.M4}> .\n",
        content_type="application/x-turtle",
    )
    return db


@pytest.fixture
def ogm(db) -> OGM:
    return OGM(db=db)


@pytest.fixture
def mw(db) -> SemanticMiddleware:
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(PLANNER),
        service_class=str(PLANNER_SERVICE),
        ogm=OGM(db=db),
    )


def held_by(ogm: OGM, instance: IRI, properties: tuple[IRI, ...]) -> dict[IRI, list]:
    """What `instance` holds of `properties`, read through the mapper under a class scope.

    The read is not materialized: the node's data holds every value as a list, as the mapper
    reads it from the store, and a linked individual as its IRI.
    """
    data = ogm.fetch(instance_iri=instance, class_scope=scope_of(properties)).data or {}
    return {
        prop: [getattr(value, "id", value) for value in data.get(prop, [])]
        for prop in properties
    }


def materialized(
    ogm: OGM, instance: IRI, properties: tuple[IRI, ...]
) -> dict[IRI, list]:
    """What `instance` holds of `properties`, as the model the mapper materializes."""
    model = ogm.fetch(
        instance_iri=instance, class_scope=scope_of(properties), materialize=True
    ).instance
    return {prop: getattr(model, prop.lined) for prop in properties}


def stored(db, subject: IRI) -> list[dict]:
    return bindings(db, f"SELECT ?p ?o WHERE {{ <{subject}> ?p ?o }}")


# --- The three holders load through a class scope ---------------------------------------------


HOLDERS = [
    (JMS.ScrewType, set(TYPE_LEVEL)),
    (JMS.Screw, {*TEN, JMS.hasScrewType}),
    (JMS.DecisionSnapshot, set(TEN)),
]


@pytest.mark.parametrize(("cls", "expected"), HOLDERS)
def test_each_holder_loads_through_a_scope_with_its_properties_each_single_valued(
    ogm: OGM, cls: IRI, expected: set[IRI]
) -> None:
    spec = ogm.get_class_spec(class_iri=cls, class_scope=scope_of(expected))

    assert set(spec.properties) == expected
    assert not any(prop.required for prop in spec.properties.values())
    assert {
        iri for iri, prop in spec.properties.items() if prop.max_count == 1
    } == expected & set(TEN)


@pytest.mark.parametrize(("cls", "expected"), HOLDERS)
def test_a_scope_with_a_property_another_holder_holds_is_refused(
    ogm: OGM, cls: IRI, expected: set[IRI]
) -> None:
    """A Screw type holds no placement value, and a decision snapshot names no Screw type."""
    for other in {*TEN, JMS.hasScrewType} - expected:
        with pytest.raises(ValueError, match="not found as property"):
            ogm.get_class_spec(class_iri=cls, class_scope=scope_of({*expected, other}))


# --- A Screw type and a Screw hold one value of each threshold --------------------------------


def test_a_screw_type_and_a_screw_hydrate_one_value_of_each_threshold(ogm: OGM) -> None:
    assert held_by(ogm, JMS.M4, TYPE_LEVEL) == {
        prop: [value] for prop, value in MOCK_M4_VALUES.items()
    }
    own = {**ARBITRARY_PLACEMENT_VALUES, **ARBITRARY_OVERRIDE}
    assert held_by(ogm, PLACED_SCREW, TEN) == {
        prop: [own[prop]] if prop in own else [] for prop in TEN
    }


def test_a_screw_without_values_hydrates_with_every_value_empty(ogm: OGM) -> None:
    held = held_by(ogm, BARE_SCREW, (*TEN, JMS.hasScrewType))

    assert held.pop(JMS.hasScrewType) == [JMS.M4]
    assert all(not values for values in held.values())


def test_a_screw_type_and_a_screw_materialize_one_value_of_each_threshold(
    ogm: OGM,
) -> None:
    """The model holds one value of each threshold that the Screw type and the Screw hold: the
    Screw's placement values and its override. Each is a list of one."""
    own = {**ARBITRARY_PLACEMENT_VALUES, **ARBITRARY_OVERRIDE}
    on_type = materialized(ogm, JMS.M4, TYPE_LEVEL)
    on_screw = materialized(ogm, PLACED_SCREW, tuple(own))

    assert on_type == {prop: [value] for prop, value in MOCK_M4_VALUES.items()}
    assert on_screw == {prop: [value] for prop, value in own.items()}


# --- A second value is refused before the write -----------------------------------------------


def test_a_new_screw_type_with_a_second_value_is_refused_before_the_write(
    mw: SemanticMiddleware, db
) -> None:
    """The refusal names the maximum, and nothing reaches the store."""
    screw_type = IRI("TestScrewTypeWithTwoValues", base=JMS_NS)

    with (
        pytest.raises(ValueError, match=OVER_THE_MAXIMUM),
        mw.create(
            str(JMS.ScrewType), ClassScope(), instance_iri=str(screw_type)
        ) as created,
    ):
        created.update({str(JMS.hasMaxDynamicTorque): [1.5, 1.6]})

    assert stored(db, screw_type) == []


def test_a_second_value_on_a_screw_type_is_refused_before_the_write(
    mw: SemanticMiddleware, ogm: OGM
) -> None:
    """The learner's write: a commit of a Screw type's threshold. Two values are refused, the
    refusal names the maximum, and the one value it held stays."""
    two_values = {str(JMS.hasMaxDynamicTorque): [1.4, 1.6]}
    with (
        pytest.raises(ValueError, match=OVER_THE_MAXIMUM),
        mw.commit(entry=[Update(instance_iri=JMS.M4, data=two_values)]),
    ):
        pass

    assert held_by(ogm, JMS.M4, (JMS.hasMaxDynamicTorque,)) == {
        JMS.hasMaxDynamicTorque: [MOCK_M4_VALUES[JMS.hasMaxDynamicTorque]]
    }


def test_one_value_of_each_threshold_is_written_through_the_middleware(
    mw: SemanticMiddleware, ogm: OGM
) -> None:
    """A Screw type created with one value of each, then one value changed, as the learner does."""
    screw_type = IRI("TestScrewTypeWithOneValue", base=JMS_NS)
    with mw.create(
        str(JMS.ScrewType), ClassScope(), instance_iri=str(screw_type)
    ) as created:
        created.update({str(prop): [value] for prop, value in MOCK_M4_VALUES.items()})
    with mw.commit(
        entry=[
            Update(instance_iri=screw_type, data={str(JMS.hasMaxDynamicTorque): [1.4]})
        ]
    ):
        pass

    assert held_by(ogm, screw_type, TYPE_LEVEL) == {
        **{prop: [value] for prop, value in MOCK_M4_VALUES.items()},
        JMS.hasMaxDynamicTorque: [1.4],
    }


# --- The effective values ---------------------------------------------------------------------


def test_the_effective_values_resolve_from_the_graph(db) -> None:
    values = read_effective_values(OGM(db=db), PLACED_SCREW)

    assert as_properties(values) == PLACED_SCREW_VALUES


def test_a_screw_without_placement_values_is_refused(db) -> None:
    with pytest.raises(UnresolvedValues) as refused:
        read_effective_values(OGM(db=db), BARE_SCREW)

    assert len(refused.value.problems) == len(PLACEMENT)


# --- The decision snapshot and the unscrewing operation ---------------------------------------


def test_a_decision_snapshot_reads_back_through_its_operation(ogm: OGM) -> None:
    scope = ClassScope.from_property_chains([[JMS.usedValues, prop] for prop in TEN])
    operation = ogm.fetch(instance_iri=OPERATION, class_scope=scope)
    (used,) = (operation.data or {})[JMS.usedValues]

    assert {prop: (used.data or {}).get(prop) for prop in TEN} == {
        prop: [value] for prop, value in PLACED_SCREW_VALUES.items()
    }


def test_the_detector_writes_a_decision_snapshot_through_the_middleware(
    mw: SemanticMiddleware, db, ogm: OGM
) -> None:
    """As the detector will write one: a snapshot of the ten values, created through the
    middleware."""
    values = as_properties(read_effective_values(OGM(db=db), PLACED_SCREW))
    with mw.create(str(JMS.DecisionSnapshot), ClassScope()) as snapshot:
        snapshot.update({str(prop): [value] for prop, value in values.items()})

    assert held_by(ogm, IRI(str(snapshot.iri)), TEN) == {
        prop: [value] for prop, value in values.items()
    }


def test_an_unscrewing_operation_keeps_the_capability_it_implements(
    mw: SemanticMiddleware,
) -> None:
    """The planner's dispatch writes the operation under the middleware's operation scope, which
    sets `cfc:implementsCapability`. The mapper knows that property only on a `cfc:Operation`."""
    operation = IRI("TestOperationWithCapability", base=JMS_NS)

    with mw.create(
        str(JMS.UnscrewingOperation),
        operation_scope(ClassScope(), capability=str(UNSCREWING_CAPABILITY)),
        instance_iri=str(operation),
    ):
        pass

    read = mw.fetch(
        operation,
        class_scope=ClassScope.from_property_chains([[CFC.implementsCapability]]),
    )
    assert read.class_spec.iri == JMS.UnscrewingOperation
    assert [str(c.id) for c in (read.data or {})[CFC.implementsCapability]] == [
        str(UNSCREWING_CAPABILITY)
    ]


# --- The operation, its Screw, its product, and the Screw type the design expects --------------


def test_an_operation_that_names_a_screw_carries_that_screws_back_link(db) -> None:
    """`:hasScrew` and `:hasOperation` are two explicit triples and no `owl:inverseOf`. This
    dataset would in fact entail an inverse — it runs `rdfsplus-optimized`, which is how Core's
    `cfc:isContainedIn` appears beside `cfc:containsWorkpiece` without anyone writing it — but a
    dataset with a weaker ruleset would hold no back-link, and no caller can read a dataset's
    ruleset. So whoever writes one triple writes the other, and this asks the store for a one-sided
    pair, in either direction, of which there must be none."""
    one_sided = bindings(
        db,
        f"""
        SELECT ?operation ?screw WHERE {{
          {{ ?operation <{JMS.hasScrew}> ?screw .
             FILTER NOT EXISTS {{ ?screw <{JMS.hasOperation}> ?operation }} }}
          UNION
          {{ ?screw <{JMS.hasOperation}> ?operation .
             FILTER NOT EXISTS {{ ?operation <{JMS.hasScrew}> ?screw }} }}
        }}
        """,
    )

    assert one_sided == [], f"these pairs are one-sided: {one_sided}"


def test_a_screw_names_the_operation_that_worked_on_it(ogm: OGM) -> None:
    """The back-link read from the Screw's side: what section 5.1 calls bidirectional
    traceability for R4. A Screw recovered later names what was done to it."""
    held = held_by(ogm, PLACED_SCREW, (JMS.hasOperation,))

    assert held == {JMS.hasOperation: [str(OPERATION)]}


def test_the_walk_from_an_operation_reaches_the_screw_type_the_design_expects(
    db,
) -> None:
    """The walk of Etienne Hoffmann's redirect of 2026-10-07: from the unscrewing operation to the
    workpiece it works on, from the workpiece to its product type, and from the product type to the
    Screw type the design calls for. It is what tells the detector which Screw type the Screw
    *should* be, for a recovered product whose Screw carries no record of its own. Every step is a
    term this ticket declares or a Core term; none is invented here."""
    found = bindings(
        db,
        f"""
        SELECT ?expected WHERE {{
          <{OPERATION}> <{JMS.hasWorkpiece}> ?product .
          ?product <{HAS_PRODUCT_TYPE}> ?type .
          ?type <{JMS.hasExpectedScrewType}> ?expected .
        }}
        """,
    )

    assert [row["expected"] for row in found] == [str(JMS.M4)]


def test_the_product_contains_the_screw_the_operation_names(db) -> None:
    """The other Core link the product carries: `cfc:containsWorkpiece`, whose range is
    `cfc:Workpiece`, which a Screw is. This repository declares no part-whole property of its
    own."""
    contained = bindings(
        db,
        f"SELECT ?screw WHERE {{ <{GRINDER}> <{CONTAINS_WORKPIECE}> ?screw }}",
    )

    assert [row["screw"] for row in contained] == [str(PLACED_SCREW)]
