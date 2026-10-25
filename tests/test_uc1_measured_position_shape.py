"""The admission of a measured position as an uncertainty quantification: the shapes in use case 1's
shapes, read without a store.

A random variable has exactly one distribution, a `uq:gmmLiteral` whose lexical form names the six
fields in their order, and exactly one value domain. A Screw has at most one best estimate of its
head position and at most one variable of it; an unscrewing operation has at most one variable of
its approach end position. Each property shape has an IRI, a path of one property and a message.

`test_uc1_uncertainty_live.py` makes the refused writes on a live store.
"""

from __future__ import annotations

import json
import re

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, SH

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.uncertainty import FIELDS, measurement_literal
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, UQ


def shape(name: str) -> URIRef:
    return URIRef(JMS_NS + name)


def iri(term: object) -> URIRef:
    return URIRef(str(term))


# Each node shape: its target, and its property shapes with their path, minimum and maximum.
NODE_SHAPES = {
    "RandomVariableShape": (
        UQ.RandomVariable,
        {
            "RandomVariableDistributionShape": (UQ.hasDistribution, 1, 1),
            "RandomVariableDomainShape": (UQ.hasDomain, 1, 1),
        },
    ),
    "ScrewMeasuredPositionShape": (
        JMS.Screw,
        {
            "ScrewMeasuredHeadPositionShape": (
                JMS.hasMeasuredScrewHeadPosition,
                None,
                1,
            ),
            "ScrewHeadPositionVariableShape": (
                JMS.hasScrewHeadPositionVariable,
                None,
                1,
            ),
        },
    ),
    "UnscrewingOperationVariableShape": (
        JMS.UnscrewingOperation,
        {
            "ApproachEndPositionVariableShape": (
                JMS.hasApproachEndPositionVariable,
                None,
                1,
            )
        },
    ),
}

DISTRIBUTION = shape("RandomVariableDistributionShape")

# The pattern the distribution shape holds, as the store reads it.
PATTERN = (
    r'^\s*\{\s*"n_components"\s*:[\s\S]*,\s*"dimensions"\s*:[\s\S]*,'
    r'\s*"covariance_type"\s*:[\s\S]*,\s*"weights"\s*:[\s\S]*,\s*"means"\s*:[\s\S]*,'
    r'\s*"covariances"\s*:[\s\S]*\}\s*$'
)


@pytest.fixture(scope="module")
def shapes() -> Graph:
    return Graph().parse(
        data=read_asset("jms_kapps.uc1", "shapes", "integration_usecase_shapes.ttl"),
        format="turtle",
    )


@pytest.mark.parametrize("name", NODE_SHAPES)
def test_each_node_shape_targets_its_class_with_its_property_shapes(
    shapes: Graph, name: str
) -> None:
    target, properties = NODE_SHAPES[name]

    assert (shape(name), RDF.type, SH.NodeShape) in shapes
    assert list(shapes.objects(shape(name), SH.targetClass)) == [iri(target)]
    assert set(shapes.objects(shape(name), SH.property)) == {
        shape(p) for p in properties
    }


@pytest.mark.parametrize(
    "name, path, min_count, max_count",
    [
        (p, *spec)
        for _, properties in NODE_SHAPES.values()
        for p, spec in properties.items()
    ],
)
def test_each_property_shape_has_one_property_its_counts_and_a_message(
    shapes: Graph, name: str, path: object, min_count: int | None, max_count: int
) -> None:
    node = shape(name)

    assert (node, RDF.type, SH.PropertyShape) in shapes
    assert shapes.value(node, SH.path) == iri(path)
    assert shapes.value(node, SH.minCount) == (
        None if min_count is None else Literal(min_count)
    )
    assert shapes.value(node, SH.maxCount) == Literal(max_count)
    assert shapes.value(node, SH["or"]) is None
    message = shapes.value(node, SH.message)
    assert message is not None and str(path).rsplit("#", 1)[1] in str(message)


def test_the_distribution_is_a_gmm_literal_whose_fields_come_in_their_order(
    shapes: Graph,
) -> None:
    assert shapes.value(DISTRIBUTION, SH.datatype) == iri(UQ.gmmLiteral)
    assert str(shapes.value(DISTRIBUTION, SH.pattern)) == PATTERN
    assert shapes.value(DISTRIBUTION, SH.flags) is None


def test_the_pattern_admits_the_written_literal_of_a_measurement() -> None:
    """`re.search`, because `sh:pattern` matches anywhere in the value, as XPath's `matches`."""
    assert re.search(PATTERN, str(measurement_literal(0.25, 0.1)))
    assert re.search(PATTERN, json.dumps(dict.fromkeys(FIELDS, 1), indent=2))


@pytest.mark.parametrize(
    "fields",
    [
        FIELDS[1:],
        (FIELDS[1], FIELDS[0], *FIELDS[2:]),
        FIELDS[:-1],
    ],
    ids=["no n_components", "a wrong order", "no covariances"],
)
def test_the_pattern_refuses_a_missing_field_or_a_wrong_order(
    fields: tuple[str, ...],
) -> None:
    assert not re.search(PATTERN, json.dumps(dict.fromkeys(fields, 1)))


def test_the_pattern_refuses_a_value_that_only_holds_the_object() -> None:
    lexical = json.dumps(dict.fromkeys(FIELDS, 1))

    assert not re.search(PATTERN, f"[{lexical}]")
