"""The configuration of one UC2 run of the measurement harness, iteration one.

A configuration is a TOML file that names every value of one run, and nothing else:

```toml
topology = "cross"              # a shipped topology, or the path of a topology file
prefill = "none"                # the path of a prefill file in Turtle, or "none"
module_strategy = "real"        # the module strategy, by its entry-point name
wms_strategy = "real"           # the WMS strategy, by its entry-point name
shacl = true                    # the repository validates with the shapes (true) or is the twin without them (false)
collect_history = false         # the store collects history during the run
boxes_per_minute = 30           # the WMS's spawn rate during the warm-up and the window
warm_up_seconds = 60            # how long the spawn rate is on before the measured window; 0: none
window_seconds = 300            # how long the measured window is
drain_bound_seconds = 300       # how long the run waits after the window for every transport to end
matcher_interval_seconds = 2    # how often the completion matcher reads the graph
transit_seconds = 3.0           # the transit time of every module that has none of its own
retry_seconds = 1.0             # how long a module waits before it asks a Reserve again
wrapper = true                  # each participant runs through the wrapper, which observes it
heavy = false                   # a sustained-load or breaking-point run, which needs an approval
```

Every value must be given: a configuration has no defaults, so a record says each value the run
took. A missing value is refused by its name, and so is a key the run does not know, so that a
misspelt key or a secret never passes unseen. A relative path is read from the configuration
file's folder. A topology file, a prefill file or a strategy that cannot be found is refused here,
before the run creates a repository.

The resolved configuration has every path absolute. `Configuration.to_toml` writes it back in the
same form, and a record holds that text, so a record can be run again: read back and resolved, it
is the configuration the run started from. The values of iteration one come from the run task
(the box rate, the warm-up, the window, the drain bound and the matcher interval); none is built
into the harness.

The warm-up and the matcher interval belong to the sweep: the
spawn rate is on for `warm_up_seconds` before the measured window starts, and a warm-up of 0 is a
run without one. `matcher_interval_seconds` is how often the completion matcher reads the graph, from the load's
start to the drain's end (`matcher`).

`wrapper` belongs to the observation of a run: with it, each
participant runs through the wrapper, which observes it from outside its code (`wrapper`); without
it, the run has no spans, its record has the run kind `uc2-unwrapped`, and it holds only the
measures that need no wrapper.

`transit_seconds` and `retry_seconds` are run settings of the spec (interim names): the transit time of
every module that has none of its own in the topology, and how long a module waits before it asks
a Reserve again. Both are keys of the configuration, so a spec can sweep them and the key of a
configuration says them; their defaults, 3.0 s and 1.0 s, are the sweep command's (`spec`).

**The pick seed** is the one value a configuration may leave out, and it is no key: `wms_pick_seed`
is a whole number, a run setting that seeds the generator the WarehouseManagementSystem picks its
modules with. It is written after the keys, where it is given, and it does not change the
configuration's key (`spec`): all repetitions of one configuration keep one key. A run without one
takes repetition 1 with the default base seed (`pick_seed`, `DEFAULT_BASE_SEED`), and always
writes the seed it used to its record and to its saved configuration, so that a saved
configuration run again makes the same picks. (A configuration has no other default.)
"""

from __future__ import annotations

import hashlib
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jms_kapps.uc2.strategies import (
    ModuleStrategy,
    UnknownStrategy,
    WmsStrategy,
    chosen,
    found,
)
from jms_kapps.uc2.topology import TopologyRefused, load_topology, topology_names

# The keys of a configuration, in the order a resolved configuration writes them.
KEYS = (
    "topology",
    "prefill",
    "module_strategy",
    "wms_strategy",
    "shacl",
    "collect_history",
    "boxes_per_minute",
    "warm_up_seconds",
    "window_seconds",
    "drain_bound_seconds",
    "matcher_interval_seconds",
    "transit_seconds",
    "retry_seconds",
    "wrapper",
    "heavy",
)

# The run setting a configuration may leave out, and writes after its keys where it is given: the
# seed of the generator the WarehouseManagementSystem picks with (interim name).
PICK_SEED = "wms_pick_seed"

# The base seed of a sweep whose spec gives none (interim name): fixed, so that repetition k gets the same seed in every configuration, in every sweep.
DEFAULT_BASE_SEED = 20261012

# The value of `prefill` for a run without a prefill file. A file of that name is given as
# `./none`.
NO_PREFILL = "none"


class ConfigurationRefused(ValueError):
    """A configuration cannot be run: a value is missing, unknown or of the wrong kind, or a file
    or a strategy it names cannot be found. The message names the value, for a person to read."""


@dataclass(frozen=True)
class Configuration:
    """One resolved configuration: every value of one run.

    `topology` is a shipped topology's name, or the absolute path of a topology file. `prefill`
    is the absolute path of the prefill file, or None for a run without one.
    """

    topology: str
    prefill: Path | None
    module_strategy: str
    wms_strategy: str
    shacl: bool
    collect_history: bool
    boxes_per_minute: float
    warm_up_seconds: float
    window_seconds: float
    drain_bound_seconds: float
    matcher_interval_seconds: float
    transit_seconds: float
    retry_seconds: float
    wrapper: bool
    heavy: bool
    wms_pick_seed: int | None = None

    def to_toml(self) -> str:
        """This configuration as a configuration file holds it, one key a line, in the order of
        `KEYS`, then the pick seed where there is one."""
        values: dict[str, str | bool | float] = {
            "topology": self.topology,
            "prefill": NO_PREFILL if self.prefill is None else str(self.prefill),
            "module_strategy": self.module_strategy,
            "wms_strategy": self.wms_strategy,
            "shacl": self.shacl,
            "collect_history": self.collect_history,
            "boxes_per_minute": self.boxes_per_minute,
            "warm_up_seconds": self.warm_up_seconds,
            "window_seconds": self.window_seconds,
            "drain_bound_seconds": self.drain_bound_seconds,
            "matcher_interval_seconds": self.matcher_interval_seconds,
            "transit_seconds": self.transit_seconds,
            "retry_seconds": self.retry_seconds,
            "wrapper": self.wrapper,
            "heavy": self.heavy,
        }
        text = "".join(f"{key} = {_toml_value(values[key])}\n" for key in KEYS)
        if self.wms_pick_seed is not None:
            text += f"{PICK_SEED} = {self.wms_pick_seed}\n"
        return text


def read_configuration(
    path: Path,
    *,
    module_strategies: Mapping[str, ModuleStrategy] | None = None,
    wms_strategies: Mapping[str, WmsStrategy] | None = None,
) -> Configuration:
    """The configuration in the TOML file at `path`, resolved (`resolve`), with a relative path
    read from the file's folder.

    Raises:
        ConfigurationRefused: If the file cannot be read or is no TOML, or `resolve` refuses it.
    """
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigurationRefused(
            f"the configuration {str(path)!r} cannot be read: {error}"
        ) from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationRefused(
            f"the configuration {str(path)!r} is no TOML: {error}"
        ) from None
    return resolve(
        values,
        base=path.resolve().parent,
        module_strategies=module_strategies,
        wms_strategies=wms_strategies,
    )


def resolve(
    values: Mapping[str, Any],
    *,
    base: Path,
    module_strategies: Mapping[str, ModuleStrategy] | None = None,
    wms_strategies: Mapping[str, WmsStrategy] | None = None,
) -> Configuration:
    """The configuration that `values` give, as a TOML file holds them, with every path made
    absolute against `base`, and each file and strategy it names checked.

    Args:
        values: The keys of a configuration, each with its value.
        base: The folder a relative path is read from: the configuration file's.
        module_strategies: The module strategies a run may name, by name: those registered in
            their entry-point group if None (`strategies.found`).
        wms_strategies: The WMS strategies a run may name, by name: likewise.

    Raises:
        ConfigurationRefused: If a value is missing, unknown or of the wrong kind, if the topology
            or the prefill file cannot be found or read, or if no strategy of its kind has the
            name given, naming the value.
    """
    unknown = sorted(set(values) - {*KEYS, PICK_SEED})
    if unknown:
        raise ConfigurationRefused(
            f"the configuration has keys a run does not know: {', '.join(unknown)}. "
            f"A configuration has exactly these: {', '.join(KEYS)}, and may have {PICK_SEED}"
        )
    missing = [key for key in KEYS if key not in values]
    if missing:
        raise ConfigurationRefused(
            f"the configuration has no value for {', '.join(missing)}: every value of a run "
            "is given, none has a default"
        )
    configuration = Configuration(
        topology=_topology(_text(values, "topology"), base),
        prefill=_prefill(_text(values, "prefill"), base),
        module_strategy=_text(values, "module_strategy"),
        wms_strategy=_text(values, "wms_strategy"),
        shacl=_flag(values, "shacl"),
        collect_history=_flag(values, "collect_history"),
        boxes_per_minute=_positive(values, "boxes_per_minute"),
        warm_up_seconds=_not_negative(values, "warm_up_seconds"),
        window_seconds=_positive(values, "window_seconds"),
        drain_bound_seconds=_not_negative(values, "drain_bound_seconds"),
        matcher_interval_seconds=_positive(values, "matcher_interval_seconds"),
        transit_seconds=_not_negative(values, "transit_seconds"),
        retry_seconds=_positive(values, "retry_seconds"),
        wrapper=_flag(values, "wrapper"),
        heavy=_flag(values, "heavy"),
        wms_pick_seed=_seed(values) if PICK_SEED in values else None,
    )
    try:
        chosen(
            ModuleStrategy,
            configuration.module_strategy,
            found(ModuleStrategy) if module_strategies is None else module_strategies,
        )
    except UnknownStrategy as unknown:
        raise ConfigurationRefused(f"module_strategy: {unknown}") from None
    try:
        chosen(
            WmsStrategy,
            configuration.wms_strategy,
            found(WmsStrategy) if wms_strategies is None else wms_strategies,
        )
    except UnknownStrategy as unknown:
        raise ConfigurationRefused(f"wms_strategy: {unknown}") from None
    return configuration


def pick_seed(base_seed: int, repetition: int) -> int:
    """The seed of repetition `repetition`, from 1, of a sweep with the base seed `base_seed`:
    the same for every configuration, so that the repetitions of two configurations stay paired.
    A whole number of 63 bits that a configuration file and a command line hold, derived by a
    digest that Python keeps the same across its versions and hosts."""
    digest = hashlib.sha256(f"{base_seed}/{repetition}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def _seed(values: Mapping[str, Any]) -> int:
    value = values[PICK_SEED]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationRefused(
            f"{PICK_SEED} is a whole number from 0, not {value!r}"
        )
    return value


def _text(values: Mapping[str, Any], key: str) -> str:
    value = values[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationRefused(f"{key} is a text that is not empty, not {value!r}")
    return value


def _flag(values: Mapping[str, Any], key: str) -> bool:
    value = values[key]
    if not isinstance(value, bool):
        raise ConfigurationRefused(f"{key} is true or false, not {value!r}")
    return value


def _number(values: Mapping[str, Any], key: str) -> float:
    value = values[key]
    # A TOML boolean reads as a Python bool, which is an int too.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationRefused(f"{key} is a number, not {value!r}")
    if not math.isfinite(value):
        raise ConfigurationRefused(f"{key} is a finite number, not {value!r}")
    return float(value)


def _positive(values: Mapping[str, Any], key: str) -> float:
    value = _number(values, key)
    if value <= 0:
        raise ConfigurationRefused(f"{key} is more than 0, not {value:g}")
    return value


def _not_negative(values: Mapping[str, Any], key: str) -> float:
    value = _number(values, key)
    if value < 0:
        raise ConfigurationRefused(f"{key} is 0 or more, not {value:g}")
    return value


def _topology(value: str, base: Path) -> str:
    """A shipped topology's name as it is, or else the absolute path of the topology file, once
    the topology loads. A shipped name always means the shipped topology, as it does for
    `jms-uc2 --topology`, and anything else is a path."""
    resolved = value if value in topology_names() else str(_absolute(value, base))
    try:
        load_topology(resolved)
    except TopologyRefused as refused:
        raise ConfigurationRefused(f"topology {value!r}: {refused}") from None
    return resolved


def _prefill(value: str, base: Path) -> Path | None:
    if value == NO_PREFILL:
        return None
    path = _absolute(value, base)
    if not path.is_file():
        raise ConfigurationRefused(
            f"prefill {value!r}: there is no file at {str(path)!r}. "
            f'A run without a prefill file gives prefill = "{NO_PREFILL}"'
        )
    return path


def _absolute(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _toml_value(value: str | bool | float) -> str:
    """`value` as TOML writes it: a boolean, a float, or a basic string with every character
    that TOML does not take as it is escaped."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    escaped = []
    for character in value:
        if character in '"\\':
            escaped.append("\\" + character)
        elif (ord(character) < 0x20 and character != "\t") or ord(character) == 0x7F:
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'
