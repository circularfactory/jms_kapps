"""The one place that chooses what the process supervision does differently on Windows and on POSIX.

Each platform has one module, `windows` and `posix`, and this switch is the only code that reads
which platform this is. Every user reads the choice from `PLATFORM`:

- **How a launcher asks a child to stop.** A forced kill is wrong, because it skips the
  middleware's deregistration. On POSIX the stop is SIGTERM. On Windows `Popen.terminate()` is a
  forced kill, so the stop is a CTRL_BREAK, which Python receives as SIGBREAK and uvicorn takes as
  a stop. Never SIGINT: a process that stopped cleanly exits with code 0 on every OS, whichever
  signal began its stop, so SIGINT adds nothing, and on Windows it cannot be sent to one process.
  Every process takes every stop signal as one stop (`one_stop`), so a second one, from a second
  Ctrl+C or a supervisor, never cuts the stop short: uvicorn's own handler forces its exit on a
  second Ctrl+C, which skips the lifespan shutdown and with it the middleware's deregistration.
- **The process group of a child.** On Windows each child gets one of its own, so that a
  CTRL_BREAK reaches that child alone, and never the launcher.
- **The signals that stop a launcher as Ctrl+C does**, and SIGHUP, which a closed terminal sends
  and Windows has not.
- **Whether a fixed port is bound with address reuse.**
- **How a command line is printed**, so that it runs when pasted into the platform's shell.
- **How another thread interrupts the main thread** as a stop signal does: on POSIX a SIGINT to
  the main thread alone, which also cuts short a call that waits there; on Windows, which cannot
  send a signal to one thread, the handler of SIGINT run at the main thread's next step (for the
  run limit of the measurement harness).

The pattern is the Launcher of the middleware's transferunits demo.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from typing import Protocol


class Platform(Protocol):
    """What one platform module chooses. Each of `windows` and `posix` is one."""

    # The creation flags of each child's process.
    NEW_PROCESS_GROUP: int
    # The signals that stop a launcher as Ctrl+C does.
    STOP_SIGNALS: tuple[int, ...]
    # SIGHUP, which a closed terminal sends, or None where there is none.
    HANGUP: int | None
    # Whether a fixed port, as the Launcher's page's, is bound with address reuse. A port the
    # system chooses is bound with it on every platform.
    REUSE_FIXED_PORT: bool

    def request_stop(self, process: subprocess.Popen) -> None:
        """Ask `process` to stop, so that it runs its shutdown. Raises OSError if the signal
        cannot be sent."""
        ...

    def command_line(self, command: Sequence[str]) -> str:
        """`command` as one line that runs when pasted into the platform's shell."""
        ...

    def interrupt_main_thread(self) -> None:
        """From another thread, run this process's handler of SIGINT on the main thread, as a
        Ctrl+C does: the handler may raise there, between two steps of whatever runs there.
        This serves the run limit of the measurement harness."""
        ...


def for_platform(name: str) -> Platform:
    """The module of the platform that `sys.platform` calls `name`: Windows' for "win32", and
    POSIX's for every other. Only the module chosen is imported: the POSIX module names signals
    that Windows does not have."""
    if name == "win32":
        from jms_kapps.common.platforms import windows

        return windows
    from jms_kapps.common.platforms import posix

    return posix


# The platform this process runs on.
PLATFORM = for_platform(sys.platform)
