"""What a running-system test reads of the services in its dataset: a wait until none is live
before it starts `jms-uc2`, and the evidence for a service still live after its own clean stop.

A service is live while it has an address and a heartbeat younger than
`MAX_HEARTBEAT_AGE_SECONDS`. A test whose run was killed, or that a process outside the test
serves, leaves a service live that long, and the next `jms-uc2` refuses to clear the dataset. So
the next test waits it out, and fails before its start, with the names, if it does not end: a
leftover then fails only the test that left it.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

import pytest
from _uc2_run import lines_of
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import MAX_HEARTBEAT_AGE_SECONDS, Service, ServiceReader

# One heartbeat write in flight and one poll, beyond the liveness age.
QUIET_MARGIN_SECONDS = 20.0
QUIET_WITHIN_SECONDS = MAX_HEARTBEAT_AGE_SECONDS + QUIET_MARGIN_SECONDS

# How many of a child's last echoed lines the evidence shows.
TAIL_LINES = 30


def wait_until_no_service_is_live(
    read: ServiceReader,
    *,
    timeout: float = QUIET_WITHIN_SECONDS,
    interval: float = 2.0,
) -> None:
    """Return once `read` finds no live service, and fail the test, naming each live resource,
    if it still finds one after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while live := list(read(())):
        if time.monotonic() > deadline:
            resources = ", ".join(sorted(str(s.resource) for s in live))
            pytest.fail(
                f"still live after {timeout:.0f} s, before jms-uc2 starts: {resources}. A "
                "process outside this test serves it, or it was killed less than "
                f"{MAX_HEARTBEAT_AGE_SECONDS:.0f} s ago."
            )
        time.sleep(interval)


def heartbeat_age(service: Service, now: datetime) -> float | None:
    """How many seconds old the service's heartbeat is at `now`, read as UTC where it carries no
    offset; None before its first heartbeat."""
    if service.heartbeat is None:
        return None
    beat = service.heartbeat
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=UTC)
    return (now - beat).total_seconds()


def live_after_the_stop(
    live: Iterable[Service],
    echoed: list[str],
    names: Mapping[IRI, str],
    now: datetime,
) -> str:
    """Why a check after a clean stop failed: each live resource with its address, its heartbeat's
    age, and the last lines the run echoed for its child, which `names` names by resource."""
    parts = []
    for service in live:
        name = names.get(service.resource, service.resource.fragment)
        age = heartbeat_age(service, now)
        shown_age = "no heartbeat" if age is None else f"heartbeat {age:.0f} s old"
        tail = "\n".join(lines_of(echoed, name)[-TAIL_LINES:]) or "(no line echoed)"
        parts.append(
            f"{service.resource} ({name}) is live after the clean stop: address "
            f"{service.address}, {shown_age}. Its last {TAIL_LINES} lines:\n{tail}"
        )
    return "\n\n".join(parts)


def assert_no_service_is_live(
    read: ServiceReader, echoed: list[str], names: Mapping[IRI, str]
) -> None:
    """One read, at once: no service is live. On failure, the evidence of `live_after_the_stop`."""
    live = list(read(()))
    assert live == [], live_after_the_stop(live, echoed, names, datetime.now(UTC))
