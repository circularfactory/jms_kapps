"""The attempt against the robot controller stand-in, over an in-process broker, through the
connectors that use case 1's descriptor builds: the seam of this build.

The connectors are built as the assessment's probe built them, on a middleware without a store: a
`transitional_sync_middleware` middleware with a hand-made model of the screwing resource that
contains the robot with its twelve parameters, persisted as the data model `resource`; each
registration the descriptor yields over hand-made bindings (`test_uc1_robot_binding`) is added as a
synced connector; the middleware's lifespan runs on a loop thread of its own, as uvicorn's does in
a process. The attempt then runs on the test's thread, as it runs on the workflow's worker thread:
its setpoints are persistence writes through `Setpoints`, which the sync layer fans out to the
write connectors, and its samples come from the read connectors through the tapped formatters.

Offline: no triple store. The broker is `mqtt_broker.ensure_transport` on a free port; the stand-in
runs in-process (`robot_controller.run`) at a speed factor, so a recording plays in well under a
second.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from collections.abc import Iterator

import aiomqtt
import pytest
from kapps_semantic_middleware.connectors.semantic import resolve_direction
from kapps_semantic_middleware.vocabulary import INF
from kapps_triplestore_interface import IRI
from pydantic import create_model
from test_uc1_robot_binding import binding
from transitional_sync_middleware.middleware.middleware import Middleware
from transitional_sync_middleware.middleware.sync.synced_connector import SyncDirection

from jms_kapps.uc1 import robot_controller
from jms_kapps.uc1.mqtt_broker import ensure_transport
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.robot_attempt import AttemptFailed, AttemptTimeout, mqtt_attempt
from jms_kapps.uc1.robot_binding import (
    RESOURCE_DATA_MODEL,
    RobotWiring,
    Setpoints,
    robot_wiring,
)
from jms_kapps.uc1.seed import (
    M4_VALUES,
    MQTT_BROKER_IP,
    RECORDING_OF,
    ROBOT,
    SCREW,
    SCREW_2,
    SCREWING_RESOURCE,
    mqtt_set_topic,
)
from jms_kapps.uc1.vocabulary import JMS, ROBOT_PARAMETERS, SETPOINT_OF

SPEED = 50.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind((MQTT_BROKER_IP, 0))
        return sock.getsockname()[1]


@pytest.fixture(autouse=True)
def fresh_sync_manager() -> None:
    """The sync layer keeps every synced connector of a process in one module-wide manager, keyed
    by the persistence connector's id, which is the same in every test here (`resource` and the
    screwing resource's IRI). Without a reset, a write in one test fans out to the disconnected
    connectors of the tests before it, each of which then tries to reconnect, for seconds. A
    process runs one middleware, so this is the test's problem alone."""
    from transitional_sync_middleware.middleware.sync.connector_sync_manager import (
        connector_sync_manager,
    )

    connector_sync_manager._initialize()


@pytest.fixture
def port() -> int:
    """A port with a broker of its own on it, as the screwing resource's middleware brings one
    up."""
    port = free_port()
    ensure_transport(MQTT_BROKER_IP, port)
    return port


class Loop:
    """An event loop on a thread of its own, as uvicorn's is in a process."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coroutine, timeout: float = 30.0):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(
            timeout=timeout
        )

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)


@pytest.fixture
def loop() -> Iterator[Loop]:
    loop = Loop()
    yield loop
    loop.close()


def models():
    """The screwing resource's model with its robot and the robot's twelve parameter fields, as
    the OGM materializes them: an `id` for a named individual, a list for each property."""
    Robot = create_model(
        "Robot", id=(str, ...), **{p.lined: (list, []) for p in ROBOT_PARAMETERS}
    )
    ScrewingResource = create_model(
        "ScrewingResource", id=(str, ...), **{JMS.hasRobot.lined: (list, [])}
    )
    robot = Robot(id=str(ROBOT))
    return ScrewingResource(
        **{"id": str(SCREWING_RESOURCE), JMS.hasRobot.lined: [robot]}
    )


class Probe:
    """A wiring of the twelve parameters on a middleware without a store, running on `loop`."""

    def __init__(self, loop: Loop, port: int) -> None:
        self.loop = loop
        self.wiring: RobotWiring = robot_wiring()
        self.middleware = Middleware()
        self.middleware.load_model_instances(RESOURCE_DATA_MODEL, [models()])
        self.middleware.add_callback(
            "on_start_up", self.middleware.persist, RESOURCE_DATA_MODEL, models()
        )
        (descriptor,) = list(self.wiring.registry)
        self.registrations = []
        for parameter in ROBOT_PARAMETERS:
            b = binding(parameter, self.wiring, port=port)
            direction = resolve_direction(b.access_mode, SyncDirection.BIDIRECTIONAL)
            for registration in descriptor.build(b, direction):
                self.registrations.append((b, registration))
                self.middleware.add_synced_connector(
                    connector_id=f"{b.resource_iri}#{b.field_id}#{registration.suffix}",
                    connector=registration.connector,
                    model_type=registration.model_type,
                    data_model_name=RESOURCE_DATA_MODEL,
                    model_id=str(SCREWING_RESOURCE),
                    contained_model_id=str(b.resource_iri),
                    field_id=b.field_id,
                    formatter=registration.formatter,
                    sync_role=registration.sync_role,
                    sync_direction=registration.sync_direction,
                )
        self.setpoints = Setpoints(
            self.middleware, SCREWING_RESOURCE, self.wiring, timeout=10.0
        )
        self._started = asyncio.Event()
        self._stop: asyncio.Event | None = None
        self._lifespan = None

    async def _run(self) -> None:
        self._stop = asyncio.Event()
        async with self.middleware.lifespan(self.middleware.app):
            self._started.set()
            await self._stop.wait()

    def start(self) -> None:
        self._lifespan = asyncio.run_coroutine_threadsafe(self._run(), self.loop.loop)
        import time

        deadline = time.monotonic() + 30.0
        while not self._started.is_set():
            if self._lifespan.done():
                self._lifespan.result()  # raises what the start-up raised
                raise RuntimeError("the lifespan ended before it started")
            if time.monotonic() > deadline:
                raise TimeoutError("the middleware did not start within 30 s")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._stop is not None:
            self.loop.loop.call_soon_threadsafe(self._stop.set)
        if self._lifespan is not None:
            self._lifespan.result(timeout=30.0)

    def robot_model(self):
        """The robot's model in the data model, as the persistence holds it now."""
        persistence = self.middleware.persistence_registry.get_connector_by_data_model_and_model_id(
            data_model_name=RESOURCE_DATA_MODEL, model_id=str(SCREWING_RESOURCE)
        )
        resource = self.loop.run(persistence.provide_persistence_value())
        (robot,) = getattr(resource, JMS.hasRobot.lined)
        return robot


@pytest.fixture
def probe(loop: Loop, port: int) -> Iterator[Probe]:
    probe = Probe(loop, port)
    probe.start()
    yield probe
    probe.stop()


@contextlib.contextmanager
def stand_in(loop: Loop, port: int, recordings=None):
    """The robot controller stand-in on the broker at `port`, while the block runs."""
    recordings = {str(s): r for s, r in (recordings or RECORDING_OF).items()}

    async def start():
        return asyncio.ensure_future(
            robot_controller.run(MQTT_BROKER_IP, port, recordings, SPEED)
        )

    task = loop.run(start())
    try:
        yield
    finally:

        async def cancel():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        loop.run(cancel())


class SetTopics:
    """Counts what reaches the set topics of the robot: what the write connectors publish."""

    def __init__(self, loop: Loop, port: int) -> None:
        self.loop = loop
        self.port = port
        self.received: list[tuple[str, bytes]] = []
        self._ready = asyncio.Event()
        self._task = None

    async def _run(self) -> None:
        async with aiomqtt.Client(MQTT_BROKER_IP, self.port) as client:
            await client.subscribe(f"{ROBOT.fragment}/+/set")
            self._ready.set()
            async for message in client.messages:
                self.received.append((str(message.topic), bytes(message.payload)))

    def start(self) -> None:
        self._task = asyncio.run_coroutine_threadsafe(self._run(), self.loop.loop)
        self.loop.run(self._ready.wait())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()


def test_the_attempt_over_mqtt_gives_the_frame_the_replay_gives(
    probe: Probe, loop: Loop, port: int
) -> None:
    """For both recordings, the frame built from the robot's samples through the connectors equals
    today's replayed frame of the same start: signals, phases, relative times, source."""
    attempt = mqtt_attempt(probe.setpoints.write, probe.wiring.sink, timeout=20.0)

    with stand_in(loop, port):
        for screw in (SCREW, SCREW_2):
            frame = attempt(screw, M4_VALUES)
            assert frame == example_frame(RECORDING_OF[screw], start=frame.start), screw


def test_the_setpoints_and_the_start_reach_the_stand_in_through_the_write_connectors(
    probe: Probe, loop: Loop, port: int
) -> None:
    """Exactly eight messages go out per attempt, one on each set topic: the seven setpoints with
    their values, and the start command with the Screw; the data model shows each value; and the
    stand-in's echo of each setpoint comes back on its read topic."""
    probe_topics = SetTopics(loop, port)
    probe_topics.start()
    attempt = mqtt_attempt(probe.setpoints.write, probe.wiring.sink, timeout=20.0)
    try:
        with stand_in(loop, port):
            attempt(SCREW, M4_VALUES)
            import json
            import time

            deadline = time.monotonic() + 5
            while len(probe_topics.received) < 8 and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        probe_topics.stop()

    by_topic = {topic: json.loads(payload) for topic, payload in probe_topics.received}
    assert len(probe_topics.received) == 8, probe_topics.received
    assert by_topic == {
        **{mqtt_set_topic(SETPOINT_OF[p]): {"v": v} for p, v in M4_VALUES.items()},
        mqtt_set_topic(JMS.hasAttemptCommand): {"v": str(SCREW)},
    }
    robot = probe.robot_model()
    for type_property, value in M4_VALUES.items():
        (node,) = getattr(robot, SETPOINT_OF[type_property].lined)
        assert getattr(node, INF.hasValue.lined) == [value]
    (command,) = getattr(robot, JMS.hasAttemptCommand.lined)
    assert getattr(command, INF.hasValue.lined) == [str(SCREW)]
    # The readings reached the data model too: the latest sample of each.
    (state,) = getattr(robot, JMS.hasAttemptState.lined)
    assert getattr(state, INF.hasValue.lined) == ["idle"]


def test_a_stand_in_that_never_answers_ends_the_attempt_failed_after_the_timeout(
    probe: Probe,
) -> None:
    """No stand-in on the broker: the setpoints and the start go out, nothing comes back, and the
    attempt raises its timeout, whose text the screwing resource puts into the operation's reason."""
    attempt = mqtt_attempt(probe.setpoints.write, probe.wiring.sink, timeout=0.5)

    with pytest.raises(AttemptTimeout) as error:
        attempt(SCREW, M4_VALUES)

    assert str(error.value).startswith("the robot did not end the attempt within 0.5 s")


def test_a_screw_the_stand_in_has_no_recording_for_fails_with_its_reason(
    probe: Probe, loop: Loop, port: int
) -> None:
    attempt = mqtt_attempt(probe.setpoints.write, probe.wiring.sink, timeout=20.0)
    unknown = IRI("Screw9", base=str(SCREW).rsplit("#", 1)[0] + "#")

    with stand_in(loop, port), pytest.raises(AttemptFailed) as error:
        attempt(unknown, M4_VALUES)

    assert str(error.value).startswith("the robot reports the attempt failed: ")
    assert "Screw9" in str(error.value)


def test_a_write_from_the_connectors_loop_is_refused(probe: Probe, loop: Loop) -> None:
    """The attempt belongs on the worker thread: a write on the event loop itself would wait for
    that same loop and never return, so it raises instead."""

    async def on_loop():
        probe.setpoints.write(JMS.hasAttemptCommand, str(SCREW))

    with pytest.raises(RuntimeError) as error:
        loop.run(on_loop())

    assert "event loop" in str(error.value)


def test_a_write_before_the_middleware_started_is_refused(port: int) -> None:
    wiring = robot_wiring()
    setpoints = Setpoints(Middleware(), SCREWING_RESOURCE, wiring)

    with pytest.raises(RuntimeError) as error:
        setpoints.write(JMS.hasAttemptCommand, str(SCREW))

    assert "has not started" in str(error.value)
