"""The files of the repository, as the tests that scan them see them: the files Git tracks in a
checkout, and every file outside a dot-folder and the bytecode caches in an exported tree.

The release's own offline tier runs the tests in a tree exported without `.git`, so a scan must not ask Git for the tracked files.
"""

from __future__ import annotations

import subprocess

from _repo import repository_files


def test_in_a_checkout_the_files_are_the_tracked_ones(tmp_path):
    (tmp_path / "tracked.txt").write_text("kept")
    (tmp_path / "untracked.txt").write_text("left out")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)

    assert repository_files(tmp_path) == [tmp_path / "tracked.txt"]


def test_in_an_exported_tree_the_files_are_all_but_dot_folders_and_caches(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("kept")
    (tmp_path / ".python-version").write_text("kept")
    for left_out in (".pytest_cache/v/data", "src/__pycache__/module.pyc", ".venv/lib/x.py"):
        path = tmp_path / left_out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("left out")

    assert repository_files(tmp_path) == [
        tmp_path / ".python-version",
        tmp_path / "src" / "module.py",
    ]
