"""A terminal that a test starts a process in, and closes as a person closes theirs.

It is a pseudo-terminal: the process writes to one end as to a person's terminal, and the test
reads every line at the other. Closing the terminal closes the test's end. From then on every
write of the process to its end fails (EIO), as after a person closed the window; the test then
sends SIGHUP to the process group, as the shell of a closed terminal does. POSIX only: a test
that needs SIGHUP or this terminal carries `POSIX_ONLY`.
"""

from __future__ import annotations

import os
import select
import threading

import pytest

# The marker of a test that needs SIGHUP, which a closed terminal sends, or this terminal.
POSIX_ONLY = pytest.mark.posix(reason="Windows has no SIGHUP and no pseudo-terminal")


class Terminal:
    """A pseudo-terminal, and every line written to it so far. Pass `end` as the process's
    stdout, and call `started` once the process holds it."""

    def __init__(self) -> None:
        self._own, self.end = os.openpty()
        self.lines: list[str] = []
        self._closing = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        pending = b""
        # A read never blocks: the terminal closes only once no read is under way, or else the
        # read would hold it open.
        while not self._closing.is_set():
            ready, _, _ = select.select([self._own], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self._own, 4096)
            except OSError:  # every process that wrote to it has gone
                return
            if not data:
                return
            pending += data
            *complete, pending = pending.split(b"\n")
            self.lines.extend(
                line.decode(errors="replace").rstrip("\r") for line in complete
            )

    def started(self) -> None:
        """Close this process's copy of the process's end: the process holds its own."""
        os.close(self.end)

    def close(self) -> None:
        """Close the terminal: every write to it fails from now on. A second close does
        nothing."""
        if self._closing.is_set():
            return
        self._closing.set()
        self._reader.join(timeout=5)
        os.close(self._own)
