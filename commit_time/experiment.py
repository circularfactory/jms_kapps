"""The commit time experiment: one command runs use case 2 on the reference implementation with
SHACL on and off on two stores, replays each SHACL-on run, and leaves one folder per store, then
makes the figures.

    uv run python commit_time/experiment.py --debug
    TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python commit_time/experiment.py

Read `main` top to bottom: it calls one small function per step, in order, and holds the only
loops (over the stores, the repetitions, the heartbeat levels and the SHACL levels in the
repetition's order). Every value the run uses is in
the two parameter blocks below: `FULL_RUN`, the values of the paper's run, and `DEBUG_RUN`, the
small numbers that `--debug` chooses. `--stores` and `--repetitions` override them for a smoke
run, and the script prints what it runs with.

The script measures nothing itself. Each step calls the measurement harness
(`jms_kapps.commit_time`), which owns the runs, the records and the statistics.

On a store that is not on loopback (the remote store) the script starts only when the command sets
`TRIPLESTORE_ALLOW_DESTRUCTIVE=1`; it never sets it itself. Every repository it uses is the
harness's own `commit-time-<run id>`, created and deleted by the harness.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from kapps_semantic_middleware.seeding import is_disposable_target

from jms_kapps.commit_time import (
    baseline,
    harness,
    packing,
    prefill,
    record,
    results,
    run_uc2,
    spec,
    wrapper,
)
from jms_kapps.commit_time.commit_table import (
    KAPPS_SHACL_OFF,
    KAPPS_SHACL_ON,
    WRITES_ONLY,
)
from jms_kapps.commit_time.configuration import (
    DEFAULT_BASE_SEED,
    PICK_SEED,
    Configuration,
    ConfigurationRefused,
    pick_seed,
    resolve,
)
from jms_kapps.commit_time.store import GraphDBServer


@dataclass(frozen=True)
class Parameters:
    """Every value one invocation runs with. The two blocks below fill it in."""

    topology: str  # a shipped topology of use case 2
    module_strategy: str
    wms_strategy: str
    preload_statements: int  # the size of the preload file, in statements
    preload_seed: int  # the seed of the preload generator
    boxes_per_minute: float  # the rate at which the WMS spawns boxes
    transit_seconds: float  # a module's transit time
    retry_seconds: float  # a module's wait before it tries a refused handover again
    warm_up_seconds: float  # not measured
    window_seconds: float  # the measured window
    drain_bound_seconds: float  # the longest wait for the boxes after the window
    matcher_interval_seconds: float  # how often the completion matcher reads the graph
    wrapper: bool  # the spans of every commit and store request; the replay needs them
    collect_history: bool
    repetitions: int  # of every configuration; repetition k takes the pick seed of k
    shacl_levels: tuple[bool, ...]  # the spec's two KAPPS configurations, in this order
    alternate_order: (
        bool  # an even repetition runs the SHACL levels in the reverse order
    )
    heartbeat_levels: tuple[str, ...]  # one runs folder per level and store
    heartbeat_interval_seconds: dict[str, float | None]  # None: no heartbeat loop
    live_heartbeat_age_seconds: dict[
        str, float
    ]  # how old a live service's heartbeat may be
    stores: tuple[str, ...]  # run one after the other, in this order
    local_heap: (
        str  # the local GraphDB's heap (`compose.yaml`, `JMS_KAPPS_GRAPHDB_HEAP`)
    )


# --- The parameters -------------------------------------------------------------------------------

# The paper's run: the centre of iteration one, on both stores, heartbeats on only (the paper
# shows no heartbeats-off arm), with more repetitions in an alternating order instead.
FULL_RUN = Parameters(
    topology="grid",  # the shipped 30-module grid
    module_strategy="real",
    wms_strategy="roundrobin",  # the entry modules in turn; `real` saturates the grid
    preload_statements=1_000_000,
    preload_seed=1,
    boxes_per_minute=30,
    transit_seconds=3.0,
    retry_seconds=1.0,
    warm_up_seconds=60,
    window_seconds=300,
    drain_bound_seconds=300,
    matcher_interval_seconds=2,
    wrapper=True,
    collect_history=False,
    repetitions=14,
    shacl_levels=(True, False),
    alternate_order=True,  # odd: SHACL on first, even: SHACL off first (7 : 7 over 14)
    heartbeat_levels=("on",),
    heartbeat_interval_seconds={"on": 30.0, "off": None},
    live_heartbeat_age_seconds={"on": 90.0, "off": 3600.0},
    stores=("kit", "local"),
    local_heap="12g",  # the remote store's heap
)

# `--debug`: the same steps with small numbers, on the local store, in minutes, with both
# heartbeat levels, so that the heartbeats-off setting stays tried.
DEBUG_RUN = Parameters(
    topology="cross",  # the shipped 5-module cross
    module_strategy="real",
    wms_strategy="real",
    preload_statements=12,  # one box: the smallest preload the generator makes
    preload_seed=1,
    # The centre module passes fewer than about 20 boxes a minute: at 6, one box every 10 s,
    # about one at a time, so no spawn is refused.
    boxes_per_minute=6,
    transit_seconds=3.0,
    retry_seconds=1.0,
    warm_up_seconds=5,
    window_seconds=30,  # about three spawns
    drain_bound_seconds=60,
    matcher_interval_seconds=2,
    wrapper=True,
    collect_history=False,
    repetitions=1,
    shacl_levels=(True, False),
    alternate_order=True,
    heartbeat_levels=("on", "off"),
    heartbeat_interval_seconds={"on": 30.0, "off": None},
    live_heartbeat_age_seconds={"on": 90.0, "off": 3600.0},
    stores=("local",),
    local_heap="12g",
)

# The three arms, as the figures name them.
ARMS = (KAPPS_SHACL_ON, KAPPS_SHACL_OFF, WRITES_ONLY)

# The writes-only replay runs on the recorded timeline: factor 1, normal load.
REPLAY_FACTOR = 1.0

# How long one KAPPS run and one writes-only replay take at FULL_RUN's values, in minutes, per
# store, from recorded runs at these values (start, warm-up, window, drain and end). The
# estimate the script prints comes from these.
MINUTES_PER_RUN = {"kit": 7.5, "local": 7.1}
MINUTES_PER_REPLAY = {"kit": 6.2, "local": 5.8}

# The two stores: the remote store (`kit`) is the one the environment's
# TRIPLESTORE_* variables name; the local one is the GraphDB of `compose.yaml`, which the harness
# starts.
KIT = "kit"
LOCAL = "local"
KNOWN_STORES = (KIT, LOCAL)

# Where the output and the preload files go (both git-ignored).
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PRELOADS = HERE / "prefills"
FIGURE_SCRIPT = HERE / "figure.py"
# The release configuration, whose hygiene checks name the strings the packing replaces. It is
# in the development repository only, so a run elsewhere is packed without replacements.
RELEASE_CONFIG = HERE.parent / "release" / "config.toml"
# A run cannot name the release that will publish it; `pack.py --release` names it later.
UNRELEASED = "<the tag of the release that publishes this run>"

# The flag a person sets on the command for a store that is not on loopback.
DESTRUCTIVE_FLAG = "TRIPLESTORE_ALLOW_DESTRUCTIVE"

# The variable that sets the local GraphDB's heap in `compose.yaml`.
LOCAL_HEAP_VARIABLE = "JMS_KAPPS_GRAPHDB_HEAP"

# The connection variables of a store.
STORE_VARIABLES = ("TRIPLESTORE_URL", "TRIPLESTORE_USERNAME", "TRIPLESTORE_PASSWORD")

# The spec of the two KAPPS configurations, filled in from the parameters, beside the output: each
# run's record names it, with its configuration's number and key (`spec.sweep_fields`).
SPEC_TEMPLATE = """\
# The two KAPPS configurations of the commit time experiment, written by experiment.py from its
# parameters. Configuration 1 has SHACL on, configuration 2 SHACL off; the heartbeat level is a run
# setting, no key, so each level has a runs folder of its own.
[defaults]
series = "commit-time"
topology = "{topology}"
prefill = "{prefill}"
module_strategy = "{module_strategy}"
wms_strategy = "{wms_strategy}"
collect_history = {collect_history}
boxes_per_minute = {boxes_per_minute}
warm_up_seconds = {warm_up_seconds}
window_seconds = {window_seconds}
drain_bound_seconds = {drain_bound_seconds}
matcher_interval_seconds = {matcher_interval_seconds}
transit_seconds = {transit_seconds}
retry_seconds = {retry_seconds}
wrapper = {wrapper}
heavy = false
repetitions = {repetitions}

[[sweep]]
shacl = [{shacl_levels}]
"""


class Refused(Exception):
    """The experiment cannot start: the message is why, for a person to read. Nothing ran."""


# --- The steps, in the order main calls them -------------------------------------------------------


def read_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--debug", action="store_true", help="the small numbers of DEBUG_RUN"
    )
    parser.add_argument(
        "--stores",
        default=None,
        help="the stores to run, in order, comma-separated: kit, local or both",
    )
    parser.add_argument(
        "--repetitions", type=int, default=None, help="the repetitions, for a smoke run"
    )
    return parser.parse_args(argv)


def choose_parameters(arguments: argparse.Namespace) -> Parameters:
    """The block the command chooses, with its overrides; prints both loudly."""
    parameters = DEBUG_RUN if arguments.debug else FULL_RUN
    print(f"== Parameters: the {'DEBUG_RUN' if arguments.debug else 'FULL_RUN'} block")
    if arguments.stores is not None:
        stores = tuple(arguments.stores.split(","))
        for store in stores:
            if store not in KNOWN_STORES:
                raise Refused(f"--stores names {store!r}; a store is kit or local")
        parameters = replace(parameters, stores=stores)
        print(f"   OVERRIDE --stores: {', '.join(stores)}")
    if arguments.repetitions is not None:
        if arguments.repetitions < 1:
            raise Refused("--repetitions is a whole number from 1")
        parameters = replace(parameters, repetitions=arguments.repetitions)
        print(
            f"   OVERRIDE --repetitions: {arguments.repetitions} of each configuration"
        )
    return parameters


def shacl_order(parameters: Parameters, repetition: int) -> tuple[bool, ...]:
    """The SHACL levels of one repetition, in the order they run: the parameters' order, and in
    an even repetition the reverse when the order alternates, so that a slow drift of the store or
    the client adds to both arms alike."""
    if parameters.alternate_order and repetition % 2 == 0:
        return tuple(reversed(parameters.shacl_levels))
    return parameters.shacl_levels


def repetition_orders(parameters: Parameters) -> dict[str, list[str]]:
    """Each repetition's arms in the order they run, by the repetition's number: the writes-only
    replay right after its SHACL-on run."""
    orders = {}
    for repetition in range(1, parameters.repetitions + 1):
        arms = []
        for shacl in shacl_order(parameters, repetition):
            arms.append(KAPPS_SHACL_ON if shacl else KAPPS_SHACL_OFF)
            if shacl:
                arms.append(WRITES_ONLY)
        orders[str(repetition)] = arms
    return orders


def estimate_duration(parameters: Parameters, arguments: argparse.Namespace) -> None:
    """Print how long each store takes, from the measured lengths of one run and one replay at
    FULL_RUN's values. The debug block's runs are shorter and get no estimate."""
    if arguments.debug:
        print("== Duration: no estimate, the measured run lengths are FULL_RUN's")
        return
    levels = len(parameters.heartbeat_levels)
    runs = parameters.repetitions * levels * len(parameters.shacl_levels)
    replays = parameters.repetitions * levels * parameters.shacl_levels.count(True)
    total = 0.0
    for store in parameters.stores:
        hours = (
            runs * MINUTES_PER_RUN[store] + replays * MINUTES_PER_REPLAY[store]
        ) / 60
        total += hours
        print(
            f"== Duration: {store}, {runs} runs and {replays} replays, about {hours:.1f} h"
        )
    print(f"   all stores, one after the other: about {total:.1f} h")


def read_kit_connection() -> dict[str, str] | None:
    """The remote store's connection variables, read once at the start and kept: the local store
    sets its own, and the remote store after it gets these back. None where the environment sets no
    store."""
    harness.use_deprecated_variables()
    if not os.environ.get("TRIPLESTORE_URL"):
        return None
    kit = {}
    for name in STORE_VARIABLES:
        kit[name] = os.environ.get(name, "")
    print(f"== The remote store: {kit['TRIPLESTORE_URL']} (from the environment, kept)")
    return kit


def refuse_what_cannot_run(parameters: Parameters, kit: dict[str, str] | None) -> None:
    """Refuse, before anything runs, a remote store the environment does not name, a store that is
    not on loopback without the destructive flag on the command, and a run without the wrapper's
    modules. The middleware's own test says what is on loopback."""
    if KIT in parameters.stores:
        if kit is None:
            raise Refused(
                "the stores include kit, and no store is configured: set TRIPLESTORE_URL, "
                "TRIPLESTORE_USERNAME and TRIPLESTORE_PASSWORD, or run --stores local"
            )
        url = kit["TRIPLESTORE_URL"]
        if not is_disposable_target(url) and os.environ.get(DESTRUCTIVE_FLAG) != "1":
            raise Refused(
                f"the remote store {url} is not on loopback: the harness creates and deletes "
                f"its own commit-time- repositories there, so start the command with "
                f"{DESTRUCTIVE_FLAG}=1 set for it. The script never sets it itself"
            )
    wrapper.available()
    print("== Checks: every store may run, the wrapper's modules are installed")


def make_output_folder() -> Path:
    """One folder per invocation, named by its start time."""
    output = RESULTS / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    print(f"== Output folder: {output}")
    return output


def preload_file(parameters: Parameters) -> Path:
    """The preload file's path: the topology and the size in its name."""
    return PRELOADS / f"{parameters.topology}-{parameters.preload_statements}.ttl"


def write_parameters(
    output: Path, parameters: Parameters, arguments: argparse.Namespace
) -> Path:
    """Write `parameters.json` (every parameter, the overrides, the git commit and the package
    versions) and the spec of the two KAPPS configurations; print both. Returns the spec's path,
    which each run's record names."""
    print("== Write the parameters")
    software = record.software_facts()
    written = {
        "block": "DEBUG_RUN" if arguments.debug else "FULL_RUN",
        "parameters": asdict(parameters),
        "overrides": {
            "stores": arguments.stores,
            "repetitions": arguments.repetitions,
        },
        "arms": list(ARMS),
        "repetition_orders": repetition_orders(parameters),
        "replay_factor": REPLAY_FACTOR,
        "preload_file": str(preload_file(parameters)),
        "software": software,
    }
    for name, value in asdict(parameters).items():
        print(f"   {name} = {value}")
    for repetition, arms in repetition_orders(parameters).items():
        print(f"   repetition {repetition}: {', '.join(arms)}")
    print(f"   jms_kapps commit: {software['jms-kapps'].get('commit')}")
    path = output / "parameters.json"
    path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")
    print(f"   wrote {path}")

    shacl_levels = []
    for level in parameters.shacl_levels:
        shacl_levels.append("true" if level else "false")
    text = SPEC_TEMPLATE.format(
        topology=parameters.topology,
        prefill=preload_file(parameters).name,
        module_strategy=parameters.module_strategy,
        wms_strategy=parameters.wms_strategy,
        collect_history="true" if parameters.collect_history else "false",
        boxes_per_minute=parameters.boxes_per_minute,
        warm_up_seconds=parameters.warm_up_seconds,
        window_seconds=parameters.window_seconds,
        drain_bound_seconds=parameters.drain_bound_seconds,
        matcher_interval_seconds=parameters.matcher_interval_seconds,
        transit_seconds=parameters.transit_seconds,
        retry_seconds=parameters.retry_seconds,
        wrapper="true" if parameters.wrapper else "false",
        repetitions=parameters.repetitions,
        shacl_levels=", ".join(shacl_levels),
    )
    spec_path = output / "commit_time.toml"
    spec_path.write_text(text, encoding="utf-8")
    print(f"   wrote the spec {spec_path}:")
    print(text)
    return spec_path


@dataclass(frozen=True)
class KappsConfiguration:
    """One of the two KAPPS configurations, as the spec numbers it: its number from 1, its values
    (the prefill by its file name) and its key, the digest of the values."""

    number: int
    values: dict[str, Any]
    key: str


def kapps_configurations(
    parameters: Parameters,
) -> dict[bool, KappsConfiguration]:
    """The two KAPPS configurations, by their SHACL level, numbered in the order of the
    parameters' SHACL levels, as the spec lists them."""
    found = {}
    for number, shacl in enumerate(parameters.shacl_levels, 1):
        values = {
            "topology": parameters.topology,
            "prefill": preload_file(parameters).name,
            "module_strategy": parameters.module_strategy,
            "wms_strategy": parameters.wms_strategy,
            "shacl": shacl,
            "collect_history": parameters.collect_history,
            "boxes_per_minute": parameters.boxes_per_minute,
            "warm_up_seconds": parameters.warm_up_seconds,
            "window_seconds": parameters.window_seconds,
            "drain_bound_seconds": parameters.drain_bound_seconds,
            "matcher_interval_seconds": parameters.matcher_interval_seconds,
            "transit_seconds": parameters.transit_seconds,
            "retry_seconds": parameters.retry_seconds,
            "wrapper": parameters.wrapper,
            "heavy": False,
        }
        found[shacl] = KappsConfiguration(number, values, spec.key(values))
    print(
        "== Configurations: "
        + ", ".join(
            f"{c.number} = SHACL {'on' if shacl else 'off'} (key {c.key})"
            for shacl, c in found.items()
        )
    )
    return found


def configuration_to_run(
    configuration: KappsConfiguration, spec_path: Path, repetition: int
) -> Configuration:
    """`configuration` as repetition `repetition` of it runs: the preload file by its path, and
    the pick seed of that repetition."""
    values = dict(configuration.values)
    values["prefill"] = str(PRELOADS.expanduser().absolute() / values["prefill"])
    values[PICK_SEED] = pick_seed(DEFAULT_BASE_SEED, repetition)
    return resolve(values, base=spec_path.resolve().parent)


def make_preload(parameters: Parameters) -> None:
    """The preload file, made by the harness's prefill generator; its digest printed."""
    path = preload_file(parameters)
    print(
        f"== Make the preload: {parameters.topology}, {parameters.preload_statements} "
        f"statements, seed {parameters.preload_seed}, into {path}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    made = prefill.write_prefill(
        path,
        topology=parameters.topology,
        size=parameters.preload_statements,
        seed=parameters.preload_seed,
    )
    facts = record.prefill_facts(path) or {}
    print(f"   {made.boxes} boxes, SHA-256 {facts.get('sha256')}")


def choose_store(
    store: str, kit: dict[str, str] | None, parameters: Parameters
) -> None:
    """Point this process's TRIPLESTORE_* variables at `store`: the kept variables of the remote
    store, or the local GraphDB, started as the harness starts it (`docker compose up -d --wait`,
    with the heap of the parameters). Prints the store's version, edition and heap."""
    print(f"== Choose the store: {store}")
    if store == KIT:
        assert kit is not None
        os.environ.update(kit)
    else:
        os.environ[LOCAL_HEAP_VARIABLE] = parameters.local_heap
        print(
            f"   starting the local GraphDB with {LOCAL_HEAP_VARIABLE}={parameters.local_heap}"
        )
        harness.start_local_store()
    server = GraphDBServer(
        os.environ["TRIPLESTORE_URL"],
        username=os.environ["TRIPLESTORE_USERNAME"],
        password=os.environ["TRIPLESTORE_PASSWORD"],
    )
    try:
        version = server.version().get("answer") or {}
        resources = server.resources().get("answer") or {}
    finally:
        server.close()
    heap = (resources.get("heapMemoryUsage") or {}).get("max")
    print(
        f"   {os.environ['TRIPLESTORE_URL']}: GraphDB {version.get('productVersion')}, "
        f"{version.get('productType')}, heap "
        + ("not read" if heap is None else f"{heap / 1024**3:.1f} GiB")
    )


def run_kapps(
    spec_path: Path,
    configuration: KappsConfiguration,
    repetition: int,
    heartbeats: str,
    store_folder: Path,
    parameters: Parameters,
) -> dict[str, Any]:
    """One KAPPS run: the harness's one run, which creates the repository, seeds and preloads it,
    starts the participants, runs the warm-up and the window, drains, stops, deletes the
    repository and records (the record's `actions` are the trace). Prints what it measured;
    returns the run's index entry."""
    arm = KAPPS_SHACL_ON if configuration.values["shacl"] else KAPPS_SHACL_OFF
    interval = parameters.heartbeat_interval_seconds[heartbeats]
    age = parameters.live_heartbeat_age_seconds[heartbeats]
    runs = store_folder / f"heartbeats-{heartbeats}" / "runs"
    print(
        f"== Run KAPPS: {arm}, heartbeats {heartbeats} (interval {interval}, live age {age}), "
        f"repetition {repetition}, into {runs}"
    )
    made = run_uc2.run_on_the_configured_store(
        configuration_to_run(configuration, spec_path, repetition),
        runs=runs,
        sweep=spec.sweep_fields(
            spec_path,
            number=configuration.number,
            key=configuration.key,
            repetition=repetition,
            repetitions=parameters.repetitions,
        ),
        heartbeat_interval_seconds=interval,
        live_heartbeat_age_seconds=age,
    )
    facts = results.run_facts(made)
    print(
        f"   run {made['run_id']} on {made.get('repository')}: {made['outcome']}, valid {made.get('valid')}"
    )
    print(f"   admitted commits of the window: {facts['commits_admitted_in_window']}")
    print(f"   not admitted: {facts['commits_not_admitted_in_window']}")
    print(f"   refusals of the window: {facts['refusals_in_window']}")
    print(
        f"   spawns of the window: {facts['spawns_accepted_in_window']} accepted of "
        f"{facts['spawns_offered_in_window']} offered"
    )
    print(f"   client CPU median: {facts['client_cpu_median_percent']} %")
    return results.index_entry(
        made,
        folder=runs / str(made["run_id"]),
        store_folder=store_folder,
        arm=arm,
        heartbeats=heartbeats,
        repetition=repetition,
    )


def extract_writes(
    run: dict[str, Any], store_folder: Path
) -> baseline.RecordedRun | None:
    """The writes-only reading of the run's record; None, with why, where it cannot be replayed."""
    folder = store_folder / str(run["folder"])
    print(f"== Extract the writes: {run['run_id']} ({folder})")
    try:
        recorded = baseline.read_recorded_run(folder, writes_only=True)
    except (baseline.BaselineRefused, ConfigurationRefused) as refused:
        print(f"   no replay: {refused}")
        return None
    print(
        f"   {len(recorded.before)} writes before the window, {len(recorded.window)} in it, "
        f"{recorded.after} after it; {recorded.reads} reads and {recorded.logins} logins left out"
    )
    return recorded


def replay_writes(
    recorded: baseline.RecordedRun,
    spec_path: Path,
    configuration: KappsConfiguration,
    run: dict[str, Any],
    store_folder: Path,
) -> dict[str, Any]:
    """The writes-only replay of the run: its writes alone, at their recorded times, straight to
    a fresh repository with the same preload. Returns the replay's index entry."""
    runs = store_folder / f"heartbeats-{run['heartbeats']}" / "runs"
    print(
        f"== Replay the writes: {run['run_id']} at the factor {REPLAY_FACTOR:g}, into {runs}"
    )
    made = baseline.run_on_the_configured_store(
        recorded,
        runs=runs,
        factor=REPLAY_FACTOR,
        sweep=spec.replay_fields(
            spec_path,
            number=configuration.number,
            key=configuration.key,
            factor=REPLAY_FACTOR,
            repetition=run["repetition"],
        ),
        writes_only=True,
    )
    print(f"   replay {made['run_id']} on {made.get('repository')}: {made['outcome']}")
    return results.index_entry(
        made,
        folder=runs / str(made["run_id"]),
        store_folder=store_folder,
        arm=WRITES_ONLY,
        heartbeats=run["heartbeats"],
        repetition=run["repetition"],
    )


def compare_outcomes(replay: dict[str, Any], store_folder: Path) -> None:
    """Print the replay's outcome check: a mismatch makes the replay invalid, and the script goes
    on."""
    folder = store_folder / str(replay["folder"])
    made = json.loads((folder / "record.json").read_text(encoding="utf-8"))
    facts = results.replay_facts(made)
    print(
        f"== Compare the outcomes: {replay['run_id']}, {facts['writes_before_the_window']} "
        f"writes before the window, {facts['writes_in_the_window']} in it, "
        f"{facts['verified_receipts']} receipts verified, {facts['late_writes']} late, "
        f"{facts['overtaking_writes']} overtaking"
    )
    print(
        f"   outcome mismatches: {facts['mismatches']} ({facts['not_comparable']} not comparable)"
    )
    for write in facts["mismatched_writes"]:
        print(f"   MISMATCH: {write}")


def write_store_results(
    store: str, store_folder: Path, index: list[dict[str, Any]]
) -> None:
    """The store's index of runs and its results page."""
    print(f"== Hand over: {store_folder}")
    path = results.write_index(store_folder, index)
    print(f"   wrote the index {path}")
    as_json, as_markdown = results.write_results(
        store_folder, store=store, entries=index
    )
    print(f"   wrote the results page {as_markdown} and {as_json}")


def load_figure_script() -> ModuleType:
    """The figure script beside this one, loaded from its file, so that a debugger steps into
    it."""
    found = importlib.util.spec_from_file_location("commit_time_figure", FIGURE_SCRIPT)
    assert found is not None and found.loader is not None
    module = importlib.util.module_from_spec(found)
    # A dataclass looks its module up by name while it is made.
    sys.modules[found.name] = module
    found.loader.exec_module(module)
    return module


def make_figures(store_folders: list[Path], output: Path) -> None:
    """The commit time figure script on the store folders."""
    figures = output / "figures"
    print(
        f"== Make the figures: of {', '.join(str(f) for f in store_folders)}, into {figures}"
    )
    figure_script = load_figure_script()
    figure_script.main(
        [*(str(folder) for folder in store_folders), "--output", str(figures)]
    )


def pack_the_run(output: Path) -> bool:
    """The run packed as the published repository holds it (`jms_kapps.commit_time.packing`):
    the raw-data part and the reports part, into `<output>-packed/` beside the run's folder.
    False when the packing refuses the run, which it prints."""
    packed = output.parent / f"{output.name}-packed"
    print(f"== Pack the run: into {packed}")
    config = RELEASE_CONFIG if RELEASE_CONFIG.is_file() else None
    if config is None:
        print(f"   no {RELEASE_CONFIG}: no string is replaced")
    try:
        manifest = packing.pack(
            output,
            packed,
            release=UNRELEASED,
            banned=packing.banned_strings(config),
            draw_figures=packing.figure_drawer(load_figure_script().main),
        )
    except packing.Refused as refused:
        print(f"   REFUSED: {refused}")
        return False
    print(
        f"   {len(manifest['archives'])} archives of {len(manifest['runs'])} runs, "
        f"{sum(r['count'] for r in manifest['replacements'])} replacements"
    )
    return True


def say_the_end(store_folders: list[Path]) -> int:
    """The last lines: per store, the runs that did not complete, the replays with a mismatch and
    the runs with a refused spawn or without a spawn count, from the results pages. 0 when there
    are none, else 1."""
    print("== The end")
    problems = 0
    for store_folder in store_folders:
        facts = json.loads(
            (store_folder / results.RESULTS_JSON).read_text(encoding="utf-8")
        )
        not_completed = []
        for run in facts["runs"] + facts["replays"]:
            if run["outcome"] != "completed":
                not_completed.append(run["run_id"])
        print(
            f"   {facts['store']}: {len(facts['runs'])} runs, {len(facts['replays'])} replays; "
            f"not completed: {', '.join(not_completed) or 'none'}; with outcome mismatches: "
            f"{', '.join(facts['replays_with_mismatches']) or 'none'}; with refused spawns: "
            f"{', '.join(facts['runs_with_refused_spawns']) or 'none'}; without a spawn "
            f"count: {', '.join(facts['runs_without_spawn_counts']) or 'none'}"
        )
        problems += len(not_completed) + len(facts["replays_with_mismatches"])
        problems += len(facts["runs_with_refused_spawns"])
        problems += len(facts["runs_without_spawn_counts"])
    return 0 if problems == 0 else 1


# --- The experiment ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    arguments = read_arguments(argv)
    try:
        parameters = choose_parameters(arguments)
        kit = read_kit_connection()
        refuse_what_cannot_run(parameters, kit)
    except (Refused, wrapper.WrapperUnavailable) as refused:
        print(f"experiment.py: {refused}", file=sys.stderr)
        return 2

    estimate_duration(parameters, arguments)
    output = make_output_folder()
    spec_path = write_parameters(output, parameters, arguments)
    configurations = kapps_configurations(parameters)
    make_preload(parameters)

    store_folders = []
    for store in parameters.stores:
        store_folder = output / store
        choose_store(store, kit, parameters)
        index = []
        for repetition in range(1, parameters.repetitions + 1):
            for heartbeats in parameters.heartbeat_levels:
                # The SHACL levels in the repetition's order; the writes-only replay of the
                # SHACL-on run right after it.
                for shacl in shacl_order(parameters, repetition):
                    run = run_kapps(
                        spec_path,
                        configurations[shacl],
                        repetition,
                        heartbeats,
                        store_folder,
                        parameters,
                    )
                    index.append(run)
                    if not shacl:
                        continue
                    recorded = extract_writes(run, store_folder)
                    if recorded is not None:
                        replay = replay_writes(
                            recorded,
                            spec_path,
                            configurations[True],
                            run,
                            store_folder,
                        )
                        index.append(replay)
                        compare_outcomes(replay, store_folder)
        write_store_results(store, store_folder, index)
        store_folders.append(store_folder)

    make_figures(store_folders, output)
    packed = pack_the_run(output)
    code = say_the_end(store_folders)
    return code if packed else 1


if __name__ == "__main__":
    raise SystemExit(main())
