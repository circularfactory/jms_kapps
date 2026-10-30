"""The learner of use case 1: it refines a threshold of each Screw type from the graph's history.

The learner is a participant of its own, with its own resource and its own middleware instance
(`learner_process`). No notification starts it: it runs every so often and queries the graph.
Each run:

1. finds, in one query, the unscrewing operations that are `done` with the outcome ScrewIntact and
   that it has not learned from yet, each with its Screw type and its time-series reference;
2. reads the curves of each through that operation's own reference, one attempt per read, so no
   read spans more than one attempt;
3. derives the maximum dynamic torque of each Screw type with the placeholder rule below;
4. commits each value that changed to its Screw type, all of them in one `mw.commit()`. It never
   writes to a Screw, and never a placement value: a Screw holds no type value, and a
   placement value depends on where the screw is, not on its type.

The anomaly detector reads the effective values of a Screw afresh at each attempt, through
`effective_values.read_effective_values`, which keeps nothing between two calls. So its next
decision, and the parameter copy that it records, hold the new value.

**The rule is a placeholder, not a regression.** The new maximum dynamic torque of a Screw type is
`PLACEHOLDER_MARGIN` (20 %) above the highest dynamic torque that the type's ScrewIntact attempts
recorded in their Disassembly phase. It shows the loop: a threshold changes from the graph's own
history, and the next decision uses it. The learner refines no other threshold.
The first value that the learner derives for a Screw type replaces
whatever the type held, a seeded example value too, higher or lower. After that the value only rises.

**It learns from the decision tree's own outcomes.** An outcome is what the detector observed, not
a confirmed Screw condition. So the rule takes a ScrewIntact attempt for an intact screw, and a
wrong threshold can reinforce itself. The learner has no source of a confirmed Screw
condition.

**What it remembers** is in memory: the operations it has learned from, and the value it derived
last for each Screw type. A learner that starts again learns from every completed operation once
more, and arrives at the same values, because the rule takes the highest torque of all of them.
Each run also tells the learner's memory (`learner_memory`) how it ended, each value it changed
with the value the Screw type held before, and what went wrong, for its dashboard box. The value
held before comes from the run's one query, so the box reads no store.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass

from kapps_ogm import Update
from kapps_semantic_middleware import SemanticMiddleware
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc1.frame import UNSCREWING_TORQUE, Frame
from jms_kapps.uc1.learner_memory import Change, LearnerMemory
from jms_kapps.uc1.timeseries import FrameNotFound, FrameReader
from jms_kapps.uc1.vocabulary import JMS

logger = logging.getLogger(__name__)

# PLACEHOLDER: how far above the highest dynamic torque of a ScrewIntact attempt the rule sets the
# maximum. An arbitrary number, not a measured or fitted one.
PLACEHOLDER_MARGIN = 0.2

# The one threshold that the placeholder rule derives.
LEARNED_THRESHOLD = JMS.hasMaxDynamicTorque


def placeholder_max_dynamic_torque(intact: Iterable[Frame]) -> float | None:
    """PLACEHOLDER RULE: a maximum dynamic torque `PLACEHOLDER_MARGIN` above the highest dynamic
    torque of `intact`, the frames of ScrewIntact attempts.

    The dynamic torque is the unscrewing torque of a Disassembly sample, counted by its magnitude,
    as the decision tree counts it. None when no frame holds a Disassembly sample with a torque.
    """
    highest = max(
        (torque for frame in intact for torque in _dynamic_torques(frame)), default=None
    )
    return None if highest is None else highest * (1 + PLACEHOLDER_MARGIN)


def _dynamic_torques(frame: Frame) -> list[float]:
    """The magnitude of the unscrewing torque at each Disassembly sample of `frame`, and none for a
    frame that recorded no unscrewing torque."""
    torques = frame.signals.get(UNSCREWING_TORQUE)
    if torques is None:
        return []
    return [
        abs(torque)
        for torque, phase in zip(torques, frame.phases, strict=True)
        if phase == "Disassembly"
    ]


@dataclass(frozen=True)
class IntactAttempt:
    """A completed unscrewing operation with the outcome ScrewIntact, as the learner reads it.

    `held` is the value of the learned threshold that its Screw type holds, or None when it holds
    none. The learner keeps it only to show what a commit replaced. `screw` is the Screw the
    operation unscrewed; the learner names it in its log line.
    """

    operation: IRI
    screw_type: IRI
    reference: str
    held: float | None = None
    screw: IRI | None = None


def intact_attempts(db: Queryable) -> list[IntactAttempt]:
    """Every unscrewing operation that is `done` with the outcome ScrewIntact, in one query.

    An operation counts once it is `done`, which the detector commits together with the outcome.
    Only ScrewIntact ends an operation `done`; a failure outcome ends it `failed`, and it keeps the
    outcome. The query keeps both filters, because a graph written before that rule holds operations
    that are `done` with another outcome. One whose Screw names no Screw type, or that holds no
    reference, is not found. The same query reads the value of the learned threshold that each Screw
    type holds, one value of it, so that a Screw type with two values gives no second row.
    """
    rows = bindings(
        db,
        f"""
        SELECT ?operation ?screw ?type ?reference (SAMPLE(?value) AS ?held) WHERE {{
            ?operation a <{JMS.UnscrewingOperation}> ;
                <{SVC.hasOperationStatus}> "{OperationStatus.DONE}" ;
                <{JMS.hasDetectionResult}> <{JMS.ScrewIntact}> ;
                <{JMS.concernsScrew}> ?screw ;
                <{JMS.hasTimeSeriesReference}> ?reference .
            ?screw <{JMS.hasScrewType}> ?type .
            OPTIONAL {{ ?type <{LEARNED_THRESHOLD}> ?value }}
        }}
        GROUP BY ?operation ?screw ?type ?reference
        """,
    )
    return [
        IntactAttempt(
            operation=IRI(str(row["operation"])),
            screw_type=IRI(str(row["type"])),
            reference=str(row["reference"]),
            held=_number(row.get("held")),
            screw=IRI(str(row["screw"])) if row.get("screw") is not None else None,
        )
        for row in rows
    ]


def _number(value: object) -> float | None:
    """`value` as a number, or None when there is none or it is no number. The learner only shows
    the value that a Screw type held, so a value that is no number must not stop it."""
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


class Learner:
    """Derives the maximum dynamic torque of each Screw type, one run at a time.

    Runs do not overlap: the process makes one after another.

    Args:
        middleware: The learner's own middleware instance. A run reads the graph through it, and
            commits through it.
        frames: Reads an attempt's frame from the time-series store.
        memory: Where it keeps what it knows of its own runs, which its dashboard box shows.
    """

    def __init__(
        self,
        middleware: SemanticMiddleware,
        frames: FrameReader,
        memory: LearnerMemory | None = None,
    ) -> None:
        self._middleware = middleware
        self._frames = frames
        self.memory = LearnerMemory() if memory is None else memory
        self._learned_from: set[IRI] = set()
        self._derived: dict[IRI, float] = {}

    def learn(self) -> dict[IRI, float]:
        """One run: learn from the attempts completed since the last run, and commit what changed.

        An attempt whose curves cannot be read is logged and passed over for good: its reference
        names no frame that the time-series store holds, or its rows do not make one frame. Any
        other failure ends the run before its commit, remembers nothing of it, and the next run
        tries the same attempts again.

        The run tells the learner's memory that it starts, and how it ended: with how many new
        attempts it learned from and each value it changed, old and new, or why it failed, at
        which Operation's attempt if at one. It tells it each attempt it passes over too.

        Returns:
            The value committed to each Screw type whose value changed. Empty when none changed,
            and then nothing is committed.

        Raises:
            InfluxDBError: If the time-series store refuses a read.
            httpx.HTTPError: If the time-series store cannot be reached.
            kapps_triplestore_interface.exceptions.TripleStoreInterfaceError: If the triple store
                refuses the commit.
        """
        self.memory.started()
        # What fails should the run fail now, and at which Operation's attempt, for the memory.
        what_fails, operation = "The query for the completed attempts failed", None
        try:
            found = intact_attempts(self._middleware.ogm.db)
            new = [
                attempt
                for attempt in found
                if attempt.operation not in self._learned_from
            ]
            intact: dict[IRI, list[Frame]] = {}
            # The new attempts of each Screw type, for the log line of a change.
            learned_from: dict[IRI, list[IntactAttempt]] = {}
            passed_over = 0
            for attempt in new:
                what_fails = "Reading its curves from the time-series store failed"
                operation = attempt.operation
                try:
                    frame = self._frames.read(attempt.reference)
                except (FrameNotFound, ValueError) as unreadable:
                    logger.warning(
                        "The learner passes over %s, whose curves it cannot read: %s",
                        attempt.operation.fragment,
                        unreadable,
                    )
                    self.memory.cannot_read(
                        attempt.operation,
                        "Its curves cannot be read, so the learner passes over it: "
                        f"{_reason(unreadable)}",
                    )
                    passed_over += 1
                    continue
                intact.setdefault(attempt.screw_type, []).append(frame)
                learned_from.setdefault(attempt.screw_type, []).append(attempt)

            what_fails = "Deriving the values failed"
            operation = None
            changed: dict[IRI, float] = {}
            for screw_type, frames in intact.items():
                derived = placeholder_max_dynamic_torque(frames)
                if derived is None:
                    continue
                # The rule takes the highest torque, so its value over every attempt so far is the
                # higher of the value derived last and the value over the new attempts.
                last = self._derived.get(screw_type)
                value = derived if last is None else max(last, derived)
                if value != last:
                    changed[screw_type] = value

            if changed:
                what_fails = (
                    f"The commit of {LEARNED_THRESHOLD.fragment} to the Screw type "
                    f"{', '.join(screw_type.fragment for screw_type in changed)} failed"
                )
                with self._middleware.commit(
                    entry=[
                        Update(
                            instance_iri=screw_type,
                            data={str(LEARNED_THRESHOLD): [value]},
                        )
                        for screw_type, value in changed.items()
                    ]
                ):
                    pass
        except Exception as error:
            self.memory.failed(operation, f"{what_fails}: {_reason(error)}")
            raise

        committed_at = self.memory.now()
        held = {attempt.screw_type: attempt.held for attempt in found}
        for screw_type, value in changed.items():
            attempts = learned_from.get(screw_type, [])
            logger.info(
                "Learner: %d new ScrewIntact attempt(s) of the Screw type %s: %s. Their highest "
                "dynamic torque, %d %% above it (placeholder rule), gives %s = %g; %s held %s "
                "before. The learner committed the new value, so the next attempt's decision "
                "uses it.",
                len(attempts),
                screw_type.fragment,
                _attempts_text(attempts),
                round(PLACEHOLDER_MARGIN * 100),
                LEARNED_THRESHOLD.fragment,
                value,
                screw_type.fragment,
                "no value" if held.get(screw_type) is None else f"{held[screw_type]:g}",
            )
        for screw_type, attempts in learned_from.items():
            if screw_type not in changed:
                logger.info(
                    "Learner: %d new ScrewIntact attempt(s) of the Screw type %s: %s. They give "
                    "no higher %s, so the learner commits nothing",
                    len(attempts),
                    screw_type.fragment,
                    _attempts_text(attempts),
                    LEARNED_THRESHOLD.fragment,
                )
        # A learner that starts again commits the value a Screw type holds once more. That is
        # no change, and the memory keeps none.
        self.memory.ran(
            learned=sum(len(frames) for frames in intact.values()),
            passed_over=passed_over,
            changes=[
                Change(
                    committed_at,
                    screw_type,
                    LEARNED_THRESHOLD,
                    held.get(screw_type),
                    value,
                )
                for screw_type, value in changed.items()
                if not _same(held.get(screw_type), value)
            ],
        )
        self._derived.update(changed)
        self._learned_from.update(attempt.operation for attempt in new)
        return changed


def _same(old: float | None, new: float) -> bool:
    """Whether a commit of `new` over `old` leaves the value as it was."""
    return old is not None and math.isclose(old, new)


# How much of an error's message the memory keeps. A store that answers with a page of HTML would
# otherwise fill the dashboard box; the log keeps the whole message.
_KEPT_REASON = 500


def _reason(error: Exception) -> str:
    """Why `error` was raised, in a line a reader of the dashboard can take in."""
    reason = " ".join(str(error).split()) or type(error).__name__
    if len(reason) > _KEPT_REASON:
        reason = reason[:_KEPT_REASON] + " … (the log holds the whole message)"
    return reason


def _attempts_text(attempts: list[IntactAttempt]) -> str:
    """The Screws of `attempts`, each with its operation: `Screw1 (UnscrewingOperation_1a2b)`."""
    return ", ".join(
        f"{attempt.screw.fragment if attempt.screw else 'a Screw'} "
        f"({attempt.operation.fragment})"
        for attempt in attempts
    )
