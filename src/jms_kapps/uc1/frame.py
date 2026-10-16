"""The frame: the raw curves of one unscrewing attempt.

A frame holds each signal that the screwing resource records during one attempt: one value of
each signal at each sample time, and the phase of each sample where the phase is known. The
screwing resource writes a frame to the time-series store. The detector and the learner read
frames back from it. The frame replay makes a frame from a recording, in place of the robot.

A frame is recorded with phases or without them. A sample outside every phase, or any sample of a
frame recorded without phases, has no phase. The legacy mock recordings have phases from labels
that a person wrote, and some of their samples have none: the retract after the disassembly, and
the missing screw's samples after its approach.

The values are floats. A measured frame has the units that the ontology gives its thresholds:
force in N, torque in N·m, position in mm along the y axis of the robot frame. The units of the
legacy mock recordings are not known (see `replay`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

# The phases of one unscrewing attempt, in the order they run.
PHASES = ("Approach", "Form-fit", "Preparation", "Disassembly")

# The three signals that the screwing resource records.
ROBOT_POSITION = "robot_position"
AXIAL_FORCE = "axial_force"
UNSCREWING_TORQUE = "unscrewing_torque"
SIGNALS = (ROBOT_POSITION, AXIAL_FORCE, UNSCREWING_TORQUE)

# Each signal becomes a column of the store, beside the time and the tags. InfluxDB allows a
# column name of letters, digits, `_` and `-` that does not start with `_`.
_COLUMN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_RESERVED = frozenset({"time", "frame", "phase", "source"})

# A tag value goes into the store's line protocol without escapes, so it may not hold a character
# that the line protocol would have to escape.
_TAG_VALUE = re.compile(r'[^\s,="\\]+')


@dataclass(frozen=True)
class Frame:
    """The raw curves of one unscrewing attempt.

    Attributes:
        times: The sample times, in nanoseconds since the Unix epoch (UTC), strictly ascending.
        signals: Each signal's values, one for each sample time.
        phases: Each sample's phase, one of `PHASES`, or None where the phase is not known.
        source: None for a frame that the robot recorded. For a replayed frame, the recording it
            replays; a mock recording says that it is mock.
    """

    times: tuple[int, ...]
    signals: Mapping[str, tuple[float, ...]]
    phases: tuple[str | None, ...]
    source: str | None = None

    def __init__(
        self,
        times: Sequence[int],
        signals: Mapping[str, Sequence[float]],
        phases: Sequence[str | None],
        source: str | None = None,
    ) -> None:
        object.__setattr__(self, "times", _times(times))
        count = len(self.times)
        object.__setattr__(self, "signals", _signals(signals, count))
        object.__setattr__(self, "phases", _phases(phases, count))
        object.__setattr__(self, "source", _source(source))

    @property
    def start(self) -> int:
        """The first sample time."""
        return self.times[0]

    @property
    def stop(self) -> int:
        """The last sample time."""
        return self.times[-1]

    @property
    def seconds(self) -> tuple[float, ...]:
        """Each sample time, in seconds after the first."""
        return tuple((t - self.start) / 1e9 for t in self.times)

    @property
    def phased(self) -> bool:
        """Whether any sample has a phase."""
        return any(phase is not None for phase in self.phases)


def _times(times: Sequence[int]) -> tuple[int, ...]:
    result = tuple(int(t) for t in times)
    if not result:
        raise ValueError("a frame needs at least one sample")
    if any(later <= earlier for earlier, later in pairwise(result)):
        # Two samples at one time would be one point in the store, and one of them would be lost.
        raise ValueError("the sample times of a frame must be strictly ascending")
    return result


def _signals(
    signals: Mapping[str, Sequence[float]], count: int
) -> dict[str, tuple[float, ...]]:
    if not signals:
        raise ValueError("a frame needs at least one signal")
    result = {}
    for name, values in signals.items():
        if not _COLUMN_NAME.fullmatch(name) or name in _RESERVED:
            raise ValueError(
                f"{name!r} is not a signal name: use letters, digits, `_` and `-`, do not start "
                f"with `_`, and do not use {sorted(_RESERVED)}"
            )
        if len(values) != count:
            raise ValueError(
                f"signal {name!r} has {len(values)} values for {count} sample times"
            )
        result[name] = tuple(_value(name, value) for value in values)
    return result


def _value(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"signal {name!r} holds {value!r}, which is not a number")
    if not math.isfinite(value):
        # The line protocol has no spelling for NaN or an infinity.
        raise ValueError(f"signal {name!r} holds {value!r}; a value must be finite")
    return float(value)


def _phases(phases: Sequence[str | None], count: int) -> tuple[str | None, ...]:
    result = tuple(phases)
    if len(result) != count:
        raise ValueError(f"phases has {len(result)} values for {count} sample times")
    for phase in result:
        if phase is not None and phase not in PHASES:
            raise ValueError(f"{phase!r} is not a phase: use one of {PHASES}, or None")
    return result


def _source(source: str | None) -> str | None:
    if source is not None and not _TAG_VALUE.fullmatch(source):
        raise ValueError(
            f"source {source!r} must be a non-empty word without spaces, commas, `=`, quotes "
            "or backslashes"
        )
    return source
