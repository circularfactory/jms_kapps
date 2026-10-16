"""What the planner knows of the presses it has made, kept in its memory.

Its dashboard box renders this memory and nothing else, so an open dashboard causes no load: no
read of the graph. The planner writes it as it presses, from what it dispatches and waits for
anyway.

One press is one Screw. The memory holds one entry per Screw that was pressed, in the order of the
first press, and each entry carries that Screw's operation and how that operation ended. A second
press of the same Screw replaces its entry, because the entry is what became of that Screw last.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from kapps_triplestore_interface import IRI

# What an entry's status can be.
#
# `WAITING` is the entry's state before the planner reaches the Screw. `DISPATCHED` means the
# operation is in the graph, `queued`, and the screwing resource was notified. `DONE` and `FAILED`
# are the two ends the middleware reports. `REFUSED` means the issuing call found no live service
# that realizes the capability, so it wrote nothing and there is no operation. `TIMED_OUT` means
# the planner stopped waiting; the operation stays in the graph.
WAITING = "waiting"
DISPATCHED = "dispatched"
DONE = "done"
FAILED = "failed"
REFUSED = "refused"
TIMED_OUT = "timed-out"


def _now() -> datetime:
    """The time of day here, with its time zone."""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class ScrewEntry:
    """One Screw that was pressed, and how far its operation got.

    `operation` is None until the Screw is dispatched, and stays None when the dispatch was
    refused. `outcome` is what the detector decided, on a Screw whose operation ended `DONE`.
    `why` carries the reason of a `FAILED`, `REFUSED` or `TIMED_OUT` entry. `at` is when the
    entry last changed.
    """

    screw: IRI
    status: str = WAITING
    operation: IRI | None = None
    outcome: str | None = None
    why: str | None = None
    at: datetime | None = None


@dataclass(frozen=True)
class PlannerState:
    """What the planner knows at one moment, as its dashboard box shows it."""

    # One entry per Screw that was pressed, in the order of its first press.
    screws: list[ScrewEntry]


class PlannerMemory:
    """What the planner knows of its presses: each Screw pressed, and how its operation ended.

    Args:
        clock: The time of day, by which it stamps what it is told.
    """

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        # A press writes on a worker thread, and a render reads on one of its own.
        self._lock = threading.Lock()
        self._screws: list[ScrewEntry] = []

    def now(self) -> datetime:
        """The time of day by its clock."""
        return self._clock()

    def pressed(self, screw: IRI) -> None:
        """`screw` was pressed: it is waiting for its operation to be issued.

        A Screw pressed again starts over, because its entry says what became of it last.
        """
        self._set(screw, status=WAITING, operation=None, outcome=None, why=None)

    def dispatched(self, screw: IRI, operation: IRI) -> None:
        """`screw`'s operation is in the graph, `queued`, and the screwing resource was
        notified."""
        self._set(screw, status=DISPATCHED, operation=operation)

    def ended(
        self,
        screw: IRI,
        status: str,
        *,
        outcome: str | None = None,
        why: str | None = None,
    ) -> None:
        """`screw`'s operation ended `DONE` with `outcome`, or `FAILED` for `why`."""
        self._set(screw, status=status, outcome=outcome, why=why)

    def refused(self, screw: IRI, why: str) -> None:
        """The issuing call for `screw` found no live service to take the work, for `why`, and
        wrote nothing. The Screw has no operation."""
        self._set(screw, status=REFUSED, why=why)

    def timed_out(self, screw: IRI, why: str) -> None:
        """The planner stopped waiting for `screw`'s operation, for `why`. The operation stays in
        the graph."""
        self._set(screw, status=TIMED_OUT, why=why)

    def state(self) -> PlannerState:
        """What it knows now."""
        with self._lock:
            return PlannerState(screws=list(self._screws))

    def _set(self, screw: IRI, **changes: object) -> None:
        """Replace the entry of `screw` with one that carries `changes`, stamped now, and add it
        where there is none.

        An entry is frozen, so a render that already took the state keeps what it read. A Screw
        that was never pressed gains its entry here, because a press is what creates one.
        """
        at = self._clock()
        with self._lock:
            for index, entry in enumerate(self._screws):
                if entry.screw == screw:
                    self._screws[index] = replace(entry, at=at, **changes)  # type: ignore[arg-type]
                    return
            self._screws.append(replace(ScrewEntry(screw), at=at, **changes))  # type: ignore[arg-type]
