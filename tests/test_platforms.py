"""The one platform switch of the process supervision, and what it chooses on Windows and on POSIX.

`jms_kapps.common.platforms` holds one module for each platform, and it is the only place that
reads which platform this is. A test of a Windows choice that can run on POSIX takes the Windows
module from the switch, with the platform injected. What only Windows can show, a real CTRL_BREAK,
cmd.exe and PowerShell, is in the tests marked `windows`, which run on Windows alone. The tests
marked `posix` need what only POSIX has, as SIGHUP and a POSIX shell.
"""

from __future__ import annotations

import json
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from _source import SOURCE, sources

from jms_kapps.common import platforms, process
from jms_kapps.common.platforms import PLATFORM, for_platform, windows

WINDOWS = for_platform("win32")
HOST = "127.0.0.1"

# A child that prints the arguments it was given, as JSON, so that a test sees what a shell made
# of a printed command line.
PRINTS_ITS_ARGUMENTS = "import json, sys; print(json.dumps(sys.argv[1:]))"


class Asked:
    """A child process that records what a stop asks of it, in place of `subprocess.Popen`."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_signal(self, sig: int) -> None:
        self.calls.append(("send_signal", sig))

    def terminate(self) -> None:
        self.calls.append(("terminate",))

    def kill(self) -> None:
        self.calls.append(("kill",))


def free_port() -> int:
    """A port that no socket holds now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def test_the_switch_takes_the_windows_module_on_windows_and_the_posix_module_elsewhere():
    assert WINDOWS is windows
    assert PLATFORM is for_platform(sys.platform)


@pytest.mark.posix
@pytest.mark.parametrize("name", ["linux", "darwin"])
def test_every_platform_but_windows_takes_the_posix_module(name):
    """The POSIX module imports only where POSIX's signals exist."""
    assert for_platform(name).__name__ == "jms_kapps.common.platforms.posix"


# What only a platform module may name: which platform this is, and the way of one platform.
PLATFORM_READS = re.compile(r"\bsys\.platform\b|\bos\.name\b|\bplatform\.system\b")
ONE_PLATFORMS_WAY = re.compile(
    r"\bCREATE_NEW_PROCESS_GROUP\b|\bCTRL_BREAK_EVENT\b|\bSIGBREAK\b|\blist2cmdline\b"
    r"|\bshlex\b|\bpthread_kill\b|\binterrupt_main\b"
)
SWITCH = SOURCE / "common" / "platforms" / "__init__.py"


@pytest.mark.parametrize(
    "path", sources(), ids=lambda path: str(path.relative_to(SOURCE))
)
def test_only_the_switch_reads_the_platform_and_only_a_platform_module_names_its_way(
    path,
):
    """The new-process-group flag, the stop signal, the signals that stop a launcher, the form of
    a printed command line and how another thread interrupts the main thread are chosen once, in
    one module per platform, and every user reads them from the switch."""
    text = path.read_text(encoding="utf-8")
    if path != SWITCH:
        assert not PLATFORM_READS.search(text), PLATFORM_READS.search(text)
    if path.parent != SWITCH.parent:
        assert not ONE_PLATFORMS_WAY.search(text), ONE_PLATFORMS_WAY.search(text)


def test_the_switch_is_among_the_sources_scanned():
    assert SWITCH in sources()
    assert platforms.__file__ == str(SWITCH)


def test_on_windows_a_child_has_a_process_group_of_its_own_and_a_stop_is_a_ctrl_break():
    """A forced kill is wrong, because it skips the middleware's deregistration, and on Windows
    `terminate()` is one. A CTRL_BREAK reaches the child's process group alone, and Python
    receives it as SIGBREAK, which uvicorn takes as a stop."""
    child = Asked()

    WINDOWS.request_stop(child)

    assert child.calls == [("send_signal", windows.CTRL_BREAK_EVENT)]
    assert windows.CTRL_BREAK_EVENT == 1
    assert WINDOWS.NEW_PROCESS_GROUP == 0x00000200
    assert WINDOWS.STOP_SIGNALS == (signal.SIGTERM, windows.SIGBREAK)
    assert windows.SIGBREAK == 21
    assert WINDOWS.HANGUP is None


@pytest.mark.posix
def test_on_posix_a_child_shares_the_launchers_process_group_and_a_stop_is_sigterm():
    child = Asked()
    posix = for_platform("linux")

    posix.request_stop(child)

    assert child.calls == [("terminate",)]
    assert posix.NEW_PROCESS_GROUP == 0
    assert posix.STOP_SIGNALS == (signal.SIGTERM,)
    assert posix.HANGUP == signal.SIGHUP


@pytest.mark.windows
def test_the_values_the_windows_module_writes_out_are_windows_own():
    """Written out, so that the Windows module imports on POSIX too."""
    assert subprocess.CREATE_NEW_PROCESS_GROUP == WINDOWS.NEW_PROCESS_GROUP
    assert signal.CTRL_BREAK_EVENT == windows.CTRL_BREAK_EVENT
    assert signal.SIGBREAK == windows.SIGBREAK


# A module's command line as `jms-uc2` prints it on Windows, from an interpreter in a project's
# environment: its IRI holds a `#`, which POSIX quoting puts in single quotes.
WINDOWS_COMMAND = [
    r"C:\Users\someone\jms_kapps\.venv\Scripts\python.exe",
    "-m",
    "jms_kapps.uc2.process",
    "--resource-iri",
    "http://w3id.org/circularfactory/FlexConveyorInstances#Module1",
    "--dataset",
    "jms-kapps",
    "--retry-time",
    "1.0",
]


def test_the_windows_form_of_a_command_line_has_no_posix_quotes():
    """cmd.exe passes single quotes on as part of the argument, so the module would get an IRI
    that starts with a quote. The Windows form quotes only what holds a space, with double
    quotes, as the C runtime of a Windows program splits its command line."""
    assert WINDOWS.command_line(WINDOWS_COMMAND) == (
        r"C:\Users\someone\jms_kapps\.venv\Scripts\python.exe -m jms_kapps.uc2.process "
        "--resource-iri http://w3id.org/circularfactory/FlexConveyorInstances#Module1 "
        "--dataset jms-kapps --retry-time 1.0"
    )
    assert WINDOWS.command_line(
        [r"C:\Program Files\Python\python.exe", "a b", 'say "hi"']
    ) == (r'"C:\Program Files\Python\python.exe" "a b" "say \"hi\""')


# Arguments a shell would read in a way of its own, if they were not quoted.
AWKWARD = [
    "http://w3id.org/circularfactory/FlexConveyorInstances#Module1",
    "a b",
    "it's",
    'say "hi"',
    "$HOME",
    "",
]


@pytest.mark.posix
def test_a_printed_command_line_runs_when_pasted_into_a_posix_shell():
    line = for_platform("linux").command_line(
        [sys.executable, "-c", PRINTS_ITS_ARGUMENTS, *AWKWARD]
    )

    pasted = subprocess.run(
        ["/bin/sh", "-c", line], capture_output=True, text=True, check=True, timeout=30
    )

    assert json.loads(pasted.stdout) == AWKWARD


# What a module's command line holds on Windows: an IRI with a `#`, a name with a dash, a number.
ON_WINDOWS = [
    "http://w3id.org/circularfactory/FlexConveyorInstances#Module1",
    "jms-kapps",
    "1.0",
    "a b",
]


@pytest.mark.windows
def test_a_printed_command_line_runs_when_pasted_into_cmd_exe():
    """`cmd.exe /s /c "<line>"` runs the line as typed at its prompt."""
    line = PLATFORM.command_line(
        [sys.executable, "-c", PRINTS_ITS_ARGUMENTS, *ON_WINDOWS]
    )

    pasted = subprocess.run(
        f'cmd.exe /d /s /c "{line}"',
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert json.loads(pasted.stdout) == ON_WINDOWS


@pytest.mark.windows
def test_a_printed_command_line_runs_when_pasted_into_powershell():
    """PowerShell reads the line from its input, as typed at its prompt."""
    line = PLATFORM.command_line(
        [sys.executable, "-c", PRINTS_ITS_ARGUMENTS, *ON_WINDOWS]
    )

    pasted = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "-"],
        input=line + "\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    (printed,) = [out for out in pasted.stdout.splitlines() if out.startswith("[")]
    assert json.loads(printed) == ON_WINDOWS


def test_a_second_launcher_on_the_fixed_port_is_refused():
    """The guard against a second Launcher: the fixed port takes one socket, on every platform."""
    port = free_port()
    first = process.bind_socket(HOST, port)
    try:
        with pytest.raises(OSError):
            process.bind_socket(HOST, port)
    finally:
        first.close()


def reuses_its_address(sock: socket.socket) -> bool:
    return sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0


def test_on_windows_only_a_port_the_system_chose_is_bound_with_address_reuse(
    monkeypatch,
):
    """On Windows, address reuse lets a second socket bind a port that one holds, so the guard
    against a second Launcher would not fire. A port the system chose is free whatever the
    option says, so it keeps it, as the middleware's factory demo binds it."""
    monkeypatch.setattr(process, "PLATFORM", WINDOWS)

    fixed = process.bind_socket(HOST, free_port())
    chosen = process.bind_socket(HOST)
    try:
        assert not reuses_its_address(fixed)
        assert reuses_its_address(chosen)
    finally:
        fixed.close()
        chosen.close()


def test_a_launcher_that_stopped_leaves_the_fixed_port_free_for_the_next_one():
    """The Launcher's page closes its connections when it stops, and the closed connections wait
    a while in the system before their port is free again. On POSIX, only address reuse lets the
    next Launcher bind the port meanwhile; there a second socket still cannot bind it while the
    first holds it. On Windows, the next Launcher binds it without."""
    port = free_port()
    first = process.bind_socket(HOST, port)
    client = socket.create_connection((HOST, port), timeout=5)
    accepted, _ = first.accept()
    accepted.close()  # the server closes first, as a stopping Launcher does
    client.close()
    first.close()

    process.bind_socket(HOST, port).close()


@pytest.mark.posix
def test_with_the_windows_module_a_hangup_changes_nothing(monkeypatch):
    """Windows has no SIGHUP: with the Windows module, `hangup_stops` sets no handler."""
    monkeypatch.setattr(process, "PLATFORM", WINDOWS)
    before = signal.getsignal(signal.SIGHUP)

    with process.hangup_stops(lambda signum, frame: None):
        assert signal.getsignal(signal.SIGHUP) is before


class Interrupted(Exception):
    """What the handler of SIGINT raises in the tests of `interrupt_main_thread`."""


@contextmanager
def interrupted_after(seconds: float, interrupt: Callable[[], None]) -> Iterator[None]:
    """While in this block, SIGINT raises `Interrupted`, and another thread calls `interrupt`
    after `seconds`. At the end of the block, the block waits until that thread has ended, and
    then puts back the handler that SIGINT had before."""

    def handler(signum: int, frame: object) -> None:
        raise Interrupted(signum)

    previous = signal.signal(signal.SIGINT, handler)
    later = threading.Timer(seconds, interrupt)
    later.start()
    try:
        yield
    finally:
        later.join()
        signal.signal(signal.SIGINT, previous)


@pytest.mark.posix(reason="sends SIGINT to the main thread")
def test_on_posix_another_thread_ends_a_wait_of_the_main_thread_with_its_sigint_handler():
    """The run limit's way (jms_kapps 106): SIGINT to the main thread alone, whose handler runs
    there and ends a wait that would not end by itself for a long time."""
    began = time.monotonic()

    with (
        pytest.raises(Interrupted),
        interrupted_after(0.1, PLATFORM.interrupt_main_thread),
    ):
        threading.Event().wait(30)

    assert time.monotonic() - began < 10


def test_with_the_windows_module_the_handler_of_sigint_runs_on_the_main_thread_at_its_next_step():
    """Windows cannot send a signal to one thread: the handler runs on the main thread between
    two of its steps, here two short sleeps."""
    began = time.monotonic()

    with (
        pytest.raises(Interrupted),
        interrupted_after(0.1, WINDOWS.interrupt_main_thread),
    ):
        while time.monotonic() - began < 10:
            time.sleep(0.01)

    assert time.monotonic() - began < 10
