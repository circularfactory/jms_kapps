"""A scan of use case 1's source: the robot is driven through the middleware's connectors, built
from the knowledge graph, so no module of use case 1 but the robot controller stand-in and the
broker hook speaks MQTT itself, and the screwing resource names no broker, port or topic.

The stand-in is a device: it speaks only MQTT, and it is the one module allowed to import the
client library. The broker hook brings up the in-process broker, and is the one module allowed to
import the broker. Everything else reaches the robot through the graph description.
"""

from __future__ import annotations

import re
from pathlib import Path

from _source import SOURCE

UC1 = SOURCE / "uc1"

# The modules of use case 1 that speak MQTT themselves: the device and the broker.
MQTT_MODULES = {"robot_controller.py", "mqtt_broker.py"}

# The modules that hold the screwing resource's side: the process, its binding and its attempt.
SCREWING_RESOURCE_MODULES = (
    "screwing_resource.py",
    "robot_binding.py",
    "robot_attempt.py",
)

IMPORTS_MQTT = re.compile(r"^\s*(from|import)\s+(aiomqtt|amqtt)\b", re.MULTILINE)


def uc1_modules() -> list[Path]:
    return sorted(UC1.glob("*.py"))


def test_only_the_stand_in_and_the_broker_hook_import_an_mqtt_library() -> None:
    importing = {
        path.name for path in uc1_modules() if IMPORTS_MQTT.search(path.read_text())
    }

    assert importing == MQTT_MODULES


def test_the_screwing_resources_code_names_no_broker_port_or_topic() -> None:
    """No broker address, no port, no topic, no `/set`: every connector comes from the graph. The
    seed's constants that hold them are not imported either."""
    from jms_kapps.uc1.seed import MQTT_BROKER_IP, MQTT_BROKER_PORT, ROBOT

    forbidden = [
        MQTT_BROKER_IP,
        str(MQTT_BROKER_PORT),
        f"{ROBOT.fragment}/",
        "/set",
        "MQTT_BROKER_IP",
        "MQTT_BROKER_PORT",
        "mqtt_topic",
        "mqtt_set_topic",
        "MqttClientConnector(",
        "aiomqtt",
        "amqtt",
    ]
    for name in SCREWING_RESOURCE_MODULES:
        text = (UC1 / name).read_text()
        found = [word for word in forbidden if word in text]
        assert not found, f"{name} names {found}"

