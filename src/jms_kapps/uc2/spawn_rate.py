"""The spawn rate: how many boxes a minute the WarehouseManagementSystem spawns on its own.

A person sets it on the WarehouseManagementSystem's dashboard, and a program sets it through the
workflow `set_spawn_rate`, which realizes `:SetSpawnRateCapability`. The timer below then starts a
spawn each time one is due at that rate, on a thread of its own, whether the spawn before it has
ended or not, each with its origin and its destination left to the WarehouseManagementSystem,
until the rate is set to 0. The timer has no wait of its own: how many spawns may be under way at
once is the WarehouseManagementSystem's rule, which refuses a spawn beyond it (`wms`).
(Changed on 2026-10-07, jms_kapps 51, decided by Etienne: "the WMS can spawn as fast as the
hardware allows, with no wait of its own". Until then: "The timer below then spawns one box after
another at that rate".)
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SetSpawnRateRequest(BaseModel):
    """How many boxes a minute the WarehouseManagementSystem spawns on its own. 0 stops it."""

    boxes_per_minute: float = Field(ge=0, allow_inf_nan=False)


class SpawnRate(BaseModel):
    """The spawn rate in force, in boxes a minute. 0 when the timer is stopped."""

    boxes_per_minute: float


class SpawnTimer:
    """Starts a spawn each time one is due at the rate in force, each on a thread of its own.

    Spawn k of a rate is due k intervals after the first, one interval being 60 s divided by the
    rate. The first is due at once, so a person who sets a rate sees a box at once. A spawn under
    way is never waited for before the next one starts: one that takes longer than the interval
    overlaps the next. A timer that wakes late starts every spawn that has fallen due meanwhile at
    once, so the spawns started match the spawns due. A change of rate counts from the last due
    spawn: the next is due one new interval after it, or at once if that is past. The spawns under
    way when the rate is set to 0 still end. Where the process can start no more threads, a due
    spawn runs on the timer's own thread, so that none is lost unrecorded. (Changed on 2026-10-07,
    jms_kapps 51, decided by Etienne: "the WMS can spawn as fast as the hardware allows, with no
    wait of its own". Until then: "Spawns one box after another at the rate in force, on a thread
    of its own. The first box of a rate is spawned at once, and each next one an interval after
    the one before it started, so a person who sets a rate sees a box at once. A spawn runs to its
    end before the next one starts: one that takes longer than the interval delays the next, and
    none overlap. A change of rate counts from the last spawn: the next box is due one new
    interval after it, or at once if that is past. A spawn already under way when the rate is set
    to 0 still ends." That a late timer starts the spawns that fell due meanwhile at once is the
    build's reading of "spawn k of a rate is due k intervals after the first", and so is the spawn
    on the timer's own thread where no thread starts.)

    Args:
        spawn: Spawns one box, with its origin and its destination left to the
            WarehouseManagementSystem.
    """

    def __init__(self, spawn: Callable[[], object]) -> None:
        self._spawn = spawn
        self._boxes_per_minute = 0.0
        # Whether a thread runs the timer, and which: at most one does. The spawns under way, each
        # on its own thread, which the stop waits for. All guarded by `_lock`.
        self._running = False
        self._thread: threading.Thread | None = None
        self._under_way: set[threading.Thread] = set()
        self._lock = threading.Lock()
        self._changed = threading.Event()

    @property
    def boxes_per_minute(self) -> float:
        """The rate in force."""
        with self._lock:
            return self._boxes_per_minute

    def set(self, boxes_per_minute: float) -> float:
        """Spawn at `boxes_per_minute` from now on, and stop at 0.

        Args:
            boxes_per_minute: The rate, a finite number of boxes a minute, 0 or more.

        Returns:
            The rate in force.

        Raises:
            ValueError: If `boxes_per_minute` is negative, or not a finite number.
        """
        if not 0 <= boxes_per_minute < float("inf"):
            raise ValueError(
                f"a spawn rate is a finite number of boxes a minute, 0 or more, not "
                f"{boxes_per_minute}"
            )
        with self._lock:
            self._boxes_per_minute = float(boxes_per_minute)
            self._changed.set()
            if self._boxes_per_minute > 0 and not self._running:
                self._running = True
                self._thread = threading.Thread(
                    target=self._run, name="spawn-timer", daemon=True
                )
                self._thread.start()
            return self._boxes_per_minute

    def stop(self, timeout: float | None = None) -> None:
        """Set the rate to 0, and wait until every spawn under way has ended, `timeout` seconds
        at most in all.

        The timer and its spawns run on daemon threads, which the end of the process would cut
        short, in the middle of a spawn: its transport would stay open with nobody to end it. So
        the process stops the timer this way when it shuts down. (Changed on 2026-10-07, jms_kapps
        51, decided by Etienne. Until then it waited "until a spawn under way has ended".)
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            thread = self._thread
        self.set(0)
        if thread is not None:
            thread.join(_left(deadline))
        # Once the timer's thread has ended, no spawn starts any more.
        with self._lock:
            under_way = list(self._under_way)
        for spawn in under_way:
            spawn.join(_left(deadline))

    def _run(self) -> None:
        interval: float | None = None
        due: float | None = None
        while True:
            with self._lock:
                self._changed.clear()
                boxes_per_minute = self._boxes_per_minute
                if boxes_per_minute == 0:
                    self._running = False
                    return
            new_interval = 60.0 / boxes_per_minute
            if due is None:
                due = time.monotonic()
            elif new_interval != interval:
                # A change of rate: the next spawn is due one new interval after the last due one,
                # which is one old interval before `due`, or at once if that is past.
                assert interval is not None
                due = max(due - interval + new_interval, time.monotonic())
            interval = new_interval
            wait = due - time.monotonic()
            if wait > 0:
                # Woken early by a change of rate, it counts the interval again.
                self._changed.wait(wait)
                continue
            self._start_spawn()
            due += interval

    def _start_spawn(self) -> None:
        """Start one spawn on a thread of its own, which the stop waits for."""
        thread = threading.Thread(target=self._spawn_once, name="spawn", daemon=True)
        with self._lock:
            self._under_way.add(thread)
        try:
            thread.start()
        except RuntimeError:
            # The process can start no more threads. The spawn runs on the timer's own thread
            # instead, which then waits for it, so that no spawn that was due is lost unrecorded.
            # (The build's reading of jms_kapps 51: "no box is lost silently".)
            with self._lock:
                self._under_way.discard(thread)
            logger.warning(
                "the spawn timer could start no thread for a spawn that was due, so it spawns "
                "on its own thread"
            )
            self._spawn_once()

    def _spawn_once(self) -> None:
        try:
            self._spawn()
        except Exception:
            # The timer goes on: one spawn that failed is no reason to stop the next.
            logger.exception("a spawn of the spawn timer failed")
        finally:
            with self._lock:
                self._under_way.discard(threading.current_thread())


def _left(deadline: float | None) -> float | None:
    """The seconds left until `deadline`, 0 once it has passed, or None if there is none."""
    return None if deadline is None else max(0.0, deadline - time.monotonic())
