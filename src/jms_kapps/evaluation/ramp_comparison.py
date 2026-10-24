"""A ramp's comparison with the replay of the matching UC2 configuration (interim names).
Pure functions: no store, no clock.

A ramp's level is UC2-limited when fewer than 90 % of its offered spawns are accepted (`ramp`).
Then the store may have been far from its limit, and the table says how far: beside UC2's achieved
store request rate at its first UC2-limited level, it puts the **offered** store request rate at
the first level where the store-only baseline's replay of the same configuration broke (`baseline`,
`replay_levels`).

**The replay of a ramp** is the compressed replay of the UC2 configuration (not a ramp's level)
that has the same topology, prefill, SHACL setting, module strategy, WMS strategy and history
collection (`MATCHING_KEYS`). When several configurations match, the first in spec order counts. The
box rate and the other keys may differ: the replay's rate is the factor, not a box rate.

**The replay's first repetition.** A replay may be repeated on request (`heavy_parts`); every
repetition of a ramp is compared with the replay's first repetition (interim reading).

**Three outcomes of the replay's side.** `broken`: the offered store request rate per minute at
the replay's first broken level (the lowest factor whose baseline is broken). `not broken`: the
replay broke at no level, text "not broken up to factor F", F the highest factor replayed.
`no replay`: no configuration matches, or the matching one has no replay level that counts.

**The licensed cores** (interim names). GraphDB Enterprise runs at most one
concurrent query for each licensed core, and the compressed replay is an open loop, so its first
broken level can be this limit of the license, not the capacity of the store. The replay's side
says the licensed cores beside it: the maximum of CPU cores (`maxCpuCores`) of the server's license
view, which each record keeps whole, from the records of the level that the comparison uses (the
first broken level, else the highest level). Records of that level with different values show each
value; where none of them has the view, the read failed, or the answer names no maximum, the side
says "not read". A side with no replay says no licensed cores.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from jms_kapps.evaluation.ramp import Judgement

# The keys of a configuration that the ramp's configuration and the replayed one share.
MATCHING_KEYS = (
    "topology",
    "prefill",
    "shacl",
    "module_strategy",
    "wms_strategy",
    "collect_history",
)

# The outcomes of the replay's side of the comparison.
REPLAY_BROKEN = "broken"
NOT_BROKEN = "not broken"
NO_REPLAY = "no replay"

# What the replay's side says of the licensed cores where no record of its level holds them
# (interim name).
NOT_READ = "not read"


def matching_row(
    ramp_values: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The row of the table whose configuration the replay of a ramp with `ramp_values` replays:
    among the rows that are no ramp's and match on `MATCHING_KEYS`, the one whose first
    configuration comes first in spec order; None where none matches."""
    matching = [
        row
        for row in rows
        if row["ramp"] is None
        and all(row["values"].get(key) == ramp_values.get(key) for key in MATCHING_KEYS)
    ]
    return min(matching, key=lambda row: min(row["configurations"]), default=None)


def replay_side(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the first repetition of the replay of the configuration of `row` (`matching_row`)
    says: its outcome, the configuration, and the factor and the offered store request rate per
    minute at its first broken level, or the highest factor where it broke at none. Each
    repetition of a ramp is compared with the replay's first repetition."""
    levels = (
        []
        if row is None
        else [
            level
            for level in row["replay_levels"]
            if (level.get("repetition") or 1) == 1
        ]
    )
    found: dict[str, Any] = {
        "outcome": NO_REPLAY,
        "configuration": None if row is None else min(row["configurations"]),
        "factor": None,
        "offered_store_requests_per_minute": None,
        "text": NO_REPLAY,
        "licensed_cores": None,
        "licensed_cores_text": None,
    }
    if not levels:
        return found
    broken = [level for level in levels if level["broken"]]
    if broken:
        first = broken[0]
        return (
            found
            | {
                "outcome": REPLAY_BROKEN,
                "factor": first["factor"],
                "offered_store_requests_per_minute": first["offered_per_minute"],
                "text": f"broken at factor {first['factor']:g}",
            }
            | licensed_cores(levels, first["factor"])
        )
    highest = max(level["factor"] or 1.0 for level in levels)
    return (
        found
        | {
            "outcome": NOT_BROKEN,
            "factor": highest,
            "text": f"not broken up to factor {highest:g}",
        }
        | licensed_cores(levels, highest)
    )


def licensed_cores(
    levels: Sequence[Mapping[str, Any]], factor: float | None
) -> dict[str, Any]:
    """The licensed cores of the replay's level at `factor` among `levels` (interim name): each value that its records hold, in rising order (`licensed_cores`), and the text that
    the table shows (`licensed_cores_text`): the values, or `NOT_READ` where no record holds one."""
    values = sorted(
        {
            int(level["licensed_cores"])
            for level in levels
            if (level["factor"] or 1.0) == (factor or 1.0)
            and level.get("licensed_cores") is not None
        }
    )
    return {
        "licensed_cores": values,
        "licensed_cores_text": ", ".join(map(str, values)) or NOT_READ,
    }


def comparison(
    ramp_values: Mapping[str, Any],
    judged: Judgement,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The comparison of a ramp, whose configurations have `ramp_values` and which went as
    `judged`, with the replay of the matching UC2 configuration among the table's `rows`: UC2's
    achieved store request rate per minute at the ramp's first UC2-limited level, None where it has
    none, and the replay's side."""
    achieved = None
    if judged.uc2_limited_at is not None:
        [at_level] = [
            level
            for level in judged.levels
            if level["boxes_per_minute"] == judged.uc2_limited_at
        ]
        achieved = at_level["numbers"]["store_requests_per_minute"]
    return {
        "uc2_limited_at": judged.uc2_limited_at,
        "uc2_store_requests_per_minute": achieved,
        "replay": replay_side(matching_row(ramp_values, rows)),
    }
