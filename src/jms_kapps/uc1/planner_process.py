"""The planner of use case 1 as its own process, behind its own middleware instance.

The process registers `:PlannerService` for the planner's resource, `:Planner1`, and serves its
middleware over REST. So the planner is a participant like the others: the graph names its service,
the service has a heartbeat, and it deregisters on the way out.

**It exposes one workflow, `unscrew`**, which realizes the planner's `:StartUnscrewingCapability`.
A POST names one Screw, and the planner opens one unscrewing operation for it. There is no product
and no rate: one call, one operation, as use case 2's `spawn_box` is one call, one box. A page and
a script press the same button.

**The press answers at once.** The wait for the operation's end goes on in a worker thread, so a
press holds no request open for as long as an attempt takes.

**The wait must not hold the event loop.** It waits with the blocking twin of the handle's wait,
and the loop of this very middleware is what takes the notification that ends that wait. A wait on
the loop would wait for itself forever. `asyncio.to_thread` keeps it off.

**The planner finds its grinders in the graph, with one SPARQL query**, because the object-graph
mapper cannot enumerate a class. Everything else — each grinder's Screws — it reads in
object-graph projections. It is no longer told what it works on; the graph is its source of truth.

**The offer comes from the graph, and the graph is its source of truth.** The offer lists every
Screw of the grinders that has no operation yet — a Screw that holds `:hasOperation` is no longer
offered, whatever the operation's status, and a press on it is refused. A press takes the Screw
from the offer at once, so a second quick press is refused too; when the operation ends, the offer
is read from the graph again, so a Screw whose operation ended has its back-link and is no longer
offered. A Screw seeded later is offered after the next `refresh` — a seed by `jms-uc1 seed` then
a restart of the planner — and the dashboard's box reads the offer at each of its refreshes: no
restart is needed for a Screw that gained an operation to leave the drop-down.

The process serves the middleware's state dashboard at `/dashboard`: its four boxes of the instance
in the tab "System", and the planner's own box in the tab "Planner" (`planner_dashboard`). That box
offers the Screws and shows how each one's operation ended, all from the planner's memory and its
current offer.

A notification that is not the planner's own feedback is logged and nothing is done with it: the
planner takes no Operation of anyone else. The handler also keeps the middleware from warning at
each start that the instance has neither a notification handler nor a callback. Feedback of an
Operation the planner issued wakes that Operation's handle and never runs a workflow, so this
handler does not stand in the way of a press.

Run it as `python -m jms_kapps.uc1.planner_process --dataset <name>`. It starts and serves as every
participant's process does, through `common.process`.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from kapps_ogm import OGM, Node
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_triplestore_interface import IRI
from pydantic import BaseModel

from jms_kapps.common.process import process_parser, run_process
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.planner import DEFAULT_TIMEOUT_SECONDS, unscrew_one
from jms_kapps.uc1.planner_dashboard import TAB, register_box
from jms_kapps.uc1.planner_memory import PlannerMemory
from jms_kapps.uc1.planner_reads import (
    ScrewOnProduct,
    read_failure_reason,
    read_offer,
    read_outcome,
)
from jms_kapps.uc1.vocabulary import JMS

# Named, not `__name__`: the process runs this module as `python -m`, where `__name__` is
# `__main__`. A logger of that name is not below `jms_kapps`, so its INFO lines would be
# dropped at the root's WARNING (`common.process` lets `jms_kapps` log from INFO up).
logger = logging.getLogger("jms_kapps.uc1.planner_process")


class UnscrewRequest(BaseModel):
    """The Screw to unscrew, by its IRI."""

    screw: str


class UnscrewResult(BaseModel):
    """What one press answers: whether it started, the Screw, and the product the operation names.
    `why` says why a press was refused."""

    started: bool
    screw: str
    product: str | None = None
    why: str | None = None


class Offer:
    """The Screws the planner offers now, read from the graph, and the products they sit in.

    The offer is what a press may take: every Screw of the grinders it finds that has no
    operation yet. It is read from the graph at start, and again after each press, so a Screw
    that gained an operation leaves the offer without a restart — and a grinder seeded later
    appears at the next refresh. A press takes a Screw at once, so a second quick press is
    refused.
    """

    def __init__(self, ogm: OGM) -> None:
        self._ogm = ogm
        self._lock = threading.Lock()
        self._offer = read_offer(ogm)
        self._product = {entry.screw: entry.product for entry in self._offer}

    def screws(self) -> list[ScrewOnProduct]:
        """The current offer, as a copy."""
        with self._lock:
            return list(self._offer)

    def product_of(self, screw: IRI) -> IRI | None:
        """The product of a Screw that is offered now, else None."""
        with self._lock:
            return self._product.get(screw)

    def taken(self, screw: IRI) -> None:
        """Remove `screw` from the offer at once: a press took it."""
        with self._lock:
            self._offer = [entry for entry in self._offer if entry.screw != screw]
            self._product.pop(screw, None)

    def refresh(self) -> None:
        """Read the offer from the graph again and replace the current one.

        A read that raises is logged as a warning and keeps the old offer: the planner goes on
        offering what it had rather than coming up empty.
        """
        try:
            offer = read_offer(self._ogm)
        except Exception:
            logger.warning(
                "Could not refresh the offer; the previous offer stays.", exc_info=True
            )
            return
        with self._lock:
            self._offer = offer
            self._product = {entry.screw: entry.product for entry in offer}


class Press:
    """One press: it opens the unscrewing of one Screw and waits for it on a worker thread.

    It knows the products from the offer, so a press names the product without reading the graph
    again. A Screw not offered is refused rather than guessed at; a Screw offered but taken by a
    previous press is refused too, because the press removed it at once.
    """

    def __init__(
        self,
        middleware: SemanticMiddleware,
        ogm: OGM,
        memory: PlannerMemory,
        offer: Offer,
        *,
        timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._middleware = middleware
        self._ogm = ogm
        self._memory = memory
        self._timeout = timeout
        self._offer = offer

    def start(self, screw: IRI) -> UnscrewResult:
        """Open the unscrewing of `screw`, and answer before it ends."""
        product = self._offer.product_of(screw)
        if product is None:
            logger.info(
                "Planner: a press of Unscrew asks for %s, which is not offered, so it is refused",
                screw.fragment,
            )
            return UnscrewResult(
                started=False,
                screw=str(screw),
                why=(
                    "this Screw is not offered: it has an operation already, or no "
                    "grinder contains it"
                ),
            )
        self._offer.taken(screw)
        logger.info(
            "Planner: a press of Unscrew opens the unscrewing of %s in %s",
            screw.fragment,
            product.fragment,
        )
        threading.Thread(
            target=self._drive,
            args=(screw, product),
            name="uc1-planner-press",
            daemon=True,
        ).start()
        return UnscrewResult(started=True, screw=str(screw), product=str(product))

    def _drive(self, screw: IRI, product: IRI) -> None:
        """One press, on its own thread. One that raises is logged: the process goes on serving,
        and its memory shows how far the press came. The offer is read from the graph again when
        the press ends, so it is the graph's again."""
        try:
            unscrew_one(
                self._middleware,
                screw,
                product,
                self._memory,
                timeout=self._timeout,
                read_outcome=lambda operation: read_outcome(self._ogm, operation),
                read_why=lambda operation: read_failure_reason(self._ogm, operation),
            )
        except Exception:
            logger.exception("The unscrewing of %s failed.", screw.fragment)
        finally:
            self._offer.refresh()


def build(
    ogm: OGM,
    host: str,
    port: int,
    *,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
) -> SemanticMiddleware:
    """The planner's middleware instance, with its workflow, its notification handler and its
    dashboard.

    Args:
        timeout: How long a press waits for its operation to end, in seconds.
    """
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(JMS.Planner1),
        service_class=str(JMS.PlannerService),
        ogm=ogm,
        host=host,
        port=port,
    )
    memory = PlannerMemory()
    offer = Offer(ogm)
    press = Press(middleware, ogm, memory, offer, timeout=timeout)

    def not_taken(operation: Node) -> None:
        logger.info(
            "The planner takes no Operation of another resource, and does nothing with %s",
            IRI(str(operation.id)).fragment,
        )

    middleware.on_notification(not_taken)

    @middleware.workflow(
        capability_class=JMS.StartUnscrewingCapability,
        workflow_class=JMS.StartUnscrewingWorkflow,
    )
    async def unscrew(request: UnscrewRequest) -> UnscrewResult:
        """Open the unscrewing of one Screw.

        It answers at once, before the attempt ends; the planner's page shows how it went.
        """
        return await asyncio.to_thread(press.start, IRI(request.screw))

    # It opens on the planner's own tab, where a person presses Unscrew.
    register_box(enable_dashboard(middleware, first_tab=TAB), memory, offer.screws)
    return middleware


def main() -> None:
    parser = process_parser("The planner of use case 1")
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Seconds a press waits for its unscrewing operation to end "
            f"(default: {DEFAULT_TIMEOUT_SECONDS:g})"
        ),
    )
    args = parser.parse_args()
    if not 0 < args.operation_timeout < float("inf"):
        parser.error("--operation-timeout must be a positive number")
    run_process(
        args.host,
        lambda port: build(
            OGM(db=connect(args.dataset)),
            args.host,
            port,
            timeout=args.operation_timeout,
        ),
        lambda address: (
            f"The planner runs on {address}. Its dashboard: {address}dashboard"
        ),
    )


if __name__ == "__main__":
    main()
