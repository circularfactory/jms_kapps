"""The decision tree: the recorded signals of one unscrewing attempt and the effective values in,
one Unscrewing outcome out.

The tree is Figure 2 of the UC1 anomaly-detection paper, with its drawn exits only. It asks its
nodes in the figure's order, and the first node that fails decides. It is a pure function: it
reads no graph and does no I/O, so a detector can feed it from wherever its values and signals
come from.

In the robot program, the tree's loops are stepwise searches: move by one step, then ask again.
Over a recorded trace, each such question becomes a search for the first sample at which a signal
crosses its threshold, and every rise-or-exceed check below is one of those searches.

Each phase is read by these nodes:

- Approach. Contact is the first sample at which the axial force reaches the minimum approach
  axial force. The depth limit is passed at the first sample whose position is below the minimum
  approach position. Whichever comes first decides; on the same sample, contact wins, since the
  figure asks about the force first. The depth limit first is a MissingScrew. A recorded approach
  that ends with neither has reached no drawn exit of its loop, and is refused. The position at
  contact must lie in the screw-head band: below it is a MissingHead, above it is HeadOccluded.
  The figure draws one value, `P_Y,screw head`, for the band's two ends.
- Form-fit. The counter-torque is present if some sample reaches its minimum. If none does, the
  outcome is RoundedHeadOrWrongType.
- Preparation. The tool steps down until the axial force reaches the preload minimum, and the
  preparation turn is every sample after that one. A loose torque above its maximum anywhere in
  the turn is StuckAtBreakaway. If the axial force never reaches its loosening minimum in the
  turn, the outcome is LooseAnchor. The figure draws no failure exit from the preload loop, so
  until the domain gains one, the loop ends where the recorded trace ends: the turn then holds no
  sample, no loose torque is exceeded, no axial force rises, and the attempt reaches LooseAnchor.
- Disassembly. A dynamic torque above its maximum anywhere is a ThreadFault. An attempt that gets
  this far without one is ScrewIntact.

Every threshold is an inclusive bound of what passes: a minimum is met by a value equal to it,
and a maximum is exceeded only by a value above it. The depth limit and the screw-head band follow
the same rule, as drawn (`P_Y < P_Y,min`). Torques are compared by their magnitude, because their
sense depends on the way the tool turns and the thresholds bound only their size. Forces and
positions are compared as recorded.

Each field of `EffectiveValues` is named after the property that holds the value in the use
case's ontology, `ontology/integration_usecase.ttl`, without its `has` prefix, in snake case:
`hasMinApproachAxialForce` is `min_approach_axial_force`. The outcomes are the ontology's
Unscrewing outcomes, by name. `effective_values` resolves the ten values of one Screw from the
graph into this same class. The preparation turning angle is a Process parameter that no node
reads; the tree takes it so that its input is the same ten values an attempt's Decision snapshot
records.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

# The exits of the tree, named as the use case's outcome vocabulary names its outcome individuals.
UnscrewingOutcome = Literal[
    "ScrewIntact",
    "MissingScrew",
    "MissingHead",
    "HeadOccluded",
    "RoundedHeadOrWrongType",
    "StuckAtBreakaway",
    "LooseAnchor",
    "ThreadFault",
]

UNSCREWING_OUTCOMES: tuple[UnscrewingOutcome, ...] = get_args(UnscrewingOutcome)


@dataclass(frozen=True)
class PhaseSignals:
    """The signals of one phase, sampled together: sample i of each is taken at the same instant.

    `axial_force` is the axial force F_Y, in N, positive while the tool presses on the screw.
    `position` is the position P_Y of the tool along the screw axis y of the robot frame, in mm;
    the approach moves it toward smaller values. `torque` is the torque around the screw axis, in
    N·m, in either sense.
    """

    axial_force: Sequence[float]
    position: Sequence[float]
    torque: Sequence[float]

    def __post_init__(self) -> None:
        if not len(self.axial_force) == len(self.position) == len(self.torque):
            raise ValueError("the signals of a phase must have the same length")


@dataclass(frozen=True)
class AttemptSignals:
    """The recorded signals of one unscrewing attempt, by phase, in the order it runs them."""

    approach: PhaseSignals
    form_fit: PhaseSignals
    preparation: PhaseSignals
    disassembly: PhaseSignals


@dataclass(frozen=True)
class EffectiveValues:
    """The ten values one unscrewing attempt is classified with."""

    # Detection thresholds, in N and N·m.
    min_approach_axial_force: float
    min_counter_torque: float
    min_preload_axial_force: float
    max_loose_torque: float
    min_loosening_axial_force: float
    max_dynamic_torque: float
    # The Process parameter, in degrees.
    preparation_turning_angle: float
    # Placement values: the depth limit of the approach and the screw-head band, in mm.
    min_approach_position: float
    min_screw_head_position: float
    max_screw_head_position: float


def classify(signals: AttemptSignals, values: EffectiveValues) -> UnscrewingOutcome:
    """The exit of the decision tree that one unscrewing attempt reached.

    Raises:
        ValueError: The recorded approach ends before contact and above the depth limit.
    """
    approach = signals.approach
    contact = _first_crossing(
        approach.axial_force, lambda f: f >= values.min_approach_axial_force
    )
    too_deep = _first_crossing(
        approach.position, lambda p: p < values.min_approach_position
    )
    if too_deep is not None and (contact is None or too_deep < contact):
        return "MissingScrew"
    if contact is None:
        raise ValueError(
            "the recorded approach ends before contact and above the depth limit"
        )
    at_contact = approach.position[contact]
    if at_contact < values.min_screw_head_position:
        return "MissingHead"
    if at_contact > values.max_screw_head_position:
        return "HeadOccluded"

    engaged = _first_crossing(
        signals.form_fit.torque, lambda t: abs(t) >= values.min_counter_torque
    )
    if engaged is None:
        return "RoundedHeadOrWrongType"

    preparation = signals.preparation
    preload = _first_crossing(
        preparation.axial_force, lambda f: f >= values.min_preload_axial_force
    )
    # With no preload, the loop ends where the trace ends, and the turn holds no sample.
    turn_start = len(preparation.axial_force) if preload is None else preload + 1
    stuck = _first_crossing(
        preparation.torque[turn_start:], lambda t: abs(t) > values.max_loose_torque
    )
    if stuck is not None:
        return "StuckAtBreakaway"
    rise = _first_crossing(
        preparation.axial_force[turn_start:],
        lambda f: f >= values.min_loosening_axial_force,
    )
    if rise is None:
        return "LooseAnchor"

    seized = _first_crossing(
        signals.disassembly.torque, lambda t: abs(t) > values.max_dynamic_torque
    )
    if seized is not None:
        return "ThreadFault"
    return "ScrewIntact"


def _first_crossing(
    signal: Sequence[float], crossed: Callable[[float], bool]
) -> int | None:
    """The index of the first sample at which `signal` has crossed its threshold, or None."""
    return next((i for i, sample in enumerate(signal) if crossed(sample)), None)
