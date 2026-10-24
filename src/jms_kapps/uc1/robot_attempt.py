"""The unscrewing attempt over MQTT: the screwing resource sets the robot's setpoints, starts the
attempt, collects every sample the robot publishes, and builds the frame.

It has the signature of every attempt of the screwing resource (`screwing_resource.Attempt`): a
Screw and the seven values of its Screw type in, the attempt's frame out, and it raises when the
attempt fails, so that the operation ends `failed` with the reason. It replaces the replay of a
recording in `jms-uc1`; a test may still inject any attempt.

**What happens on the wire.** The attempt opens the sink first, so that no sample is missed. It
writes the seven setpoints, one per value of the Screw type (`vocabulary.SETPOINT_OF` names the
robot's setpoint of each), then the start command with the Screw's IRI; each write goes out
through the parameter's write connector (`robot_binding.Setpoints`). The robot answers with its
state, `running`, then each sample on the three reading topics, then `done` with the count of
samples per reading and the source tag of what it replayed, or `failed` with a reason. Each
connector has a client of its own, so `done` may arrive before the last readings: the attempt
ends once `done` has arrived and every reading holds exactly as many samples as `done` counted.
A `failed` ends it at once. `idle`, `running`, and the robot's echo of each setpoint are ignored.

**The frame.** Each reading's samples carry the sample's time, its value and its phase. The three
readings of one sample share one time; the frame holds one value of each signal at each time, the
phases, and the source the robot named.

**Timeout.** An attempt that has not ended within `ATTEMPT_TIMEOUT_SECONDS` raises: above the
longest recording (about seven seconds) and well below the planner's operation timeout, so that
a robot that does not answer ends the operation `failed`, with the reason readable on the page
and the screwing resource's dashboard, before the planner gives up on it.

Every public name of this module is an interim name, marked so in the glossary.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from kapps_triplestore_interface import IRI

from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.robot_binding import Messages, NoMessage, Sink
from jms_kapps.uc1.vocabulary import JMS, SETPOINT_OF

logger = logging.getLogger("jms_kapps.uc1.robot_attempt")

# How long an attempt may take from the start command to its end. The longest example recording
# plays for about seven seconds; the planner waits 300 s for an operation.
ATTEMPT_TIMEOUT_SECONDS = 60.0

# The frame's signal that each reading of the robot carries.
SIGNAL_OF: dict[IRI, str] = {
    JMS.hasRobotPosition: ROBOT_POSITION,
    JMS.hasAxialForce: AXIAL_FORCE,
    JMS.hasUnscrewingTorque: UNSCREWING_TORQUE,
}

# The states of the robot that end an attempt.
DONE = "done"
FAILED = "failed"

TypeValues = Mapping[IRI, float]
"""The seven values of the Screw type, by their type-level property: what the attempt is given."""

Write = Callable[[IRI, object], None]
"""Writes a value of a parameter of the robot, so that its write connector publishes it."""


class AttemptFailed(RuntimeError):
    """The robot reported the attempt failed, or what it sent cannot be made a frame."""


class AttemptTimeout(AttemptFailed):
    """The attempt did not end within its timeout."""


class _Sample(NamedTuple):
    """One sample of one reading, as the robot published it."""

    t: int
    v: float
    phase: str | None


@dataclass(frozen=True)
class _End:
    """What `done` said: how many samples each reading has, by reading, and the source of what
    the robot replayed."""

    counts: Mapping[IRI, int]
    source: str | None


def mqtt_attempt(
    write: Write, sink: Sink, *, timeout: float = ATTEMPT_TIMEOUT_SECONDS
) -> Callable[[IRI, TypeValues], Frame]:
    """The attempt of `jms-uc1`: the robot, driven over MQTT through the middleware's connectors.

    Args:
        write: Writes a value of a parameter of the robot: the setpoints and the start command.
        sink: Where the read connectors put every payload of the robot.
        timeout: How long the attempt may take from its start.
    """

    def attempt(screw: IRI, values: TypeValues) -> Frame:
        with sink.open() as messages:
            for type_property, value in values.items():
                write(SETPOINT_OF[type_property], value)
            write(JMS.hasAttemptCommand, str(screw))
            logger.info("the robot is started on %s", screw.fragment)
            return collect(messages, timeout=timeout)

    return attempt


def collect(messages: Messages, *, timeout: float) -> Frame:
    """The frame of the attempt that has just been started, from the messages the robot publishes
    until it reports the end.

    Raises:
        AttemptFailed: If the robot reports `failed`, with its reason; or if what it sent is not a
            frame: a reading without its time or value, counts that do not name each reading, more
            samples of a reading than counted, or readings that disagree on the sample times.
        AttemptTimeout: If the attempt has not ended within `timeout` seconds of this call.
    """
    deadline = time.monotonic() + timeout
    samples: dict[IRI, list[_Sample]] = {reading: [] for reading in SIGNAL_OF}
    end: _End | None = None
    while end is None or any(
        len(samples[reading]) != end.counts[reading] for reading in SIGNAL_OF
    ):
        try:
            parameter, payload = messages.get(timeout=deadline - time.monotonic())
        except NoMessage:
            raise AttemptTimeout(_timeout_reason(timeout, samples, end)) from None
        if parameter in SIGNAL_OF:
            samples[parameter].append(_sample(parameter, payload))
            if end is not None and len(samples[parameter]) > end.counts[parameter]:
                raise AttemptFailed(
                    f"the robot sent more samples of {parameter.fragment} than its end message "
                    f"counted ({end.counts[parameter]})"
                )
        elif parameter == JMS.hasAttemptState:
            state = payload.get("v") if isinstance(payload, Mapping) else payload
            if state == FAILED:
                reason = payload.get("reason") if isinstance(payload, Mapping) else None
                raise AttemptFailed(
                    f"the robot reports the attempt failed: {reason or 'no reason given'}"
                )
            if state == DONE:
                end = _end(payload)
        # Any other parameter: the robot's echo of a setpoint, or the command. Not a sample.
    return _frame(samples, end.source)


def _timeout_reason(
    timeout: float, samples: Mapping[IRI, list[_Sample]], end: _End | None
) -> str:
    reason = f"the robot did not end the attempt within {timeout:g} s"
    if end is None:
        return reason + "; it did not report done or failed"
    return (
        reason
        + "; it reported done, but not every reading arrived: "
        + ", ".join(
            f"{reading.fragment} {len(samples[reading])} of {end.counts[reading]}"
            for reading in SIGNAL_OF
        )
    )


def _sample(reading: IRI, payload: object) -> _Sample:
    if not isinstance(payload, Mapping) or "t" not in payload or "v" not in payload:
        raise AttemptFailed(
            f"a message of {reading.fragment} is not a sample: it holds no time t and value v, "
            f"but {payload!r}"
        )
    try:
        return _Sample(int(payload["t"]), float(payload["v"]), payload.get("phase"))
    except (TypeError, ValueError) as error:
        raise AttemptFailed(
            f"a message of {reading.fragment} is not a sample: {error}"
        ) from None


def _end(payload: Mapping) -> _End:
    counts = payload.get("samples")
    if not isinstance(counts, Mapping):
        raise AttemptFailed(
            f"the robot reports done without the count of samples per reading: {payload!r}"
        )
    by_name = {reading.fragment: reading for reading in SIGNAL_OF}
    if set(counts) != set(by_name):
        raise AttemptFailed(
            "the robot reports done with counts for "
            + ", ".join(sorted(counts))
            + "; its readings are "
            + ", ".join(sorted(by_name))
        )
    return _End(
        counts={by_name[name]: int(count) for name, count in counts.items()},
        source=payload.get("source"),
    )


def _frame(samples: Mapping[IRI, list[_Sample]], source: str | None) -> Frame:
    """The frame of the collected samples: the readings share each sample's time and phase."""
    by_reading = {
        reading: sorted(points, key=lambda sample: sample.t)
        for reading, points in samples.items()
    }
    times = [sample.t for sample in next(iter(by_reading.values()))]
    for points in by_reading.values():
        if [sample.t for sample in points] != times:
            raise AttemptFailed(
                "the readings disagree on the sample times: "
                + ", ".join(
                    f"{reading.fragment} has {len(points)} samples"
                    for reading, points in by_reading.items()
                )
            )
    phases: list[str | None] = []
    for index in range(len(times)):
        phase_of_each = {points[index].phase for points in by_reading.values()}
        if len(phase_of_each) != 1:
            raise AttemptFailed(
                f"the readings disagree on the phase of the sample at {times[index]}: "
                + ", ".join(repr(p) for p in sorted(phase_of_each, key=str))
            )
        phases.append(phase_of_each.pop())
    try:
        return Frame(
            times=times,
            signals={
                SIGNAL_OF[reading]: [sample.v for sample in points]
                for reading, points in by_reading.items()
            },
            phases=phases,
            source=source,
        )
    except (TypeError, ValueError) as error:
        raise AttemptFailed(f"what the robot sent is not a frame: {error}") from None
