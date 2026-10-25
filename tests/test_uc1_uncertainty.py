"""The Gaussian-mixture literal of one measured position: written, read back, and checked.

Offline, without a store. The lexical form is ProbSPARQL's: a JSON object with exactly the six
fields `n_components`, `dimensions`, `covariance_type`, `weights`, `means` and `covariances`, in
this order, whose arrays have the shapes that `n_components` and `dimensions` give.
"""

from __future__ import annotations

import json

import pytest
from rdflib import Literal

from jms_kapps.uc1.uncertainty import (
    FIELDS,
    GMM_LITERAL,
    MalformedMixture,
    check_well_formed,
    measurement_literal,
    read_measurement,
)

# A well-formed mixture of two components in one dimension.
TWO_COMPONENTS = {
    "n_components": 2,
    "dimensions": 1,
    "covariance_type": "full",
    "weights": [0.5, 0.5],
    "means": [[0.0], [1.0]],
    "covariances": [[[0.01]], [[0.04]]],
}


def lexical(fields: dict) -> str:
    return json.dumps(fields)


def test_the_fields_are_probsparqls_six_in_their_order() -> None:
    assert FIELDS == (
        "n_components",
        "dimensions",
        "covariance_type",
        "weights",
        "means",
        "covariances",
    )


def test_a_measurement_is_one_component_in_one_dimension() -> None:
    literal = measurement_literal(0.25, 0.1)

    assert isinstance(literal, Literal)
    assert literal.datatype == GMM_LITERAL
    fields = json.loads(str(literal))
    assert list(fields) == list(FIELDS)
    assert fields["n_components"] == 1
    assert fields["dimensions"] == 1
    assert fields["covariance_type"] == "full"
    assert fields["weights"] == [1.0]
    assert fields["means"] == [[0.25]]
    assert fields["covariances"] == [[[pytest.approx(0.01)]]]


def test_the_written_form_reads_back_as_mean_and_variance() -> None:
    mean, variance = read_measurement(measurement_literal(-0.3, 0.1))

    assert mean == -0.3
    assert variance == pytest.approx(0.01)


def test_the_reader_takes_a_lexical_form_too() -> None:
    assert read_measurement(str(measurement_literal(1.5, 0.2))) == (
        1.5,
        pytest.approx(0.04),
    )


def test_the_check_accepts_a_well_formed_two_component_mixture() -> None:
    check_well_formed(lexical(TWO_COMPONENTS))


@pytest.mark.parametrize(
    "covariance_type, covariances",
    [
        ("full", [[[0.01]], [[0.04]]]),
        ("diag", [[0.01], [0.04]]),
        ("spherical", [0.01, 0.04]),
    ],
)
def test_the_check_accepts_each_covariance_type(covariance_type, covariances) -> None:
    check_well_formed(
        lexical(
            {
                **TWO_COMPONENTS,
                "covariance_type": covariance_type,
                "covariances": covariances,
            }
        )
    )


def test_the_reader_of_one_measurement_refuses_two_components() -> None:
    with pytest.raises(MalformedMixture, match="one component in one dimension"):
        read_measurement(lexical(TWO_COMPONENTS))


def test_the_check_refuses_a_missing_field() -> None:
    fields = {k: v for k, v in TWO_COMPONENTS.items() if k != "weights"}

    with pytest.raises(MalformedMixture, match="six fields"):
        check_well_formed(lexical(fields))


def test_the_check_refuses_a_wrong_order() -> None:
    fields = {
        "dimensions": 1,
        **{k: v for k, v in TWO_COMPONENTS.items() if k != "dimensions"},
    }

    with pytest.raises(MalformedMixture, match="six fields"):
        check_well_formed(lexical(fields))


def test_the_check_refuses_an_extra_field() -> None:
    with pytest.raises(MalformedMixture, match="six fields"):
        check_well_formed(lexical({**TWO_COMPONENTS, "label": "x"}))


def test_the_check_refuses_a_repeated_field() -> None:
    text = lexical(TWO_COMPONENTS)[:-1] + ', "covariances": [[[0.01]], [[0.04]]]}'

    with pytest.raises(MalformedMixture, match="six fields"):
        check_well_formed(text)


@pytest.mark.parametrize(
    "field, value",
    [
        ("weights", [1.0]),
        ("means", [[0.0]]),
        ("means", [[0.0, 1.0], [1.0, 2.0]]),
        ("covariances", [[[0.01]]]),
        ("covariances", [[[0.01, 0.0]], [[0.04, 0.0]]]),
    ],
)
def test_the_check_refuses_an_array_whose_shape_does_not_match(field, value) -> None:
    with pytest.raises(MalformedMixture, match=field):
        check_well_formed(lexical({**TWO_COMPONENTS, field: value}))


@pytest.mark.parametrize(
    "fields",
    [
        {**TWO_COMPONENTS, "n_components": 0},
        {**TWO_COMPONENTS, "dimensions": 0},
        {**TWO_COMPONENTS, "n_components": True},
        {**TWO_COMPONENTS, "n_components": 2.0},
        {**TWO_COMPONENTS, "covariance_type": "tied"},
        {**TWO_COMPONENTS, "weights": [0.5, "0.5"]},
        {**TWO_COMPONENTS, "weights": [0.5, True]},
    ],
)
def test_the_check_refuses_a_wrong_value(fields) -> None:
    with pytest.raises(MalformedMixture):
        check_well_formed(lexical(fields))


@pytest.mark.parametrize("text", ["", "[]", "not json", '{"n_components": NaN}'])
def test_the_check_refuses_what_is_not_a_json_object(text) -> None:
    with pytest.raises(MalformedMixture):
        check_well_formed(text)


def test_the_check_does_not_ask_the_weights_to_sum_to_one() -> None:
    """As ProbSPARQL's datatype, the check is of the form only."""
    check_well_formed(lexical({**TWO_COMPONENTS, "weights": [0.2, 0.2]}))


@pytest.mark.parametrize("standard_deviation", [-0.1, float("nan"), float("inf")])
def test_a_measurement_needs_a_finite_non_negative_standard_deviation(
    standard_deviation,
) -> None:
    with pytest.raises(ValueError):
        measurement_literal(0.0, standard_deviation)


def test_a_malformed_mixture_is_a_value_error() -> None:
    assert issubclass(MalformedMixture, ValueError)


def test_the_check_refuses_an_array_of_name_value_pairs() -> None:
    with pytest.raises(MalformedMixture, match="six fields"):
        check_well_formed(json.dumps([[k, v] for k, v in TWO_COMPONENTS.items()]))
