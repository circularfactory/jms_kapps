"""The run limit of the measurement harness: a run older than its limit ends as failed (over time),
with the limit and the step at which the limit came (added on 2026-10-13, jms_kapps 106, decided by
Sören on jms_kapps 85, note 1866296: the series must run with no person present; interim names,
jms_kapps 83).

Each step of a run has a limit of its own: a participant's start, a workflow step, each store
request of the harness, the warm-up and the window, the drain. The run limit is the limit of the
whole run, so that a run that hangs between two steps does not stop a sweep until a person comes.
A UC2 run of the bench launcher (`bench`), the store-only baseline and each replay level
(`baseline`) have it, each with the same default, `RUN_LIMIT_SECONDS`.

**What counts.** The time counts on the run's clock from the run's start, where it reads the facts
before the run, the moment before it creates its repository, to the start of the run's end. The
end itself, the stop of the participants, the deletion of the repository and the write of the
record, is outside the limit: a UC2 run stops its limit before it stops its participants. So a run
over its limit still cleans up and writes its record, and a run whose measurement is complete is
never over time.

**How it ends the run.** A thread of its own looks at the run's clock every `LOOK_SECONDS`. Once
the run is older than its limit, it interrupts the main thread as a stop signal does
(`Platform.interrupt_main_thread`): the run's handler of the stop signals (`run_and_end`, through
`one_stop`) raises `OverTime` there, between two steps of whatever runs there, and on POSIX also in
a call that waits there. The run then ends through the end that a stop signal takes, and its
outcome begins with `OVER_TIME`, "failed (over time)", and names the limit and the step. A failed
run is a result, so a sweep does not make it again. A stop signal that comes once the limit has
come ends the run as over time too, and the limit does nothing once a stop signal has begun the
run's end. The end is certain, wherever the interrupt comes (`run_and_end`). A run off the main
thread, where Python sets no signal handler, has no limit.

The limit is a run setting, outside the configuration's key: the record keeps it under
`run_settings`, as it keeps the other limits.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from jms_kapps.common.platforms import PLATFORM
from jms_kapps.common.process import one_stop

# The limit of a run, in seconds: 30 minutes. A run of the "after" series takes about 12 minutes.
RUN_LIMIT_SECONDS = 30 * 60.0

# The start of the outcome of a run that its limit ended. The rest of the outcome names the limit
# and the step at which it came.
OVER_TIME = "failed (over time)"

# How often the limit looks at the run's clock, in seconds: the limit comes at most this late.
LOOK_SECONDS = 0.25


class _Clock(Protocol):
    def monotonic(self) -> float: ...


class OverTime(KeyboardInterrupt):
    """What the main thread of a run raises once the run is older than its limit. It is a
    `KeyboardInterrupt`, as a stop signal raises, so that every step lets it through as it lets a
    stop signal through, and nothing that catches an `Exception` keeps the run going. Its text is
    the run's outcome."""


class RunLimit:
    """The limit of one run, and the step the run is at.

    Args:
        seconds: The limit, in seconds of the run's clock.
        clock: The run's clock.
    """

    def __init__(self, seconds: float, clock: _Clock) -> None:
        self.seconds = seconds
        self._clock = clock
        self._step: str | None = None
        self._came_during: str | None = None
        self._began: float | None = None
        self._lock = threading.Lock()
        self._passed = threading.Event()
        self._stopped = threading.Event()

    def start(self, step: str) -> None:
        """Start counting at the run's start, in its first step, `step`. Call it on the run's
        thread, inside the run's handler of the stop signals: off the main thread the limit does
        nothing."""
        self._step = step
        self._began = self._clock.monotonic()
        if threading.current_thread() is threading.main_thread():
            threading.Thread(target=self._look, name="run-limit", daemon=True).start()

    def at(self, step: str) -> None:
        """The run enters `step`."""
        with self._lock:
            self._step = step

    def stop(self) -> None:
        """Stop counting, at the start of the run's end: the limit no longer comes."""
        with self._lock:
            self._stopped.set()

    @property
    def passed(self) -> bool:
        """Whether the limit came: the run grew older than it before its end began."""
        return self._passed.is_set()

    @property
    def came_during(self) -> str | None:
        """The step the run was at when the limit came, or None while it has not come."""
        return self._came_during

    def outcome(self) -> str:
        """The outcome of the run that the limit ended: `OVER_TIME`, the limit and the step."""
        return f"{OVER_TIME}: the run limit of {self.seconds:g} s came during {self._came_during}"

    def over_time(self) -> OverTime:
        """What the run's handler of the stop signals raises once the limit has come."""
        return OverTime(self.outcome())

    def _look(self) -> None:
        """Wait until the run is older than its limit, then interrupt the main thread; or end,
        where the run's end begins first."""
        assert self._began is not None
        while True:
            left = self._began + self.seconds - self._clock.monotonic()
            if left <= 0:
                break
            if self._stopped.wait(min(left, LOOK_SECONDS)):
                return
        # Under the lock, so that once `stop` has returned, no interrupt of the limit follows.
        with self._lock:
            if self._stopped.is_set():
                return
            self._came_during = self._step
            self._passed.set()
            PLATFORM.interrupt_main_thread()


# The action that a run's record holds once its limit has come, with the limit and the step.
RUN_LIMIT_REACHED = "run limit reached"


def run_and_end(
    record: dict[str, Any],
    limit: RunLimit,
    steps: Callable[[], None],
    end: Callable[[], None],
    *,
    first_step: str,
    interrupted: str,
    add_action: Callable[..., object],
) -> None:
    """Run a run's `steps` under its handler of the stop signals and under its `limit`, set the
    run's outcome in `record`, and then run its `end` once, whatever comes when. A UC2 run of the
    bench launcher and a baseline both end so.

    The outcome is `completed` where the steps return; `OVER_TIME` with the limit and the step
    (`RunLimit.outcome`) where the limit ended them, also where a cleanup of a step failed while
    the limit's interrupt went through it, and then `add_action` adds `RUN_LIMIT_REACHED`;
    `interrupted` where a stop signal ended them; and `failed:` with the error where a step
    failed.

    **The end is certain.** Python runs a signal's handler after any instruction of the main
    thread, so a stop signal or the limit may raise its interrupt as the steps end, before the run
    has begun its end. The handler raises at most once (`one_stop`), and not once the run's end
    has begun. So the end begins, its flag set, as the last step of the steps and as the first
    step after an error of theirs; an interrupt that comes before it is caught one level up, and
    sets the outcome. The end itself runs in a `finally`, where no interrupt comes any more.

    Args:
        record: The run's record: its `outcome` is set here, before `end` runs.
        limit: The run's limit, which starts here, in `first_step`, inside the handler.
        steps: The run's steps, from its start to the start of its end. They mark each step on
            `limit` (`RunLimit.at`).
        end: The run's end: the stop of whatever runs, the deletion of its repository and the
            write of its record. A stop signal or the limit does not cut it short.
        interrupted: The outcome of a run that a stop signal ended.
        add_action: Adds an action to the run's record, by its name and its details.
    """
    ending = threading.Event()

    def interrupt(signum: int) -> None:
        if not ending.is_set():
            if limit.passed:
                raise limit.over_time()
            raise KeyboardInterrupt

    def settle(error: BaseException) -> None:
        if _over_time(error):
            record["outcome"] = limit.outcome()
            add_action(
                RUN_LIMIT_REACHED, limit_seconds=limit.seconds, step=limit.came_during
            )
        elif isinstance(error, KeyboardInterrupt):
            record["outcome"] = interrupted
        else:
            record["outcome"] = f"failed: {type(error).__name__}: {error}"

    with one_stop(interrupt):
        try:
            try:
                try:
                    limit.start(first_step)
                    steps()
                    record["outcome"] = "completed"
                    ending.set()
                except (KeyboardInterrupt, Exception) as error:  # noqa: BLE001 - every end of the steps is an outcome
                    ending.set()
                    settle(error)
            except KeyboardInterrupt as late:
                # The handler's one interrupt, raised in the clause above before it set
                # `ending`: the steps had failed just before.
                ending.set()
                settle(late)
        finally:
            ending.set()
            limit.stop()
            end()


def _over_time(error: BaseException) -> bool:
    """Whether the limit ended the run: `error` is the limit's interrupt, or an error that a
    cleanup raised while the interrupt went through it."""
    seen: set[int] = set()
    found: BaseException | None = error
    while found is not None and id(found) not in seen:
        if isinstance(found, OverTime):
            return True
        seen.add(id(found))
        found = found.__context__
    return False
