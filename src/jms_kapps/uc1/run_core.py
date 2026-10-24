"""The run core of use case 1: seed the demo, then start its five participants under one supervisor.

A run of use case 1 is one fixed set of five processes under one `Supervisor`: the anomaly
detector, the screwing resource, the robot controller stand-in, the learner and the planner
(`participants`). `start_run` seeds the dataset and starts them in that order. Use case 1 has no
topology, so the run core names the participants itself.

The order matters. A participant starts after the participants it calls: the screwing resource
calls the anomaly detector, and the planner dispatches to the screwing resource. The robot
controller stand-in connects to the broker that the screwing resource's middleware hosts, so it
comes right after it; it is a device with no service in the graph, live while its process runs.
The learner calls nobody. The planner starts last also because it reads the seeded grinders at its
start.

The seed clears the dataset, so the caller asks `refuse_if_live` first. Then the caller waits
for readiness, `Supervisor.wait_until_ready`, and in the end stops the participants,
`Supervisor.stop_all`, the ordered teardown.

The seed also writes the resources of the screwing resource and the anomaly detector, each with
its label, as use case 2's seed writes the resources of its run. The screwing resource is a
`:ScrewingResource`, a `cfc:Resource` that has a robot, which `seed.seed_robot` writes; the
anomaly detector is an `:AnomalyDetector`, a `cfc:Resource` that is also a PROV agent, the agent
of each classification (`SEEDED_RESOURCE_CLASSES`). The
ontology declares the other two, `:Planner1` and `:Learner1`, already. Without its resource in
the graph, a participant's middleware cannot find the resource's class, and it serves no data
model for it.

Use case 1 has its own run core and imports nothing of use case 2, whose run core is
topology-specific.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import NamedTuple

from kapps_ogm import OGM
from kapps_semantic_middleware.vocabulary import CORE_NS
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.supervision import LaunchFailed, Supervisor
from jms_kapps.uc1.anomaly_detector import RESOURCE as ANOMALY_DETECTOR_RESOURCE
from jms_kapps.uc1.learner_process import DEFAULT_LEARN_SECONDS
from jms_kapps.uc1.planner import DEFAULT_TIMEOUT_SECONDS
from jms_kapps.uc1.screwing_resource import RESOURCE as SCREWING_RESOURCE_RESOURCE
from jms_kapps.uc1.seed import (
    MQTT_BROKER_IP,
    MQTT_BROKER_PORT,
    RECORDING_OF,
    ROBOT,
    reset_uc1,
    seed_demo,
    unmeasured_screw_type_turtle,
)
from jms_kapps.uc1.vocabulary import JMS

# The resources that the seed writes, each with its label: the participants that the ontology
# does not declare.
SEEDED_RESOURCES: dict[IRI, str] = {
    ANOMALY_DETECTOR_RESOURCE: "Anomaly detector",
    SCREWING_RESOURCE_RESOURCE: "Screwing resource",
}

# The class of each seeded resource that is not a plain `cfc:Resource`: the anomaly detector's is
# a PROV agent too, because it is the agent of each classification, and the screwing resource's
# has a robot. Each resource has this one class, because its middleware instance resolves the data
# model it serves from the one class the resource is written with.
SEEDED_RESOURCE_CLASSES: dict[IRI, IRI] = {
    ANOMALY_DETECTOR_RESOURCE: JMS.AnomalyDetector,
    SCREWING_RESOURCE_RESOURCE: JMS.ScrewingResource,
}

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


class Participant(NamedTuple):
    """One process of a run: the resource it serves, what a person calls it, its command line,
    the classes of the workflows it registers, without which it is not ready, its service
    class (None when the middleware does not register one), and whether it is a device, a
    process with no service in the graph at all (the robot controller stand-in), which is live
    while it runs."""

    resource: IRI
    name: str
    command: list[str]
    workflows: tuple[IRI, ...] = ()
    service: IRI | None = None
    device: bool = False


def participants(
    dataset: str,
    *,
    learn_seconds: float = DEFAULT_LEARN_SECONDS,
    operation_timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Participant]:
    """The five participants of every run of use case 1, in the order they start.

    A participant comes after the participants it calls, and the planner comes last, because it
    reads the seeded grinders at its start. The robot controller stand-in comes right after the
    screwing resource, whose middleware hosts the broker it connects to; it is a device, with the
    broker and the recording of each Screw (`seed.RECORDING_OF`) on its command line and no
    dataset, as the controller of a real cell would be configured. Each number goes on its
    command line as `str(float(x))`, after its flag.

    Args:
        dataset: The dataset of the triple store every process runs against.
        learn_seconds: The time from one run of the learner to the next, a positive number of seconds.
        operation_timeout: How long a press of the planner waits for its operation to end, a positive number of
            seconds.
    """
    return [
        Participant(
            ANOMALY_DETECTOR_RESOURCE,
            "Anomaly detector",
            [
                sys.executable,
                "-m",
                "jms_kapps.uc1.anomaly_detector",
                "--dataset",
                dataset,
            ],
            (JMS.ClassifyWorkflow,),
            JMS.AnomalyDetectorService,
        ),
        Participant(
            SCREWING_RESOURCE_RESOURCE,
            "Screwing resource",
            [
                sys.executable,
                "-m",
                "jms_kapps.uc1.screwing_resource",
                "--dataset",
                dataset,
            ],
            (JMS.UnscrewingWorkflow,),
            JMS.ScrewingResourceService,
        ),
        Participant(
            ROBOT,
            "Robot controller",
            [
                sys.executable,
                "-m",
                "jms_kapps.uc1.robot_controller",
                "--broker-host",
                MQTT_BROKER_IP,
                "--broker-port",
                str(MQTT_BROKER_PORT),
                *(
                    part
                    for screw, recording in RECORDING_OF.items()
                    for part in ("--recording", f"{screw}={recording}")
                ),
            ],
            device=True,
        ),
        Participant(
            JMS.Learner1,
            "Learner",
            [
                sys.executable,
                "-m",
                "jms_kapps.uc1.learner_process",
                "--dataset",
                dataset,
                "--learn-seconds",
                str(float(learn_seconds)),
            ],
            (),
            JMS.LearnerService,
        ),
        Participant(
            JMS.Planner1,
            "Planner",
            [
                sys.executable,
                "-m",
                "jms_kapps.uc1.planner_process",
                "--dataset",
                dataset,
                "--operation-timeout",
                str(float(operation_timeout)),
            ],
            (JMS.StartUnscrewingWorkflow,),
            JMS.PlannerService,
        ),
    ]


def seed(db: TripleStore, *, unmeasured_screw_type: bool = False) -> None:
    """Clear `db`, load UC1's ontology with the seeded resources (`SEEDED_RESOURCES`), and write
    the demo: `seed.reset_uc1`, then `seed.seed_demo`.

    With `unmeasured_screw_type`, the demo also holds a Screw type with none of the seven values,
    a Screw of it and its grinder (`seed.unmeasured_screw_type_turtle` and `seed.seed_demo`), as
    `jms-uc1 infeasible-operation` seeds it.
    """
    individuals = resources_turtle()
    if unmeasured_screw_type:
        individuals += unmeasured_screw_type_turtle()
    reset_uc1(db, individuals=individuals)
    seed_demo(OGM(db=db), unmeasured_screw_type=unmeasured_screw_type)


def resources_turtle() -> str:
    """The Turtle of `SEEDED_RESOURCES`: each one with its label, and of the class that
    `SEEDED_RESOURCE_CLASSES` gives it, else a `cfc:Resource`.

    Full IRIs and no prefixes, because the store keeps a prefix that an import declares.
    """
    resource_class = IRI("Resource", base=CORE_NS)
    return "".join(
        f"<{resource}> <{_RDF_TYPE}> <{SEEDED_RESOURCE_CLASSES.get(resource, resource_class)}> ; "
        f'<{_RDFS_LABEL}> "{label}"@en .\n'
        for resource, label in SEEDED_RESOURCES.items()
    )


def start_run(
    seed: Callable[[], None],
    supervisor: Supervisor,
    participants: Sequence[Participant],
) -> None:
    """Seed, then start each of `participants`, in their order, under `supervisor`, and watch them.

    The caller asks the live-instance guard first, because the seed clears the dataset, and
    makes `supervisor` the one its stops reach before this starts anything, so that a stop
    reaches every process this starts.

    Args:
        seed: Clears the dataset and writes the demo into it.
        supervisor: The supervisor every participant runs under.
        participants: The participants to start, in their order.

    Raises:
        LaunchFailed: If seeding or starting a process failed, naming why. Every process this
            started is stopped again then, and nothing runs.
    """
    try:
        seed()
        for participant in participants:
            supervisor.spawn(
                participant.resource,
                participant.name,
                participant.command,
                workflows=participant.workflows,
                has_service=not participant.device,
            )
    except Exception as error:
        supervisor.stop_all()
        raise LaunchFailed(str(error)) from error
    supervisor.watch()
