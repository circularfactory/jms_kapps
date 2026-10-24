"""What the anomaly detector knows of its own classifications, kept in its memory.

Its dashboard boxes render this memory and nothing else, so an open dashboard causes no load: no
read of the graph and no read of the time-series store. The detector writes it as it classifies,
from what each classification reads anyway: a classification once its commit has reached the
store, and the values of a Screw type at each read of a Screw's effective values.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from kapps_triplestore_interface import IRI

from jms_kapps.uc1.decision_tree import ASKED, DetectionResult
from jms_kapps.uc1.effective_values import (
    PROPERTY_OF_FIELD,
    EffectiveValuesWithOrigin,
    as_properties,
)

# How many classifications it keeps: the newest.
KEPT_CLASSIFICATIONS = 20


def _now() -> datetime:
    """The time of day here, with its time zone."""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class UsedValue:
    """One of the ten values that a classification used: the property that holds it, the value,
    and whether the decision tree asked it on its way to the exit."""

    prop: IRI
    value: float
    asked: bool


@dataclass(frozen=True)
class Classification:
    """One attempt that the detector classified, or could not classify, and when it ended.

    A classified attempt has its `outcome` and the ten `values` it was decided with. Its operation
    ended `done` for the outcome ScrewIntact; any other outcome is a failure outcome, which ended
    it `failed`, and `why` names it. An attempt that could not be classified has no outcome,
    and `why`; it has its values when they were read before the classification failed.
    """

    at: datetime
    operation: IRI
    screw: IRI | None
    screw_type: IRI | None
    outcome: DetectionResult | None
    why: str | None
    values: tuple[UsedValue, ...]


@dataclass(frozen=True)
class TypeValues:
    """The values that one Screw type held itself at the detector's last read of it, and when it
    read them."""

    at: datetime
    screw_type: IRI
    values: dict[IRI, float]


@dataclass(frozen=True)
class DetectorState:
    """What the detector knows of its classifications at one moment, as its boxes show it."""

    # The newest first.
    classifications: list[Classification]
    classification_count: int
    # One for each Screw type it has read, by the type's IRI.
    type_values: list[TypeValues]


def used_values(
    with_origin: EffectiveValuesWithOrigin, outcome: DetectionResult | None
) -> tuple[UsedValue, ...]:
    """The ten values of `with_origin`, each with whether the tree asked it on its
    way to `outcome`: none, without an outcome."""
    asked = (
        {PROPERTY_OF_FIELD[name] for name in ASKED[outcome]}
        if outcome is not None
        else set()
    )
    return tuple(
        UsedValue(prop, value, prop in asked)
        for prop, value in as_properties(with_origin.values).items()
    )


class DetectorMemory:
    """What the anomaly detector knows of its classifications: each one, newest first, and what
    each Screw type held at its last read.

    It keeps the newest `KEPT_CLASSIFICATIONS` classifications, and counts every one.

    Args:
        clock: The time of day, by which it stamps what it is told.
    """

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        # A classification writes on the thread of its call, and a render reads on one of its own.
        self._lock = threading.Lock()
        self._classifications: deque[Classification] = deque(
            maxlen=KEPT_CLASSIFICATIONS
        )
        self._count = 0
        self._type_values: dict[IRI, TypeValues] = {}

    def read(self, with_origin: EffectiveValuesWithOrigin) -> None:
        """The detector read `with_origin` now, and with it what the Screw's type holds."""
        if with_origin.screw_type is None:
            return
        values = TypeValues(
            self._clock(), with_origin.screw_type, dict(with_origin.type_values)
        )
        with self._lock:
            self._type_values[with_origin.screw_type] = values

    def classified(
        self,
        operation: IRI,
        with_origin: EffectiveValuesWithOrigin,
        outcome: DetectionResult,
        anomaly: str | None,
    ) -> None:
        """The detector ended `operation` now, with `outcome`, decided with `with_origin`: `done`,
        or `failed` with `anomaly`, the reason that names an outcome other than ScrewIntact."""
        self._add(
            Classification(
                at=self._clock(),
                operation=operation,
                screw=with_origin.screw,
                screw_type=with_origin.screw_type,
                outcome=outcome,
                why=anomaly,
                values=used_values(with_origin, outcome),
            )
        )

    def not_classified(
        self,
        operation: IRI,
        screw: IRI | None,
        with_origin: EffectiveValuesWithOrigin | None,
        why: str,
    ) -> None:
        """The detector ended `operation` `failed` now, for `why`. `with_origin` is what it read of
        `screw` before the classification failed, or None when it read nothing."""
        self._add(
            Classification(
                at=self._clock(),
                operation=operation,
                screw=screw,
                screw_type=None if with_origin is None else with_origin.screw_type,
                outcome=None,
                why=why,
                values=() if with_origin is None else used_values(with_origin, None),
            )
        )

    def _add(self, classification: Classification) -> None:
        with self._lock:
            self._classifications.appendleft(classification)
            self._count += 1

    def state(self) -> DetectorState:
        """What it knows now."""
        with self._lock:
            return DetectorState(
                classifications=list(self._classifications),
                classification_count=self._count,
                type_values=[
                    self._type_values[screw_type]
                    for screw_type in sorted(self._type_values)
                ],
            )
