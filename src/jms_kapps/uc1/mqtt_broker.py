"""The MQTT broker of the screwing resource's robot, brought up inside the screwing resource's process.

`ensure_transport` is the transport hook that the screwing resource passes to its middleware
instance. The middleware calls it with the broker's address, as the graph names it on each
parameter of the robot, before it builds the first connector for that address. The hook then
starts an in-process amqtt broker there, on a daemon thread with its own event loop. The broker
lives as long as the process that started it and needs no teardown. The robot controller
stand-in reconnects to it when it is not there yet.
"""

from __future__ import annotations

import asyncio
import socket
import threading

# How long the hook waits for the broker to listen.
BROKER_READY_TIMEOUT_SECONDS = 5.0


def listening(host: str, port: int) -> bool:
    """Whether something answers at `host`:`port`."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


async def _serve(host: str, port: int, ready: threading.Event) -> None:
    """Run one amqtt broker at `host`:`port` until the process ends; set `ready` when it listens."""
    from amqtt.broker import Broker

    broker = Broker(
        {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{host}:{port}",
                    "max_connections": 100,
                }
            },
            "sys_interval": 0,
            "auth": {"allow-anonymous": True},
            "topic-check": {"enabled": False},
        }
    )
    await broker.start()
    ready.set()
    await asyncio.Event().wait()


def ensure_transport(host: str, port: int) -> None:
    """Bring up an MQTT broker at `host`:`port`, unless something listens there already.

    The middleware calls it once for each address, from its constructor, before the process has
    an event loop; so the broker gets a thread and an event loop of its own. The thread is a
    daemon, and the broker ends with the process.

    Raises:
        RuntimeError: If the broker does not listen within `BROKER_READY_TIMEOUT_SECONDS`, or its
            start fails, with the cause. The middleware's constructor then fails, and the process
            ends with the reason.
    """
    if listening(host, port):
        return

    # Set when the broker listens, or when its start has failed: the wait ends at the first.
    settled = threading.Event()
    failure: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(_serve(host, port, settled))
        except BaseException as error:
            if settled.is_set():
                raise
            failure.append(error)
            settled.set()

    threading.Thread(target=run, name=f"mqtt-broker-{port}", daemon=True).start()
    settled.wait(timeout=BROKER_READY_TIMEOUT_SECONDS)

    if failure:
        cause = failure[0]
        raise RuntimeError(
            f"The MQTT broker at {host}:{port} did not start: {type(cause).__name__}: {cause}"
        ) from cause
    if not settled.is_set():
        raise RuntimeError(
            f"The MQTT broker at {host}:{port} is not listening after "
            f"{BROKER_READY_TIMEOUT_SECONDS:g} s."
        )
