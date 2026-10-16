"""The call of the anomaly detector's classify workflow, which the screwing resource makes after
each attempt.

The detector is found in the graph by its Classify capability: the endpoint of the workflow that
realizes a `:ClassifyCapability`, on a live service. A service is live while it has an address and
a heartbeat younger than `MAX_HEARTBEAT_AGE_SECONDS`, judged against this reader's clock, so a
detector that was killed before it deregistered is not called. Of several live ones, the one with
the newest heartbeat is called.

The call names the unscrewing operation and nothing else, as `ClassifyRequest`: the detector reads
the rest from the graph. It is a call inside the cell, and the caller needs no result from it,
because the detector ends the operation. The detector's classify workflow takes `ClassifyRequest`.
"""

from __future__ import annotations

import httpx
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI
from pydantic import BaseModel

from jms_kapps.common.services import live_pattern
from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc1.vocabulary import JMS

# How long the call may take. The call waits for the detector's answer, which may come only once
# it has classified the attempt.
CALL_TIMEOUT_SECONDS = 30.0


class ClassifyRequest(BaseModel):
    """What the classify workflow is called with: the IRI of one unscrewing operation."""

    operation: str


class DetectorNotReached(Exception):
    """The anomaly detector did not take the call. The message says why, for a person to read:
    the caller writes it into the operation's result."""


class NoDetector(DetectorNotReached, LookupError):
    """The graph names no live service whose workflow realizes a Classify capability."""


def classify_endpoint(db: Queryable) -> str | None:
    """The endpoint of the workflow that realizes a Classify capability on a live service, or
    None when the graph names none. The service is found by the workflow, not by its resource, so
    the read takes what counts as live from the one reader of a service (`live_pattern`)."""
    rows = bindings(
        db,
        f"""
        SELECT ?endpoint WHERE {{
            ?capability a <{JMS.ClassifyCapability}> ;
                        <{SVC.realizedByWorkflow}> ?workflow .
            ?workflow <{SVC.isWorkflowOf}> ?service ;
                      <{SVC.endpoint}> ?endpoint .
            {live_pattern("service")}
        }} ORDER BY DESC(?heartbeat) ?service LIMIT 1
        """,
    )
    return str(rows[0]["endpoint"]) if rows else None


def call_detector(db: Queryable, operation: IRI) -> None:
    """Call the live anomaly detector's classify workflow with `operation`, and wait until it
    answers. Its answer is not read.

    Raises:
        NoDetector: If the graph names no live detector.
        DetectorNotReached: If the call fails, takes longer than `CALL_TIMEOUT_SECONDS`, or the
            detector refuses it. A detector that took the call may still end the operation.
    """
    endpoint = classify_endpoint(db)
    if endpoint is None:
        raise NoDetector(
            "the graph names no live service whose workflow realizes a "
            f"{JMS.ClassifyCapability.fragment}"
        )
    try:
        httpx.post(
            endpoint,
            json=ClassifyRequest(operation=str(operation)).model_dump(),
            timeout=CALL_TIMEOUT_SECONDS,
        ).raise_for_status()
    except httpx.TimeoutException as error:
        raise DetectorNotReached(
            f"its classify workflow at {endpoint} did not answer within "
            f"{CALL_TIMEOUT_SECONDS:g} s"
        ) from error
    except httpx.HTTPStatusError as error:
        answer = error.response
        raise DetectorNotReached(
            f"its classify workflow at {endpoint} answered {answer.status_code} "
            f"{answer.reason_phrase}"
        ) from error
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise DetectorNotReached(
            f"the call of its classify workflow at {endpoint} failed: {error!r}"
        ) from error
