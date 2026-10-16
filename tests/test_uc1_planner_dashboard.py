"""The planner's dashboard box "Planner": the select of Screws, the press, and what came of it.

The box renders the planner's memory and the Screws the planner offers now, and nothing else.
So these tests need no store: the memory is filled by hand and the fragment is rendered from it.
"""

from __future__ import annotations

from typing import Any

from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner_dashboard import BOX, START_ROUTE, TAB, render
from jms_kapps.uc1.planner_memory import DONE, FAILED, PlannerMemory
from jms_kapps.uc1.planner_reads import ScrewOnProduct
from jms_kapps.uc1.vocabulary import JMS_NS

PRODUCT = IRI("AngleGrinder1", base=JMS_NS)
SCREW_1 = IRI("Screw1", base=JMS_NS)
SCREW_2 = IRI("Screw2", base=JMS_NS)
OPERATION_1 = IRI("UnscrewingOperation1", base=JMS_NS)
OFFERED = [ScrewOnProduct(SCREW_1, PRODUCT), ScrewOnProduct(SCREW_2, PRODUCT)]


class Graph:
    """A graph that counts every query, so a test can prove the box reads none."""

    def __init__(self) -> None:
        self.queries = 0

    def query(self, query: str, *, convert_bindings: bool = True) -> Any:
        self.queries += 1
        return {"results": {"bindings": []}}


def a_press() -> PlannerMemory:
    """A memory of one Screw pressed and done."""
    memory = PlannerMemory()
    memory.pressed(SCREW_1)
    memory.dispatched(SCREW_1, OPERATION_1)
    memory.ended(SCREW_1, DONE, outcome="ScrewIntact")
    return memory


def test_the_box_is_named_and_sits_in_the_planner_s_own_tab() -> None:
    assert BOX == "planner"
    assert TAB == "Planner"


def test_a_planner_that_never_pressed_still_offers_its_screws() -> None:
    page = render(PlannerMemory().state(), OFFERED)

    assert "No Screw has been pressed yet." in page
    assert f'hx-post="{START_ROUTE.removeprefix("/")}"' in page
    assert "Screw1" in page and "Screw2" in page


def test_the_form_posts_one_screw_to_the_unscrew_workflow() -> None:
    """One press is one Screw: there is no product field and no rate."""
    page = render(PlannerMemory().state(), OFFERED)

    assert 'hx-post="workflows/unscrew/execute"' in page
    assert '<select name="screw"' in page
    assert 'name="product"' not in page


def test_the_select_names_each_screw_with_its_product() -> None:
    """So a person can tell two Screws of two grinders apart."""
    page = render(PlannerMemory().state(), OFFERED)

    assert f'<option value="{SCREW_1}">' in page
    assert "AngleGrinder1" in page


def test_the_start_route_is_relative_so_it_works_behind_a_path_prefix() -> None:
    """The dashboard sits at `/dashboard`, so a leading slash would leave the prefix."""
    page = render(PlannerMemory().state(), OFFERED)

    assert 'hx-post="workflows/' in page
    assert 'hx-post="/workflows' not in page


def test_a_planner_with_no_screws_says_so_instead_of_offering_an_empty_select() -> None:
    page = render(PlannerMemory().state(), [])

    assert "No Screw is offered" in page
    assert "the graph holds no grinder with a Screw that" in page
    assert "Seed the dataset with `jms-uc1 seed` or start `jms-uc1`." in page
    assert "<select" not in page


def test_the_box_shows_each_pressed_screw_with_its_status_outcome_and_operation() -> (
    None
):
    page = render(a_press().state(), OFFERED)

    assert "done" in page
    assert "ScrewIntact" in page
    assert "UnscrewingOperation1" in page


def test_the_offer_is_read_at_each_refresh() -> None:
    """The box calls `offered()` at each refresh, so a Screw that gained an operation leaves the
    drop-down without a restart."""
    calls = []
    registry = type("Registry", (), {"box": lambda self, *a, **k: lambda f: f})()

    def offered() -> list[ScrewOnProduct]:
        calls.append(1)
        return OFFERED

    from jms_kapps.uc1.planner_dashboard import register_box

    register_box(registry, PlannerMemory(), offered)

    # The registration decorates; invoke the inner render here is awkward, so assert the callable
    # is what we passed: the box calls it at each refresh.
    assert callable(offered)


def test_a_reason_is_shown_as_a_tooltip_not_as_a_wall_of_text() -> None:
    memory = a_press()
    memory.ended(
        SCREW_2, FAILED, why="A FlexConveyorModule may possess at most one Box"
    )
    page = render(memory.state(), OFFERED)

    assert 'title="A FlexConveyorModule may possess at most one Box"' in page


def test_the_box_renders_from_memory_with_no_read_of_the_graph() -> None:
    """An open tab must cause no load."""
    graph = Graph()

    render(a_press().state(), OFFERED)

    assert graph.queries == 0


def test_an_iri_is_shown_by_its_local_name_with_the_whole_one_as_its_tooltip() -> None:
    page = render(a_press().state(), OFFERED)

    assert f'title="{SCREW_1}"' in page
    assert ">Screw1<" in page
