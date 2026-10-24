"""No shipped file or test spells an ontology namespace with ``https://``.

Every ontology namespace of the project uses ``http://``, in step with the Core ontology: a
namespace is an identifier and not a link, and a single spelling keeps a stored graph, a shape and
a query from missing each other. The patterns are built from fragments, so this module's own text
names no banned literal.

**The match.** Two rules, over the same files as the old-name guard.

- A closed list of the project's namespace prefixes, ``NAMESPACE_PREFIXES``, spelled with the
  ``https`` scheme anywhere.
- Any ``https`` IRI inside a ``@prefix`` or ``PREFIX`` declaration.

**The exceptions, a named list and nothing else.**

- External vocabularies whose own namespace is ``https``: none is cited today. The closed list
  does not name them, but a ``@prefix`` line that declares one would match; add an allowance here
  when the first one is cited.
- Web URLs and plain links: the documentation sites, the tracker, the code host, a store URL, a
  forwarded page URL such as ``example.org/forwarded``. None is in the closed list, so none matches.
- Test data that must cover the ``https`` scheme of IRI syntax: none of it uses a listed prefix.
- The released sections of ``CHANGELOG.md``.
- The dated audit reports under ``docs/audits``, which ``tests/_text_files.py`` skips.
"""

from __future__ import annotations

import re

from _text_files import REPO_ROOT, text_files, unreleased_changelog_lines

# Built from fragments, so that no line of this shipped file holds a banned literal.
_SCHEME = "https" + ":" + "//"
_CRC = "www.sfb1574.kit.edu/ontologies/"
_CORE = "w3id.org/circularfactory/"

NAMESPACE_PREFIXES = (
    _CORE + "Core",
    _CORE + "Service",
    _CORE + "MES",
    _CORE + "FlexConveyor",
    _CORE + "FlexConveyorInstances",
    _CRC + "TransferUnit#",
    _CORE + "Unscrewing",
    # The example namespaces of the tests and the docs.
    "example.org/plant#",
    "example.org/test#",
    "example.org/shacl-refusal#",
    "example.org/stations#",
    "example.org/shapes#",
    "example.org/another-vocabulary#",
    "example.org/not-a-jms-kapps-vocabulary#",
)

LISTED = re.compile(
    re.escape(_SCHEME) + "(?:" + "|".join(re.escape(p) for p in NAMESPACE_PREFIXES) + ")"
)
DECLARED = re.compile(r"^\s*(?:@prefix|PREFIX)\b[^\n]*" + re.escape(_SCHEME))


def _is_offence(line: str) -> bool:
    return bool(LISTED.search(line) or DECLARED.search(line))


def _offences() -> list[str]:
    found = []
    for path in text_files():
        relative = path.relative_to(REPO_ROOT)
        lines = path.read_text(encoding="utf-8").splitlines()
        if relative.parts == ("CHANGELOG.md",):
            # Released sections are exempt; the Unreleased section is checked, so the line
            # numbers below count from its heading.
            lines = unreleased_changelog_lines()
            relative = relative / "Unreleased"
        for number, line in enumerate(lines, 1):
            if _is_offence(line):
                found.append(f"{relative}:{number}: {line.strip()}")
    return found


def test_the_pattern_hits_a_listed_namespace_and_a_prefix_declaration() -> None:
    assert _is_offence("<" + _SCHEME + _CORE + "Core#Operation>")
    assert _is_offence('JMS = "' + _SCHEME + "w3id.org/circularfactory/Unscrewing#" + '"')
    assert _is_offence("@prefix ex: <" + _SCHEME + "elsewhere.org/ns#> .")
    assert _is_offence("PREFIX ex: <" + _SCHEME + "elsewhere.org/ns#>")


def test_the_pattern_misses_the_named_exceptions() -> None:
    assert not _is_offence(_SCHEME + "store.example.org")
    assert not _is_offence(_SCHEME + "example.org/forwarded/8090/")
    assert not _is_offence("[c](" + _SCHEME + "example.org/)")
    assert not _is_offence(_SCHEME + "docs.astral.sh/uv/")
    assert not _is_offence("<http" + "://" + _CORE + "Core#Operation>")


def test_the_scan_reaches_the_ontologies_and_the_user_guide() -> None:
    names = {path.name for path in text_files()}
    assert {"integration_usecase.ttl", "flexconveyor_usecase_shapes.ttl", "user-guide.md"} <= names


def test_no_shipped_file_or_test_spells_an_ontology_namespace_with_https() -> None:
    found = _offences()
    assert not found, "\n".join(found)
