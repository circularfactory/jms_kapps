"""The look that the Launcher and the monitor share: SFB 1574's logo and one set of colours.

The two pages are served by two processes, so each carries its own stylesheet and its own copy of the
logo; these tests read the static files and check that the copies agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import jms_kapps.uc2.launcher
import jms_kapps.uc2.monitor

LAUNCHER = Path(jms_kapps.uc2.launcher.__file__).parent / "static"
MONITOR = Path(jms_kapps.uc2.monitor.__file__).parent / "static"
PNG = b"\x89PNG\r\n\x1a\n"

# What each page writes in its header to ask for the logo: the Launcher serves its static folder at
# `/`, the monitor under `static/`, and neither path starts with a slash.
LOGO_PATHS = {LAUNCHER: "sfb-logo.png", MONITOR: "static/sfb-logo.png"}


def colour_variables(stylesheet: str) -> dict[str, str]:
    """The custom properties of the first `:root` block that names a colour."""
    for block in re.findall(r":root\s*{([^}]*)}", stylesheet):
        found = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))
        if any(value.strip().startswith("#") for value in found.values()):
            return {name: value.strip() for name, value in found.items()}
    return {}


@pytest.mark.parametrize("static", [LAUNCHER, MONITOR], ids=["launcher", "monitor"])
def test_each_page_ships_the_logo_and_asks_for_it_by_a_relative_path(static):
    assert (static / "sfb-logo.png").read_bytes().startswith(PNG)
    header = (static / "index.html").read_text(encoding="utf-8").split("</header>")[0]
    assert f'src="{LOGO_PATHS[static]}"' in header


def test_the_two_copies_of_the_logo_are_the_same_file():
    assert (LAUNCHER / "sfb-logo.png").read_bytes() == (MONITOR / "sfb-logo.png").read_bytes()


def test_both_stylesheets_start_with_the_same_colour_variables():
    launcher = colour_variables((LAUNCHER / "style.css").read_text(encoding="utf-8"))
    monitor = colour_variables((MONITOR / "style.css").read_text(encoding="utf-8"))

    assert launcher == monitor
    assert {"--teal", "--petrol", "--indigo", "--signal", "--ink"} <= launcher.keys()
    assert launcher["--teal"].lower() == "#1b7a86"
    assert launcher["--signal"].lower() == "#d0443a"


@pytest.mark.parametrize("static", [LAUNCHER, MONITOR], ids=["launcher", "monitor"])
def test_the_page_has_a_white_background(static):
    stylesheet = (static / "style.css").read_text(encoding="utf-8")
    assert colour_variables(stylesheet)["--ground"].lower() in {"#fff", "#ffffff"}
    assert re.search(r"body\s*{[^}]*background:\s*var\(--ground\)", stylesheet)
