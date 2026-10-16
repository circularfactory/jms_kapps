"""The seed of use case 1: the demo's two grinders and their Screws, the model, and `:M4`'s mock
thresholds.

These tests need no store. What they check is the one thing a seed must get right: that the two
recordings the paper's artifact ships classify as what their names say, under the values the seed
writes. A threshold that does not fit those recordings makes the demo report a wrong outcome for a
good screw, and raise on the missing one. `test_uc1_seed_live.py` writes the seed to a store.
"""

from __future__ import annotations

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.anomaly_detector import attempt_signals
from jms_kapps.uc1.decision_tree import classify
from jms_kapps.uc1.effective_values import resolve
from jms_kapps.uc1.replay import MOCK_RECORDINGS, mock_frame
from jms_kapps.uc1.seed import (
    GRINDER,
    GRINDER_2,
    GRINDERS,
    M4_VALUES,
    PLACEMENT_VALUES,
    PRODUCT_TYPE,
    RECORDING_OF,
    SCREW,
    SCREW_2,
)
from jms_kapps.uc1.vocabulary import JMS, PLACEMENT, TYPE_LEVEL

# What each of the artifact's two recordings must classify as. Their names say it, and a demo that
# says anything else is showing the thresholds, not the screw.
EXPECTED = {"success": "ScrewIntact", "missing_screw": "MissingScrew"}


def seeded_values():
    """The effective values of the seeded Screws: their own placement values, else `:M4`'s."""
    return resolve(
        SCREW,
        {prop: [value] for prop, value in PLACEMENT_VALUES.items()},
        JMS.M4,
        {prop: [value] for prop, value in M4_VALUES.items()},
    )


@pytest.mark.parametrize("recording", sorted(EXPECTED))
def test_each_recording_of_the_artifact_classifies_as_its_name(recording: str) -> None:
    """The acceptance the seed exists for. With `:M4`'s legacy mock values and placement values
    that do not fit, `success` reaches `LooseAnchor` or `HeadOccluded` and `missing_screw` raises,
    because the screw-head band then sits where the recording never touches and the preload is
    reached only at the last sample."""
    outcome = classify(attempt_signals(mock_frame(recording)), seeded_values())

    assert outcome == EXPECTED[recording]


def test_the_seed_covers_every_recording_the_artifact_ships() -> None:
    """A recording added later must be classified too, or this file stops proving what it claims."""
    assert set(MOCK_RECORDINGS) == set(EXPECTED)


def test_the_loosening_minimum_stays_above_the_preload_minimum() -> None:
    """So that a force held at the preload level is not read as a rise (Max Goebels, 2026-10-09).
    The ticket of the UC1 runtime names this as a criterion of the seed."""
    assert (
        M4_VALUES[JMS.hasMinLooseningAxialForce]
        > M4_VALUES[JMS.hasMinPreloadAxialForce]
    )


def test_the_screw_type_holds_every_type_level_value_and_no_placement_value() -> None:
    """A placement value belongs to where the screw sits, not to its type."""
    assert set(M4_VALUES) == set(TYPE_LEVEL)
    assert set(M4_VALUES).isdisjoint(PLACEMENT)


def test_the_screws_hold_every_placement_value_and_override_nothing() -> None:
    """UC1's demo Screws take their thresholds from their type; only where each sits is its own."""
    assert set(PLACEMENT_VALUES) == set(PLACEMENT)
    assert set(PLACEMENT_VALUES).isdisjoint(TYPE_LEVEL)


def test_the_demo_individuals_are_six_and_distinct() -> None:
    """Two grinders, one Screw in each, one model they are — one Screw per grinder for UC1."""
    individuals = (GRINDER, GRINDER_2, SCREW, SCREW_2, PRODUCT_TYPE)

    assert all(isinstance(iri, IRI) for iri in individuals)
    assert len(set(individuals)) == 5


def test_the_grinders_cover_exactly_the_demo_grinders() -> None:
    """`GRINDERS` is the tuple that `jms-uc1` passes to the planner, both grinders, in order."""
    assert GRINDERS == (GRINDER, GRINDER_2)


def test_recording_of_covers_exactly_the_two_demo_screws() -> None:
    """`RECORDING_OF` names a recording for each demo Screw, and for no one else."""
    assert set(RECORDING_OF) == {SCREW, SCREW_2}
    assert RECORDING_OF[SCREW] == "success"
    assert RECORDING_OF[SCREW_2] == "missing_screw"


@pytest.mark.parametrize(
    "screw, recording",
    [(SCREW, "success"), (SCREW_2, "missing_screw")],
)
def test_each_demo_screws_recording_classifies_as_expected(
    screw: IRI, recording: str
) -> None:
    """The recording that `RECORDING_OF` names for each Screw classifies as its name says, under
    the seeded values. A demo run shows both exits, `ScrewIntact` and `MissingScrew`."""
    outcome = classify(attempt_signals(mock_frame(recording)), seeded_values())

    assert outcome == EXPECTED[recording]
