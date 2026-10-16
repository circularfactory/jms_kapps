"""The package's own source, read as files: what the tests that check the source scan."""

from __future__ import annotations

from pathlib import Path

import jms_kapps

# The installed package's directory, `src/jms_kapps` in a checkout.
SOURCE = Path(jms_kapps.__file__).parent


def sources() -> list[Path]:
    """Every Python file of the package, in a fixed order."""
    return sorted(SOURCE.rglob("*.py"))
