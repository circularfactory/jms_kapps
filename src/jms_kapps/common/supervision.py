"""Starting, watching and stopping the processes of a use case, one per resource.

A launcher spawns each child through a `Supervisor`, which keeps one handle per child, keyed by
the child's resource IRI. The supervisor prints each child's command line, in the form of the
platform's shell, so the child can be run on its own, and echoes every line the child writes with
the child's name in front. It reads each child's state from the child's service in the graph,
through the one reader of a service (`common.services`): a child is live once its service is live
and has registered every workflow the child registers, so that a caller can call any of them at
once. It finds a child that exited without reading the graph, so that a read that hangs does not
hide the exit. Its readiness gate, `wait_until_ready`, waits until every child is live. When a
child turns `failed`, it prints one line with the child's name and why, the exit code if its
process exited, because a launcher without a page, as the bench launcher, shows nothing else. The
pattern is the Launcher of the middleware's transferunits demo.

A stop asks each child to stop, and the child deregisters its service in its own shutdown. A
child that the stop kills at the stop timeout, or that exited without a stop, as a crash does,
skipped that shutdown, and its service stays registered until its heartbeat is stale: see
`may_not_have_deregistered`. A stop signal that fails is recorded, and the stop goes on to every
later child. The reference page on stopping, `docs/reference/stopping.md`, lists every case that
cannot deregister, with why. How a child is asked to stop, its process group and the form of its
printed command line are the platform's (`jms_kapps.common.platforms`). The process states, and the sets
of them that a launcher asks about, are defined here and nowhere else.

A launcher clears the dataset it seeds, so it first checks that no other process's service is
live there: `refuse_if_live`, the one live-instance guard, which leaves out the resources of the
launcher's own processes that a stop killed or that exited on their own.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import Literal

from kapps_triplestore_interface import IRI

from jms_kapps.common.platforms import PLATFORM
from jms_kapps.common.services import (
    MAX_HEARTBEAT_AGE_SECONDS,
    Service,
    ServiceReader,
)

OUTPUT_LINES = 20
WATCH_INTERVAL_SECONDS = 1.0

# A middleware registers its service and its workflows as it starts, so a child that has not
# turned live this long has failed.
START_TIMEOUT_SECONDS = 60.0

# How long a stop waits for a child to exit before it kills the child. Every launcher's default.
# A child's own graceful shutdown must be bounded below it, or a kill cuts its deregistration.
STOP_TIMEOUT_SECONDS = 10

# The process states, here and nowhere else. A child is `starting` until its service is ready: live,
# with an address and a fresh heartbeat, and with every workflow the child registers. It is `live`
# while it stays so. It is `failed` when it exits without being asked, when its service stops
# being ready, or when it has not turned live within the start timeout; one that still runs turns
# live again once its service is ready. `stopped` is a child that exited when a stop asked it to,
# so it deregistered its service first. `killed` is a stop that ended in a kill at the stop
# timeout. It is not `stopped`, because a kill skips the deregistration: the service keeps its
# address, and it counts as live until its last heartbeat is `MAX_HEARTBEAT_AGE_SECONDS` old. A
# child that exits with code 0 once the stop has begun (`Supervisor.begin_stop`), before the stop
# asks it, is `stopped` too: a stop signal that reached every process of the launcher's process
# group ended it, as a terminal's Ctrl+C does. Any other child that had exited before a stop asked
# it stays `failed` when the stop reaches it: its exit skipped the deregistration too. (Changed on
# 2026-10-10, jms_kapps 70, after the orchestrator's live run of the Launcher: a Ctrl+C to the
# process group printed a "failed" line for each process. Until then, in place of the last two
# sentences, it read "A child that had exited before a stop asked it stays `failed` when the stop
# reaches it: its exit skipped the deregistration too.")
State = Literal["starting", "live", "failed", "stopped", "killed"]

# The states at which the readiness gate stops waiting: the child will not turn live by waiting,
# or a stop has reached it. The other sets that a launcher asks about rest on the process as well
# as on its state, and are `_Child`'s: whether it runs, whether a stop would still ask it, and
# whether it may have left its service live.
WILL_NOT_TURN_LIVE: frozenset[State] = frozenset({"failed", "stopped", "killed"})

# A state that rests on the reads of the graph is unconfirmed once the latest read of them failed,
# or once no read has succeeded for this long: a read can hang until the client's own timeout
# gives up.
UNCONFIRMED_AFTER_SECONDS = 5 * WATCH_INTERVAL_SECONDS

# How often the readiness gate reads the graph while it waits.
READY_INTERVAL_SECONDS = 0.5


class LiveInstanceError(RuntimeError):
    """A service is live in the dataset a launch would clear, and no process of this launcher
    left it there."""


class LaunchFailed(RuntimeError):
    """A launch stopped part-way, or could not read which services are live. Whatever it had
    started is stopped again."""


class NotReady(RuntimeError):
    """A child did not turn live: it failed, a stop reached it, or the readiness gate's wait ran
    out."""


def display_names(resources: Iterable[IRI]) -> str:
    """`resources` as a person reads them, each by its fragment, sorted and joined by commas.

    For display only. The resource of another process need not have a fragment, because this
    package does not choose its IRI, so such a one is named whole: a refusal never names nobody.
    """
    return ", ".join(sorted(r.fragment or str(r) for r in resources))


def _live_refusal(live: Collection[IRI], dataset: str) -> str:
    """Why a launch did not clear `dataset`, for a person to read."""
    names, one = display_names(live), len(live) == 1
    return (
        f"{names} {'carries' if one else 'carry'} a fresh heartbeat in {dataset}, so nothing "
        f"was cleared. No process of this launcher serves {'it' if one else 'them'}: another "
        "launcher runs there, or a process runs on its own from its command line. Stop that "
        "first. A process that was killed counts until its last heartbeat is "
        f"{MAX_HEARTBEAT_AGE_SECONDS:g} s old."
    )


def refuse_if_live(
    services: ServiceReader,
    *,
    dataset: str,
    leave_out: Collection[IRI] = (),
    force: bool = False,
) -> None:
    """The live-instance guard: refuse to clear `dataset` while a service there is live.

    A clear cannot be undone, so a launch clears only while no other process's service is live in
    the dataset: another launcher's, or that of a process run on its own from its printed
    command line, even on a resource this launcher stopped cleanly.

    Args:
        services: Reads the dataset's live services.
        dataset: The dataset's name, for the refusal.
        leave_out: The resources of this launcher's own processes that may not have deregistered,
            because a stop killed them or they had exited on their own. Their services are this
            launcher's own, and the seed clears what they left.
        force: Go on whatever is live. Nothing is read then.

    Raises:
        LiveInstanceError: If another service is live, naming each live resource.
        LaunchFailed: If the graph cannot say which services are live.
    """
    if force:
        return
    try:
        live = {service.resource for service in services(())} - set(leave_out)
    # Whatever the reader raises: the launch must not clear what it could not check.
    except Exception as error:
        raise LaunchFailed(
            f"could not read which services are live: {error}"
        ) from error
    if live:
        raise LiveInstanceError(_live_refusal(live, dataset))


def _print(line: str) -> None:
    """Print `line` on stdout. A terminal that has closed takes no line: the write fails, and the
    line is dropped. The child's output is still read, so that its pipe never fills and blocks
    it, and the stop that the terminal's SIGHUP began reaches every child."""
    with contextlib.suppress(OSError):
        print(line, flush=True)


@dataclass
class _Child:
    resource: IRI
    name: str
    # The command line as the supervisor printed it.
    command: str
    process: subprocess.Popen
    # The classes of the workflows the child registers: it is live only once its service has them.
    workflows: frozenset[IRI] = frozenset()
    state: State = "starting"
    started_at: float = field(default_factory=time.monotonic)
    stopping: bool = False
    # Whether a stop asked the child to stop while it ran. One that had exited was not asked.
    asked_to_stop: bool = False
    output: deque[str] = field(default_factory=lambda: deque(maxlen=OUTPUT_LINES))
    # The address of the live service, kept from the read that found the child live.
    address: str | None = None
    # When the state was last confirmed: at the spawn, or by a read of the graph.
    confirmed_at: float = field(default_factory=time.monotonic)
    # Whether its process was found to have exited unasked: its state was set then, once.
    exit_found: bool = False

    def runs(self) -> bool:
        """Whether its process has not exited."""
        return self.process.poll() is None

    def stoppable(self) -> bool:
        """Whether a stop would still ask it to stop: it runs, and no stop has reached it. Its
        state is then the last one read from the graph, which can go unconfirmed. The state of one
        that is not is this process's own knowledge: an exit, a stop or a kill."""
        return not self.stopping and self.runs()

    def may_not_have_deregistered(self) -> bool:
        """Whether it may have left its service live: a stop killed it, or it exited with no stop
        asking it to. Either skipped the deregistration that a stop asks for. One that still runs
        has not, whatever its state says: a stop asks it, and it deregisters as it goes. Nor has
        one that exited when it was asked, nor one that is `stopped` because it exited with code 0
        once the stop had begun (`Supervisor.begin_stop`)."""
        return self.state == "killed" or not (
            self.runs() or self.asked_to_stop or self.state == "stopped"
        )

    def ready_in(self, found: Iterable[Service]) -> Service | None:
        """Its live service among `found` that has registered every workflow it registers: the
        newest one, as `found` comes newest heartbeat first, or None while there is none."""
        return next(
            (
                service
                for service in found
                if service.resource == self.resource
                and self.workflows <= service.workflows.keys()
            ),
            None,
        )

    def fail(self, why: str) -> str | None:
        """Turn it `failed` for `why`, and forget its address. Returns the one line a supervisor
        prints of the turn, with its name and why, or None if it was `failed` already."""
        turned = self.state != "failed"
        self.state, self.address = "failed", None
        return f"{self.name} failed: {why}." if turned else None

    def exit_reason(self) -> str:
        """Why it failed, once its process exited with no stop asking it to: its exit code."""
        return f"its process exited with code {self.process.returncode}"


@dataclass(frozen=True)
class ChildSnapshot:
    """One child as the supervisor holds it at one moment. What `Supervisor.snapshot` returns.

    `command` is the child's command line as one string, as the supervisor printed it. `address`
    is its live service's address, kept from the read that found it live. `unconfirmed_for` is
    how many seconds ago a state that rests on the reads of the graph was last confirmed, once it
    counts as unconfirmed. It is None while the reads succeed, and for a state this process knows
    by itself: an exit, a stop or a kill. `stoppable` is whether a stop would still ask the process
    to stop: it runs, and no stop has reached it yet. A child that has exited is not, whatever
    its state says.
    """

    resource: IRI
    name: str
    state: State
    pid: int
    command: str
    output: list[str]
    address: str | None
    unconfirmed_for: float | None
    stoppable: bool


class Supervisor:
    """The children of one launcher, and what the launcher knows about each.

    Args:
        services: Reads the live services, each with its workflows of the classes it is given:
            `common.services.reader` over the dataset the children run against.
        echo: Where the command lines and the children's output go. The launcher's stdout if
            None.
        stop_timeout: How long a stop waits for a child to exit before it kills the child.
        start_timeout: How long a child may start before it counts as failed.
    """

    def __init__(
        self,
        services: ServiceReader,
        *,
        echo: Callable[[str], None] | None = None,
        stop_timeout: float = STOP_TIMEOUT_SECONDS,
        start_timeout: float = START_TIMEOUT_SECONDS,
    ) -> None:
        self._services = services
        self._heartbeat_error: str | None = None
        self._echo = echo if echo is not None else _print
        self._stop_timeout = stop_timeout
        self._start_timeout = start_timeout
        self._children: dict[IRI, _Child] = {}
        self._lock = threading.Lock()
        self._all_stopped = threading.Event()
        # Set by `begin_stop`, from a signal handler: one assignment, read without the lock.
        self._stop_begun = False

    def spawn(
        self,
        resource: IRI,
        name: str,
        command: list[str],
        *,
        workflows: Collection[IRI] = (),
    ) -> None:
        """Start `command` as the process of `resource`, shown as `name`, which is live once its
        service has registered a workflow of each class in `workflows`.

        A resource has one child for the life of the supervisor. A second handle under the same
        IRI would replace the first, and nothing could stop the first process any more.

        Raises:
            ValueError: If a child was spawned for `resource` already. Nothing is started then.
        """
        with self._lock:
            earlier = self._children.get(IRI(resource))
        if earlier is not None:
            raise ValueError(
                f"A process for {resource} was spawned already, as {earlier.name!r}."
            )
        line = PLATFORM.command_line(command)
        self._echo(line)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # A Python child writes to a pipe in blocks unless told otherwise.
            env=os.environ | {"PYTHONUNBUFFERED": "1"},
            text=True,
            bufsize=1,
            creationflags=PLATFORM.NEW_PROCESS_GROUP,
        )
        child = _Child(
            resource=IRI(resource),
            name=name,
            command=line,
            process=process,
            workflows=frozenset(IRI(w) for w in workflows),
        )
        with self._lock:
            self._children[child.resource] = child
        threading.Thread(target=self._drain, args=(child,), daemon=True).start()

    def _drain(self, child: _Child) -> None:
        """Read the child's output until it exits, so that the pipe never fills and blocks it."""
        assert child.process.stdout is not None
        for raw in child.process.stdout:
            line = raw.rstrip("\n")
            with self._lock:
                child.output.append(line)
            self._echo(f"{child.name}  {line}")

    def refresh(self) -> None:
        """Check every child's process, then read the services once and set each state.

        A child is live while its service is live and has registered every workflow the child
        registers, and it keeps its service's address while it stays live. It has failed when its
        process exits without being told to, when its service stops being so, or when it has not
        turned live within the start timeout. A child that failed while it still runs turns live
        again once its service is.

        An exit is this process's own knowledge, so it is set before the graph is read, and it
        shows while the graph is away. A read that fails leaves every other state as it was, now
        unconfirmed, and raises its error again. Each turn to `failed` is printed, one line each.
        """
        self._find_exits()
        try:
            found = list(self._services(self._workflows()))
        except Exception as error:
            with self._lock:
                self._heartbeat_error = str(error) or type(error).__name__
            raise
        now = time.monotonic()
        turned: list[str | None] = []
        with self._lock:
            self._heartbeat_error = None
            for child in self._children.values():
                if child.stopping:
                    continue
                child.confirmed_at = now
                ready = child.ready_in(found)
                if not child.runs():
                    turned.append(self._exited_unasked(child))
                elif ready is not None:
                    child.state = "live"
                elif child.state == "live":
                    turned.append(child.fail("its service is not ready any more"))
                elif now - child.started_at > self._start_timeout:
                    turned.append(
                        child.fail(
                            f"it did not turn live within {self._start_timeout:g} s"
                        )
                    )
                if child.state != "live":
                    child.address = None
                elif child.address is None and ready is not None:
                    child.address = ready.address
        self._print_failed(turned)

    def _print_failed(self, turned: Iterable[str | None]) -> None:
        """Print the line of each child that turned `failed`: a bench launcher has no page, so
        its terminal is where a failed child shows. Printed outside the lock, so that a terminal
        that is slow to take a line holds up no read of a state."""
        for line in turned:
            if line is not None:
                self._echo(line)

    def _workflows(self) -> frozenset[IRI]:
        """The classes of the workflows that any child registers: what a read of the services
        reads with each."""
        with self._lock:
            return frozenset().union(*(c.workflows for c in self._children.values()))

    def _find_exits(self) -> None:
        """Set each child whose process exited without a stop asking it to `failed`. It reads
        no graph, so nothing it waits for can hang."""
        with self._lock:
            turned = [
                self._exited_unasked(child)
                for child in self._children.values()
                if not child.stopping and not child.runs()
            ]
        self._print_failed(turned)

    def heartbeat_error(self) -> str | None:
        """Why the latest read of the services failed, or None if it succeeded."""
        with self._lock:
            return self._heartbeat_error

    def watch(self, interval: float = WATCH_INTERVAL_SECONDS) -> None:
        """Watch the children every `interval` seconds, until `stop_all`, on two daemon threads:
        one finds the processes that exited, the other refreshes the states from the services.

        A read of the graph can hang until the client's own timeout gives up, 60 s. The first
        thread reads no graph, so it finds an exit within one interval however long a read
        hangs. The second keeps at most one read under way.
        """

        def exits() -> None:
            while not self._all_stopped.wait(interval):
                self._find_exits()

        def heartbeats() -> None:
            while not self._all_stopped.wait(interval):
                try:
                    self.refresh()
                # Whatever the service reader raises: a watch that died would freeze every
                # state on the page, and the graph can be away for a moment.
                except Exception as error:  # noqa: BLE001
                    self._echo(f"Could not read the heartbeats: {error}")

        for loop in (exits, heartbeats):
            threading.Thread(target=loop, daemon=True).start()

    def wait_until_ready(
        self,
        timeout: float | None = None,
        *,
        interval: float = READY_INTERVAL_SECONDS,
    ) -> None:
        """The readiness gate: wait until every child is live, so that its service has an
        address, a fresh heartbeat and every workflow the child registers, and a caller can call
        any of them at once.

        It reads the graph itself, every `interval` seconds, whether the watch runs or not. A read
        that fails is tried again until the wait runs out.

        Args:
            timeout: How long to wait at most, in seconds: the start timeout and one interval
                more if None, by when a child that has not turned live has failed.

        Raises:
            NotReady: If a child failed, or a stop reached it, before every child was live, naming
                it with its last output; or if not every child is live within `timeout`, naming
                each that is not, and why the last read of the graph failed if it did.
        """
        wait = self._start_timeout + interval if timeout is None else timeout
        deadline = time.monotonic() + wait
        while True:
            read_error = None
            try:
                self.refresh()
            # Whatever the service reader raises: the graph can be away for a moment.
            except Exception as error:  # noqa: BLE001
                read_error = str(error) or type(error).__name__
            children = self.snapshot()
            for child in children:
                if child.state in WILL_NOT_TURN_LIVE:
                    raise NotReady(
                        f"{child.name} is {child.state}, so it will not turn live. Its last "
                        "output:\n" + "\n".join(child.output)
                    )
            waiting = [child.name for child in children if child.state != "live"]
            if not waiting:
                return
            if time.monotonic() > deadline:
                why = (
                    f"; the last read of the graph failed: {read_error}"
                    if read_error
                    else ""
                )
                raise NotReady(f"not live within {wait:g} s: {', '.join(waiting)}{why}")
            time.sleep(interval)

    def begin_stop(self) -> None:
        """Mark the stop as begun, before `stop_all` asks any child: from now on, a child whose
        process exits with code 0 before a stop asks it has `stopped`, not `failed`.

        A stop signal can reach a child before the launcher asks it: a terminal's Ctrl+C, or a
        SIGINT to the process group of `./run-uc2`, reaches every process of the launcher's
        process group at once, while the launcher still waits for the open connections of its
        own page. A child ends on Ctrl+C with code 0, once it has deregistered its service. An exit
        with any other code still turns it `failed`.

        One assignment and no lock, so it is safe in a signal handler. (Added on 2026-10-10,
        jms_kapps 70.)
        """
        self._stop_begun = True

    def _exited_unasked(self, child: _Child) -> str | None:
        """Set the state of `child`, whose process exited with no stop of this supervisor asking
        it to, when the exit is first found: `stopped` if the stop had begun (`begin_stop`) and it
        exited with code 0, else `failed`. A later finding keeps that state, so an exit found
        before the stop began stays `failed`. Returns the line to print of a turn to `failed`, or
        None. Called with the lock held."""
        if child.exit_found:
            return None
        line = None
        if self._stop_begun and child.process.returncode == 0:
            child.state, child.address = "stopped", None
        else:
            line = child.fail(child.exit_reason())
        # Only once the state is set: the main thread can be interrupted between the two.
        child.exit_found = True
        return line

    def stop(self, resource: IRI) -> None:
        """Stop the child of `resource`, and wait until it has exited.

        Raises:
            KeyError: If no child was spawned for `resource`.
        """
        with self._lock:
            child = self._children[IRI(resource)]
        self._stop([child])

    def stop_all(self) -> None:
        """Stop every child, and wait until all of them have exited.

        This is the ordered teardown: each child is asked to stop, in the order they were
        spawned, so that it deregisters its service in its own shutdown; they stop in parallel,
        within one stop timeout, and a child still running then is killed and reported. Every
        child of a use case is a middleware instance with no device process behind it, so there
        is no second tier to stop after them.
        """
        with self._lock:
            children = list(self._children.values())
        self._stop(children)
        self._all_stopped.set()

    def _stop(self, children: list[_Child]) -> None:
        """Ask every child to stop, so each deregisters its service, then kill stragglers.

        The children stop in parallel, within one shared timeout. A child that had exited before
        it was asked keeps its state: it did not deregister when it exited. A stop signal that
        fails is recorded, and every later child still gets its own. The child it failed for was
        not asked: it runs on until the kill at the timeout, unless it has exited meanwhile.
        """
        for child in children:
            child.stopping = True
            if child.runs():
                self._ask_to_stop(child)
        deadline = time.monotonic() + self._stop_timeout
        for child in children:
            notice = None
            try:
                child.process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.process.kill()
                child.process.wait()
                notice = f"{child.name} did not stop within {self._stop_timeout:g} s, and was killed."
                self._echo(notice)
            turned = None
            with self._lock:
                child.address = None
                if notice is not None:
                    # In the child's output too, where the page shows it next to the state.
                    child.output.append(notice)
                    child.state = "killed"
                elif not child.asked_to_stop:
                    # It exited before it was asked, perhaps before any read of the graph saw it.
                    turned = self._exited_unasked(child)
                # A second stop does not make a kill clean.
                elif child.state != "killed":
                    child.state = "stopped"
            self._print_failed([turned])

    def _ask_to_stop(self, child: _Child) -> None:
        """Send the platform's stop signal to `child`. One that fails is recorded where a kill is,
        in the echo and in the child's output, and raises nothing: the child was not asked."""
        # Set before the signal, so that a child that exits on it at once never reads as one that
        # exited unasked; taken back if the signal fails.
        child.asked_to_stop = True
        try:
            PLATFORM.request_stop(child.process)
        except OSError as error:
            child.asked_to_stop = False
            notice = f"Could not ask {child.name} to stop: {error}"
            self._echo(notice)
            with self._lock:
                child.output.append(notice)

    def may_not_have_deregistered(self) -> set[IRI]:
        """The resources whose child may have left its service live: the killed children, and
        those that exited without a stop asking them to (see `_Child.may_not_have_deregistered`).
        """
        with self._lock:
            return {
                child.resource
                for child in self._children.values()
                if child.may_not_have_deregistered()
            }

    def snapshot(self) -> list[ChildSnapshot]:
        """Every child as it is now, in the order they were spawned."""
        now = time.monotonic()
        with self._lock:
            return [
                ChildSnapshot(
                    resource=child.resource,
                    name=child.name,
                    state=child.state,
                    pid=child.process.pid,
                    command=child.command,
                    output=list(child.output),
                    address=child.address,
                    unconfirmed_for=self._unconfirmed_for(child, now),
                    stoppable=child.stoppable(),
                )
                for child in self._children.values()
            ]

    def _unconfirmed_for(self, child: _Child, now: float) -> float | None:
        if not child.stoppable():
            return None
        age = now - child.confirmed_at
        if self._heartbeat_error is None and age <= UNCONFIRMED_AFTER_SECONDS:
            return None
        return round(age, 1)
