"""What the learner knows of its own runs, kept in its memory.

Its dashboard box renders this memory and nothing else, so an open dashboard causes no load: no
read of the graph and no read of the time-series store. The learner writes it as it runs, from
what each run reads and commits anyway.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from kapps_triplestore_interface import IRI

# How many changed values it keeps: the newest.
KEPT_CHANGES = 50


def _now() -> datetime:
    """The time of day here, with its time zone."""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class Change:
    """A value that the learner committed to a Screw type, and when.

    `old` is what the Screw type held when the run read it, before the commit: a seeded value, a
    value of an earlier run, or None when it held none.
    """

    at: datetime
    screw_type: IRI
    threshold: IRI
    old: float | None
    new: float


@dataclass(frozen=True)
class LastRun:
    """How the learner's last run ended, and when: with how many new attempts it learned from and
    how many values it changed, or failed."""

    at: datetime
    learned: int = 0
    changed: int = 0
    failed: bool = False


@dataclass(frozen=True)
class LearnerError:
    """The last thing that went wrong, and when: the Operation whose attempt the learner was at,
    or None when the run failed at no single Operation, and why."""

    at: datetime
    operation: IRI | None
    why: str


@dataclass(frozen=True)
class LearnerState:
    """What the learner knows of its own runs at one moment, as its dashboard box shows it."""

    last_run: LastRun | None
    # Whether a run is under way now.
    running: bool
    next_run: datetime | None
    # How often it runs, once its runs are scheduled.
    every_seconds: float | None
    # How many attempts it learned from since it started. How many of them were new in the last
    # run is in `last_run`.
    learned: int
    # How many attempts it passed over since it started, because it could not read their curves.
    passed_over: int
    # The newest first.
    changes: list[Change]
    change_count: int
    last_error: LearnerError | None


class LearnerMemory:
    """What the learner knows of its own runs: how the last run ended and when the next comes, how
    many attempts it learned from, each value it changed, and the last thing that went wrong.

    It keeps the newest `KEPT_CHANGES` changes, and counts every one.

    Args:
        clock: The time of day, by which it stamps what it is told.
    """

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        # A run writes on a worker thread, and a render reads on one of its own.
        self._lock = threading.Lock()
        self._last_run: LastRun | None = None
        self._running = False
        self._next_run: datetime | None = None
        self._every_seconds: float | None = None
        self._learned = 0
        self._passed_over = 0
        self._changes: deque[Change] = deque(maxlen=KEPT_CHANGES)
        self._change_count = 0
        self._last_error: LearnerError | None = None

    def now(self) -> datetime:
        """The time of day by its clock."""
        return self._clock()

    def next_run_in(self, seconds: float) -> None:
        """The next run comes `seconds` from now, as one run comes every `seconds`."""
        at = self._clock() + timedelta(seconds=seconds)
        with self._lock:
            self._next_run = at
            self._every_seconds = seconds

    def started(self) -> None:
        """A run starts now."""
        with self._lock:
            self._running = True

    def ran(self, *, learned: int, passed_over: int, changes: Iterable[Change]) -> None:
        """A run ended now: it learned from `learned` new attempts, passed over `passed_over`
        whose curves it could not read, and changed the values of `changes`."""
        changes = list(changes)
        at = self._clock()
        with self._lock:
            self._last_run = LastRun(at, learned=learned, changed=len(changes))
            self._running = False
            self._learned += learned
            self._passed_over += passed_over
            self._changes.extendleft(changes)
            self._change_count += len(changes)

    def cannot_read(self, operation: IRI, why: str) -> None:
        """The learner cannot read the curves of the attempt of `operation`, for `why`, and passes
        over it."""
        error = LearnerError(self._clock(), operation, why)
        with self._lock:
            self._last_error = error

    def failed(self, operation: IRI | None, why: str) -> None:
        """A run failed now, at the attempt of `operation` or at none, for `why`."""
        at = self._clock()
        with self._lock:
            self._last_run = LastRun(at, failed=True)
            self._running = False
            self._last_error = LearnerError(at, operation, why)

    def state(self) -> LearnerState:
        """What it knows now."""
        with self._lock:
            return LearnerState(
                last_run=self._last_run,
                running=self._running,
                next_run=self._next_run,
                every_seconds=self._every_seconds,
                learned=self._learned,
                passed_over=self._passed_over,
                changes=list(self._changes),
                change_count=self._change_count,
                last_error=self._last_error,
            )
