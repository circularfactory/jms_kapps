"""Which browser the pages' tests run in, and the skip where none is installed.

pytest-playwright's options choose the browser (`--browser`, `--browser-channel`, `--headed`); by
default it is Playwright's own Chromium, which `uv run playwright install chromium` downloads.
Where that one is not installed and no channel was named, the Google Chrome installed on this
machine runs the tests instead.

Playwright itself starts here too, in place of pytest-playwright's own start: see
`start_playwright`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

import pytest
from playwright.sync_api import Browser, Playwright
from playwright.sync_api import Error as PlaywrightError

from jms_kapps.common.platforms import PLATFORM, windows

# How Playwright says that the browser it was to launch is not on this machine: one of its own
# ("Executable doesn't exist at ..."), or one a channel names ("... is not found at ...").
_NOT_INSTALLED = re.compile(r"executable doesn't exist|is not found at", re.IGNORECASE)

_HOW_TO_INSTALL = "install Playwright's Chromium with `uv run playwright install chromium`, or Google Chrome"


def _not_installed(error: PlaywrightError) -> bool:
    return bool(_NOT_INSTALLED.search(error.message))


def launch(
    launch_browser: Callable[..., Browser],
    browser_name: str,
    browser_channel: str | None,
) -> Browser:
    """The browser that pytest-playwright's options name, launched with `launch_browser`; or the
    installed Google Chrome, where the default Chromium is not installed and no channel was named.

    Skips the test where neither is installed. Any other failure to launch fails it.
    """
    try:
        return launch_browser()
    except PlaywrightError as error:
        if not _not_installed(error):
            raise
        if browser_name != "chromium" or browser_channel:
            pytest.skip(
                f"The browser asked for is not installed: {error.message.splitlines()[0]}"
            )
    try:
        return launch_browser(channel="chrome")
    except PlaywrightError as error:
        if not _not_installed(error):
            raise
        pytest.skip(f"No browser is installed to run the page in: {_HOW_TO_INSTALL}.")


def start_playwright(start: Callable[[], Playwright]) -> Playwright:
    """Playwright, started with `start`: on Windows on a Proactor loop, elsewhere as it is.

    Playwright runs its driver as a child process. It makes its event loop once, at its start,
    from the event-loop policy in effect, and keeps it. On Windows only a Proactor loop can start a
    child process, and an MQTT client needs a Selector loop. The sync layer of the middleware sets
    a policy of Selector loops for the whole process when its MQTT connector is imported, so there
    the policy of Proactor loops is in effect for Playwright's start alone. After the start, failed
    or not, the policy is the one before, and the tests of an MQTT client keep their Selector
    loops.
    """
    if PLATFORM is not windows:
        return start()
    before = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        return start()
    finally:
        asyncio.set_event_loop_policy(before)
