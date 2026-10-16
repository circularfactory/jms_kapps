"""The next module of a box: the first module on a shortest path to its destination, by hop count
over the connections, with a tie going to the lowest IRI."""

from __future__ import annotations

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.routing import (
    detour,
    next_module,
    next_module_choices,
    pure_corridor,
    reachable,
)
from jms_kapps.uc2.topology import choices, shipped_topology
from jms_kapps.uc2.vocabulary import FCI_NS, module_iri

M1, M2, M3, M4, M5, M6 = (module_iri(n) for n in range(1, 7))


def cross() -> set:
    """The connections of the shipped 5-module cross, as the seed writes them."""
    return {
        (module.resource, module_iri(int(connection.to.removeprefix("Module"))))
        for module in shipped_topology("cross").modules
        for connection in module.connections
    }


def test_on_the_cross_every_entry_goes_through_module_3_to_either_exit():
    connections = cross()

    for entry in (M1, M2):
        for exit_ in (M4, M5):
            assert next_module(connections, entry, exit_) == M3
    assert next_module(connections, M3, M4) == M4
    assert next_module(connections, M3, M5) == M5


def test_the_shortest_path_wins_over_a_longer_one():
    connections = {(M1, M2), (M2, M3), (M3, M4), (M1, M4)}

    assert next_module(connections, M1, M4) == M4


def test_a_tie_goes_to_the_lowest_iri():
    connections = {(M1, M3), (M1, M2), (M2, M4), (M3, M4)}

    assert next_module(connections, M1, M4) == M2


def test_a_tie_is_decided_by_the_iri_whatever_the_order_the_connections_come_in():
    forward = [(M1, M2), (M1, M3), (M2, M4), (M3, M4)]

    assert next_module(forward, M1, M4) == next_module(reversed(forward), M1, M4) == M2


def test_a_connection_leads_one_way_only():
    assert next_module({(M2, M1)}, M1, M2) is None


def test_with_no_path_there_is_no_next_module():
    assert next_module({(M1, M2), (M3, M4)}, M1, M4) is None


def test_a_module_does_not_route_to_itself():
    assert next_module({(M1, M2)}, M1, M1) is None


# -- Never straight back ----------------------------------------------------------------------- #


def test_a_box_never_goes_straight_back_to_the_module_it_came_from():
    """Two ways lead from Module 2 to Module 4: back over Module 1, which the box came from, and
    on over Module 3. A path that runs back through Module 1 counts for nothing."""
    connections = {(M1, M2), (M2, M1), (M1, M4), (M2, M3), (M3, M5), (M5, M4)}

    assert next_module_choices(connections, M2, M4) == [M1]
    assert next_module_choices(connections, M2, M4, avoid=[M1]) == [M3]


# -- A detour ----------------------------------------------------------------------------------- #


def test_the_detour_is_the_module_the_next_module_ties_with():
    """A diamond: Module 1 hands boxes to Module 2 and to Module 3, and both to Module 4. The next
    module is Module 2, the lowest IRI; the detour is Module 3."""
    connections = {(M1, M2), (M1, M3), (M2, M4), (M3, M4)}

    assert detour(connections, M1, M4, avoid=[M2]) == M3


def test_a_detour_may_be_longer_than_the_shortest_path():
    """Module 2 is next; Module 3 leads there in two hops more, and is the detour."""
    connections = {(M1, M2), (M2, M4), (M1, M3), (M3, M5), (M5, M4)}

    assert detour(connections, M1, M4, avoid=[M2]) == M3


def test_of_two_detours_the_one_nearer_the_destination_wins_and_then_the_lowest_iri():
    connections = {(M1, M2), (M2, M6), (M1, M3), (M3, M5), (M5, M6), (M1, M4), (M4, M6)}

    assert detour(connections, M1, M6, avoid=[M2]) == M4
    assert detour(connections, M1, M6, avoid=[M2, M4]) == M3


def test_a_detour_is_never_the_module_the_box_came_from():
    connections = {(M1, M2), (M2, M1), (M2, M3), (M1, M3)}

    assert detour(connections, M2, M3, avoid=[M3, M1]) is None


def test_a_neighbour_from_which_the_destination_is_reached_only_back_through_here_is_no_detour():
    """On the cross every path runs over Module 3: from Module 3, a step to Module 2 or Module 5
    would only lead back to Module 3."""
    connections = cross()

    assert detour(connections, M3, M4, avoid=[M4, M1]) is None


def test_a_neighbour_from_which_no_path_leads_to_the_destination_is_no_detour():
    connections = {(M1, M2), (M2, M4), (M1, M3)}

    assert detour(connections, M1, M4, avoid=[M2]) is None


# -- A pure two-way corridor -------------------------------------------------------------------- #


def test_a_module_whose_only_two_connections_both_run_both_ways_is_a_pure_corridor():
    """A line of three, two-way: a box from Module 1 to Module 3 and one from Module 3 to Module 1
    meet head-on on Module 2, and neither has a neighbour to step aside to."""
    connections = {(M1, M2), (M2, M1), (M2, M3), (M3, M2)}

    assert pure_corridor(connections) == (M2, M1, M3)


def test_two_modules_that_hand_boxes_to_each_other_make_no_pure_corridor():
    assert pure_corridor({(M1, M2), (M2, M1)}) is None


def test_a_third_connection_or_one_that_runs_one_way_makes_no_pure_corridor():
    line = {(M1, M2), (M2, M1), (M2, M3), (M3, M2)}

    assert pure_corridor(line | {(M2, M4)}) is None
    assert pure_corridor(line - {(M3, M2)}) is None


def test_the_cross_and_the_shipped_one_way_samples_hold_no_pure_corridor():
    assert pure_corridor(cross()) is None
    for name in ("pair", "tee", "grid"):
        topology = shipped_topology(name)
        connections = {
            (m.resource, module_iri(int(c.to.removeprefix("Module"))))
            for m in topology.modules
            for c in m.connections
        }
        assert pure_corridor(connections) is None, name


def test_of_several_pure_corridors_the_one_of_the_lowest_iri_is_named():
    connections = {
        (a, b)
        for x, y in ((M1, M2), (M2, M3), (M3, M4), (M4, M5))
        for a, b in ((x, y), (y, x))
    }

    assert pure_corridor(connections) == (M2, M1, M3)


# -- Reachability (jms_kapps 51) ---------------------------------------------------------------- #


def test_a_module_reaches_every_module_its_connections_lead_to_one_way_as_listed():
    connections = {(M1, M2), (M2, M3), (M4, M3), (M3, M5), (M5, M3)}

    assert reachable(connections, M1) == {M2, M3, M5}
    assert reachable(connections, M2) == {M3, M5}
    assert reachable(connections, M4) == {M3, M5}
    assert reachable(connections, M3) == {M3, M5}
    assert reachable(connections, M6) == set()


def test_a_module_reaches_itself_only_over_a_cycle():
    assert M1 not in reachable({(M1, M2)}, M1)
    assert M1 in reachable({(M1, M2), (M2, M1)}, M1)


@pytest.mark.parametrize("name", ["cross", "grid", "tee", "pair", "merge-fault-demo"])
def test_on_each_shipped_topology_every_origin_reaches_every_destination_but_itself(
    name,
):
    """Every pick the WarehouseManagementSystem makes today stays possible: from every module a
    box may start on, a path leads to every module it may end on, other than the origin."""
    topology = shipped_topology(name)
    connections = {
        (m.resource, IRI(c.to, base=FCI_NS))
        for m in topology.modules
        for c in m.connections
    }
    origins, destinations = choices({m.resource: m.types for m in topology.modules})

    for origin in origins.modules:
        others = set(destinations.modules) - {origin}
        assert others <= reachable(connections, origin), origin
