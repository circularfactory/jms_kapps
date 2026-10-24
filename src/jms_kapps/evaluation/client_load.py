"""The client host's own load during a run: its CPU use and its memory use, sampled from the
operating system's figures (`/proc` on Linux) while the participants run beside the harness, so
that a slow step can be told apart from a busy client.

**What is sampled.** The whole host, not one process: the CPU use is the share of the time all
CPUs were busy since the sample before (`/proc/stat`: everything but idle and waiting for input or
output), in percent of all CPUs together; the memory use is what is in use, the host's memory less
what `/proc/meminfo` calls available. Nothing is installed for it: it reads two small files.

**When.** From the load's start, the spawn rate set at the warm-up's start (the window's, for a
run without a warm-up), through the window and the drain, to the drain's end; the store-only
baseline samples from before its first replayed request to its window's end. A sample is taken
every `CLIENT_LOAD_INTERVAL_SECONDS`, a run setting of the record (`run_settings`), by a thread of
the harness that sleeps between two reads, and one last sample comes at the stop. A host without
`/proc/stat` and `/proc/meminfo` gives no sample: the record says "not available".

**The record** (`client_load`): `status` (`sampled` or `not available`), `interval_seconds`,
`memory_total_bytes`, and the `samples`, each with its time (`at`), `cpu_percent` (None where no
time passed since the sample before) and `memory_used_bytes`.

Every name is interim.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

# How often the client host is sampled, in seconds: a run setting of every record (`run_settings`).
CLIENT_LOAD_INTERVAL_SECONDS = 1.0

# Where the operating system keeps its figures; a test points it at a folder of its own.
PROC = Path("/proc")

# The two statuses of the record's `client_load`.
SAMPLED = "sampled"
NOT_AVAILABLE = "not available"

_IDLE_FIELDS = (3, 4)  # idle, iowait (fields of the `cpu` line after its name)
_TOTAL_FIELDS = (
    8  # user nice system idle iowait irq softirq steal; guest is in user already
)


class _Clock(Protocol):
    def now(self) -> datetime: ...


def read_cpu_ticks(proc: Path) -> tuple[int, int]:
    """The ticks all CPUs were busy and the ticks they ran in all, since boot, from `proc/stat`.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If its first line is no `cpu` line.
    """
    with (proc / "stat").open(encoding="utf-8") as file:
        fields = file.readline().split()
    if not fields or fields[0] != "cpu":
        raise ValueError("no cpu line")
    ticks = [int(field) for field in fields[1 : 1 + _TOTAL_FIELDS]]
    idle = sum(ticks[i] for i in _IDLE_FIELDS if i < len(ticks))
    total = sum(ticks)
    return total - idle, total


def read_memory(proc: Path) -> tuple[int, int]:
    """The host's memory in use and in all, in bytes, from `proc/meminfo`.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If it names no total or no available memory.
    """
    found: dict[str, int] = {}
    with (proc / "meminfo").open(encoding="utf-8") as file:
        for line in file:
            name, _, rest = line.partition(":")
            if name in ("MemTotal", "MemAvailable"):
                found[name] = int(rest.split()[0]) * 1024  # the file counts kibibytes
    if len(found) != 2:
        raise ValueError("no MemTotal or MemAvailable")
    return found["MemTotal"] - found["MemAvailable"], found["MemTotal"]


class ClientLoadSampler:
    """The sampler of one run's client load.

    Args:
        clock: The run's clock, for the time of each sample.
        interval: Seconds from one sample to the next; `CLIENT_LOAD_INTERVAL_SECONDS` if None.
        proc: The operating system's figures; `PROC` if None.
    """

    def __init__(
        self,
        clock: _Clock,
        *,
        interval: float | None = None,
        proc: Path | None = None,
    ) -> None:
        self.interval = CLIENT_LOAD_INTERVAL_SECONDS if interval is None else interval
        self._clock = clock
        self._proc = PROC if proc is None else proc
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._ticks: tuple[int, int] | None = None
        self._memory_total: int | None = None
        self._samples: list[dict[str, Any]] = []
        self.started = False
        self.available = False

    def start(self) -> None:
        """Take the first reading, which later samples measure against, and sample every interval
        until `stop`. A host without the figures samples nothing."""
        self.started = True
        try:
            self._ticks = read_cpu_ticks(self._proc)
            self._memory_total = read_memory(self._proc)[1]
        except (OSError, ValueError):
            return
        self.available = True
        self._thread = threading.Thread(
            target=self._loop, name="client-load", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Take one last sample and stop. It may be called again, and before `start`."""
        if self._thread is None:
            return
        self._stopping.set()
        self._thread.join()
        self._thread = None
        self.sample()

    def _loop(self) -> None:
        while not self._stopping.wait(self.interval):
            self.sample()

    def sample(self) -> None:
        """Take one sample now. A reading that fails is skipped."""
        with self._lock:
            try:
                busy, total = read_cpu_ticks(self._proc)
                used, _ = read_memory(self._proc)
            except (OSError, ValueError):
                return
            before = self._ticks
            self._ticks = (busy, total)
            share = None
            if before is not None and total > before[1]:
                share = 100.0 * (busy - before[0]) / (total - before[1])
            self._samples.append(
                {
                    "at": self._clock.now().isoformat(),
                    "cpu_percent": None if share is None else round(share, 2),
                    "memory_used_bytes": used,
                }
            )

    def record(self) -> dict[str, Any]:
        """What the record holds of the client load: see the module's description."""
        with self._lock:
            return {
                "status": SAMPLED if self.available else NOT_AVAILABLE,
                "interval_seconds": self.interval,
                "memory_total_bytes": self._memory_total,
                "samples": list(self._samples),
            }
