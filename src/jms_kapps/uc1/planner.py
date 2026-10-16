"""The planner of use case 1: it opens the unscrewing of one Screw and waits for it to end.

**It is the record-opening half of the paper's perception service.** The screwing resource is the
sensing half: it makes the attempt and records the curves. The planner classifies nothing, decides
no outcome and chooses no order — which Screw to take out is a press, as use case 2's warehouse
management system spawns one box per press.

**One press is one `mw.issue_operation()`.** The call finds a live service that realizes the
screwing resource's `:UnscrewingCapability`, creates the operation `queued` in one commit, and
notifies that service — in that order, so a notification that fails leaves nothing of the operation
in the graph. The planner looks up no address and calls no workflow itself.

**The operation names three things**, and only two of them fit in that one commit. `:hasScrew` and
`:hasWorkpiece` are the operation's own properties and go in `op.data`. `:hasOperation` belongs to
the Screw, which already exists, and `issue_operation()` joins only creations nested in its block,
not an update of another instance. The ontology declares `:hasOperation` the `owl:inverseOf`
`:hasScrew`, so the store infers the back-link inside the operation's own commit, and the pair is
never one-sided. The planner also writes it explicitly, in a second commit right after;
explicit and inferred give one value. If that commit is refused, the inferred link stands, so the
planner logs a warning and goes on rather than raising. An issuing call that accepts changes of
its own, which kapps_ogm can already write (`OGM.commit(changes=[Create, Update])`), is
kapps_semantic_middleware 480.

**A dispatch that finds no service is not a fault.** `issue_operation()` raises
`OperationResolutionError` before its block and writes nothing, so the Screw has no operation. The
planner records that and returns, because a Screw nobody can take must not raise at the caller.
Every other error is a fault of this press and reaches the caller.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from kapps_ogm import Update
from kapps_semantic_middleware.errors import OperationResolutionError
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner_memory import DONE, PlannerMemory
from jms_kapps.uc1.vocabulary import JMS

logger = logging.getLogger(__name__)

# How long one operation may take before the planner stops waiting for it. A lost notification
# leaves a wait without a limit open forever, so every press passes one.
DEFAULT_TIMEOUT_SECONDS = 300.0


def dispatch(
    middleware: Any, screw: IRI, workpiece: IRI, memory: PlannerMemory
) -> Any | None:
    """Issue one unscrewing operation for `screw`, and record it in `memory`.

    Args:
        middleware: The planner's middleware instance.
        screw: The one physical Screw the operation removes.
        workpiece: The product the Screw sits in, which the operation names.
        memory: What the planner knows, which this writes to.

    Returns:
        The handle of the issued Operation, on which the caller waits for its end, or None when no
        live service realizes the capability. The Screw then has no operation, and nothing was
        written. The handle carries the Operation's IRI as `iri`.

    Raises:
        Any error of the issuing call other than `OperationResolutionError`.
    """
    try:
        with middleware.issue_operation(
            capability_class=JMS.UnscrewingCapability,
            operation_class=JMS.UnscrewingOperation,
        ) as operation:
            # The Operation's own properties, as the middleware takes them: the IRI of the
            # property, and a list of values. One operation names one Screw and one workpiece.
            operation.data[str(JMS.hasScrew)] = [{"id": str(screw)}]
            operation.data[str(JMS.hasWorkpiece)] = [{"id": str(workpiece)}]
    except OperationResolutionError as refusal:
        memory.refused(screw, why=str(refusal))
        return None

    _write_back_link(middleware, screw, operation.iri)
    memory.dispatched(screw, operation.iri)
    return operation


def _write_back_link(middleware: Any, screw: IRI, operation: IRI) -> None:
    """Write the Screw's `:hasOperation`, the counterpart of the operation's `:hasScrew`.

    The store already inferred it from `:hasScrew` in the operation's own commit, because the
    ontology declares the pair `owl:inverseOf`; this writes it explicitly too. A refusal here is
    logged and does not raise: the inferred link stands, and the operation is under way.
    """
    try:
        with middleware.commit(
            entry=[
                Update(
                    instance_iri=screw,
                    data={str(JMS.hasOperation): [{"id": str(operation)}]},
                )
            ]
        ):
            pass
    except Exception as refusal:  # noqa: BLE001 - the press goes on, whatever the store said
        logger.warning(
            "%s holds no back-link to %s: %s. The operation is in the graph and under way.",
            screw,
            operation,
            refusal,
        )


def unscrew_one(
    middleware: Any,
    screw: IRI,
    workpiece: IRI,
    memory: PlannerMemory,
    *,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    read_outcome: Callable[[IRI], str | None] | None = None,
    read_why: Callable[[IRI], str | None] | None = None,
) -> None:
    """Open the unscrewing of `screw` and wait for that operation to end.

    **It blocks its thread** and must therefore not run on the event loop of the planner's own
    middleware: that loop has to take the notification which ends the wait. The planner's process
    runs it in a worker thread.

    **Nothing here raises for the three ends the ticket names.** A dispatch that finds no live
    screwing resource, an operation that ends `failed`, and a wait that passes its timeout each
    keep their entry in `memory`. Only an error that is none of these reaches the caller.

    Args:
        middleware: The planner's middleware instance.
        screw: The Screw to unscrew.
        workpiece: The product it sits in.
        memory: What the planner knows, which this writes to.
        timeout: How long to wait for the operation to end, in seconds. None waits without a
            limit, which a lost notification can make forever.
        read_outcome: Reads the outcome the detector wrote on an operation that ended `done`.
            Left out, nothing is read and no outcome is recorded.
        read_why: Reads why an operation ended `failed`. Left out, nothing is read and the
            failure is recorded without a reason.
    """
    memory.pressed(screw)
    operation = dispatch(middleware, screw, workpiece, memory)
    if operation is None:
        # No live service realizes the capability. The refusal is already recorded.
        return
    try:
        status = operation.wait_ended(timeout=timeout)
    except TimeoutError as expiry:
        memory.timed_out(screw, why=str(expiry))
        return
    if status == DONE:
        outcome = read_outcome(operation.iri) if read_outcome else None
        memory.ended(screw, DONE, outcome=outcome)
    else:
        why = read_why(operation.iri) if read_why else None
        memory.ended(screw, status, why=why)
