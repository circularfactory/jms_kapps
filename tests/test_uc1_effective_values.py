"""The effective values of a Screw: the type values of its Screw type, its placement values, all or none.

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
from _uc1_example import (
    ARBITRARY_PLACEMENT_VALUES,
    EXAMPLE_M4_VALUES,
    STRAY_TYPE_VALUE,
)
from kapps_triplestore_interface import IRI
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from jms_kapps.uc1.decision_tree import EffectiveValues
from jms_kapps.uc1.effective_values import (
    EFFECTIVE_VALUES_SCOPE,
    PROPERTY_OF_FIELD,
    UnresolvedValues,
    as_properties,
    read_effective_values,
    read_effective_values_with_origin,
    resolve,
    resolve_with_origin,
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
        SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, held(EXAMPLE_M4_VALUES)
    )

    assert type(values) is EffectiveValues


def test_a_screw_takes_its_type_values_and_its_placement_values() -> None:
    values = resolve(
        SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, held(EXAMPLE_M4_VALUES)
    )

    assert as_properties(values) == {**EXAMPLE_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES}


def test_a_screw_that_holds_a_type_value_is_refused_not_preferred() -> None:
    own = held({**ARBITRARY_PLACEMENT_VALUES, **STRAY_TYPE_VALUE})

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, own, JMS.M4, held(EXAMPLE_M4_VALUES))

    assert refused.value.screw == SCREW
    assert len(refused.value.problems) == 1
    assert "hasMaxDynamicTorque" in refused.value.problems[0]
    assert "only its Screw type can" in refused.value.problems[0]


def test_a_screw_that_holds_a_type_value_is_refused_also_without_a_type_value() -> None:
    own = held({**ARBITRARY_PLACEMENT_VALUES, **STRAY_TYPE_VALUE})
    type_values = held(EXAMPLE_M4_VALUES)
    del type_values[JMS.hasMaxDynamicTorque]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, own, JMS.M4, type_values)

    assert len(refused.value.problems) == 2


def test_a_screw_without_placement_values_is_refused() -> None:
    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, {}, JMS.M4, held(EXAMPLE_M4_VALUES))

    assert refused.value.screw == SCREW
    assert len(refused.value.problems) == 3
    for prop in PLACEMENT:
        assert prop.fragment in str(refused.value)


def test_a_placement_value_of_the_type_is_not_used() -> None:
    type_values = held({**EXAMPLE_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES})

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, {}, JMS.M4, type_values)

    assert len(refused.value.problems) == 3


def test_a_value_that_neither_holds_is_refused_and_named() -> None:
    type_values = held(EXAMPLE_M4_VALUES)
    del type_values[JMS.hasMaxLooseTorque]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, type_values)

    assert len(refused.value.problems) == 1
    assert "hasMaxLooseTorque" in refused.value.problems[0]
    assert str(JMS.M4) in refused.value.problems[0]


def test_a_screw_without_a_type_is_refused_for_every_type_value() -> None:
    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, held(ARBITRARY_PLACEMENT_VALUES), None, {})

    assert len(refused.value.problems) == 7
    assert "names no Screw type" in refused.value.problems[0]


def test_type_values_on_a_screw_without_a_type_are_not_used() -> None:
    own = held({**EXAMPLE_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES})

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, own, None, {})

    assert len(refused.value.problems) == 14


def test_two_values_on_the_screw_are_refused_rather_than_inherited() -> None:
    own = held(ARBITRARY_PLACEMENT_VALUES)
    own[JMS.hasMinApproachPosition] = [-10.0, -9.0]

    with pytest.raises(UnresolvedValues) as refused:
        resolve(SCREW, own, JMS.M4, held(EXAMPLE_M4_VALUES))

    assert len(refused.value.problems) == 1
    assert "2 values" in refused.value.problems[0]
    assert "functional" in refused.value.problems[0]


def test_two_values_on_the_type_are_refused() -> None:
    type_values = held(EXAMPLE_M4_VALUES)
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
        resolve(SCREW, own, JMS.M4, held(EXAMPLE_M4_VALUES))

    assert "hasMinApproachPosition" in refused.value.problems[0]


def test_a_decimal_or_an_integer_counts_as_a_number() -> None:
    own = held(ARBITRARY_PLACEMENT_VALUES)
    own[JMS.hasMinApproachPosition] = [Decimal("-10.5")]
    own[JMS.hasMinScrewHeadPosition] = [-2]

    values = resolve(SCREW, own, JMS.M4, held(EXAMPLE_M4_VALUES))

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
    store = store_with(M4=EXAMPLE_M4_VALUES, TestScrew=ARBITRARY_PLACEMENT_VALUES)
    store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(JMS.M4)))

    values = read_effective_values(store, SCREW)

    assert as_properties(values) == {**EXAMPLE_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES}


def test_the_reader_does_not_ask_a_screw_for_a_type_value() -> None:
    """The ontology's domain of a type value names no Screw, so the mapper would refuse the scope
    that asked for it. A stray value in the graph is therefore not seen, and not used: the refusal
    of `resolve` covers what the reader is given, which is none."""
    store = store_with(
        M4=EXAMPLE_M4_VALUES,
        TestScrew={**ARBITRARY_PLACEMENT_VALUES, **STRAY_TYPE_VALUE},
    )
    store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(JMS.M4)))

    chains = {tuple(chain) for chain in EFFECTIVE_VALUES_SCOPE.to_property_chains()}
    assert not chains & {(prop,) for prop in TYPE_LEVEL}
    assert as_properties(read_effective_values(store, SCREW)) == {
        **EXAMPLE_M4_VALUES,
        **ARBITRARY_PLACEMENT_VALUES,
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
        M4={**EXAMPLE_M4_VALUES, **ARBITRARY_PLACEMENT_VALUES}, TestScrew={}
    )
    store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(JMS.M4)))

    with pytest.raises(UnresolvedValues) as refused:
        read_effective_values(store, SCREW)

    assert len(refused.value.problems) == 3


# Where each value came from, for the detector's dashboard: the Screw or its Screw type. And the
# values the Screw type holds, so that a value the learner wrote to the type shows.


def test_each_value_names_the_individual_that_held_it() -> None:
    own = held(ARBITRARY_PLACEMENT_VALUES)

    with_origin = resolve_with_origin(SCREW, own, JMS.M4, held(EXAMPLE_M4_VALUES))

    assert with_origin.values == resolve(SCREW, own, JMS.M4, held(EXAMPLE_M4_VALUES))
    assert with_origin.screw == SCREW
    assert with_origin.screw_type == JMS.M4
    assert set(with_origin.origin) == {*TYPE_LEVEL, *PLACEMENT}
    for prop in PLACEMENT:
        assert with_origin.origin[prop] == SCREW
    for prop in TYPE_LEVEL:
        assert with_origin.origin[prop] == JMS.M4


def test_the_type_values_are_what_the_screw_type_holds() -> None:
    with_origin = resolve_with_origin(
        SCREW, held(ARBITRARY_PLACEMENT_VALUES), JMS.M4, held(EXAMPLE_M4_VALUES)
    )

    assert with_origin.type_values == EXAMPLE_M4_VALUES


def test_a_screw_without_a_type_is_refused_also_with_resolve_with_origin() -> None:
    with pytest.raises(UnresolvedValues):
        resolve_with_origin(SCREW, held(ARBITRARY_PLACEMENT_VALUES), None, {})


def test_the_reader_gives_the_origin_and_the_type_values_from_the_graph() -> None:
    store = store_with(M4=EXAMPLE_M4_VALUES, TestScrew=ARBITRARY_PLACEMENT_VALUES)
    store.graph.add((URIRef(SCREW), URIRef(JMS.hasScrewType), URIRef(JMS.M4)))

    with_origin = read_effective_values_with_origin(store, SCREW)

    assert with_origin.values == read_effective_values(store, SCREW)
    assert with_origin.screw_type == JMS.M4
    assert with_origin.origin[JMS.hasMinApproachPosition] == SCREW
    assert with_origin.origin[JMS.hasMinCounterTorque] == JMS.M4
    assert with_origin.type_values == EXAMPLE_M4_VALUES
