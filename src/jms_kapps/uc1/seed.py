"""What use case 1 finds in the graph before any participant starts.

Two recovered angle grinders, the Screw in each, the model the grinders are, the Screw type that
model is built with, and how the screwing resource replays a recording for each Screw. The planner
offers the Screws, the screwing resource makes the attempts, the detector classifies them against
this Screw type's thresholds, and the learner refines them.

Two grinders, because a demonstration should show more than one, and the few existing recordings
are enough for each: the demo names two grinders with two Screws and replays its two recordings,
one per Screw. So one run shows both exits of an attempt, `ScrewIntact` and `MissingScrew`.

**Every value here is an example value, fitted to the two example recordings, and none of it is
measured.** The thresholds are fitted to the two
recordings the paper's artifact ships (`data/unscrewing_timeseries/`), which are the only
unscrewing data use case 1 has: with them the recording `success` classifies as `ScrewIntact` and
`missing_screw` as `MissingScrew`, which is what a demonstration must show. Thresholds that do not
fit those recordings make the demo report `HeadOccluded` for a good screw and raise on the missing
one, because the screw-head band then sits where the recording never touches and the preload is
reached only at the last sample. `test_uc1_seed.py` holds both facts as tests. Milestone 2 replaces
all of them with values measured on the real cell.

The seed keeps `hasMinLooseningAxialForce` above `hasMinPreloadAxialForce`, so that a force held at
the preload level is not read as a rise.
"""

from __future__ import annotations

import logging

from kapps_ogm import OGM, ClassScope, Create, Update
from kapps_semantic_middleware.vocabulary import CORE_NS, INF
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import read_asset, reset
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, READWRITE, ROBOT_PARAMETERS

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

# What `jms-uc1 infeasible-operation` seeds besides the demo: a Screw type that holds none of the
# seven values an attempt needs, a model that expects it, a Screw of that type and a grinder that
# contains the Screw. The planner offers the Screw like any other, and the store refuses the
# unscrewing operation the planner issues for it. A normal launch seeds none of it.
UNMEASURED_SCREW_TYPE = IRI("M3", base=JMS_NS)
UNMEASURED_PRODUCT_TYPE = IRI("AG2", base=JMS_NS)
UNMEASURED_SCREW = IRI("Screw3", base=JMS_NS)
UNMEASURED_GRINDER = IRI("Grinder101", base=JMS_NS)

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

# Which example recording the screwing resource replays for each demo Screw, so that one run
# shows both exits, ScrewIntact and MissingScrew. It lives here, not in the graph, because a
# recording is the demo's stand-in for the robot, not a fact about the Screw.
RECORDING_OF: dict[IRI, str] = {SCREW: "success", SCREW_2: "missing_screw"}

# Core's three links the grinder carries, and the class of its model. The middleware's vocabulary
# names only the Core terms its own code uses, and none of these is one of them, so each is built
# from the namespace the middleware exports rather than spelled out here. The model is one
# individual, AG1, that is both the product type and the assembly type of the grinders: the grinder
# names it with `cfc:hasProductType` and with `cfc:hasAssemblyType`. AG1's only explicit class is
# the assembly type, which carries the design. By Core's range of `cfc:hasProductType` it is also a
# product type for a reasoner that applies `rdfs:range`; the demo store's ruleset
# (rdfsplus-optimized) does not materialize range conclusions. The mapper takes an instance's class from its explicit types, so a later
# update of AG1 still finds `:hasExpectedScrewType`.
CONTAINS_WORKPIECE = IRI("containsWorkpiece", base=CORE_NS)
HAS_PRODUCT_TYPE = IRI("hasProductType", base=CORE_NS)
HAS_ASSEMBLY_TYPE = IRI("hasAssemblyType", base=CORE_NS)
CORE_ASSEMBLY_TYPE = IRI("AssemblyType", base=CORE_NS)

# `:M4`'s thresholds. Example values, fitted to the two example recordings. The preload is reached at the
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

# The Screw's placement values, which belong to where this screw sits and not to its type. Example
# values, fitted to the two example recordings. The approach of both ends at 0.0, so the screw-head band holds 0.0 and
# the depth limit lies above it: the `success` recording touches the head inside the band, and the
# approach of `missing_screw`, which touches nothing, crosses the limit and reads as a missing
# screw instead of raising. The fitted placement fits both recordings, so both Screws share it.
PLACEMENT_VALUES: dict[IRI, float] = {
    JMS.hasMinApproachPosition: 0.25,
    JMS.hasMinScrewHeadPosition: -0.5,
    JMS.hasMaxScrewHeadPosition: 0.5,
}

# The screwing resource, which `run_core` writes with its label, and its robot, which the seed
# writes. The robot's controller speaks only MQTT; the robot controller stand-in
# (`robot_controller`) takes its place, on the same broker and the same topics.
SCREWING_RESOURCE = IRI("ScrewingResource1", base=JMS_NS)
ROBOT = IRI("Robot1", base=JMS_NS)

# The broker that carries every parameter of the robot. The screwing resource's middleware
# instance brings it up at this address when it builds its connectors (`mqtt_broker`), and the
# stand-in is started with it. The port is the use case's own: not 1883, and not a port of the
# TransferUnit demo or of the middleware's test suite.
MQTT_BROKER_IP = "127.0.0.1"
MQTT_BROKER_PORT = 18860

# The key of a payload of the robot that holds the value. Each payload is a JSON object.
MQTT_VALUE_PATH = "v"


def mqtt_topic(parameter: IRI) -> str:
    """The topic on which the robot publishes `parameter`: `Robot1/<its local name>`."""
    return f"{ROBOT.fragment}/{parameter.fragment}"


def mqtt_set_topic(parameter: IRI) -> str:
    """The topic to which a client publishes a new value of `parameter`: its topic, then `/set`."""
    return f"{mqtt_topic(parameter)}/set"


def robot_parameter_node(parameter: IRI) -> dict:
    """The node of `parameter` in the graph: how it is reached, and whether a client may set it.

    The node carries no value. The value is the robot's, and it reaches the screwing resource's
    data model over MQTT, not through the graph.
    """
    readwrite = parameter in READWRITE
    node: dict = {
        str(INF.accessMode): ["readwrite" if readwrite else "read"],
        str(INF.hasMQTTTopic): [mqtt_topic(parameter)],
        str(INF.hasMQTTBrokerIP): [MQTT_BROKER_IP],
        str(INF.hasMQTTBrokerPort): [MQTT_BROKER_PORT],
        str(INF.hasMQTTValuePath): [MQTT_VALUE_PATH],
    }
    if readwrite:
        node[str(INF.hasMQTTSetTopic)] = [mqtt_set_topic(parameter)]
    return node


def unmeasured_screw_type_turtle() -> str:
    """The Turtle of `UNMEASURED_SCREW_TYPE`: a `:ScrewType` with its label and none of the seven
    values, declared as the ontology declares `:M4`.

    Full IRIs and no prefixes, because the store keeps a prefix that an import declares.
    """
    return (
        f"<{UNMEASURED_SCREW_TYPE}> <{_RDF_TYPE}> <{JMS.ScrewType}> ; "
        f'<{_RDFS_LABEL}> "{UNMEASURED_SCREW_TYPE.fragment}"@en .\n'
    )


def reset_uc1(db: TripleStore, *, individuals: str = "") -> None:
    """Empty the dataset and load use case 1's ontology and shapes, then `individuals`.

    The shapes are `shapes/integration_usecase_shapes.ttl`: the parameter-copy shape, the PROV
    witness on the decision, which makes the store refuse a parameter copy that does not name
    the classification that generated it; the capability shape, which makes the store refuse
    a capability that does not belong to exactly one resource; and the unscrewing operation
    shape, which makes the store refuse an unscrewing operation whose Screw's Screw type does
    not hold each of the seven values an attempt needs. `reset` adds the middleware's
    operation-status shape to them.
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
# Screw type that its model, its assembly type, expects.
DESIGN_SCOPE = ClassScope.from_property_chains(
    [[CONTAINS_WORKPIECE], [HAS_ASSEMBLY_TYPE, JMS.hasExpectedScrewType]]
)


def seed_demo(ogm: OGM, *, unmeasured_screw_type: bool = False) -> None:
    """Write the demo's two grinders, their Screws, their model and the values of the Screw type
    it is built with, then give each Screw the Screw type its grinder's model expects.

    In that order, so that each link names something the graph already holds: the Screw type's
    values, then the model that expects the type, then the Screws with their placement values,
    then the grinders that contain the Screws and name the model.

    **A Screw's own `:hasScrewType` is not seeded as a value.** It is the record of this screw,
    taken from the design: the seed reads, for each grinder, the Screw type that its
    assembly type expects (`:hasExpectedScrewType`), and writes it to each Screw the grinder contains
    (`_record_from_the_design`). So the record follows the design in the graph, and a change of
    the design changes the next seed's records.

    Both Screws share `PLACEMENT_VALUES`, which fit both recordings, and both grinders share the
    model. The recordings are not a fact about a Screw, but the demo's stand-in for the robot, so
    which recording each Screw replays lives in `RECORDING_OF`, not in the graph.

    `:M4` is **updated**, not created: the ontology declares it, with no values
    (`test_uc1_ontology.py`), and the values are what a seed adds.

    With `unmeasured_screw_type`, it also writes the model `UNMEASURED_PRODUCT_TYPE`, which
    expects the Screw type `UNMEASURED_SCREW_TYPE`, the Screw `UNMEASURED_SCREW` with
    `PLACEMENT_VALUES`, and the grinder `UNMEASURED_GRINDER` that contains it, and gives the Screw
    its Screw type from the design. The Screw type itself holds no value: the caller declares it
    in the reset's individuals (`unmeasured_screw_type_turtle`), as the ontology declares `:M4`.
    The Screw has no recording, because the store refuses its operation before the screwing
    resource is notified.
    """
    ogm.commit(
        instance_iri=JMS.M4,
        data={str(prop): [value] for prop, value in M4_VALUES.items()},
    )
    _create(
        ogm,
        CORE_ASSEMBLY_TYPE,
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
                str(HAS_ASSEMBLY_TYPE): [{"id": str(PRODUCT_TYPE)}],
            },
        )
    for grinder in GRINDERS:
        _record_from_the_design(ogm, grinder)
    if unmeasured_screw_type:
        _seed_unmeasured_screw_type(ogm)
    seed_robot(ogm)


def _seed_unmeasured_screw_type(ogm: OGM) -> None:
    """Write the model that expects `UNMEASURED_SCREW_TYPE`, its Screw and its grinder, in the
    order of `seed_demo`, and give the Screw its Screw type from the design."""
    _create(
        ogm,
        CORE_ASSEMBLY_TYPE,
        UNMEASURED_PRODUCT_TYPE,
        {str(JMS.hasExpectedScrewType): [{"id": str(UNMEASURED_SCREW_TYPE)}]},
    )
    _create(
        ogm,
        JMS.Screw,
        UNMEASURED_SCREW,
        {str(prop): [value] for prop, value in PLACEMENT_VALUES.items()},
    )
    _create(
        ogm,
        JMS.AngleGrinder,
        UNMEASURED_GRINDER,
        {
            str(CONTAINS_WORKPIECE): [{"id": str(UNMEASURED_SCREW)}],
            str(HAS_PRODUCT_TYPE): [{"id": str(UNMEASURED_PRODUCT_TYPE)}],
            str(HAS_ASSEMBLY_TYPE): [{"id": str(UNMEASURED_PRODUCT_TYPE)}],
        },
    )
    _record_from_the_design(ogm, UNMEASURED_GRINDER)


def seed_robot(ogm: OGM) -> None:
    """Write the robot of the screwing resource, with a node for each of its parameters, and link
    the two in both directions.

    The screwing resource must be in the graph already: `run_core` writes it, of the class
    `:ScrewingResource`. Both links are written, and not left to the store's inference of the
    inverse, because the middleware finds a resource's components and their parameters only in
    the triples that are written.

    Both links go in one commit: the update of the resource and the creation of the robot. The
    mapper reads the resource before it writes, and its read includes inferred links. A separate
    update after the robot's creation would read `:hasRobot` as the store already infers it from
    `:isRobotOf`, find nothing to change, and write nothing. Then the link exists only by
    inference, and the middleware finds no robot below the resource. In one commit, the read
    comes before the robot exists, so the update writes the link.
    """
    data: dict = {str(JMS.isRobotOf): [{"id": str(SCREWING_RESOURCE)}]}
    for parameter in ROBOT_PARAMETERS:
        data[str(parameter)] = [robot_parameter_node(parameter)]
    scope = ClassScope.from_property_chains([[IRI(prop)] for prop in data])
    log_scope(
        logger,
        "Seed",
        "writes",
        ROBOT,
        "its own scope",
        scope,
        "it writes exactly the properties it seeds",
    )
    ogm.commit(
        changes=[
            Update(
                instance_iri=SCREWING_RESOURCE,
                data={str(JMS.hasRobot): [{"id": str(ROBOT)}]},
            ),
            Create(
                class_iri=JMS.Robot,
                instance_iri=ROBOT,
                data=data,
                class_scope=scope,
            ),
        ]
    )


def _record_from_the_design(ogm: OGM, grinder: IRI) -> None:
    """Give each Screw that `grinder` contains the Screw type that the grinder's assembly type
    expects, as the Screw's own `:hasScrewType`.

    Raises:
        ValueError: If the grinder's assembly type expects no Screw type, or more than one.
    """
    log_scope(
        logger,
        "Seed",
        "reads",
        grinder,
        "DESIGN_SCOPE",
        DESIGN_SCOPE,
        "it wants the Screws the grinder contains (containsWorkpiece) and the Screw type its "
        "assembly type expects (hasAssemblyType, then hasExpectedScrewType)",
    )
    data = ogm.fetch(instance_iri=grinder, class_scope=DESIGN_SCOPE).data or {}
    expected = {
        _id(screw_type)
        for assembly_type in data.get(HAS_ASSEMBLY_TYPE, [])
        for screw_type in (getattr(assembly_type, "data", None) or {}).get(
            JMS.hasExpectedScrewType, []
        )
    }
    if len(expected) != 1:
        raise ValueError(
            f"{grinder.fragment}'s assembly type expects {len(expected)} Screw types; the seed "
            "gives a Screw the one it expects"
        )
    (screw_type,) = expected
    for screw in data.get(CONTAINS_WORKPIECE, []):
        logger.info(
            "Seed: %s gets hasScrewType %s, the Screw type that %s's assembly type expects",
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
