"""The terms of use case 1 that its Python reads and writes.

Each term is declared in `ontology/integration_usecase.ttl`. The ontology smoke test checks that
the file declares every term here, so the two cannot drift apart.
"""

from __future__ import annotations

from kapps_triplestore_interface import IRI

JMS_NS = "http://w3id.org/circularfactory/Unscrewing#"
PROV_NS = "http://www.w3.org/ns/prov#"


class JMS:
    """Terms from `ontology/integration_usecase.ttl`."""

    # Classes
    Screw = IRI("Screw", base=JMS_NS)
    ScrewType = IRI("ScrewType", base=JMS_NS)
    ParameterCopy = IRI("ParameterCopy", base=JMS_NS)
    UnscrewingOperation = IRI("UnscrewingOperation", base=JMS_NS)
    # The product: a Core assembly, which names the Screws it contains, and a Core product, which
    # names its model. Both links are Core's, so this file names neither.
    AngleGrinder = IRI("AngleGrinder", base=JMS_NS)

    # Object properties
    hasScrewType = IRI("hasScrewType", base=JMS_NS)
    # The design, on the `cfc:AssemblyType`, beside `hasScrewType`, the record on the
    # Screw. Two properties, because a domain is an inference rule: a reasoner that applies
    # `rdfs:domain` would conclude that an assembly type that carries `hasScrewType` is a Screw.
    # Nothing in the use cases relies on the store materializing that conclusion.
    hasExpectedScrewType = IRI("hasExpectedScrewType", base=JMS_NS)
    classifiedWith = IRI("classifiedWith", base=JMS_NS)

    # Where an unscrewing operation's curves are, in the time-series store
    hasTimeSeriesReference = IRI("hasTimeSeriesReference", base=JMS_NS)

    # The detection thresholds and the preparation turning angle
    hasMinApproachAxialForce = IRI("hasMinApproachAxialForce", base=JMS_NS)
    hasMinCounterTorque = IRI("hasMinCounterTorque", base=JMS_NS)
    hasMinPreloadAxialForce = IRI("hasMinPreloadAxialForce", base=JMS_NS)
    hasPreparationTurningAngle = IRI("hasPreparationTurningAngle", base=JMS_NS)
    hasMaxLooseTorque = IRI("hasMaxLooseTorque", base=JMS_NS)
    hasMinLooseningAxialForce = IRI("hasMinLooseningAxialForce", base=JMS_NS)
    hasMaxDynamicTorque = IRI("hasMaxDynamicTorque", base=JMS_NS)

    # The placement values
    hasMinApproachPosition = IRI("hasMinApproachPosition", base=JMS_NS)
    hasMinScrewHeadPosition = IRI("hasMinScrewHeadPosition", base=JMS_NS)
    hasMaxScrewHeadPosition = IRI("hasMaxScrewHeadPosition", base=JMS_NS)

    # Individuals
    M4 = IRI("M4", base=JMS_NS)

    # The screwing resource: the Screw an operation names, its service, and its workflow and the
    # capability that workflow realizes. The planner dispatches each unscrewing operation to
    # `UnscrewingCapability`. Then the capability of the anomaly detector, by which the screwing
    # resource finds the detector's classify workflow.
    concernsScrew = IRI("concernsScrew", base=JMS_NS)
    # What the operation works on, and the Screw's link back to the operation. The pair
    # `hasOperation` is the `owl:inverseOf` `concernsScrew`: the store infers it in the operation's own
    # commit, and the planner also writes it explicitly, in that same commit.
    concernsWorkpiece = IRI("concernsWorkpiece", base=JMS_NS)
    hasOperation = IRI("hasOperation", base=JMS_NS)
    ScrewingResourceService = IRI("ScrewingResourceService", base=JMS_NS)
    UnscrewingWorkflow = IRI("UnscrewingWorkflow", base=JMS_NS)
    UnscrewingCapability = IRI("UnscrewingCapability", base=JMS_NS)
    ClassifyCapability = IRI("ClassifyCapability", base=JMS_NS)

    # The anomaly detector: its service, and its classify workflow, which realizes the
    # `ClassifyCapability`. Then the class of the tree's exits, and what the detector commits on
    # an unscrewing operation besides the parameter copy and the outcome (`hasDetectionResult`, below):
    # after a failure outcome, the measured end of the approach.
    AnomalyDetectorService = IRI("AnomalyDetectorService", base=JMS_NS)
    AnomalyDetector = IRI("AnomalyDetector", base=JMS_NS)
    ClassifyWorkflow = IRI("ClassifyWorkflow", base=JMS_NS)
    DetectionResult = IRI("DetectionResult", base=JMS_NS)
    hasMeasuredApproachEndPosition = IRI("hasMeasuredApproachEndPosition", base=JMS_NS)

    # An unscrewing operation's outcome, and the outcome of an attempt that passed
    hasDetectionResult = IRI("hasDetectionResult", base=JMS_NS)
    ScrewIntact = IRI("ScrewIntact", base=JMS_NS)

    # The learner: its resource class, its service class, and its one resource
    Learner = IRI("Learner", base=JMS_NS)
    LearnerService = IRI("LearnerService", base=JMS_NS)
    Learner1 = IRI("Learner1", base=JMS_NS)

    # The planner: its resource class, its service class, the workflow that opens the unscrewing
    # of one Screw, the capability that workflow realizes, and its one resource. "Start", because
    # `UnscrewingWorkflow` and `UnscrewingCapability` above are the screwing resource's: the
    # planner opens the work and dispatches to those.
    Planner = IRI("Planner", base=JMS_NS)
    PlannerService = IRI("PlannerService", base=JMS_NS)
    StartUnscrewingWorkflow = IRI("StartUnscrewingWorkflow", base=JMS_NS)
    StartUnscrewingCapability = IRI("StartUnscrewingCapability", base=JMS_NS)
    Planner1 = IRI("Planner1", base=JMS_NS)

    # The screwing resource's class, and its robot, whose controller speaks only MQTT. Each
    # parameter of the robot is a property of the robot's resource whose node names its topic and
    # its broker (`READINGS`, `SETPOINT_OF`, below).
    ScrewingResource = IRI("ScrewingResource", base=JMS_NS)
    Robot = IRI("Robot", base=JMS_NS)
    hasRobot = IRI("hasRobot", base=JMS_NS)
    isRobotOf = IRI("isRobotOf", base=JMS_NS)

    # The robot's readings: one value per sample of an attempt's curve
    hasRobotPosition = IRI("hasRobotPosition", base=JMS_NS)
    hasAxialForce = IRI("hasAxialForce", base=JMS_NS)
    hasUnscrewingTorque = IRI("hasUnscrewingTorque", base=JMS_NS)

    # The state of the robot's attempt, and the command that starts one
    hasAttemptState = IRI("hasAttemptState", base=JMS_NS)
    hasAttemptCommand = IRI("hasAttemptCommand", base=JMS_NS)

    # The robot's setpoints, one for each value of the Screw type that an attempt is given
    hasRobotMinApproachAxialForce = IRI("hasRobotMinApproachAxialForce", base=JMS_NS)
    hasRobotMinCounterTorque = IRI("hasRobotMinCounterTorque", base=JMS_NS)
    hasRobotMinPreloadAxialForce = IRI("hasRobotMinPreloadAxialForce", base=JMS_NS)
    hasRobotPreparationTurningAngle = IRI(
        "hasRobotPreparationTurningAngle", base=JMS_NS
    )
    hasRobotMaxLooseTorque = IRI("hasRobotMaxLooseTorque", base=JMS_NS)
    hasRobotMinLooseningAxialForce = IRI("hasRobotMinLooseningAxialForce", base=JMS_NS)
    hasRobotMaxDynamicTorque = IRI("hasRobotMaxDynamicTorque", base=JMS_NS)


class PROV:
    """The terms of PROV-O that use case 1 writes, declared in `ontology/integration_usecase.ttl`.

    One PROV witness, on the decision: the classification is an `Activity`, the
    anomaly detector's resource its agent, and the parameter copy the entity it generated.
    """

    Activity = IRI("Activity", base=PROV_NS)
    Entity = IRI("Entity", base=PROV_NS)
    Agent = IRI("Agent", base=PROV_NS)
    wasGeneratedBy = IRI("wasGeneratedBy", base=PROV_NS)
    generated = IRI("generated", base=PROV_NS)
    wasAssociatedWith = IRI("wasAssociatedWith", base=PROV_NS)
    wasAssociateFor = IRI("wasAssociateFor", base=PROV_NS)
    endedAtTime = IRI("endedAtTime", base=PROV_NS)


# Only a Screw type holds these (and a parameter copy records them); a Screw holds none.
TYPE_LEVEL: tuple[IRI, ...] = (
    JMS.hasMinApproachAxialForce,
    JMS.hasMinCounterTorque,
    JMS.hasMinPreloadAxialForce,
    JMS.hasPreparationTurningAngle,
    JMS.hasMaxLooseTorque,
    JMS.hasMinLooseningAxialForce,
    JMS.hasMaxDynamicTorque,
)

# Only a Screw holds these, because they depend on where the screw is, not on its type.
PLACEMENT: tuple[IRI, ...] = (
    JMS.hasMinApproachPosition,
    JMS.hasMinScrewHeadPosition,
    JMS.hasMaxScrewHeadPosition,
)

# The robot's readings, each the parameter of one signal of a frame.
READINGS: tuple[IRI, ...] = (
    JMS.hasRobotPosition,
    JMS.hasAxialForce,
    JMS.hasUnscrewingTorque,
)

# The robot's setpoint for each value of the Screw type, in the order of `TYPE_LEVEL`.
SETPOINT_OF: dict[IRI, IRI] = {
    JMS.hasMinApproachAxialForce: JMS.hasRobotMinApproachAxialForce,
    JMS.hasMinCounterTorque: JMS.hasRobotMinCounterTorque,
    JMS.hasMinPreloadAxialForce: JMS.hasRobotMinPreloadAxialForce,
    JMS.hasPreparationTurningAngle: JMS.hasRobotPreparationTurningAngle,
    JMS.hasMaxLooseTorque: JMS.hasRobotMaxLooseTorque,
    JMS.hasMinLooseningAxialForce: JMS.hasRobotMinLooseningAxialForce,
    JMS.hasMaxDynamicTorque: JMS.hasRobotMaxDynamicTorque,
}

# The robot's setpoints, in the order of `TYPE_LEVEL`.
SETPOINTS: tuple[IRI, ...] = tuple(SETPOINT_OF.values())

# Every parameter of the robot: its readings, the state of its attempt, the command that starts
# one, and its setpoints. Each is reached over MQTT.
ROBOT_PARAMETERS: tuple[IRI, ...] = (
    *READINGS,
    JMS.hasAttemptState,
    JMS.hasAttemptCommand,
    *SETPOINTS,
)

# The parameters a client may set: the start command and the setpoints. The others it only reads.
READWRITE: tuple[IRI, ...] = (JMS.hasAttemptCommand, *SETPOINTS)
