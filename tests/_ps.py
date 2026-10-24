"""The processes of this machine as `ps` shows them, for a live test that checks the command line
of each process a run started.

`ps` is told to show each line whole (`-ww`): without it, under pytest, it cuts each line at 80
columns, and a module process's `--resource-iri`, `--dataset` and `--module-strategy` fall off.
A run's processes are found by their parent, the run's own process, which starts each of them.
"""

from __future__ import annotations

import subprocess


def shown_processes() -> list[str]:
    """Every process of this machine, one line each: its pid, its parent's pid, and its command
    line, whole."""
    return subprocess.run(
        ["ps", "-ww", "-eo", "pid=,ppid=,args="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def children_of(parent: int, shown: list[str]) -> list[str]:
    """The command line of each process in `shown` whose parent is `parent`."""
    lines = []
    for line in shown:
        _pid, ppid, args = line.split(maxsplit=2)
        if ppid == str(parent):
            lines.append(args)
    return lines
