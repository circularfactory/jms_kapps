"""The command line of the monitor's process: the mode it starts in. It is "graph only"
unless the Launcher says otherwise, so a monitor started by hand matches a monitor the Launcher
started without its box for the refusals.
"""

from __future__ import annotations

import pytest

from jms_kapps.uc2.monitor import process
from jms_kapps.uc2.monitor.memory import GRAPH_AND_STREAMS, GRAPH_ONLY


def test_the_command_line_names_no_mode_of_its_own():
    assert "--mode" not in process.monitor_command("repo")


@pytest.fixture
def started(monkeypatch) -> list[tuple]:
    """What `main` builds the monitor with, as `run_process` calls its factory."""
    builds: list[tuple] = []

    def build(dataset, host, port, mode=GRAPH_ONLY):
        builds.append((dataset, host, port, mode))

    monkeypatch.setattr(process, "build", build)
    monkeypatch.setattr(
        process, "run_process", lambda host, factory, announce: factory(8123)
    )
    return builds


def test_a_monitor_started_by_hand_starts_in_graph_only(monkeypatch, started):
    monkeypatch.setattr("sys.argv", ["monitor", "--dataset", "repo"])

    process.main()

    assert started == [("repo", "127.0.0.1", 8123, GRAPH_ONLY)]


def test_a_monitor_started_with_the_mode_graph_and_streams_starts_in_it(
    monkeypatch, started
):
    monkeypatch.setattr(
        "sys.argv", ["monitor", "--dataset", "repo", "--mode", GRAPH_AND_STREAMS]
    )

    process.main()

    assert started == [("repo", "127.0.0.1", 8123, GRAPH_AND_STREAMS)]


def test_a_monitor_refuses_a_mode_it_does_not_have(monkeypatch, started):
    monkeypatch.setattr("sys.argv", ["monitor", "--mode", "everything"])

    with pytest.raises(SystemExit):
        process.main()
