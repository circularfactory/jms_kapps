"""`jms-uc1 infeasible-operation`: the store refuses a planned operation that the factory
information state cannot carry out.

The command runs use case 1 as `jms-uc1` does, on a dataset it clears, and adds to the demo a
Screw whose Screw type holds none of the seven values an attempt needs: the Screw type `:M3`, the
model `:AG2` that expects it, the Screw `:Screw3` and the grinder `:Grinder101` that contains it
(`seed.seed_demo`). It starts the five participants through the run core, waits until they are
ready, and plays two presses of Unscrew on the planner, printing what happens at each:

1. Unscrew on `:Screw1`, a Screw of the measured Screw type `:M4`: the planner issues its
   unscrewing operation, the store admits it, and the operation ends `done`.
2. Unscrew on `:Screw3`: the press is the planning decision. The planner issues the unscrewing
   operation, and the store refuses the planner's commit by `:UnscrewingOperationScrewShape`,
   whose message says what the operation needs. Nothing is written and no resource is notified.

The command reads each answer from the planner's record route (`GET /presses`), until the
Screw's entry has ended or `--wait-seconds` pass. It prints the store's answer, with the violated
shape, its message and the validation report, and stops the participants. `--record FILE` writes
the report verbatim.

It exits 0 when the first operation ended `done` and the second was refused by
`:UnscrewingOperationScrewShape`, and 1 otherwise, naming the step that did not behave as stated,
or when the run could not start.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, TextIO

import httpx
from kapps_semantic_middleware.validation_report import validation_results
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import one_stop
from jms_kapps.common.services import reader, reader_of_one
from jms_kapps.common.supervision import (
    LaunchFailed,
    LiveInstanceError,
    NotReady,
    Supervisor,
    display_names,
    refuse_if_live,
)
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.planner_dashboard import START_ROUTE
from jms_kapps.uc1.planner_memory import DISPATCHED, DONE, REFUSED, WAITING
from jms_kapps.uc1.planner_process import PRESSES_ROUTE
from jms_kapps.uc1.run_core import Participant, participants, start_run
from jms_kapps.uc1.run_core import seed as run_core_seed
from jms_kapps.uc1.seed import SCREW, UNMEASURED_SCREW, UNMEASURED_SCREW_TYPE
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

PROGRAM = "jms-uc1 infeasible-operation"

# The shape whose refusal the command shows.
RULE = IRI("UnscrewingOperationScrewShape", base=JMS_NS)

# How long the command waits for each press to end, by default, in seconds: long enough for one
# attempt of the screwing resource, which replays a recording.
DEFAULT_WAIT_SECONDS = 300.0

# How often it reads the planner's record route while it waits, in seconds.
POLL_SECONDS = 0.5

# The statuses of an entry that has not ended yet.
UNDER_WAY = (WAITING, DISPATCHED)


class Planner(Protocol):
    """The planner of a run, as the command reaches it: a press of Unscrew, and its record."""

    def press(self, screw: IRI) -> dict[str, Any]:
        """Press Unscrew on `screw`, and return the planner's answer."""
        ...

    def presses(self) -> list[dict[str, Any]]:
        """The planner's record: one entry per Screw pressed."""
        ...


class PlannerOverHttp:
    """The planner of a run at `address`, over its REST interface."""

    def __init__(self, address: str, *, timeout: float = 30.0) -> None:
        self._address = address.rstrip("/")
        self._timeout = timeout

    def press(self, screw: IRI) -> dict[str, Any]:
        """Post `screw` to the planner's `unscrew` workflow, and return its answer."""
        answer = httpx.post(
            f"{self._address}{START_ROUTE}",
            json={"screw": str(screw)},
            timeout=self._timeout,
        )
        answer.raise_for_status()
        return answer.json()

    def presses(self) -> list[dict[str, Any]]:
        """Read the planner's record route, and return its entries."""
        answer = httpx.get(f"{self._address}{PRESSES_ROUTE}", timeout=self._timeout)
        answer.raise_for_status()
        return answer.json()["screws"]


def _ended(
    planner: Planner,
    screw: IRI,
    *,
    wait_seconds: float,
    poll_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], object],
    stopped: Callable[[], bool],
) -> dict[str, Any] | None:
    """The planner's entry of `screw` once it has ended, or None when it has not within
    `wait_seconds`, or a stop began."""
    deadline = clock() + wait_seconds
    while True:
        for entry in planner.presses():
            if entry["screw"] == str(screw) and entry["status"] not in UNDER_WAY:
                return entry
        if stopped() or clock() >= deadline:
            return None
        sleep(poll_seconds)


def _shapes(entry: dict[str, Any]) -> set[str]:
    """The shapes that the validation report of a refused entry names, by their full IRIs."""
    report = entry.get("report")
    if not report:
        return set()
    return {result.shape for result in validation_results(report)}


def _print_refusal(entry: dict[str, Any], out: TextIO) -> None:
    """Print a refused entry: the rules and their messages, then the store's report, or the
    reason when the entry has no report."""
    rules = ", ".join(entry["rules"])
    messages = " ".join(entry["messages"])
    print(
        f"   the store: refused{f', by {rules}' if rules else ''}"
        f"{f': {messages}' if messages else ''}",
        file=out,
    )
    if entry.get("report"):
        print("   the store's validation report:", file=out)
        print(
            "\n".join(f"     {line}" for line in entry["report"].strip().splitlines()),
            file=out,
        )
    elif entry.get("why"):
        print(f"   why: {entry['why']}", file=out)


def _press(planner: Planner, screw: IRI, out: TextIO) -> bool:
    """Press Unscrew on `screw`, and say so when the planner did not start it."""
    answer = planner.press(screw)
    if not answer.get("started"):
        print(f"   the planner did not start it: {answer.get('why')}", file=out)
        return False
    return True


def demo(
    planner: Planner,
    *,
    out: TextIO = sys.stdout,
    record: Path | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], object] = time.sleep,
    stopped: Callable[[], bool] = lambda: False,
) -> int:
    """Play the two presses on `planner` of a running use case 1, and print each with what became
    of it to `out`. With `record`, write the report of the refusal there verbatim.

    Returns 0 when the first operation ended `done` and the second was refused by `RULE`, and 1
    when not, after naming the step that did not behave as stated. A wait ends early once
    `stopped` is true.
    """
    failed: list[str] = []

    def ended(screw: IRI) -> dict[str, Any] | None:
        return _ended(
            planner,
            screw,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            clock=clock,
            sleep=sleep,
            stopped=stopped,
        )

    print(
        f"1. Press Unscrew on {SCREW.fragment}, whose Screw type {JMS.M4.fragment} holds "
        "the seven values the attempt needs",
        file=out,
    )
    entry = ended(SCREW) if _press(planner, SCREW, out) else None
    if entry is None:
        print(f"   no end within {wait_seconds:g} s", file=out)
    elif entry["status"] == REFUSED:
        _print_refusal(entry, out)
    else:
        operation = IRI(entry["operation"]).fragment if entry["operation"] else "-"
        outcome = f", outcome {entry['outcome']}" if entry["outcome"] else ""
        print(
            f"   the store: admitted; {operation} ended {entry['status']}{outcome}",
            file=out,
        )
    if entry is None or entry["status"] != DONE:
        failed.append(
            "step 1 did not behave as stated: expected the operation to be admitted and to "
            "end done"
        )

    print(
        f"2. Press Unscrew on {UNMEASURED_SCREW.fragment}, whose Screw type "
        f"{UNMEASURED_SCREW_TYPE.fragment} holds none of the seven values",
        file=out,
    )
    entry = ended(UNMEASURED_SCREW) if _press(planner, UNMEASURED_SCREW, out) else None
    if entry is None:
        print(f"   no end within {wait_seconds:g} s", file=out)
    elif entry["status"] == REFUSED:
        _print_refusal(entry, out)
        if record is not None and entry.get("report"):
            record.write_text(entry["report"], encoding="utf-8")
            print(f"   the report is written to {record}", file=out)
    else:
        print(
            f"   the store: admitted; the operation ended {entry['status']}", file=out
        )
    if entry is None or entry["status"] != REFUSED or str(RULE) not in _shapes(entry):
        failed.append(
            f"step 2 did not behave as stated: expected the store to refuse the operation by "
            f"{RULE.fragment}"
        )

    for line in failed:
        print(line, file=out)
    return 1 if failed else 0


def _planner_address(supervisor: Supervisor) -> str | None:
    """The address of the planner's live service, from the supervisor's snapshot."""
    for child in supervisor.snapshot():
        if child.resource == JMS.Planner1:
            return child.address
    return None


def play(
    supervisor: Supervisor,
    seed: Callable[[], None],
    the_participants: Sequence[Participant],
    planner_at: Callable[[str], Planner],
    *,
    out: TextIO = sys.stdout,
    record: Path | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    stopping: threading.Event | None = None,
) -> int:
    """Seed and start the participants under `supervisor`, wait until they are ready, play `demo`
    on the planner, and stop every participant, whatever happened. Returns `demo`'s exit code, or
    1 when the run could not start or a stop signal ended it first."""
    stopping = stopping or threading.Event()
    try:
        start_run(seed, supervisor, the_participants)
        if stopping.is_set():
            return 1
        supervisor.wait_until_ready()
        address = _planner_address(supervisor)
        if address is None:
            print(f"{PROGRAM}: the planner has no address", file=sys.stderr)
            return 1
        if stopping.is_set():
            return 1
        return demo(
            planner_at(address),
            out=out,
            record=record,
            wait_seconds=wait_seconds,
            stopped=stopping.is_set,
        )
    except (LaunchFailed, NotReady, httpx.HTTPError) as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1
    finally:
        print("Stopping the five participants...", file=out, flush=True)
        supervisor.stop_all()
        left = supervisor.may_not_have_deregistered()
        if left:
            print(
                f"These may not have deregistered their services: {display_names(left)}",
                file=out,
                flush=True,
            )


def run(
    dataset: str,
    *,
    force: bool = False,
    record: Path | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> int:
    """Guard `dataset` against a live run, then play the demonstration on it. Returns the exit
    code of `play`, or 1 when the guard refuses."""
    db = connect(dataset)
    try:
        refuse_if_live(reader(db), dataset=dataset, force=force)
    except LiveInstanceError as error:
        print(
            f"{PROGRAM}: {error} Or pass --force to clear it anyway.", file=sys.stderr
        )
        return 1
    except LaunchFailed as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1

    supervisor = Supervisor(reader(db), service_of=reader_of_one(db))
    stopping = threading.Event()

    def begin(signum: int) -> None:
        supervisor.begin_stop()
        stopping.set()

    with one_stop(begin):
        return play(
            supervisor,
            lambda: run_core_seed(db, unmeasured_screw_type=True),
            participants(dataset),
            PlannerOverHttp,
            record=record,
            wait_seconds=wait_seconds,
            stopping=stopping,
        )
