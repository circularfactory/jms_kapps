"""The seed of use case 1 on a live store: what a run finds before any participant starts.

It writes the seed through the object-graph mapper, as the seed does, and reads back what the
planner and the detector walk. These tests clear the dataset they run against. They run only when
the store's connection variables are set, against the dataset `--dataset` names.
"""

from __future__ import annotations

import pytest
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import CORE_NS
from kapps_triplestore_interface import IRI, SPARQLQuery

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.planner_reads import ScrewOnProduct, read_screws
from jms_kapps.uc1.run_core import resources_turtle
from jms_kapps.uc1.seed import (
    GRINDER,
    GRINDER_2,
    M4_VALUES,
    MQTT_BROKER_IP,
    MQTT_BROKER_PORT,
    PLACEMENT_VALUES,
    PRODUCT_TYPE,
    ROBOT,
    SCREW,
    SCREW_2,
    SCREWING_RESOURCE,
    mqtt_set_topic,
    mqtt_topic,
    reset_uc1,
    seed_demo,
)
from jms_kapps.uc1.vocabulary import JMS, READWRITE, ROBOT_PARAMETERS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

HAS_PRODUCT_TYPE = IRI("hasProductType", base=CORE_NS)
HAS_ASSEMBLY_TYPE = IRI("hasAssemblyType", base=CORE_NS)
CORE_ASSEMBLY_TYPE = IRI("AssemblyType", base=CORE_NS)
RDF_TYPE = IRI("type", base="http://www.w3.org/1999/02/22-rdf-syntax-ns#")
CONTAINS_WORKPIECE = IRI("containsWorkpiece", base=CORE_NS)


@pytest.fixture(scope="module")
def seeded(dataset):
    """The dataset with use case 1's ontology, the resources that `jms-uc1` writes and the demo
    seed in it."""
    db = connect(dataset)
    reset_uc1(db, individuals=resources_turtle())
    ogm = OGM(db=db)
    seed_demo(ogm)
    return ogm


def held(db, subject: IRI, prop: IRI) -> list[str]:
    rows = bindings(db, f"SELECT ?o WHERE {{ <{subject}> <{prop}> ?o }} ORDER BY ?o")
    return [str(row["o"]) for row in rows]


def written(db, subject: IRI, prop: IRI) -> list[str]:
    """What `subject` holds of `prop` as written triples, without what the store infers."""
    query = SPARQLQuery.select(
        variables=["?o"],
        where_clauses=[f"<{subject}> <{prop}> ?o"],
        include_implicit=False,
    )
    return sorted(str(row["o"]) for row in bindings(db, query))


def test_the_planner_offers_each_demo_screw_on_its_grinder(seeded: OGM) -> None:
    """One Screw per grinder, so the select holds exactly one entry per grinder."""
    assert read_screws(seeded, GRINDER) == [ScrewOnProduct(SCREW, GRINDER)]
    assert read_screws(seeded, GRINDER_2) == [ScrewOnProduct(SCREW_2, GRINDER_2)]


def test_each_grinder_names_its_screw_and_its_model(seeded: OGM) -> None:
    """Both over Core's links: this repository declares no part-whole property of its own."""
    assert held(seeded.db, GRINDER, CONTAINS_WORKPIECE) == [str(SCREW)]
    assert held(seeded.db, GRINDER, HAS_PRODUCT_TYPE) == [str(PRODUCT_TYPE)]
    assert held(seeded.db, GRINDER, HAS_ASSEMBLY_TYPE) == [str(PRODUCT_TYPE)]
    assert held(seeded.db, GRINDER_2, CONTAINS_WORKPIECE) == [str(SCREW_2)]
    assert held(seeded.db, GRINDER_2, HAS_PRODUCT_TYPE) == [str(PRODUCT_TYPE)]
    assert held(seeded.db, GRINDER_2, HAS_ASSEMBLY_TYPE) == [str(PRODUCT_TYPE)]


def test_the_model_holds_the_assembly_type_and_the_design(seeded: OGM) -> None:
    """AG1's explicit class is the assembly type, and it carries the design. The store (rdfsplus-optimized)
    does not materialize `cfc:ProductType` from the range of `cfc:hasProductType`, so none is asserted."""
    assert str(CORE_ASSEMBLY_TYPE) in held(seeded.db, PRODUCT_TYPE, RDF_TYPE)
    assert held(seeded.db, PRODUCT_TYPE, JMS.hasExpectedScrewType) == [str(JMS.M4)]


def test_the_design_reaches_the_screw_type_from_the_assembly_type(seeded: OGM) -> None:
    """The step the detector takes for a recovered product whose Screw carries no record."""
    assert held(seeded.db, PRODUCT_TYPE, JMS.hasExpectedScrewType) == [str(JMS.M4)]


def test_each_screw_keeps_its_own_record_of_its_screw_type(seeded: OGM) -> None:
    """Which outlives the grinder it came from, and is what R4 asks for once it is removed."""
    assert held(seeded.db, SCREW, JMS.hasScrewType) == [str(JMS.M4)]
    assert held(seeded.db, SCREW_2, JMS.hasScrewType) == [str(JMS.M4)]


def test_the_screw_type_holds_the_seeded_thresholds(seeded: OGM) -> None:
    for prop, value in M4_VALUES.items():
        assert held(seeded.db, JMS.M4, prop) == [str(value)], prop


def test_each_screw_holds_its_placement_values(seeded: OGM) -> None:
    for screw in (SCREW, SCREW_2):
        for prop, value in PLACEMENT_VALUES.items():
            assert held(seeded.db, screw, prop) == [str(value)], (screw, prop)


def test_the_walk_from_each_product_reaches_the_expected_screw_type(
    seeded: OGM,
) -> None:
    """What tells the detector which Screw type the Screw should be: product → assembly type →
    expected Screw type."""
    for grinder in (GRINDER, GRINDER_2):
        found = bindings(
            seeded.db,
            f"""
            SELECT ?expected WHERE {{
              <{grinder}> <{HAS_ASSEMBLY_TYPE}> ?type .
              ?type <{JMS.hasExpectedScrewType}> ?expected .
            }}
            """,
        )

        assert [row["expected"] for row in found] == [str(JMS.M4)]


def test_the_robot_belongs_to_the_screwing_resource_both_ways(seeded: OGM) -> None:
    """Both links are written triples, which the middleware's recognition reads."""
    assert str(JMS.ScrewingResource) in held(seeded.db, SCREWING_RESOURCE, RDF_TYPE)
    assert str(JMS.Robot) in held(seeded.db, ROBOT, RDF_TYPE)
    # Written, not inferred: the store infers each link from the other, and the middleware
    # reads only what is written.
    assert written(seeded.db, SCREWING_RESOURCE, JMS.hasRobot) == [str(ROBOT)]
    assert written(seeded.db, ROBOT, JMS.isRobotOf) == [str(SCREWING_RESOURCE)]


def test_the_store_does_not_make_the_robot_a_screw_type(seeded: OGM) -> None:
    """With the store's own reasoning (rdfsplus-optimized), inferred triples included."""
    types = held(seeded.db, ROBOT, RDF_TYPE)

    assert str(JMS.ScrewType) not in types
    assert str(JMS.Screw) not in types


def test_each_robot_parameter_names_its_topic_and_its_broker(seeded: OGM) -> None:
    rows = bindings(
        seeded.db,
        f"""
        PREFIX inf: <http://www.sfb1574.kit.edu/ontologies/CrcInterfaces#>
        SELECT ?p ?mode ?topic ?set ?ip ?port ?path WHERE {{
            VALUES ?p {{ {" ".join(f"<{prop}>" for prop in ROBOT_PARAMETERS)} }}
            <{ROBOT}> ?p ?node .
            ?node inf:accessMode ?mode ; inf:hasMQTTTopic ?topic ;
                inf:hasMQTTBrokerIP ?ip ; inf:hasMQTTBrokerPort ?port ;
                inf:hasMQTTValuePath ?path .
            OPTIONAL {{ ?node inf:hasMQTTSetTopic ?set }}
        }}
        """,
    )
    found = {
        str(row["p"]): (
            str(row["mode"]),
            str(row["topic"]),
            str(row["set"]) if row.get("set") is not None else None,
            str(row["ip"]),
            int(str(row["port"])),
            str(row["path"]),
        )
        for row in rows
    }

    assert found == {
        str(prop): (
            "readwrite" if prop in READWRITE else "read",
            mqtt_topic(prop),
            mqtt_set_topic(prop) if prop in READWRITE else None,
            MQTT_BROKER_IP,
            MQTT_BROKER_PORT,
            "v",
        )
        for prop in ROBOT_PARAMETERS
    }
