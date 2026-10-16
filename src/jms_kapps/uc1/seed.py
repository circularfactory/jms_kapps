"""What use case 1 finds in the graph before any participant starts.

Two recovered angle grinders, the Screw in each, the model the grinders are, the Screw type that
model is built with, and how the screwing resource replays a recording for each Screw. The planner
offers the Screws, the screwing resource makes the attempts, the detector classifies them against
this Screw type's thresholds, and the learner refines them.

Two grinders, because a demonstration should show more than one, and the few existing recordings
are enough for each: the demo names two grinders with two Screws and replays its two recordings,
one per Screw. So one run shows both exits of an attempt, `ScrewIntact` and `MissingScrew`.

**Every value here is mock, and none of it is measured.** The thresholds are fitted to the two
recordings the paper's artifact ships (`data/unscrewing_timeseries/`), which are the only
unscrewing data use case 1 has: with them the recording `success` classifies as `ScrewIntact` and
`missing_screw` as `MissingScrew`, which is what a demonstration must show. Thresholds that do not
fit those recordings make the demo report `HeadOccluded` for a good screw and raise on the missing
one, because the screw-head band then sits where the recording never touches and the preload is
reached only at the last sample. `test_uc1_seed.py` holds both facts as tests. Milestone 2 replaces
all of them with values measured on the real cell.

The seed keeps `hasMinLooseningAxialForce` above `hasMinPreloadAxialForce`, so that a force held at
the preload level is not read as a rise (Max Goebels, 2026-10-09, on the detector's ticket).
"""

from __future__ import annotations

import logging

from kapps_ogm import OGM, ClassScope
from kapps_semantic_middleware.vocabulary import CORE_NS
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import read_asset, reset
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

logger = logging.getLogger(__name__)

# The demo's two grinders, their two Screws and the model they are. The paper names the grinder of
# its traceability chain `ex:AngleGrinder_099`; this is that grinder.
GRINDER = IRI("Grinder099", base=JMS_NS)
GRINDER_2 = IRI("Grinder100", base=JMS_NS)
SCREW = IRI("Screw1", base=JMS_NS)
SCREW_2 = IRI("Screw2", base=JMS_NS)
PRODUCT_TYPE = IRI("AG1", base=JMS_NS)

# The demo's grinders, which `jms-uc1` passes to the planner.
GRINDERS: tuple[IRI, ...] = (GRINDER, GRINDER_2)

# Which legacy mock recording the screwing resource replays for each demo Screw, so that one run
# shows both exits, ScrewIntact and MissingScrew. It lives here, not in the graph, because a
# recording is the demo's stand-in for the robot, not a fact about the Screw.
RECORDING_OF: dict[IRI, str] = {SCREW: "success", SCREW_2: "missing_screw"}

# Core's two links the grinder carries, and the class of its model. The middleware's vocabulary
# names only the Core terms its own code uses, and none of these is one of them, so each is built
# from the namespace the middleware exports rather than spelled out here.
CONTAINS_WORKPIECE = IRI("containsWorkpiece", base=CORE_NS)
HAS_PRODUCT_TYPE = IRI("hasProductType", base=CORE_NS)
CORE_PRODUCT_TYPE = IRI("ProductType", base=CORE_NS)

# `:M4`'s thresholds. Mock, fitted to the recordings, not measured. The preload is reached at the
# first preparation sample, so the turn after it holds the second, whose force meets the loosening
# minimum: the `success` recording passes every node. A preload of 1.0 is reached only at the last
# sample, leaving no turn to measure, and the attempt then exits at `LooseAnchor`.
M4_VALUES: dict[IRI, float] = {
    JMS.hasMinApproachAxialForce: 0.5,
    JMS.hasMinCounterTorque: 0.5,
    JMS.hasMinPreloadAxialForce: 0.1,
    JMS.hasMinLooseningAxialForce: 1.0,
    JMS.hasMaxLooseTorque: 3.0,
    JMS.hasMaxDynamicTorque: 1.5,
    JMS.hasPreparationTurningAngle: 30.0,
}

# The Screw's placement values, which belong to where this screw sits and not to its type. Mock,
# fitted to the recordings. The approach of both ends at 0.0, so the screw-head band holds 0.0 and
# the depth limit lies above it: the `success` recording touches the head inside the band, and the
# approach of `missing_screw`, which touches nothing, crosses the limit and reads as a missing
# screw instead of raising. The fitted placement fits both recordings, so both Screws share it.
PLACEMENT_VALUES: dict[IRI, float] = {
    JMS.hasMinApproachPosition: 0.25,
    JMS.hasMinScrewHeadPosition: -0.5,
    JMS.hasMaxScrewHeadPosition: 0.5,
}


def reset_uc1(db: TripleStore, *, individuals: str = "") -> None:
    """Empty the dataset and load use case 1's ontology and shapes, then `individuals`.

    The shapes are `shapes/integration_usecase_shapes.ttl`: one shape, the PROV witness on the
    decision, which makes the store refuse a decision snapshot that does not name the
    classification that generated it. (Added on 2026-10-16, jms_kapps 59. Before, use case 1
    shipped no shapes and sent none.)
    """
    reset(
        db,
        ontology=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        shapes=read_asset("jms_kapps.uc1", "shapes", "integration_usecase_shapes.ttl"),
        individuals=individuals,
    )


def _create(ogm: OGM, class_iri: IRI, instance: IRI, data: dict) -> None:
    """Create `instance` with all of `data` in one validated write."""
    scope = ClassScope.from_property_chains([[IRI(prop)] for prop in data])
    log_scope(
        logger,
        "Seed",
        "writes",
        instance,
        "its own scope",
        scope,
        "it writes exactly the properties it seeds",
    )
    ogm.create(
        class_iri=class_iri,
        instance_iri=instance,
        data=data,
        class_scope=scope,
        persist=True,
    )


# What the seed reads of a grinder to give its Screws their type: the Screws it contains, and the
# Screw type that its model, its product type, expects.
DESIGN_SCOPE = ClassScope.from_property_chains(
    [[CONTAINS_WORKPIECE], [HAS_PRODUCT_TYPE, JMS.hasExpectedScrewType]]
)


def seed_demo(ogm: OGM) -> None:
    """Write the demo's two grinders, their Screws, their model and the values of the Screw type
    it is built with, then give each Screw the Screw type its grinder's model expects.

    In that order, so that each link names something the graph already holds: the Screw type's
    values, then the model that expects the type, then the Screws with their placement values,
    then the grinders that contain the Screws and name the model.

    **A Screw's own `:hasScrewType` is not seeded as a value.** It is the record of this screw,
    taken from the design: the seed reads, for each grinder, the Screw type that its product
    type expects (`:hasExpectedScrewType`), and writes it to each Screw the grinder contains
    (`_record_from_the_design`). So the record follows the design in the graph, and a change of
    the design changes the next seed's records.

    Both Screws share `PLACEMENT_VALUES`, which fit both recordings, and both grinders share the
    model. The recordings are not a fact about a Screw, but the demo's stand-in for the robot, so
    which recording each Screw replays lives in `RECORDING_OF`, not in the graph.

    `:M4` is **updated**, not created: the ontology declares it, with no values
    (`test_uc1_ontology.py`), and the values are what a seed adds.
    """
    ogm.commit(
        instance_iri=JMS.M4,
        data={str(prop): [value] for prop, value in M4_VALUES.items()},
    )
    _create(
        ogm,
        CORE_PRODUCT_TYPE,
        PRODUCT_TYPE,
        {str(JMS.hasExpectedScrewType): [{"id": str(JMS.M4)}]},
    )
    for screw in (SCREW, SCREW_2):
        _create(
            ogm,
            JMS.Screw,
            screw,
            {str(prop): [value] for prop, value in PLACEMENT_VALUES.items()},
        )
    for grinder, screw in ((GRINDER, SCREW), (GRINDER_2, SCREW_2)):
        _create(
            ogm,
            JMS.AngleGrinder,
            grinder,
            {
                str(CONTAINS_WORKPIECE): [{"id": str(screw)}],
                str(HAS_PRODUCT_TYPE): [{"id": str(PRODUCT_TYPE)}],
            },
        )
    for grinder in GRINDERS:
        _record_from_the_design(ogm, grinder)


def _record_from_the_design(ogm: OGM, grinder: IRI) -> None:
    """Give each Screw that `grinder` contains the Screw type that the grinder's product type
    expects, as the Screw's own `:hasScrewType`.

    Raises:
        ValueError: If the grinder's product type expects no Screw type, or more than one.
    """
    log_scope(
        logger,
        "Seed",
        "reads",
        grinder,
        "DESIGN_SCOPE",
        DESIGN_SCOPE,
        "it wants the Screws the grinder contains (containsWorkpiece) and the Screw type its "
        "product type expects (hasProductType, then hasExpectedScrewType)",
    )
    data = ogm.fetch(instance_iri=grinder, class_scope=DESIGN_SCOPE).data or {}
    expected = {
        _id(screw_type)
        for product_type in data.get(HAS_PRODUCT_TYPE, [])
        for screw_type in (getattr(product_type, "data", None) or {}).get(
            JMS.hasExpectedScrewType, []
        )
    }
    if len(expected) != 1:
        raise ValueError(
            f"{grinder.fragment}'s product type expects {len(expected)} Screw types; the seed "
            "gives a Screw the one it expects"
        )
    (screw_type,) = expected
    for screw in data.get(CONTAINS_WORKPIECE, []):
        logger.info(
            "Seed: %s gets hasScrewType %s, the Screw type that %s's product type expects",
            IRI(_id(screw)).fragment,
            IRI(screw_type).fragment,
            grinder.fragment,
        )
        ogm.commit(
            instance_iri=IRI(_id(screw)),
            data={str(JMS.hasScrewType): [{"id": screw_type}]},
        )


def _id(value: object) -> str:
    """The IRI of a linked value, which the mapper gives as a node with an `id`."""
    return str(getattr(value, "id", value))
