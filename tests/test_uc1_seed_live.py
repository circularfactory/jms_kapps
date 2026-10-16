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
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.planner_reads import ScrewOnProduct, read_screws
from jms_kapps.uc1.seed import (
    GRINDER,
    GRINDER_2,
    M4_VALUES,
    PLACEMENT_VALUES,
    PRODUCT_TYPE,
    SCREW,
    SCREW_2,
    reset_uc1,
    seed_demo,
)
from jms_kapps.uc1.vocabulary import JMS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

HAS_PRODUCT_TYPE = IRI("hasProductType", base=CORE_NS)
CONTAINS_WORKPIECE = IRI("containsWorkpiece", base=CORE_NS)


@pytest.fixture(scope="module")
def seeded(dataset):
    """The dataset with use case 1's ontology and the demo seed in it."""
    db = connect(dataset)
    reset_uc1(db)
    ogm = OGM(db=db)
    seed_demo(ogm)
    return ogm


def held(db, subject: IRI, prop: IRI) -> list[str]:
    rows = bindings(db, f"SELECT ?o WHERE {{ <{subject}> <{prop}> ?o }} ORDER BY ?o")
    return [str(row["o"]) for row in rows]


def test_the_planner_offers_each_demo_screw_on_its_grinder(seeded: OGM) -> None:
    """One Screw per grinder, so the select holds exactly one entry per grinder."""
    assert read_screws(seeded, GRINDER) == [ScrewOnProduct(SCREW, GRINDER)]
    assert read_screws(seeded, GRINDER_2) == [ScrewOnProduct(SCREW_2, GRINDER_2)]


def test_each_grinder_names_its_screw_and_its_model(seeded: OGM) -> None:
    """Both over Core's links: this repository declares no part-whole property of its own."""
    assert held(seeded.db, GRINDER, CONTAINS_WORKPIECE) == [str(SCREW)]
    assert held(seeded.db, GRINDER, HAS_PRODUCT_TYPE) == [str(PRODUCT_TYPE)]
    assert held(seeded.db, GRINDER_2, CONTAINS_WORKPIECE) == [str(SCREW_2)]
    assert held(seeded.db, GRINDER_2, HAS_PRODUCT_TYPE) == [str(PRODUCT_TYPE)]


def test_the_design_reaches_the_screw_type_from_the_product_type(seeded: OGM) -> None:
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
    """What tells the detector which Screw type the Screw should be: product → product type →
    expected Screw type."""
    for grinder in (GRINDER, GRINDER_2):
        found = bindings(
            seeded.db,
            f"""
            SELECT ?expected WHERE {{
              <{grinder}> <{HAS_PRODUCT_TYPE}> ?type .
              ?type <{JMS.hasExpectedScrewType}> ?expected .
            }}
            """,
        )

        assert [row["expected"] for row in found] == [str(JMS.M4)]
