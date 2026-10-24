"""The planner's dashboard box "Planner", on its own dashboard, in the tab "Planner".

The dashboard is the middleware's: a dashboard box is a render function that returns an HTML
fragment, and the page loads each one on its own. The box renders what the planner holds in memory
(`planner_memory`) and its current offer, and nothing else: no read of the graph, so an open tab
causes no load. It carries no JavaScript of its own.

A person presses Unscrew from here, by a form that posts one Screw to the planner's own `unscrew`
workflow. That is a post to a workflow of the instance that serves the page, which is the one write
a box may make. The Screws it offers are those the planner's `Offer` holds now; the box reads
nothing itself, and the offer's refresh is what keeps the drop-down current.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from kapps_semantic_middleware.dashboard import (
    DashboardRegistry,
    escape,
    hint,
    kv_table,
)
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner_memory import (
    DONE,
    FAILED,
    REFUSED,
    TIMED_OUT,
    WAITING,
    PlannerMemory,
    PlannerState,
    ScrewEntry,
)
from jms_kapps.uc1.planner_reads import ScrewOnProduct

TAB = "Planner"
BOX = "planner"

# A press changes at the pace of one attempt, which takes seconds, so the box asks again often
# enough to follow one Screw from waiting to its outcome.
REFRESH_SECONDS = 5

# The planner's own workflow, which the Unscrew button posts one Screw to.
START_ROUTE = "/workflows/unscrew/execute"

# What each status says to a person, and the colour it reads as. A status the memory can hold but
# this map does not name would render as itself, so the two cannot fall silently out of step.
STATUS_WORDS: dict[str, tuple[str, str]] = {
    WAITING: ("waiting", "#6b7280"),
    "dispatched": ("under way", "#1d4ed8"),
    DONE: ("done", "#15803d"),
    FAILED: ("failed", "#b91c1c"),
    REFUSED: ("refused", "#b45309"),
    TIMED_OUT: ("timed out", "#b45309"),
}


def _from_the_dashboard(route: str) -> str:
    """`route` as the dashboard, at `/dashboard`, asks for it: relative, so that it reaches the
    planner's route at the planner's own address and behind a path prefix alike."""
    return route.removeprefix("/")


def _time(moment: datetime | None) -> str | None:
    return None if moment is None else moment.strftime("%H:%M:%S")


def _iri(value: IRI | None) -> str:
    """An IRI by its local name, with the whole one as its tooltip."""
    if value is None:
        return escape(None)
    return f'<span title="{escape(value)}">{escape(value.fragment)}</span>'


def _status(entry: ScrewEntry) -> str:
    word, colour = STATUS_WORDS.get(entry.status, (entry.status, "#6b7280"))
    cell = f'<span style="color: {colour}; font-weight: 600">{escape(word)}</span>'
    # The reason of a refusal, a failure or a timeout, where there is one.
    if entry.why:
        cell = f'{cell} <span title="{escape(entry.why)}">ⓘ</span>'
    # The store's answer to a refused operation, readable in the row: each rule its report
    # names, and what the rules say.
    for rule in entry.rules:
        cell += f'<div class="hint">by {escape(rule)}</div>'
    for message in entry.messages:
        cell += f'<div class="hint">{escape(message)}</div>'
    return cell


def _screws(state: PlannerState) -> str:
    if not state.screws:
        return hint("No Screw has been pressed yet.")
    rows = "".join(
        "<tr>"
        f"<td>{_iri(entry.screw)}</td>"
        f"<td>{_status(entry)}</td>"
        f"<td>{escape(entry.outcome)}</td>"
        f"<td>{_iri(entry.operation)}</td>"
        f"<td>{escape(_time(entry.at))}</td>"
        "</tr>"
        for entry in state.screws
    )
    return (
        '<div class="table-scroll"><table>'
        "<thead><tr><th>Screw</th><th>Status</th><th>Outcome</th>"
        "<th>Operation</th><th>Changed</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _press_form(screws: Sequence[ScrewOnProduct]) -> str:
    """The form that presses Unscrew on one Screw: a select of the Screws the planner offers now,
    and the button. A Screw that has an operation is not offered — the offer leaves it out, and a
    press on it is refused — so the form can only name an offered Screw."""
    if not screws:
        return hint(
            "No Screw is offered: the knowledge graph holds no grinder with a Screw that "
            "has no operation yet. Seed the dataset with `jms-uc1 seed` or start `jms-uc1`."
        )
    options = "".join(
        f'<option value="{escape(entry.screw)}">'
        f"{escape(entry.screw.fragment)} &mdash; {escape(entry.product.fragment)}"
        "</option>"
        for entry in screws
    )
    return (
        f'<form hx-post="{escape(_from_the_dashboard(START_ROUTE))}" hx-ext="json-enc" '
        'hx-swap="none">'
        f'<select name="screw" required style="min-width: 22rem">{options}</select>'
        '<button type="submit">Unscrew</button>'
        "</form>"
    )


def render(state: PlannerState, screws: Sequence[ScrewOnProduct] = ()) -> str:
    """The box's fragment, from what the planner knows now and the Screws it offers now."""
    counted = {
        word: sum(1 for entry in state.screws if entry.status == status)
        for status, (word, _) in STATUS_WORDS.items()
    }
    summary = kv_table(
        [
            ("Screws offered", escape(str(len(screws))) if screws else "—"),
            (
                "Pressed",
                escape(str(len(state.screws))) if state.screws else escape(None),
            ),
            (
                "By status",
                escape(", ".join(f"{n} {word}" for word, n in counted.items() if n))
                or escape(None),
            ),
        ]
    )
    return summary + _press_form(screws) + _screws(state)


def register_box(
    registry: DashboardRegistry,
    memory: PlannerMemory,
    offered: Callable[[], Sequence[ScrewOnProduct]] = lambda: (),
) -> None:
    """Register the dashboard box "Planner" in `registry`, in the tab "Planner".

    Args:
        registry: The dashboard of the planner's middleware.
        memory: What the planner knows of its presses, which the box shows.
        offered: Read the planner's current offer; the box calls it at each refresh, so a Screw
            that gained an operation leaves the drop-down without a restart.
    """

    @registry.box(BOX, title="Planner", tab=TAB, refresh_seconds=REFRESH_SECONDS)
    def planner(middleware: Any) -> str:
        return render(memory.state(), offered())
