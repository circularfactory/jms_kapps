"""`_live_services`, the helper with which the running-system tests wait until no service is live
before they start `jms-uc2`, and show why a service is still live after a clean stop.

A service that one test left live made the next test's `jms-uc2` refuse to clear the dataset, so two
tests failed for one cause. Here a stand-in reader plays the store.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from _live_services import (
    TAIL_LINES,
    assert_no_service_is_live,
    live_after_the_stop,
    wait_until_no_service_is_live,
)
from _services import live_service
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service, ServiceReader

M2 = IRI("http://w3id.org/circularfactory/FlexConveyorInstances#Module2")
WMS = IRI(
    "http://w3id.org/circularfactory/FlexConveyorInstances#WarehouseManagementSystem1"
)
NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)


def service(resource: IRI, *, heartbeat: datetime | None = NOW) -> Service:
    """The live service of `resource`, with its heartbeat at `heartbeat`."""
    return replace(live_service(resource, "http://127.0.0.1:8102"), heartbeat=heartbeat)


def reading(*answers: list[Service]) -> ServiceReader:
    """A reader that gives each of `answers` in turn, and the last one from then on."""
    rest = list(answers)

    def read(workflows):
        return rest.pop(0) if len(rest) > 1 else rest[0]

    return read


def test_no_live_service_returns_at_once():
    asked = []

    def read(workflows):
        asked.append(workflows)
        return []

    wait_until_no_service_is_live(read, timeout=0.0, interval=10.0)
    assert len(asked) == 1


def test_services_that_stop_being_live_end_the_wait():
    read = reading([service(M2)], [service(M2)], [])
    wait_until_no_service_is_live(read, timeout=5.0, interval=0.01)


def test_services_still_live_at_the_bound_fail_with_their_names():
    read = reading([service(M2), service(WMS)])
    with pytest.raises(pytest.fail.Exception) as failed:
        wait_until_no_service_is_live(read, timeout=0.05, interval=0.01)
    message = str(failed.value)
    assert str(M2) in message and str(WMS) in message
    assert "outside this test" in message
    assert "killed less than 90 s ago" in message


def test_the_evidence_names_address_age_and_the_childs_last_lines():
    echoed = [f"Module2  line {n}" for n in range(40)] + [
        "Warehouse Management System  not this child's",
        "Module2  ERROR:jms_kapps.uc2.process:deregistration failed",
    ]
    message = live_after_the_stop(
        [service(M2, heartbeat=NOW - timedelta(seconds=12))],
        echoed,
        {M2: "Module2"},
        NOW,
    )
    assert str(M2) in message
    assert "http://127.0.0.1:8102" in message
    assert "heartbeat 12 s old" in message
    shown = message.split("lines:\n", 1)[1].splitlines()
    assert len(shown) == TAIL_LINES
    assert shown[-1] == "ERROR:jms_kapps.uc2.process:deregistration failed"
    assert "not this child's" not in message


def test_a_heartbeat_without_an_offset_counts_as_utc():
    naive = (NOW - timedelta(seconds=5)).replace(tzinfo=None)
    message = live_after_the_stop([service(M2, heartbeat=naive)], [], {}, NOW)
    assert "heartbeat 5 s old" in message
    # A resource the run never named shows by its fragment.
    assert "(Module2)" in message
    assert "(no line echoed)" in message


def test_the_check_after_the_stop_reads_once_and_fails_with_the_evidence():
    asked = []

    def read(workflows):
        asked.append(workflows)
        return [service(M2)]

    with pytest.raises(AssertionError) as failed:
        assert_no_service_is_live(read, ["Module2  stopping"], {M2: "Module2"})
    assert len(asked) == 1
    assert "stopping" in str(failed.value)
    assert_no_service_is_live(lambda workflows: [], [], {})
