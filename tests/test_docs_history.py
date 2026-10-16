"""The README, the reviewer guide and the reference pages hold the current text, and no history.

What a version changed is in `CHANGELOG.md`, and what a text said before is in git. So the front
door of the repository, `README.md`, the reviewer guide, the pages under `docs/reference/`, and
the comments of `run-uc2`, `compose.yaml` and the manifest, carry no dated note, no ticket
reference and no name of a person. The manifest's authors and maintainers name people by design, and stay.

The package's own name, `jms_kapps`, stays wherever it names the package: a path, a module or an
entry-point group. Only a ticket reference, such as the package name followed by a number, fails.

The guide is held more strictly: it tells a reader what they see and how to use it, and the paper
says what it means. So it names no ticket, no internal record and not even the package's own name,
numbers no claim of the paper, and has no table of them.

The README is no longer than the siblings', about 300 lines at most. Its table of the paper's
words, and the guide's, is the glossary's. Every relative link of the README, of the guide and of
the reference pages leads to a file, and to a heading of that file where it names one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
REFERENCE = REPO_ROOT / "docs" / "reference"
GUIDE = REPO_ROOT / "docs" / "reviewer-guide.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

HISTORY_FREE = (
    "README.md",
    "run-uc2",
    "compose.yaml",
    "pyproject.toml",
    "CHANGELOG.md",
)


def _history_free_files() -> list[Path]:
    return [REPO_ROOT / name for name in HISTORY_FREE] + [GUIDE] + sorted(REFERENCE.glob("*.md"))


HISTORY = {
    "a dated note": re.compile(r"\b(?:changed|added) on\b", re.IGNORECASE),
    "a quoted earlier version": re.compile(r"\buntil then\b", re.IGNORECASE),
    "a decision's author": re.compile(r"\bdecided (?:by|on)\b", re.IGNORECASE),
    "a build's reading": re.compile(r"\bthe build's reading\b", re.IGNORECASE),
    "a ticket reference": re.compile(
        r"\b(?:jms_kapps|KSM|kapps_[a-z_]+)\s+[#!]?\d+\b|(?<![\w/&-])[#!]\d+\b"
    ),
    "the middleware's short name": re.compile(r"\bKSM\b"),
    "a person": re.compile(r"\b(?:Etienne|Hoffmann|Sören|Soeren|Weindel)\b"),
}

# The manifest names its authors and maintainers by design.
PEOPLE_BY_DESIGN = re.compile(r'^\s*\{ name = "[^"]+", email = "[^"]+" \},?$')


def offences_in(relative: str, text: str) -> list[str]:
    """Each line of `text` that carries history, with what it carries."""
    offences: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if relative == "pyproject.toml" and PEOPLE_BY_DESIGN.match(line):
            continue
        for what, pattern in HISTORY.items():
            if pattern.search(line):
                offences.append(f"{relative}:{number}: {what}: {line.strip()}")
    return offences


def test_the_front_door_carries_no_history():
    offences: list[str] = []
    for path in _history_free_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        offences += offences_in(relative, path.read_text(encoding="utf-8"))
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("(Changed on 2026-10-06, jms_kapps 43, decided by Etienne.)", True),
        ("added on 2026-10-10", True),
        ('Until then: "the format of the four shipped ones".', True),
        ("the Windows run (KSM 317) measures it", True),
        ("kapps_triplestore_interface 32 took it", True),
        ("the build's reading of the rule", True),
        ("Sören's point", True),
        ("see `src/jms_kapps/evaluation/README.md`", False),
        ("`jms_kapps.uc2.module_strategies`", False),
        ("until the store answers", False),
        ("a module added stays on the cell clicked", False),
    ],
)
def test_the_search_finds_history_and_leaves_names_of_code(line: str, found: bool):
    assert bool(offences_in("README.md", line)) is found


def test_the_manifest_keeps_its_authors():
    line = '    { name = "Etienne Hoffmann", email = "etienne.hoffmann@kit.edu" },'
    assert offences_in("pyproject.toml", line) == []
    assert offences_in("README.md", line) != []


def test_the_readme_is_the_siblings_size():
    lines = README.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 300, f"the README has {len(lines)} lines"


def test_the_changelog_has_one_unreleased_heading():
    text = CHANGELOG.read_text(encoding="utf-8")
    assert re.findall(r"^## Unreleased$", text, re.MULTILINE) == ["## Unreleased"]


def test_the_readme_links_every_reference_page_and_the_changelog():
    targets = set(_relative_links(README))
    pages = {f"docs/reference/{page.name}" for page in REFERENCE.glob("*.md")}
    assert "docs/reference/glossary.md" in pages
    missing = pages - targets
    assert not missing, f"the README does not link {sorted(missing)}"
    for target in ("CHANGELOG.md", "docs/reviewer-guide.md", "docs/images/uc2-launcher.png"):
        assert target in targets, f"the README does not link {target}"


def test_the_guide_links_the_glossary():
    assert "docs/reference/glossary.md" in set(_relative_links(GUIDE))


def _words_map(page: Path) -> list[str]:
    """The rows of the table of the paper's words and the demo's on `page`."""
    text = page.read_text(encoding="utf-8")
    section = text.split("## The paper's words and the demo's", 1)[1].split("\n\n", 2)[1]
    return [line for line in section.splitlines() if line.startswith("|")]


def test_the_readme_and_the_glossary_map_the_paper_s_words_alike():
    readme_map = _words_map(README)
    assert len(readme_map) >= 4
    assert readme_map == _words_map(REFERENCE / "glossary.md")


def test_the_guide_maps_the_paper_s_words_as_the_glossary_does():
    assert _words_map(GUIDE) == _words_map(REFERENCE / "glossary.md")


# What the guide never says: a dated note, a decision's author, a ticket or an internal record by
# its short name, and a claim of the paper by its number or in a table of claims. The search is
# literal, as a reader's search of the page is; the dataset's dashed name, `jms-kapps`, stays.
GUIDE_NEVER = {
    "Changed on": re.compile(r"changed on", re.IGNORECASE),
    "Added on": re.compile(r"added on", re.IGNORECASE),
    "decided by": re.compile(r"decided by", re.IGNORECASE),
    # The clone's address and its folder name the code's home, not a ticket.
    "jms_kapps": re.compile(r"(?<!github\.com/circularfactory/)(?<!cd )jms_kapps"),
    "KSM": re.compile(r"\bKSM\b"),
    "ADR": re.compile(r"\bADR\b"),
    "a claim's number": re.compile(r"\bclaims? \d+|\bC\d+\b", re.IGNORECASE),
    "the claims' table": re.compile(r"the paper says, and", re.IGNORECASE),
}


def guide_offences(text: str) -> list[str]:
    """Each line of the guide's `text` that says what the guide never says, with what it says."""
    return [
        f"docs/reviewer-guide.md:{number}: {what}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        for what, pattern in GUIDE_NEVER.items()
        if pattern.search(line)
    ]


def test_the_guide_names_no_ticket_record_or_claim():
    offences = guide_offences(GUIDE.read_text(encoding="utf-8"))
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("the access needs a KIT account (jms_kapps 17)", True),
        ("see `src/jms_kapps/uc2/transport.py`", True),
        # Built from parts: the release refuses a shipped file that cites a decision record.
        ("its record, " + "ADR" + " 0008, lists it", True),
        ("KSM's move", True),
        ("| # | The paper says, and what the code does | Run, and see it |", True),
        ("the tests of claims 5 and 8", True),
        ("the audit's C7", True),
        ("the store's address and its port", False),
        ("the dataset `jms-kapps`", False),
        (
            "`git clone https://github.com/circularfactory/jms_kapps`, then `cd jms_kapps`",
            False,
        ),
        ("`uc2/transport.py`, where a transport is opened", False),
    ],
)
def test_the_guide_search_finds_what_the_guide_never_says(line: str, found: bool):
    assert bool(guide_offences(line)) is found


def test_the_guide_uses_the_paper_s_words():
    text = GUIDE.read_text(encoding="utf-8")
    for word in ("fault injection", "fetch", "authoritative", "write path"):
        assert word in text, f"the guide never says {word!r}"


LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def _relative_links(page: Path) -> list[str]:
    """The relative links of `page`, each resolved from the repository root, with no anchor."""
    found: list[str] = []
    for target in LINK.findall(page.read_text(encoding="utf-8")):
        if "://" in target or target.startswith(("mailto:", "#")):
            continue
        path = (page.parent / target.split("#", 1)[0]).resolve()
        found.append(path.relative_to(REPO_ROOT).as_posix())
    return found


def _anchors(page: Path) -> set[str]:
    """The anchors a forge gives the headings of `page`."""
    anchors: set[str] = set()
    fenced = False
    for line in page.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        heading = None if fenced else re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if heading:
            text = re.sub(r"[`*_]", "", heading.group(1)).strip().lower()
            text = re.sub(r"[^\w\- ]", "", text)
            anchors.add(text.replace(" ", "-"))
    return anchors


def test_every_link_of_the_readme_and_the_reference_leads_somewhere():
    broken: list[str] = []
    for page in [README, GUIDE, *sorted(REFERENCE.glob("*.md"))]:
        relative = page.relative_to(REPO_ROOT).as_posix()
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            file_part, _, anchor = target.partition("#")
            destination = (page.parent / file_part).resolve() if file_part else page
            if not destination.exists():
                broken.append(f"{relative}: {target}: no such file")
            elif anchor and destination.suffix == ".md" and anchor not in _anchors(destination):
                broken.append(f"{relative}: {target}: no such heading")
    assert not broken, "\n".join(broken)
