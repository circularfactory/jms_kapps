"""A plain install of jms_kapps gains no dependency from the measurement harness (jms_kapps 47).

Every module of `jms_kapps.evaluation` imports only the standard library, jms_kapps itself, and the
distributions that jms_kapps declares for every install. A package that only the harness needs
belongs in a dependency group or an extra named `evaluation`, which a plain install leaves out.
That use case 1 and use case 2 import nothing from the folder is `test_evaluation_folder.py`'s.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

import pytest
from _repo import find_repo_root

import jms_kapps

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"


def top_level_imports(source: str) -> set[str]:
    """The top-level name of every absolute module that `source` imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def distribution_name(requirement: str) -> str:
    """The normalized name of the distribution a requirement names."""
    return (
        re.split(r"[\s<>=~!;\[]", requirement, maxsplit=1)[0].lower().replace("_", "-")
    )


@pytest.fixture(scope="module")
def manifest() -> dict:
    root = find_repo_root(Path(__file__))
    if root is None:
        pytest.skip("an installed copy has no manifest")
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_evaluation_imports_only_what_a_plain_install_has(manifest):
    declared = {distribution_name(r) for r in manifest["project"]["dependencies"]}
    harness_only = {
        distribution_name(r)
        for r in manifest.get("dependency-groups", {}).get("evaluation", [])
        + manifest["project"].get("optional-dependencies", {}).get("evaluation", [])
        if isinstance(r, str)
    }
    providers = importlib.metadata.packages_distributions()
    sources = sorted(EVALUATION.rglob("*.py"))
    assert len(sources) > 1

    needed = {}
    for path in sources:
        for name in top_level_imports(path.read_text(encoding="utf-8")):
            if name in sys.stdlib_module_names or name in ("jms_kapps", "__future__"):
                continue
            distributions = {
                d.lower().replace("_", "-") for d in providers.get(name, [name])
            }
            if not distributions & (declared | harness_only):
                needed[f"{path.name}: {name}"] = sorted(distributions)

    assert needed == {}
