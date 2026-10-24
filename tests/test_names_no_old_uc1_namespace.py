"""No shipped file or test spells UC1's old ontology namespace.

UC1's ontology, shapes and seeded instances live in ``http://w3id.org/circularfactory/Unscrewing#``,
beside the Core and Service namespaces. The old namespace has no alias and no equivalence: a file
that still spells it would write or read terms the ontology no longer declares.

**The match.** The old namespace without its scheme, whatever the scheme and whatever follows. The
pattern is built from fragments, so this module's own text names no banned literal.

**What is scanned.** The files of ``tests/_text_files.py``, which skips the dated audit reports
under ``docs/audits``, and the Unreleased section of ``CHANGELOG.md``.
"""

from __future__ import annotations

import re

from _text_files import REPO_ROOT, text_files, unreleased_changelog_lines

OLD_NAMESPACE = re.compile("sfb1574.kit.edu/ontologies/" + "JMS_Usecase" + "_Demo")


def _offences() -> list[str]:
    found = []
    for path in text_files():
        relative = path.relative_to(REPO_ROOT)
        lines = path.read_text(encoding="utf-8").splitlines()
        if relative.parts == ("CHANGELOG.md",):
            lines = unreleased_changelog_lines()
            relative = relative / "Unreleased"
        for number, line in enumerate(lines, 1):
            if OLD_NAMESPACE.search(line):
                found.append(f"{relative}:{number}: {line.strip()}")
    return found


def test_the_pattern_hits_the_old_namespace_under_either_scheme() -> None:
    assert OLD_NAMESPACE.search("http://sfb1574.kit.edu/ontologies/" + "JMS_Usecase" + "_Demo#")
    assert OLD_NAMESPACE.search("https://sfb1574.kit.edu/ontologies/" + "JMS_Usecase" + "_Demo")


def test_the_pattern_misses_the_new_and_the_transfer_unit_namespaces() -> None:
    assert not OLD_NAMESPACE.search("http://w3id.org/circularfactory/Unscrewing#")
    assert not OLD_NAMESPACE.search("http://www.sfb1574.kit.edu/ontologies/TransferUnit#")


def test_the_scan_reaches_the_ontology_the_shapes_and_the_user_guide() -> None:
    names = {path.name for path in text_files()}
    assert {"integration_usecase.ttl", "integration_usecase_shapes.ttl", "user-guide.md"} <= names


def test_no_shipped_file_or_test_spells_the_old_uc1_namespace() -> None:
    found = _offences()
    assert not found, "\n".join(found)
