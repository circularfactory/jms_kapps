"""The `live` marker: every test that needs a live service, so that `-m "not live"` is the offline tier.

A test that needs a live store is marked by the service it needs, `graphdb` or `influxdb`. This
plugin marks each of them `live` too, at collection, so that the offline tier is the same command
here as in the three libraries and in the release mechanism's own tier: `pytest -m "not live"`.
"""

from __future__ import annotations

import pytest

# The markers of the tests that need a live service.
LIVE_SERVICES = ("graphdb", "influxdb")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark `live` each collected test that needs a live service."""
    for item in items:
        if any(item.get_closest_marker(name) for name in LIVE_SERVICES):
            item.add_marker(pytest.mark.live)
