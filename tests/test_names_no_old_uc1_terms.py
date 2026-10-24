"""No shipped file or test names one of UC1's six renamed ontology terms by its old name.

The ontology renames six terms of its own, so that the paper's text and the supplement read the
same: the copy of the parameter values (``:ParameterCopy``, ``:classifiedWith``), the operation's
links to what it concerns (``:concernsScrew``, ``:concernsWorkpiece``) and the detection result
(``:DetectionResult``, ``:hasDetectionResult``). The old names have no alias and no equivalence. A
file that still spells one would write or read a property that the ontology no longer declares.

**The match.** Each pattern is case-sensitive, and ``OLD_NAMES`` below builds each from fragments.

- The four property names (the operation's screw link, its workpiece link, the link to the copy of
  the values and the link to the result) match as whole words, so that the screw-type property,
  whose name starts with the screw link's, does not match.
- The two class names also match as the start of a longer name, so that the old shape names and the
  old Python names are caught. A bare fragment in a string (a test's term lookup) is caught too.
- The result link is not matched where the service prefix precedes it: the prefixed form, the Python
  service vocabulary and the service namespace IRI. That property belongs to the middleware
  (the service ontology's naming) and keeps its name.

The patterns are built from fragments, so this module's own text names no banned literal.

**What is scanned.** The same files as the status-property guard: ``tests/_text_files.py``.

**The exceptions, a named list and nothing else.**

- The released sections of ``CHANGELOG.md``.
- The one line of the Unreleased section that announces the renames.
- Any line that carries the fixed marker ``RENAMED_MARKER``.
- The dated audit reports under ``docs/audits``, which ``tests/_text_files.py`` skips.
"""

from __future__ import annotations

import re
from pathlib import Path

from _text_files import REPO_ROOT, text_files, unreleased_changelog_lines

# Built from fragments, so that no line of this shipped file holds a date.
RENAMED_MARKER = "(renamed on " + "2026" + "-10-" + "23 from"

SERVICE_NS = "circularfactory/Service#"

OLD_NAMES = {
    "has" + "Screw": re.compile(r"\bhas" + "Screw" + r"\b"),
    "used" + "Values": re.compile(r"\bused" + "Values" + r"\b"),
    "has" + "Workpiece": re.compile(r"\bhas" + "Workpiece" + r"\b"),
    "has" + "Outcome": re.compile(r"(?<!svc:)(?<!SVC\.)\bhas" + "Outcome" + r"\b"),
    "Decision" + "Snapshot": re.compile("Decision" + "Snapshot"),
    "Unscrewing" + "Outcome": re.compile("Unscrewing" + "Outcome"),
}


def _names_in(line: str) -> list[str]:
    if SERVICE_NS in line:
        line = line.replace(SERVICE_NS + "has" + "Outcome", "")
    return [name for name, pattern in OLD_NAMES.items() if pattern.search(line)]


def _offences() -> list[str]:
    found = []
    for path in text_files():
        relative = path.relative_to(REPO_ROOT)
        if relative.parts == ("CHANGELOG.md",):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RENAMED_MARKER not in line and _names_in(line):
                found.append(f"{relative}:{number}: {line.strip()}")
    return found


def test_the_scan_reaches_the_package_the_tests_and_the_ontology() -> None:
    names = {path.name for path in text_files()}
    assert {
        "integration_usecase.ttl",
        "integration_usecase_shapes.ttl",
        "planner.py",
        "vocabulary.py",
        "use-case-1.md",
        Path(__file__).name,
    } <= names


def test_each_old_name_is_matched() -> None:
    for old in (
        ":" + "has" + "Screw",
        ":used" + "Values",
        "uns:has" + "Workpiece",
        "JMS.has" + "Outcome",
        '"has' + 'Outcome"',
        "Decision" + "SnapshotShape",
        "Decision" + "SnapshotGeneratedByShape",
        "Unscrewing" + "OutcomeThing",
        '"Decision' + 'Snapshot"',
    ):
        assert _names_in(old), old


def test_the_screw_type_and_the_service_property_pass() -> None:
    assert not _names_in(":has" + "ScrewType")
    assert not _names_in("uns:has" + "ScrewType ?type")
    assert not _names_in("svc:has" + "Outcome")
    assert not _names_in("SVC.has" + "Outcome")
    assert not _names_in("<https://w3id.org/" + SERVICE_NS + "has" + "Outcome>")
    assert not _names_in("hasDetectionResult")
    assert not _names_in("ParameterCopy classifiedWith concernsScrew")


def test_the_patterns_are_case_sensitive() -> None:
    assert not _names_in("decision" + "snapshot")
    assert not _names_in("has" + "outcome")
    assert not _names_in("Used" + "Values")


def test_no_shipped_file_or_test_names_an_old_uc1_term() -> None:
    found = _offences()
    assert not found, "\n".join(found)


def test_the_unreleased_changelog_names_the_old_terms_in_one_line_at_most() -> None:
    naming = [line for line in unreleased_changelog_lines() if _names_in(line)]
    assert len(naming) <= 1, naming
