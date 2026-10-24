"""The two recordings of use case 1 are example data, and no shipped text calls them anything else.

They are two example recordings of an unscrewing attempt, to try the anomaly detector against, not
training data. So no text that ships calls them, or the seed's values, "mock" or "legacy", and none
says that their units "are not known": the replay states the units it assumes, as an assumption of
the demo. This module walks the repository's files, outside the tests and the agents' logs, and
fails on each such word that is not in `EXCEPTIONS`.

`EXCEPTIONS` lists the places where a word means something else, each with its reason. A text that
holds a word for the recordings does not belong there: reword it.

The walk reads the files, and does not call git: the tree a release builds has no `.git`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WORDS = re.compile(r"mock|legacy|not known", re.IGNORECASE)

# Folders that are not the shipped tree: the tests, the agents' logs and the tool caches.
SKIPPED_DIRS = {
    ".git",
    ".venv",
    ".claude",
    "tests",
    "audits",  # the audit reports, which do not ship
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

# A file, or a folder ending in `/`, whose hits are exempt, with the reason, and the words that the
# reason covers (a regular expression matched against the line).
EXCEPTIONS: dict[str, tuple[str, str]] = {
    "docs/site/conf.py": (
        "Sphinx's `autodoc_mock_imports` setting, which mocks imports for the documentation build",
        r"mock",
    ),
    "src/jms_kapps/evaluation/": (
        (
            'the evaluation\'s legacy key of the "before" series, and its own "not known": a '
            "store state that a failed call leaves open"
        ),
        r"legacy|not known",
    ),
    "tests_release/": (
        "the release configuration's empty legacy-names table",
        r"legacy",
    ),
    "release/config.toml": (
        "the release configuration's legacy-names table",
        r"legacy",
    ),
    "RELEASE.md": (
        "the sentence about the release configuration's legacy-names table",
        r"legacy",
    ),
    "uv.lock": ("the lock file", r"mock|legacy|not known"),
    "CHANGELOG.md": (
        "the released sections, which stay as they were; the Unreleased section is checked below",
        r"mock|legacy|not known",
    ),
    "src/jms_kapps/uc2/": (
        'use case 2\'s own "not known": a handover whose origin is not known',
        r"not known",
    ),
    "CONTEXT.md": (
        (
            "the glossary's entry that tells an agent to avoid the term MockWMS, and the "
            'evaluation\'s legacy key and its own "not known"'
        ),
        r"MockWMS|legacy_key|LATER_KEYS|deletion is not known",
    ),
}


def _exception_for(relative: str, line: str) -> str | None:
    for path, (reason, covers) in EXCEPTIONS.items():
        applies = relative.startswith(path) if path.endswith("/") else relative == path
        if applies and all(
            re.search(covers, hit.group(), re.IGNORECASE) or re.search(covers, line)
            for hit in WORDS.finditer(line)
        ):
            return reason
    return None


def _lines_of_the_shipped_tree():
    for path in sorted(REPO_ROOT.rglob("*")):
        relative = path.relative_to(REPO_ROOT)
        if not path.is_file() or SKIPPED_DIRS & set(relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary file, an image for one
        for number, line in enumerate(text.splitlines(), start=1):
            yield relative.as_posix(), number, line


def test_no_shipped_text_calls_the_example_recordings_mock_or_legacy():
    hits = [
        f"{relative}:{number}: {line.strip()[:100]}"
        for relative, number, line in _lines_of_the_shipped_tree()
        if WORDS.search(line) and _exception_for(relative, line) is None
    ]
    assert not hits, "\n".join(hits)


def test_the_newest_changelog_section_says_what_this_change_did_and_nothing_else():
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("\n## ", 2)[1]
    this_change = [line for line in unreleased.splitlines() if WORDS.search(line)]
    # The one entry that records the renaming names the old words, and nothing else may.
    assert all(
        re.search(r"mock_frame|MOCK_RECORDINGS|MOCK_SOURCE|\"mock\"|\"legacy\"", line)
        for line in this_change
    ), this_change
