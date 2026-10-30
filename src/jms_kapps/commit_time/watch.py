"""The server watch: from the load's start to the drain's end, the harness reads the server's
resource view and measures one round trip about every `WATCH_INTERVAL_SECONDS`, and keeps each
sample with its time.

Other use of the same GraphDB may overlap a run, and the samples are what shows it: the CPU load,
the heap and the round trip of the server across the whole run, not only at its start and end.

**One request per view per interval.** A sample is two requests, the resource view and the
version (the round trip), and the next sample is one interval after the one before. A sample that
takes longer than the interval skips the samples it missed, so that reads never pile up and a
slow server is not asked more often.

**Between the run's own steps.** Like the completion matcher, the watch reads where the run
waits, not beside it: the run's clock (`WatchedClock`) splits each wait at the time of the next
sample, takes the sample, and goes on waiting. So a sample never runs beside the run's own steps,
and a test's clock, which moves only when the run waits, drives it.

**A read that fails never stops the run.** A refused or lost read is kept with its status or its
error, as the server's views are. A sample that raises anything else is kept as a sample whose
reads failed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from jms_kapps.commit_time.store import GraphDBServer

# How long from one sample to the next, in seconds (a run setting: the record keeps the one used).
WATCH_INTERVAL_SECONDS = 5.0


class _Clock(Protocol):
    def now(self) -> Any: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class ServerWatch:
    """The watch of one run.

    Args:
        server: The configured GraphDB's REST API.
        interval: The seconds from one sample to the next.
        clock: The run's clock.
    """

    def __init__(
        self, server: GraphDBServer, *, interval: float, clock: _Clock
    ) -> None:
        self.interval = interval
        self._server = server
        self._clock = clock
        self._next: float | None = None
        self.samples: list[dict[str, Any]] = []

    @property
    def active(self) -> bool:
        """Whether the watch has started and not stopped."""
        return self._next is not None

    def due(self) -> float | None:
        """When the next sample is due, on the monotonic clock, or None where the watch is not
        active."""
        return self._next

    def start(self) -> None:
        """Start at the load's start: the first sample is taken now, the next one interval after
        it."""
        self._next = self._clock.monotonic()
        self.sample()

    def stop(self) -> None:
        """Stop at the drain's end, or where the run ends early: no sample after this."""
        self._next = None

    def sample(self) -> None:
        """Take one sample now: the resource view and the round trip, and schedule the next."""
        began = self._clock.monotonic()
        at = self._clock.now().isoformat()
        taken: dict[str, Any] = {"at": at}
        taken["resources"] = kept(
            "GET rest/monitor/infrastructure", self._server.resources
        )
        taken["round_trip"] = kept("GET rest/info/version", self._server.round_trip)
        taken["seconds"] = round(self._clock.monotonic() - began, 6)
        self.samples.append(taken)
        if self._next is not None:
            self._next += self.interval
            now = self._clock.monotonic()
            while self._next < now:
                self._next += self.interval

    def record(self) -> dict[str, Any]:
        """What the record holds of the watch: its interval, how many samples it took, and each
        sample with its time, the server's resource view as it answered, the round trip, and how
        long the sample took."""
        return {
            "interval_seconds": self.interval,
            "sample_count": len(self.samples),
            "samples": list(self.samples),
        }


def kept(request: str, read: Callable[[], Any]) -> Any:
    """What `read` gives, or, where it raises anything at all, a view that says `request` failed
    and why: a read of the watch, or of the counters around the run, never stops the run."""
    try:
        return read()
    except Exception as error:  # noqa: BLE001 - a failed read is kept, and the run goes on
        return {"request": request, "error": str(error) or type(error).__name__}


class WatchedClock:
    """The run's clock, whose waits take the watch's samples in due time. Without an active
    watch, it is the clock it wraps."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.watch: ServerWatch | None = None

    def now(self) -> Any:
        return self._clock.now()

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def sleep(self, seconds: float) -> None:
        end = self._clock.monotonic() + seconds
        while True:
            now = self._clock.monotonic()
            due = self.watch.due() if self.watch is not None else None
            if self.watch is not None and due is not None and due <= end:
                if due > now:
                    self._clock.sleep(due - now)
                self.watch.sample()
                continue
            if end > now:
                self._clock.sleep(end - now)
            return
