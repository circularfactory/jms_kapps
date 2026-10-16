"""The mock values the use case 1 tests seed. None of them is measured.

No known-good value exists yet for any of the ten properties. Until one does, the tests use two
kinds of stand-in, and each value below says which kind it is:

- **Legacy mock:** the four placeholder values of the paper artifact (`turtle/
  integration_usecase_JMS.ttl` on the `paper` branch at `d320118`, on `:M4_Screw`). Units unknown.
  Each is carried to the property that replaced its old one.
- **Arbitrary:** a number invented for the test, for the six properties the artifact gives no
  value for. It has no source and no unit behind it.
"""

from __future__ import annotations

from jms_kapps.uc1.seed import M4_VALUES, PLACEMENT_VALUES
from jms_kapps.uc1.vocabulary import JMS

# Legacy mock values from the paper artifact `d320118`, units unknown. Not measured.
LEGACY_MOCK_TYPE_VALUES = {
    JMS.hasMinApproachAxialForce: 0.5,  # was hasAxialForceApproach
    JMS.hasMinCounterTorque: 0.5,  # was hasLowerTighteningTorque
    JMS.hasMinLooseningAxialForce: 2.0,  # was hasLowerAxialForce
    JMS.hasMaxDynamicTorque: 1.5,  # was hasUpperDynamicLoseningTorque
}

# Arbitrary values, invented for the tests. The artifact has none for these. Not measured.
ARBITRARY_TYPE_VALUES = {
    JMS.hasMinPreloadAxialForce: 1.0,
    JMS.hasPreparationTurningAngle: 30.0,
    JMS.hasMaxLooseTorque: 3.0,
}

# Arbitrary values, invented for the tests. The artifact has no placement values. Not measured.
ARBITRARY_PLACEMENT_VALUES = {
    JMS.hasMinApproachPosition: -10.0,
    JMS.hasMinScrewHeadPosition: -2.0,
    JMS.hasMaxScrewHeadPosition: -1.0,
}

# The mock values of `:M4`: legacy mock where the artifact has one, arbitrary for the rest.
MOCK_M4_VALUES = {**LEGACY_MOCK_TYPE_VALUES, **ARBITRARY_TYPE_VALUES}

# An arbitrary override that a test Screw holds, different from `:M4`'s legacy mock 1.5.
ARBITRARY_OVERRIDE = {JMS.hasMaxDynamicTorque: 1.2}

# The two value sets the seed writes, and the only ones fitted to the artifact's recordings.
# They are defined in `jms_kapps.uc1.seed`, not here: the demo and the tests must decide with the
# same numbers, or a test proves nothing about the demo. The names stay as the tests spell them.
#
# `RECORDING_PLACEMENT_VALUES`: the approach of both frames ends at 0.0 (that of `success` touches
# the head there, that of `missing_screw` touches nothing), so the depth limit lies above 0.0 and
# the screw-head band holds it.
#
# `SUCCESS_PASSING_M4_VALUES`: `:M4`'s mock values with two minimums the frame replayed from
# `success` passes, chosen by reading the frame — its preparation reaches 0.1 on its first sample
# and 1.0 on its second, so the preload is reached at once and the turn rises to the loosening
# minimum. They replace the arbitrary preload minimum 1.0 and the legacy mock loosening minimum
# 2.0, with which the same frame reaches LooseAnchor.
RECORDING_PLACEMENT_VALUES = PLACEMENT_VALUES
SUCCESS_PASSING_M4_VALUES = M4_VALUES
