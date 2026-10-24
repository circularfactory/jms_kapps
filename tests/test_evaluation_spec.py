"""The spec of a sweep of the measurement harness, iteration one.

A spec is a TOML file of sweep blocks: each block is the cross product of its own axis values, and
the configurations of the spec are the union of its blocks, in the listed order, with no
duplicates. A value comes from the command's defaults, then the spec's own defaults, then its
block, then a flag of the command. A spec in which a configuration misses a value is refused,
naming the value. Nothing here reaches a store, and no file the spec names needs to exist.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import jms_kapps
from jms_kapps.evaluation.configuration import KEYS, NO_PREFILL
from jms_kapps.evaluation.spec import (
    COMMAND_DEFAULTS,
    SpecRefused,
    configurations,
    numbers,
    read_spec,
    resolved,
    setting,
)
from jms_kapps.uc2.strategies import (
    REAL_MODULE_STRATEGY,
    REAL_WMS_STRATEGY,
    ModuleStrategy,
    WmsStrategy,
)

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"
ITERATION_ONE = EVALUATION / "specs" / "iteration-one.toml"

# The values a configuration needs beside the command's defaults, as a spec's defaults give them.
TIMING = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
"""


def spec_file(folder: Path, text: str, name: str = "spec.toml") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def axes(found, *keys: str) -> list[tuple]:
    """The values of `keys` of each configuration, in order."""
    return [tuple(c.values[key] for key in keys) for c in found]


def test_two_overlapping_blocks_expand_in_the_listed_order_with_no_duplicates(
    tmp_path,
):
    spec = read_spec(
        spec_file(
            tmp_path,
            TIMING
            + """
[[sweep]]
topology = ["cross", "grid"]
shacl = [true, false]

[[sweep]]
topology = ["grid", "pair"]
shacl = false
""",
        )
    )

    found = configurations(spec)

    assert axes(found, "topology", "shacl") == [
        ("cross", True),
        ("cross", False),
        ("grid", True),
        ("grid", False),
        ("pair", False),
    ]
    assert [c.number for c in found] == [1, 2, 3, 4, 5]
    assert [c.block for c in found] == [1, 1, 1, 1, 2]
    assert len({c.key for c in found}) == 5


def test_a_block_with_no_axis_is_one_configuration_of_the_defaults(tmp_path):
    spec = read_spec(spec_file(tmp_path, TIMING + 'topology = "cross"\n[[sweep]]\n'))

    [only] = configurations(spec)

    assert only.values == {
        "topology": "cross",
        "prefill": NO_PREFILL,
        "module_strategy": "real",
        "wms_strategy": "real",
        "shacl": True,
        "collect_history": False,
        "boxes_per_minute": 6,
        "warm_up_seconds": 0,
        "window_seconds": 60,
        "drain_bound_seconds": 120,
        "matcher_interval_seconds": 2,
        "transit_seconds": 3.0,
        "retry_seconds": 1.0,
        "wrapper": True,
        "heavy": False,
    }
    assert only.repetitions == 1


def test_the_command_s_defaults_are_real_strategies_shacl_on_no_history_no_prefill_one_repetition():
    """And the wrapper on: a spec's runs are observed unless it turns it off."""
    assert COMMAND_DEFAULTS == {
        "prefill": "none",
        "module_strategy": "real",
        "wms_strategy": "real",
        "shacl": True,
        "collect_history": False,
        "wrapper": True,
        "transit_seconds": 3.0,
        "retry_seconds": 1.0,
        "repetitions": 1,
    }


def test_a_flag_overrides_the_spec_and_the_spec_overrides_a_default(tmp_path):
    spec = read_spec(
        spec_file(
            tmp_path,
            TIMING.replace(
                "heavy = false", "heavy = false\nshacl = false\nrepetitions = 3"
            )
            + """
[[sweep]]
topology = ["cross", "grid"]
boxes_per_minute = [10, 20]
""",
        )
    )

    plain = configurations(spec)
    flagged = configurations(
        spec, overrides=dict([setting("boxes_per_minute=4"), setting("shacl=true")])
    )

    # The spec's defaults over the command's: SHACL off, 3 repetitions.
    assert {c.values["shacl"] for c in plain} == {False}
    assert {c.repetitions for c in plain} == {3}
    # The block over the spec's defaults: the box rate 10 and 20, not 6.
    assert axes(plain, "topology", "boxes_per_minute") == [
        ("cross", 10),
        ("cross", 20),
        ("grid", 10),
        ("grid", 20),
    ]
    # A flag over the block and the spec's defaults; the configurations it makes the same are one.
    assert axes(flagged, "topology", "boxes_per_minute", "shacl") == [
        ("cross", 4, True),
        ("grid", 4, True),
    ]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("boxes_per_minute=4", 4),
        ("window_seconds=12.5", 12.5),
        ("shacl=false", False),
        ("topology=grid", "grid"),
        ('topology="my grid.json"', "my grid.json"),
        ("prefill=none", "none"),
        ("repetitions=2", 2),
    ],
)
def test_a_flag_s_value_reads_as_toml_or_else_as_text(text, value):
    key = text.split("=")[0]

    assert setting(text) == (key, value)


def test_a_flag_without_a_value_is_refused():
    with pytest.raises(SpecRefused, match="KEY=VALUE"):
        setting("boxes_per_minute")


def test_a_flag_for_a_key_a_spec_does_not_know_is_refused_by_its_name(tmp_path):
    spec = read_spec(spec_file(tmp_path, TIMING + 'topology = "cross"\n[[sweep]]\n'))

    with pytest.raises(SpecRefused, match="box_rate"):
        configurations(spec, overrides={"box_rate": 3})


@pytest.mark.parametrize(
    "missing",
    [
        "boxes_per_minute",
        "warm_up_seconds",
        "window_seconds",
        "drain_bound_seconds",
        "matcher_interval_seconds",
        "topology",
        "heavy",
    ],
)
def test_a_spec_without_its_own_defaults_that_misses_a_value_is_refused_by_its_name(
    tmp_path, missing
):
    """Every value that is not a default of the command comes from the spec: a block that misses
    one is refused before any run starts, whatever the other blocks give."""
    given = {
        "topology": '"cross"',
        "boxes_per_minute": "6",
        "warm_up_seconds": "60",
        "window_seconds": "300",
        "drain_bound_seconds": "300",
        "matcher_interval_seconds": "2",
        "heavy": "false",
    }
    complete = "".join(f"{key} = {value}\n" for key, value in given.items())
    missing_one = "".join(
        f"{key} = {value}\n" for key, value in given.items() if key != missing
    )
    spec = read_spec(
        spec_file(tmp_path, f"[[sweep]]\n{complete}\n[[sweep]]\n{missing_one}")
    )

    with pytest.raises(SpecRefused, match=rf"block 2\b.*\b{missing}\b"):
        configurations(spec)


def test_a_missing_value_given_by_a_flag_is_not_missing(tmp_path):
    spec = read_spec(
        spec_file(tmp_path, TIMING.replace("heavy = false\n", "") + "[[sweep]]\n")
    )

    found = configurations(spec, overrides={"topology": "cross", "heavy": False})

    assert axes(found, "topology", "heavy") == [("cross", False)]


@pytest.mark.parametrize(
    ("text", "named"),
    [
        (TIMING + "[[sweep]]\nshacl_layer = [true]\n", "shacl_layer"),
        (TIMING.replace("heavy", "heavvy") + "[[sweep]]\n", "heavvy"),
        (TIMING + "[sweeps]\n", "sweeps"),
        (TIMING + "[[sweep]]\ntopology = []\n", "topology"),
        (TIMING + "[[sweep]]\nrepetitions = 0\n", "repetitions"),
        (TIMING + "[[sweep]]\nrepetitions = [true]\n", "repetitions"),
        (TIMING + "sweep = 3\n", "sweep"),
        (TIMING, "sweep"),
    ],
)
def test_a_spec_that_is_not_one_is_refused_by_what_it_misses_or_does_not_know(
    tmp_path, text, named
):
    with pytest.raises(SpecRefused, match=named):
        configurations(read_spec(spec_file(tmp_path, text)))


def test_a_configuration_listed_again_with_other_repetitions_is_refused(tmp_path):
    spec = read_spec(
        spec_file(
            tmp_path,
            TIMING
            + 'topology = "cross"\n[[sweep]]\nrepetitions = 5\n[[sweep]]\nrepetitions = 2\n',
        )
    )

    with pytest.raises(SpecRefused, match="repetitions"):
        configurations(spec)


def test_a_spec_that_is_no_toml_is_refused(tmp_path):
    with pytest.raises(SpecRefused, match="TOML"):
        read_spec(spec_file(tmp_path, "[[sweep]\n"))


def test_the_prefill_names_its_topology_where_it_says_so(tmp_path):
    """A prefill file is for one topology, so a block of topologies and store sizes names each
    file by the topology's name: a shipped name, or a topology file's name without its suffix."""
    spec = read_spec(
        spec_file(
            tmp_path,
            TIMING
            + """
[[sweep]]
topology = ["grid", "../topologies/grid-60.json"]
prefill = ["{topology}-1k.ttl", "none"]
""",
        )
    )

    assert axes(configurations(spec), "topology", "prefill") == [
        ("grid", "grid-1k.ttl"),
        ("grid", "none"),
        ("../topologies/grid-60.json", "grid-60-1k.ttl"),
        ("../topologies/grid-60.json", "none"),
    ]


def test_the_key_of_a_configuration_is_its_values_not_its_place_or_its_spelling(
    tmp_path,
):
    """So a run is found again when the spec lists it elsewhere, or writes 60 as 60.0."""
    first = read_spec(
        spec_file(
            tmp_path, TIMING + '[[sweep]]\ntopology = ["cross", "grid"]\n', "a.toml"
        )
    )
    second = read_spec(
        spec_file(
            tmp_path,
            TIMING.replace("window_seconds = 60", "window_seconds = 60.0")
            + '[[sweep]]\ntopology = ["grid", "cross"]\n',
            "b.toml",
        )
    )

    keys = {c.values["topology"]: c.key for c in configurations(first)}
    again = {c.values["topology"]: c.key for c in configurations(second)}

    assert keys == again
    assert keys["cross"] != keys["grid"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3", [3]), ("1-3,7", [1, 2, 3, 7]), ("2, 4-5", [2, 4, 5]), ("5-5", [5])],
)
def test_a_selection_names_numbers_and_ranges(text, expected):
    assert numbers(text) == expected


@pytest.mark.parametrize("text", ["0", "3-1", "a", "1-", ""])
def test_a_selection_that_names_no_number_from_1_is_refused(text):
    with pytest.raises(SpecRefused):
        numbers(text)


# The 9 configurations near the centre, in the order the iteration-one spec lists
# them: (topology, prefill, module strategy, WMS strategy, SHACL, history).
GRID_60 = "../topologies/grid-60.json"
GRID_120 = "../topologies/grid-120.json"
CENTRE = [
    ("grid", "grid-1M.ttl", "real", "real", True, False),
    ("cross", "cross-1M.ttl", "real", "real", True, False),
    (GRID_60, "grid-60-1M.ttl", "real", "real", True, False),
    (GRID_120, "grid-120-1M.ttl", "real", "real", True, False),
    ("grid", "grid-1k.ttl", "real", "real", True, False),
    ("grid", "grid-100k.ttl", "real", "real", True, False),
    ("grid", "grid-10M.ttl", "real", "real", True, False),
    ("grid", "grid-1M.ttl", "real", "real", False, False),
    ("grid", "grid-1M.ttl", "southward", "southward", True, False),
]
AXES = (
    "topology",
    "prefill",
    "module_strategy",
    "wms_strategy",
    "shacl",
    "collect_history",
)
SIZES = ("1k", "100k", "1M", "10M")


def test_the_iteration_one_spec_holds_its_24_configurations_and_the_history_comparison():
    found = configurations(read_spec(ITERATION_ONE))

    design = {
        *(
            (topology, f"{name}-{size}.ttl", "real", "real", True, False)
            for topology, name in (
                ("cross", "cross"),
                ("grid", "grid"),
                (GRID_60, "grid-60"),
                (GRID_120, "grid-120"),
            )
            for size in SIZES
        ),
        *(("grid", f"grid-{s}.ttl", "real", "real", False, False) for s in SIZES),
        *(
            ("grid", f"grid-{s}.ttl", "southward", "southward", True, False)
            for s in SIZES
        ),
    }
    history = ("grid", "grid-1M.ttl", "real", "real", True, True)
    assert len(design) == 24
    assert set(axes(found, *AXES)) == design | {history}
    assert len(found) == 25
    assert axes(found, *AXES)[:9] == CENTRE
    assert {c.repetitions for c in found} == {5}
    assert sum(c.repetitions for c in found) == 125


def test_the_iteration_one_spec_runs_each_configuration_with_the_run_task_s_timing():
    """The run timing: 30 boxes per minute, a warm-up of 1 minute,
    a window of 5 minutes, a drain bound of 5 minutes, a matcher interval of 2 seconds, and a load
    that is not heavy."""
    for configuration in configurations(read_spec(ITERATION_ONE)):
        values = configuration.values
        assert values["boxes_per_minute"] == 30
        assert values["warm_up_seconds"] == 60
        assert values["window_seconds"] == 300
        assert values["drain_bound_seconds"] == 300
        assert values["matcher_interval_seconds"] == 2
        assert values["heavy"] is False


def test_the_iteration_one_spec_resolves_once_its_files_are_there(tmp_path):
    """With a stand-in for each file it names: the two grid files and the prefill files, which a
    person makes. Each configuration then resolves to the run task's timing."""
    specs = tmp_path / "specs"
    specs.mkdir()
    spec = specs / ITERATION_ONE.name
    shutil.copy(ITERATION_ONE, spec)
    (tmp_path / "topologies").mkdir()
    grid = EVALUATION.parent / "uc2" / "topologies" / "grid.json"
    for name in ("grid-60.json", "grid-120.json"):
        shutil.copy(grid, tmp_path / "topologies" / name)
    prefills = tmp_path / "prefills"
    prefills.mkdir()
    found = configurations(read_spec(spec))
    for configuration in found:
        (prefills / configuration.values["prefill"]).write_text("# a stand-in\n")
    strategies: dict = {
        "module_strategies": {
            "real": REAL_MODULE_STRATEGY,
            "southward": _module_strategy(),
        },
        "wms_strategies": {"real": REAL_WMS_STRATEGY, "southward": _wms_strategy()},
    }

    runs = [
        resolved(c, spec=read_spec(spec), prefills=prefills, **strategies)
        for c in found
    ]

    assert {r.topology for r in runs} == {
        "cross",
        "grid",
        str(tmp_path / "specs" / GRID_60),
        str(tmp_path / "specs" / GRID_120),
    }
    assert {r.prefill.parent for r in runs if r.prefill} == {prefills}
    for run in runs:
        assert run.boxes_per_minute == 30.0
        assert run.warm_up_seconds == 60.0
        assert run.window_seconds == 300.0
        assert run.drain_bound_seconds == 300.0
        assert run.matcher_interval_seconds == 2.0
        assert run.heavy is False


def test_a_file_the_spec_names_that_is_not_there_is_refused_when_it_resolves(tmp_path):
    spec = read_spec(ITERATION_ONE)
    [grid_60] = [
        c
        for c in configurations(spec)
        if c.values["topology"] == GRID_60 and c.values["prefill"] == "grid-60-1M.ttl"
    ]

    # The topology file first, while it is not there yet; then the prefill file, which is not.
    with pytest.raises(SpecRefused, match=r"configuration 3\b.*(topology|prefill)"):
        resolved(grid_60, spec=spec, prefills=tmp_path)


def _module_strategy() -> ModuleStrategy:
    from jms_kapps.evaluation.southward import SOUTHWARD_MODULE_STRATEGY

    return SOUTHWARD_MODULE_STRATEGY


def _wms_strategy() -> WmsStrategy:
    from jms_kapps.evaluation.southward import SOUTHWARD_WMS_STRATEGY

    return SOUTHWARD_WMS_STRATEGY


def test_every_key_of_a_configuration_can_be_an_axis():
    """The spec's keys are those of a configuration, and the number of repetitions."""
    from jms_kapps.evaluation.spec import SPEC_KEYS

    assert SPEC_KEYS == (*KEYS, "repetitions")


# The run settings of the spec (interim names).


def test_the_transit_time_and_the_retry_wait_come_from_the_command_s_defaults_and_can_be_swept(
    tmp_path,
):
    spec = read_spec(
        spec_file(
            tmp_path,
            TIMING
            + 'topology = "cross"\n[[sweep]]\ntransit_seconds = [1.0, 3.0]\n'
            + "retry_seconds = [0.5, 1.0]\n",
        )
    )

    found = configurations(spec)

    assert axes(found, "transit_seconds", "retry_seconds") == [
        (1.0, 0.5),
        (1.0, 1.0),
        (3.0, 0.5),
        (3.0, 1.0),
    ]
    assert len({c.key for c in found}) == 4


def test_the_spec_s_defaults_state_the_transit_time_and_the_retry_wait_over_the_command_s(
    tmp_path,
):
    text = TIMING + "transit_seconds = 2.0\nretry_seconds = 0.5\n"
    [only] = configurations(
        read_spec(spec_file(tmp_path, text + 'topology = "cross"\n[[sweep]]\n'))
    )

    assert (only.values["transit_seconds"], only.values["retry_seconds"]) == (2.0, 0.5)


def test_the_keys_of_a_configuration_differ_from_those_before_the_two_keys(tmp_path):
    """The "after" series starts fresh: no earlier run is found under a new key."""
    import hashlib
    import json

    [only] = configurations(
        read_spec(spec_file(tmp_path, TIMING + 'topology = "cross"\n[[sweep]]\n'))
    )
    old_values = {
        k: v
        for k, v in only.values.items()
        if k not in ("transit_seconds", "retry_seconds")
    }
    old_key = hashlib.sha256(
        json.dumps(
            {
                k: float(v)
                if isinstance(v, int | float) and not isinstance(v, bool)
                else v
                for k, v in old_values.items()
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()[:16]

    assert only.key != old_key


def test_the_base_seed_is_the_spec_s_or_none_and_is_no_part_of_a_configuration(
    tmp_path,
):
    plain = read_spec(spec_file(tmp_path, TIMING + 'topology = "cross"\n[[sweep]]\n'))
    seeded = read_spec(
        spec_file(
            tmp_path,
            TIMING + 'pick_base_seed = 99\ntopology = "cross"\n[[sweep]]\n',
            "seeded.toml",
        )
    )

    assert (plain.base_seed, seeded.base_seed) == (None, 99)
    assert [c.key for c in configurations(plain)] == [
        c.key for c in configurations(seeded)
    ]
    assert "pick_base_seed" not in configurations(seeded)[0].values


@pytest.mark.parametrize("seed", ["-1", "1.5", '"7"', "true"])
def test_a_base_seed_that_is_no_whole_number_from_0_is_refused(tmp_path, seed):
    text = TIMING + f"pick_base_seed = {seed}\n" + 'topology = "cross"\n[[sweep]]\n'

    with pytest.raises(SpecRefused, match="pick_base_seed"):
        read_spec(spec_file(tmp_path, text))


def test_a_block_may_not_give_the_base_seed_since_it_is_one_for_every_configuration(
    tmp_path,
):
    text = TIMING + 'topology = "cross"\n[[sweep]]\npick_base_seed = 5\n'

    with pytest.raises(SpecRefused, match="pick_base_seed"):
        read_spec(spec_file(tmp_path, text))


@pytest.mark.parametrize(
    "path",
    sorted((EVALUATION / "specs").glob("*.toml")),
    ids=lambda p: p.name,
)
def test_every_shipped_spec_states_the_transit_time_and_the_retry_wait(path):
    import tomllib

    defaults = tomllib.loads(path.read_text(encoding="utf-8"))["defaults"]

    assert defaults["transit_seconds"] == 3.0
    assert defaults["retry_seconds"] == 1.0


@pytest.mark.parametrize(
    "path",
    sorted((EVALUATION / "configurations").glob("*.toml")),
    ids=lambda p: p.name,
)
def test_every_shipped_configuration_states_the_transit_time_and_the_retry_wait(path):
    import tomllib

    values = tomllib.loads(path.read_text(encoding="utf-8"))

    assert values["transit_seconds"] == 3.0
    assert values["retry_seconds"] == 1.0
