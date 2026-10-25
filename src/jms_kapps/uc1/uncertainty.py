"""The Gaussian-mixture literal of a measured position: write one measurement, read it back, and
check that a lexical form is well formed.

The literal is a `uq:gmmLiteral` of the uncertainty module (`ontology/uncertainty.ttl`), whose
lexical form is ProbSPARQL's: a JSON object with exactly the six fields of `FIELDS`, in this order.
`n_components` (K) and `dimensions` (D) are positive integers; `weights` holds K numbers; `means`
holds K rows of D numbers; and `covariances` holds, by `covariance_type`, K matrices of D by D
numbers (`full`), K rows of D numbers (`diag`) or K numbers (`spherical`). The check is of the form
only, as ProbSPARQL's datatype is: it does not ask the weights to sum to 1, or a covariance to be
positive.

The literal is written as an `rdflib.Literal` with the datatype `uq:gmmLiteral`; a plain string
would be stored as an `xsd:string`. Pure: no I/O.
"""

from __future__ import annotations

import json
import math
from typing import Any

from rdflib import Literal, URIRef

from jms_kapps.uc1.vocabulary import UQ

FIELDS: tuple[str, ...] = (
    "n_components",
    "dimensions",
    "covariance_type",
    "weights",
    "means",
    "covariances",
)

GMM_LITERAL = URIRef(str(UQ.gmmLiteral))


class MalformedMixture(ValueError):
    """A lexical form that is not a well-formed Gaussian mixture, or not the one measurement a
    reader asked for."""


def measurement_literal(value: float, standard_deviation: float) -> Literal:
    """The literal of one measurement: one component in one dimension, `covariance_type` `full`,
    the measured `value` as the mean and the squared `standard_deviation` as the variance.

    Raises:
        ValueError: If `value` is not finite, or `standard_deviation` is negative or not finite.
    """
    if not math.isfinite(value):
        raise ValueError(f"a measured value must be finite, not {value}")
    if not (math.isfinite(standard_deviation) and standard_deviation >= 0):
        raise ValueError(
            f"a standard deviation must be finite and not negative, not {standard_deviation}"
        )
    fields = {
        "n_components": 1,
        "dimensions": 1,
        "covariance_type": "full",
        "weights": [1.0],
        "means": [[float(value)]],
        "covariances": [[[float(standard_deviation) ** 2]]],
    }
    return Literal(json.dumps(fields, separators=(",", ":")), datatype=GMM_LITERAL)


def read_measurement(literal: Literal | str) -> tuple[float, float]:
    """The mean and the variance of the one measurement that `literal` holds.

    Raises:
        MalformedMixture: If the lexical form is not well formed (`check_well_formed`), or holds
            more than one component or more than one dimension.
    """
    fields = check_well_formed(str(literal))
    if fields["n_components"] != 1 or fields["dimensions"] != 1:
        raise MalformedMixture(
            "a measurement is one component in one dimension; this mixture has "
            f"{fields['n_components']} components in {fields['dimensions']} dimensions"
        )
    covariance = fields["covariances"][0]
    while isinstance(covariance, list):
        covariance = covariance[0]
    return float(fields["means"][0][0]), float(covariance)


def check_well_formed(lexical: str) -> dict[str, Any]:
    """The fields of `lexical`, a well-formed Gaussian mixture; see the module for the rules.

    Raises:
        MalformedMixture: If `lexical` is not a JSON object with exactly the six fields of
            `FIELDS` in their order, or a value or an array's shape breaks a rule.
    """
    try:
        pairs = json.loads(
            lexical, object_pairs_hook=_ObjectPairs, parse_constant=_refuse_constant
        )
    except (ValueError, TypeError) as error:
        raise MalformedMixture(f"not a JSON object: {error}") from None
    if (
        not isinstance(pairs, _ObjectPairs)
        or tuple(name for name, _ in pairs) != FIELDS
    ):
        names = (
            [name for name, _ in pairs]
            if isinstance(pairs, _ObjectPairs)
            else type(pairs).__name__
        )
        raise MalformedMixture(
            f"a Gaussian mixture has exactly the six fields {', '.join(FIELDS)}, in this "
            f"order; this one has {names}"
        )
    fields = dict(pairs)
    components = _positive_integer(fields, "n_components")
    dimensions = _positive_integer(fields, "dimensions")
    _numbers(fields["weights"], (components,), "weights")
    _numbers(fields["means"], (components, dimensions), "means")
    shape_of = {
        "full": (components, dimensions, dimensions),
        "diag": (components, dimensions),
        "spherical": (components,),
    }
    covariance_type = fields["covariance_type"]
    if covariance_type not in shape_of:
        raise MalformedMixture(
            f"covariance_type is one of {', '.join(shape_of)}, not {covariance_type!r}"
        )
    _numbers(fields["covariances"], shape_of[covariance_type], "covariances")
    return fields


class _ObjectPairs(list):
    """The fields of one JSON object, in their order and with any repeated name kept."""


def _refuse_constant(name: str) -> None:
    raise ValueError(f"{name} is not a number of JSON")


def _positive_integer(fields: dict[str, Any], name: str) -> int:
    value = fields[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MalformedMixture(f"{name} is a positive integer, not {value!r}")
    return value


def _numbers(value: object, shape: tuple[int, ...], name: str) -> None:
    """Raise unless `value` is nested lists of numbers of exactly `shape`."""
    if not shape:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MalformedMixture(f"{name} holds numbers, not {value!r}")
        return
    if not isinstance(value, list) or len(value) != shape[0]:
        raise MalformedMixture(
            f"{name} has the shape {' x '.join(map(str, shape))}; it does not match "
            "n_components and dimensions"
        )
    for item in value:
        _numbers(item, shape[1:], name)
