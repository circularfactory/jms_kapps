"""The planner of use case 1: it opens the unscrewing of one Screw and waits for it to end.

**It is the record-opening half of the paper's perception service.** The screwing resource is the
sensing half: it makes the attempt and records the curves. The planner classifies nothing, decides
no outcome and chooses no order — which Screw to take out is a press, as use case 2's warehouse
management system spawns one box per press.

**One press is one `mw.issue_operation()`.** The call finds a live service that realizes the
screwing resource's `:UnscrewingCapability`, creates the operation `queued` in one commit, and
notifies that service — in that order, so a notification that fails leaves nothing of the operation
in the graph. The planner looks up no address and calls no workflow itself.

**One press is one commit.** The operation names three things. `:concernsScrew` and `:concernsWorkpiece` are
the operation's own properties and go in `op.data`. `:hasOperation` belongs to the Screw, which
already exists, so the planner writes it with `op.update()`, the issuing call's update of an
existing instance. The operation, its `queued` status and the Screw's explicit back-link are one
write transaction: the store admits all of it or none of it, and a refusal leaves neither the
operation nor the back-link in the graph. The ontology also declares `:hasOperation` the
`owl:inverseOf` `:concernsScrew`, so a store whose ruleset infers inverses would hold the link anyway;
explicit and inferred give one value.

**A dispatch that finds no service is not a fault.** `issue_operation()` raises
`OperationResolutionError` before its block and writes nothing, so the Screw has no operation. The
planner records that and returns, because a Screw nobody can take must not raise at the caller.

**Nor is an operation that the store refuses.** Use case 1's shapes admit an unscrewing operation
only for a Screw whose Screw type holds each of the seven values the attempt needs. The store
checks the planner's commit, and a commit it refuses raises `ShaclValidationError` with the store's
validation report: nothing was written, and nobody was notified. The planner records the Screw as
refused, with the rules the report names, their messages and the report. Every other error is a
fault of this press and reaches the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kapps_semantic_middleware.errors import OperationResolutionError
from kapps_semantic_middleware.validation_report import readable_results, rule_names
from kapps_triplestore_interface import IRI, ShaclValidationError

from jms_kapps.uc1.planner_memory import DONE, PlannerMemory
from jms_kapps.uc1.vocabulary import JMS

# Why the entry of a Screw whose operation the store refused says it was refused.
STORE_REFUSED = "the triple store refused the unscrewing operation"

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
        live service realizes the capability or the store refused the operation. The Screw then
        has no operation, and nothing was written. The handle carries the Operation's IRI as `iri`.

    Raises:
        Any error of the issuing call other than `OperationResolutionError` and
        `ShaclValidationError`.
    """
    try:
        with middleware.issue_operation(
            capability_class=JMS.UnscrewingCapability,
            operation_class=JMS.UnscrewingOperation,
        ) as operation:
            # The Operation's own properties, as the middleware takes them: the IRI of the
            # property, and a list of values. One operation names one Screw and one workpiece.
            operation.data[str(JMS.concernsScrew)] = [{"id": str(screw)}]
            operation.data[str(JMS.concernsWorkpiece)] = [{"id": str(workpiece)}]
            # The Screw already exists, so its back-link is an update of an existing instance, in
            # the Operation's own commit: admitted or refused together with the Operation.
            operation.update(str(screw), {str(JMS.hasOperation): [operation.iri]})
    except OperationResolutionError as refusal:
        memory.refused(screw, why=str(refusal))
        return None
    except ShaclValidationError as refusal:
        memory.refused(
            screw,
            why=STORE_REFUSED,
            rules=tuple(rule_names(refusal.report)),
            messages=_messages(refusal.report),
            report=refusal.report,
        )
        return None

    memory.dispatched(screw, operation.iri)
    return operation


def _messages(report: str) -> tuple[str, ...]:
    """What the rules of `report` say, each message once, in the order the reader gives them."""
    messages: list[str] = []
    for result in readable_results(report) or []:
        if result.message is not None and result.message.short not in messages:
            messages.append(result.message.short)
    return tuple(messages)


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

    **Nothing here raises for the ends a press can have.** A dispatch that finds no live
    screwing resource, an operation the store refuses, an operation that ends `failed`, and a wait
    that passes its timeout each keep their entry in `memory`. Only an error that is none of these reaches the caller.

    Args:
        middleware: The planner's middleware instance.
        screw: The Screw to unscrew.
        workpiece: The product it sits in.
        memory: What the planner knows, which this writes to.
        timeout: How long to wait for the operation to end, in seconds. None waits without a
            limit, which a lost notification can make forever.
        read_outcome: Reads the outcome the detector wrote on an operation, at every end: an
            operation that ended `failed` holds one too, when the outcome is a failure outcome. Left
            out, nothing is read and no outcome is recorded.
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
    outcome = read_outcome(operation.iri) if read_outcome else None
    if status == DONE:
        memory.ended(screw, DONE, outcome=outcome)
    else:
        why = read_why(operation.iri) if read_why else None
        memory.ended(screw, status, outcome=outcome, why=why)
