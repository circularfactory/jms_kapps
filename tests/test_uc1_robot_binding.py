"""Offline tests of use case 1's binding descriptor: over hand-made parameter bindings, as the
assessment's probe built them, it yields the read and write registrations of the twelve robot
parameters with the wrapping formatter, in a registry of the screwing resource's own.

No store, no broker, no middleware: a `ParameterBinding` is built by hand from the node the seed
writes for each parameter (`seed.robot_parameter_node`), and the descriptor is asked for its
registrations as the middleware's wiring would ask it.
"""

from __future__ import annotations

from kapps_semantic_middleware.connectors.mqtt_binding import (
    MQTTBinding,
    MQTTParameterFormatter,
)
from kapps_semantic_middleware.connectors.semantic import (
    ParameterBinding,
    default_registry,
    normalize_metadata,
    resolve_direction,
)
from kapps_semantic_middleware.vocabulary import INF
from kapps_triplestore_interface import IRI
from pydantic import create_model
from transitional_sync_middleware.middleware.sync.synced_connector import SyncDirection

from jms_kapps.uc1.robot_binding import (
    ROBOT_SCOPE,
    NoMessage,
    RobotWiring,
    Sink,
    TappedFormatter,
    robot_wiring,
)
from jms_kapps.uc1.seed import (
    MQTT_BROKER_IP,
    MQTT_BROKER_PORT,
    ROBOT,
    mqtt_set_topic,
    mqtt_topic,
    robot_parameter_node,
)
from jms_kapps.uc1.vocabulary import JMS, READINGS, READWRITE, ROBOT_PARAMETERS


def node_model():
    """A node model with the fields the OGM mangles from the node's properties, as the generated
    AnonymousClass has them."""
    fields = {
        INF.hasValue.lined: (list, []),
        INF.accessMode.lined: (list, []),
    }
    return create_model("AnonymousClass", **fields)  # type: ignore[call-overload]


def binding(
    parameter: IRI, wiring: RobotWiring, *, port: int = MQTT_BROKER_PORT
) -> ParameterBinding:
    """The binding of `parameter` of the robot, as recognition would build it from the seeded
    node, for the descriptor of `wiring`."""
    metadata = robot_parameter_node(parameter)
    metadata[str(INF.hasMQTTBrokerPort)] = [port]
    (descriptor,) = list(wiring.registry)
    return ParameterBinding(
        resource_iri=ROBOT,
        parameter_property=parameter,
        field_id=parameter.lined,
        metadata=normalize_metadata(metadata),
        descriptor=descriptor,
        node_model_type=node_model(),
    )


def registrations(wiring: RobotWiring, hook=None, *, port: int = MQTT_BROKER_PORT):
    """Every registration of the twelve parameters, as the wiring plan asks the descriptor:
    `(binding, registration)` pairs, the direction narrowed by the parameter's access mode."""
    (descriptor,) = list(wiring.registry)
    pairs = []
    for parameter in ROBOT_PARAMETERS:
        b = binding(parameter, wiring, port=port)
        direction = resolve_direction(b.access_mode, SyncDirection.BIDIRECTIONAL)
        for registration in descriptor.build(b, direction, ensure_transport=hook):
            pairs.append((b, registration))
    return pairs


def test_the_twelve_parameters_give_twelve_read_and_eight_write_registrations() -> None:
    """One read leg per parameter, one write leg per readwrite parameter: twenty connectors."""
    pairs = registrations(robot_wiring())

    assert len(pairs) == 20
    suffixes = [r.suffix for _, r in pairs]
    assert suffixes.count("read") == 12
    assert suffixes.count("write") == 8
    written = {IRI(str(b.parameter_property)) for b, r in pairs if r.suffix == "write"}
    assert written == set(READWRITE)
    directions = {r.suffix: r.sync_direction for _, r in pairs}
    assert directions == {
        "read": SyncDirection.TO_PERSISTENCE,
        "write": SyncDirection.FROM_PERSISTENCE,
    }


def test_each_connector_takes_its_topic_and_broker_from_the_graph_description() -> None:
    """The read leg subscribes to the parameter's topic, the write leg publishes to its set topic,
    both on the seeded broker: nothing of it is spelled out in the screwing resource."""
    for b, r in registrations(robot_wiring()):
        parameter = IRI(str(b.parameter_property))
        connector = r.connector
        assert connector.mqtt_broker_ip == MQTT_BROKER_IP
        assert connector.mqtt_broker_port == MQTT_BROKER_PORT
        expected = (
            mqtt_topic(parameter) if r.suffix == "read" else mqtt_set_topic(parameter)
        )
        assert connector.topic == expected


def test_the_connector_ids_the_middleware_would_give_are_unique() -> None:
    ids = [
        f"{b.resource_iri}#{b.field_id}#{r.suffix}"
        for b, r in registrations(robot_wiring())
    ]

    assert len(ids) == len(set(ids)) == 20


def test_each_registration_carries_the_tapped_formatter_shared_by_both_legs() -> None:
    """The read leg and the write leg of a parameter share one tapped formatter, which wraps the
    middleware's own and names the parameter."""
    wiring = robot_wiring()
    by_parameter: dict[IRI, list] = {}
    for b, r in registrations(wiring):
        by_parameter.setdefault(IRI(str(b.parameter_property)), []).append(r.formatter)

    assert set(by_parameter) == set(ROBOT_PARAMETERS)
    for parameter, formatters in by_parameter.items():
        assert all(isinstance(f, TappedFormatter) for f in formatters)
        assert len({id(f) for f in formatters}) == 1
        assert formatters[0].parameter == parameter
        assert formatters[0].sink is wiring.sink
        assert isinstance(formatters[0].inner, MQTTParameterFormatter)
        assert formatters[0].inner.value_path == "v"


def test_the_wiring_records_each_binding_and_the_middlewares_formatter_by_parameter() -> (
    None
):
    wiring = robot_wiring()
    registrations(wiring)

    assert set(wiring.bound) == set(ROBOT_PARAMETERS)
    assert (
        wiring.bound[JMS.hasAttemptCommand].binding.field_id == JMS.hasAttemptCommand.lined
    )
    wiring.check()


def test_the_check_names_the_parameters_the_middleware_did_not_bind() -> None:
    wiring = robot_wiring()
    (descriptor,) = list(wiring.registry)
    b = binding(JMS.hasAxialForce, wiring)
    list(descriptor.build(b, SyncDirection.TO_PERSISTENCE))

    try:
        wiring.check()
    except RuntimeError as error:
        assert "hasRobotPosition" in str(error)
        assert "hasAxialForce" not in str(error)
    else:
        raise AssertionError("the check passed with eleven parameters unbound")


def test_the_transport_hook_is_called_with_the_seeded_broker_address() -> None:
    """The hook comes from the graph description: the broker and the port of the node. Deduping
    across parameters is the wiring plan's job, so here it is called once per parameter."""
    calls: list[tuple[str, int]] = []
    registrations(
        robot_wiring(), lambda host, port: calls.append((host, port)), port=18999
    )

    assert set(calls) == {(MQTT_BROKER_IP, 18999)}
    assert len(calls) == 12


def test_the_registry_is_the_screwing_resources_own_and_the_default_stays_as_shipped() -> (
    None
):
    wiring = robot_wiring()

    assert wiring.registry is not default_registry
    assert (
        wiring.registry.for_interface_property(INF.isInterfaceAccessibleMQTTParameter)
        is not MQTTBinding
    )
    assert (
        default_registry.for_interface_property(INF.isInterfaceAccessibleMQTTParameter)
        is MQTTBinding
    )
    assert (
        wiring.registry.declared_connection_metadata()
        == default_registry.declared_connection_metadata()
    )


def test_the_tapped_formatter_copies_the_whole_envelope_to_an_open_sink() -> None:
    """The sink gets the payload as the connector parsed it, time and phase included, and the
    middleware's formatter still gets its value: the data model is updated as before."""
    wiring = robot_wiring()
    force = [
        r for b, r in registrations(wiring) if b.parameter_property == JMS.hasAxialForce
    ]
    (read,) = force  # a reading: one leg, no write
    payload = {"t": 1700000000000000000, "v": 2.5, "phase": "Approach"}

    with wiring.sink.open() as messages:
        nodes = read.formatter.deserialize(payload)
        assert messages.get(timeout=0) == (JMS.hasAxialForce, payload)
        try:
            messages.get(timeout=0)
        except NoMessage:
            pass
        else:
            raise AssertionError("one payload gave two messages")
    assert len(nodes) == 1
    assert getattr(nodes[0], INF.hasValue.lined) == [2.5]


def test_a_closed_sink_drops_what_arrives_and_opening_it_starts_empty() -> None:
    sink = Sink()
    sink.put(JMS.hasAttemptState, {"v": "idle"})
    assert not sink.is_open

    with sink.open() as messages:
        assert sink.is_open
        try:
            messages.get(timeout=0)
        except NoMessage:
            pass
        else:
            raise AssertionError("a message that arrived while closed was kept")
        sink.put(JMS.hasAttemptState, {"v": "running"})
        assert messages.get(timeout=0) == (JMS.hasAttemptState, {"v": "running"})
    assert not sink.is_open


def test_the_tapped_formatter_serializes_as_the_middlewares_formatter_does() -> None:
    wiring = robot_wiring()
    (write,) = [
        r
        for b, r in registrations(wiring)
        if b.parameter_property == JMS.hasAttemptCommand and r.suffix == "write"
    ]
    node = wiring.bound[JMS.hasAttemptCommand].formatter
    value = [node.model_type(**{node.value_field: ["https://example.org/Screw1"]})]

    assert write.formatter.serialize(value) == node.serialize(value)
    assert write.formatter.serialize(value) == b'{"v": "https://example.org/Screw1"}'


def test_the_class_scope_reaches_each_parameter_through_the_robot() -> None:
    """Twelve chains, each `hasRobot` then the parameter: the middleware follows `hasRobot` to the
    robot and finds the parameter nodes one level below the resource."""
    chains = ROBOT_SCOPE.to_property_chains()

    assert sorted(list(map(str, chain)) for chain in chains) == sorted(
        [str(JMS.hasRobot), str(parameter)] for parameter in ROBOT_PARAMETERS
    )
    assert len(READINGS) == 3
