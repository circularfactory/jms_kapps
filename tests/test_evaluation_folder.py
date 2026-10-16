"""The `evaluation` folder stays apart from the demonstration (jms_kapps 52, decided by Etienne on
2026-10-07): use case 1 and use case 2 import nothing from `jms_kapps.evaluation`, the folder has
its README, and it holds the two `southward` strategies of jms_kapps 80 and no other. A strategy
reaches use case 2 only through the entry-point groups, whose discovery skips an entry that cannot
be imported, with one WARNING each, so the demonstration runs with the folder removed.
"""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

import jms_kapps
from jms_kapps.uc2.strategies import (
    MODULE_STRATEGIES,
    WMS_STRATEGIES,
    ModuleStrategy,
    WmsStrategy,
    found,
)

PACKAGE = Path(jms_kapps.__file__).parent
EVALUATION = "jms_kapps.evaluation"


def module_name(path: Path) -> str:
    """The dotted name of the module at `path`, below `PACKAGE`."""
    parts = list(path.relative_to(PACKAGE.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported(source: str, module: str, is_package: bool = False) -> set[str]:
    """Every module that `source`, the module `module`, imports, by its absolute dotted name,
    relative imports resolved, and each name a `from` import takes, as a module of its own too."""
    package = module.split(".") if is_package else module.split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            origin = ".".join([*base, *([node.module] if node.module else [])])
            names.add(origin)
            names.update(f"{origin}.{alias.name}" for alias in node.names)
    return names


def from_the_evaluation(names: set[str]) -> set[str]:
    return {n for n in names if n == EVALUATION or n.startswith(f"{EVALUATION}.")}


def test_nothing_in_use_case_1_or_use_case_2_imports_the_evaluation():
    sources = [
        path
        for use_case in ("uc1", "uc2")
        for path in sorted((PACKAGE / use_case).rglob("*.py"))
    ]
    assert sources

    found = {
        module_name(path): from_the_evaluation(
            imported(
                path.read_text(encoding="utf-8"),
                module_name(path),
                path.name == "__init__.py",
            )
        )
        for path in sources
    }

    assert {name: names for name, names in found.items() if names} == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import jms_kapps.evaluation", EVALUATION),
        ("from jms_kapps import evaluation", EVALUATION),
        ("from jms_kapps.evaluation.harness import run", f"{EVALUATION}.harness"),
        ("from ...evaluation import harness", EVALUATION),
        ("from ... import evaluation", EVALUATION),
    ],
)
def test_the_check_finds_each_spelling_of_an_import_of_the_evaluation(source, expected):
    """A probe of the check above: a module of use case 2 that imports the folder in any of the
    ways Python allows is found."""
    names = imported(source, "jms_kapps.uc2.launcher.probe")

    assert expected in from_the_evaluation(names), names


def test_starting_use_case_2_loads_nothing_of_the_evaluation():
    """The modules every process of use case 2 starts from, and the Launcher, imported as their
    processes import them: no module of the folder is loaded."""
    script = (
        "import sys\n"
        "import jms_kapps.uc2.__main__, jms_kapps.uc2.process, jms_kapps.uc2.wms_process\n"
        "import jms_kapps.uc2.launcher.api, jms_kapps.uc2.monitor.process\n"
        "print('\\n'.join(sys.modules))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    ).stdout.split()

    assert from_the_evaluation(set(loaded)) == set()


def test_the_evaluation_folder_has_its_readme_and_registers_exactly_the_southward_strategies():
    readme = (PACKAGE / "evaluation" / "README.md").read_text(encoding="utf-8")

    assert "runs without" in readme
    assert MODULE_STRATEGIES in readme and WMS_STRATEGIES in readme
    assert "southward" in readme
    registered = {
        group: sorted(
            (entry.name, entry.value)
            for entry in entry_points(group=group)
            if entry.value.startswith(EVALUATION)
        )
        for group in (MODULE_STRATEGIES, WMS_STRATEGIES)
    }
    assert registered == {
        MODULE_STRATEGIES: [
            ("southward", f"{EVALUATION}.southward:SOUTHWARD_MODULE_STRATEGY")
        ],
        WMS_STRATEGIES: [
            ("southward", f"{EVALUATION}.southward:SOUTHWARD_WMS_STRATEGY")
        ],
    }


def test_nothing_in_the_evaluation_says_any_more_that_it_holds_no_strategy():
    for path in sorted((PACKAGE / "evaluation").rglob("*")):
        if path.suffix in {".py", ".md"}:
            text = " ".join(path.read_text(encoding="utf-8").split())
            assert "holds no strategy" not in text, path


def test_with_the_folder_removed_discovery_skips_both_southward_entries_with_one_warning_each(
    monkeypatch, caplog
):
    """The folder's modules cannot be imported: the entries are skipped, and `real` is found."""
    for name in [m for m in sys.modules if m.startswith(EVALUATION)]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, EVALUATION, None)

    with caplog.at_level(logging.WARNING, logger="jms_kapps.uc2.strategies"):
        modules = found(ModuleStrategy)
        wms = found(WmsStrategy)

    assert list(modules) == ["real"] and list(wms) == ["real"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert sorted(w.split(" is skipped")[0] for w in warnings) == [
        "The WMS strategy southward (jms_kapps.evaluation.southward:SOUTHWARD_WMS_STRATEGY)",
        "The module strategy southward (jms_kapps.evaluation.southward:SOUTHWARD_MODULE_STRATEGY)",
    ]
