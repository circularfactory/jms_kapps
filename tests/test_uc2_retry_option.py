"""How long a module waits before it asks an occupied or reserved module's Reserve again: set per
run, from `jms-uc2` through the Launcher's command line of each module process."""

from __future__ import annotations

import pytest

from jms_kapps.uc2 import __main__ as jms_uc2
from jms_kapps.uc2.launcher import runner
from jms_kapps.uc2.module import DEFAULT_RETRY_SECONDS
from jms_kapps.uc2.process import module_command, module_parser
from jms_kapps.uc2.vocabulary import module_iri


def parsed(command: list[str]):
    """What the module process reads from `command`, the command line that starts it."""
    assert command[1:3] == ["-m", "jms_kapps.uc2.process"]
    return module_parser().parse_args(command[3:])


def test_the_module_command_line_carries_the_retry_time_to_the_process():
    command = module_command(module_iri(1), "repo", retry_seconds=0.1)

    assert parsed(command).retry_time == 0.1


def test_the_module_command_line_carries_no_transit_time_the_graph_is_the_one_source():
    command = module_command(module_iri(1), "repo")

    assert "--transit-time" not in command


def test_a_module_waits_one_second_by_default():
    assert DEFAULT_RETRY_SECONDS == 1.0
    assert parsed(module_command(module_iri(1), "repo")).retry_time == 1.0
    assert module_parser().parse_args(["--resource-iri", "x"]).retry_time == 1.0


@pytest.fixture
def runs(monkeypatch):
    """Each run of the Launcher that `jms-uc2` starts, with its keyword arguments."""
    started: list[dict] = []

    def run(dataset, **options):
        started.append(options)
        return 0

    monkeypatch.setattr(runner, "run", run)
    return started


def test_jms_uc2_passes_the_retry_time_to_the_launcher(runs):
    assert jms_uc2.main(["--dataset", "repo", "--retry-time", "0.1"]) == 0

    assert runs[0]["retry_seconds"] == 0.1


def test_jms_uc2_leaves_the_retry_time_to_the_processes_unless_it_is_given(runs):
    jms_uc2.main(["--dataset", "repo"])

    assert "retry_seconds" not in runs[0]


@pytest.mark.parametrize("seconds", ["0", "-1"])
def test_jms_uc2_refuses_a_retry_time_that_is_not_positive(runs, seconds, capsys):
    with pytest.raises(SystemExit):
        jms_uc2.main(["--retry-time", seconds])

    assert "--retry-time must be positive" in capsys.readouterr().err
    assert runs == []
