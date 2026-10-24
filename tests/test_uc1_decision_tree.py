"""The decision tree of use case 1: the signals of one unscrewing attempt and the effective values
in, one Detection result out, where the first failing node decides.

Every signal here is synthetic. One intact attempt is the base, and each test changes the one
phase that sends it to another exit.
"""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import fields, replace
from pathlib import Path

import pytest

from jms_kapps.uc1 import decision_tree
from jms_kapps.uc1.decision_tree import (
    DETECTION_RESULTS,
    AttemptSignals,
    DetectionResult,
    EffectiveValues,
    PhaseSignals,
    classify,
)

VALUES = EffectiveValues(
    min_approach_axial_force=5.0,
    min_counter_torque=0.5,
    min_preload_axial_force=20.0,
    max_loose_torque=3.0,
    min_loosening_axial_force=25.0,
    max_dynamic_torque=1.5,
    preparation_turning_angle=360.0,
    min_approach_position=-10.0,
    min_screw_head_position=-6.0,
    max_screw_head_position=-4.0,
)


def phase(
    *,
    force: Sequence[float] = (),
    position: Sequence[float] = (),
    torque: Sequence[float] = (),
) -> PhaseSignals:
    """One phase from the signals a test cares about; any signal left out is zero throughout."""
    length = max(len(force), len(position), len(torque))
    return PhaseSignals(
        axial_force=force or [0.0] * length,
        position=position or [0.0] * length,
        torque=torque or [0.0] * length,
    )


# Contact at -5 mm, inside the screw-head band, well above the approach's depth limit.
APPROACH = phase(force=[0.0, 0.0, 8.0], position=[-3.0, -4.0, -5.0])
# The counter-torque reaches its minimum.
FORM_FIT = phase(torque=[0.1, 0.8, 0.2])
# The preload is reached at the third sample; the turn after it neither sticks nor lacks force.
PREPARATION = phase(
    force=[8.0, 15.0, 22.0, 26.0, 28.0], torque=[0.0, 0.0, 0.0, 2.0, 1.0]
)
# The dynamic torque stays below its maximum while the screw is turned out.
DISASSEMBLY = phase(torque=[1.0, 0.8, 0.5], position=[-5.0, -4.0, -3.0])

INTACT = AttemptSignals(
    approach=APPROACH,
    form_fit=FORM_FIT,
    preparation=PREPARATION,
    disassembly=DISASSEMBLY,
)


def test_an_attempt_that_passes_every_node_is_screw_intact():
    assert classify(INTACT, VALUES) == "ScrewIntact"


def test_an_approach_that_passes_the_depth_limit_before_contact_is_a_missing_screw():
    approach = phase(force=[0.0, 0.0, 0.0], position=[-8.0, -9.5, -10.5])

    assert classify(replace(INTACT, approach=approach), VALUES) == "MissingScrew"


def test_contact_above_the_screw_head_band_is_an_occluded_head():
    approach = phase(force=[0.0, 8.0], position=[-2.0, -3.0])

    assert classify(replace(INTACT, approach=approach), VALUES) == "HeadOccluded"


def test_contact_below_the_screw_head_band_is_a_missing_head():
    approach = phase(force=[0.0, 0.0, 8.0], position=[-5.0, -6.5, -7.0])

    assert classify(replace(INTACT, approach=approach), VALUES) == "MissingHead"


def test_contact_on_the_sample_that_passes_the_depth_limit_counts_as_contact():
    # Figure 2 asks about the axial force before it asks about the depth limit.
    approach = phase(force=[0.0, 8.0], position=[-9.0, -10.5])

    assert classify(replace(INTACT, approach=approach), VALUES) == "MissingHead"


def test_contact_after_the_depth_limit_is_passed_is_still_a_missing_screw():
    approach = phase(force=[0.0, 0.0, 8.0], position=[-9.0, -10.5, -11.0])

    assert classify(replace(INTACT, approach=approach), VALUES) == "MissingScrew"


def test_an_approach_that_ends_with_neither_contact_nor_the_depth_limit_is_refused():
    approach = phase(force=[0.0, 0.0], position=[-3.0, -4.0])

    with pytest.raises(ValueError, match="approach"):
        classify(replace(INTACT, approach=approach), VALUES)


def test_a_form_fit_with_no_counter_torque_is_a_rounded_head_or_wrong_type():
    form_fit = phase(torque=[0.1, 0.3, 0.2])

    assert (
        classify(replace(INTACT, form_fit=form_fit), VALUES) == "RoundedHeadOrWrongType"
    )


def test_a_loose_torque_above_its_maximum_in_the_turn_is_stuck_at_breakaway():
    preparation = phase(force=[8.0, 22.0, 26.0, 28.0], torque=[0.0, 0.0, 3.5, 1.0])

    assert (
        classify(replace(INTACT, preparation=preparation), VALUES) == "StuckAtBreakaway"
    )


def test_torque_before_the_preload_is_reached_is_not_loose_torque():
    # The tool still steps down for its preload, so the preparation turn has not started.
    preparation = phase(force=[8.0, 22.0, 26.0], torque=[4.0, 4.0, 1.0])

    assert classify(replace(INTACT, preparation=preparation), VALUES) == "ScrewIntact"


def test_no_axial_force_rise_in_the_turn_is_a_loose_anchor():
    preparation = phase(force=[8.0, 22.0, 18.0, 12.0], torque=[0.0, 0.0, 2.0, 1.0])

    assert classify(replace(INTACT, preparation=preparation), VALUES) == "LooseAnchor"


def test_the_preload_itself_is_not_an_axial_force_rise_in_the_turn():
    # The preload sample is above the loosening minimum, but the turn only starts after it.
    preparation = phase(force=[8.0, 30.0, 18.0, 12.0], torque=[0.0, 0.0, 2.0, 1.0])

    assert classify(replace(INTACT, preparation=preparation), VALUES) == "LooseAnchor"


def test_a_preload_never_reached_ends_its_loop_with_the_trace_and_reaches_a_loose_anchor():
    # Figure 2 draws no exit from the preload loop. The loop ends where the recorded trace ends,
    # so the turn after it holds no sample: no loose torque is exceeded, and no axial force
    # rises. The torque while stepping down is not loose torque.
    preparation = phase(force=[8.0, 12.0, 15.0], torque=[0.0, 4.0, 0.0])

    assert classify(replace(INTACT, preparation=preparation), VALUES) == "LooseAnchor"


def test_a_dynamic_torque_above_its_maximum_is_a_thread_fault():
    disassembly = phase(torque=[1.0, 2.0, 0.5], position=[-5.0, -4.5, -4.0])

    assert classify(replace(INTACT, disassembly=disassembly), VALUES) == "ThreadFault"


# The sense of a torque depends on the way the tool turns; each threshold bounds only its size.
@pytest.mark.parametrize(
    ("attempt", "outcome"),
    [
        pytest.param(
            replace(INTACT, form_fit=phase(torque=[-0.1, -0.8, -0.2])),
            "ScrewIntact",
            id="counter-torque",
        ),
        pytest.param(
            replace(INTACT, preparation=phase(force=[22.0, 26.0], torque=[0.0, -3.5])),
            "StuckAtBreakaway",
            id="loose-torque",
        ),
        pytest.param(
            replace(INTACT, disassembly=phase(torque=[-1.0, -2.0])),
            "ThreadFault",
            id="dynamic-torque",
        ),
    ],
)
def test_a_torque_counts_by_its_magnitude_in_either_sense(
    attempt: AttemptSignals, outcome: DetectionResult
):
    assert classify(attempt, VALUES) == outcome


# Each phase broken so that its first node fails. The approach can fail only one way at a time.
TOO_DEEP = phase(force=[0.0, 0.0], position=[-9.0, -10.5])
OCCLUDED = phase(force=[0.0, 8.0], position=[-2.0, -3.0])
NO_COUNTER_TORQUE = phase(torque=[0.1, 0.2])
STUCK_AND_NO_RISE = phase(force=[22.0, 10.0], torque=[0.0, 3.5])
NO_RISE = phase(force=[22.0, 10.0], torque=[0.0, 1.0])
THREAD_FAULT = phase(torque=[2.0])


@pytest.mark.parametrize(
    ("attempt", "outcome"),
    [
        (
            AttemptSignals(
                TOO_DEEP, NO_COUNTER_TORQUE, STUCK_AND_NO_RISE, THREAD_FAULT
            ),
            "MissingScrew",
        ),
        (
            AttemptSignals(
                OCCLUDED, NO_COUNTER_TORQUE, STUCK_AND_NO_RISE, THREAD_FAULT
            ),
            "HeadOccluded",
        ),
        (
            AttemptSignals(
                APPROACH, NO_COUNTER_TORQUE, STUCK_AND_NO_RISE, THREAD_FAULT
            ),
            "RoundedHeadOrWrongType",
        ),
        (
            AttemptSignals(APPROACH, FORM_FIT, STUCK_AND_NO_RISE, THREAD_FAULT),
            "StuckAtBreakaway",
        ),
        (AttemptSignals(APPROACH, FORM_FIT, NO_RISE, THREAD_FAULT), "LooseAnchor"),
        (AttemptSignals(APPROACH, FORM_FIT, PREPARATION, THREAD_FAULT), "ThreadFault"),
    ],
)
def test_the_first_failing_node_decides(
    attempt: AttemptSignals, outcome: DetectionResult
):
    assert classify(attempt, VALUES) == outcome


def test_the_preparation_turning_angle_changes_no_outcome():
    for angle in (0.0, 90.0, 360.0, 720.0):
        values = replace(VALUES, preparation_turning_angle=angle)

        assert classify(INTACT, values) == "ScrewIntact"
        assert classify(replace(INTACT, preparation=NO_RISE), values) == "LooseAnchor"


# Boundary cases. A minimum is met by a value equal to it, and a maximum is exceeded only by a
# value above it: each threshold is an inclusive bound of what passes. Just below and just above
# are the nearest floats on either side.


def below(x: float) -> float:
    return math.nextafter(x, -math.inf)


def above(x: float) -> float:
    return math.nextafter(x, math.inf)


def with_contact_force(force: float) -> AttemptSignals:
    # Contact inside the band if the force rises; the depth limit on the sample after if not.
    return replace(
        INTACT, approach=phase(force=[0.0, force, 0.0], position=[-4.0, -5.0, -10.5])
    )


def with_depth(depth: float) -> AttemptSignals:
    # Contact on the sample after the one at `depth`, below the screw-head band.
    return replace(
        INTACT, approach=phase(force=[0.0, 0.0, 8.0], position=[-9.0, depth, depth])
    )


def with_contact_at(position: float) -> AttemptSignals:
    return replace(INTACT, approach=phase(force=[0.0, 8.0], position=[-3.0, position]))


def with_counter_torque(torque: float) -> AttemptSignals:
    return replace(INTACT, form_fit=phase(torque=[0.1, torque, 0.1]))


def with_preload(force: float) -> AttemptSignals:
    # If the first sample is no preload, the second is, and the turn after it holds no sample.
    return replace(INTACT, preparation=phase(force=[force, 26.0], torque=[0.0, 1.0]))


def with_loose_torque(torque: float) -> AttemptSignals:
    return replace(
        INTACT, preparation=phase(force=[22.0, 26.0, 28.0], torque=[0.0, torque, 1.0])
    )


def with_loosening_force(force: float) -> AttemptSignals:
    return replace(
        INTACT, preparation=phase(force=[22.0, force, 20.0], torque=[0.0, 2.0, 1.0])
    )


def with_dynamic_torque(torque: float) -> AttemptSignals:
    return replace(INTACT, disassembly=phase(torque=[1.0, torque, 0.5]))


# For each threshold: an attempt whose deciding sample is the argument, and the outcome below the
# threshold, at it, and above it.
BOUNDARIES: dict[
    str,
    tuple[
        Callable[[float], AttemptSignals],
        tuple[DetectionResult, DetectionResult, DetectionResult],
    ],
] = {
    "min_approach_axial_force": (
        with_contact_force,
        ("MissingScrew", "ScrewIntact", "ScrewIntact"),
    ),
    "min_approach_position": (
        with_depth,
        ("MissingScrew", "MissingHead", "MissingHead"),
    ),
    "min_screw_head_position": (
        with_contact_at,
        ("MissingHead", "ScrewIntact", "ScrewIntact"),
    ),
    "max_screw_head_position": (
        with_contact_at,
        ("ScrewIntact", "ScrewIntact", "HeadOccluded"),
    ),
    "min_counter_torque": (
        with_counter_torque,
        ("RoundedHeadOrWrongType", "ScrewIntact", "ScrewIntact"),
    ),
    "min_preload_axial_force": (
        with_preload,
        ("LooseAnchor", "ScrewIntact", "ScrewIntact"),
    ),
    "max_loose_torque": (
        with_loose_torque,
        ("ScrewIntact", "ScrewIntact", "StuckAtBreakaway"),
    ),
    "min_loosening_axial_force": (
        with_loosening_force,
        ("LooseAnchor", "ScrewIntact", "ScrewIntact"),
    ),
    "max_dynamic_torque": (
        with_dynamic_torque,
        ("ScrewIntact", "ScrewIntact", "ThreadFault"),
    ),
}


@pytest.mark.parametrize(
    ("attempt", "sample", "outcome"),
    [
        pytest.param(build, sample, outcome, id=f"{name}-{case}")
        for name, (build, outcomes) in BOUNDARIES.items()
        for threshold in [getattr(VALUES, name)]
        for case, sample, outcome in zip(
            ("below", "at", "above"),
            (below(threshold), threshold, above(threshold)),
            outcomes,
            strict=True,
        )
    ],
)
def test_each_threshold_is_an_inclusive_bound(
    attempt: Callable[[float], AttemptSignals],
    sample: float,
    outcome: DetectionResult,
):
    assert classify(attempt(sample), VALUES) == outcome


def test_the_boundary_cases_reach_every_exit_of_the_tree():
    reached = {outcome for _, outcomes in BOUNDARIES.values() for outcome in outcomes}

    assert reached == set(DETECTION_RESULTS)


def test_every_threshold_has_its_boundary_cases():
    # The preparation turning angle is a process parameter, not a threshold: no node reads it.
    thresholds = {f.name for f in fields(EffectiveValues)} - {
        "preparation_turning_angle"
    }

    assert set(BOUNDARIES) == thresholds


def test_the_signals_of_a_phase_have_one_sample_per_instant():
    with pytest.raises(ValueError, match="same length"):
        PhaseSignals(axial_force=[0.0, 8.0], position=[-3.0], torque=[0.0, 0.0])


# The tree is a pure function: it reads no graph and does no I/O, so a detector can feed it from
# wherever its values and signals come from.
GRAPH_PACKAGES = (
    "kapps_semantic_middleware",
    "kapps_ogm",
    "kapps_triplestore_interface",
    "rdflib",
    "jms_kapps.common",
)
IO_MODULES = {
    "io",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "urllib",
    "http",
}


def imported_modules(source: str) -> set[str]:
    """The top-level name of every module that `source` imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import has no module name, and counts as no standard library module.
            names.add((node.module or "").split(".")[0])
    return names


def test_the_tree_imports_only_the_standard_library_and_nothing_that_does_io():
    imported = imported_modules(
        Path(decision_tree.__file__).read_text(encoding="utf-8")
    )

    assert imported <= set(sys.stdlib_module_names) | {"__future__"}
    assert not imported & IO_MODULES


def test_importing_the_tree_loads_no_graph_ogm_or_middleware_package():
    script = "import sys, jms_kapps.uc1.decision_tree; print('\\n'.join(sys.modules))"
    loaded = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    ).stdout.split()

    assert not [m for m in loaded if m.startswith(GRAPH_PACKAGES)]


# What the detector's dashboard shows of a classification: the rule of the exit it reached, and
# the values the tree asked on its way there. The tree never reads a value it did not ask, so
# changing any other value leaves the outcome as it was.

MISSING_HEAD = phase(force=[0.0, 8.0], position=[-5.0, -7.0])


@pytest.mark.parametrize(
    ("attempt", "outcome"),
    [
        (INTACT, "ScrewIntact"),
        (replace(INTACT, approach=TOO_DEEP), "MissingScrew"),
        (replace(INTACT, approach=MISSING_HEAD), "MissingHead"),
        (replace(INTACT, approach=OCCLUDED), "HeadOccluded"),
        (replace(INTACT, form_fit=NO_COUNTER_TORQUE), "RoundedHeadOrWrongType"),
        (replace(INTACT, preparation=STUCK_AND_NO_RISE), "StuckAtBreakaway"),
        (replace(INTACT, preparation=NO_RISE), "LooseAnchor"),
        (replace(INTACT, disassembly=THREAD_FAULT), "ThreadFault"),
    ],
)
def test_a_value_the_tree_did_not_ask_on_the_way_to_an_exit_changes_nothing(
    attempt: AttemptSignals, outcome: DetectionResult
):
    assert classify(attempt, VALUES) == outcome
    asked = decision_tree.ASKED[outcome]
    for field in fields(EffectiveValues):
        if field.name in asked:
            continue
        for extreme in (-1e9, 1e9):
            assert (
                classify(attempt, replace(VALUES, **{field.name: extreme})) == outcome
            )


def test_every_exit_names_its_rule_and_the_values_it_asked():
    assert set(decision_tree.EXIT_RULE) == set(DETECTION_RESULTS)
    assert set(decision_tree.ASKED) == set(DETECTION_RESULTS)
    names = {field.name for field in fields(EffectiveValues)}
    for outcome in DETECTION_RESULTS:
        assert decision_tree.EXIT_RULE[outcome]
        assert set(decision_tree.ASKED[outcome]) <= names
        assert "preparation_turning_angle" not in decision_tree.ASKED[outcome]
