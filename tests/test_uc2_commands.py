"""The commands of `jms-uc2`, as a reader sees them in its help."""

from __future__ import annotations

import contextlib
import io
import re

import pytest

from jms_kapps.uc2.__main__ import main


def listed_commands() -> list[str]:
    """The subcommands that `jms-uc2 --help` lists, in the order it lists them."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    braces = re.search(r"\{([^}]*)\}", out.getvalue())
    assert braces, out.getvalue()
    return braces.group(1).split(",")


def test_the_help_lists_the_audit_and_the_three_demonstrations_only():
    assert listed_commands() == [
        "audit",
        "capability-resource",
        "reachable-destination",
        "trace-box",
    ]
