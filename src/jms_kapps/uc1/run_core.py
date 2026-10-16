"""The run core of use case 1: seed the demo, then start its four participants under one supervisor.

A run of use case 1 is one fixed set of four processes under one `Supervisor`: the anomaly
detector, the screwing resource, the learner and the planner (`participants`). `start_run` seeds
the dataset and starts them in that order. Use case 1 has no topology, so the run core names the
participants itself.

The order matters. A participant starts after the participants it calls: the screwing resource
calls the anomaly detector, and the planner dispatches to the screwing resource. The learner calls
nobody. The planner starts last also because it reads the seeded grinders at its start.

The seed clears the dataset, so the caller asks `refuse_if_live` first. Then the caller waits
for readiness, `Supervisor.wait_until_ready`, and in the end stops the participants,
`Supervisor.stop_all`, the ordered teardown.

The seed also writes the resources of the screwing resource and the anomaly detector, each with
its label, as use case 2's seed writes the resources of its run. The screwing resource is a plain
`cfc:Resource`; the anomaly detector is an `:AnomalyDetector`, a `cfc:Resource` that is
also a PROV agent, the agent of each classification (`SEEDED_RESOURCE_CLASSES`, jms_kapps 59). The
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
from jms_kapps.uc1.seed import reset_uc1, seed_demo
from jms_kapps.uc1.vocabulary import JMS

# The resources that the seed writes, each with its label: the participants that the ontology
# does not declare.
SEEDED_RESOURCES: dict[IRI, str] = {
    ANOMALY_DETECTOR_RESOURCE: "Anomaly detector",
    SCREWING_RESOURCE_RESOURCE: "Screwing resource",
}

# The class of each seeded resource that is not a plain `cfc:Resource`: the anomaly detector's is
# a PROV agent too, because it is the agent of each classification. (Added on 2026-10-16,
# jms_kapps 59.)
SEEDED_RESOURCE_CLASSES: dict[IRI, IRI] = {
    ANOMALY_DETECTOR_RESOURCE: JMS.AnomalyDetector,
}

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


class Participant(NamedTuple):
    """One process of a run: the resource it serves, what a person calls it, its command line,
    the classes of the workflows it registers, without which it is not ready, and its service
    class (None when the middleware does not register one)."""

    resource: IRI
    name: str
    command: list[str]
    workflows: tuple[IRI, ...] = ()
    service: IRI | None = None


def participants(
    dataset: str,
    *,
    learn_seconds: float = DEFAULT_LEARN_SECONDS,
    operation_timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Participant]:
    """The four participants of every run of use case 1, in the order they start.

    A participant comes after the participants it calls, and the planner comes last, because it
    reads the seeded grinders at its start. Each number goes on its command line as
    `str(float(x))`, after its flag.

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


def seed(db: TripleStore) -> None:
    """Clear `db`, load UC1's ontology with the seeded resources (`SEEDED_RESOURCES`), and write
    the demo: `seed.reset_uc1`, then `seed.seed_demo`."""
    reset_uc1(db, individuals=resources_turtle())
    seed_demo(OGM(db=db))


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
            )
    except Exception as error:
        supervisor.stop_all()
        raise LaunchFailed(str(error)) from error
    supervisor.watch()
