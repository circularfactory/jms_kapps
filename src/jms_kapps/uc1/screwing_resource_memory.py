"""What the screwing resource knows of its own attempts, kept in its memory.

Its dashboard box renders this memory and nothing else, so an open dashboard causes no load: no
read of the graph and no read of the time-series store. The screwing resource writes it as it
works, from what each attempt has at hand anyway: the operation as the intake read it, the
time-series reference it wrote, the detector it called, and the detector's answer.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from kapps_triplestore_interface import IRI

from jms_kapps.uc1.vocabulary import TYPE_LEVEL

# How many attempts it keeps: the newest.
KEPT_ATTEMPTS = 50


def _now() -> datetime:
    """The time of day here, with its time zone."""
    return datetime.now().astimezone()


def values_text(values: Mapping[IRI, float]) -> str:
    """The type values an attempt was given, in the order of `TYPE_LEVEL`, as the log line and the
    dashboard name them: `MinApproachAxialForce 0.5, MinCounterTorque 0.5, ...`. Without values,
    `no values`."""
    parts = [
        f"{prop.fragment.removeprefix('has')} {values[prop]:g}"
        for prop in TYPE_LEVEL
        if prop in values
    ]
    return ", ".join(parts) if parts else "no values"


@dataclass(frozen=True)
class AttemptRecord:
    """One attempt of the screwing resource, as its dashboard shows it.

    `at` is when the attempt began. `reference` is where its curves went, or None when the attempt
    failed before. `detector` is the endpoint of the classify workflow it called, or None when it
    called none or found none. `status` is the operation's status once the detector answered, or
    once the screwing resource ended it itself, or None when no answer said it; `outcome` is the
    Detection result that the detector answered. `why` says why the operation failed, or was
    left as it was, as the detector or the screwing resource gave it. `type_values` are the seven
    values of the Screw type that the attempt was given, and empty when the screwing resource
    made no attempt because the Screw type does not give them.
    """

    at: datetime
    operation: IRI
    screw: IRI | None
    screw_type: IRI | None
    reference: str | None = None
    detector: str | None = None
    status: str | None = None
    outcome: str | None = None
    why: str | None = None
    type_values: Mapping[IRI, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ScrewingState:
    """What the screwing resource knows of its attempts at one moment, as its box shows it."""

    # The newest first.
    attempts: list[AttemptRecord]
    attempt_count: int


class ScrewingMemory:
    """What the screwing resource knows of its attempts: each one, newest first.

    It keeps the newest `KEPT_ATTEMPTS` attempts, and counts every one.

    Args:
        clock: The time of day, by which the screwing resource stamps each attempt.
    """

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        # The workflow writes on the intake's thread, and a render reads on one of its own.
        self._lock = threading.Lock()
        self._attempts: deque[AttemptRecord] = deque(maxlen=KEPT_ATTEMPTS)
        self._count = 0

    def now(self) -> datetime:
        """The time of day by its clock."""
        return self._clock()

    def attempted(self, record: AttemptRecord) -> None:
        """An attempt has ended, as `record` says."""
        with self._lock:
            self._attempts.appendleft(record)
            self._count += 1

    def state(self) -> ScrewingState:
        """What it knows now."""
        with self._lock:
            return ScrewingState(
                attempts=list(self._attempts), attempt_count=self._count
            )
