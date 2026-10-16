"""The grid script of the measurement harness (jms_kapps 94): it makes a topology file of W columns
and H rows in the pattern of the shipped 30-module grid, and the 60- and 120-module grids of
iteration one are its 12 x 5 and 24 x 5 grids, committed. Nothing here reaches a store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kapps_triplestore_interface import IRI

import jms_kapps
from jms_kapps.evaluation import grid, prefill
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.uc2.routing import reachable
from jms_kapps.uc2.topology import (
    FCI_NS,
    entries_and_exits,
    load_topology,
    parse_topology,
    topology_file,
)

PACKAGE = Path(jms_kapps.__file__).parent
SHIPPED_GRID = PACKAGE / "uc2" / "topologies" / "grid.json"
COMMITTED = PACKAGE / "evaluation" / "topologies"
ITERATION_ONE = PACKAGE / "evaluation" / "specs" / "iteration-one.toml"

GRIDS = [(60, 12, 5), (120, 24, 5)]


def test_six_by_five_reproduces_the_shipped_grid_byte_for_byte():
    assert grid.grid_text(6, 5) == SHIPPED_GRID.read_text(encoding="utf-8")


def test_six_by_five_has_the_shipped_grids_modules_connections_and_roles():
    """The same, as the loader reads both: the modules, each module's connections, the entry
    modules and the exit modules."""
    made = parse_topology(grid.grid_modules(6, 5))
    shipped = load_topology("grid")

    assert made.model_dump() == shipped.model_dump()
    assert entries_and_exits(
        {m.id: m.types for m in made.modules}
    ) == entries_and_exits({m.id: m.types for m in shipped.modules})


def test_the_pattern_below_the_top_row_alternates_west_and_east():
    """Row 1 enters and links south; rows 2 to H-1 link south and alternate west, east; the last
    row exits, chained east to a last module with no connection."""
    modules = {m.id: m for m in parse_topology(grid.grid_modules(4, 4)).modules}

    def links(n: int) -> list[tuple[str, str]]:
        return [(c.direction, c.to) for c in modules[f"Module{n}"].connections]

    assert links(1) == [("South", "Module5")]
    assert links(5) == [("South", "Module9")]  # row 2, first column: no west neighbour
    assert links(6) == [("South", "Module10"), ("West", "Module5")]
    assert links(9) == [("South", "Module13"), ("East", "Module10")]  # row 3, east
    assert links(10) == [("South", "Module14"), ("East", "Module11")]
    assert links(12) == [("South", "Module16")]  # row 3, last column: no east neighbour
    assert links(13) == [("East", "Module14")]  # the exit row
    assert links(16) == []


@pytest.mark.parametrize(("modules", "width", "height"), GRIDS)
def test_the_committed_grid_is_what_the_script_makes(modules, width, height):
    committed = COMMITTED / f"grid-{modules}.json"

    assert committed.read_text(encoding="utf-8") == grid.grid_text(width, height)
    assert len(json.loads(committed.read_text(encoding="utf-8"))) == modules


@pytest.mark.parametrize(("modules", "width", "height"), GRIDS)
def test_the_iteration_one_spec_resolves_the_committed_grid(modules, width, height):
    spec = read_spec(ITERATION_ONE)
    names = {f"../topologies/grid-{modules}.json"}
    found = [c for c in configurations(spec) if c.values["topology"] in names]

    assert found
    for configuration in found:
        path = (ITERATION_ONE.parent / str(configuration.values["topology"])).resolve()
        assert path == (COMMITTED / f"grid-{modules}.json").resolve()
        assert len(topology_file(path).modules) == modules


@pytest.mark.parametrize(("modules", "width", "height"), GRIDS)
def test_the_committed_grid_passes_the_load_check(modules, width, height):
    topology = topology_file(COMMITTED / f"grid-{modules}.json")

    assert len(topology.modules) == modules
    assert len({m.id for m in topology.modules}) == modules


@pytest.mark.parametrize(("modules", "width", "height"), GRIDS)
def test_every_module_is_reachable_from_an_entry_and_reaches_an_exit(
    modules, width, height
):
    topology = topology_file(COMMITTED / f"grid-{modules}.json")
    entries, exits = entries_and_exits({m.id: m.types for m in topology.modules})
    assert len(entries) == width
    assert len(exits) == width
    connections = [
        (m.resource, IRI(c.to, base=FCI_NS))
        for m in topology.modules
        for c in m.connections
    ]

    from_an_entry = {IRI(e, base=FCI_NS) for e in entries}
    for entry in entries:
        from_an_entry |= reachable(connections, IRI(entry, base=FCI_NS))
    assert from_an_entry == {m.resource for m in topology.modules}

    to_an_exit = set()
    exit_resources = {IRI(e, base=FCI_NS) for e in exits}
    for module in topology.modules:
        if module.resource in exit_resources or (
            reachable(connections, module.resource) & exit_resources
        ):
            to_an_exit.add(module.resource)
    assert to_an_exit == {m.resource for m in topology.modules}


@pytest.mark.parametrize(("modules", "width", "height"), GRIDS)
def test_the_prefill_generator_accepts_the_committed_grid(
    tmp_path, modules, width, height
):
    path = tmp_path / f"grid-{modules}-1k.ttl"

    result = prefill.write_prefill(
        path, topology=str(COMMITTED / f"grid-{modules}.json"), size=1000, seed=1
    )

    assert result.boxes > 0
    assert path.read_text(encoding="utf-8").startswith("# topology: ")


def test_the_command_writes_the_file_and_says_what_it_made(tmp_path, capsys):
    target = tmp_path / "grid-60.json"

    status = grid.main(["--width", "12", "--height", "5", "--output", str(target)])

    assert status == 0
    assert target.read_text(encoding="utf-8") == (COMMITTED / "grid-60.json").read_text(
        encoding="utf-8"
    )
    assert str(target) in capsys.readouterr().out


@pytest.mark.parametrize(
    "options",
    [["--width", "0", "--height", "5"], ["--width", "6", "--height", "1"]],
)
def test_the_command_refuses_a_grid_without_an_entry_row_and_an_exit_row(
    tmp_path, capsys, options
):
    target = tmp_path / "grid.json"

    with pytest.raises(SystemExit) as stopped:
        grid.main([*options, "--output", str(target)])

    assert stopped.value.code == 2
    assert not target.exists()
    assert "grid" in capsys.readouterr().err
