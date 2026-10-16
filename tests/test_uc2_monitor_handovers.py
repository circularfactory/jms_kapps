"""The handovers the monitor sees: a box whose possessor changed between two of its reads, and a
box it sees for the first time on its origin, which the WarehouseManagementSystem handed it to.

The WarehouseManagementSystem is whatever the graph types as one: `WMS` here, as the seed names
it, and another IRI where a test says so.
"""

from __future__ import annotations

from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.monitor.handovers import SeenHandover, handovers_between
from jms_kapps.uc2.monitor.reads import BoxRow
from jms_kapps.uc2.vocabulary import WMS, box_iri, module_iri

SEEN = "2026-10-04T12:00:01+00:00"
THE_WMS = {WMS}


def box(n: int, on: IRI | None, status: str = OperationStatus.RUNNING) -> BoxRow:
    """Box `n` on `on`, its transport from Module 1 to Module 5."""
    return BoxRow(box_iri(n), status, module_iri(1), module_iri(5), on)


def test_a_box_whose_possessor_changed_between_two_reads_was_handed_from_the_one_to_the_other():
    before = [box(1, module_iri(1))]
    after = [box(1, module_iri(3))]

    assert handovers_between(before, after, SEEN, THE_WMS) == [
        SeenHandover(
            seen=SEEN, box=box_iri(1), giver=module_iri(1), receiver=module_iri(3)
        )
    ]


def test_a_box_whose_possessor_stayed_was_not_handed_over():
    before = [box(1, module_iri(3))]
    after = [box(1, module_iri(3), status=OperationStatus.FAILED)]

    assert handovers_between(before, after, SEEN, THE_WMS) == []


def test_the_handovers_with_the_wms_at_both_ends_are_seen_too():
    before = [box(1, WMS, OperationStatus.QUEUED), box(2, module_iri(5))]
    after = [
        box(1, module_iri(1), OperationStatus.QUEUED),
        box(2, WMS, OperationStatus.DONE),
    ]

    assert handovers_between(before, after, SEEN, THE_WMS) == [
        SeenHandover(seen=SEEN, box=box_iri(1), giver=WMS, receiver=module_iri(1)),
        SeenHandover(seen=SEEN, box=box_iri(2), giver=module_iri(5), receiver=WMS),
    ]


def test_a_box_first_seen_on_its_origin_was_handed_there_by_the_wms():
    """The WMS creates a box and hands it to its origin within a few milliseconds, so the
    monitor usually first sees the box on its origin already."""
    assert handovers_between([], [box(1, module_iri(1))], SEEN, THE_WMS) == [
        SeenHandover(seen=SEEN, box=box_iri(1), giver=WMS, receiver=module_iri(1))
    ]


def test_a_box_first_seen_anywhere_but_its_origin_was_not_seen_handed_over():
    after = [box(1, module_iri(3)), box(2, WMS, OperationStatus.QUEUED), box(3, None)]

    assert handovers_between([], after, SEEN, THE_WMS) == []


def test_a_box_that_left_the_graph_or_lost_its_possessor_was_not_handed_over():
    before = [box(1, module_iri(1)), box(2, module_iri(3)), box(3, None)]
    after = [box(2, None), box(3, module_iri(2))]

    assert handovers_between(before, after, SEEN, THE_WMS) == []


def test_every_box_handed_over_is_listed_once_in_the_order_of_the_boxes_iris():
    before = [box(2, module_iri(3)), box(1, module_iri(1)), box(3, module_iri(2))]
    after = [box(3, module_iri(3)), box(1, module_iri(3)), box(2, module_iri(4))]

    assert [
        (h.box, h.giver, h.receiver)
        for h in handovers_between(before, after, SEEN, THE_WMS)
    ] == [
        (box_iri(1), module_iri(1), module_iri(3)),
        (box_iri(2), module_iri(3), module_iri(4)),
        (box_iri(3), module_iri(2), module_iri(3)),
    ]


def test_the_wms_that_hands_a_box_to_its_origin_is_the_one_the_graph_names():
    """Found by its class in the graph, not by the IRI the seed gives it."""
    elsewhere = IRI("Warehouse", base="http://example.org/plant#")

    assert handovers_between([], [box(1, module_iri(1))], SEEN, {elsewhere}) == [
        SeenHandover(seen=SEEN, box=box_iri(1), giver=elsewhere, receiver=module_iri(1))
    ]


def test_where_the_graph_names_no_wms_or_several_a_box_first_seen_on_its_origin_was_not_seen_handed_over():
    """Who handed it there is not known then."""
    elsewhere = IRI("Warehouse", base="http://example.org/plant#")
    after = [box(1, module_iri(1))]

    assert handovers_between([], after, SEEN, set()) == []
    assert handovers_between([], after, SEEN, {WMS, elsewhere}) == []
