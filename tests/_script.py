"""A Python process that a test starts, and every line it prints.

The tests of how a process starts and stops run the real code in a Python process of their own:
a signal reaches only the main thread of the process it is sent to, and the shared process start
sets up the logging of its whole process.
"""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest
from _terminal import Terminal
from _wait import eventually

# How long a Python process of these tests may take to start, to print, and to answer.
WITHIN_SECONDS = 30.0


class Script:
    """A Python process that runs `source`, and every line it has printed so far.

    In `terminal`, if one is given, it prints to that terminal, in a session of its own, so that
    a signal to its process group reaches it alone, as a closed terminal's SIGHUP does.
    """

    def __init__(
        self, source: str, *args: str, terminal: Terminal | None = None
    ) -> None:
        command = [sys.executable, "-c", source, *args]
        if terminal is not None:
            self.process = subprocess.Popen(
                command,
                stdout=terminal.end,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            terminal.started()
            self.lines = terminal.lines
            return
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.lines = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))

    def printed(self, text: str) -> bool:
        return any(line.startswith(text) for line in self.lines)

    def port(self) -> int:
        """The port the process printed, once it has."""
        eventually(lambda: self.printed("port "), timeout=WITHIN_SECONDS)
        return int(next(line for line in self.lines if line.startswith("port "))[5:])

    def wait(self, timeout: float) -> int:
        """The exit code, once the process has exited. Fails, with its output, if it has not."""
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"the process did not exit within {timeout:g} s:\n"
                + "\n".join(self.lines)
            )

    def end(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
