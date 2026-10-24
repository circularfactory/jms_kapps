"""The robot of use case 1 in the graph: the ontology's terms for it and the nodes the seed writes.

The screwing resource has one robot, whose controller speaks only MQTT. The middleware binds a
parameter of a resource over MQTT when its property is a subproperty of
`inf:isInterfaceAccessibleMQTTParameter` with an `inf:hasValue` restriction as its range, and when
its node names a broker and a topic. These tests parse `uc1/ontology/integration_usecase.ttl` and
build the seed's nodes offline; `test_uc1_seed_live.py` writes them to a store.
"""

from __future__ import annotations

import pytest
from kapps_semantic_middleware.vocabulary import CFC, INF
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, XSD

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc1 import run_core, screwing_resource
from jms_kapps.uc1.seed import (
    MQTT_BROKER_IP,
    MQTT_BROKER_PORT,
    ROBOT,
    SCREWING_RESOURCE,
    mqtt_set_topic,
    mqtt_topic,
    robot_parameter_node,
)
from jms_kapps.uc1.vocabulary import (
    JMS,
    JMS_NS,
    READINGS,
    READWRITE,
    ROBOT_PARAMETERS,
    SETPOINT_OF,
    SETPOINTS,
    TYPE_LEVEL,
)

MQTT_PARAMETER = URIRef(str(INF.isInterfaceAccessibleMQTTParameter))

# The datatype of each parameter's value.
DATATYPE = {
    **{prop: XSD.double for prop in READINGS},
    JMS.hasAttemptState: XSD.string,
    JMS.hasAttemptCommand: XSD.string,
    **{prop: XSD.double for prop in SETPOINTS},
}

# The MQTT connection metadata that the range of `inf:isInterfaceAccessibleMQTTParameter` declares.
MQTT_METADATA = {
    INF.hasMQTTTopic: XSD.string,
    INF.hasMQTTBrokerIP: XSD.string,
    INF.hasMQTTBrokerPort: XSD.integer,
    INF.hasMQTTSetTopic: XSD.string,
    INF.hasMQTTValuePath: XSD.string,
}


def uri(iri: object) -> URIRef:
    return URIRef(str(iri))


@pytest.fixture(scope="module")
def graph() -> Graph:
    return Graph().parse(
        data=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        format="turtle",
    )


def restrictions(graph: Graph, cls: object) -> dict[URIRef, URIRef]:
    """The `owl:allValuesFrom` of each restriction in the intersection that `cls` is."""
    members = graph.value(cls, OWL.intersectionOf)
    assert members is not None, f"{cls} is not an intersection"
    return {
        graph.value(member, OWL.onProperty): graph.value(member, OWL.allValuesFrom)
        for member in Collection(graph, members)
    }


def test_the_screwing_resource_and_the_robot_are_core_resources_by_one_triple(
    graph: Graph,
) -> None:
    """Each by one direct triple, as every resource class of use case 1."""
    for cls in (JMS.ScrewingResource, JMS.Robot):
        assert set(graph.objects(uri(cls), RDFS.subClassOf)) == {uri(CFC.Resource)}


def test_the_robot_link_goes_both_ways(graph: Graph) -> None:
    assert graph.value(uri(JMS.hasRobot), RDFS.domain) == uri(JMS.ScrewingResource)
    assert graph.value(uri(JMS.hasRobot), RDFS.range) == uri(JMS.Robot)
    assert graph.value(uri(JMS.isRobotOf), RDFS.domain) == uri(JMS.Robot)
    assert graph.value(uri(JMS.isRobotOf), RDFS.range) == uri(JMS.ScrewingResource)
    assert graph.value(uri(JMS.isRobotOf), OWL.inverseOf) == uri(JMS.hasRobot)


def test_the_robot_parameters_are_exactly_the_mqtt_parameters_of_the_file(
    graph: Graph,
) -> None:
    declared = set(graph.subjects(RDFS.subPropertyOf, MQTT_PARAMETER))

    assert declared == {uri(prop) for prop in ROBOT_PARAMETERS}
    assert len(ROBOT_PARAMETERS) == 12


@pytest.mark.parametrize("prop", ROBOT_PARAMETERS, ids=lambda p: p.fragment)
def test_each_parameter_is_an_mqtt_parameter_of_the_robot_with_a_value(
    graph: Graph, prop
) -> None:
    """Without the `inf:hasValue` range the property is no complex property, and the middleware
    never binds it."""
    assert (uri(prop), RDF.type, OWL.ObjectProperty) in graph
    assert set(graph.objects(uri(prop), RDFS.subPropertyOf)) == {MQTT_PARAMETER}
    assert set(graph.objects(uri(prop), RDFS.domain)) == {uri(JMS.Robot)}
    ranges = list(graph.objects(uri(prop), RDFS.range))
    assert len(ranges) == 1 and isinstance(ranges[0], BNode)
    assert restrictions(graph, ranges[0]) == {uri(INF.hasValue): DATATYPE[prop]}


def test_the_file_carries_the_interface_terms_that_the_middleware_reads(
    graph: Graph,
) -> None:
    """The middleware reads a parameter node's metadata only where a range declares it, and it
    hides from the served data model what the MQTT range declares."""
    generic = uri(INF.isInterfaceAccessibleParameter)
    assert graph.value(MQTT_PARAMETER, RDFS.subPropertyOf) == generic
    assert restrictions(graph, graph.value(generic, RDFS.range)) == {
        uri(INF.accessMode): XSD.string
    }
    assert restrictions(graph, graph.value(MQTT_PARAMETER, RDFS.range)) == {
        uri(prop): datatype for prop, datatype in MQTT_METADATA.items()
    }
    for prop in (INF.hasValue, INF.accessMode, *MQTT_METADATA):
        assert (uri(prop), RDF.type, OWL.DatatypeProperty) in graph


def test_each_setpoint_belongs_to_one_value_of_the_screw_type() -> None:
    assert tuple(SETPOINT_OF) == TYPE_LEVEL
    assert SETPOINTS == tuple(SETPOINT_OF.values())
    for type_level, setpoint in SETPOINT_OF.items():
        assert setpoint.fragment == "hasRobot" + type_level.fragment.removeprefix("has")


def test_the_start_command_and_the_setpoints_are_the_settable_parameters() -> None:
    assert set(READWRITE) == {JMS.hasAttemptCommand, *SETPOINTS}


def closure(graph: Graph) -> Graph:
    """`graph` closed under RDFS 2, 3, 5, 7, 9 and 11 and OWL's inverse properties."""
    closed = Graph()
    for triple in graph:
        closed.add(triple)
    size = -1
    while len(closed) != size:
        size = len(closed)
        for relation in (RDFS.subPropertyOf, RDFS.subClassOf):
            for a, _, b in list(closed.triples((None, relation, None))):
                for _, _, c in list(closed.triples((b, relation, None))):
                    closed.add((a, relation, c))
        for prop, _, parent in list(closed.triples((None, RDFS.subPropertyOf, None))):
            for s, _, o in list(closed.triples((None, prop, None))):
                closed.add((s, parent, o))
        for prop, _, inverse in list(closed.triples((None, OWL.inverseOf, None))):
            for s, _, o in list(closed.triples((None, prop, None))):
                closed.add((o, inverse, s))
        for prop, _, cls in list(closed.triples((None, RDFS.domain, None))):
            for s, _, _o in list(closed.triples((None, prop, None))):
                closed.add((s, RDF.type, cls))
        for prop, _, cls in list(closed.triples((None, RDFS.range, None))):
            for _s, _, o in list(closed.triples((None, prop, None))):
                closed.add((o, RDF.type, cls))
        for cls, _, parent in list(closed.triples((None, RDFS.subClassOf, None))):
            for s, _, _o in list(closed.triples((None, RDF.type, cls))):
                closed.add((s, RDF.type, parent))
    return closed


def seeded_robot(graph: Graph) -> Graph:
    """The ontology with what the seed writes of the robot: its class, both links and a node for
    each parameter, with that node's metadata."""
    seeded = Graph()
    for triple in graph:
        seeded.add(triple)
    seeded.add((uri(SCREWING_RESOURCE), RDF.type, uri(JMS.ScrewingResource)))
    seeded.add((uri(ROBOT), RDF.type, uri(JMS.Robot)))
    seeded.add((uri(SCREWING_RESOURCE), uri(JMS.hasRobot), uri(ROBOT)))
    seeded.add((uri(ROBOT), uri(JMS.isRobotOf), uri(SCREWING_RESOURCE)))
    for prop in ROBOT_PARAMETERS:
        node = BNode()
        seeded.add((uri(ROBOT), uri(prop), node))
        for key, values in robot_parameter_node(prop).items():
            for value in values:
                seeded.add((node, URIRef(key), Literal(value)))
    return seeded


def test_the_robot_is_no_screw_type_and_no_screw(graph: Graph) -> None:
    """The setpoints carry the names of the Screw type's values but are properties of the robot:
    a robot that held a type-level property would be a Screw type by that property's domain."""
    closed = closure(seeded_robot(graph))
    assert (uri(ROBOT), RDF.type, uri(JMS.Robot)) in closed

    for individual in (ROBOT, SCREWING_RESOURCE):
        for cls in (JMS.ScrewType, JMS.Screw, JMS.ParameterCopy):
            assert (uri(individual), RDF.type, uri(cls)) not in closed


def test_the_check_sees_a_setpoint_that_specializes_its_type_level_property(
    graph: Graph,
) -> None:
    """The test above must fail on a file that makes a setpoint a subproperty of its value."""
    broken = seeded_robot(graph)
    broken.add(
        (
            uri(JMS.hasRobotMinCounterTorque),
            RDFS.subPropertyOf,
            uri(JMS.hasMinCounterTorque),
        )
    )
    # A setpoint's value is a node in the seed, so the broken file types the robot through the
    # union domain of the type-level property.
    closed = closure(broken)
    members = closed.value(uri(JMS.hasMinCounterTorque), RDFS.domain)
    union = set(Collection(closed, closed.value(members, OWL.unionOf)))

    assert (uri(ROBOT), RDF.type, members) in closed
    assert uri(JMS.ScrewType) in union


@pytest.mark.parametrize("prop", ROBOT_PARAMETERS, ids=lambda p: p.fragment)
def test_each_parameter_node_names_its_broker_and_its_topics(prop) -> None:
    node = robot_parameter_node(prop)
    readwrite = prop in READWRITE

    expected = {
        str(INF.accessMode): ["readwrite" if readwrite else "read"],
        str(INF.hasMQTTTopic): [f"Robot1/{prop.fragment}"],
        str(INF.hasMQTTBrokerIP): ["127.0.0.1"],
        str(INF.hasMQTTBrokerPort): [18860],
        str(INF.hasMQTTValuePath): ["v"],
    }
    if readwrite:
        expected[str(INF.hasMQTTSetTopic)] = [f"Robot1/{prop.fragment}/set"]
    assert node == expected
    # An `int`, because the range is `xsd:integer`.
    assert type(node[str(INF.hasMQTTBrokerPort)][0]) is int


def test_the_topics_and_the_broker_are_the_contracts() -> None:
    assert ROBOT == JMS_NS + "Robot1"
    assert (MQTT_BROKER_IP, MQTT_BROKER_PORT) == ("127.0.0.1", 18860)
    assert mqtt_topic(JMS.hasAttemptState) == "Robot1/hasAttemptState"
    assert mqtt_set_topic(JMS.hasAttemptCommand) == "Robot1/hasAttemptCommand/set"


def test_the_seed_links_the_robot_to_the_screwing_resource_that_jms_uc1_starts() -> (
    None
):
    assert SCREWING_RESOURCE == screwing_resource.RESOURCE


def test_the_run_writes_the_screwing_resource_of_its_one_class() -> None:
    """One asserted class: the middleware resolves the data model it serves from it."""
    turtle = run_core.resources_turtle()
    lines = [line for line in turtle.splitlines() if f"<{SCREWING_RESOURCE}>" in line]

    assert len(lines) == 1
    assert f"<{JMS.ScrewingResource}>" in lines[0]
    assert f"<{CFC.Resource}>" not in lines[0]
