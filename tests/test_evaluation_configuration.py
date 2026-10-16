"""The configuration of one UC2 run of the measurement harness, iteration one (jms_kapps 47).

A configuration is a TOML file with every value the run takes: a missing value is refused by its
name, and so is a key the run does not know. A topology file, a prefill file or a strategy that
cannot be found is refused before anything reaches a store. The resolved configuration is written
back in the same TOML form, and reads back to the same configuration, so a record can be run again.
Nothing here reaches a store.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from jms_kapps.evaluation.configuration import (
    DEFAULT_BASE_SEED,
    KEYS,
    NO_PREFILL,
    Configuration,
    ConfigurationRefused,
    pick_seed,
    read_configuration,
    resolve,
)
from jms_kapps.uc2.strategies import REAL_MODULE_STRATEGY, REAL_WMS_STRATEGY

STRATEGIES = {
    "module_strategies": {"real": REAL_MODULE_STRATEGY},
    "wms_strategies": {"real": REAL_WMS_STRATEGY},
}

CROSS = {
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
    "transit_seconds": 3,
    "retry_seconds": 1,
    "wrapper": True,
    "heavy": False,
}


def toml(values: dict) -> str:
    """`values` as the lines of a configuration file, as a person writes one."""
    lines = []
    for key, value in values.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def written(folder: Path, values: dict, name: str = "run.toml") -> Path:
    path = folder / name
    path.write_text(toml(values), encoding="utf-8")
    return path


def test_a_complete_configuration_resolves_with_every_value(tmp_path):
    configuration = read_configuration(written(tmp_path, CROSS), **STRATEGIES)

    assert configuration == Configuration(
        topology="cross",
        prefill=None,
        module_strategy="real",
        wms_strategy="real",
        shacl=True,
        collect_history=False,
        boxes_per_minute=6.0,
        warm_up_seconds=0.0,
        window_seconds=60.0,
        drain_bound_seconds=120.0,
        matcher_interval_seconds=2.0,
        transit_seconds=3.0,
        retry_seconds=1.0,
        wrapper=True,
        heavy=False,
    )


@pytest.mark.parametrize("missing", KEYS)
def test_a_configuration_with_a_missing_value_is_refused_with_the_value_s_name(
    tmp_path, missing
):
    values = {k: v for k, v in CROSS.items() if k != missing}

    with pytest.raises(ConfigurationRefused, match=rf"\b{missing}\b"):
        read_configuration(written(tmp_path, values), **STRATEGIES)


def test_a_key_the_run_does_not_know_is_refused_by_its_name(tmp_path):
    """So that a misspelt key, or a password, never passes unseen."""
    values = CROSS | {"password": "x"}

    with pytest.raises(ConfigurationRefused, match="password"):
        read_configuration(written(tmp_path, values), **STRATEGIES)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("shacl", "yes"),
        ("heavy", 1),
        ("boxes_per_minute", 0),
        ("boxes_per_minute", -3),
        ("boxes_per_minute", True),
        ("window_seconds", 0),
        ("drain_bound_seconds", -1),
        ("warm_up_seconds", -1),
        ("warm_up_seconds", "1 min"),
        ("matcher_interval_seconds", 0),
        ("matcher_interval_seconds", -2),
        ("wrapper", "on"),
        ("wrapper", 1),
        ("topology", ""),
        ("module_strategy", 3),
    ],
)
def test_a_value_of_the_wrong_kind_is_refused_with_its_name(tmp_path, key, value):
    with pytest.raises(ConfigurationRefused, match=rf"\b{key}\b"):
        read_configuration(written(tmp_path, CROSS | {key: value}), **STRATEGIES)


@pytest.mark.parametrize("missing", ["warm_up_seconds", "matcher_interval_seconds"])
def test_a_configuration_of_part_one_without_the_two_new_values_is_refused_by_name(
    tmp_path, missing
):
    """The warm-up and the matcher interval came with the sweep (jms_kapps 78): a configuration
    written before them has neither, and is refused with the name of the one it misses."""
    values = {k: v for k, v in CROSS.items() if k != missing}

    with pytest.raises(ConfigurationRefused, match=rf"no value for {missing}\b"):
        read_configuration(written(tmp_path, values), **STRATEGIES)


def test_a_configuration_written_before_the_wrapper_is_refused_by_its_name(tmp_path):
    """The wrapper came with the observation (jms_kapps 48): a configuration written before it
    has no `wrapper`, and is refused with that name."""
    values = {k: v for k, v in CROSS.items() if k != "wrapper"}

    with pytest.raises(ConfigurationRefused, match=r"no value for wrapper\b"):
        read_configuration(written(tmp_path, values), **STRATEGIES)


@pytest.mark.parametrize("wrapper", [True, False], ids=["wrapped", "unwrapped"])
def test_a_configuration_turns_the_wrapper_on_or_off_and_writes_it_back(
    tmp_path, wrapper
):
    configuration = read_configuration(
        written(tmp_path, CROSS | {"wrapper": wrapper}), **STRATEGIES
    )

    assert configuration.wrapper is wrapper
    assert tomllib.loads(configuration.to_toml())["wrapper"] is wrapper


def test_a_warm_up_of_0_is_a_run_without_one(tmp_path):
    configuration = read_configuration(written(tmp_path, CROSS), **STRATEGIES)

    assert configuration.warm_up_seconds == 0.0
    assert configuration.matcher_interval_seconds == 2.0


def test_a_missing_topology_file_is_refused(tmp_path):
    with pytest.raises(ConfigurationRefused, match="topology"):
        read_configuration(
            written(tmp_path, CROSS | {"topology": "no-such-grid.json"}), **STRATEGIES
        )


def test_a_missing_prefill_file_is_refused(tmp_path):
    with pytest.raises(ConfigurationRefused, match="prefill"):
        read_configuration(
            written(tmp_path, CROSS | {"prefill": "missing.ttl"}), **STRATEGIES
        )


@pytest.mark.parametrize("key", ["module_strategy", "wms_strategy"])
def test_a_strategy_that_is_not_found_is_refused(tmp_path, key):
    with pytest.raises(ConfigurationRefused, match=key):
        read_configuration(
            written(tmp_path, CROSS | {key: "no-such-strategy"}), **STRATEGIES
        )


def test_files_are_found_beside_the_configuration_and_resolved_to_absolute_paths(
    tmp_path, monkeypatch
):
    """A relative path is read from the configuration's folder, wherever the command runs."""
    folder = tmp_path / "configurations"
    folder.mkdir()
    (folder / "prefill.ttl").write_text("# a prefill\n", encoding="utf-8")
    cross = Path(__file__).parent / "topologies" / "modules_preview_cross.json"
    (folder / "my-cross.json").write_bytes(cross.read_bytes())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    configuration = read_configuration(
        written(
            folder, CROSS | {"topology": "my-cross.json", "prefill": "prefill.ttl"}
        ),
        **STRATEGIES,
    )

    assert configuration.topology == str(folder / "my-cross.json")
    assert configuration.prefill == folder / "prefill.ttl"


def test_the_resolved_configuration_reads_back_to_itself_in_the_same_toml_form(
    tmp_path,
):
    """What a record holds, read back and resolved, is the configuration the run started from."""
    (tmp_path / 'odd "name" \\ é.ttl').write_text("# a prefill\n", encoding="utf-8")
    started = read_configuration(
        written(tmp_path, CROSS | {"prefill": 'odd \\"name\\" \\\\ é.ttl'}),
        **STRATEGIES,
    )

    text = started.to_toml()
    again = resolve(tomllib.loads(text), base=tmp_path / "anywhere", **STRATEGIES)

    assert again == started
    assert list(tomllib.loads(text)) == list(KEYS)


def test_no_prefill_is_written_back_as_none(tmp_path):
    started = read_configuration(written(tmp_path, CROSS), **STRATEGIES)

    assert tomllib.loads(started.to_toml())["prefill"] == NO_PREFILL


def test_a_configuration_that_is_no_toml_is_refused(tmp_path):
    path = tmp_path / "run.toml"
    path.write_text("topology = \n", encoding="utf-8")

    with pytest.raises(ConfigurationRefused, match="TOML"):
        read_configuration(path, **STRATEGIES)


# The run settings of the spec (jms_kapps 93, interim names, jms_kapps 83): the transit time and
# the retry wait are keys, and the pick seed is the one value a configuration may leave out.


def test_the_transit_time_and_the_retry_wait_are_keys_and_missing_ones_are_refused(
    tmp_path,
):
    assert "transit_seconds" in KEYS and "retry_seconds" in KEYS
    for missing in ("transit_seconds", "retry_seconds"):
        values = {k: v for k, v in CROSS.items() if k != missing}

        with pytest.raises(ConfigurationRefused, match=rf"\b{missing}\b"):
            read_configuration(written(tmp_path, values), **STRATEGIES)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("transit_seconds", -1),
        ("transit_seconds", "3"),
        ("retry_seconds", 0),
        ("retry_seconds", -1),
        ("retry_seconds", True),
    ],
)
def test_a_transit_time_or_retry_wait_of_the_wrong_kind_is_refused_by_name(
    tmp_path, key, value
):
    with pytest.raises(ConfigurationRefused, match=key):
        read_configuration(written(tmp_path, CROSS | {key: value}), **STRATEGIES)


def test_a_transit_time_of_0_is_a_run_without_a_move(tmp_path):
    configuration = read_configuration(
        written(tmp_path, CROSS | {"transit_seconds": 0}), **STRATEGIES
    )

    assert configuration.transit_seconds == 0.0


def test_the_pick_seed_may_be_left_out_and_is_no_key(tmp_path):
    configuration = read_configuration(written(tmp_path, CROSS), **STRATEGIES)

    assert "wms_pick_seed" not in KEYS
    assert configuration.wms_pick_seed is None
    assert "wms_pick_seed" not in configuration.to_toml()


def test_the_pick_seed_is_written_after_the_keys_and_reads_back(tmp_path):
    configuration = read_configuration(
        written(tmp_path, CROSS | {"wms_pick_seed": 987654321}), **STRATEGIES
    )

    text = configuration.to_toml()

    assert configuration.wms_pick_seed == 987654321
    assert text.splitlines()[-1] == "wms_pick_seed = 987654321"
    assert tomllib.loads(text)["wms_pick_seed"] == 987654321
    again = read_configuration(
        written(tmp_path, tomllib.loads(text), "again.toml"), **STRATEGIES
    )
    assert again == configuration


@pytest.mark.parametrize("seed", [-1, 1.5, "7", True])
def test_a_pick_seed_that_is_no_whole_number_from_0_is_refused(tmp_path, seed):
    with pytest.raises(ConfigurationRefused, match="wms_pick_seed"):
        read_configuration(
            written(tmp_path, CROSS | {"wms_pick_seed": seed}), **STRATEGIES
        )


def test_a_key_beside_the_seed_that_the_run_does_not_know_is_still_refused(tmp_path):
    with pytest.raises(ConfigurationRefused, match="wms_pick_sed"):
        read_configuration(written(tmp_path, CROSS | {"wms_pick_sed": 1}), **STRATEGIES)


def test_repetition_k_has_one_seed_for_a_base_seed_whatever_the_configuration():
    assert pick_seed(7, 1) == pick_seed(7, 1)
    seeds = [pick_seed(7, k) for k in range(1, 21)]
    assert len(set(seeds)) == 20
    assert seeds != [pick_seed(8, k) for k in range(1, 21)]
    assert all(0 <= seed < 2**63 for seed in seeds)


def test_the_derived_seeds_are_fixed_numbers_that_python_keeps_across_versions():
    """A digest, not `hash()`, so that the "after" series' seeds are the same on every host."""
    assert [pick_seed(DEFAULT_BASE_SEED, k) for k in (1, 2, 3)] == [
        4218638168896275004,
        4942483095314964677,
        7510743106381998201,
    ]
