"""The report's command starts on a plain install of jms_kapps, which leaves out the extra
`evaluation`: its module imports the report, and pandas with it, only when the command runs, and
says which extra to install when pandas or matplotlib is missing.

The release's install check imports every command's module on a plain install, so the report's top-level import of pandas must not fail it.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from jms_kapps.evaluation import report, report_command

ROOT = Path(__file__).resolve().parents[1]


def test_the_manifest_points_the_report_command_at_the_entry_module():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]

    assert scripts["jms-eval-report"] == "jms_kapps.evaluation.report_command:main"


def test_the_entry_module_imports_neither_pandas_nor_matplotlib():
    probe = (
        "import sys; import jms_kapps.evaluation.report_command; "
        "print(sorted({'pandas', 'matplotlib'} & set(sys.modules)))"
    )

    found = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert found.stdout.strip() == "[]"


def test_the_command_runs_the_report_with_its_arguments(monkeypatch):
    given = []
    monkeypatch.setattr(report, "main", lambda argv=None: given.append(argv) or 0)

    assert report_command.main(["spec.toml", "--runs", "runs"]) == 0
    assert given == [["spec.toml", "--runs", "runs"]]


@pytest.mark.parametrize("missing", ["pandas", "matplotlib"])
def test_without_the_extra_the_command_says_which_extra_to_install(
    monkeypatch, capsys, missing
):
    # The report and its own modules imported afresh, as a plain install first imports them.
    package = sys.modules["jms_kapps.evaluation"]
    for name in list(sys.modules):
        module = name.rpartition(".")[2]
        # The report and its helpers, `report_*.py`, but not the entry module under test.
        report_module = module == "report" or module.startswith("report_")
        if (
            name.startswith("jms_kapps.evaluation.")
            and report_module
            and module != "report_command"
        ):
            monkeypatch.delitem(sys.modules, name)
            monkeypatch.delattr(package, module, raising=False)
    for name in list(sys.modules):
        if name.startswith(f"{missing}."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, missing, None)

    assert report_command.main(["spec.toml"]) == 2
    error = capsys.readouterr().err
    assert "uv sync --extra evaluation" in error
    assert missing in error
