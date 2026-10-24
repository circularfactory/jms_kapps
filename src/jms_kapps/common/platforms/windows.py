"""The process supervision's choices on Windows. Read them from `PLATFORM`, never from here.

The three values of Windows itself are written out, so that this module imports on POSIX too,
where a test reads its choices with the platform injected. A test that runs on Windows alone
checks each one against the value that `subprocess` and `signal` define there.
"""

from __future__ import annotations

import _thread
import signal
import subprocess
from collections.abc import Sequence

# `subprocess.CREATE_NEW_PROCESS_GROUP`: the child gets a process group of its own, so that a
# CTRL_BREAK reaches that child alone, and never the launcher.
NEW_PROCESS_GROUP = 0x00000200
# `signal.CTRL_BREAK_EVENT`: the console event a launcher sends to a child's process group.
CTRL_BREAK_EVENT = 1
# `signal.SIGBREAK`: how a Python process receives a CTRL_BREAK.
SIGBREAK = 21

# SIGTERM, as on POSIX, although here only the process itself can raise it; and SIGBREAK, which is
# how a CTRL_BREAK arrives.
STOP_SIGNALS: tuple[int, ...] = (signal.SIGTERM, SIGBREAK)

# Windows has no SIGHUP.
HANGUP: int | None = None

# Address reuse lets a second socket bind a port that one holds already, so the guard against a
# second Launcher would not fire. Without it, the next Launcher still binds the port of one that
# stopped.
REUSE_FIXED_PORT = False


def request_stop(process: subprocess.Popen) -> None:
    """A CTRL_BREAK to the child's process group, which uvicorn handles gracefully.
    `Popen.terminate()` is a forced kill here, which skips the middleware's deregistration.

    Raises:
        OSError: If the event cannot be sent, as when the child has exited meanwhile or the
            launcher has no console.
    """
    process.send_signal(CTRL_BREAK_EVENT)


def command_line(command: Sequence[str]) -> str:
    """`command` as cmd.exe and PowerShell take it: an argument that holds a space in double
    quotes, as the C runtime of a Windows program splits its command line. POSIX quoting would
    put an IRI's `#` in single quotes, which cmd.exe passes on as part of the argument.

    In PowerShell, a line whose program is quoted, because its path holds a space, runs with `&`
    and a space in front of it.
    """
    return subprocess.list2cmdline(command)


def interrupt_main_thread() -> None:
    """The handler of SIGINT, run on the main thread at its next step, as if the signal arrived
    there (`_thread.interrupt_main`). Windows cannot send a signal to one thread, so a call that
    waits there may not be cut short: the handler then runs once the call returns. It serves the
    run limit of the measurement harness."""
    _thread.interrupt_main(signal.SIGINT)
