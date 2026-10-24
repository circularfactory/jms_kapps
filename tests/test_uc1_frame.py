"""A frame, and the frame replay that makes one from an example recording.

No network and no store: these tests prove what a frame accepts and refuses, and what the replay
reads from the two example recordings that ship with the package.
"""

from __future__ import annotations

import math

import pytest

from jms_kapps.uc1.frame import (
    AXIAL_FORCE,
    PHASES,
    ROBOT_POSITION,
    SIGNALS,
    UNSCREWING_TORQUE,
    Frame,
)
from jms_kapps.uc1.replay import (
    EXAMPLE_RECORDINGS,
    EXAMPLE_SOURCE,
    PHASE_OF_LABEL,
    example_frame,
)

T0 = 1_758_800_000_000_000_000


def frame(**overrides) -> Frame:
    arguments = {
        "times": (T0, T0 + 500_000_000),
        "signals": {AXIAL_FORCE: (0.0, 0.2)},
        "phases": (None, None),
    }
    arguments.update(overrides)
    return Frame(**arguments)


def test_a_frame_holds_one_value_of_each_signal_per_sample_time():
    f = frame(signals={AXIAL_FORCE: [0.0, 0.2], ROBOT_POSITION: [1, 0.5]})

    assert f.times == (T0, T0 + 500_000_000)
    assert f.signals == {AXIAL_FORCE: (0.0, 0.2), ROBOT_POSITION: (1.0, 0.5)}
    assert f.start == T0
    assert f.stop == T0 + 500_000_000
    assert f.seconds == (0.0, 0.5)


def test_a_frame_without_phases_is_unphased():
    assert frame().phased is False
    assert frame(phases=(PHASES[0], None)).phased is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"times": ()}, "at least one sample"),
        ({"times": (T0 + 1, T0)}, "ascending"),
        ({"times": (T0, T0)}, "ascending"),
        ({"signals": {}}, "at least one signal"),
        ({"signals": {AXIAL_FORCE: (0.0,)}}, "2 sample times"),
        ({"signals": {AXIAL_FORCE: (0.0, math.nan)}}, "finite"),
        ({"signals": {AXIAL_FORCE: (0.0, math.inf)}}, "finite"),
        ({"signals": {"axial force": (0.0, 0.2)}}, "signal name"),
        ({"signals": {"_axial": (0.0, 0.2)}}, "signal name"),
        ({"signals": {"phase": (0.0, 0.2)}}, "signal name"),
        ({"signals": {"time": (0.0, 0.2)}}, "signal name"),
        ({"phases": (None,)}, "2 sample times"),
        ({"phases": ("Retract", None)}, "phase"),
        ({"source": ""}, "source"),
        ({"source": "example data"}, "source"),
        ({"source": "a,b=c"}, "source"),
    ],
)
def test_a_frame_refuses_what_the_store_could_not_hold(overrides, message):
    with pytest.raises(ValueError, match=message):
        frame(**overrides)


def test_a_frame_refuses_a_value_that_is_not_a_number():
    with pytest.raises(TypeError, match="not a number"):
        frame(signals={AXIAL_FORCE: (0.0, True)})
    with pytest.raises(TypeError, match="not a number"):
        frame(signals={AXIAL_FORCE: (0.0, "0.2")})


def test_the_replay_ships_the_two_example_recordings():
    assert EXAMPLE_RECORDINGS == ("success", "missing_screw")


def test_the_replay_reads_an_example_frame_with_its_three_signals():
    f = example_frame("success", start=T0)

    assert set(f.signals) == set(SIGNALS)
    assert len(f.times) == 14
    assert f.seconds[:3] == (0.0, 0.5, 1.0)
    assert f.seconds[-1] == 6.5
    assert f.signals[ROBOT_POSITION][:3] == (1.0, 0.5, 0.0)
    assert f.signals[AXIAL_FORCE][2] == 0.5
    assert f.signals[UNSCREWING_TORQUE][3:5] == (1.0, -1.0)


def test_the_replay_takes_the_success_frame_s_phases_from_its_labels():
    f = example_frame("success", start=T0)

    assert f.phases == (
        ("Approach",) * 2
        # "form-fit - touches screw head": the touching sample ends the Approach
        + ("Approach",)
        + ("Form-fit",)
        + ("Preparation",) * 2
        + ("Disassembly",) * 5
        # "disassembly complete - retract", "retract", "retract complete"
        + (None,) * 3
    )


def test_the_replay_gives_the_missing_screw_no_form_fit():
    f = example_frame("missing_screw", start=T0)

    # "form-fit - touches screw head --> Nope" is the touching sample, so it is the Approach's,
    # though it touches nothing. No label, then "Status: Missing screw": no phase.
    assert f.phases == ("Approach", "Approach", "Approach", None, None)


def test_the_touching_sample_of_each_example_frame_is_the_approach_s():
    """The decision tree finds contact in the Approach, so the sample where the tool reaches the
    screw head belongs to it, though the recordings' labels call it form-fit."""
    touching = [label for label in PHASE_OF_LABEL if "touches screw head" in label]

    assert touching
    assert {PHASE_OF_LABEL[label] for label in touching} == {"Approach"}


def test_the_replay_maps_each_label_to_one_of_the_four_phases_or_to_none():
    assert set(PHASE_OF_LABEL.values()) == {*PHASES, None}


def test_the_replay_refuses_a_label_that_it_cannot_map(monkeypatch):
    monkeypatch.delitem(PHASE_OF_LABEL, "retract")

    with pytest.raises(ValueError, match="'retract'"):
        example_frame("success", start=T0)


def test_the_replay_stamps_an_example_frame_from_its_start():
    f = example_frame("missing_screw", start=T0)

    assert f.times == tuple(T0 + n * 500_000_000 for n in range(5))
    assert f.signals[ROBOT_POSITION] == (1.0, 0.5, 0.0, -0.5, -1.0)


def test_a_replayed_example_frame_names_its_recording():
    assert EXAMPLE_SOURCE == "example-d320118"
    assert example_frame("success", start=T0).source == "example-d320118/success"


def test_the_replay_stamps_an_example_frame_now_by_default():
    import time

    before = time.time_ns()
    f = example_frame("success")

    assert before <= f.start <= time.time_ns()


def test_the_replay_refuses_a_recording_it_does_not_ship():
    with pytest.raises(ValueError, match="success, missing_screw"):
        example_frame("rounded_head")
