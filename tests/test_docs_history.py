"""The README, the user guide and the reference pages hold the current text, and no history.

What a version changed is in `CHANGELOG.md`, and what a text said before is in git. So the front
door of the repository, `README.md`, the user guide, the pages under `docs/reference/`, and the
comments of `run-uc2`, `compose.yaml` and the manifest, carry no dated note, no ticket reference and
no name of a person. So do the evaluation package's README, the docstrings and comments of the
source tree and the tests, and the comments of the static and ontology files. The manifest's authors
and maintainers name people by design, and stay.

The package's own name, `jms_kapps`, stays wherever it names the package: a path, a module or an
entry-point group. Only a ticket reference, such as the package name followed by a number, fails.

The guide is held more strictly: it tells a reader what they see and how to use it, and the paper
says what it means. So it names no ticket, no internal record and not even the package's own name,
numbers no claim of the paper, and has no table of them. The README and the reference pages are
searched for a claim's number and a table of claims too.

The README is no longer than the siblings', about 300 lines at most. Its table of the paper's
words, and the guide's, is the glossary's. Every relative link of the README, of the guide and of
the reference pages leads to a file, and to a heading of that file where it names one.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
REFERENCE = REPO_ROOT / "docs" / "reference"
GUIDE = REPO_ROOT / "docs" / "user-guide.md"
EVALUATION_README = REPO_ROOT / "src" / "jms_kapps" / "evaluation" / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

HISTORY_FREE = (
    "README.md",
    "run-uc2",
    "compose.yaml",
    "pyproject.toml",
    "CHANGELOG.md",
)


def _history_free_files() -> list[Path]:
    return (
        [REPO_ROOT / name for name in HISTORY_FREE]
        + [GUIDE]
        + sorted(REFERENCE.glob("*.md"))
    )


HISTORY = {
    "a dated note": re.compile(r"\b(?:changed|added) on\b", re.IGNORECASE),
    "a quoted earlier version": re.compile(r"\buntil then\b", re.IGNORECASE),
    "a decision's author": re.compile(r"\bdecided (?:by|on)\b", re.IGNORECASE),
    "a build's reading": re.compile(r"\bthe build's reading\b", re.IGNORECASE),
    "a ticket reference": re.compile(
        r"\b(?:jms_kapps|KSM|kapps_[a-z_]+)\s+[#!]?\d+\b|(?<![\w/&-])[#!]\d+\b"
    ),
    "the middleware's short name": re.compile(r"\bKSM\b"),
    # "Hoffmann et al." is a cited publication, not a person of the history.
    "a person": re.compile(r"\b(?:Etienne|Hoffmann(?! et al\.)|Sören|Soeren|Weindel)\b"),
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


# The source tree and the tests keep the same rule in their docstrings and comments: a module says
# what it does now, once. What it did until a date is in git. The middleware's short name alone
# is no history there; a ticket reference, a dated note and a person's name are.
SOURCE_HISTORY = {
    what: pattern
    for what, pattern in HISTORY.items()
    if what != "the middleware's short name"
}
SOURCE_FOLDERS = ("src", "tests", "tests_release", "scripts")


def _source_files() -> list[Path]:
    return sorted(
        path
        for folder in SOURCE_FOLDERS
        for path in (REPO_ROOT / folder).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def source_offences_in(relative: str, text: str) -> list[str]:
    """Each docstring or comment line of the Python `text` that carries history, with what it
    carries. A ticket reference wrapped across a line break inside one docstring or comment block
    counts too.
    """
    prose: dict[int, str] = {}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            prose[token.start[0]] = token.string
    for node in ast.walk(ast.parse(text)):
        # A string that stands alone as a statement is a docstring: a module's, a class's, a
        # function's, or the one that follows an assignment and describes it.
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for number in range(node.lineno, node.end_lineno + 1):
                prose[number] = text.splitlines()[number - 1]
    offences = [
        f"{relative}:{number}: {what}: {line.strip()}"
        for number, line in sorted(prose.items())
        for what, pattern in SOURCE_HISTORY.items()
        if pattern.search(line)
    ]
    # A ticket reference can be wrapped across a line break ("jms_kapps\n    110"), which a search
    # line by line misses. Each run of consecutive prose lines is joined, with its comment marks
    # and indentation dropped, and each reference that starts on one line and ends on the next is
    # reported.
    ticket = SOURCE_HISTORY["a ticket reference"]
    for numbers, lines in _prose_blocks(prose):
        starts, joined = [], ""
        for number, line in zip(numbers, lines):
            starts.append((len(joined), number))
            joined += line + " "
        for match in ticket.finditer(joined):
            first, last = (
                max(n for at, n in starts if at <= position)
                for position in (match.start(), match.end() - 1)
            )
            if first != last:
                offences.append(
                    f"{relative}:{first}: a ticket reference: "
                    f"{' '.join(match.group(0).split())} (wrapped across lines)"
                )
    return offences


def _prose_blocks(prose: dict[int, str]) -> list[tuple[list[int], list[str]]]:
    """Each run of consecutive prose lines as (its line numbers, its lines without marks)."""
    runs: list[list[int]] = []
    for number in sorted(prose):
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    return [(run, [prose[n].strip().lstrip("#").strip() for n in run]) for run in runs]


# The static pages, the stylesheets and the ontology and shape files carry the same rule in their
# comments. They hold no other text that could read as history, so each line is searched; a bare
# hash and number is no ticket reference there, since a colour is written so.
STATIC_SUFFIXES = (".html", ".js", ".css", ".ttl", ".toml")
STATIC_HISTORY = {
    what: (
        re.compile(r"\b(?:jms_kapps|KSM|kapps_[a-z_]+)\s+[#!]?\d+\b")
        if what == "a ticket reference"
        else pattern
    )
    for what, pattern in SOURCE_HISTORY.items()
}


def static_offences_in(relative: str, text: str) -> list[str]:
    """Each line of the static or ontology `text` that carries history, with what it carries."""
    return [
        f"{relative}:{number}: {what}: {line.strip()[:120]}"
        for number, line in enumerate(text.splitlines(), start=1)
        for what, pattern in STATIC_HISTORY.items()
        if pattern.search(line)
    ]


def test_the_static_files_and_the_ontologies_carry_no_history():
    offences: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*")):
        if path.suffix in STATIC_SUFFIXES:
            relative = path.relative_to(REPO_ROOT).as_posix()
            offences += static_offences_in(relative, path.read_text(encoding="utf-8"))
    assert not offences, f"{len(offences)} lines:\n" + "\n".join(offences)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("<!-- Added on 2026-10-10, jms_kapps 24, decided by Etienne -->", True),
        ("// (Changed on 2026-10-10, jms_kapps 24: the arrows went.)", True),
        ("# Provenance of a decision (jms_kapps 59)", True),
        ("svg.topology .module rect.frame { fill: #262626; }", False),
        # Built at runtime: the release check reads a literal `#<digits>` as a tracker reference.
        ("a.href = `#" + "1566`;", False),
    ],
)
def test_the_static_search_finds_history_and_leaves_colours(text: str, found: bool):
    assert bool(static_offences_in("x", text)) is found


def test_the_source_tree_and_the_tests_carry_no_history():
    offences: list[str] = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        offences += source_offences_in(relative, path.read_text(encoding="utf-8"))
    assert not offences, f"{len(offences)} lines:\n" + "\n".join(offences)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ('"""A module.\n\nChanged on 2026-10-06, jms_kapps 43.\n"""\n', True),
        ("x = 1  # decided by Etienne\n", True),
        ("def f():\n    '''Does it (KSM 317).'''\n", True),
        ('class C:\n    """Sören\'s point."""\n', True),
        ('X = 1\n"""What X is. (Added on 2026-10-12.)"""\n', True),
        ('"""A module.\n\nThe tables (jms_kapps\n    110).\n"""\n', True),
        ("# the rounds (KSM\n# 413) of a sweep\nx = 1\n", True),
        ('"""A module.\n\nSee jms_kapps 5 and, wrapped, jms_kapps\n7.\n"""\n', True),
        ("# the first jms_kapps\n\n# 413 is not one block\nx = 1\n", False),
        ('"""A module."""\nx = "jms_kapps 43"\n', False),
        ('"""A module."""\n\n\ndef f():\n    return "decided by"\n', False),
        ("# see `jms_kapps.uc2.module_strategies`\nx = 1\n", False),
        ("# the KSM's store layer\nx = 1\n", False),
    ],
)
def test_the_source_search_reads_docstrings_and_comments_only(text: str, found: bool):
    assert bool(source_offences_in("x.py", text)) is found


def test_the_front_door_carries_no_history():
    offences: list[str] = []
    for path in _history_free_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        offences += offences_in(relative, path.read_text(encoding="utf-8"))
    assert not offences, "\n".join(offences)


# The evaluation README is a shipped page under the same rule. It names the middleware by its short
# name as a plain word ("outside KSM"), so it is searched with the source tree's patterns, which
# leave that one out. A ticket reference is also searched across a line break.


def evaluation_readme_offences(text: str) -> list[str]:
    """Each place in the evaluation README `text` that carries history, with what it carries."""
    relative = "src/jms_kapps/evaluation/README.md"
    offences = [
        f"{relative}:{number}: {what}: {line.strip()[:120]}"
        for number, line in enumerate(text.splitlines(), start=1)
        for what, pattern in SOURCE_HISTORY.items()
        if pattern.search(line)
    ]
    for match in SOURCE_HISTORY["a ticket reference"].finditer(text):
        number = text.count("\n", 0, match.start()) + 1
        found = f"{relative}:{number}: a ticket reference:"
        if not any(offence.startswith(found) for offence in offences):
            offences.append(f"{found} {' '.join(match.group(0).split())}")
    return offences


def test_the_evaluation_readme_carries_no_history():
    text = EVALUATION_README.read_text(encoding="utf-8")
    offences = evaluation_readme_offences(text)
    assert not offences, f"{len(offences)} places:\n" + "\n".join(offences)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("(Changed on 2026-10-12, jms_kapps 99, decided by Sören Weindel.)", True),
        ("the rounds (jms_kapps\n  111) of a sweep", True),
        ("Until then a sweep made one pass.", True),
        ("the harness observes a run from outside KSM", False),
        ("`--stop-at 2026-10-14T06:00+02:00` is a stop time", False),
        ("measured on the KIT GraphDB 10.8.5 on 2026-10-01", False),
        ("`jms_kapps.evaluation.prefill` writes it", False),
    ],
)
def test_the_evaluation_readme_search_finds_history(text: str, found: bool):
    assert bool(evaluation_readme_offences(text)) is found


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
    for target in (
        "CHANGELOG.md",
        "docs/user-guide.md",
        "docs/images/uc2-launcher.png",
    ):
        assert target in targets, f"the README does not link {target}"


def test_the_guide_links_the_glossary():
    assert "docs/reference/glossary.md" in set(_relative_links(GUIDE))


CONCEPTS_HEADING = "## The paper's concepts and their instances in the use cases"


def _concepts_table(page: Path) -> list[str]:
    """The rows of the table of the paper's concepts on `page`."""
    text = page.read_text(encoding="utf-8")
    section = text.split(CONCEPTS_HEADING, 1)[1].split("\n\n", 2)[1]
    return [line for line in section.splitlines() if line.startswith("|")]


def test_the_glossary_holds_the_one_table_of_the_paper_s_concepts():
    glossary = (REFERENCE / "glossary.md").read_text(encoding="utf-8")
    assert glossary.count(CONCEPTS_HEADING) == 1
    rows = _concepts_table(REFERENCE / "glossary.md")
    assert rows[0] == "| Concept in the paper | Instance in the use cases |"
    assert len(rows) >= 4
    for page in (README, GUIDE):
        text = page.read_text(encoding="utf-8")
        assert CONCEPTS_HEADING not in text
        assert rows[0] not in text
        assert "#the-papers-concepts-and-their-instances-in-the-use-cases" in text


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


def guide_offences(
    text: str, relative: str = GUIDE.relative_to(REPO_ROOT).as_posix()
) -> list[str]:
    """Each line of `text`, the guide's unless `relative` names another page, that says what the
    guide never says, with what it says."""
    return [
        f"{relative}:{number}: {what}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        for what, pattern in GUIDE_NEVER.items()
        if pattern.search(line)
    ]


def test_the_guide_names_no_ticket_record_or_claim():
    offences = guide_offences(GUIDE.read_text(encoding="utf-8"))
    assert not offences, "\n".join(offences)


def test_the_readme_and_the_reference_pages_name_no_claim_number_or_claims_table():
    offences: list[str] = []
    for path in [README, *sorted(REFERENCE.glob("*.md"))]:
        relative = path.relative_to(REPO_ROOT).as_posix()
        offences += [
            offence
            for offence in guide_offences(path.read_text(encoding="utf-8"), relative)
            if "a claim's number" in offence or "the claims' table" in offence
        ]
    assert not offences, "\n".join(offences)


def test_the_claim_search_finds_a_planted_claim_and_names_its_page():
    planted = "Paper claim 16 is shown here."
    offences = guide_offences(planted, "README.md")
    assert any(o.startswith("README.md:1: a claim's number") for o in offences)


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
            # A forge keeps an underscore inside a word and drops one that marks emphasis.
            text = re.sub(r"[`*]|(?<!\w)_|_(?!\w)", "", heading.group(1)).strip().lower()
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
            elif (
                anchor
                and destination.suffix == ".md"
                and anchor not in _anchors(destination)
            ):
                broken.append(f"{relative}: {target}: no such heading")
    assert not broken, "\n".join(broken)
