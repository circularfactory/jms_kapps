"""An unscrewing operation concerns one Screw whose Screw type holds the seven values an attempt
needs: the shape in use case 1's shapes, read without a store.

The planner's commit of an unscrewing operation is the write the store checks. The operation names
exactly one Screw (`:concernsScrew`), that Screw has exactly one Screw type (`:hasScrewType`), and
the type holds each of the seven values of `TYPE_LEVEL` once, as an `xsd:double`. The store refuses
a commit that breaks it, and its report names the one shape with an IRI,
`:UnscrewingOperationScrewShape`, with its message. The rule is SHACL Core in nested single steps:
each property shape's path is one property, and none uses `sh:or`.

`test_uc1_infeasible_operation_live.py` makes the refused commit on a live store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kapps_semantic_middleware.validation_report import (
    readable_results,
    validation_results,
)
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, SH, XSD

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, TYPE_LEVEL

OPERATION_SHAPE = URIRef(f"{JMS_NS}UnscrewingOperationShape")
SCREW_SHAPE = URIRef(f"{JMS_NS}UnscrewingOperationScrewShape")
SCREW_OF_MEASURED_TYPE = URIRef(f"{JMS_NS}ScrewOfMeasuredTypeShape")
MEASURED_SCREW_TYPE = URIRef(f"{JMS_NS}MeasuredScrewTypeShape")

# The store's report of the refused commit, as `jms-uc1 infeasible-operation --record` writes it.
RECORD = Path(__file__).parent / "reports" / "infeasible-operation.nq"


@pytest.fixture(scope="module")
def shapes() -> Graph:
    return Graph().parse(
        data=read_asset("jms_kapps.uc1", "shapes", "integration_usecase_shapes.ttl"),
        format="turtle",
    )


def one_property(shapes: Graph, node: URIRef) -> BNode | URIRef:
    (prop,) = shapes.objects(node, SH.property)
    return prop


def test_the_operation_shape_targets_the_unscrewing_operation(shapes: Graph) -> None:
    assert (OPERATION_SHAPE, RDF.type, SH.NodeShape) in shapes
    assert list(shapes.objects(OPERATION_SHAPE, SH.targetClass)) == [
        URIRef(str(JMS.UnscrewingOperation))
    ]
    assert one_property(shapes, OPERATION_SHAPE) == SCREW_SHAPE


def test_the_operation_concerns_exactly_one_screw_of_a_measured_type(
    shapes: Graph,
) -> None:
    assert (SCREW_SHAPE, RDF.type, SH.PropertyShape) in shapes
    assert shapes.value(SCREW_SHAPE, SH.path) == URIRef(str(JMS.concernsScrew))
    assert shapes.value(SCREW_SHAPE, SH.minCount) == Literal(1)
    assert shapes.value(SCREW_SHAPE, SH.maxCount) == Literal(1)
    assert shapes.value(SCREW_SHAPE, SH.node) == SCREW_OF_MEASURED_TYPE
    message = shapes.value(SCREW_SHAPE, SH.message)
    assert message is not None and "seven values" in str(message)


def test_the_screw_has_exactly_one_screw_type_that_is_measured(shapes: Graph) -> None:
    prop = one_property(shapes, SCREW_OF_MEASURED_TYPE)
    assert shapes.value(prop, SH.path) == URIRef(str(JMS.hasScrewType))
    assert shapes.value(prop, SH.minCount) == Literal(1)
    assert shapes.value(prop, SH.maxCount) == Literal(1)
    assert shapes.value(prop, SH.node) == MEASURED_SCREW_TYPE


def test_the_screw_type_holds_each_of_the_seven_values_once_as_a_double(
    shapes: Graph,
) -> None:
    props = list(shapes.objects(MEASURED_SCREW_TYPE, SH.property))
    paths = {shapes.value(prop, SH.path) for prop in props}
    assert paths == {URIRef(str(value)) for value in TYPE_LEVEL}
    assert len(props) == len(TYPE_LEVEL)
    for prop in props:
        assert shapes.value(prop, SH.minCount) == Literal(1)
        assert shapes.value(prop, SH.maxCount) == Literal(1)
        assert shapes.value(prop, SH.datatype) == XSD.double


def test_every_path_of_the_rule_is_one_property_and_none_is_a_choice(
    shapes: Graph,
) -> None:
    props = [
        SCREW_SHAPE,
        one_property(shapes, SCREW_OF_MEASURED_TYPE),
        *shapes.objects(MEASURED_SCREW_TYPE, SH.property),
    ]
    for prop in props:
        assert isinstance(shapes.value(prop, SH.path), URIRef)
    for node in (OPERATION_SHAPE, SCREW_OF_MEASURED_TYPE, MEASURED_SCREW_TYPE, *props):
        assert shapes.value(node, SH["or"]) is None


def test_the_recorded_report_names_the_shape_with_its_message(shapes: Graph) -> None:
    """The store's report of the refused commit, as recorded: one result, by
    `:UnscrewingOperationScrewShape`, at the operation, whose value is the Screw of the type that
    holds no values, with the message the shapes file gives."""
    report = RECORD.read_text(encoding="utf-8")
    (result,) = validation_results(report)
    assert result.shape == str(SCREW_SHAPE)
    assert result.path == f"<{JMS.concernsScrew}>"
    (readable,) = readable_results(report) or []
    assert readable.value is not None and readable.value.full == f"{JMS_NS}Screw3"
    assert readable.message is not None
    assert readable.message.short == str(shapes.value(SCREW_SHAPE, SH.message))
