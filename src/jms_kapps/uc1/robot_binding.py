"""How the screwing resource's middleware instance drives the robot over MQTT: the binding of the
robot's parameters, built from the knowledge graph, and the sink that collects every sample.

The knowledge graph describes the robot of the screwing resource (`:Robot1`, written by the seed)
as twelve parameters that a client reaches over MQTT: three readings, the attempt's state, the
start command and seven setpoints, each a parameter node one level below the robot, with its
topic, its broker and its access mode (`seed.robot_parameter_node`). The middleware's connector
wiring recognises such a node by its property, a subproperty of the middleware's MQTT interface
property, and asks a **binding descriptor** for the connectors that realize it: one that reads
the topic into the data model, and one that publishes the data model's value to the set topic for
a parameter a client may set. The middleware ships that descriptor, `MQTTBinding`, and builds
twenty connectors for the robot from it: twelve read, eight write.

**Use case 1's own descriptor** (`robot_wiring`) delegates to the middleware's and changes one
thing: the formatter of each connector. The middleware's formatter turns a payload into the value
the data model holds, and the data model holds only the latest value; an attempt needs every
sample. So the descriptor wraps the formatter (`TappedFormatter`): each payload the read connector
takes goes to the attempt's `Sink` whole, with the parameter it arrived for, before the formatter
takes its value out of it. The sink is thread-safe, because the connectors run on the middleware's
event loop and the attempt on the workflow's worker thread, and gated: it keeps messages only
while an attempt has it open, so a payload between attempts is dropped. The descriptor is
registered in a connector registry of the screwing resource's own, so the process-wide default
registry stays as the middleware ships it. The descriptor also records each parameter it bound
(`BoundParameter`): the setpoint writer needs the field and the node type of a parameter.

**A setpoint is a persistence write that names the parameter** (`Setpoints`, a helper of
its own: the middleware has no public verb for it). The write sets the parameter's field on the robot's
model in the data model and tells the persistence connector which field changed, so that exactly
the write connector of that parameter publishes the value to its set topic, and the data model
shows the value. It runs on the middleware's event loop, from the worker thread, through the loop
that an on-start-up callback captured.

The screwing resource's code names no broker, port or topic: every connector comes from the graph
description, and the broker comes up through the transport hook the build is given
(`mqtt_broker.ensure_transport`).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, NamedTuple

from kapps_ogm import ClassScope
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.connectors.mqtt_binding import (
    MQTTBinding,
    MQTTParameterFormatter,
)
from kapps_semantic_middleware.connectors.semantic import (
    ParameterBinding,
    Registration,
    SemanticConnectorRegistry,
)
from kapps_triplestore_interface import IRI
from transitional_sync_middleware.middleware.registries import ConnectionInfo
from transitional_sync_middleware.middleware.sync.synced_connector import SyncDirection
from transitional_sync_middleware.model.data_model import DataModel

from jms_kapps.uc1.vocabulary import JMS, ROBOT_PARAMETERS

logger = logging.getLogger("jms_kapps.uc1.robot_binding")

# What the screwing resource's middleware reads of its resource to build the robot's connectors:
# each parameter of the robot, one level below the resource. The chains name the parameters, so a
# parameter the graph holds but this list does not is not wired.
ROBOT_SCOPE = ClassScope.from_property_chains(
    [[JMS.hasRobot, parameter] for parameter in ROBOT_PARAMETERS]
)

# How long a setpoint write may take on the event loop before the worker thread gives up on it.
SETPOINT_TIMEOUT_SECONDS = 10.0

# The name of the data model the middleware serves its resource as, and the connectors sync with.
RESOURCE_DATA_MODEL = "resource"

Message = tuple[IRI, Any]
"""One payload a read connector took: the parameter it arrived for, and the payload as the
connector parsed it from JSON (a dict with the value at `v`, and whatever else the robot sent)."""


class NoMessage(Exception):
    """No message arrived within the wait."""


class Messages:
    """The messages of one open attempt, in the order the connectors took them. Within one
    parameter the order is the topic's; across parameters nothing is promised, since each connector
    has a client of its own."""

    def __init__(self, messages: queue.Queue[Message]) -> None:
        self._messages = messages

    def get(self, timeout: float) -> Message:
        """The next message, waiting at most `timeout` seconds.

        Raises:
            NoMessage: If none arrived in time.
        """
        try:
            return self._messages.get(timeout=max(0.0, timeout))
        except queue.Empty:
            raise NoMessage from None


class Sink:
    """Where every payload of the robot goes while an attempt collects: a thread-safe queue that
    the connectors fill from the event loop and the attempt drains on its worker thread.

    It is open only inside `open`. A payload that arrives while it is closed is dropped: between
    attempts the robot still publishes its state, and a sample of an attempt that ended would
    otherwise be taken for one of the next. Opening it drains what a race left behind.
    """

    def __init__(self) -> None:
        self._messages: queue.Queue[Message] = queue.Queue()
        self._open = threading.Event()

    @property
    def is_open(self) -> bool:
        return self._open.is_set()

    def put(self, parameter: IRI, payload: Any) -> None:
        """Keep `payload` of `parameter`, if an attempt is collecting. Never raises: it is called
        inside a connector's receive loop, which a raise would end for good."""
        if self._open.is_set():
            self._messages.put((parameter, payload))

    @contextmanager
    def open(self) -> Iterator[Messages]:
        """Collect while the block runs. Opened before the start command goes out, so no sample
        is missed; closed once the attempt has its frame or gave up."""
        self._drain()
        self._open.set()
        try:
            yield Messages(self._messages)
        finally:
            self._open.clear()
            self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self._messages.get_nowait()
            except queue.Empty:
                return


class TappedFormatter:
    """The middleware's formatter of one parameter, with every inbound payload copied to the sink.

    `deserialize` runs in the read connector's receive loop, with the payload as the connector
    parsed it from JSON, before the formatter takes the value at the value path out of it: so the
    sink gets the whole envelope, the time and the phase of a reading, the counts and the source
    of an end message. `serialize` is the formatter's own: a setpoint goes out as the middleware
    encodes it. Both legs of a parameter share one instance, as they share the formatter they
    wrap.
    """

    def __init__(
        self, inner: MQTTParameterFormatter, parameter: IRI, sink: Sink
    ) -> None:
        self.inner = inner
        self.parameter = parameter
        self.sink = sink

    def deserialize(self, data: Any) -> Any:
        self.sink.put(self.parameter, data)
        return self.inner.deserialize(data)

    def serialize(self, data: Any) -> bytes:
        return self.inner.serialize(data)


class BoundParameter(NamedTuple):
    """One parameter of the robot as the middleware bound it: the binding recognition gave the
    descriptor (the holder, the field on its model, the node's type and metadata), and the
    middleware's formatter of it, which knows the node's shape for a setpoint."""

    binding: ParameterBinding
    formatter: MQTTParameterFormatter

    @property
    def parameter(self) -> IRI:
        return IRI(str(self.binding.parameter_property))


@dataclass
class RobotWiring:
    """What the screwing resource's build is given to drive the robot: the sink its attempt
    collects from, the registry that holds use case 1's descriptor, the transport hook that
    brings up the broker, and, once the middleware has built its connectors, each parameter it
    bound.

    Args:
        ensure_transport: The transport hook the middleware calls with the broker's address
            before it builds the first connector for it; None brings up nothing.
    """

    sink: Sink
    registry: SemanticConnectorRegistry
    ensure_transport: Callable[[str, int], None] | None = None
    bound: dict[IRI, BoundParameter] = field(default_factory=dict)

    def check(self) -> None:
        """Raise unless every parameter of `ROBOT_PARAMETERS` was bound. A graph without the
        seeded robot gives a middleware without connectors, and then every attempt would wait for
        its timeout in silence."""
        missing = [p.fragment for p in ROBOT_PARAMETERS if p not in self.bound]
        if missing:
            raise RuntimeError(
                "the middleware built no connector for "
                + ", ".join(missing)
                + ": the graph does not describe the robot's parameters as the seed writes them"
            )


def robot_wiring(
    ensure_transport: Callable[[str, int], None] | None = None,
) -> RobotWiring:
    """Use case 1's binding descriptor, in a registry of its own, with the sink it fills.

    The descriptor builds what the middleware's `MQTTBinding` builds, from the same metadata, and
    wraps each registration's formatter in a `TappedFormatter` over the wiring's sink. It records
    each parameter it bound.
    """
    sink = Sink()
    bound: dict[IRI, BoundParameter] = {}

    class RobotMQTTBinding:
        """`MQTTBinding` with the formatter tapped: every payload reaches the attempt's sink."""

        connector_cls: ClassVar[Any] = MQTTBinding.connector_cls
        interface_property: ClassVar[IRI] = MQTTBinding.interface_property
        connection_metadata: ClassVar[tuple[IRI, ...]] = MQTTBinding.connection_metadata

        @staticmethod
        def build(
            binding: ParameterBinding,
            direction: SyncDirection,
            *,
            ensure_transport: Callable[[str, int], None] | None = None,
        ) -> Iterable[Registration]:
            registrations = list(
                MQTTBinding.build(binding, direction, ensure_transport=ensure_transport)
            )
            if not registrations:
                return
            inner = registrations[0].formatter
            parameter = IRI(str(binding.parameter_property))
            if not isinstance(inner, MQTTParameterFormatter):
                raise TypeError(
                    f"the middleware's MQTT binding gave {parameter.fragment} a "
                    f"{type(inner).__name__}, not its formatter"
                )
            tapped = TappedFormatter(inner, parameter, sink)
            bound[parameter] = BoundParameter(binding, inner)
            for registration in registrations:
                yield replace(registration, formatter=tapped)

    return RobotWiring(
        sink=sink,
        registry=SemanticConnectorRegistry([RobotMQTTBinding]),
        ensure_transport=ensure_transport,
        bound=bound,
    )


class Setpoints:
    """Writes a value of a parameter of the robot into the data model of the screwing resource's
    middleware, so that the parameter's write connector publishes it to its set topic. The
    middleware has no public verb for a write that names a parameter.

    The write runs on the middleware's event loop, where the connectors run: it reads the
    resource's model from the persistence connector, sets the parameter's field on the robot's
    model to a node holding the value, and hands the model back with the field named as what
    changed. The persistence connector then notifies the connectors of that field alone, and of
    those only the write leg publishes; the data model shows the value from then on. The loop is
    the one the middleware runs its start-up callbacks on, captured there; a write before the
    middleware has started raises.

    Args:
        middleware: The screwing resource's middleware instance.
        resource: The screwing resource, the root of the data model.
        wiring: The wiring that holds each bound parameter: its field and its node's shape.
        timeout: How long the worker thread waits for the loop to finish one write.
    """

    def __init__(
        self,
        middleware: SemanticMiddleware,
        resource: IRI,
        wiring: RobotWiring,
        *,
        timeout: float = SETPOINT_TIMEOUT_SECONDS,
    ) -> None:
        self._middleware = middleware
        self._resource = IRI(resource)
        self._wiring = wiring
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        middleware.add_callback("on_start_up", self._capture_loop)

    async def _capture_loop(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._started.set()

    def write(self, parameter: IRI, value: object) -> None:
        """Write `value` as the value of `parameter` of the robot.

        Raises:
            RuntimeError: If the middleware has not started, if this is called on the event loop
                itself (where waiting for the loop would never end), or if the write did not
                finish within the timeout.
            KeyError: If the middleware built no connector for `parameter`.
        """
        if not self._started.is_set() or self._loop is None:
            raise RuntimeError(
                f"{parameter.fragment} cannot be set: the middleware has not started"
            )
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            raise RuntimeError(
                f"{parameter.fragment} cannot be set from the event loop that runs the "
                "connectors: the attempt belongs on the workflow's worker thread"
            )
        bound = self._wiring.bound.get(IRI(parameter))
        if bound is None:
            raise KeyError(
                f"the middleware built no connector for {parameter.fragment}; it has "
                + ", ".join(sorted(p.fragment for p in self._wiring.bound))
            )
        future = asyncio.run_coroutine_threadsafe(
            self._write(bound, _node(bound.formatter, value)), self._loop
        )
        try:
            future.result(timeout=self._timeout)
        except TimeoutError:
            future.cancel()
            raise RuntimeError(
                f"the write of {parameter.fragment} did not finish within "
                f"{self._timeout:g} s"
            ) from None

    async def _write(self, bound: BoundParameter, node: list[Any]) -> None:
        binding = bound.binding
        persistence = self._middleware.persistence_registry.get_connector_by_data_model_and_model_id(
            data_model_name=RESOURCE_DATA_MODEL, model_id=str(self._resource)
        )
        model = await persistence.provide_persistence_value()
        robot = DataModel.from_models(model).get_model(str(binding.resource_iri))
        setattr(robot, binding.field_id, node)
        changed = ConnectionInfo(
            data_model_name=RESOURCE_DATA_MODEL,
            model_id=str(self._resource),
            contained_model_id=str(binding.resource_iri),
            field_id=binding.field_id,
        )
        logger.info(
            "%s: %s is set on %s",
            self._resource.fragment,
            bound.parameter.fragment,
            binding.resource_iri.fragment,
        )
        await persistence.consume(model, changed=changed)


def _node(formatter: MQTTParameterFormatter, value: object) -> list[Any]:
    """The persistence value of a parameter holding `value`: a one-element list with the node, in
    the shape the middleware's formatter writes for an inbound reading, with the same facets."""
    fields = dict(formatter.northbound_facets)
    fields[formatter.value_field] = [value]
    return [formatter.model_type(**fields)]
