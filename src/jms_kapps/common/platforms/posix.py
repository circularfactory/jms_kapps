"""The process supervision's choices on POSIX. Read them from `PLATFORM`, never from here."""

from __future__ import annotations

import asyncio
import shlex
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence

# A child shares the launcher's process group, so that a terminal's Ctrl+C or SIGHUP reaches it
# as it reaches the launcher.
NEW_PROCESS_GROUP = 0

# SIGTERM, which `kill` sends.
STOP_SIGNALS: tuple[int, ...] = (signal.SIGTERM,)

HANGUP: int | None = signal.SIGHUP

# Address reuse lets the next Launcher bind the port of one that stopped, while the connections it
# closed still wait in the system; without it the port stays taken for up to a minute. A second
# socket still cannot bind a port that one holds.
REUSE_FIXED_PORT = True

# asyncio's default loop watches sockets for reading and writing, as an MQTT client needs.
EVENT_LOOP_FACTORY: Callable[[], asyncio.AbstractEventLoop] | None = None


def request_stop(process: subprocess.Popen) -> None:
    """SIGTERM, which uvicorn handles gracefully.

    Raises:
        OSError: If the signal cannot be sent.
    """
    process.terminate()


def command_line(command: Sequence[str]) -> str:
    """`command` as a POSIX shell takes it."""
    return shlex.join(command)


def interrupt_main_thread() -> None:
    """SIGINT to the main thread alone: no other process and no other thread gets it. Its handler
    runs on the main thread, and a call that waits there, as a sleep, a lock or a read of a
    socket, is cut short: Python goes on waiting only where the handler returns, and a handler
    that raises ends the wait with its exception. It serves the run limit of the measurement
    harness."""
    main = threading.main_thread().ident
    assert main is not None, "the main thread has started"
    signal.pthread_kill(main, signal.SIGINT)
