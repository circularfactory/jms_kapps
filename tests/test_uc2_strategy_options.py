"""The strategies chosen at start, on the command lines: `jms-uc2 --module-strategy` and
`--wms-strategy`, and the same options of the module and WMS processes the Launcher starts.

Each process loads its strategy by its name at start; a name it does not know ends it at start
with a readable reason, before it reads or writes the store, and `jms-uc2` with an unknown name
exits with that reason before anything is cleared. None of these tests reaches a store: the
store's connection variables are taken away from every process they start. (Added on
2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07.)
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from jms_kapps.uc2 import __main__ as jms_uc2
from jms_kapps.uc2.launcher import runner
from jms_kapps.uc2.process import module_parser
from jms_kapps.uc2.vocabulary import module_iri

# The store's connection variables, and their deprecated predecessors, which the store still
# reads, which no process of these tests may see.
STORE_VARIABLES = (
    "TRIPLESTORE_URL",
    "TRIPLESTORE_USERNAME",
    "TRIPLESTORE_PASSWORD",
    "TRIPLESTORE_ALLOW_DESTRUCTIVE",
    "GRAPHDB_URL",
    "GRAPHDB_USERNAME",
    "GRAPHDB_PASSWORD",
    "GRAPHDB_ALLOW_DESTRUCTIVE",
    "INFLUXDB_URL",
    "INFLUXDB_TOKEN",
)
WITHOUT_A_STORE = {k: v for k, v in os.environ.items() if k not in STORE_VARIABLES}

MODULE_REASON = "no module strategy 'nosuch'; the module strategies are real"
WMS_REASON = "no WMS strategy 'nosuch'; the WMS strategies are real"


def started(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=WITHOUT_A_STORE,
        timeout=60,
        check=False,
    )


# -- The processes -------------------------------------------------------------------------- #


def test_a_module_process_takes_its_strategy_by_name_real_by_default():
    parsed = module_parser().parse_args(["--resource-iri", "x"])
    assert parsed.module_strategy == "real"
    parsed = module_parser().parse_args(
        ["--resource-iri", "x", "--module-strategy", "south"]
    )
    assert parsed.module_strategy == "south"


def test_a_module_process_given_an_unknown_name_ends_at_start_with_the_reason():
    done = started(
        sys.executable,
        "-m",
        "jms_kapps.uc2.process",
        "--resource-iri",
        str(module_iri(1)),
        "--dataset",
        "no-dataset",
        "--module-strategy",
        "nosuch",
    )

    assert done.returncode == 1
    assert MODULE_REASON in done.stderr, done.stderr
    assert "Traceback" not in done.stderr


def test_the_wms_process_given_an_unknown_name_ends_at_start_with_the_reason():
    done = started(
        sys.executable,
        "-m",
        "jms_kapps.uc2.wms_process",
        "--dataset",
        "no-dataset",
        "--wms-strategy",
        "nosuch",
    )

    assert done.returncode == 1
    assert WMS_REASON in done.stderr, done.stderr
    assert "Traceback" not in done.stderr


# -- jms-uc2 -------------------------------------------------------------------------------- #


@pytest.fixture
def runs(monkeypatch):
    """Each run of the Launcher that `jms-uc2` starts, with its keyword arguments."""
    started: list[dict] = []

    def run(dataset, **options):
        started.append(options)
        return 0

    monkeypatch.setattr(runner, "run", run)
    return started


def test_jms_uc2_runs_with_real_for_both_unless_told_otherwise(runs):
    assert jms_uc2.main(["--dataset", "repo"]) == 0

    assert (runs[0]["module_strategy"], runs[0]["wms_strategy"]) == ("real", "real")


def test_jms_uc2_passes_the_two_strategies_to_the_run(runs):
    assert (
        jms_uc2.main(
            [
                "--dataset",
                "repo",
                "--module-strategy",
                "real",
                "--wms-strategy",
                "real",
            ]
        )
        == 0
    )

    assert (runs[0]["module_strategy"], runs[0]["wms_strategy"]) == ("real", "real")


@pytest.mark.parametrize(
    ("option", "reason"),
    [("--module-strategy", MODULE_REASON), ("--wms-strategy", WMS_REASON)],
)
def test_jms_uc2_with_an_unknown_name_exits_with_the_reason_and_runs_nothing(
    runs, capsys, option, reason
):
    with pytest.raises(SystemExit) as exited:
        jms_uc2.main([option, "nosuch", "--dataset", "ogm-disposable-jms52"])

    assert exited.value.code != 0
    assert reason in capsys.readouterr().err
    assert runs == []


def test_the_console_script_with_an_unknown_name_exits_before_it_reaches_a_store():
    """As a person runs it. Without the store's variables, a run that went on would fail on its
    first read of the store, with another message."""
    done = started(
        sys.executable,
        "-m",
        "jms_kapps.uc2",
        "--module-strategy",
        "nosuch",
        "--dataset",
        "ogm-disposable-jms52",
    )

    assert done.returncode == 2
    assert MODULE_REASON in done.stderr, done.stderr
    assert "Traceback" not in done.stderr
