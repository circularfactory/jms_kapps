"""The effective values of one Screw: the ten values that one decision of the detector uses.

A Screw type holds the detection thresholds and the preparation turning angle, and only the Screw
type: the resolution refuses a Screw that is given one, and the reader does not ask a Screw for
them. The placement values (the approach depth limit and the screw-head band) depend on where the screw is, not on its type, so only the Screw holds them.

The graph does not compute this, and the object-graph mapper does not either: it reads what one
individual holds, with no inheritance from one individual to another. So the rule is here, where
the detector and the learner can both use it.

A decision needs all ten values. When a value is missing, when the individual it is read from
holds it more than once, or when it is not a finite number, the resolution fails and names each
problem. The decision tree never runs with a value missing. The resolution gives the decision
tree's own `EffectiveValues`, so the detector passes it to `classify()` as it is.

The ontology declares each of the ten functional, so an individual holds at most one value of it.
The triple store does not refuse a second value, and a write that bypasses the object-graph mapper
can store one. So the resolution still checks, and refuses two values instead of choosing one.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from kapps_ogm import OGM, ClassScope
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.decision_tree import EffectiveValues
from jms_kapps.uc1.scope_log import log_scope
from jms_kapps.uc1.vocabulary import JMS, PLACEMENT, TYPE_LEVEL

logger = logging.getLogger(__name__)

# What one read of a Screw takes for its effective values: the three placement values the Screw
# holds, and, through its Screw type, the seven values the type holds. The Screw is not asked for
# a type-level value: the ontology's domain of those names the Screw type and the parameter
# copy only, and the mapper refuses a scope with a property outside the class's domain. One
# projection, so the detector writes no SPARQL: reads go through one projection. The walk through `:hasScrewType` works,
# because the mapper specifies a nested individual by the property's range, here `:ScrewType`,
# which declares the seven properties.
_CHAINS: list[list[str | IRI]] = [[prop] for prop in PLACEMENT]
_CHAINS += [[JMS.hasScrewType, prop] for prop in TYPE_LEVEL]
EFFECTIVE_VALUES_SCOPE = ClassScope.from_property_chains(_CHAINS)


class UnresolvedValues(ValueError):
    """A Screw has no usable effective value for one or more of the ten properties."""

    def __init__(self, screw: IRI, problems: Sequence[str]) -> None:
        self.screw = screw
        self.problems = tuple(problems)
        super().__init__(
            f"The Screw <{screw}> has no usable effective value: " + "; ".join(problems)
        )


PROPERTY_OF_FIELD: dict[str, IRI] = {
    "min_approach_axial_force": JMS.hasMinApproachAxialForce,
    "min_counter_torque": JMS.hasMinCounterTorque,
    "min_preload_axial_force": JMS.hasMinPreloadAxialForce,
    "preparation_turning_angle": JMS.hasPreparationTurningAngle,
    "max_loose_torque": JMS.hasMaxLooseTorque,
    "min_loosening_axial_force": JMS.hasMinLooseningAxialForce,
    "max_dynamic_torque": JMS.hasMaxDynamicTorque,
    "min_approach_position": JMS.hasMinApproachPosition,
    "min_screw_head_position": JMS.hasMinScrewHeadPosition,
    "max_screw_head_position": JMS.hasMaxScrewHeadPosition,
}


def as_properties(values: EffectiveValues) -> dict[IRI, float]:
    """The values by property, as a parameter copy records them."""
    return {prop: getattr(values, name) for name, prop in PROPERTY_OF_FIELD.items()}


@dataclass(frozen=True)
class EffectiveValuesWithOrigin:
    """The effective values of one Screw, with where each came from.

    `origin` names, for each of the ten properties, the individual whose value counts: the Screw
    for a placement value, its Screw type for a type-level one. `type_values` is what the Screw
    type holds of the seven type-level values, each it holds once and as a finite number.
    """

    screw: IRI
    values: EffectiveValues
    origin: dict[IRI, IRI]
    screw_type: IRI | None
    type_values: dict[IRI, float]


def resolve(
    screw: IRI,
    own: Mapping[IRI, Sequence[object]],
    screw_type: IRI | None,
    inherited: Mapping[IRI, Sequence[object]],
) -> EffectiveValues:
    """The effective values of `screw`: the placement values of the Screw, the type-level values
    of its Screw type.

    `resolve_with_origin` gives the same values with where each came from.

    Raises:
        UnresolvedValues: As `resolve_with_origin` does.
    """
    return resolve_with_origin(screw, own, screw_type, inherited).values


def resolve_with_origin(
    screw: IRI,
    own: Mapping[IRI, Sequence[object]],
    screw_type: IRI | None,
    inherited: Mapping[IRI, Sequence[object]],
) -> EffectiveValuesWithOrigin:
    """The effective values of `screw`: the placement values of the Screw, the type-level values
    of its Screw type, with where each came from.

    `own` holds the values of the Screw and `inherited` the values of its Screw type, each as a
    list for each property, as the graph holds them. A placement value is read from `own` only,
    a type-level value from `inherited` only. Each property is functional, so two values on one
    holder break the graph's contract. A type-level value in `own` is a problem too: the Screw
    type is the only holder of those, so the resolution refuses it and does not choose.

    Raises:
        UnresolvedValues: With one problem for each property that has no usable value.
    """
    values: dict[IRI, float] = {}
    origin: dict[IRI, IRI] = {}
    problems: list[str] = []
    for prop in TYPE_LEVEL:
        if own.get(prop):
            problems.append(
                f"{prop.fragment}: the Screw holds a value, but only its Screw type can"
            )
    for prop in (*TYPE_LEVEL, *PLACEMENT):
        if prop in PLACEMENT:
            if own.get(prop):
                holder, candidates = screw, own[prop]
            else:
                problems.append(
                    f"{prop.fragment}: the Screw holds no value, and only a Screw can"
                )
                continue
        elif screw_type is None:
            problems.append(f"{prop.fragment}: the Screw names no Screw type")
            continue
        elif inherited.get(prop):
            holder, candidates = screw_type, inherited[prop]
        else:
            problems.append(
                f"{prop.fragment}: its Screw type <{screw_type}> holds no value"
            )
            continue

        if len(candidates) > 1:
            problems.append(
                f"{prop.fragment}: <{holder}> holds {len(candidates)} values "
                f"{list(candidates)}, but the property is functional and allows one"
            )
        elif not _is_finite_number(candidates[0]):
            problems.append(
                f"{prop.fragment}: <{holder}> holds {candidates[0]!r}, not a finite number"
            )
        else:
            values[prop] = float(candidates[0])  # type: ignore[arg-type]
            origin[prop] = holder

    if problems:
        raise UnresolvedValues(screw, problems)
    return EffectiveValuesWithOrigin(
        screw=screw,
        values=EffectiveValues(
            **{name: values[prop] for name, prop in PROPERTY_OF_FIELD.items()}
        ),
        origin=origin,
        screw_type=screw_type,
        type_values={} if screw_type is None else usable_values(inherited),
    )


def usable_values(held: Mapping[IRI, Sequence[object]]) -> dict[IRI, float]:
    """Each type-level value of `held` that is held once and is a finite number, in the order of
    `TYPE_LEVEL`."""
    usable: dict[IRI, float] = {}
    for prop in TYPE_LEVEL:
        candidates = held.get(prop) or []
        if len(candidates) == 1 and _is_finite_number(candidates[0]):
            usable[prop] = float(candidates[0])  # type: ignore[arg-type]
    return usable


def type_value_problems(
    screw_type: IRI, held: Mapping[IRI, Sequence[object]]
) -> list[str]:
    """What keeps `held`, the values that `screw_type` holds, from giving each of the seven
    type-level values once as a finite number: one readable problem for each value that it holds
    zero times, more than once, or not as a finite number, in the order of `TYPE_LEVEL`. Empty
    when all seven are usable."""
    name = screw_type.fragment
    problems: list[str] = []
    for prop in TYPE_LEVEL:
        value = prop.fragment.removeprefix("has")
        candidates = held.get(prop) or []
        if not candidates:
            problems.append(f"the screw type {name} holds no value of {value}")
        elif len(candidates) > 1:
            problems.append(
                f"the screw type {name} holds {len(candidates)} values of {value}, "
                "but allows one"
            )
        elif not _is_finite_number(candidates[0]):
            problems.append(
                f"the screw type {name} holds {candidates[0]!r} as {value}, "
                "not a finite number"
            )
    return problems


def read_effective_values(ogm: OGM, screw: IRI) -> EffectiveValues:
    """Read the effective values of `screw` from the graph, as
    `read_effective_values_with_origin` does, without where each came from.

    Raises:
        UnresolvedValues: As `read_effective_values_with_origin` does.
    """
    return read_effective_values_with_origin(ogm, screw).values


def read_effective_values_with_origin(
    ogm: OGM, screw: IRI
) -> EffectiveValuesWithOrigin:
    """Read the effective values of `screw` from the graph, with where each came from, in one
    projection of the object-graph mapper (`EFFECTIVE_VALUES_SCOPE`).

    It reads the graph on each call and keeps nothing, so a value that the learner has changed
    counts from the next call. The mapper gives every value an individual holds, a second value of
    a functional property too, so `resolve` still finds and refuses it (measured on 2026-10-15).
    The read does not ask the Screw for a type-level value, so a Screw that holds one in the graph
    is not seen here: the refusal covers what `resolve` is given.

    Raises:
        UnresolvedValues: As `resolve` does, and when the Screw names more than one Screw type.
    """
    log_scope(
        logger,
        "Anomaly detector",
        "reads",
        screw,
        "EFFECTIVE_VALUES_SCOPE",
        EFFECTIVE_VALUES_SCOPE,
        "it wants the placement values the Screw holds, and through its Screw type (hasScrewType) "
        "the values of the type: the ten values that one decision uses",
    )
    data = ogm.fetch(instance_iri=screw, class_scope=EFFECTIVE_VALUES_SCOPE).data or {}
    types = data.get(JMS.hasScrewType) or []
    if len(types) > 1:
        named = sorted(IRI(_id(screw_type)) for screw_type in types)
        raise UnresolvedValues(
            screw, [f"it names {len(types)} Screw types {named}, not one"]
        )
    own = _held(data, PLACEMENT)
    if not types:
        return resolve_with_origin(screw, own, None, {})
    (screw_type,) = types
    inherited = _held(getattr(screw_type, "data", None) or {}, TYPE_LEVEL)
    return resolve_with_origin(screw, own, IRI(_id(screw_type)), inherited)


def _held(data: Mapping, properties: Sequence[IRI]) -> dict[IRI, list[object]]:
    """The values that a projection's `data` holds for each of `properties`, every value of each."""
    return {prop: list(data[prop]) for prop in properties if data.get(prop)}


def _id(value: object) -> str:
    """The IRI of a linked value, which the mapper gives as a node with an `id`."""
    return str(getattr(value, "id", value))


def _is_finite_number(value: object) -> bool:
    """Whether `value` is an int, a float or a Decimal, and finite. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    return math.isfinite(value)
