"""What the tests share: the dataset a live run may clear, and the browser of the pages' tests.

The `graphdb` tests clear the dataset they run against, so it is named on the command line,
never read from the environment: `uv run pytest --dataset <name>`. There is no default: where
the store's connection variables are set, a run that selects a test needing the dataset and
names none stops before any test runs. Without the variables the graphdb tests skip and no
dataset is needed. The option, the check and the `dataset` fixture are in
`_dataset_option.py`.

The `browser` tests run a page's JavaScript in a headless browser, through pytest-playwright, whose
options (`--browser`, `--browser-channel`, `--headed`) choose it, and Playwright starts as
`_browser.start_playwright` starts it: see `_browser.py`.

A test marked `posix` skips on Windows, and one marked `windows` skips everywhere else. Which
platform this is, the tests read where the package reads it: from its one platform switch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from _browser import launch, start_playwright
from playwright.sync_api import Browser, Playwright, sync_playwright

from jms_kapps.common.platforms import PLATFORM, windows

# `pytester` runs the small pytest sessions of `test_dataset_option.py` and `test_live_marker.py`.
pytest_plugins = ["_dataset_option", "_live_marker", "_uc1_mode_option", "pytester"]


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a test that works only on another platform, with the reason its marker gives, if it
    gives one: `@pytest.mark.posix(reason="...")`."""
    other = "posix" if PLATFORM is windows else "windows"
    marker = item.get_closest_marker(other)
    if marker is not None:
        only = "POSIX" if other == "posix" else "Windows"
        why = marker.kwargs.get("reason")
        pytest.skip(f"works only on {only}" + (f": {why}" if why else ""))


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    """Playwright, in place of pytest-playwright's own fixture, which starts and stops it the same
    way: see `_browser.start_playwright`."""
    started = start_playwright(sync_playwright().start)
    yield started
    started.stop()


@pytest.fixture(scope="session")
def browser(
    launch_browser: Callable[..., Browser],
    browser_name: str,
    browser_channel: str | None,
) -> Iterator[Browser]:
    """The browser the pages' tests run in, in place of pytest-playwright's own fixture: see
    `_browser.launch`. A test that needs one skips where none is installed."""
    launched = launch(launch_browser, browser_name, browser_channel)
    yield launched
    launched.close()
