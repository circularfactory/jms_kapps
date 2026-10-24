"""`_ps`, the helper with which the live run of the strategies reads the command line of each
process the Launcher started.

Under pytest, `ps` cuts each line at 80 columns unless it is told otherwise, so a module process's
`--resource-iri`, `--dataset` and `--module-strategy` fell off its line, and the live run found
no module process at all. Here a stand-in parent in a session of its own starts one child with a
long command line, as `jms-uc2` starts a module, and the helper finds the child's whole line.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator

import pytest
from _ps import children_of, shown_processes
from _wait import eventually

pytestmark = pytest.mark.posix(reason="reads the processes with ps")

# A child's command line longer than 80 columns, which ends with what the live run checks.
CHILD_ARGS = [
    "-c",
    "import time; time.sleep(60)",
    "-m",
    "jms_kapps.uc2.process",
    "--resource-iri",
    "http://w3id.org/circularfactory/FlexConveyorInstances#Module30",
    "--dataset",
    "ogm-disposable-jms52",
    "--module-strategy",
    "real",
]
PARENT = (
    "import subprocess, sys, time\n"
    f"subprocess.Popen([sys.executable, *{CHILD_ARGS!r}])\n"
    "time.sleep(60)\n"
)


@pytest.fixture
def parent() -> Iterator[subprocess.Popen[bytes]]:
    """A stand-in for `jms-uc2`: a process in a session of its own with one long-lined child."""
    process = subprocess.Popen([sys.executable, "-c", PARENT], start_new_session=True)
    try:
        yield process
    finally:
        subprocess.run(["pkill", "-KILL", "-s", str(process.pid)], check=False)
        process.wait()


def test_the_child_s_whole_command_line_is_found_by_its_parent(parent):
    eventually(lambda: children_of(parent.pid, shown_processes()) != [], timeout=10)

    [line] = children_of(parent.pid, shown_processes())

    assert line.endswith(
        "--resource-iri http://w3id.org/circularfactory/FlexConveyorInstances#Module30 "
        "--dataset ogm-disposable-jms52 --module-strategy real"
    ), line


def test_a_process_with_no_children_has_none(parent):
    assert children_of(sys.maxsize, shown_processes()) == []
