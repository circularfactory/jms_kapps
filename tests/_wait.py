"""Wait in a test until a condition holds, or fail the test.

The processes these tests start run on their own threads and in their own processes, so what a test
checks after an action holds a little later, not at once.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest


def eventually(
    condition: Callable[[], bool], *, timeout: float = 10.0, interval: float = 0.05
) -> None:
    """Return once `condition()` holds, and fail the test if it does not within `timeout` seconds.

    It asks every `interval` seconds. A condition that raises `httpx.HTTPError` has not held yet:
    it asked a server that does not answer yet, or no longer answers.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            if condition():
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            pytest.fail("the condition did not hold in time")
        time.sleep(interval)
