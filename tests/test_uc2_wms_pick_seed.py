"""The WarehouseManagementSystem's pick takes a seed (jms_kapps 93, decided by Sören on jms_kapps
85): the same seed and topology give the same sequence of picks, for each strategy, `real` and
`southward`. Offline, on the shipped grid as a fake topology: a station whose modules refuse every
Reserve, so that a spawn ends with its pick in the answer and the world stays as it was.
"""

from __future__ import annotations

import random

import pytest
from _station import Notifications, Reads, Records, Tasks, World
from kapps_triplestore_interface import IRI

from jms_kapps.evaluation.southward import SOUTHWARD_WMS_STRATEGY
from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.strategies import REAL_WMS_STRATEGY, WmsStrategy
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS
from jms_kapps.uc2.wms import SpawnBoxRequest, WarehouseManagementSystem, seeded_choice
from jms_kapps.uc2.wms_process import wms_command, wms_parser

GRID = load_topology("grid")
SPAWNS = 40


def grid_world() -> World:
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


def refuse_every_reserve(endpoint: str, payload: object) -> dict:
    return {"accepted": False, "reason": "occupied", "reserved": False}


def picks(strategy: WmsStrategy, name: str, seed: int | None) -> list[tuple[str, str]]:
    """The origin and the destination of each of `SPAWNS` spawns, on a fresh WMS whose chooser
    is seeded with `seed`."""
    world = grid_world()
    wms = WarehouseManagementSystem(
        WMS,
        graph=Reads(world, WMS),
        records=Records(world),
        call=refuse_every_reserve,
        notify=Notifications(world, Tasks()).notify,
        choose=seeded_choice(seed),
        events=HandoverEvents(WMS, token="wms"),
        strategy=strategy,
        strategy_name=name,
    )
    made = []
    for _ in range(SPAWNS):
        answer = wms.spawn(SpawnBoxRequest())
        assert answer.origin and answer.destination, answer.reason
        made.append((answer.origin, answer.destination))
    return made


STRATEGIES = [
    pytest.param(REAL_WMS_STRATEGY, "real", id="real"),
    pytest.param(SOUTHWARD_WMS_STRATEGY, "southward", id="southward"),
]


@pytest.mark.parametrize(("strategy", "name"), STRATEGIES)
def test_the_same_seed_and_topology_give_the_same_sequence_of_picks(strategy, name):
    first = picks(strategy, name, seed=20261012)
    second = picks(strategy, name, seed=20261012)

    assert first == second
    # The sequence is no single pick repeated: the seed fixes a sequence, not a constant.
    assert len(set(first)) > 3


@pytest.mark.parametrize(("strategy", "name"), STRATEGIES)
def test_another_seed_gives_another_sequence_of_picks(strategy, name):
    assert picks(strategy, name, seed=1) != picks(strategy, name, seed=2)


def test_a_seeded_chooser_leaves_the_shared_generator_alone_and_no_seed_is_random_choice():
    options = list(range(100))
    random.seed(0)
    before = random.random()

    [seeded_choice(5)(options) for _ in range(10)]
    random.seed(0)
    after = random.random()

    assert before == after
    assert seeded_choice(None) is random.choice


def test_one_seeded_chooser_makes_a_sequence_and_a_new_one_with_the_seed_makes_it_again():
    options = list(range(100))
    chooser = seeded_choice(11)
    again = seeded_choice(11)

    assert [chooser(options) for _ in range(20)] == [again(options) for _ in range(20)]


def test_the_wms_process_takes_the_seed_on_its_command_line_and_without_it_is_unseeded():
    assert "--pick-seed" not in wms_command("ds")
    seeded = wms_command("ds", pick_seed=42)
    assert seeded[-2:] == ["--pick-seed", "42"]
    assert wms_parser().parse_args(seeded[3:]).pick_seed == 42
    assert wms_parser().parse_args(wms_command("ds")[3:]).pick_seed is None
