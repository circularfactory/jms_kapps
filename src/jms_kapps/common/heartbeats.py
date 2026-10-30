"""The two heartbeat settings of a participant process, read from its environment: how often its
middleware writes its service's heartbeat, and how old a heartbeat may be for a service to count
as live.

- `JMS_KAPPS_HEARTBEAT_INTERVAL_SECONDS`: the heartbeat interval that the two UC2 processes, a
  module and the WarehouseManagementSystem, give their middleware. Unset or empty, the
  middleware's own default (30 s). `off` or `0`, none: the middleware starts no heartbeat loop,
  and only the heartbeat that its registration writes with the service's address stays.
- `JMS_KAPPS_LIVE_HEARTBEAT_AGE_SECONDS`: the live-heartbeat age that the services reader
  (`common.services`) judges a service by, read once as that module is imported. Unset or empty,
  90 s.

Both exist for the measurement harness, which turns the heartbeats off for a run: the interval
none and the age above the run's limit, so that no service turns stale during the run. Without
the variables, every process takes the defaults above.

A value that is no number of seconds is refused, with a message that names the variable: a
process refuses to start rather than run with a setting it was not given.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Mapping

from kapps_semantic_middleware import SemanticMiddleware

HEARTBEAT_INTERVAL_VARIABLE = "JMS_KAPPS_HEARTBEAT_INTERVAL_SECONDS"
LIVE_HEARTBEAT_AGE_VARIABLE = "JMS_KAPPS_LIVE_HEARTBEAT_AGE_SECONDS"

# The value of the interval variable that turns the heartbeat loop off, as `0` does.
OFF = "off"

# The middleware's own heartbeat interval, the default of its constructor.
MIDDLEWARE_HEARTBEAT_INTERVAL_SECONDS: float | None = (
    inspect.signature(SemanticMiddleware).parameters["heartbeat_interval"].default
)

# The middleware's own staleness threshold: it writes a heartbeat every 30 s, and its watchdog
# sweeps a service whose heartbeat is older than 90 s.
DEFAULT_LIVE_HEARTBEAT_AGE_SECONDS = 90.0


class HeartbeatSettingRefused(ValueError):
    """A heartbeat setting holds no number of seconds that it takes."""


def heartbeat_interval(environ: Mapping[str, str]) -> float | None:
    """The heartbeat interval, in seconds, that `environ` gives a middleware: None for no
    heartbeat loop.

    Raises:
        HeartbeatSettingRefused: If the variable holds neither `off` nor a number of seconds of 0
            or more.
    """
    return interval_seconds(
        environ.get(HEARTBEAT_INTERVAL_VARIABLE, ""), HEARTBEAT_INTERVAL_VARIABLE
    )


def live_heartbeat_age(environ: Mapping[str, str]) -> float:
    """How old a heartbeat may be, in seconds, for its service to count as live, as `environ`
    gives it.

    Raises:
        HeartbeatSettingRefused: If the variable holds no positive number of seconds.
    """
    return age_seconds(
        environ.get(LIVE_HEARTBEAT_AGE_VARIABLE, ""), LIVE_HEARTBEAT_AGE_VARIABLE
    )


def interval_seconds(value: str, name: str) -> float | None:
    """The heartbeat interval that `value`, the setting `name` (a variable, or the harness's
    option), says: the middleware's own if it is empty, None for `off` or 0.

    Raises:
        HeartbeatSettingRefused: If `value` is neither `off` nor a number of seconds of 0 or
            more, naming `name`.
    """
    value = value.strip()
    if value == "":
        return MIDDLEWARE_HEARTBEAT_INTERVAL_SECONDS
    if value.lower() == OFF:
        return None
    seconds = _seconds(value)
    if seconds is None or seconds < 0:
        raise HeartbeatSettingRefused(
            f"{name} must be a number of seconds, or {OFF} (or 0) for no heartbeat loop, "
            f"not {value!r}"
        )
    if seconds == 0:
        return None
    return seconds


def age_seconds(value: str, name: str) -> float:
    """The live-heartbeat age that `value`, the setting `name` (a variable, or the harness's
    option), says: 90 s if it is empty.

    Raises:
        HeartbeatSettingRefused: If `value` is no positive number of seconds, naming `name`.
    """
    value = value.strip()
    if value == "":
        return DEFAULT_LIVE_HEARTBEAT_AGE_SECONDS
    seconds = _seconds(value)
    if seconds is None or seconds <= 0:
        raise HeartbeatSettingRefused(
            f"{name} must be a positive number of seconds, not {value!r}"
        )
    return seconds


def interval_value(seconds: float | None) -> str | None:
    """What the interval variable holds for a process to take `seconds`: None, the variable
    unset, for the middleware's own default."""
    if seconds == MIDDLEWARE_HEARTBEAT_INTERVAL_SECONDS:
        return None
    if seconds is None:
        return OFF
    return repr(float(seconds))


def age_value(seconds: float) -> str | None:
    """What the age variable holds for a process to take `seconds`: None, the variable unset, for
    the default."""
    if seconds == DEFAULT_LIVE_HEARTBEAT_AGE_SECONDS:
        return None
    return repr(float(seconds))


def _seconds(value: str) -> float | None:
    """`value` as a finite number of seconds, or None if it is none."""
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not math.isfinite(seconds):
        return None
    return seconds
