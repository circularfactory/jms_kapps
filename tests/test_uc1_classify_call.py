"""The call of the anomaly detector's classify workflow, offline: what the caller learns of it.

The graph's lookup of the endpoint and the HTTP call are stand-ins, so each test sets what the
detector answers. `test_uc1_screwing_resource.py` makes the call on a live GraphDB.
"""

from __future__ import annotations

import httpx
import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.uc1 import classify_call
from jms_kapps.uc1.classify_call import (
    ClassifyAnswer,
    DetectorNotReached,
    NoDetector,
    call_detector,
)
from jms_kapps.uc1.vocabulary import JMS_NS

OPERATION = IRI("TestUnscrewingOperation", base=JMS_NS)
ENDPOINT = "http://127.0.0.1:9/workflows/classify/execute"


def answering(monkeypatch, status: int, body: object) -> None:
    """The detector at `ENDPOINT` answers `status` with `body` as JSON."""
    monkeypatch.setattr(classify_call, "classify_endpoint", lambda db: ENDPOINT)

    def post(url, *, json, timeout):
        request = httpx.Request("POST", url, json=json)
        return httpx.Response(status, json=body, request=request)

    monkeypatch.setattr(classify_call.httpx, "post", post)


def test_the_caller_learns_the_endpoint_and_what_the_detector_did(monkeypatch):
    answer = ClassifyAnswer(
        operation=str(OPERATION), ended="done", outcome=f"{JMS_NS}ScrewIntact"
    )
    answering(monkeypatch, 200, answer.model_dump())

    called = call_detector(None, OPERATION)

    assert called.endpoint == ENDPOINT
    assert called.answer == answer


def test_an_answer_of_another_shape_is_not_read(monkeypatch):
    answering(monkeypatch, 200, {"outcome": f"{JMS_NS}ScrewIntact"})

    called = call_detector(None, OPERATION)

    assert called.endpoint == ENDPOINT
    assert called.answer is None


def test_a_detector_that_refuses_the_call_names_its_endpoint(monkeypatch):
    answering(monkeypatch, 500, {"detail": "the store does not answer"})

    with pytest.raises(DetectorNotReached) as refused:
        call_detector(None, OPERATION)

    assert refused.value.endpoint == ENDPOINT


def test_no_detector_names_no_endpoint(monkeypatch):
    monkeypatch.setattr(classify_call, "classify_endpoint", lambda db: None)

    with pytest.raises(NoDetector) as refused:
        call_detector(None, OPERATION)

    assert refused.value.endpoint is None
