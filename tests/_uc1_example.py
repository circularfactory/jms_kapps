"""The example values the use case 1 tests seed. None of them is measured.

No known-good value exists for any of the ten properties. The tests use two kinds of stand-in,
and each value below says which kind it is:

- **Artifact:** four placeholder values. No unit is stated.
- **Arbitrary:** a number invented for the test, for the six properties that have no placeholder
  value. It has no source and no unit behind it.
"""

from __future__ import annotations

from jms_kapps.uc1.seed import M4_VALUES, PLACEMENT_VALUES
from jms_kapps.uc1.vocabulary import JMS

# Placeholder values, no unit stated. Not measured.
ARTIFACT_TYPE_VALUES = {
    JMS.hasMinApproachAxialForce: 0.5,
    JMS.hasMinCounterTorque: 0.5,
    JMS.hasMinLooseningAxialForce: 2.0,
    JMS.hasMaxDynamicTorque: 1.5,
}

# Arbitrary values, invented for the tests. No placeholder exists for these. Not measured.
ARBITRARY_TYPE_VALUES = {
    JMS.hasMinPreloadAxialForce: 1.0,
    JMS.hasPreparationTurningAngle: 30.0,
    JMS.hasMaxLooseTorque: 3.0,
}

# Arbitrary values, invented for the tests. No placeholder exists for these. Not measured.
ARBITRARY_PLACEMENT_VALUES = {
    JMS.hasMinApproachPosition: -10.0,
    JMS.hasMinScrewHeadPosition: -2.0,
    JMS.hasMaxScrewHeadPosition: -1.0,
}

# The example values of `:M4`: the placeholder where there is one, arbitrary for the rest.
EXAMPLE_M4_VALUES = {**ARTIFACT_TYPE_VALUES, **ARBITRARY_TYPE_VALUES}

# A type value that a Screw must not hold, different from `:M4`'s example 1.5.
STRAY_TYPE_VALUE = {JMS.hasMaxDynamicTorque: 1.2}

# The two value sets the seed writes, and the only ones fitted to the example recordings.
# They are defined in `jms_kapps.uc1.seed`, not here: the demo and the tests must decide with the
# same numbers, or a test proves nothing about the demo. The names stay as the tests spell them.
#
# `RECORDING_PLACEMENT_VALUES`: the approach of both frames ends at 0.0 (that of `success` touches
# the head there, that of `missing_screw` touches nothing), so the depth limit lies above 0.0 and
# the screw-head band holds it.
#
# `SUCCESS_PASSING_M4_VALUES`: `:M4`'s example values with two minimums the frame replayed from
# `success` passes, chosen by reading the frame — its preparation reaches 0.1 on its first sample
# and 1.0 on its second, so the preload is reached at once and the turn rises to the loosening
# minimum. They replace the arbitrary preload minimum 1.0 and the example loosening minimum
# 2.0, with which the same frame reaches LooseAnchor.
RECORDING_PLACEMENT_VALUES = PLACEMENT_VALUES
SUCCESS_PASSING_M4_VALUES = M4_VALUES
