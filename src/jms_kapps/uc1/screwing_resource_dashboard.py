"""The screwing resource's dashboard box "Attempts", on its own dashboard, in the tab "Screwing
resource".

The dashboard is the middleware's: a dashboard box is a render function that returns an HTML
fragment, and the page loads each one on its own. The box renders what the screwing resource holds
in memory (`screwing_resource_memory`) and nothing else: no read of the graph and no read of the
time-series store, so an open tab causes no load. It carries no JavaScript and no styling of its
own.

It shows each unscrewing attempt, newest first: the operation, the Screw and its type, when, the
time-series reference under which its curves went, the values of the Screw type that the attempt
was given, the anomaly detector it called, and the operation's status after the detector's answer,
or why it failed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from kapps_semantic_middleware.dashboard import (
    DashboardRegistry,
    escape,
    hint,
)
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.screwing_resource_memory import (
    AttemptRecord,
    ScrewingMemory,
    ScrewingState,
    values_text,
)
from jms_kapps.uc1.vocabulary import TYPE_LEVEL

TAB = "Screwing resource"

# The id of the dashboard box "Attempts".
BOX = "attempts"

# How often the box asks for itself again. It reads only memory.
REFRESH_SECONDS = 2


def _time(at: datetime) -> str:
    return at.strftime("%H:%M:%S")


def _short(iri: IRI | None) -> str:
    """`iri` shortened to its local name, in full when the pointer rests on it; a dash if none."""
    if iri is None:
        return escape(None)
    return f'<span title="{escape(iri)}">{escape(iri.fragment)}</span>'


def _shortened(full: str | None, short: str) -> str:
    """`short` for `full`, in full when the pointer rests on it; a dash if there is none."""
    if full is None:
        return escape(None)
    return f'<span title="{escape(full)}">{escape(short)}</span>'


def _reference(reference: str | None) -> str:
    """A time-series reference by the id of its frame."""
    if reference is None:
        return escape(None)
    frames = parse_qs(urlsplit(reference).query).get("frame")
    return _shortened(reference, frames[0] if frames else reference)


def _type_values(record: AttemptRecord) -> str:
    """The seven values the attempt was given, as bare numbers in the order of the box's note,
    each named when the pointer rests on the cell; a dash if no attempt was made with them."""
    if not record.type_values:
        return escape(None)
    numbers = " ".join(
        f"{record.type_values[prop]:g}"
        for prop in TYPE_LEVEL
        if prop in record.type_values
    )
    return _shortened(values_text(record.type_values), numbers)


def _detector(endpoint: str | None) -> str:
    """The detector called, by the address of its classify workflow."""
    if endpoint is None:
        return escape(None)
    return _shortened(endpoint, urlsplit(endpoint).netloc or endpoint)


def _status(record: AttemptRecord) -> str:
    """The operation's status after the answer, with the outcome the detector answered."""
    if record.status is None:
        return "not reported"
    if record.outcome is None:
        return escape(record.status)
    return f"{escape(record.status)}, {_short(IRI(record.outcome))}"


def _row(record: AttemptRecord) -> str:
    cells = [
        escape(_time(record.at)),
        _short(record.operation),
        _short(record.screw),
        _short(record.screw_type),
        _reference(record.reference),
        _type_values(record),
        _detector(record.detector),
        _status(record),
        escape(record.why),
    ]
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def render(state: ScrewingState) -> str:
    """The fragment of the box "Attempts" for `state`."""
    if not state.attempts:
        return hint("It has made no attempt yet.")
    newest = ""
    if len(state.attempts) < state.attempt_count:
        newest = hint(
            f"The newest {len(state.attempts)} of {state.attempt_count} attempts, "
            "newest first."
        )
    header = "".join(
        f"<th>{escape(title)}</th>"
        for title in (
            "When",
            "Operation",
            "Screw",
            "Screw type",
            "Curves",
            "Type values",
            "Detector",
            "Status",
            "Why",
        )
    )
    body = "".join(_row(record) for record in state.attempts)
    return (
        newest
        + hint(
            "Curves is the id of the frame that holds the attempt's time series. Type values "
            "are the values of the Screw type that the attempt was given, in this order: "
            f"{', '.join(prop.fragment.removeprefix('has') for prop in TYPE_LEVEL)}; the "
            "replay ignores them. Status is "
            "the operation's status after the detector's answer, or after the screwing "
            "resource itself ended it."
        )
        + f'<div class="table-scroll"><table><tr>{header}</tr>{body}</table></div>'
    )


def register_box(registry: DashboardRegistry, memory: ScrewingMemory) -> None:
    """Register the dashboard box "Attempts" in `registry`, in the tab "Screwing resource".

    Args:
        registry: The dashboard of the screwing resource's middleware.
        memory: What the screwing resource knows of its attempts, which the box shows.
    """

    @registry.box(BOX, title="Attempts", tab=TAB, refresh_seconds=REFRESH_SECONDS)
    def attempts(middleware: Any) -> str:
        return render(memory.state())
