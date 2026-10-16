"""A test that needs a live store is also a `live` test, so that `-m "not live"` is the offline tier.

The release mechanism runs a repository's own offline tier as `pytest -m "not live"`, as the
three libraries' CI does. Here a test that needs a live service is marked `graphdb` or
`influxdb`, so each of them is also `live`, and the offline tier deselects them.

Each case is a small pytest session in this process (pytest's own `pytester`), with the plugin
module of the marker loaded. No test of the sessions connects to anything.
"""

from __future__ import annotations

import pytest

SESSION_TESTS = """
import pytest


@pytest.mark.graphdb
def test_store():
    pass


@pytest.mark.influxdb
def test_frames():
    pass


@pytest.mark.posix
def test_offline():
    pass
"""

MARKERS = """
[pytest]
markers =
    graphdb: a live GraphDB
    influxdb: a live InfluxDB
    posix: POSIX only
    live: a live service
addopts = --strict-markers
"""


@pytest.fixture
def session(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeini(MARKERS)
    pytester.makepyfile(SESSION_TESTS)
    return pytester


def run(session: pytest.Pytester, *args: str) -> pytest.RunResult:
    # Without pytest-playwright: its hooks need a browser run's state that these sessions lack.
    return session.runpytest_inprocess("-p", "_live_marker", "-p", "no:playwright", *args)


def test_the_offline_tier_deselects_every_test_of_a_live_service(
    session: pytest.Pytester,
) -> None:
    result = run(session, "-m", "not live")
    result.assert_outcomes(passed=1, deselected=2)


def test_the_live_tier_selects_the_tests_of_both_services(session: pytest.Pytester) -> None:
    result = run(session, "-m", "live")
    result.assert_outcomes(passed=2, deselected=1)
