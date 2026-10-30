"""Use case 1's parties as middleware instances, served in the test's own process, on a live
GraphDB.

`served` binds a port, builds a party's middleware for it, and serves it on a thread of its own
until the block ends, with the serve of `_participants.py`, which serves the parties of use case
2. So the notification of an operation, and the call of a workflow, go over REST, and every write
goes through the middleware into the store.

**The anomaly detector is a fake** where a test of the screwing resource needs to control it.
`FakeDetector` realizes the detector's Classify capability, as the real one does
(`jms_kapps.uc1.anomaly_detector`), so the screwing resource finds it in the graph. It keeps the
operation of each call, answers with a fixed detection result, and writes nothing: it never ends
an operation. A test that needs a detector which refuses the call, answers late, or ends the
operation itself gives it what to do on each call. The detector's own tests serve the real one.

**The planner is a stand-in too**: a middleware that is never served, and dispatches an operation
with `mw.issue_operation()`, as the planner does.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from _participants import HOST, STARTED_WITHIN_SECONDS, _serve
from kapps_ogm import OGM
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.vocabulary import CFC, SVC
from kapps_triplestore_interface import IRI
from pydantic import BaseModel

from jms_kapps.common.process import bind_socket
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.classify_call import ClassifyRequest
from jms_kapps.uc1.seed import M4_VALUES
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

# The parties of these tests. None is declared in the ontology: a seed writes each as a
# `cfc:Resource`.
SCREWING_RESOURCE = IRI("TestScrewingResource", base=JMS_NS)
DETECTOR = IRI("TestAnomalyDetector", base=JMS_NS)
PLANNER = IRI("TestPlanner", base=JMS_NS)


@contextmanager
def served(build: Callable[[int], SemanticMiddleware]) -> Iterator[SemanticMiddleware]:
    """Bind a free port, build the party's middleware for it, and serve it until the block ends.

    The party has registered its service and its workflows in the graph when the block starts,
    and has deregistered them when it ends.
    """
    sock = bind_socket(HOST)
    party = _serve(build(sock.getsockname()[1]), sock)
    try:
        deadline = time.monotonic() + STARTED_WITHIN_SECONDS
        while not party.server.started:
            assert party.thread.is_alive(), "the party stopped while it started"
            assert time.monotonic() < deadline, "the party did not start in time"
            time.sleep(0.05)
        yield party.middleware
    finally:
        party.server.should_exit = True
        party.thread.join(timeout=30)
        sock.close()


class FixedOutcome(BaseModel):
    """The fake detector's answer to every call: the same detection result, whatever the
    curves."""

    outcome: str = f"{JMS_NS}ScrewIntact"


OnCall = Callable[[SemanticMiddleware, IRI], None]
"""What a fake detector does with a call, after it kept the operation and before it answers:
given its own middleware and the operation. It may raise, so that the detector refuses the call,
block, so that it answers late, or commit, so that it ends the operation."""


class FakeDetector:
    """A stand-in for the anomaly detector: it realizes the Classify capability, keeps the
    operation of each call in `calls`, runs `on_call` if it has one, and answers `FixedOutcome`.
    Without `on_call` it writes nothing."""

    def __init__(self, dataset: str, *, on_call: OnCall | None = None) -> None:
        self.dataset = dataset
        self.on_call = on_call
        self.calls: list[str] = []

    def build(self, port: int) -> SemanticMiddleware:
        middleware = SemanticMiddleware(
            mode=Mode.RESOURCE,
            resource_iri=str(DETECTOR),
            service_class=str(SVC.Service),
            ogm=OGM(db=connect(self.dataset)),
            host=HOST,
            port=port,
        )

        @middleware.workflow(
            capability_class=JMS.ClassifyCapability, workflow_class=SVC.Workflow
        )
        def classify(request: ClassifyRequest) -> FixedOutcome:
            """Keep the call, do what the test asks, and answer the fixed detection result."""
            self.calls.append(request.operation)
            if self.on_call is not None:
                self.on_call(middleware, IRI(request.operation))
            return FixedOutcome()

        return middleware


def planner(dataset: str) -> SemanticMiddleware:
    """The planner's middleware, never served: `mw.issue_operation()` needs only its graph."""
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(PLANNER),
        service_class=str(SVC.Service),
        ogm=OGM(db=connect(dataset)),
    )


def dispatch(planner: SemanticMiddleware, screw: IRI) -> IRI:
    """Dispatch an unscrewing operation of `screw`, as the planner does: `mw.issue_operation()`
    creates it `queued` and notifies the resource whose unscrewing workflow realizes the
    capability."""
    with planner.issue_operation(
        capability_class=JMS.UnscrewingCapability,
        operation_class=JMS.UnscrewingOperation,
    ) as operation:
        operation.data[str(JMS.concernsScrew)] = [{"id": str(screw)}]
    return IRI(str(operation.iri))


def seed_parties(screws: list[IRI]) -> str:
    """The Turtle of the setup: the three parties as Core Resources, and each of `screws` a
    `:Screw` of the Screw type `:M4`. A demonstration seed writes it directly, before any party
    starts."""
    resources = "".join(
        f"<{party}> a <{CFC.Resource}> .\n"
        for party in (SCREWING_RESOURCE, DETECTOR, PLANNER)
    )
    return resources + "".join(
        f"<{screw}> a <{JMS.Screw}> ; <{JMS.hasScrewType}> <{JMS.M4}> .\n"
        for screw in screws
    )


def seed_screw_type_values(db) -> None:
    """Gives the Screw type `:M4` its seven type values, in one commit. An attempt needs them: the
    screwing resource ends an operation `failed` before the attempt when its Screw type holds none."""
    OGM(db=db).commit(
        instance_iri=JMS.M4,
        data={str(prop): [value] for prop, value in M4_VALUES.items()},
    )
