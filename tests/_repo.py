"""Shared helpers to locate the repository root from a test module, and its files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` to the first directory that holds `pyproject.toml`.

    Returns `None` when no ancestor holds it. An installed copy has no manifest,
    so a caller must skip when that happens.
    """
    current = start.resolve()
    while True:
        if (current / "pyproject.toml").is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent


def withheld_text(path: Path) -> str:
    """The text of a development-only file, or a skip where the public tree withholds it.

    The release withholds some files from the public tree, such as `CONTEXT.md`,
    while `tests/` ships whole. A test that reads such a file to check that it agrees with a
    shipped one runs in the development checkout and skips in a public one.
    """
    if not path.is_file():
        pytest.skip(f"{path.name} is withheld from the public tree")
    return path.read_text(encoding="utf-8")


def repository_files(root: Path) -> list[Path]:
    """The files of the repository at `root`, sorted: the files Git tracks in a checkout.

    The release's own offline tier runs the tests in a tree exported without `.git`, which holds
    the tracked files and what the run writes. There they are every file outside a dot-folder
    (`.pytest_cache`, a virtual environment) and the bytecode caches.
    """
    if (root / ".git").exists():
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        ).stdout.split(b"\0")
        return sorted(root / name.decode() for name in listed if name)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(root).parent.parts
        )
    )
