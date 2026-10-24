"""The robot controller stand-in on the MQTT wire, with an in-process broker and no store.

A test client drives the stand-in exactly as the screwing resource's connectors will: it sends
the start command and the setpoints on their set topics, and collects the readings and the state
of the attempt. The frame it puts together from the readings must be the example frame of the
recording, stamped at the attempt's start.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import aiomqtt
import pytest

from jms_kapps.uc1 import robot_controller
from jms_kapps.uc1.frame import Frame
from jms_kapps.uc1.mqtt_broker import ensure_transport
from jms_kapps.uc1.replay import EXAMPLE_RECORDINGS, example_frame
from jms_kapps.uc1.robot_controller import (
    COMMAND,
    READINGS,
    SETPOINTS,
    STATE,
    set_topic,
    topic,
)
from jms_kapps.uc1.seed import RECORDING_OF, mqtt_set_topic, mqtt_topic
from jms_kapps.uc1.vocabulary import JMS
from jms_kapps.uc1.vocabulary import READINGS as READING_TERMS
from jms_kapps.uc1.vocabulary import SETPOINTS as SETPOINT_TERMS

HOST = "127.0.0.1"
SPEED = 200.0
RECORDINGS = {str(screw): name for screw, name in RECORDING_OF.items()}
SCREW_OF = {name: screw for screw, name in RECORDINGS.items()}
# Long enough for a replay at `SPEED` and a broker's start.
TIMEOUT = 10.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def run_apart(main: Coroutine) -> None:
    """Run `main` on an event loop of its own thread: a test before this one may have left a
    loop running on the main thread, where `asyncio.run` refuses to start."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, main).result()


@pytest.fixture
def port() -> int:
    """A port with a broker of its own on it."""
    port = free_port()
    ensure_transport(HOST, port)
    return port


@asynccontextmanager
async def controller(port: int, speed: float = SPEED) -> AsyncIterator[None]:
    """The stand-in, running on the broker at `port` while the block runs."""
    task = asyncio.create_task(robot_controller.run(HOST, port, RECORDINGS, speed))
    try:
        yield
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class Probe:
    """The test client: every message on the device's read topics, in the order they came."""

    def __init__(self, client: aiomqtt.Client) -> None:
        self.client = client
        self.messages: list[tuple[str, dict]] = []
        self._arrived = asyncio.Condition()

    async def collect(self) -> None:
        async for message in self.client.messages:
            async with self._arrived:
                self.messages.append((message.topic.value, json.loads(message.payload)))
                self._arrived.notify_all()

    async def wait_for(self, found: Callable[[list[tuple[str, dict]]], bool]) -> None:
        async with self._arrived:
            await asyncio.wait_for(
                self._arrived.wait_for(lambda: found(self.messages)), TIMEOUT
            )

    def states(self) -> list[dict]:
        return [payload for name, payload in self.messages if name == topic(STATE)]

    async def send(self, parameter: str, payload: dict) -> None:
        await self.client.publish(set_topic(parameter), json.dumps(payload), qos=1)


def with_probe(port: int, body: Callable[[Probe], Awaitable[None]]) -> None:
    """Run `body` with a probe subscribed to every read topic of the device."""

    async def main() -> None:
        async with aiomqtt.Client(HOST, port) as client:
            for parameter in (*READINGS, STATE, *SETPOINTS):
                await client.subscribe(topic(parameter), qos=1)
            probe = Probe(client)
            collecting = asyncio.create_task(probe.collect())
            try:
                await body(probe)
            finally:
                collecting.cancel()

    run_apart(main())


def has_state(state: str, count: int = 1):
    return lambda messages: (
        sum(1 for name, p in messages if name == topic(STATE) and p["v"] == state)
        >= count
    )


def reassembled(probe: Probe, name: str) -> Frame:
    """The frame that the readings make, with the source that `done` names."""
    readings = {
        parameter: [p for t, p in probe.messages if t == topic(parameter)]
        for parameter in READINGS
    }
    first = readings["hasRobotPosition"]
    (done,) = [state for state in probe.states() if state["v"] == "done"]
    for parameter in READINGS:
        assert [p["t"] for p in readings[parameter]] == [p["t"] for p in first]
        assert [p["phase"] for p in readings[parameter]] == [p["phase"] for p in first]
    assert done["samples"] == {parameter: len(first) for parameter in READINGS}
    return Frame(
        times=[p["t"] for p in first],
        signals={
            signal: [p["v"] for p in readings[parameter]]
            for parameter, signal in READINGS.items()
        },
        phases=[p["phase"] for p in first],
        source=done["source"],
    )


@pytest.mark.parametrize("name", EXAMPLE_RECORDINGS)
def test_a_start_replays_the_example_frame_of_the_screws_recording(
    port: int, name: str
) -> None:
    async def body(probe: Probe) -> None:
        async with controller(port):
            await probe.wait_for(has_state("idle"))
            await probe.send(COMMAND, {"v": SCREW_OF[name]})
            await probe.wait_for(has_state("idle", 2))

        states = [state["v"] for state in probe.states()]
        assert states == ["idle", "running", "done", "idle"]
        frame = reassembled(probe, name)
        # Stamped as recorded at the attempt's start: the example frame, moved by that time.
        recorded = example_frame(name, start=0)
        start = frame.times[0] - recorded.times[0]
        assert frame == example_frame(name, start=start)

    with_probe(port, body)


def test_a_setpoint_comes_back_on_its_read_topic(port: int) -> None:
    async def body(probe: Probe) -> None:
        async with controller(port):
            await probe.wait_for(has_state("idle"))
            for index, parameter in enumerate(SETPOINTS):
                await probe.send(parameter, {"v": float(index) + 0.5})
            await probe.wait_for(
                lambda messages: (
                    len([m for m in messages if m[0] != topic(STATE)]) == len(SETPOINTS)
                )
            )

        echoed = {name: p for name, p in probe.messages if name != topic(STATE)}
        assert echoed == {
            topic(parameter): {"v": float(index) + 0.5}
            for index, parameter in enumerate(SETPOINTS)
        }

    with_probe(port, body)


def test_an_unknown_screw_ends_failed_with_a_reason(port: int) -> None:
    async def body(probe: Probe) -> None:
        async with controller(port):
            await probe.wait_for(has_state("idle"))
            await probe.send(COMMAND, {"v": "http://example.org/NoScrew"})
            await probe.wait_for(has_state("idle", 2))

        idle, failed, idle_again = probe.states()
        assert (idle["v"], failed["v"], idle_again["v"]) == ("idle", "failed", "idle")
        assert "NoScrew" in failed["reason"]

    with_probe(port, body)


def test_a_start_during_an_attempt_is_answered_failed_and_the_attempt_goes_on(
    port: int,
) -> None:
    screw = SCREW_OF["success"]

    async def body(probe: Probe) -> None:
        async with controller(port, speed=20.0):
            await probe.wait_for(has_state("idle"))
            await probe.send(COMMAND, {"v": screw})
            await probe.wait_for(has_state("running"))
            await probe.send(COMMAND, {"v": screw})
            await probe.wait_for(has_state("idle", 2))

        states = [state["v"] for state in probe.states()]
        assert states == ["idle", "running", "failed", "done", "idle"]
        assert probe.states()[2]["reason"]

    with_probe(port, body)


def test_the_controller_connects_to_a_broker_that_starts_after_it() -> None:
    port = free_port()

    async def main() -> None:
        async with controller(port):
            await asyncio.sleep(0.5)
            await asyncio.to_thread(ensure_transport, HOST, port)
            async with aiomqtt.Client(HOST, port) as client:
                await client.subscribe(topic(STATE), qos=1)
                await client.subscribe(topic(SETPOINTS[0]), qos=1)

                async def echoed() -> dict:
                    # The stand-in may not be subscribed yet: send until the echo comes.
                    while True:
                        await client.publish(
                            set_topic(SETPOINTS[0]), '{"v": 1.0}', qos=1
                        )
                        try:
                            async with asyncio.timeout(0.5):
                                async for message in client.messages:
                                    if message.topic.value == topic(SETPOINTS[0]):
                                        return json.loads(message.payload)
                        except TimeoutError:
                            continue

                assert await asyncio.wait_for(echoed(), TIMEOUT) == {"v": 1.0}

    run_apart(main())


def test_the_topics_are_the_ones_the_seed_describes() -> None:
    """The stand-in is configured like a device, so it names its topics itself; they must be the
    ones that the graph names for the robot."""
    assert [topic(name) for name in READINGS] == [mqtt_topic(p) for p in READING_TERMS]
    assert [set_topic(name) for name in SETPOINTS] == [
        mqtt_set_topic(p) for p in SETPOINT_TERMS
    ]
    assert topic(STATE) == mqtt_topic(JMS.hasAttemptState)
    assert set_topic(COMMAND) == mqtt_set_topic(JMS.hasAttemptCommand)


def test_the_controller_imports_nothing_of_the_middleware_the_ontology_or_the_graph() -> (
    None
):
    """In a fresh interpreter, so that what the module pulls in transitively counts too."""
    script = (
        "import sys, jms_kapps.uc1.robot_controller\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in {"
        "'kapps_semantic_middleware', 'kapps_ogm', 'kapps_triplestore_interface', "
        "'transitional_sync_middleware', 'rdflib', 'pyshacl'} "
        "or m in {'jms_kapps.uc1.vocabulary', 'jms_kapps.uc1.seed', "
        "'jms_kapps.common.triplestore'})\n"
        "print(bad)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"


def test_a_recording_that_does_not_parse_ends_the_process_with_an_error() -> None:
    for argument in ("no-equals-sign", f"{SCREW_OF['success']}=no_such_recording"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "jms_kapps.uc1.robot_controller",
                "--broker-host",
                HOST,
                "--broker-port",
                "1",
                "--recording",
                argument,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode != 0
        assert "--recording" in result.stderr


def test_the_module_replays_over_mqtt_as_its_own_process(port: int) -> None:
    """`python -m`, as the supervisor starts it."""
    command = [
        sys.executable,
        "-m",
        "jms_kapps.uc1.robot_controller",
        "--broker-host",
        HOST,
        "--broker-port",
        str(port),
        "--speed",
        str(SPEED),
        *(
            argument
            for screw, name in RECORDINGS.items()
            for argument in ("--recording", f"{screw}={name}")
        ),
    ]

    async def body(probe: Probe) -> None:
        process = await asyncio.create_subprocess_exec(*command)
        try:
            await probe.wait_for(has_state("idle"))
            await probe.send(COMMAND, {"v": SCREW_OF["missing_screw"]})
            await probe.wait_for(has_state("idle", 2))
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)

        frame = reassembled(probe, "missing_screw")
        recorded = example_frame("missing_screw", start=0)
        assert frame == example_frame(
            "missing_screw", start=frame.times[0] - recorded.times[0]
        )

    with_probe(port, body)
