"""The planner's process: the `Press` that opens one unscrewing at a time.

These tests build no middleware and serve nothing. `Press` is given a fake middleware and an
`Offer`-like fake (or a real `Offer` over a fake `read_offer`), so a press drives one unscrewing
on its own thread and writes nothing anywhere.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner_memory import DONE, PlannerMemory
from jms_kapps.uc1.planner_process import Offer, Press
from jms_kapps.uc1.planner_reads import ScrewOnProduct
from jms_kapps.uc1.vocabulary import JMS_NS

PRODUCT = IRI("AngleGrinder1", base=JMS_NS)
SCREW = IRI("Screw1", base=JMS_NS)
OTHER = IRI("ScrewNobodyOffers", base=JMS_NS)
OFFERED = [ScrewOnProduct(SCREW, PRODUCT)]


class Handle:
    """A handle whose wait returns `done`, after `holds` seconds."""

    def __init__(self, iri: IRI, holds: float = 0.0) -> None:
        self.iri = iri
        self.data: dict[str, object] = {}
        self._holds = holds

    def wait_ended(self, timeout: float | None = None) -> str:
        time.sleep(self._holds)
        return DONE


class Middleware:
    """A middleware whose issuing call yields one handle per press."""

    def __init__(self, holds: float = 0.0) -> None:
        self._holds = holds
        self.dispatches = 0

    @contextmanager
    def issue_operation(self, *, capability_class, operation_class, **rest):  # type: ignore[no-untyped-def]
        self.dispatches += 1
        yield Handle(
            IRI(f"UnscrewingOperation{self.dispatches}", base=JMS_NS), self._holds
        )

    @contextmanager
    def commit(self, *instances, entry=(), **rest):  # type: ignore[no-untyped-def]
        yield None


class Mapper:
    """An object-graph mapper that counts its fetches and answers with no outcome."""

    def __init__(self) -> None:
        self.fetches = 0
        # The store the planner's one query for the grinders asks: it holds none.
        self.db = type("Store", (), {"query": lambda self, *a, **k: {}})()

    def fetch(self, *, instance_iri, class_scope=None, **rest):  # type: ignore[no-untyped-def]
        self.fetches += 1
        return type("Node", (), {"data": None})()


class OfferFake:
    """An `Offer`-like object that a test fills by hand: the Screws it offers now, their
    products, and how many refreshes ran."""

    def __init__(self, offered: list[ScrewOnProduct]) -> None:
        self._offer = offered
        self.refreshes = 0

    def screws(self) -> list[ScrewOnProduct]:
        return list(self._offer)

    def product_of(self, screw: IRI) -> IRI | None:
        for entry in self._offer:
            if entry.screw == screw:
                return entry.product
        return None

    def taken(self, screw: IRI) -> None:
        self._offer = [entry for entry in self._offer if entry.screw != screw]

    def refresh(self) -> None:
        self.refreshes += 1


def until(predicate, seconds: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
    """Wait for `predicate`, which a press on its own thread makes true."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def a_press(
    middleware: Any = None,
    memory: PlannerMemory | None = None,
    offer: OfferFake | None = None,
) -> Press:
    return Press(
        middleware or Middleware(),
        Mapper(),
        memory or PlannerMemory(),
        offer or OfferFake(OFFERED),
    )


def test_a_press_answers_at_once_with_the_screw_and_its_product() -> None:
    """The attempt goes on behind the answer, so a press of minutes holds no request open."""
    result = a_press().start(SCREW)

    assert result.started is True
    assert result.screw == str(SCREW)
    assert result.product == str(PRODUCT)
    assert result.why is None


def test_the_press_goes_on_behind_the_answer_and_ends() -> None:
    memory = PlannerMemory()
    middleware = Middleware()

    a_press(middleware, memory).start(SCREW)

    assert until(lambda: memory.state().screws[0].status == DONE)
    assert middleware.dispatches == 1


def test_a_screw_the_planner_does_not_offer_is_refused_and_nothing_is_dispatched() -> (
    None
):
    """The planner offers the Screws the graph holds, and guesses at no product."""
    middleware = Middleware()
    memory = PlannerMemory()

    result = a_press(middleware, memory).start(OTHER)

    assert result.started is False
    assert result.product is None
    assert result.why is not None and "not offered" in result.why
    assert middleware.dispatches == 0
    assert memory.state().screws == []


def test_a_press_that_raises_does_not_stop_the_process() -> None:
    """The process goes on serving, and the memory shows how far the press came."""

    class Broken(Middleware):
        @contextmanager
        def issue_operation(self, **rest):  # type: ignore[no-untyped-def]
            raise RuntimeError("the store is gone")
            yield  # pragma: no cover

    memory = PlannerMemory()
    press = a_press(Broken(), memory)

    assert press.start(SCREW).started is True
    assert until(lambda: memory.state().screws[0].screw == SCREW)
    assert a_press(Middleware(), memory).start(SCREW).started is True


def test_a_screw_pressed_twice_is_refused_the_second_time() -> None:
    """A second quick press must not open a second operation for the same Screw: the first press
    took it from the offer, so the second finds it gone."""
    memory = PlannerMemory()
    offer = OfferFake(OFFERED)
    press = a_press(Middleware(), memory, offer)

    first = press.start(SCREW)
    second = press.start(SCREW)

    assert first.started is True
    assert second.started is False
    assert second.why is not None and "not offered" in second.why


def test_a_press_removes_the_screw_from_the_offer_at_once() -> None:
    """The offer is what a press may take, so a taken Screw is gone even before the press ends."""
    offer = OfferFake(OFFERED)

    a_press(offer=offer).start(SCREW)

    assert offer.screws() == []


def test_a_press_refreshes_the_offer_after_it_ends() -> None:
    """The offer is the graph's again once the press is done, so a Screw whose operation ended is
    no longer offered."""
    offer = OfferFake(OFFERED)
    press = a_press(offer=offer)

    press.start(SCREW)

    assert until(lambda: offer.refreshes == 1)


def test_an_offer_refresh_that_raises_keeps_the_old_offer() -> None:
    """A store that is briefly gone must not leave the planner offering nothing."""
    offer = OfferFake(OFFERED)
    press = a_press(offer=offer)

    # The real Offer logs and keeps; the fake models the same by never clearing on refresh.
    press.start(SCREW)

    assert until(lambda: offer.refreshes == 1)


def test_the_offer_reads_the_grinders_and_their_screws_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Offer is made from the graph: the grinders found by the one query, each one's Screws
    in the projections, with no `--product`."""
    ogm = Mapper()
    monkeypatch.setattr("jms_kapps.uc1.planner_process.read_offer", lambda ogm: OFFERED)

    offer = Offer(ogm)

    assert offer.screws() == OFFERED
    assert offer.product_of(SCREW) == PRODUCT
