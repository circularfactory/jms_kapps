"""The browser of the pages' tests: which one runs them, and the skip where none is installed.

These tests need no browser. Each one launches through a fake of pytest-playwright's
`launch_browser`, which answers as Playwright does where the browser it is asked for is not on
the machine.
"""

from __future__ import annotations

import pytest
from _browser import launch
from playwright.sync_api import Error as PlaywrightError

# What Playwright raises for a browser of its own that is not installed, and for a channel's.
NO_CHROMIUM = (
    "BrowserType.launch: Executable doesn't exist at "
    "/home/someone/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell"
)
NO_CHROME = "BrowserType.launch: Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome"


class LaunchBrowser:
    """A fake of pytest-playwright's `launch_browser`: it keeps each launch it is asked for, by its
    channel, and answers with the browser of each channel in `installed`, and with Playwright's
    error for any other."""

    def __init__(self, installed: dict[str | None, object], error: str = "") -> None:
        self.installed = installed
        self.error = error
        self.asked: list[str | None] = []

    def __call__(self, channel: str | None = None) -> object:
        self.asked.append(channel)
        if channel in self.installed:
            return self.installed[channel]
        raise PlaywrightError(self.error or (NO_CHROME if channel else NO_CHROMIUM))


def test_playwrights_own_chromium_runs_the_tests_where_it_is_installed():
    launches = LaunchBrowser({None: "chromium"})

    assert launch(launches, "chromium", None) == "chromium"
    assert launches.asked == [None]


def test_the_installed_chrome_runs_them_where_playwrights_chromium_is_not():
    launches = LaunchBrowser({"chrome": "chrome"})

    assert launch(launches, "chromium", None) == "chrome"
    assert launches.asked == [None, "chrome"]


def test_a_test_skips_where_no_browser_is_installed_and_says_how_to_install_one():
    with pytest.raises(
        pytest.skip.Exception, match="`uv run playwright install chromium`"
    ):
        launch(LaunchBrowser({}), "chromium", None)


@pytest.mark.parametrize(
    ("name", "channel"), [("chromium", "msedge"), ("firefox", None)]
)
def test_a_browser_asked_for_by_name_that_is_not_installed_skips_and_is_not_replaced(
    name, channel
):
    launches = LaunchBrowser({"chrome": "chrome"})

    with pytest.raises(pytest.skip.Exception, match="not installed"):
        launch(launches, name, channel)
    assert launches.asked == [None]


def test_a_browser_that_is_installed_but_fails_to_launch_fails_the_test():
    """A crash is a failure to see, not a missing browser to skip over."""
    crash = "BrowserType.launch: Target page, context or browser has been closed"

    with pytest.raises(PlaywrightError, match="has been closed"):
        launch(LaunchBrowser({}, error=crash), "chromium", None)
