"""The learner's dashboard box "Learner", on its own dashboard, in the tab "Learner".

The dashboard is the middleware's: a dashboard box is a render function that returns an HTML
fragment, and the page loads each one on its own. The box renders what the learner holds in memory
(`learner_memory`) and nothing else: no read of the graph and no read of the time-series store, so
an open tab causes no load. It carries no JavaScript of its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kapps_semantic_middleware.dashboard import (
    DashboardRegistry,
    escape,
    hint,
    kv_table,
)
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.learner_memory import (
    Change,
    LastRun,
    LearnerMemory,
    LearnerState,
)

TAB = "Learner"

# The id of the dashboard box "Learner".
BOX = "learner"

# How often the box asks for itself again. It reads only memory.
REFRESH_SECONDS = 2


def _time(at: datetime) -> str:
    return at.strftime("%H:%M:%S")


def _count(count: int, thing: str) -> str:
    return f"{count} {thing}" if count == 1 else f"{count} {thing}s"


def _last_run(run: LastRun | None) -> str:
    if run is None:
        return "none yet"
    if run.failed:
        return f"{_time(run.at)}, failed: see the last error"
    return (
        f"{_time(run.at)}, learned from {_count(run.learned, 'new attempt')}, "
        f"changed {_count(run.changed, 'value')}"
    )


def _next_run(state: LearnerState) -> str:
    if state.running:
        when = "under way now"
    elif state.next_run is None:
        when = "not scheduled"
    else:
        when = _time(state.next_run)
    if state.every_seconds is None:
        return when
    return f"{when}, every {state.every_seconds:g} s"


def _summary(state: LearnerState) -> str:
    """How the last run ended, when the next comes, and how many attempts it learned from."""
    learned = str(state.learned)
    if state.learned and state.last_run is not None and not state.last_run.failed:
        learned += f", {state.last_run.learned} of them new in the last run"
    rows = [
        ("Last run", _last_run(state.last_run)),
        ("Next run", _next_run(state)),
        ("Attempts learned from", learned),
    ]
    if state.passed_over:
        rows.append(
            ("Passed over", f"{state.passed_over}, whose curves could not be read")
        )
    return kv_table(rows)


def _heading(text: str) -> str:
    """The title of a part of the box."""
    return f'<h3 style="margin: 16px 0 6px; font-size: 0.9rem">{escape(text)}</h3>'


def _short(iri: IRI | None) -> str:
    """`iri` shortened to its local name, in full when the pointer rests on it; a dash if none."""
    if iri is None:
        return escape(None)
    return f'<span title="{escape(iri)}">{escape(iri.fragment)}</span>'


def _value(value: float | None) -> str:
    return escape(None) if value is None else f"{value:g}"


def _row(change: Change) -> str:
    cells = [
        escape(_time(change.at)),
        _short(change.screw_type),
        _short(change.threshold),
        f"{_value(change.old)} → {_value(change.new)}",
    ]
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _changes(state: LearnerState) -> str:
    """Each value the learner changed, newest first: when, on which Screw type, which threshold,
    and the value the Screw type held before and after."""
    rule = hint(
        "The rule is a placeholder. The first value it derives for a Screw type replaces the "
        "value the type held, higher or lower. After that the value only rises."
    )
    if not state.changes:
        return (
            _heading("Values it changed") + rule + hint("It has changed no value yet.")
        )
    newest = ""
    if len(state.changes) < state.change_count:
        newest = hint(
            f"The newest {len(state.changes)} of {state.change_count} changes, newest first."
        )
    header = "".join(
        f"<th>{escape(title)}</th>"
        for title in ("When", "Screw type", "Threshold", "Old → new")
    )
    body = "".join(_row(change) for change in state.changes)
    return (
        _heading("Values it changed")
        + rule
        + newest
        + f'<div class="table-scroll"><table><tr>{header}</tr>{body}</table></div>'
    )


def _last_error(state: LearnerState) -> str:
    """The last thing that went wrong: when, at which Operation's attempt, and why."""
    error = state.last_error
    if error is None:
        return _heading("Last error") + hint("No error since the learner started.")
    rows = [
        ("When", escape(_time(error.at))),
        ("Operation", _short(error.operation)),
        ("Why", escape(error.why)),
    ]
    table = "".join(f"<tr><th>{label}</th><td>{cell}</td></tr>" for label, cell in rows)
    return (
        _heading("Last error")
        + f'<div class="error-box"><table class="kv">{table}</table></div>'
    )


def render(state: LearnerState) -> str:
    """The fragment of the box "Learner" for `state`."""
    return _summary(state) + _changes(state) + _last_error(state)


def register_box(registry: DashboardRegistry, memory: LearnerMemory) -> None:
    """Register the dashboard box "Learner" in `registry`, in the tab "Learner".

    Args:
        registry: The dashboard of the learner's middleware.
        memory: What the learner knows of its own runs, which the box shows.
    """

    @registry.box(BOX, title="Learner", tab=TAB, refresh_seconds=REFRESH_SECONDS)
    def learner(middleware: Any) -> str:
        return render(memory.state())
