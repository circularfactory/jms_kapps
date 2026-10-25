"""The switch "Save measurements as uncertainty quantifications" of UC1's page in a headless
browser: it shows the anomaly detector's mode and sets it, through the page's passthrough.

The page is the real one, served by the page's real app. Behind its passthrough, the stand-in of
the detector serves the detector's real mode route over a real `DetectorMemory`, on a port of its
own, as a run serves it: both ends are real servers. Nothing reaches a store and no process runs.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _serving import serving
from _wait import eventually
from fastapi import FastAPI
from kapps_triplestore_interface import IRI
from playwright.sync_api import Page, expect

from jms_kapps.common.supervision import ChildSnapshot
from jms_kapps.uc1.anomaly_detector import mount_mode
from jms_kapps.uc1.anomaly_detector_memory import DetectorMemory
from jms_kapps.uc1.page import SAVE_UQ_LABEL, build_app

pytestmark = pytest.mark.browser


def _detector(address: str | None) -> ChildSnapshot:
    return ChildSnapshot(
        resource=IRI("http://example.org/test#AnomalyDetector1"),
        name="Anomaly detector",
        state="live",
        pid=1,
        command="python -m x",
        output=[],
        address=address,
        unconfirmed_for=None,
        stoppable=True,
        why_failed=None,
    )


@pytest.fixture
def memory() -> DetectorMemory:
    return DetectorMemory()


@pytest.fixture
def children(memory: DetectorMemory) -> Iterator[list[ChildSnapshot]]:
    detector = FastAPI()
    mount_mode(detector, memory)
    with serving(detector) as address:
        yield [_detector(address + "/")]


@pytest.fixture
def opened(page: Page, children: list[ChildSnapshot]) -> Iterator[Page]:
    with serving(build_app(lambda: list(children))) as address:
        page.goto(f"{address}/")
        yield page


def test_the_switch_shows_the_detectors_mode_and_turns_it_on_and_off(
    opened: Page, memory: DetectorMemory
) -> None:
    switch = opened.get_by_label(SAVE_UQ_LABEL)
    expect(switch).to_be_enabled(timeout=5000)
    expect(switch).not_to_be_checked()

    switch.check()

    eventually(lambda: memory.saves_uq)
    expect(switch).to_be_checked()
    expect(switch).to_be_enabled()

    switch.uncheck()

    eventually(lambda: not memory.saves_uq)
    expect(switch).not_to_be_checked()


def test_the_switch_follows_a_mode_set_elsewhere(opened: Page, memory: DetectorMemory) -> None:
    """The mode that the detector's start option set, or another page, shows at the next poll."""
    switch = opened.get_by_label(SAVE_UQ_LABEL)
    expect(switch).to_be_enabled(timeout=5000)

    memory.set_saves_uq(True)

    expect(switch).to_be_checked(timeout=5000)


def test_the_switch_stays_disabled_while_the_detector_has_no_address(
    opened: Page, children: list[ChildSnapshot], memory: DetectorMemory
) -> None:
    switch = opened.get_by_label(SAVE_UQ_LABEL)
    expect(switch).to_be_enabled(timeout=5000)
    address = children[0].address

    children[0] = _detector(None)

    expect(switch).to_be_disabled(timeout=5000)

    children[0] = _detector(address)

    expect(switch).to_be_enabled(timeout=5000)
    assert memory.saves_uq is False
