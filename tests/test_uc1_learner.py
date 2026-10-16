"""The learner's placeholder rule, run on fixed curves, without a store.

The rule is a placeholder, not a regression: the new maximum dynamic torque of a Screw type is 20 %
above the highest dynamic torque that the given ScrewIntact attempts recorded in their Disassembly
phase. Each expected value below is worked out by hand from the curves the test gives. The curves
that `frame` makes are arbitrary test values, not measured; the one recording is legacy mock data.
"""

from __future__ import annotations

import pytest

from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.learner import placeholder_max_dynamic_torque
from jms_kapps.uc1.replay import mock_frame


def frame(phases_and_torques: list[tuple[str | None, float]]) -> Frame:
    """A frame with one sample a second, each with its phase and its torque."""
    count = len(phases_and_torques)
    return Frame(
        times=[i * 1_000_000_000 for i in range(count)],
        signals={
            ROBOT_POSITION: [0.0] * count,
            AXIAL_FORCE: [0.0] * count,
            UNSCREWING_TORQUE: [torque for _, torque in phases_and_torques],
        },
        phases=[phase for phase, _ in phases_and_torques],
    )


def test_the_maximum_is_20_percent_above_the_highest_dynamic_torque() -> None:
    """Only the Disassembly samples count, and a torque counts by its magnitude: the highest is
    the -0.9 N·m, so the rule gives 1.08 N·m. The 2.0 of the Preparation and the 3.0 outside every
    phase are not dynamic torques."""
    attempt = frame(
        [
            ("Approach", 0.0),
            ("Form-fit", 0.6),
            ("Preparation", 2.0),
            ("Disassembly", 0.3),
            ("Disassembly", -0.9),
            ("Disassembly", 0.5),
            (None, 3.0),
        ]
    )

    assert placeholder_max_dynamic_torque([attempt]) == pytest.approx(1.08)


def test_the_rule_takes_the_highest_dynamic_torque_of_all_the_attempts() -> None:
    first = frame([("Disassembly", 0.4), ("Disassembly", 0.2)])
    second = frame([("Disassembly", 1.0), ("Disassembly", 0.1)])

    assert placeholder_max_dynamic_torque([first, second]) == pytest.approx(1.2)


def test_the_rule_gives_no_value_without_a_disassembly_sample() -> None:
    """An attempt recorded without phases, or without a Disassembly, shows no dynamic torque."""
    unphased = frame([(None, 0.8), (None, 0.9)])

    assert placeholder_max_dynamic_torque([unphased]) is None
    assert placeholder_max_dynamic_torque([]) is None


def test_the_rule_gives_no_value_for_a_frame_without_an_unscrewing_torque() -> None:
    """A frame read back from the store holds only the signals that it recorded."""
    no_torque = Frame(
        times=[0, 1_000_000_000],
        signals={AXIAL_FORCE: [0.5, 0.6]},
        phases=["Disassembly", "Disassembly"],
    )

    assert placeholder_max_dynamic_torque([no_torque]) is None


def test_the_rule_on_the_legacy_mock_success_recording() -> None:
    """The recording's Disassembly torques are 0.7, 0.6, 0.5, 0.4 and 0.3 (`success.csv`, legacy
    mock data, units unknown), so the rule gives 0.84, not the legacy mock 1.5 of `:M4`."""
    assert placeholder_max_dynamic_torque(
        [mock_frame("success", start=0)]
    ) == pytest.approx(0.84)
