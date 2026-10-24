"""Offline tests of the MQTT attempt's collection: what the robot's messages become, with the
messages fed into the sink by the test and no connector in between. The attempt against the
stand-in over a broker is `test_uc1_robot_attempt_wire.py`."""

from __future__ import annotations

import threading
import time

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.robot_attempt import (
    ATTEMPT_TIMEOUT_SECONDS,
    SIGNAL_OF,
    AttemptFailed,
    AttemptTimeout,
    collect,
    mqtt_attempt,
)
from jms_kapps.uc1.robot_binding import Sink
from jms_kapps.uc1.seed import M4_VALUES, SCREW
from jms_kapps.uc1.vocabulary import JMS, SETPOINT_OF

READING_OF = {signal: reading for reading, signal in SIGNAL_OF.items()}


def messages_of(
    frame: Frame, *, source: str | None = None, done_first: bool = False
) -> list:
    """The messages the robot publishes for `frame`: each sample on the three reading topics,
    then `done` with the counts; or `done` first, as it may arrive when each connector has its own
    client."""
    samples = [
        (
            READING_OF[signal],
            {"t": t, "v": frame.signals[signal][i], "phase": frame.phases[i]},
        )
        for i, t in enumerate(frame.times)
        for signal in (ROBOT_POSITION, AXIAL_FORCE, UNSCREWING_TORQUE)
    ]
    done = (
        JMS.hasAttemptState,
        {
            "v": "done",
            "samples": {reading.fragment: len(frame.times) for reading in SIGNAL_OF},
            "source": source,
        },
    )
    return [done, *samples] if done_first else [*samples, done]


def fed(messages: list) -> Sink:
    """A sink that holds `messages` once opened: they are put while it is open."""
    sink = Sink()
    original_open = sink.open

    def open_and_fill():
        manager = original_open()
        inner = manager.__enter__()
        for parameter, payload in messages:
            sink.put(parameter, payload)

        class _Ctx:
            def __enter__(self):
                return inner

            def __exit__(self, *exc):
                return manager.__exit__(*exc)

        return _Ctx()

    sink.open = open_and_fill  # type: ignore[method-assign]
    return sink


def test_the_collected_frame_equals_the_frame_the_robot_replayed() -> None:
    """Both recordings, each sample on three topics, `done` last: the frame has the same times,
    signals, phases and source."""
    for name in ("success", "missing_screw"):
        frame = example_frame(name, start=1_700_000_000_000_000_000)
        sink = fed(messages_of(frame, source=frame.source))
        with sink.open() as messages:
            collected = collect(messages, timeout=1.0)
        assert collected == frame


def test_done_may_arrive_before_the_readings() -> None:
    """Each connector has a client of its own, so the end message can overtake the samples: the
    attempt waits until every reading holds as many samples as `done` counted."""
    frame = example_frame("success", start=1_700_000_000_000_000_000)
    sink = fed(messages_of(frame, source=frame.source, done_first=True))

    with sink.open() as messages:
        assert collect(messages, timeout=1.0) == frame


def test_idle_running_and_setpoint_echoes_are_ignored() -> None:
    frame = example_frame("success", start=1_700_000_000_000_000_000)
    noise = [
        (JMS.hasAttemptState, {"v": "idle"}),
        (JMS.hasAttemptState, {"v": "running"}),
        (JMS.hasRobotMaxDynamicTorque, {"v": 1.5}),
        (JMS.hasAttemptCommand, {"v": str(SCREW)}),
    ]
    sink = fed(noise + messages_of(frame, source=frame.source))

    with sink.open() as messages:
        assert collect(messages, timeout=1.0) == frame


def test_a_failed_state_raises_with_the_robots_reason() -> None:
    sink = fed(
        [(JMS.hasAttemptState, {"v": "failed", "reason": "no recording for Screw9"})]
    )

    with sink.open() as messages, pytest.raises(AttemptFailed) as error:
        collect(messages, timeout=1.0)

    assert (
        str(error.value)
        == "the robot reports the attempt failed: no recording for Screw9"
    )


def test_a_robot_that_never_answers_raises_the_timeout_with_what_was_missing() -> None:
    sink = Sink()
    started = time.monotonic()

    with sink.open() as messages, pytest.raises(AttemptTimeout) as error:
        collect(messages, timeout=0.2)

    assert time.monotonic() - started < 2.0
    assert str(error.value) == (
        "the robot did not end the attempt within 0.2 s; it did not report done or failed"
    )


def test_a_done_without_every_reading_raises_the_timeout_naming_the_counts() -> None:
    frame = example_frame("success", start=1_700_000_000_000_000_000)
    messages = messages_of(frame, source=frame.source)
    # The last sample of the torque never arrives.
    missing = next(m for m in reversed(messages) if m[0] == JMS.hasUnscrewingTorque)
    messages.remove(missing)
    sink = fed(messages)

    with sink.open() as msgs, pytest.raises(AttemptTimeout) as error:
        collect(msgs, timeout=0.2)

    assert "it reported done, but not every reading arrived" in str(error.value)
    assert f"hasUnscrewingTorque {len(frame.times) - 1} of {len(frame.times)}" in str(
        error.value
    )


def test_readings_that_disagree_on_the_sample_times_are_not_a_frame() -> None:
    frame = example_frame("success", start=1_700_000_000_000_000_000)
    messages = messages_of(frame, source=frame.source)
    # One force sample stamped one nanosecond off.
    index = next(i for i, m in enumerate(messages) if m[0] == JMS.hasAxialForce)
    parameter, payload = messages[index]
    messages[index] = (parameter, {**payload, "t": payload["t"] + 1})
    sink = fed(messages)

    with sink.open() as msgs, pytest.raises(AttemptFailed) as error:
        collect(msgs, timeout=0.2)

    assert "disagree on the sample times" in str(error.value)


def test_a_message_without_time_or_value_is_not_a_sample() -> None:
    sink = fed([(JMS.hasAxialForce, {"v": 1.0})])

    with sink.open() as messages, pytest.raises(AttemptFailed) as error:
        collect(messages, timeout=0.2)

    assert "is not a sample" in str(error.value)


def test_done_without_the_counts_is_refused() -> None:
    sink = fed([(JMS.hasAttemptState, {"v": "done"})])

    with sink.open() as messages, pytest.raises(AttemptFailed) as error:
        collect(messages, timeout=0.2)

    assert "without the count of samples" in str(error.value)


def test_the_attempt_writes_the_seven_setpoints_then_the_start_with_the_sink_open() -> (
    None
):
    """Each type-level value goes to its robot setpoint, in the order given, then the start command
    carries the Screw's IRI; the sink is open before the first write, so no sample is missed."""
    frame = example_frame("success", start=1_700_000_000_000_000_000)
    sink = Sink()
    writes: list[tuple[IRI, object, bool]] = []

    def write(parameter: IRI, value: object) -> None:
        writes.append((parameter, value, sink.is_open))
        if parameter == JMS.hasAttemptCommand:
            for p, payload in messages_of(frame, source=frame.source):
                sink.put(p, payload)

    attempt = mqtt_attempt(write, sink, timeout=1.0)
    result = attempt(SCREW, M4_VALUES)

    assert result == frame
    assert [w[0] for w in writes] == [
        *(SETPOINT_OF[p] for p in M4_VALUES),
        JMS.hasAttemptCommand,
    ]
    assert [w[1] for w in writes][:-1] == list(M4_VALUES.values())
    assert writes[-1][1] == str(SCREW)
    assert all(open_ for _, _, open_ in writes)
    assert not sink.is_open


def test_the_attempt_runs_off_the_connectors_thread() -> None:
    """The sink is filled from another thread while the attempt collects: a put from the event
    loop's thread reaches the worker thread's wait."""
    frame = example_frame("missing_screw", start=1_700_000_000_000_000_000)
    sink = Sink()

    def write(parameter: IRI, value: object) -> None:
        if parameter == JMS.hasAttemptCommand:

            def publish() -> None:
                time.sleep(0.05)
                for p, payload in messages_of(
                    frame, source=frame.source, done_first=True
                ):
                    sink.put(p, payload)

            threading.Thread(target=publish, daemon=True).start()

    assert mqtt_attempt(write, sink, timeout=2.0)(SCREW, M4_VALUES) == frame


def test_the_timeout_sits_between_the_longest_recording_and_the_planners_operation_timeout() -> (
    None
):
    from jms_kapps.uc1.planner import DEFAULT_TIMEOUT_SECONDS

    longest = max(example_frame(n).seconds[-1] for n in ("success", "missing_screw"))
    assert longest < ATTEMPT_TIMEOUT_SECONDS < DEFAULT_TIMEOUT_SECONDS
