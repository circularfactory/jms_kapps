"""The effective values of a Screw: its own value, else its Screw type's, and all ten or none.

The resolution is tested as a pure function, and its reader against an in-memory rdflib graph
that answers the same SPARQL the triple store does. `test_uc1_ontology_live.py` reads the same
values from a live GraphDB.
"""

from __future__ import annotations

import math
from dataclasses import fields
from decimal import Decimal
from types import SimpleNamespace

import pytest
from _uc1_mock import (
    ARBITRARY_OVERRIDE,
    ARBITRARY_PLACEMENT_VALUES,
    LEGACY_MOCK_TYPE_VALUES,
    MOCK_M4_VALUES,
)
from kapps_triplestore_interface import IRI
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from jms_kapps.uc1.decision_tree import EffectiveValues
from jms_kapps.uc1.effective_values import (
    PROPERTY_OF_FIELD,
    UnresolvedValues,
    as_properties,
    read_effective_values,
    resolve,
)
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PLACEMENT, TYPE_LEVEL

SCREW = IRI("TestScrew", base=JMS_NS)


def held(values: dict[IRI, float]) -> dict[IRI, list[object]]:
    """Values as the graph holds them: a list for each property."""
    return {prop: [value] for prop, value in values.items()}


def test_the_fields_are_the_ten_properties() -> None:
    assert set(PROPERTY_OF_FIELD) == {field.name for field in fields(EffectiveValues)}
    assert set(PROPERTY_OF_FIELD.values()) == {*TYPE_LEVEL, *PLACEMENT}
    assert len(TYPE_LEVEL) == 7
    assert len(PLACEMENT) == 3


def test_the_resolution_is_what_the_decision_tree_takes() -> None:
    values = resolve(
        SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, held(MOCK_M4_VALUES)
    )

    assert type(values) is EffectiveValues


def test_a_screw_without_overrides_takes_its_type_values() -> None:
    values = resolve(
        SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, held(MOCK_M4_VALUES)
    )

    assert as_properties(values) == {**MOCK_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES}


def test_a_value_of_the_screw_overrides_the_value_of_its_type() -> None:
    own = held({**ARBITRARY_PLACEMENT_VALUES, **ARBITRARY_OVERRIDE})

    values = resolve(SCREW, own, JMS.M4, held(MOCK_M4_VALUES))

    assert values.max_dynamic_torque == 1.2
    assert values.min_counter_torque == LEGACY_MOCK_TYPE_VALUES[JMS.hasMinCounterTorque]


def test_a_screw_without_placement_values_is_refused() -> None:
    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, {}, JMS.M4, held(MOCK_M4_VALUES))

    assert refused.value.screw == SCREW
    assert len(refused.value.problems) == 3
    for prop in PLACEMENT:
        assert prop.fragment in str(refused.value)


def test_a_placement_value_of_the_type_is_not_used() -> None:
    type_values = held({**MOCK_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES})

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, {}, JMS.M4, type_values)

    assert len(refused.value.problems) == 3


def test_a_value_that_neither_holds_is_refused_and_named() -> None:
    type_values = held(MOCK_M4_VALUES)
    del type_values[JMS.hasMaxLooseTorque]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, type_values)

    assert len(refused.value.problems) == 1
    assert "hasMaxLooseTorque" in refused.value.problems[0]
    assert str(JMS.M4) in refused.value.problems[0]


def test_a_screw_without_a_type_needs_every_value_of_its_own() -> None:
    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, held(ARBITRARY_PLACEMENT_VALUES), None, {})

    assert len(refused.value.problems) == 7
    assert "no Screw type" in refused.value.problems[0]

    own = held({**MOCK_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES})
    assert as_properties(resolve(SCREW, own, None, {})) == {
        **MOCK_M4_VALUES,
        **ARBITRARY_PLACEMENT_VALUES,
    }


def test_two_values_on_the_screw_are_refused_rather_than_inherited() -> None:
    own = held(ARBITRARY_PLACEMENT_VALUES)
    own[JMS.hasMaxDynamicTorque] = [1.2, 1.3]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, own, JMS.M4, held(MOCK_M4_VALUES))

    assert len(refused.value.problems) == 1
    assert "2 values" in refused.value.problems[0]
    assert "functional" in refused.value.problems[0]


def test_two_values_on_the_type_are_refused() -> None:
    type_values = held(MOCK_M4_VALUES)
    type_values[JMS.hasMinCounterTorque] = [0.5, 0.6]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, type_values)

    assert "hasMinCounterTorque" in refused.value.problems[0]
    assert str(JMS.M4) in refused.value.problems[0]


@pytest.mark.parametrize("bad", ["0.5", True, math.nan, math.inf, None])
def test_a_value_that_is_not_a_finite_number_is_refused(bad: object) -> None:
    own = held(ARBITRARY_PLACEMENT_VALUES)
    own[JMS.hasMinApproachPosition] = [bad]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, own, JMS.M4, held(MOCK_M4_VALUES))

    assert "hasMinApproachPosition" in refused.value.problems[0]


def test_a_decimal_or_an_integer_counts_as_a_number() -> None:
    own = held(ARBITRARY_PLACEMENT_VALUES)
    own[JMS.hasMinApproachPosition] = [Decimal("-10.5")]
    own[JMS.hasMinScrewHeadPosition] = [-2]

    values = resolve(SCREW, own, JMS.M4, held(MOCK_M4_VALUES))

    assert values.min_approach_position == -10.5
    assert values.min_screw_head_position == -2.0
    assert isinstance(values.min_screw_head_position, float)


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, {}, None, {})

    assert len(refused.value.problems) == 10


class RdflibMapper:
    """An object-graph mapper over an in-memory graph: its `fetch` answers a class scope as the
    mapper does, every value of each property in a list, and a link as a node with an `id` and
    the data its nested chains ask for."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    def fetch(self, *, instance_iri, class_scope=None, **rest):  # type: ignore[no-untyped-def]
        return self._node(URIRef(str(instance_iri)), class_scope.to_property_chains())

    def _node(self, subject: URIRef, chains: list) -> SimpleNamespace:
        data: dict[IRI, list] = {}
        for prop in {chain[0] for chain in chains}:
            rest = [
                chain[1:] for chain in chains if chain[0] == prop and len(chain) > 1
            ]
            for value in self.graph.objects(subject, URIRef(str(prop))):
                data.setdefault(IRI(str(prop)), []).append(
                    self._node(value, rest) if rest else value.toPython()
                )
        return SimpleNamespace(id=str(subject), data=data)


def store_with(**holders: dict[IRI, float]) -> RdflibMapper:
    """A store in which each named test individual holds the given values."""
    graph = Graph()
    for local_name, values in holders.items():
        subject = URIRef(JMS_NS + local_name)
        for prop, value in values.items():
            graph.add((subject, URIRef(prop), Literal(value, datatype=XSD.double)))
    return RdflibMapper(graph)


def test_the_reader_resolves_from_the_graph() -> None:
    store = store_with(
        M4=MOCK_M4_VALUES,
        TestScrew={**ARBITRARY_PLACEMENT_VALUES, **ARBITRARY_OVERRIDE},
    )
    store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(JMS.M4)))

    values = read_effective_values(store, SCREW)

    assert as_properties(values) == {
        **MOCK_M4_VALUES,
        **ARBITRARY_PLACEMENT_VALUES,
        **ARBITRARY_OVERRIDE,
    }


def test_the_reader_refuses_a_screw_with_two_types() -> None:
    store = store_with(TestScrew=ARBITRARY_PLACEMENT_VALUES)
    for screw_type in (JMS.M4, IRI("M5", base=JMS_NS)):
        store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(screw_type)))

    with pytest.raises(UnresolvedValues) as refused:
        read_effective_values(store, SCREW)

    assert "2 Screw types" in str(refused.value)


def test_the_reader_does_not_read_placement_from_the_type() -> None:
    store = store_with(
        M4={**MOCK_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES}, TestScrew={}
    )
    store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(JMS.M4)))

    with pytest.raises(UnresolvedValues) as refused:
        read_effective_values(store, SCREW)

    assert len(refused.value.problems) == 3
