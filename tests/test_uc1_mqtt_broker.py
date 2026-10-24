"""The transport hook of the screwing resource: an in-process MQTT broker at the robot's address."""

from __future__ import annotations

import asyncio
import socket
import threading

import pytest

from jms_kapps.uc1 import mqtt_broker
from jms_kapps.uc1.mqtt_broker import ensure_transport, listening

HOST = "127.0.0.1"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def broker_threads(port: int) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == f"mqtt-broker-{port}"]


def test_the_hook_brings_up_a_broker_once_for_an_address() -> None:
    port = free_port()
    assert not listening(HOST, port)

    ensure_transport(HOST, port)
    ensure_transport(HOST, port)

    assert listening(HOST, port)
    assert len(broker_threads(port)) == 1


def test_the_hook_starts_nothing_where_something_listens_already() -> None:
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        sock.listen()
        port = sock.getsockname()[1]

        ensure_transport(HOST, port)

        assert broker_threads(port) == []


def test_a_broker_that_does_not_start_fails_the_hook_with_its_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(host: str, port: int, ready: threading.Event) -> None:
        raise OSError("no such address")

    monkeypatch.setattr(mqtt_broker, "_serve", broken)

    with pytest.raises(RuntimeError, match="did not start: OSError: no such address"):
        ensure_transport(HOST, free_port())


def test_a_broker_that_never_listens_fails_the_hook_after_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def silent(host: str, port: int, ready: threading.Event) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(mqtt_broker, "_serve", silent)
    monkeypatch.setattr(mqtt_broker, "BROKER_READY_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(RuntimeError, match="is not listening after 0.2 s"):
        ensure_transport(HOST, free_port())
