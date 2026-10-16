"""The southward benchmark strategies of jms_kapps 80, offline on the shipped grid: the module
strategy asks only the southern neighbour, the WMS strategy sends each box to the exit straight
below its origin, and a spawn without such a pair ends in the WMS's refusal. Both are found
through their entry-point groups under the name `southward`.
"""

from __future__ import annotations

import logging

import pytest
from _station import Network, Notifications, Reads, Records, Tasks, World
from kapps_triplestore_interface import IRI

from jms_kapps.evaluation.southward import (
    SOUTHWARD_MODULE_STRATEGY,
    SOUTHWARD_WMS_STRATEGY,
)
from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.strategies import (
    ModuleStrategy,
    WmsStrategy,
    chosen,
    found,
)
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, box_iri, module_iri
from jms_kapps.uc2.wms import SpawnBoxRequest, WarehouseManagementSystem
from jms_kapps.uc2.wms import logger as wms_logger

M = {n: module_iri(n) for n in range(1, 31)}
GRID = load_topology("grid")
CONNECTIONS = [
    DirectedConnection(module.resource, IRI(c.to, base=FCI_NS), c.direction)
    for module in GRID.modules
    for c in module.connections
]
ENTRIES = [m.resource for m in GRID.modules if "EntryModule" in m.types]
EXITS = [m.resource for m in GRID.modules if "ExitModule" in m.types]
BOTTOM_ROW = set(EXITS)


def first(options):
    return options[0]


def column_exit(entry: IRI) -> IRI:
    """The exit in the column of `entry`: the grid has six columns, numbered 1 to 6 on the top
    row and 25 to 30 on the bottom row."""
    return M[int(entry.fragment.removeprefix("Module")) + 24]


def test_the_grid_has_six_entries_on_the_top_row_and_six_exits_on_the_bottom_row():
    assert [e.fragment for e in ENTRIES] == [f"Module{n}" for n in range(1, 7)]
    assert [e.fragment for e in EXITS] == [f"Module{n}" for n in range(25, 31)]


def test_the_wms_strategy_answers_each_entry_with_the_exit_in_its_column():
    for entry in ENTRIES:
        candidates = {entry: list(EXITS)}

        pair = SOUTHWARD_WMS_STRATEGY.origin_and_destination(
            candidates, CONNECTIONS, first
        )

        assert pair == (entry, column_exit(entry))


def test_the_wms_strategy_picks_an_origin_with_the_chooser_among_those_with_an_exit_below():
    """Entry 1's exit below is Module 25, which is not among its destinations; the chooser picks
    among Entries 2 and 3 alone."""
    candidates = {M[1]: [M[26]], M[2]: [M[26]], M[3]: [M[27], M[27]]}
    offered = []

    def last(options):
        offered.append(list(options))
        return options[-1]

    pair = SOUTHWARD_WMS_STRATEGY.origin_and_destination(candidates, CONNECTIONS, last)

    assert offered == [[M[2], M[3]]]
    assert pair == (M[3], M[27])


def test_without_an_exit_below_among_the_destinations_it_answers_the_first_candidate_origin():
    candidates = {M[2]: [M[25]], M[3]: [M[25]]}

    def never(options):
        raise AssertionError("the chooser picks nothing here")

    pair = SOUTHWARD_WMS_STRATEGY.origin_and_destination(candidates, CONNECTIONS, never)

    assert pair == (M[2], M[26])


def test_the_module_strategy_answers_the_southern_neighbour_and_none_on_the_bottom_row():
    for module in GRID.modules:
        south = [
            c.target
            for c in CONNECTIONS
            if c.source == module.resource and c.direction == "South"
        ]

        answer = SOUTHWARD_MODULE_STRATEGY.neighbours_to_ask(
            module.resource, M[30], M[1], None, CONNECTIONS
        )

        assert answer == south
        assert len(answer) == (0 if module.resource in BOTTOM_ROW else 1)


def test_the_module_strategy_ignores_the_destination_and_the_look_ahead_and_never_detours():
    plain = SOUTHWARD_MODULE_STRATEGY.neighbours_to_ask(
        M[8], M[25], None, None, CONNECTIONS
    )
    other = SOUTHWARD_MODULE_STRATEGY.neighbours_to_ask(
        M[8], M[30], M[7], M[7], CONNECTIONS
    )

    assert plain == other == [M[14]]


def test_each_strategy_is_found_through_its_entry_point_group_as_southward():
    modules = found(ModuleStrategy)
    wms = found(WmsStrategy)

    assert chosen(ModuleStrategy, "southward", modules) is SOUTHWARD_MODULE_STRATEGY
    assert chosen(WmsStrategy, "southward", wms) is SOUTHWARD_WMS_STRATEGY
    assert SOUTHWARD_MODULE_STRATEGY.label == "Southward (synthetic)"
    assert SOUTHWARD_WMS_STRATEGY.label == "Southward (synthetic)"
    assert "real" in modules and "real" in wms


@pytest.fixture
def world():
    world = World()
    world.wms_iri = WMS
    for module in GRID.modules:
        for connection in module.connections:
            to = IRI(connection.to, base=FCI_NS)
            world.connections.add((module.resource, to))
            world.directions[(module.resource, to)] = connection.direction
        for role, role_class in (
            ("EntryModule", FC.EntryModule),
            ("ExitModule", FC.ExitModule),
        ):
            if role in module.types:
                world.classes[role_class].append(module.resource)
    return world


def test_a_spawn_without_an_exit_below_among_the_destinations_is_refused_naming_the_strategy(
    world, caplog
):
    """The request names Module 26 as the destination, but Module 2, the entry straight above it,
    holds a box. Every other entry reaches Module 26, and none has it straight below: the strategy
    answers the first candidate origin, Module 1, with the exit straight below it, Module 25."""
    world.held[M[2]].append(box_iri(99))
    network = Network()
    wms = WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=Records(world),
        call=network.call,
        notify=Notifications(world, Tasks()).notify,
        choose=first,
        events=HandoverEvents(WMS, token="wms"),
        strategy=SOUTHWARD_WMS_STRATEGY,
        strategy_name="southward",
    )

    with caplog.at_level(logging.INFO, logger=wms_logger.name):
        answer = wms.spawn(SpawnBoxRequest(destination=str(M[26])))

    reason = (
        "the strategy southward chose Module1 to Module25, which is not a candidate"
    )
    assert (answer.accepted, answer.reason) == (False, reason)
    assert world.jobs == {}
