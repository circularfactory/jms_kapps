"""The terms of use case 1 that its Python reads and writes.

Each term is declared in `ontology/integration_usecase.ttl`. The ontology smoke test checks that
the file declares every term here, so the two cannot drift apart.
"""

from __future__ import annotations

from kapps_triplestore_interface import IRI

JMS_NS = "https://sfb1574.kit.edu/ontologies/JMS_Usecase_Demo#"
PROV_NS = "http://www.w3.org/ns/prov#"


class JMS:
    """Terms from `ontology/integration_usecase.ttl`."""

    # Classes
    Screw = IRI("Screw", base=JMS_NS)
    ScrewType = IRI("ScrewType", base=JMS_NS)
    DecisionSnapshot = IRI("DecisionSnapshot", base=JMS_NS)
    UnscrewingOperation = IRI("UnscrewingOperation", base=JMS_NS)
    # The product: a Core assembly, which names the Screws it contains, and a Core product, which
    # names its model. Both links are Core's, so this file names neither.
    AngleGrinder = IRI("AngleGrinder", base=JMS_NS)

    # Object properties
    hasScrewType = IRI("hasScrewType", base=JMS_NS)
    # The design, on the product's `cfc:ProductType`, beside `hasScrewType`, the record on the
    # Screw. Two properties, because a domain is an inference rule: `hasScrewType` on a product
    # type would make that product type a Screw.
    hasExpectedScrewType = IRI("hasExpectedScrewType", base=JMS_NS)
    usedValues = IRI("usedValues", base=JMS_NS)

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
    hasScrew = IRI("hasScrew", base=JMS_NS)
    # What the operation works on, and the Screw's link back to the operation. The pair
    # `hasOperation` is the `owl:inverseOf` `hasScrew`: the store infers it in the operation's own
    # commit, and the planner also writes it explicitly (Etienne, 2026-10-15).
    hasWorkpiece = IRI("hasWorkpiece", base=JMS_NS)
    hasOperation = IRI("hasOperation", base=JMS_NS)
    ScrewingResourceService = IRI("ScrewingResourceService", base=JMS_NS)
    UnscrewingWorkflow = IRI("UnscrewingWorkflow", base=JMS_NS)
    UnscrewingCapability = IRI("UnscrewingCapability", base=JMS_NS)
    ClassifyCapability = IRI("ClassifyCapability", base=JMS_NS)

    # The anomaly detector: its service, and its classify workflow, which realizes the
    # `ClassifyCapability`. Then the class of the tree's exits, and what the detector commits on
    # an unscrewing operation besides the decision snapshot and the outcome (`hasOutcome`, below):
    # after a failure outcome, the measured end of the approach.
    AnomalyDetectorService = IRI("AnomalyDetectorService", base=JMS_NS)
    AnomalyDetector = IRI("AnomalyDetector", base=JMS_NS)
    ClassifyWorkflow = IRI("ClassifyWorkflow", base=JMS_NS)
    UnscrewingOutcome = IRI("UnscrewingOutcome", base=JMS_NS)
    hasMeasuredApproachEndPosition = IRI("hasMeasuredApproachEndPosition", base=JMS_NS)

    # An unscrewing operation's outcome, and the outcome of an attempt that passed
    hasOutcome = IRI("hasOutcome", base=JMS_NS)
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


class PROV:
    """The terms of PROV-O that use case 1 writes, declared in `ontology/integration_usecase.ttl`.

    One PROV witness, on the decision (jms_kapps 59): the classification is an `Activity`, the
    anomaly detector's resource its agent, and the decision snapshot the entity it generated.
    """

    Activity = IRI("Activity", base=PROV_NS)
    Entity = IRI("Entity", base=PROV_NS)
    Agent = IRI("Agent", base=PROV_NS)
    wasGeneratedBy = IRI("wasGeneratedBy", base=PROV_NS)
    generated = IRI("generated", base=PROV_NS)
    wasAssociatedWith = IRI("wasAssociatedWith", base=PROV_NS)
    wasAssociateFor = IRI("wasAssociateFor", base=PROV_NS)
    endedAtTime = IRI("endedAtTime", base=PROV_NS)


# A Screw type holds these, and a Screw can override its type's value.
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
