"""The topologies use case 2 launches, read and checked without a store.

The expected connections and places are copied by hand from the artifact's
`modules_preview_cross.json` on the `paper` branch, not derived from the asset under test, and
what each shipped topology seeds is compared with what it seeded before it became a module list
(`_topologies`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal

import pytest
from _topologies import FIRST_ITERATIONS_CROSS, SEEDED_BEFORE, as_seeded, module_entry
from kapps_triplestore_interface import IRI
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from jms_kapps.uc2.launcher import runner
from jms_kapps.uc2.routing import next_module
from jms_kapps.uc2.topology import (
    CONNECTION_KEY,
    CONNECTS_TO_KEY,
    DIRECTION_KEY,
    LABEL_KEY,
    PORT_KEY,
    TOPOLOGY_FORMAT,
    TRANSIT_TIME_KEY,
    TYPE_KEY,
    Connection,
    Module,
    Topology,
    edited_topology,
    entries_and_exits,
    load_topology,
    parse_topology,
    parse_topology_file,
    shipped_topology,
    topology_file,
    topology_names,
)
from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS, MONITOR, module_iri


def test_the_first_iterations_cross_file_loads_as_it_is():
    """Its five modules with the types it lists, no label, and each connection as it lists it."""
    topology = load_topology(str(FIRST_ITERATIONS_CROSS))

    assert [(m.id, m.types, m.label, m.name) for m in topology.modules] == [
        ("Module1", ["EntryModule"], None, "Module1"),
        ("Module2", ["EntryModule"], None, "Module2"),
        ("Module3", ["FlexConveyorModule"], None, "Module3"),
        ("Module4", ["ExitModule"], None, "Module4"),
        ("Module5", ["ExitModule"], None, "Module5"),
    ]
    assert {
        (m.id, c.direction, c.to, c.port)
        for m in topology.modules
        for c in m.connections
    } == {
        ("Module1", "South", "Module3", 0),
        ("Module2", "East", "Module3", 0),
        ("Module3", "North", "Module1", 0),
        ("Module3", "West", "Module2", 0),
        ("Module3", "East", "Module4", 0),
        ("Module3", "South", "Module5", 0),
        ("Module4", "West", "Module3", 0),
        ("Module5", "North", "Module3", 0),
    }


@pytest.mark.parametrize("name", sorted(SEEDED_BEFORE))
def test_each_shipped_topology_converted_writes_the_modules_it_wrote_before(name):
    """The same modules, with the same types, labels and connections, as the short topology
    document it was converted from, but for the cross's types. (Changed on 2026-10-06, jms_kapps
    45, decided by Etienne: the shipped cross names no roles, as `shipped_as_seeded_before.json`
    holds it since. Until then the sentence ended at "from".)"""
    assert as_seeded(shipped_topology(name).graph(0)) == SEEDED_BEFORE[name]


def test_the_cross_topology_names_five_modules():
    topology = load_topology("cross")

    assert [(m.resource, m.label) for m in topology.modules] == [
        (module_iri(1), "Module 1"),
        (module_iri(2), "Module 2"),
        (module_iri(3), "Module 3"),
        (module_iri(4), "Module 4"),
        (module_iri(5), "Module 5"),
    ]


def test_the_cross_topology_connects_the_modules_as_the_artifact_does():
    graph = load_topology("cross").graph(0)

    connections = {
        (
            module.fragment,
            graph.value(c, URIRef(FC.hasDirection)).fragment,
            graph.value(c, URIRef(FC.connectsTo)).fragment,
            graph.value(c, URIRef(FC.onPort)).toPython(),
        )
        for module, c in graph.subject_objects(URIRef(FC.hasConnection))
    }

    assert connections == {
        ("Module1", "South", "Module3", 0),
        ("Module2", "East", "Module3", 0),
        ("Module3", "North", "Module1", 0),
        ("Module3", "West", "Module2", 0),
        ("Module3", "East", "Module4", 0),
        ("Module3", "South", "Module5", 0),
        ("Module4", "West", "Module3", 0),
        ("Module5", "North", "Module3", 0),
    }


def test_the_graph_types_and_labels_every_module_and_connection():
    """The cross names no roles: each of its modules is a FlexConveyorModule, and none is an
    entry or an exit module. (Changed on 2026-10-06, jms_kapps 45, decided by Etienne: "in the
    current topologies already the cross topology would make sense to have this". Until then the
    graph typed Modules 1 and 2 as entry modules and Modules 4 and 5 as exit modules.)"""
    graph = load_topology("cross").graph(0)

    assert {
        m.fragment for m in graph.subjects(RDF.type, URIRef(FC.FlexConveyorModule))
    } == {"Module1", "Module2", "Module3", "Module4", "Module5"}
    assert not set(graph.subjects(RDF.type, URIRef(FC.EntryModule)))
    assert not set(graph.subjects(RDF.type, URIRef(FC.ExitModule)))
    assert graph.value(URIRef(module_iri(3)), RDFS.label) == Literal("Module 3")
    for _, connection in graph.subject_objects(URIRef(FC.hasConnection)):
        assert (connection, RDF.type, URIRef(FC.Connection)) in graph


def test_the_cross_is_placed_on_a_grid_by_its_directions():
    assert load_topology("cross").places() == {
        "Module1": (1, 0),
        "Module2": (0, 1),
        "Module3": (1, 1),
        "Module4": (2, 1),
        "Module5": (1, 2),
    }


def test_a_module_no_connection_reaches_is_placed_on_a_row_of_its_own():
    topology = Topology.model_validate(
        {
            "modules": [
                {
                    "id": "A",
                    "label": "A",
                    "connections": [{"direction": "East", "to": "B"}],
                },
                {"id": "B", "label": "B"},
                {"id": "C", "label": "C"},
            ]
        }
    )

    assert topology.places() == {"A": (0, 0), "B": (1, 0), "C": (0, 1)}


def test_a_part_that_is_not_connected_to_the_first_module_is_laid_out_whole_below_it():
    """The page's editor holds A on its own, C east of A and B south of C, with the one
    connection C to B. Laid out from B, C would lie north of B, on A's cell: the part B and C is
    laid out on its own, and then below A, so that every connection still leads one step in its
    direction."""
    topology = Topology.model_validate(
        {
            "modules": [
                {"id": "A"},
                {"id": "B"},
                {"id": "C", "connections": [{"direction": "South", "to": "B"}]},
            ]
        }
    )

    assert topology.places() == {"A": (0, 0), "C": (0, 1), "B": (0, 2)}


def test_the_shipped_topologies_are_listed():
    assert "cross" in topology_names()


# The first iteration's other samples, on its `paper` branch: `modules_preview_minimal.json` (two
# modules), `modules_preview.json` (four) and `modules_30.json` (thirty), in their shapes. There,
# every link ran both ways; here each runs one way, so that no two boxes can wait for each other
# forever (Etienne, 2026-10-04).
SAMPLES = {
    # name: (modules, columns, rows)
    "pair": (2, 1, 2),
    "tee": (4, 2, 3),
    "grid": (30, 6, 5),
}

_STEP = {"North": (0, -1), "East": (1, 0), "South": (0, 1), "West": (-1, 0)}


def test_the_first_iterations_samples_ship_beside_the_cross():
    assert topology_names() == ["cross", "grid", "merge-fault-demo", "pair", "tee"]


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_each_sample_parses_and_lays_out_by_its_directions(name):
    """Every module gets a cell of its own, the cells fill the sample's grid, and every connection
    leads one step in its direction: no module is set aside on a row of its own."""
    modules, columns, rows = SAMPLES[name]
    topology = shipped_topology(name)
    places = topology.places()

    assert len(topology.modules) == len(places) == len(set(places.values())) == modules
    assert {x for x, _ in places.values()} == set(range(columns))
    assert {y for _, y in places.values()} == set(range(rows))
    for module in topology.modules:
        x, y = places[module.id]
        for connection in module.connections:
            dx, dy = _STEP[connection.direction]
            assert places[connection.to] == (x + dx, y + dy), (module.id, connection)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_each_sample_runs_one_way(name):
    topology = shipped_topology(name)
    links = {(m.id, c.to) for m in topology.modules for c in m.connections}

    assert not links & {(to, frm) for frm, to in links}


@pytest.mark.parametrize("name", topology_names())
def test_a_box_from_every_entry_reaches_every_exit(name):
    """A topology that names no entry (exit) module counts every module as one, so on the cross
    a box from every module reaches every other. (Changed on 2026-10-06, jms_kapps 45, decided by
    Etienne: roles are optional. Until then the entries and the exits were the modules of those
    types, and each shipped topology had both.)"""
    topology = shipped_topology(name)
    connections = [
        (m.resource, IRI(c.to, base=FCI_NS))
        for m in topology.modules
        for c in m.connections
    ]
    entries, exits = entries_and_exits({m.resource: m.types for m in topology.modules})

    assert entries and exits
    for entry in entries:
        for exit_ in exits:
            if entry == exit_:
                continue
            here, hops = entry, 0
            while here != exit_:
                here = next_module(connections, here, exit_)
                hops += 1
                assert here is not None, (entry, exit_)
            assert hops <= len(topology.modules)


def test_the_grid_enters_on_its_top_row_and_exits_on_its_bottom_row():
    topology = shipped_topology("grid")
    places = topology.places()

    assert {m.id for m in topology.modules if "EntryModule" in m.types} == {
        f"Module{n}" for n in range(1, 7)
    }
    assert {m.id for m in topology.modules if "ExitModule" in m.types} == {
        f"Module{n}" for n in range(25, 31)
    }
    assert {places[f"Module{n}"] for n in range(1, 7)} == {(x, 0) for x in range(6)}
    assert {places[f"Module{n}"] for n in range(25, 31)} == {(x, 4) for x in range(6)}


def test_the_grid_flows_down_every_column_and_along_its_rows_by_turns_below_its_top_row():
    """The top row, of the entry modules, passes boxes only downwards, so no box arrives on an
    entry module from a neighbour (Etienne, 2026-10-05). Row 2 runs to the West, row 3 to the
    East, and so on; Module n is in row (n - 1) // 6, counted from 0."""
    links = {
        (m.id, c.direction, c.to)
        for m in shipped_topology("grid").modules
        for c in m.connections
    }
    down = {(f"Module{n}", "South", f"Module{n + 6}") for n in range(1, 25)}
    along = {
        (f"Module{n}", "East", f"Module{n + 1}")
        for row in (2, 4)
        for n in range(row * 6 + 1, row * 6 + 6)
    } | {
        (f"Module{n}", "West", f"Module{n - 1}")
        for row in (1, 3)
        for n in range(row * 6 + 2, row * 6 + 7)
    }

    assert links == down | along


def test_no_connection_of_the_grid_leads_into_an_entry_module():
    topology = shipped_topology("grid")
    entries = {m.id for m in topology.modules if "EntryModule" in m.types}

    assert {c.to for m in topology.modules for c in m.connections} & entries == set()


def test_the_pair_enters_on_module_1_and_exits_on_module_2():
    assert [(m.id, m.types) for m in shipped_topology("pair").modules] == [
        ("Module1", ["EntryModule"]),
        ("Module2", ["ExitModule"]),
    ]


def test_the_tee_enters_on_its_top_and_exits_on_both_ends_of_its_fork():
    topology = shipped_topology("tee")

    assert [(m.id, m.types) for m in topology.modules] == [
        ("Module1", ["EntryModule"]),
        ("Module2", ["FlexConveyorModule"]),
        ("Module3", ["ExitModule"]),
        ("Module4", ["ExitModule"]),
    ]
    assert {(m.id, c.to) for m in topology.modules for c in m.connections} == {
        ("Module1", "Module2"),
        ("Module2", "Module3"),
        ("Module2", "Module4"),
    }


def test_the_merge_fault_demo_downloaded_and_uploaded_gives_the_same_module_list(
    tmp_path,
):
    """The editor's download is `module_list()`, its upload is `parse_topology`: the shipped merge
    of two streams survives both (jms_kapps 46)."""
    topology = shipped_topology("merge-fault-demo")

    assert parse_topology(topology.module_list()) == topology
    downloaded = tmp_path / "merge-fault-demo.json"
    downloaded.write_text(json.dumps(topology.module_list()))
    assert topology_file(downloaded) == topology


# -- No pure two-way corridor, checked when a topology is loaded ----------------------------- #


def two_way(name: str) -> list[dict]:
    """A shipped sample as a module list with the way back of every link, as the first iteration
    seeded it, and without its roles, as the first iteration's files had them."""
    modules = shipped_topology(name).model_copy(deep=True).modules
    back = {"North": "South", "South": "North", "East": "West", "West": "East"}
    by_id = {m.id: m for m in modules}
    for module in modules:
        for c in list(module.connections):
            other = by_id[c.to]
            if not any(o.to == module.id for o in other.connections):
                other.connections.append(
                    Connection(direction=back[c.direction], to=module.id)
                )
        module.types = ["FlexConveyorModule"]
    return Topology(modules=modules).module_list()


def sources_grid() -> list[dict]:
    """The first iteration's grid, `modules_30.json`, as it seeded it: six columns and five rows,
    every module linked both ways to each neighbour, its top row entering and its bottom row
    exiting. Built here, since the shipped grid's top row no longer passes boxes sideways."""
    back = {"East": "West", "South": "North"}
    modules = [
        Module(
            id=f"Module{n}",
            label=f"Module {n}",
            types=[
                "EntryModule"
                if n <= 6
                else "ExitModule"
                if n > 24
                else "FlexConveyorModule"
            ],
        )
        for n in range(1, 31)
    ]
    for n in range(1, 31):
        for direction, other in (("East", n + 1), ("South", n + 6)):
            if (direction == "East" and n % 6 == 0) or other > 30:
                continue
            modules[n - 1].connections.append(
                Connection(direction=direction, to=f"Module{other}")
            )
            modules[other - 1].connections.append(
                Connection(direction=back[direction], to=f"Module{n}")
            )
    return Topology(modules=modules).module_list()


CORRIDOR = (
    "Module1 is a pure two-way corridor: its only two connections, to Module2 and to Module7, "
    "both run both ways. Two boxes that meet there head-on, one each way, could wait for each "
    "other forever, with no neighbour to step aside to. Let one of those connections run one "
    "way, or give Module1 a third connection."
)


def line_of_three() -> list[dict]:
    """Module 1, Module 2 and Module 3 in a row, each linked both ways to its neighbour: Module 2
    is a pure two-way corridor."""
    return Topology(
        modules=[
            Module(
                id="Module1", connections=[Connection(direction="East", to="Module2")]
            ),
            Module(
                id="Module2",
                connections=[
                    Connection(direction="West", to="Module1"),
                    Connection(direction="East", to="Module3"),
                ],
            ),
            Module(
                id="Module3", connections=[Connection(direction="West", to="Module2")]
            ),
        ]
    ).module_list()


def test_the_first_iterations_two_way_grid_is_refused_naming_a_pure_corridor(tmp_path):
    """Its corner, Module 1, links both ways to Module 2 and to Module 7 and to nothing else: a
    box from Module 2 bound for Module 7 and one from Module 7 bound for Module 2 could meet
    head-on there (Etienne, 2026-10-06, jms_kapps 43)."""
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(sources_grid()))

    with pytest.raises(ValueError) as refused:
        load_topology(str(path))

    assert str(refused.value) == CORRIDOR


@pytest.mark.parametrize("name", ["tee", "pair"])
def test_the_first_iterations_two_way_tee_and_pair_load(tmp_path, name):
    """Two-way connections and no pure corridor: the tee's middle module has three connections,
    and no module of the pair has two. (Changed on 2026-10-06, jms_kapps 43, decided by Etienne:
    "any other routed cycle loads". Until then both were refused, naming the cycle Module1 →
    Module2 → Module1.)"""
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(two_way(name)))

    topology = load_topology(str(path))

    links = {(m.id, c.to) for m in topology.modules for c in m.connections}
    assert ("Module1", "Module2") in links and ("Module2", "Module1") in links


def test_a_pure_two_way_corridor_is_refused_with_a_reason_a_person_reads(tmp_path):
    path = tmp_path / "line.json"
    path.write_text(json.dumps(line_of_three()))

    with pytest.raises(
        ValueError,
        match=(
            "^Module2 is a pure two-way corridor: its only two connections, to Module1 and to "
            "Module3, both run both ways"
        ),
    ):
        load_topology(str(path))


def test_jms_uc2_refuses_such_a_topology_before_it_connects_to_the_store(
    tmp_path, monkeypatch, capsys
):
    """`jms-uc2 --topology` stops at the load, with the reason, before it clears anything."""
    path = tmp_path / "line.json"
    path.write_text(json.dumps(line_of_three()))
    monkeypatch.setattr(
        runner, "connect", lambda dataset: pytest.fail("it connected to the store")
    )

    assert runner.run("unused", topology=str(path), force=False) == 2
    assert "Module2 is a pure two-way corridor" in capsys.readouterr().err


# -- At least one module, checked when a topology is loaded (jms_kapps 69) ------------------- #

NO_MODULE = "a topology needs at least one module"


def test_an_empty_module_list_fails_the_load_check_with_the_reason_a_person_reads(
    tmp_path,
):
    """A run on it would have the WarehouseManagementSystem and the monitor and no module to
    spawn a box on. Every way to load a topology reads the one check."""
    path = tmp_path / "empty.json"
    path.write_text("[]")

    with pytest.raises(ValueError) as from_edit:
        edited_topology([])
    with pytest.raises(ValueError) as from_file:
        topology_file(path)
    with pytest.raises(ValueError) as from_path:
        load_topology(str(path))

    assert (
        str(from_edit.value)
        == str(from_file.value)
        == str(from_path.value)
        == NO_MODULE
    )


def test_reading_an_empty_module_list_runs_no_load_check():
    """The editor opens an empty grid and an empty file, so that a person edits it until it
    passes."""
    assert parse_topology([]).modules == []
    assert parse_topology_file(b"[]").modules == []


def test_a_topology_of_one_module_passes_the_load_check(tmp_path):
    path = tmp_path / "one.json"
    path.write_text(json.dumps([module_entry("Module1")]))

    assert [m.id for m in edited_topology([module_entry("Module1")]).modules] == [
        "Module1"
    ]
    assert [m.id for m in load_topology(str(path)).modules] == ["Module1"]


def test_jms_uc2_refuses_an_empty_module_list_before_it_connects_to_the_store(
    tmp_path, monkeypatch, capsys
):
    """`jms-uc2 --topology` stops at the load, with the reason and exit code 2, before it clears
    anything."""
    path = tmp_path / "empty.json"
    path.write_text("[]")
    monkeypatch.setattr(
        runner, "connect", lambda dataset: pytest.fail("it connected to the store")
    )

    assert runner.run("unused", topology=str(path), force=False) == 2
    assert NO_MODULE in capsys.readouterr().err


def test_the_console_script_refuses_an_empty_module_list_with_exit_code_2_before_a_store(
    tmp_path,
):
    """As a person runs it, without the store's variables: a run that went on would fail on its
    first read of the store, with another message."""
    path = tmp_path / "empty.json"
    path.write_text("[]")
    without_a_store = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TRIPLESTORE_", "GRAPHDB_", "INFLUXDB_"))
    }

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "jms_kapps.uc2",
            "--topology",
            str(path),
            "--dataset",
            "ogm-disposable-jms69",
        ],
        capture_output=True,
        text=True,
        env=without_a_store,
        timeout=60,
        check=False,
    )

    assert done.returncode == 2
    assert NO_MODULE in done.stderr, done.stderr
    assert "Traceback" not in done.stderr


def test_the_cross_is_loaded_although_its_connections_hold_cycles():
    topology = load_topology("cross")
    links = {(m.id, c.to) for m in topology.modules for c in m.connections}

    assert ("Module3", "Module1") in links and ("Module1", "Module3") in links


def test_a_topology_can_be_a_file(tmp_path):
    path = tmp_path / "one.json"
    path.write_text(json.dumps([{"id": FCI_NS + "Module7", LABEL_KEY: ["Module 7"]}]))

    topology = load_topology(str(path))

    assert [(m.resource, m.label) for m in topology.modules] == [
        (module_iri(7), "Module 7")
    ]


ONE_MODULE = json.dumps([{"id": FCI_NS + "Module7"}])


def test_a_shipped_name_loads_the_shipped_topology_whatever_the_working_directory_holds(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cross").write_text(ONE_MODULE)

    assert load_topology("cross") == shipped_topology("cross")
    assert len(load_topology("cross").modules) == 5


def test_a_file_named_like_a_shipped_topology_is_reached_by_its_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cross").write_text(ONE_MODULE)

    assert [m.id for m in load_topology("./cross").modules] == ["Module7"]
    assert [m.id for m in topology_file(tmp_path / "cross").modules] == ["Module7"]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            b"Module1 -> Module2\n",
            "This file is no JSON: Expecting value: line 1 column 1",
        ),
        (b"\x89PNG\r\n", "This file is no text in UTF-8, so no JSON: 'utf-8' codec"),
    ],
)
def test_a_file_that_is_no_json_is_refused_naming_the_format_a_topology_has(
    tmp_path, content, reason
):
    """The command line and the Launcher page's upload read a file alike."""
    (tmp_path / "notes.txt").write_bytes(content)

    with pytest.raises(ValueError) as refused:
        load_topology(str(tmp_path / "notes.txt"))

    assert str(refused.value).startswith(reason)
    assert str(refused.value).endswith(TOPOLOGY_FORMAT)


def test_a_path_that_names_no_file_is_refused_and_never_read_as_a_shipped_name(
    tmp_path,
):
    with pytest.raises(ValueError, match="Nor is there a file at"):
        load_topology(str(tmp_path / "cross"))


def test_a_shipped_topology_is_only_ever_one_the_package_ships(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mine.json").write_text(ONE_MODULE)

    for name in ("mine", "mine.json", str(tmp_path / "mine.json")):
        with pytest.raises(ValueError, match="no shipped topology"):
            shipped_topology(name)


ROLES = f"{FC_NS}FlexConveyorModule, {FC_NS}EntryModule, {FC_NS}ExitModule"


@pytest.mark.parametrize(
    ("modules", "reason"),
    [
        ([module_entry("A"), module_entry("A")], "a module id is used twice: A"),
        (
            [module_entry("A", links=[("East", "Z", 0)])],
            "A connects to Z, which is not in the topology",
        ),
        (
            [
                module_entry("A", links=[("East", "B", 0), ("East", "C", 0)]),
                module_entry("B"),
                module_entry("C"),
            ],
            "A has two connections to the East",
        ),
        (
            [module_entry("A", links=[("Up", "A", 0)])],
            (
                f'A: its connection at index 0: "{FC_NS}Up" is no direction: it is one of '
                f"{FC_NS}North, {FC_NS}East, {FC_NS}South, {FC_NS}West"
            ),
        ),
        (
            [{"id": FCI_NS + "not an id"}],
            (
                f'The module at index 0 has the id "{FCI_NS}not an id": the id of a module is its '
                f'IRI in {FCI_NS}, with a local name that begins with a letter or "_" and holds '
                f'only letters, digits, "_" and "-", such as "{FCI_NS}Module1".'
            ),
        ),
        (
            [{"id": "Module1"}],
            'The module at index 0 has the id "Module1": the id of a module is its IRI in',
        ),
        (
            [module_entry("WarehouseManagementSystem1")],
            "WarehouseManagementSystem1 is the WarehouseManagementSystem's",
        ),
        (
            [module_entry("A", "Robot")],
            f'A: "{FC_NS}Robot" is no type of a module: it is one of {ROLES}',
        ),
        (
            [{**module_entry("A"), "type": "EntryModule"}],
            (
                f'A: "type" is no key of a module, which has "id", "{TYPE_KEY}", "{LABEL_KEY}", '
                f'"{TRANSIT_TIME_KEY}", "{CONNECTION_KEY}"'
            ),
        ),
        (
            [module_entry("A", label="")],
            'A: its label is a string that is not empty, not ""',
        ),
        (
            [module_entry("A", links=[("East", "B", "1")]), module_entry("B")],
            'A: its connection at index 0: its port is a whole number, not "1"',
        ),
        (
            [{"id": FCI_NS + "A", CONNECTION_KEY: [{DIRECTION_KEY: [FC_NS + "East"]}]}],
            f'A: its connection at index 0: it has no "{CONNECTS_TO_KEY}"',
        ),
        ([1], "The module at index 0 is a JSON number, not a JSON object."),
        ([[]], "The module at index 0 is a JSON list, not a JSON object."),
        (
            [{"id": FCI_NS + "A", TYPE_KEY: FC_NS + "EntryModule"}],
            f'A: "{TYPE_KEY}" holds a list of values, not "{FC_NS}EntryModule"',
        ),
        (
            [{"id": FCI_NS + "Module1\n"}],
            (
                f'The module at index 0 has the id "{FCI_NS}Module1\\n": the id of a module is '
                "its IRI in"
            ),
        ),
    ],
)
def test_a_topology_that_cannot_be_seeded_is_refused_naming_why(modules, reason):
    with pytest.raises(ValueError) as refused:
        parse_topology(modules)

    assert str(refused.value).startswith(reason)


def test_a_module_may_take_the_monitors_resource_which_only_the_launcher_reserves():
    """A run without the demo Launcher starts no monitor, so the topology model leaves its
    resource free; the Launcher refuses it itself (jms_kapps 31)."""
    topology = parse_topology([module_entry("Monitor1")])

    assert [m.resource for m in topology.modules] == [MONITOR]


def test_a_file_that_cannot_be_seeded_is_refused_in_the_load_checks_own_words(tmp_path):
    """`jms-uc2 --topology` prints the reason as the Launcher page shows it, without the
    parser's report around it."""
    path = tmp_path / "twice.json"
    path.write_text(json.dumps([module_entry("A"), module_entry("A")]))

    with pytest.raises(ValueError) as refused:
        load_topology(str(path))

    assert str(refused.value) == "a module id is used twice: A"


def test_an_unknown_topology_is_refused_with_the_known_ones():
    with pytest.raises(ValueError, match="no shipped topology 'ring'.*cross"):
        load_topology("ring")


# -- The format: the first iteration's module list -------------------------------------------- #


def test_a_module_listed_with_two_types_has_both_in_the_graph():
    """The first iteration's seed kept the first type and dropped the rest."""
    topology = parse_topology(
        [
            module_entry(
                "Module1",
                "FlexConveyorModule",
                "EntryModule",
                links=[("South", "Module2", 0)],
            ),
            module_entry("Module2", "ExitModule"),
        ]
    )

    graph = topology.graph(0)

    assert set(graph.objects(URIRef(module_iri(1)), RDF.type)) == {
        URIRef(FC.FlexConveyorModule),
        URIRef(FC.EntryModule),
    }


def test_a_one_way_connection_stays_one_way():
    """The first iteration's seed added the way back of every link."""
    topology = parse_topology(
        [
            module_entry("Module1", links=[("East", "Module2", 0)]),
            module_entry("Module2"),
        ]
    )

    graph = topology.graph(0)

    assert list(graph.subject_objects(URIRef(FC.hasConnection))) == [
        (URIRef(module_iri(1)), URIRef(FCI_NS + "Module1_connection_East"))
    ]


def test_a_module_that_lists_no_type_is_a_flexconveyor_module_as_the_first_iteration_seeded_it():
    assert parse_topology([module_entry("Module1")]).modules[0].types == [
        "FlexConveyorModule"
    ]


def test_a_label_is_optional_and_without_one_a_module_is_called_by_its_local_name():
    topology = parse_topology(
        [module_entry("Module1", label="In"), module_entry("Module2")]
    )

    assert [(m.label, m.name) for m in topology.modules] == [
        ("In", "In"),
        (None, "Module2"),
    ]
    graph = topology.graph(0)
    assert graph.value(URIRef(module_iri(1)), RDFS.label) == Literal("In")
    assert graph.value(URIRef(module_iri(2)), RDFS.label) is None


def test_references_may_be_written_as_ids_and_a_port_may_be_left_out_as_the_first_iteration_did():
    """`modules_30.json` writes each reference as `{"id": ...}`, gives each connection an id of its
    own, which the seed does not take, and gives no port."""
    topology = parse_topology(
        [
            {
                "id": FCI_NS + "Module1",
                TYPE_KEY: [{"id": FC_NS + "EntryModule"}],
                CONNECTION_KEY: [
                    {
                        "id": FCI_NS + "Connection1",
                        CONNECTS_TO_KEY: [{"id": FCI_NS + "Module2"}],
                        DIRECTION_KEY: [{"id": FC_NS + "South"}],
                    }
                ],
            },
            module_entry("Module2", "ExitModule"),
        ]
    )

    assert topology.modules[0].types == ["EntryModule"]
    assert topology.modules[0].connections == [
        Connection(direction="South", to="Module2", port=0)
    ]


@pytest.mark.parametrize(
    ("document", "found"),
    [
        (
            {
                "modules": [
                    {"id": "Module1", "label": "Module 1", "type": "EntryModule"}
                ]
            },
            (
                'This is a JSON object with its modules under "modules", the short topology '
                "document, which use case 2 reads no longer."
            ),
        ),
        ({"Module1": {}}, "This is a JSON object, not a list of modules."),
        ("cross", "This is a JSON string, not a list of modules."),
    ],
)
def test_anything_but_a_module_list_is_refused_naming_the_format(document, found):
    with pytest.raises(ValueError) as refused:
        parse_topology(document)

    assert str(refused.value) == (
        f"{found} A topology is a JSON list of modules, as the first iteration wrote them: each "
        f'module a JSON object with its "id", an IRI in {FCI_NS}, its types under "{TYPE_KEY}", '
        f'an optional label under "{LABEL_KEY}", an optional transit time in seconds under '
        f'"{TRANSIT_TIME_KEY}", and its connections under "{CONNECTION_KEY}", each one a JSON '
        f'object with the module it leads to under "{CONNECTS_TO_KEY}", its direction under '
        f'"{DIRECTION_KEY}" and its port under "{PORT_KEY}". Each value is a list.'
    )


def test_a_file_in_the_short_format_is_refused_by_jms_uc2_naming_the_format(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "short.json"
    path.write_text(
        json.dumps(
            {"modules": [{"id": "Module1", "label": "Module 1", "connections": []}]}
        )
    )
    monkeypatch.setattr(
        runner, "connect", lambda dataset: pytest.fail("it connected to the store")
    )

    assert runner.run("unused", topology=str(path), force=False) == 2
    refusal = capsys.readouterr().err
    assert "the short topology document, which use case 2 reads no longer" in refusal
    assert "A topology is a JSON list of modules" in refusal


def test_an_edited_topology_is_read_as_a_file_is():
    assert edited_topology(
        json.loads(FIRST_ITERATIONS_CROSS.read_text())
    ) == load_topology(str(FIRST_ITERATIONS_CROSS))


@pytest.mark.parametrize("name", topology_names())
def test_a_topology_written_as_a_module_list_reads_back_as_it_was(name):
    topology = shipped_topology(name)

    assert parse_topology(json.loads(json.dumps(topology.module_list()))) == topology


def test_the_first_iterations_cross_runs_as_the_shipped_cross_without_its_labels():
    """The shipped cross names no roles, where the first iteration's names two entry and two
    exit modules. (Changed on 2026-10-06, jms_kapps 45, decided by Etienne: the shipped cross
    names no roles. Until then the two differed only in the labels.)"""
    first = load_topology(str(FIRST_ITERATIONS_CROSS))
    shipped = shipped_topology("cross")
    its_roles = [
        ["EntryModule"],
        ["EntryModule"],
        ["FlexConveyorModule"],
        ["ExitModule"],
        ["ExitModule"],
    ]

    assert [m.types for m in shipped.modules] == [["FlexConveyorModule"]] * 5
    assert first == Topology(
        modules=[
            m.model_copy(update={"label": None, "types": types})
            for m, types in zip(shipped.modules, its_roles, strict=True)
        ]
    )


# -- The transit time of a module (jms_kapps 8) ------------------------------------------------- #


def test_a_module_list_may_give_a_module_its_own_transit_time_and_writes_it_back():
    modules = [
        module_entry("A", "EntryModule", transit_time=1.5, links=[("East", "B")]),
        module_entry("B", "ExitModule", transit_time=0),
        module_entry("C"),
    ]

    topology = parse_topology(modules)

    assert [m.transit_seconds for m in topology.modules] == [1.5, 0.0, None]
    written = topology.module_list()
    assert [m.get(TRANSIT_TIME_KEY) for m in written] == [[1.5], [0.0], None]
    assert parse_topology(json.loads(json.dumps(written))) == topology


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ([-1], "A: its transit time"),
        (["3"], "A: its transit time"),
        ([True], "A: its transit time"),
        ([None], "A: its transit time"),
        ([float("nan")], "A: its transit time"),
        ([1, 2], f'A: "{TRANSIT_TIME_KEY}" holds one value, not 2'),
    ],
)
def test_a_transit_time_that_is_no_number_is_negative_or_many_is_refused_naming_the_module(
    values, reason
):
    entry = {**module_entry("A"), TRANSIT_TIME_KEY: values}

    with pytest.raises(ValueError) as refused:
        parse_topology([entry])

    assert str(refused.value).startswith(reason)


def test_a_module_may_not_list_its_transit_time_outside_a_list():
    entry = {**module_entry("A"), TRANSIT_TIME_KEY: 2}

    with pytest.raises(ValueError, match='A: ".*" holds a list of values, not 2'):
        parse_topology([entry])


def test_the_graph_gives_each_module_one_transit_time_its_own_or_the_launchs():
    """The launch of the cross at 0.5 s, where the topology gives Module3 its own 2 s."""
    cross = load_topology("cross")
    modules = [
        m.model_copy(update={"transit_seconds": 2.0}) if m.id == "Module3" else m
        for m in cross.modules
    ]
    topology = Topology(modules=modules)

    graph = topology.graph(transit_seconds=0.5)

    times = {
        str(s): [o.toPython() for o in graph.objects(s, URIRef(FC.hasTransitTime))]
        for s in {URIRef(m.resource) for m in topology.modules}
    }
    assert {str(module_iri(n)): times[str(module_iri(n))] for n in range(1, 6)} == {
        str(module_iri(1)): [Decimal("0.5")],
        str(module_iri(2)): [Decimal("0.5")],
        str(module_iri(3)): [Decimal("2.0")],
        str(module_iri(4)): [Decimal("0.5")],
        str(module_iri(5)): [Decimal("0.5")],
    }
    assert all(
        o.datatype == XSD.decimal
        for o in graph.objects(None, URIRef(FC.hasTransitTime))
    )
