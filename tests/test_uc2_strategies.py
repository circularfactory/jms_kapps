"""The seam of jms_kapps 52: the two kinds of strategy, `real`, and the discovery of strategies from
their two entry-point groups.

A module strategy answers which neighbours a module asks for a box, in order, at most two; a WMS
strategy answers the origin and the destination of a spawn among its candidates. `real` is one
strategy of each kind, and is today's behaviour exactly. Strategies are found by two Python
entry-point groups; here a fake set of entry points stands in for the installed ones, except where
a test reads what jms_kapps itself registers. (Added on 2026-10-10, jms_kapps 52, decided by
Etienne on 2026-10-07.)
"""

from __future__ import annotations

import logging

import pytest
from _strategies import (
    EXIT_STRAIGHT_BELOW,
    REAL_ENTRIES,
    SOUTH_ONLY,
    TEST_ENTRIES,
    entry_points,
)

from jms_kapps.uc2 import strategies
from jms_kapps.uc2.discovery import DirectedConnection
from jms_kapps.uc2.strategies import (
    MODULE_STRATEGIES,
    REAL,
    REAL_MODULE_STRATEGY,
    REAL_WMS_STRATEGY,
    WMS_STRATEGIES,
    ModuleStrategy,
    UnknownStrategy,
    WmsStrategy,
    chosen,
    found,
)
from jms_kapps.uc2.vocabulary import module_iri

M1, M2, M3, M4, M5 = (module_iri(n) for n in range(1, 6))


def one_way(*pairs, direction=None) -> list[DirectedConnection]:
    return [DirectedConnection(s, t, direction) for s, t in pairs]


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == strategies.logger.name and r.levelno == logging.WARNING
    ]


# -- The two kinds, and `real` ------------------------------------------------------------------ #


def test_real_is_one_strategy_of_each_kind_labelled_real_behaviour():
    assert isinstance(REAL_MODULE_STRATEGY, ModuleStrategy)
    assert isinstance(REAL_WMS_STRATEGY, WmsStrategy)
    assert REAL == "real"
    assert REAL_MODULE_STRATEGY.label == "Real behaviour"
    assert REAL_WMS_STRATEGY.label == "Real behaviour"


def test_a_synthetic_strategy_s_label_ends_in_synthetic_and_real_s_does_not():
    assert SOUTH_ONLY.label.endswith("(synthetic)")
    assert EXIT_STRAIGHT_BELOW.label.endswith("(synthetic)")
    assert not REAL_MODULE_STRATEGY.label.endswith("(synthetic)")
    assert not REAL_WMS_STRATEGY.label.endswith("(synthetic)")


def test_real_asks_the_first_module_on_a_shortest_path_then_the_module_it_ties_with():
    """Module 1 reaches Module 4 over Module 2 or Module 3, two hops each: the lowest IRI first,
    the other as the detour."""
    connections = one_way((M1, M2), (M1, M3), (M2, M4), (M3, M4))

    assert REAL_MODULE_STRATEGY.neighbours_to_ask(M1, M4, None, None, connections) == [
        M2,
        M3,
    ]


def test_real_asks_the_module_reserved_ahead_first_while_a_connection_leads_there():
    connections = one_way((M1, M2), (M1, M3), (M2, M4), (M3, M4))

    assert REAL_MODULE_STRATEGY.neighbours_to_ask(M1, M4, None, M3, connections) == [
        M3,
        M2,
    ]
    # No connection of Module 1 leads to Module 5: the shortest path decides.
    assert REAL_MODULE_STRATEGY.neighbours_to_ask(M1, M4, None, M5, connections) == [
        M2,
        M3,
    ]


def test_real_never_asks_the_module_the_box_came_from_nor_a_step_that_only_leads_back():
    """Two-way between Module 1 and Module 2, and Module 2 on to Module 3: a box on Module 2 that
    came from Module 1 asks Module 3 alone. One on Module 1 that came from Module 2 has no way on."""
    connections = one_way((M1, M2), (M2, M1), (M2, M3))

    assert REAL_MODULE_STRATEGY.neighbours_to_ask(M2, M3, M1, None, connections) == [M3]
    assert REAL_MODULE_STRATEGY.neighbours_to_ask(M1, M3, M2, None, connections) == []


def test_real_has_no_detour_where_the_only_other_neighbour_leads_back():
    connections = one_way((M1, M2), (M2, M3), (M2, M4), (M4, M2))

    assert REAL_MODULE_STRATEGY.neighbours_to_ask(M2, M3, M1, None, connections) == [M3]


def test_real_lets_the_chooser_pick_the_origin_then_the_destination_among_its_own():
    chosen_from: list[list] = []

    def last(options):
        chosen_from.append(list(options))
        return options[-1]

    candidates = {M1: [M3, M4], M2: [M4, M5]}

    assert REAL_WMS_STRATEGY.origin_and_destination(candidates, [], last) == (M2, M5)
    assert chosen_from == [[M1, M2], [M4, M5]]


# -- Finding strategies ------------------------------------------------------------------------- #


def test_jms_kapps_registers_real_once_in_each_group():
    """What the installed jms_kapps registers, read through the real entry points."""
    assert found(ModuleStrategy)[REAL] is REAL_MODULE_STRATEGY
    assert found(WmsStrategy)[REAL] is REAL_WMS_STRATEGY


def test_strategies_are_found_by_their_group_and_named_by_their_entry(caplog):
    fake = entry_points(REAL_ENTRIES, TEST_ENTRIES)

    assert found(ModuleStrategy, fake) == {
        "real": REAL_MODULE_STRATEGY,
        "south": SOUTH_ONLY,
    }
    assert found(WmsStrategy, fake) == {
        "real": REAL_WMS_STRATEGY,
        "exit-below": EXIT_STRAIGHT_BELOW,
    }
    assert warnings(caplog) == []


def test_an_entry_whose_module_cannot_be_imported_is_skipped_with_one_warning_naming_it(
    caplog,
):
    """As with the `evaluation` folder removed: `real` is still found."""
    fake = entry_points(
        REAL_ENTRIES,
        {MODULE_STRATEGIES: [("gone", "jms_kapps.evaluation.no_such_module:STRATEGY")]},
    )

    assert found(ModuleStrategy, fake) == {"real": REAL_MODULE_STRATEGY}
    [warning] = warnings(caplog)
    assert "module strategy gone" in warning
    assert "jms_kapps.evaluation.no_such_module" in warning


def test_an_object_of_the_other_kind_is_skipped_with_one_warning_naming_it(caplog):
    fake = entry_points(
        REAL_ENTRIES,
        {WMS_STRATEGIES: [("south", "_strategies:SOUTH_ONLY")]},
    )

    assert found(WmsStrategy, fake) == {"real": REAL_WMS_STRATEGY}
    [warning] = warnings(caplog)
    assert "WMS strategy south" in warning
    assert "SouthOnly" in warning


def test_of_two_strategies_of_one_kind_with_one_name_the_first_found_counts(caplog):
    fake = entry_points(
        REAL_ENTRIES,
        {MODULE_STRATEGIES: [("real", "_strategies:SOUTH_ONLY")]},
    )

    assert found(ModuleStrategy, fake) == {"real": REAL_MODULE_STRATEGY}
    [warning] = warnings(caplog)
    assert "module strategy real" in warning
    assert "_strategies:SOUTH_ONLY" in warning


def test_one_name_may_name_a_strategy_of_each_kind():
    fake = entry_points(REAL_ENTRIES)

    assert set(found(ModuleStrategy, fake)) == set(found(WmsStrategy, fake)) == {REAL}


# -- Choosing one by its name ------------------------------------------------------------------- #


def test_a_strategy_is_chosen_by_its_name():
    among = found(ModuleStrategy, entry_points(REAL_ENTRIES, TEST_ENTRIES))

    assert chosen(ModuleStrategy, "south", among) is SOUTH_ONLY


def test_an_unknown_name_is_refused_naming_it_and_the_strategies_of_its_kind():
    among = found(ModuleStrategy, entry_points(REAL_ENTRIES, TEST_ENTRIES))

    with pytest.raises(UnknownStrategy) as refused:
        chosen(ModuleStrategy, "nosuch", among)

    assert str(refused.value) == (
        "no module strategy 'nosuch'; the module strategies are real, south"
    )


def test_an_unknown_wms_strategy_names_the_wms_strategies():
    among = found(WmsStrategy, entry_points(REAL_ENTRIES))

    with pytest.raises(UnknownStrategy) as refused:
        chosen(WmsStrategy, "nosuch", among)

    assert str(refused.value) == (
        "no WMS strategy 'nosuch'; the WMS strategies are real"
    )
