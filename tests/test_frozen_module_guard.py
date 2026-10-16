"""Guard test that no frozen module is named anywhere in the repository.

It scans every `*.py` file below `src/` and below `tests/`, and `pyproject.toml`.
The forbidden names are the old middleware packages, which are frozen at the
versions the paper's artifact used.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _repo import find_repo_root

FORBIDDEN_PATTERNS = [
    # The triplestore interface was renamed to `kapps_triplestore_interface`.
    # Both the Python name and the distribution name are forbidden.
    r"graph_db_interface",
    r"graph-db-interface",
    # These three are frozen and have replacements in `kapps-semantic-middleware`.
    r"semantic_service",
    r"datamodel_connector",
    r"graph_db_to_middleware",
    # The middleware itself is frozen at 0.2.6. Match on identifier boundaries,
    # so `kapps_semantic_middleware` and `kapps-semantic-middleware` do not trip it.
    r"(?<![A-Za-z0-9_-])semantic_middleware\b",
]


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Return `(line_number, line, pattern)` for every forbidden match.

    The pattern is included so the assertion message identifies which rule fired.
    """
    findings: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, line):
                findings.append((line_number, line.strip(), pattern))
    return findings


def scan_repository(
    root: Path, exclude: Path | None = None
) -> list[tuple[Path, int, str, str]]:
    """Scan all Python files and `pyproject.toml` below `root`.

    Returns `(path, line_number, line, pattern)` for every finding. The guard
    module itself is excluded: its own `FORBIDDEN_PATTERNS` list and its
    self-test hold the forbidden literals on purpose.
    """
    findings: list[tuple[Path, int, str, str]] = []
    targets = [root / "src", root / "tests", root / "pyproject.toml"]
    for target in targets:
        if target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if exclude is not None and path.resolve() == exclude.resolve():
                    continue
                findings.extend(scan_file(path, root))
        elif target.is_file():
            findings.extend(scan_file(target, root))
    return findings


def scan_file(path: Path, root: Path) -> list[tuple[Path, int, str, str]]:
    """Scan one file for forbidden patterns, relative to `root`."""
    text = path.read_text(encoding="utf-8")
    raw = scan_text(text)
    return [
        (path.relative_to(root), line, content, pattern)
        for line, content, pattern in raw
    ]


def test_no_frozen_module_is_named() -> None:
    """No file in `src/`, `tests/`, or `pyproject.toml` names a frozen module."""
    root = find_repo_root(Path(__file__))
    if root is None:
        pytest.skip(
            "No pyproject.toml found above the test file; an installed copy has no manifest"
        )
    findings = scan_repository(root, exclude=Path(__file__))
    assert not findings, "Frozen module names found:\n" + "\n".join(
        f"{path}:{line}: {content} ({pattern})"
        for path, line, content, pattern in findings
    )


def test_guard_accepts_kapps_semantic_middleware() -> None:
    """The guard accepts the legitimate `kapps_semantic_middleware` name."""
    text = "import kapps_semantic_middleware\n"
    assert scan_text(text) == []


def test_guard_rejects_bare_semantic_middleware() -> None:
    """The guard rejects a bare `import semantic_middleware`."""
    text = "import semantic_middleware\n"
    findings = scan_text(text)
    assert findings, "Bare semantic_middleware was not flagged"
