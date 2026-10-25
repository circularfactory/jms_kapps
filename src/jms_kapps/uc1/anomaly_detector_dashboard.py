"""The anomaly detector's dashboard boxes "Classifications" and "Screw type values", on its own
dashboard, in the tab "Anomaly detector".

The dashboard is the middleware's: a dashboard box is a render function that returns an HTML
fragment, and the page loads each one on its own. The boxes render what the detector holds in
memory (`anomaly_detector_memory`) and nothing else: no read of the graph and no read of the
time-series store, so an open tab causes no load. They carry no JavaScript and no styling of
their own.

"Classifications" shows each attempt the detector classified, newest first: the outcome, the rule
of the tree's exit, how it ended the operation (`done` for ScrewIntact, `failed` with its `ANOMALY`
reason for every other outcome), whether it saved measurements as uncertainty quantifications and
each measured position it saved so, with its standard deviation, and the ten values it was
decided with, each with whether the tree asked it. "Screw type values" shows what each Screw type held at the
detector's last read of it, so a value that the learner commits shows there at the detector's next
read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kapps_semantic_middleware.dashboard import (
    DashboardRegistry,
    escape,
    hint,
)
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.anomaly_detector_memory import (
    Classification,
    DetectorMemory,
    DetectorState,
)
from jms_kapps.uc1.decision_tree import EXIT_RULE

TAB = "Anomaly detector"

# The ids of the two boxes.
CLASSIFICATIONS_BOX = "classifications"
TYPE_VALUES_BOX = "screw-type-values"

# How often a box asks for itself again. It reads only memory.
REFRESH_SECONDS = 2


def _time(at: datetime) -> str:
    return at.strftime("%H:%M:%S")


def _short(iri: IRI | None) -> str:
    """`iri` shortened to its local name, in full when the pointer rests on it; a dash if none."""
    if iri is None:
        return escape(None)
    return f'<span title="{escape(iri)}">{escape(iri.fragment)}</span>'


def _table(header: tuple[str, ...], rows: list[list[str]]) -> str:
    """A table of `rows`, whose cells are HTML already, under `header`."""
    head = "".join(f"<th>{escape(title)}</th>" for title in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<div class="table-scroll"><table><tr>{head}</tr>{body}</table></div>'


def _kv(rows: list[tuple[str, str]]) -> str:
    """A key/value table whose values are HTML already: the library's `kv_table` escapes its
    values, which would show the markup of a shortened IRI as text."""
    body = "".join(f"<tr><th>{escape(k)}</th><td>{v}</td></tr>" for k, v in rows)
    return f'<table class="kv">{body}</table>'


def _values(classification: Classification) -> str:
    """The ten values of a classification: each value, and whether the tree asked it on its way
    to the exit."""
    rows = [
        [
            _short(used.prop),
            escape(f"{used.value:g}"),
            "asked" if used.asked else "",
        ]
        for used in classification.values
    ]
    return _table(("Value", "Held", "By the tree"), rows)


def _classification(classification: Classification) -> str:
    """One classification: when, which operation and Screw, the outcome, the rule of its exit and
    how it ended the operation, or why it could not be classified; whether it saved measurements
    as uncertainty quantifications, and each measured position it saved so; then its values."""
    rows = [
        ("Operation", _short(classification.operation)),
        ("Screw", _short(classification.screw)),
        ("Screw type", _short(classification.screw_type)),
    ]
    if classification.outcome is not None:
        rows += [
            ("Outcome", escape(classification.outcome)),
            ("Exit rule", escape(EXIT_RULE[classification.outcome])),
            # An intact screw ends the operation done; a failure outcome ends it failed, and names it.
            ("Ended", "done" if classification.why is None else "failed"),
        ]
        if classification.why is not None:
            rows.append(("Why", escape(classification.why)))
    else:
        rows += [
            ("Outcome", "not classified: the operation failed"),
            ("Why", escape(classification.why)),
        ]
    rows.append(
        (
            "Measurements as uncertainty quantifications",
            "saved" if classification.saves_uq else "not saved",
        )
    )
    rows += [
        (
            f"Measured {measured.position} position",
            escape(
                f"{measured.value:g} mm, standard deviation "
                f"{measured.standard_deviation:g} mm"
            ),
        )
        for measured in classification.positions
    ]
    values = _values(classification) if classification.values else ""
    return (
        f"<h3>{escape(_time(classification.at))}, "
        f"{_short(classification.operation)}</h3>" + _kv(rows) + values
    )


def render_classifications(state: DetectorState) -> str:
    """The fragment of the box "Classifications" for `state`."""
    if not state.classifications:
        return hint("It has classified no attempt yet.")
    newest = ""
    if len(state.classifications) < state.classification_count:
        newest = hint(
            f"The newest {len(state.classifications)} of "
            f"{state.classification_count} classifications, newest first."
        )
    explained = hint(
        "The seven type values are the Screw type's, the three placement values the Screw's. "
        "Only the values the decision tree asked on its way to the exit decided the outcome."
    )
    return (
        newest + explained + "".join(_classification(c) for c in state.classifications)
    )


def render_type_values(state: DetectorState) -> str:
    """The fragment of the box "Screw type values" for `state`."""
    if not state.type_values:
        return hint("It has read no Screw type yet.")
    rows = [
        [
            _short(read.screw_type),
            _short(prop),
            escape(f"{value:g}"),
            escape(_time(read.at)),
        ]
        for read in state.type_values
        for prop, value in read.values.items()
    ]
    return hint(
        "What each Screw type held at the detector's last read of it, for its last "
        "classification. A value the learner commits shows at the next read."
    ) + _table(("Screw type", "Value", "Held", "Read at"), rows)


def register_boxes(registry: DashboardRegistry, memory: DetectorMemory) -> None:
    """Register the boxes "Classifications" and "Screw type values" in `registry`, in the tab
    "Anomaly detector".

    Args:
        registry: The dashboard of the detector's middleware.
        memory: What the detector knows of its classifications, which the boxes show.
    """

    @registry.box(
        CLASSIFICATIONS_BOX,
        title="Classifications",
        tab=TAB,
        refresh_seconds=REFRESH_SECONDS,
    )
    def classifications(middleware: Any) -> str:
        return render_classifications(memory.state())

    @registry.box(
        TYPE_VALUES_BOX,
        title="Screw type values",
        tab=TAB,
        refresh_seconds=REFRESH_SECONDS,
    )
    def type_values(middleware: Any) -> str:
        return render_type_values(memory.state())
