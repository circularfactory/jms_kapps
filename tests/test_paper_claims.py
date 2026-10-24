"""The `paper_claim` marker: the tests that prove the paper's claims.

The paper's claims are the numbered rows of the acceptance checklist, read from arXiv 2605.22457v1:
claims 1-9 are use case 2's, claims 10-14 use case 1's, and claim 15 is the object-graph mapper's,
proved in kapps_ogm. Claim 16, the provenance of §4.2.2, is not in the checklist: it was added with
use case 1's PROV witness. Claim 17, that a rule across entities is a SHACL-SPARQL shape, is not in
the checklist either: it was added with use case 2's reachable-destination rule. Claim 18,
that every capability belongs to exactly one resource, checked at commit, was added with the
shape that holds it. Claim 19, that admission refuses a planned operation that the factory
information state cannot carry out, was added with R7, use case 1's unscrewing operation shape and
`jms-uc1 infeasible-operation`. Claim 20, that a run of use case 2 with four modules in a line reads every
change of possession of a box back from the triple store's history, was added with the paper's
R14 and its command `jms-uc2 trace-box`. Every test in jms_kapps that proves one carries the marker `paper_claim`, and
its docstring names the claim's number ("Paper claim 4", "Paper claims 5 and 8"), so that one run
by the marker, `uv run pytest -m paper_claim --dataset <name>`, runs them all. The numbering is
internal: the user guide names no claim, and tells its reader to run the marker.

These tests read the declaration and a collection of the whole suite by the marker in a pytest of
its own, without a store.
"""

from __future__ import annotations

import importlib
import inspect
import os
import re
import subprocess
import sys
import tomllib
from functools import cache
from pathlib import Path

import pytest
from _repo import find_repo_root

ROOT = find_repo_root(Path(__file__))

pytestmark = pytest.mark.skipif(
    ROOT is None,
    reason="an installed copy has no repository root and no suite",
)

# The claims a jms_kapps test proves: use case 2's and use case 1's, claim 16, use case 1's PROV
# witness, claim 17, the SHACL-SPARQL rule, claim 18, a capability's one resource, claim 19,
# the refused infeasible operation, and claim 20, a box's possessions read back from the store's
# history. Claim 15 is kapps_ogm's.
CLAIMS_OF_JMS_KAPPS = set(range(1, 15)) | {16, 17, 18, 19, 20}

# The claims that each have a marked test: use case 2's and use case 1's (1 to 14), and claims 16 to
# 20.
CLAIMS_WITH_A_TEST = set(range(1, 15)) | {16, 17, 18, 19, 20}

# How a claim test's docstring names its claims: "Paper claim 4", "Paper claims 5 and 8".
NAMED = re.compile(r"Paper claims? (\d+(?:(?:, | and )\d+)*)")

# The connection variables of a store. The collection runs without them: with them set, a
# collection that selects a test needing a dataset and names none stops before it ends. It
# runs without `PYTEST_ADDOPTS` too, so that it selects by the marker alone.
STORE_VARIABLES = ("GRAPHDB_", "TRIPLESTORE_")


def claims_named(docstring: str | None) -> set[int]:
    """The numbers of the claims that `docstring` names."""
    text = " ".join((docstring or "").split())
    return {
        int(number)
        for found in NAMED.finditer(text)
        for number in re.findall(r"\d+", found.group(1))
    }


@cache
def collected(*selection: str) -> dict[str, set[int]]:
    """Each test of the whole suite that `selection`, pytest's options, selects, as
    `<file>::<function>` (a class between the two where it has one), with the claims its
    docstring names. The suite is collected in a pytest of its own, as a run collects it."""
    assert ROOT is not None
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(STORE_VARIABLES) and name != "PYTEST_ADDOPTS"
    }
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            *selection,
            "-p",
            "no:cacheprovider",
            "tests",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
    found: dict[str, set[int]] = {}
    for line in run.stdout.splitlines():
        if "::" not in line:
            continue
        test = re.sub(r"\[.*\]$", "", line.strip())
        path, *names = test.split("::")
        target: object = importlib.import_module(Path(path).stem)
        for name in names:
            target = getattr(target, name)
        found[test] = claims_named(inspect.getdoc(target))
    return found


def marked() -> dict[str, set[int]]:
    """Each test that a run by the marker selects, with the claims its docstring names."""
    return collected("-m", "paper_claim")


def test_the_marker_is_declared():
    assert ROOT is not None
    markers = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]["markers"]

    assert [m for m in markers if m.startswith("paper_claim:")], markers


def test_each_claim_of_jms_kapps_has_a_marked_test():
    covered = set().union(*marked().values())

    assert CLAIMS_WITH_A_TEST <= covered, sorted(CLAIMS_WITH_A_TEST - covered)


def test_each_marked_test_names_the_claims_of_jms_kapps_it_proves():
    for test, claims in marked().items():
        assert claims, f"{test} carries the marker, and its docstring names no claim"
        assert claims <= CLAIMS_OF_JMS_KAPPS, f"{test} names {sorted(claims)}"


def test_each_test_the_user_guide_names_exists_and_carries_the_marker():
    assert ROOT is not None
    guide = (ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
    named = set(
        re.findall(r"`(tests/test_\w+\.py::test_\w+)`", " ".join(guide.split()))
    )

    assert named, "the guide names no test"
    assert named <= set(marked()), sorted(named - set(marked()))


def test_each_test_that_names_a_claim_carries_the_marker():
    naming = {test for test, claims in collected().items() if claims}

    assert naming - set(marked()) == set()
