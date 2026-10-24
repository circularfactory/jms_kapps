"""The robot controller stand-in: a device that speaks only MQTT and replays an example recording.

It takes the place of the controller of the screwing resource's robot, as the PLC of the
TransferUnit demo of kapps_semantic_middleware takes the place of a conveyor's. It knows nothing
of the middleware, the ontology or the knowledge graph: it is configured like a device, with its
broker and its topics, and the graph describes it. The screwing resource's middleware instance
builds its connectors from that description.

Started as `python -m jms_kapps.uc1.robot_controller` with `--broker-host`, `--broker-port` and
one `--recording <Screw IRI>=<recording>` for each Screw it can unscrew.

**On the wire.** Every topic is `Robot1/<parameter>`, and a client sets a parameter on
`Robot1/<parameter>/set`. Every payload is a JSON object with the value at `v`. No message is
retained.

- Once connected, and after each end of an attempt, it publishes `idle` on `Robot1/hasAttemptState`.
- A start is `{"v": "<Screw IRI>"}` on `Robot1/hasAttemptCommand/set`. It publishes `running`,
  then each sample of the Screw's recording on the three reading topics, as
  `{"t": <ns since epoch>, "v": <value>, "phase": <phase or null>}`, in the recording's own timing
  divided by the speed factor. `t` is the sample's time in the recording stamped at the start of
  the attempt, whatever the speed. Then it publishes
  `{"v": "done", "samples": {<reading>: <count>, …}, "source": <the recording's tag>}`, then `idle`.
- A start for a Screw it has no recording for ends in `{"v": "failed", "reason": …}`, then `idle`.
  A start while an attempt runs is answered `failed` with a reason, and the running attempt goes on.
- It republishes each setpoint it receives on that setpoint's read topic, so that a reader sees it
  arrive. The setpoints do not change the replay.

When the broker is not there yet, or goes away, it connects again with a growing pause. An attempt
that the broker's loss interrupts ends with no `done`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence

import aiomqtt
from aiomqtt.types import PayloadType

from jms_kapps.uc1.frame import AXIAL_FORCE, ROBOT_POSITION, UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.replay import EXAMPLE_RECORDINGS, example_frame

# Named, not `__name__`: the process runs this module as `python -m`.
logger = logging.getLogger("jms_kapps.uc1.robot_controller")

# The device's name, the first segment of each of its topics.
DEVICE = "Robot1"

# The reading topics, by the signal of a frame that each carries.
READINGS: dict[str, str] = {
    "hasRobotPosition": ROBOT_POSITION,
    "hasAxialForce": AXIAL_FORCE,
    "hasUnscrewingTorque": UNSCREWING_TORQUE,
}
STATE = "hasAttemptState"
COMMAND = "hasAttemptCommand"
SETPOINTS: tuple[str, ...] = (
    "hasRobotMinApproachAxialForce",
    "hasRobotMinCounterTorque",
    "hasRobotMinPreloadAxialForce",
    "hasRobotPreparationTurningAngle",
    "hasRobotMaxLooseTorque",
    "hasRobotMinLooseningAxialForce",
    "hasRobotMaxDynamicTorque",
)

# The pause before the first reconnect, and the longest pause.
FIRST_RETRY_SECONDS = 0.2
LONGEST_RETRY_SECONDS = 5.0


def topic(parameter: str) -> str:
    """The topic on which the device publishes `parameter`."""
    return f"{DEVICE}/{parameter}"


def set_topic(parameter: str) -> str:
    """The topic on which the device takes a new value of `parameter`."""
    return f"{topic(parameter)}/set"


# The setpoint that each set topic sets.
_SETPOINT_OF_SET_TOPIC: dict[str, str] = {set_topic(p): p for p in SETPOINTS}


def _payload(value: object, **fields: object) -> bytes:
    """A payload of the device: `value` at `v`, beside the other `fields` of the message."""
    return json.dumps({"v": value, **fields}).encode()


class RobotController:
    """The stand-in on one connection to the broker.

    Args:
        recordings: The example recording that a start replays, by the IRI of the Screw.
        speed: How much faster than recorded the replay runs; 1.0 is the recording's own timing.
    """

    def __init__(self, recordings: Mapping[str, str], speed: float = 1.0) -> None:
        if speed <= 0:
            raise ValueError(f"the speed factor must be positive, not {speed}")
        for screw, name in recordings.items():
            if name not in EXAMPLE_RECORDINGS:
                raise ValueError(
                    f"no example recording {name!r} for {screw}: the replay has "
                    f"{', '.join(EXAMPLE_RECORDINGS)}"
                )
        self.recordings = dict(recordings)
        self.speed = speed
        self._attempt: asyncio.Task | None = None

    async def serve(self, client: aiomqtt.Client) -> None:
        """Subscribe, announce `idle`, and answer the messages of one connection until it ends."""
        await client.subscribe(set_topic(COMMAND))
        for parameter in SETPOINTS:
            await client.subscribe(set_topic(parameter))
        await self._state(client, "idle")
        try:
            async for message in client.messages:
                await self._handle(client, message.topic.value, message.payload)
        finally:
            if self._attempt is not None and not self._attempt.done():
                self._attempt.cancel()
                logger.warning(
                    "The broker went away during an attempt; it ends with no result"
                )
            self._attempt = None

    async def _handle(
        self, client: aiomqtt.Client, name: str, payload: PayloadType
    ) -> None:
        if name == set_topic(COMMAND):
            await self._start(client, payload)
            return
        parameter = _SETPOINT_OF_SET_TOPIC.get(name)
        if parameter is not None:
            logger.info("Setpoint %s <- %s", parameter, _text(payload))
            await client.publish(topic(parameter), payload, qos=1)

    async def _start(self, client: aiomqtt.Client, payload: PayloadType) -> None:
        if self._attempt is not None and not self._attempt.done():
            await self._state(client, "failed", reason="an attempt is running")
            return
        try:
            screw = json.loads(_text(payload))["v"]
        except (ValueError, TypeError, KeyError) as error:
            await self._end_failed(
                client, f"the start command is not {{'v': <Screw>}}: {error}"
            )
            return
        name = self.recordings.get(screw) if isinstance(screw, str) else None
        if name is None:
            await self._end_failed(client, f"no recording for the Screw {screw!r}")
            return
        logger.info("Start: %s replays %s", screw, name)
        self._attempt = asyncio.create_task(self._replay(client, name))
        self._attempt.add_done_callback(_report_an_attempt_that_raised)

    async def _replay(self, client: aiomqtt.Client, name: str) -> None:
        await self._state(client, "running")
        frame = example_frame(name, start=time.time_ns())
        await self._publish_samples(client, frame)
        await self._state(
            client,
            "done",
            samples={parameter: len(frame.times) for parameter in READINGS},
            source=frame.source,
        )
        logger.info("Done: %s, %d samples", frame.source, len(frame.times))
        await self._state(client, "idle")

    async def _publish_samples(self, client: aiomqtt.Client, frame: Frame) -> None:
        """Each sample of `frame` on the three reading topics, at its time divided by the speed."""
        loop = asyncio.get_running_loop()
        began = loop.time()
        first = frame.times[0]
        for index, stamp in enumerate(frame.times):
            delay = began + (stamp - first) / 1e9 / self.speed - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            for parameter, signal in READINGS.items():
                await client.publish(
                    topic(parameter),
                    _payload(
                        frame.signals[signal][index], t=stamp, phase=frame.phases[index]
                    ),
                    qos=1,
                )

    async def _end_failed(self, client: aiomqtt.Client, reason: str) -> None:
        logger.warning("Failed: %s", reason)
        await self._state(client, "failed", reason=reason)
        await self._state(client, "idle")

    async def _state(
        self, client: aiomqtt.Client, state: str, **fields: object
    ) -> None:
        await client.publish(topic(STATE), _payload(state, **fields), qos=1)


def _report_an_attempt_that_raised(attempt: asyncio.Task) -> None:
    """Log why an attempt ended early, when a publish of it failed: the broker went away."""
    if not attempt.cancelled() and attempt.exception() is not None:
        logger.warning("The attempt ended with no result: %s", attempt.exception())


def _text(payload: PayloadType) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


async def run(
    host: str, port: int, recordings: Mapping[str, str], speed: float = 1.0
) -> None:
    """Serve on the broker at `host`:`port` until cancelled, and connect again whenever the
    connection fails, after a pause that doubles up to `LONGEST_RETRY_SECONDS`."""
    controller = RobotController(recordings, speed)
    pause = FIRST_RETRY_SECONDS
    while True:
        try:
            async with aiomqtt.Client(
                host, port, identifier=f"{DEVICE}-controller"
            ) as client:
                logger.info("Connected to the broker at %s:%d", host, port)
                pause = FIRST_RETRY_SECONDS
                await controller.serve(client)
        except aiomqtt.MqttError as error:
            logger.info(
                "No broker at %s:%d (%s); again in %.1f s", host, port, error, pause
            )
        await asyncio.sleep(pause)
        pause = min(pause * 2, LONGEST_RETRY_SECONDS)


def _recording(text: str) -> tuple[str, str]:
    screw, sep, name = text.rpartition("=")
    if not sep or not screw:
        raise argparse.ArgumentTypeError(f"{text!r} is not <Screw IRI>=<recording>")
    if name not in EXAMPLE_RECORDINGS:
        raise argparse.ArgumentTypeError(
            f"no example recording {name!r}: the replay has {', '.join(EXAMPLE_RECORDINGS)}"
        )
    return screw, name


def _speed(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not value > 0:
        raise argparse.ArgumentTypeError(
            f"the speed factor must be positive, not {text}"
        )
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jms_kapps.uc1.robot_controller",
        description="The robot controller stand-in of use case 1: it replays an example "
        "recording over MQTT on each start command.",
    )
    parser.add_argument(
        "--broker-host", required=True, help="The address of the MQTT broker."
    )
    parser.add_argument(
        "--broker-port", required=True, type=int, help="The port of the MQTT broker."
    )
    parser.add_argument(
        "--recording",
        required=True,
        action="append",
        type=_recording,
        metavar="SCREW=RECORDING",
        help="The example recording that a start for the Screw replays; once for each Screw.",
    )
    parser.add_argument(
        "--speed",
        type=_speed,
        default=1.0,
        help="How much faster than recorded the replay runs (default: 1.0).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    try:
        asyncio.run(
            run(args.broker_host, args.broker_port, dict(args.recording), args.speed)
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
