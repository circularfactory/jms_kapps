"""The spec of a sweep of the measurement harness, iteration one: which configurations a sweep
runs, and how often each.

A spec is a plain TOML file of **sweep blocks**, with defaults of its own:

::

    [defaults]                      # the spec's own defaults, for each of its configurations
    boxes_per_minute = 30
    warm_up_seconds = 60
    window_seconds = 300
    drain_bound_seconds = 300
    matcher_interval_seconds = 2
    transit_seconds = 3.0
    retry_seconds = 1.0
    heavy = false
    repetitions = 5
    pick_base_seed = 20261012       # optional: the base of each repetition's pick seed

    [[sweep]]                       # one sweep block: the cross product of its axis values
    topology = ["cross", "grid"]
    prefill = ["{topology}-1k.ttl", "{topology}-1M.ttl"]

A block's keys are those of a configuration (`configuration.KEYS`) and `repetitions`, each with
one value or a list of values: the block is the cross product of its lists, the first key varying
slowest. A block with no key at all is one configuration, of the defaults alone.

**Precedence.** A value comes from the command's defaults (`COMMAND_DEFAULTS`: the strategies
`real`, SHACL on, history collection off, no prefill, the wrapper on, the transit time 3.0 s, the
retry wait 1.0 s, 1 repetition), then the spec's defaults,
then its block, then a flag of the command (`setting`), each over the one before.

**The pick seed** (added on 2026-10-12, jms_kapps 93, decided by Sören on jms_kapps 85; interim
names, jms_kapps 83). Repetition k of every configuration gets the seed `pick_seed(base, k)` for
the WarehouseManagementSystem's picks, so that the repetitions of two configurations stay paired.
The base is `pick_base_seed` of the spec's `[defaults]`, a whole number, or else the command's fixed
`DEFAULT_BASE_SEED`. The seed is no key of a configuration and no part of its key: all repetitions
of a configuration keep one key. The sweep writes it into each run's saved configuration.

**The prefill seed** (added on 2026-10-12, jms_kapps 100, interim name, jms_kapps 83). `jms-eval`
makes each prefill file that a spec names and that is missing (`harness`), with the seed 1 unless
the spec's `[defaults]` give `prefill_seed`, a whole number from 0. Like the pick base seed it is
no key of a configuration and no part of its key.

**The series** (added on 2026-10-14, jms_kapps 110, decided by Sören on 2026-10-13 as the product
owner of the tests, jms_kapps 85, note 1866296; interim name, jms_kapps 83). The configuration
numbers of two specs point to different configurations: configuration 7 of the iteration-one spec
is not configuration 7 of the "after" spec. So a spec names its series in its defaults, `series =
"before"`, a text, and the readers and the sweep show each number with it, "before 7"
(`SpecConfiguration.name`). A spec without the name takes its file name without the extension.
Like the seeds, the series is no key of a configuration and no part of its key; the records match
by their key as before, and a run's folder, a record file and `--configurations` keep the number.

**The configurations of a spec** are the union of its blocks, in the listed order, with no
duplicates: a configuration that a block lists again keeps its place and its number. Each has its
number of repetitions, and one listed again with another number of them is refused. So a spec
lists the configurations it runs first in blocks of their own, and its design in blocks after
them, which repeat them without running them twice.

**A missing value** is refused before any run starts: every value that is not a default of the
command comes from the spec or a flag, and the refusal names the block and the value.

**A prefill file is for one topology**, so a prefill may name its topology's name with
`{topology}`: a shipped topology's name, or a topology file's name without its suffix
(`../topologies/grid-60.json` gives `grid-60`).

**Paths.** A topology file is read from the spec's folder, as a configuration reads its own; a
prefill file from the prefills folder of the command, since a prefill file of 10M statements is
no file to keep beside a spec. Neither needs to exist until its configuration resolves
(`resolved`), just before the sweep's first run.

**The key of a configuration** is a digest of its values as the spec gives them, the prefill's
topology filled in, with every number as a float: so a run is found again when the spec lists its
configuration elsewhere or writes 60 as 60.0, and where the spec, the checkout or the prefills
folder moved.

**A ramp** is a block marked as a ramp, `ramp = true`, whose only axis is its box rates, rising:

::

    [[sweep]]
    ramp = true
    topology = "grid"
    prefill = "{topology}-1M.ttl"
    boxes_per_minute = [10, 20, 40, 80, 160, 320]
    repetitions = 1

Each rate is one load level, made by one run, so a ramp has 1 repetition. Its runs are heavy: its
configurations take `heavy = true` whatever the spec's defaults say, and a ramp whose block or a
flag says `heavy = false` is refused. They run with the wrapper, since the broken rule reads the
measures that need it (`ramp`). A ramp's configurations are its own: one that another block lists
too is refused. (The ramp added on 2026-10-12, jms_kapps 49.)

**The precision rule** (added on 2026-10-12, jms_kapps 101, interim names, jms_kapps 83). A spec
with a `[precision]` table does not give a fixed number of repetitions to its configurations at
normal load: each runs until the rule holds (`precision`), in rounds (`rounds`), at most
`maximum_runs` times.

::

    [precision]                     # every key is optional; these are the values
    latency_tolerance = 0.10        # the bootstrap interval of the pooled commit p95 within +-10 %
    throughput_spread = 0.10        # the IQR of the transports ended per minute at most 10 % of the median
    minimum_runs = 10               # valid runs needed, from 2: from at least two rounds
    maximum_runs = 20               # runs made at most

These are settings of the spec outside a configuration's key. A table that gives `pass_sizes` is
refused: the rounds replaced the passes (jms_kapps 111, decided by Sören on 2026-10-13; until
then `pass_sizes = [5, 5]` gave the sizes of the first passes). A configuration under the rule is
one that is neither a ramp's level nor heavy; its `repetitions` is `maximum_runs`, and the spec,
a block or a flag that gives it `repetitions` is refused. A ramp's level and a heavy configuration
are heavy parts (`heavy_parts`): each runs one time, in round 1, and again on request, so one with
`repetitions` other than 1 is refused (jms_kapps 114, decided by Sören on 2026-10-13; until then
they kept their fixed repetitions, as in a spec without the rule).

**Replay factors** (added on 2026-10-12, jms_kapps 98, interim names, jms_kapps 83). A sweep block
may list the factors by which the store-only baseline replays a recorded run compressed in time:

::

    [[sweep]]
    topology = "grid"
    prefill = "{topology}-1M.ttl"
    boxes_per_minute = 30
    replay_factors = [1, 2, 4, 8, 16]

After the UC2 runs of each configuration of the block, the sweep replays the first valid run of that
configuration once per factor, in rising order (`replay_levels`). The factors are a setting of the
block, outside the configuration's key and no axis of its cross product: a configuration that a
later block lists again gets the factors of both. Each is a number from 1; a replay needs the
store requests of a run with the wrapper, so a configuration with factors has `wrapper = true`.
Every factor above 1 is a heavy run (`approval`). Each level has a key of its own, the
configuration's key and the factor (`replay_levels.level_key`).

(Added on 2026-10-11, jms_kapps 78.)
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from jms_kapps.evaluation.configuration import (
    DEFAULT_BASE_SEED,
    KEYS,
    NO_PREFILL,
    Configuration,
    ConfigurationRefused,
    pick_seed,
    resolve,
)
from jms_kapps.uc2.module import DEFAULT_RETRY_SECONDS
from jms_kapps.uc2.seed import DEFAULT_TRANSIT_SECONDS
from jms_kapps.uc2.strategies import ModuleStrategy, WmsStrategy

# The keys of a spec's defaults and of each of its blocks: those of a configuration, and how many
# times each configuration runs.
REPETITIONS = "repetitions"
SPEC_KEYS = (*KEYS, REPETITIONS)

# The command's defaults: the strategies `real`, SHACL on, history collection off, no prefill, the
# wrapper on (added on 2026-10-12, jms_kapps 48), the transit time 3.0 s and the retry wait 1.0 s
# (added on 2026-10-12, jms_kapps 93: the values the use case takes by default), and 1 repetition.
# Every other value comes from the spec, or from a flag.
COMMAND_DEFAULTS: Mapping[str, Any] = {
    "prefill": NO_PREFILL,
    "module_strategy": "real",
    "wms_strategy": "real",
    "shacl": True,
    "collect_history": False,
    "wrapper": True,
    "transit_seconds": DEFAULT_TRANSIT_SECONDS,
    "retry_seconds": DEFAULT_RETRY_SECONDS,
    REPETITIONS: 1,
}

# What a prefill names to name its topology's name.
TOPOLOGY_PLACEHOLDER = "{topology}"

# The key that marks a block as a ramp (added on 2026-10-12, jms_kapps 49). A block may say it;
# the spec's defaults and the flags may not.
RAMP = "ramp"

# The key of the spec's defaults that gives the seed of the prefill files that `jms-eval` makes
# (added on 2026-10-12, jms_kapps 100, interim name): no key of a configuration either.
PREFILL_SEED = "prefill_seed"

# The key of the spec's defaults that names the spec's series (added on 2026-10-14, jms_kapps 110,
# interim name): no key of a configuration either.
SERIES = "series"

# The key of the spec's defaults that gives the base seed of the pick seeds (added on 2026-10-12,
# jms_kapps 93, interim name): no key of a configuration, so no block and no flag may give it.
BASE_SEED = "pick_base_seed"

# The key of a block that lists the replay factors of its configurations (added on 2026-10-12,
# jms_kapps 98, interim name): no key of a configuration, so no default and no flag may give it.
REPLAY_FACTORS = "replay_factors"

# The tables of a spec.
_DEFAULTS = "defaults"
_SWEEP = "sweep"
_PRECISION = "precision"

# The key of a `[precision]` table that gave the sizes of the first passes, until the rounds
# replaced them (jms_kapps 111): a table that gives it is refused, naming the rounds.
PASS_SIZES = "pass_sizes"


@dataclass(frozen=True)
class PrecisionRule:
    """The settings of the precision rule that a spec's `[precision]` table gives (added on
    2026-10-12, jms_kapps 101, interim names).

    A configuration meets the rule when it has `minimum_runs` valid runs, the bootstrap 95 %
    interval of its pooled commit p95 lies within `latency_tolerance` of the p95 on both sides,
    and the IQR of its transports ended per minute is at most `throughput_spread` of their median.
    At most `maximum_runs` runs are made of it. The sweep works in rounds (`rounds`, jms_kapps
    111): round k makes repetition k of each configuration that is still open, so the last round
    is `maximum_runs`.
    """

    latency_tolerance: float = 0.10
    throughput_spread: float = 0.10
    minimum_runs: int = 10
    maximum_runs: int = 20


class SpecRefused(ValueError):
    """A spec cannot be swept: it is no TOML, misses a value, names a key a spec does not know, or
    a configuration of it cannot be run. The message names the value, for a person to read."""


@dataclass(frozen=True)
class Spec:
    """A spec as its file holds it: its defaults and its blocks, each a key with its values."""

    path: Path
    defaults: Mapping[str, Any]
    blocks: tuple[Mapping[str, list[Any]], ...]
    # The numbers of the blocks marked as a ramp, from 1.
    ramps: frozenset[int] = frozenset()
    # The replay factors of each block that lists them, by the block's number, each in rising order
    # (jms_kapps 98, interim name).
    replays: Mapping[int, tuple[float, ...]] = field(default_factory=dict)
    # The base seed the spec gives for the pick seeds, or None where the command's default applies.
    base_seed: int | None = None
    # The precision rule of the spec's `[precision]` table, or None for a spec of fixed
    # repetitions (added on 2026-10-12, jms_kapps 101, interim name).
    precision: PrecisionRule | None = None
    # The seed of the prefill files that `jms-eval` makes, or None for the seed 1 (jms_kapps 100,
    # interim name).
    prefill_seed: int | None = None
    # The series that the spec's defaults name, or None (jms_kapps 110, interim name).
    series_name: str | None = None

    @property
    def series(self) -> str:
        """The spec's series: the name its defaults give, else its file name without the
        extension (jms_kapps 110, interim name)."""
        return self.series_name if self.series_name is not None else self.path.stem


@dataclass(frozen=True)
class SpecConfiguration:
    """One configuration of a spec.

    `number` is its place among the spec's configurations, from 1, and `block` the block that
    lists it first, from 1. `values` holds every key of a configuration as the spec and the flags
    give it, the prefill's topology filled in; `key` is their digest. `ramp` is the number of the
    block of the ramp it is a level of, or None. `ruled` is whether it runs under the spec's
    precision rule (jms_kapps 101, interim name): then `repetitions` is the rule's maximum.
    `replay_factors` are the factors its blocks list for the store-only baseline's replay of it,
    in rising order, or none (jms_kapps 98, interim name); they are no part of its key.
    `spec_file` is the file name of the spec that lists it (jms_kapps 105, interim name): a record
    of the "before" series is matched to it only when its sweep names that file. `series` is the
    spec's series (jms_kapps 110, interim name), no part of the key, and `name` the number with
    it, as the readers and the sweep show a configuration.
    """

    number: int
    block: int
    values: Mapping[str, Any]
    repetitions: int
    key: str
    ramp: int | None = None
    ruled: bool = False
    replay_factors: tuple[float, ...] = ()
    spec_file: str = ""
    series: str = ""

    @property
    def name(self) -> str:
        """The configuration's number with its series, such as `before 7`: the number alone
        where it has no series (jms_kapps 110, interim name)."""
        return f"{self.series} {self.number}" if self.series else str(self.number)


def read_spec(path: Path) -> Spec:
    """The spec in the TOML file at `path`.

    Raises:
        SpecRefused: If the file cannot be read or is no TOML, has a table a spec does not know,
            or a key in its defaults or a block that a spec does not know, or an axis with no
            value.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SpecRefused(f"the spec {str(path)!r} cannot be read: {error}") from None
    except tomllib.TOMLDecodeError as error:
        raise SpecRefused(f"the spec {str(path)!r} is no TOML: {error}") from None
    unknown = sorted(set(data) - {_DEFAULTS, _SWEEP, _PRECISION})
    if unknown:
        raise SpecRefused(
            f"the spec has tables a spec does not know: {', '.join(unknown)}. A spec has "
            f"[{_DEFAULTS}] and its blocks, each [[{_SWEEP}]], and may have [{_PRECISION}]"
        )
    precision = _precision(data.get(_PRECISION))
    defaults = data.get(_DEFAULTS, {})
    if not isinstance(defaults, dict):
        raise SpecRefused(f"{_DEFAULTS} is a table, [{_DEFAULTS}]")
    defaults = dict(defaults)
    base_seed = _base_seed(defaults.pop(BASE_SEED, None))
    prefill_seed = _seed_of(defaults.pop(PREFILL_SEED, None), PREFILL_SEED)
    series_name = _series_of(defaults.pop(SERIES, None))
    _known(defaults, f"[{_DEFAULTS}]")
    blocks = data.get(_SWEEP)
    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        raise SpecRefused(
            f"the spec lists no sweep block: each block is a table [[{_SWEEP}]]"
        )
    if not blocks:
        raise SpecRefused(f"the spec lists no sweep block, [[{_SWEEP}]]")
    ramps = frozenset(
        number for number, block in enumerate(blocks, 1) if _is_ramp(block, number)
    )
    replays = {
        number: factors
        for number, block in enumerate(blocks, 1)
        if (factors := _replay_factors(block.get(REPLAY_FACTORS), number))
    }
    return Spec(
        path=path,
        defaults=dict(defaults),
        blocks=tuple(
            _axes(
                {k: v for k, v in block.items() if k not in (RAMP, REPLAY_FACTORS)},
                number,
            )
            for number, block in enumerate(blocks, 1)
        ),
        ramps=ramps,
        replays=replays,
        base_seed=base_seed,
        precision=precision,
        prefill_seed=prefill_seed,
        series_name=series_name,
    )


def setting(text: str) -> tuple[str, Any]:
    """The key and the value of a flag `KEY=VALUE`: the value as TOML reads it, a number, a
    boolean or a quoted text, or else the text as it is.

    Raises:
        SpecRefused: If `text` has no `=`.
    """
    key, equals, value = text.partition("=")
    if not equals or not key.strip():
        raise SpecRefused(f"a setting is KEY=VALUE, not {text!r}")
    try:
        return key.strip(), tomllib.loads(f"value = {value.strip()}")["value"]
    except tomllib.TOMLDecodeError:
        return key.strip(), value.strip()


def configurations(
    spec: Spec, *, overrides: Mapping[str, Any] | None = None
) -> list[SpecConfiguration]:
    """The configurations of `spec`, in its order, each with its number of repetitions.

    Args:
        overrides: The flags of the command, each a key with its value, over every value of the
            spec.

    Raises:
        SpecRefused: If a flag names a key a spec does not know; if a block, with the defaults
            and the flags, misses a value, naming the block and the value; if a configuration
            listed again has another number of repetitions; or if a ramp is not one: its rates
            do not rise, it has more than 1 repetition, it is not heavy or has no wrapper, or
            another block lists one of its configurations too.
    """
    overrides = dict(overrides or {})
    _known(overrides, "the flags")
    found: dict[str, SpecConfiguration] = {}
    for number, block in enumerate(spec.blocks, 1):
        ramp = number if number in spec.ramps else None
        if ramp is not None:
            _refuse_a_light_ramp(block, overrides, number)
        given = {**COMMAND_DEFAULTS, **spec.defaults, **block, **overrides}
        missing = [key for key in SPEC_KEYS if key not in given]
        if missing:
            raise SpecRefused(
                f"block {number} of the spec has no value for {', '.join(missing)}, and "
                f"neither have the spec's [{_DEFAULTS}] nor the flags. Every value but the "
                f"command's defaults ({', '.join(COMMAND_DEFAULTS)}) comes from the spec"
            )
        for values in _product(block):
            layered = {**COMMAND_DEFAULTS, **spec.defaults, **values, **overrides}
            if ramp is not None:
                layered["heavy"] = True
            repetitions = _repetitions(layered.pop(REPETITIONS), number)
            ruled = spec.precision is not None and ramp is None and not layered["heavy"]
            if spec.precision is not None and not ruled and repetitions != 1:
                raise SpecRefused(
                    f"block {number} of the spec has a [{_PRECISION}] table, and its heavy "
                    f"configuration has {REPETITIONS} = {repetitions} (from the block, the "
                    f"spec's [{_DEFAULTS}] or a flag): in the rounds a heavy configuration runs "
                    "one time, in round 1, and a person asks for each more repetition with "
                    "--repeat (decided by Sören on 2026-10-13, jms_kapps 114). Give it "
                    f"{REPETITIONS} = 1, or none"
                )
            if ruled:
                assert spec.precision is not None
                if any(
                    REPETITIONS in given_in
                    for given_in in (spec.defaults, values, overrides)
                ):
                    raise SpecRefused(
                        f"block {number} of the spec has a [{_PRECISION}] table and a fixed "
                        f"{REPETITIONS}: a configuration at normal load runs under the rule or "
                        f"a fixed number of times, and only a ramp or a heavy configuration "
                        f"keeps {REPETITIONS}"
                    )
                repetitions = spec.precision.maximum_runs
            configuration_values = {key: layered[key] for key in KEYS}
            configuration_values["prefill"] = _filled(
                configuration_values["prefill"], configuration_values["topology"]
            )
            key = _key(configuration_values)
            factors = spec.replays.get(number, ())
            if factors and configuration_values["wrapper"] is not True:
                raise SpecRefused(
                    f"block {number} of the spec lists {REPLAY_FACTORS}, and a replay sends the "
                    "store requests that a run's record keeps with the wrapper: the "
                    "configuration needs wrapper = true"
                )
            if key in found:
                first = found[key]
                if ramp is not None and first.block == number:
                    raise SpecRefused(
                        f"block {number} of the spec is a ramp, whose box rates rise: it "
                        f"lists {layered['boxes_per_minute']} twice"
                    )
                if ramp is not None or first.ramp is not None:
                    raise SpecRefused(
                        f"block {number} of the spec lists configuration {first.number} "
                        f"again, of block {first.block}: a ramp's configurations are its own"
                    )
                if first.repetitions != repetitions:
                    raise SpecRefused(
                        f"block {number} of the spec lists configuration {first.number} "
                        f"again with {repetitions} {REPETITIONS}, where block {first.block} "
                        f"gave it {first.repetitions}"
                    )
                if factors:
                    found[key] = replace(
                        first,
                        replay_factors=tuple(sorted({*first.replay_factors, *factors})),
                    )
                continue
            found[key] = SpecConfiguration(
                number=len(found) + 1,
                block=number,
                values=configuration_values,
                repetitions=repetitions,
                key=key,
                ramp=ramp,
                ruled=ruled,
                replay_factors=factors,
                spec_file=spec.path.name,
                series=spec.series,
            )
    configured = list(found.values())
    for number in sorted(spec.ramps):
        _refuse_a_malformed_ramp([c for c in configured if c.ramp == number], number)
    return configured


def ramps_of(
    found: Sequence[SpecConfiguration],
) -> dict[int, list[SpecConfiguration]]:
    """The ramps among `found`, each by the number of its block, with its levels in rising
    order of their box rates."""
    ramps: dict[int, list[SpecConfiguration]] = {}
    for configuration in found:
        if configuration.ramp is not None:
            ramps.setdefault(configuration.ramp, []).append(configuration)
    return {
        number: sorted(levels, key=lambda c: float(c.values["boxes_per_minute"]))
        for number, levels in ramps.items()
    }


def resolved(
    configuration: SpecConfiguration,
    *,
    spec: Spec,
    prefills: Path,
    module_strategies: Mapping[str, ModuleStrategy] | None = None,
    wms_strategies: Mapping[str, WmsStrategy] | None = None,
) -> Configuration:
    """The configuration of one run of `configuration`, resolved as a configuration file is
    (`configuration.resolve`): a topology file read from the spec's folder, and a prefill file
    from `prefills`.

    Raises:
        SpecRefused: If the configuration cannot be run, naming its number and the value.
    """
    values = dict(configuration.values)
    if isinstance(values["prefill"], str) and values["prefill"] != NO_PREFILL:
        # The prefills folder as the command was given it, from its working directory.
        folder = Path(prefills).expanduser().absolute()
        values["prefill"] = str(folder / Path(values["prefill"]).expanduser())
    try:
        return resolve(
            values,
            base=spec.path.resolve().parent,
            module_strategies=module_strategies,
            wms_strategies=wms_strategies,
        )
    except ConfigurationRefused as refused:
        raise SpecRefused(
            f"configuration {configuration.number} of the spec: {refused}"
        ) from None


def for_repetition(
    spec: Spec, configuration: Configuration, repetition: int
) -> Configuration:
    """`configuration`, resolved, as repetition `repetition` of it runs: with that repetition's
    pick seed, from the spec's base seed or the default. A sweep and `jms-eval-uc2` run from a
    spec both make it, so that one run is the same run from either (jms_kapps 96, interim)."""
    base_seed = DEFAULT_BASE_SEED if spec.base_seed is None else spec.base_seed
    return replace(configuration, wms_pick_seed=pick_seed(base_seed, repetition))


def sweep_fields(
    spec: Spec, configuration: SpecConfiguration, repetition: int
) -> dict[str, Any]:
    """What the record of a run made from a spec says of it: the spec, the configuration's number
    and key, and the repetition of how many. A sweep's run and a run of `jms-eval-uc2` made from a
    spec and the configuration's number record the same fields (jms_kapps 96, interim)."""
    return {
        "spec": str(spec.path),
        "configuration": configuration.number,
        "key": configuration.key,
        "repetition": repetition,
        "repetitions": configuration.repetitions,
    }


def _comparable(values: Mapping[str, Any]) -> dict[str, Any]:
    """The values of a configuration as a spec lists them and as a resolved configuration holds
    them, made comparable: a topology or prefill path by its file name (a resolved one is
    absolute), each number as a float."""
    comparable: dict[str, Any] = {}
    for key in KEYS:
        value = values[key]
        if key in ("topology", "prefill") and isinstance(value, str):
            value = Path(value).name
        elif isinstance(value, int | float) and not isinstance(value, bool):
            value = float(value)
        comparable[key] = value
    return comparable


def fingerprint(configuration: Configuration) -> str:
    """The key of a resolved configuration, computed as the spec computes keys (`_key`) over its
    values made comparable, the pick seed left out: the same for the spec's configuration and the
    resolved one it gives, and for the configuration text a record keeps (jms_kapps 96,
    interim)."""
    values = tomllib.loads(replace(configuration, wms_pick_seed=None).to_toml())
    return _key(_comparable(values))


def lists(listed: SpecConfiguration, configuration: Configuration) -> bool:
    """Whether `configuration`, resolved, is the configuration `listed` (jms_kapps 96, interim)."""
    return _key(_comparable(listed.values)) == fingerprint(configuration)


def numbers(text: str) -> list[int]:
    """The numbers that a selection names, such as `1-9,12`: numbers from 1 and ranges of them,
    separated by commas, in order and each once.

    Raises:
        SpecRefused: If a part is neither a number from 1 nor a range from a lower to a higher.
    """
    selected: set[int] = set()
    for part in text.split(","):
        first, dash, last = part.strip().partition("-")
        try:
            low = int(first)
            high = int(last) if dash else low
        except ValueError:
            raise SpecRefused(
                f"a selection is numbers from 1 and ranges of them, such as 1-9,12, not "
                f"{text!r}"
            ) from None
        if low < 1 or high < low:
            raise SpecRefused(
                f"a selection is numbers from 1 and ranges from a lower to a higher, not "
                f"{text!r}"
            )
        selected.update(range(low, high + 1))
    return sorted(selected)


def _base_seed(value: Any) -> int | None:
    """The base seed that the spec's defaults give: None where they give none.

    Raises:
        SpecRefused: If it is no whole number from 0.
    """
    return _seed_of(value, BASE_SEED)


def _series_of(value: Any) -> str | None:
    """The series that the spec's defaults name: None where they name none (jms_kapps 110).

    Raises:
        SpecRefused: If it is no text, or an empty one.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SpecRefused(
            f"[{_DEFAULTS}] {SERIES} names the spec's series, a text that is not empty, such as "
            f'"before", not {value!r}'
        )
    return value.strip()


def _seed_of(value: Any, key: str) -> int | None:
    """The seed that the spec's defaults give under `key`: None where they give none.

    Raises:
        SpecRefused: If it is no whole number from 0.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpecRefused(
            f"[{_DEFAULTS}] {key} is a whole number from 0, not {value!r}"
        )
    return value


def _precision(table: Any) -> PrecisionRule | None:
    """The rule that the `[precision]` table gives: None where the spec has no such table.

    Raises:
        SpecRefused: If it is no table, names a key the rule does not know, gives `pass_sizes`,
            which the rounds replaced (jms_kapps 111), or has a value out of range: the
            tolerances are numbers above 0, the minimum is at least 2, so that the valid runs
            come from at least two rounds, and at most the maximum.
    """
    if table is None:
        return None
    where = f"[{_PRECISION}]"
    if not isinstance(table, dict):
        raise SpecRefused(f"{_PRECISION} is a table, {where}")
    if PASS_SIZES in table:
        raise SpecRefused(
            f"{where} gives {PASS_SIZES}, and the passes are no more: the rounds replaced them "
            "(decided by Sören on 2026-10-13, jms_kapps 111). A call makes round after round, "
            "and round k makes repetition k of each configuration that does not meet the rule "
            f"and has fewer than maximum_runs runs. Remove {PASS_SIZES} from the spec"
        )
    known = (
        "latency_tolerance",
        "throughput_spread",
        "minimum_runs",
        "maximum_runs",
    )
    unknown = sorted(set(table) - set(known))
    if unknown:
        raise SpecRefused(
            f"{where} names keys the rule does not know: {', '.join(unknown)}. It knows "
            f"these: {', '.join(known)}"
        )
    defaults = PrecisionRule()
    for key in ("latency_tolerance", "throughput_spread"):
        value = table.get(key, getattr(defaults, key))
        if not _is_rate(value) or not 0 < value <= 1:
            raise SpecRefused(
                f"{where} {key} is a number above 0, at most 1, not {value!r}"
            )
    for key in ("minimum_runs", "maximum_runs"):
        value = table.get(key, getattr(defaults, key))
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SpecRefused(f"{where} {key} is a whole number from 1, not {value!r}")
    rule = PrecisionRule(
        latency_tolerance=float(
            table.get("latency_tolerance", defaults.latency_tolerance)
        ),
        throughput_spread=float(
            table.get("throughput_spread", defaults.throughput_spread)
        ),
        minimum_runs=table.get("minimum_runs", defaults.minimum_runs),
        maximum_runs=table.get("maximum_runs", defaults.maximum_runs),
    )
    if rule.minimum_runs > rule.maximum_runs:
        raise SpecRefused(
            f"{where} minimum_runs ({rule.minimum_runs}) is above maximum_runs "
            f"({rule.maximum_runs})"
        )
    if rule.minimum_runs < 2:
        raise SpecRefused(
            f"{where} minimum_runs ({rule.minimum_runs}) must be at least 2, so that the valid "
            "runs come from at least two time windows: a round makes one run of each "
            "configuration"
        )
    return rule


def valid_factor(value: Any) -> bool:
    """Whether `value` is a replay factor: a finite number from 1. Below 1 a replay would be slower
    than the recorded run, which is no load to find a limit with (jms_kapps 98, interim)."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 1
    )


def _replay_factors(value: Any, number: int) -> tuple[float, ...]:
    """The replay factors that block `number` lists, in rising order: none where it lists none.

    Raises:
        SpecRefused: If they are no list of numbers from 1, an empty list, or list one twice.
    """
    if value is None:
        return ()
    listed = value if isinstance(value, list) else [value]
    if not listed or not all(valid_factor(factor) for factor in listed):
        raise SpecRefused(
            f"block {number} of the spec: {REPLAY_FACTORS} is a list of numbers from 1, such as "
            f"[1, 2, 4], not {value!r}"
        )
    factors = tuple(sorted(float(factor) for factor in listed))
    if len(set(factors)) != len(factors):
        raise SpecRefused(
            f"block {number} of the spec lists a replay factor twice: {value!r}"
        )
    return factors


def _is_ramp(block: Mapping[str, Any], number: int) -> bool:
    """Whether `block`, the block `number`, is marked as a ramp; a ramp's only axis is its box
    rates.

    Raises:
        SpecRefused: If its marker is no boolean, or it is a ramp with another axis, or with no
            list of box rates.
    """
    marker = block.get(RAMP, False)
    if not isinstance(marker, bool):
        raise SpecRefused(
            f"block {number} of the spec: {RAMP} is true or false, not {marker!r}"
        )
    if not marker:
        return False
    rates = block.get("boxes_per_minute")
    if not isinstance(rates, list):
        raise SpecRefused(
            f"block {number} of the spec is a ramp, which lists its rising box rates: "
            "boxes_per_minute = [10, 20, 40, ...]"
        )
    other_axes = sorted(
        key
        for key, value in block.items()
        if key != "boxes_per_minute" and isinstance(value, list)
    )
    if other_axes:
        raise SpecRefused(
            f"block {number} of the spec is a ramp, whose only axis is boxes_per_minute; it "
            f"lists several values of {', '.join(other_axes)}. Write one ramp for each"
        )
    return True


def _refuse_a_light_ramp(
    block: Mapping[str, list[Any]], overrides: Mapping[str, Any], number: int
) -> None:
    """Refuse the ramp `block`, the block `number`, where it or a flag says that it is not heavy:
    a ramp's runs are heavy."""
    said = [
        *block.get("heavy", []),
        *([overrides["heavy"]] if "heavy" in overrides else []),
    ]
    if any(value is not True for value in said):
        raise SpecRefused(
            f"block {number} of the spec is a ramp, whose runs are heavy: it, or a flag, says "
            "heavy = false"
        )


def _refuse_a_malformed_ramp(levels: Sequence[SpecConfiguration], number: int) -> None:
    """Refuse the ramp of block `number`, with its `levels` in the block's order, unless its box
    rates rise, it has 1 repetition and it runs with the wrapper."""
    rates = [level.values["boxes_per_minute"] for level in levels]
    rising = all(
        _is_rate(rate) and (before is None or float(rate) > float(before))
        for before, rate in zip([None, *rates], rates, strict=False)
    )
    if len(rates) < 2 or not rising:
        raise SpecRefused(
            f"block {number} of the spec is a ramp, whose box rates rise, at least two of "
            f"them; it lists {', '.join(map(str, rates))}"
        )
    repetitions = {level.repetitions for level in levels}
    if repetitions != {1}:
        raise SpecRefused(
            f"block {number} of the spec is a ramp, which runs each rate once, "
            f"{REPETITIONS} = 1; it has {', '.join(map(str, sorted(repetitions)))}"
        )
    if any(level.values["wrapper"] is not True for level in levels):
        raise SpecRefused(
            f"block {number} of the spec is a ramp, which runs with the wrapper "
            "(wrapper = true): the broken rule reads the measures that need it"
        )


def _is_rate(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _known(values: Mapping[str, Any], where: str) -> None:
    unknown = sorted(set(values) - set(SPEC_KEYS))
    if unknown:
        raise SpecRefused(
            f"{where} names keys a spec does not know: {', '.join(unknown)}. A spec knows "
            f"these: {', '.join(SPEC_KEYS)}"
        )


def _axes(block: Mapping[str, Any], number: int) -> dict[str, list[Any]]:
    """The axes of a block, each key with its values: one value is a list of one. The block's
    ramp marker is not among them."""
    _known(block, f"block {number} of the spec")
    axes: dict[str, list[Any]] = {}
    for key, value in block.items():
        values = value if isinstance(value, list) else [value]
        if not values:
            raise SpecRefused(f"block {number} of the spec has no value for {key}: []")
        axes[key] = values
    return axes


def _product(block: Mapping[str, list[Any]]) -> list[dict[str, Any]]:
    """The cross product of a block's axes, the first key varying slowest."""
    return [
        dict(zip(block, values, strict=True))
        for values in itertools.product(*block.values())
    ]


def _repetitions(value: Any, block: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpecRefused(
            f"block {block} of the spec: {REPETITIONS} is a whole number from 1, not {value!r}"
        )
    return value


def _filled(prefill: Any, topology: Any) -> Any:
    """The prefill with its topology's name where it names `{topology}`."""
    if not isinstance(prefill, str) or TOPOLOGY_PLACEHOLDER not in prefill:
        return prefill
    return prefill.replace(TOPOLOGY_PLACEHOLDER, Path(str(topology)).stem)


# The keys that joined a configuration's key with the run settings of the spec (jms_kapps 93):
# the records of the "before" series were keyed without them.
LATER_KEYS = ("transit_seconds", "retry_seconds")


def legacy_key(configuration: SpecConfiguration) -> str:
    """The key that a record of the "before" series holds for `configuration` (jms_kapps 105,
    interim name): the digest of its values without the transit time and the retry wait."""
    return _key({k: v for k, v in configuration.values.items() if k not in LATER_KEYS})


def _key(values: Mapping[str, Any]) -> str:
    """The digest of a configuration's values, each number as a float."""
    canonical = {
        key: float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else value
        for key, value in values.items()
    }
    text = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
