"""A stand-in of the store for the lists of UC1's page, Operations and Learned values: it answers
each of their two queries with fixed rows, as SPARQL's JSON results write them, and keeps every
query text it receives. `test_uc1_page.py` and `test_uc1_page_lists_browser.py` read the page's
lists through it, so nothing reaches a real store.
"""

from __future__ import annotations

from collections.abc import Sequence

from jms_kapps.uc1.page_lists import LEARNED_VALUES, OPERATIONS
from jms_kapps.uc1.vocabulary import JMS_NS


def _uri(value: str) -> dict:
    return {"type": "uri", "value": value}


def _text(value: str) -> dict:
    return {"type": "literal", "value": value}


class ListStore:
    """Answers the query of Operations with the two operations after both presses, Screw1's done
    and ScrewIntact and Screw2's failed and MissingScrew, each issued by `planner`, with
    `detector` as the deciding resource; and the query of Learned values with M4 at `torque`,
    which a test may change. While `away` holds a text, it raises that instead."""

    def __init__(self, detector: str, planner: str, torque: str = "1.8") -> None:
        self.detector = detector
        self.planner = planner
        self.torque = torque
        self.received: list[str] = []
        self.away: str | None = None

    def _answers(self, query: str) -> Sequence[dict]:
        if query == LEARNED_VALUES:
            return [{"screwType": _uri(f"{JMS_NS}M4"), "maxDynamicTorque": _text(self.torque)}]
        assert query == OPERATIONS, query
        return [
            {
                "operation": _uri(f"{JMS_NS}UnscrewingOperation_b1"),
                "screw": _uri(f"{JMS_NS}Screw1"),
                "issuer": _uri(self.planner),
                "status": _text("done"),
                "outcome": _uri(f"{JMS_NS}ScrewIntact"),
                "decidedBy": _uri(self.detector),
            },
            {
                "operation": _uri(f"{JMS_NS}UnscrewingOperation_a2"),
                "screw": _uri(f"{JMS_NS}Screw2"),
                "issuer": _uri(self.planner),
                "status": _text("failed"),
                "outcome": _uri(f"{JMS_NS}MissingScrew"),
                "reason": _text("ANOMALY: <MissingScrew>"),
                "decidedBy": _uri(self.detector),
            },
        ]

    def query(self, query: str, *, convert_bindings: bool = False) -> dict:
        self.received.append(query)
        if self.away is not None:
            raise ConnectionError(self.away)
        return {"results": {"bindings": list(self._answers(query))}}
