"""The completion matcher: from the load's start until the drain's end, it reads from the graph the
run's transports that have ended, at the matcher interval of the run's configuration.

The WarehouseManagementSystem answers a spawn when it accepts it, so the end of a transport comes
from the graph, not from a participant: the matcher reads it at a fixed low rate, and the record
states that rate. Its reads are the harness's own load on the store, so it records each one with
its time and how long it took.

The load starts at the warm-up's start, where the run has a warm-up, so the matcher sees a
transport that ends in the warm-up too. Its first read comes one interval after the load's start,
and each later one an interval after the one before; a read that takes longer than the interval
skips the reads it missed, so that reads never pile up. The run waits through the matcher
(`wait`), so a read comes between two of the run's own steps and never beside them. Its last
read is at the drain's end.

For each transport, it keeps the end it saw first: its status, its place, and the time of the read
that saw it. A transport whose status changes again after it ended keeps the end the matcher saw
first: the run's checks compare it with what the graph shows after the drain.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol


class _Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class _End(Protocol):
    @property
    def transport(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def place(self) -> str | None: ...


class CompletionMatcher:
    """The completion matcher of one run.

    Args:
        read_ended: One read of the run's transports that have ended, from the graph: of the run's
            own transports (`own_transports`) that it has not read ended before, so a read grows
            with the transports still open, not with the run (jms_kapps 90).
        interval: The seconds from one read to the next: the configuration's
            `matcher_interval_seconds`.
        clock: The run's clock.
    """

    def __init__(
        self,
        read_ended: Callable[[], Sequence[_End]],
        *,
        interval: float,
        clock: _Clock,
    ) -> None:
        self.interval = interval
        self._read_ended = read_ended
        self._clock = clock
        self._next: float | None = None
        self.reads: list[dict[str, Any]] = []
        self.ends: dict[str, dict[str, Any]] = {}

    @property
    def started(self) -> bool:
        return self._next is not None

    def start(self) -> None:
        """Start at the load's start: the first read is one interval later."""
        self._next = self._clock.monotonic() + self.interval

    def wait(self, seconds: float) -> None:
        """Wait `seconds`, reading at each read's time that falls in them. Before the start it
        only waits."""
        end = self._clock.monotonic() + seconds
        while True:
            now = self._clock.monotonic()
            if self._next is not None and self._next <= end:
                if self._next > now:
                    self._clock.sleep(self._next - now)
                self.read()
                self._next += self.interval
                read_at = self._clock.monotonic()
                while self._next < read_at:
                    self._next += self.interval
                continue
            if end > now:
                self._clock.sleep(end - now)
            return

    def read(self) -> None:
        """Read the run's ended transports now, and keep the end of each one not seen before."""
        at = self._clock.now().isoformat()
        began = self._clock.monotonic()
        ended = self._read_ended()
        seconds = self._clock.monotonic() - began
        self.reads.append({"at": at, "seconds": round(seconds, 6), "ended": len(ended)})
        for end in ended:
            if end.transport not in self.ends:
                self.ends[end.transport] = {
                    "transport": end.transport,
                    "status": end.status,
                    "place": end.place,
                    "seen_at": at,
                }

    def record(self) -> dict[str, Any]:
        """What the record holds of the matcher: its interval, each of its reads with its time,
        how long it took and how many ended transports it read (those it had not read ended before),
        their number and their seconds
        in all, and the end it saw of each transport, in the order it saw them."""
        return {
            "interval_seconds": self.interval,
            "read_count": len(self.reads),
            "read_seconds": round(sum(read["seconds"] for read in self.reads), 6),
            "reads": list(self.reads),
            "ends": list(self.ends.values()),
        }
