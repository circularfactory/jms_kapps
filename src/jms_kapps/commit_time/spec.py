"""What a run's record says of the spec it belongs to: the key of a configuration, and the fields
of a KAPPS run and of a writes-only replay that name the spec, the configuration and the
repetition (`sweep_fields`, `replay_fields`).

The commit time experiment script writes the spec of its two KAPPS configurations beside its
parameters (`commit_time.toml`) and builds the configurations itself; each record names that
file, the configuration's number and its key.

**The key** of a configuration is the digest of its values (`configuration.KEYS`), each number as
a float, the prefill by its file name: the first 16 hex digits of the SHA-256 of their JSON with
sorted keys. The pick seed is no part of it, so all repetitions of a configuration keep one key.
A writes-only replay's key is the replayed configuration's key and its factor, such as
`3f9a1c2b7d4e5f60@x1`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def key(values: Mapping[str, Any]) -> str:
    """The digest of a configuration's values, each number as a float."""
    canonical = {
        name: float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else value
        for name, value in values.items()
    }
    text = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def valid_factor(value: Any) -> bool:
    """Whether `value` is a replay factor: a finite number from 1. Below 1 a replay would be slower
    than the recorded run."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 1
    )


def sweep_fields(
    spec: Path, *, number: int, key: str, repetition: int, repetitions: int
) -> dict[str, Any]:
    """What the record of a KAPPS run says of the spec: the spec's file, the configuration's
    number and key, and the repetition of how many."""
    return {
        "spec": str(spec),
        "configuration": number,
        "key": key,
        "repetition": repetition,
        "repetitions": repetitions,
    }


def replay_fields(
    spec: Path, *, number: int, key: str, factor: float, repetition: int
) -> dict[str, Any]:
    """What the record of a writes-only replay says of the spec: the spec's file,
    the replayed configuration's number, the replay's key (the configuration's key and `factor`),
    the key it replays, its factor and its repetition."""
    return {
        "spec": str(spec),
        "configuration": number,
        "key": f"{key}@x{factor:g}",
        "replayed_key": key,
        "replay_factor": factor,
        "repetition": repetition,
        "repetitions": 1,
    }
