"""Pack a finished commit time run into the raw-data part and the reports part of the published
repository (`jms_kapps.commit_time.packing`), with the figure script beside this one.

    uv run python commit_time/pack.py <run folder> --out commit_time/results --release <tag> \
        --release-config release/config.toml
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from jms_kapps.commit_time import packing

FIGURE_SCRIPT = Path(__file__).resolve().parent / "figure.py"


def load_figure_script() -> ModuleType:
    """The figure script beside this one, loaded from its file, so that a debugger steps into
    it."""
    found = importlib.util.spec_from_file_location("commit_time_figure", FIGURE_SCRIPT)
    assert found is not None and found.loader is not None
    module = importlib.util.module_from_spec(found)
    # A dataclass looks its module up by name while it is made.
    sys.modules[found.name] = module
    found.loader.exec_module(module)
    return module


if __name__ == "__main__":
    figure = load_figure_script()
    raise SystemExit(packing.main(draw_figures=packing.figure_drawer(figure.main)))
