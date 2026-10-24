"""`jms-uc1 infeasible-operation`: two presses of Unscrew, and the store's answer to each.

These tests need no store and start no process. The planner is faked by its two calls, a press
and its record route, and the run by a supervisor that records its calls, so the command's flow is
checked against the answers it can get: it exits 0 when the first operation ended `done` and the
second was refused by `:UnscrewingOperationScrewShape`, and 1 otherwise.
`test_uc1_infeasible_operation_live.py` runs it on a live store.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kapps_triplestore_interface import IRI

from jms_kapps.common.supervision import NotReady
from jms_kapps.uc1 import __main__ as main_module
from jms_kapps.uc1 import infeasible_operation
from jms_kapps.uc1.infeasible_operation import RULE, demo, play
from jms_kapps.uc1.seed import SCREW, UNMEASURED_SCREW
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

# The store's report of the refused commit, as the command records it.
REPORT = (Path(__file__).parent / "reports" / "infeasible-operation.nq").read_text(
    encoding="utf-8"
)

OPERATION = IRI("UnscrewingOperation1", base=JMS_NS)
MESSAGE = (
    "An unscrewing operation concerns exactly one Screw, whose Screw type holds each of the "
    "seven values the attempt needs (the six detection thresholds and the preparation turning "
    "angle), once each."
)


def entry(screw: IRI, status: str, **fields: Any) -> dict[str, Any]:
    """One entry of the planner's record route."""
    return {
        "screw": str(screw),
        "status": status,
        "operation": None,
        "outcome": None,
        "why": None,
        "at": None,
        "rules": [],
        "messages": [],
        "report": None,
        **fields,
    }


DONE_1 = entry(SCREW, "done", operation=str(OPERATION), outcome="ScrewIntact")
REFUSED_3 = entry(
    UNMEASURED_SCREW,
    "refused",
    why="the triple store refused the unscrewing operation",
    rules=["UnscrewingOperationScrewShape at UnscrewingOperation2"],
    messages=[MESSAGE],
    report=REPORT,
)


class FakePlanner:
    """A planner whose record route answers what `ends` gives for each Screw pressed: the entry
    is `dispatched` at the first `under_way` reads after the press, and has its end from then
    on."""

    def __init__(
        self,
        ends: dict[IRI, dict[str, Any]],
        *,
        starts: bool = True,
        under_way: int = 1,
    ) -> None:
        self._ends = ends
        self._starts = starts
        self._under_way = under_way
        self.pressed: list[IRI] = []
        self._reads: dict[IRI, int] = {}

    def press(self, screw: IRI) -> dict[str, Any]:
        self.pressed.append(screw)
        if not self._starts:
            return {"started": False, "screw": str(screw), "why": "not offered"}
        self._reads[screw] = 0
        return {"started": True, "screw": str(screw)}

    def presses(self) -> list[dict[str, Any]]:
        shown = []
        for screw in self.pressed:
            if screw not in self._reads:
                continue
            self._reads[screw] += 1
            if self._reads[screw] <= self._under_way or screw not in self._ends:
                shown.append(entry(screw, "dispatched"))
            else:
                shown.append(self._ends[screw])
        return shown


class Clock:
    """A clock that a sleep moves on, so a wait ends without waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def played(planner: FakePlanner, **options: Any) -> tuple[int, str]:
    out = io.StringIO()
    clock = Clock()
    code = demo(
        planner,
        out=out,
        wait_seconds=5.0,
        poll_seconds=1.0,
        clock=clock,
        sleep=clock.sleep,
        **options,
    )
    return code, out.getvalue()


def test_the_admitted_operation_and_the_refusal_by_the_shape_exit_0() -> None:
    planner = FakePlanner({SCREW: DONE_1, UNMEASURED_SCREW: REFUSED_3})

    code, out = played(planner)

    assert code == 0, out
    assert planner.pressed == [SCREW, UNMEASURED_SCREW]
    assert "admitted; UnscrewingOperation1 ended done, outcome ScrewIntact" in out
    assert "refused, by UnscrewingOperationScrewShape at UnscrewingOperation2" in out
    assert MESSAGE in out
    assert "the store's validation report:" in out
    assert f"<{RULE}>" in out, "the report is printed verbatim"


def test_the_second_screw_is_pressed_only_once_the_first_operation_ended() -> None:
    """The normal sequence runs to its end first."""
    planner = FakePlanner({SCREW: DONE_1, UNMEASURED_SCREW: REFUSED_3})
    reads: list[list[IRI]] = []
    presses = planner.presses

    def recording() -> list[dict[str, Any]]:
        reads.append(list(planner.pressed))
        return presses()

    planner.presses = recording  # type: ignore[method-assign]

    played(planner)

    assert reads[0] == [SCREW] and reads[1] == [SCREW]


def test_an_admitted_infeasible_operation_exits_1() -> None:
    planner = FakePlanner(
        {SCREW: DONE_1, UNMEASURED_SCREW: entry(UNMEASURED_SCREW, "failed")}
    )

    code, out = played(planner)

    assert code == 1
    assert "step 2 did not behave as stated" in out


def test_a_refusal_for_want_of_a_service_exits_1() -> None:
    """A refusal counts only when the report names the shape."""
    planner = FakePlanner(
        {
            SCREW: DONE_1,
            UNMEASURED_SCREW: entry(
                UNMEASURED_SCREW,
                "refused",
                why="no live service realizes the capability",
            ),
        }
    )

    code, out = played(planner)

    assert code == 1
    assert "no live service realizes the capability" in out
    assert "step 2 did not behave as stated" in out


def test_a_first_operation_that_failed_exits_1() -> None:
    planner = FakePlanner(
        {
            SCREW: entry(SCREW, "failed", operation=str(OPERATION)),
            UNMEASURED_SCREW: REFUSED_3,
        }
    )

    code, out = played(planner)

    assert code == 1
    assert "step 1 did not behave as stated" in out
    assert "step 2" not in out.split("step 1 did not")[1]


def test_a_press_that_does_not_start_exits_1_without_waiting() -> None:
    planner = FakePlanner({}, starts=False)

    code, out = played(planner)

    assert code == 1
    assert "the planner did not start it: not offered" in out


def test_a_press_that_never_ends_exits_1_after_the_wait() -> None:
    planner = FakePlanner({})

    code, out = played(planner)

    assert code == 1
    assert "no end within 5 s" in out


def test_a_stop_ends_the_wait() -> None:
    planner = FakePlanner({})

    code, out = played(planner, stopped=lambda: True)

    assert code == 1
    assert len([line for line in out.splitlines() if "no end" in line]) == 2


def test_the_record_holds_the_report_verbatim(tmp_path: Path) -> None:
    record = tmp_path / "refusal.nq"
    planner = FakePlanner({SCREW: DONE_1, UNMEASURED_SCREW: REFUSED_3})

    code, _ = played(planner, record=record)

    assert code == 0
    assert record.read_text(encoding="utf-8") == REPORT


class FakeSupervisor:
    """A supervisor that records its calls, with the planner's address in its snapshot."""

    def __init__(self, *, ready_raises: BaseException | None = None) -> None:
        self.calls: list[str] = []
        self._ready_raises = ready_raises

    def spawn(self, resource, name, command, *, workflows=(), has_service=True) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(f"spawn {name}")

    def watch(self) -> None:
        self.calls.append("watch")

    def wait_until_ready(self) -> None:
        self.calls.append("ready")
        if self._ready_raises is not None:
            raise self._ready_raises

    def snapshot(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(resource=JMS.Learner1, address="http://127.0.0.1:1/"),
            SimpleNamespace(resource=JMS.Planner1, address="http://127.0.0.1:2/"),
        ]

    def stop_all(self) -> None:
        self.calls.append("stop_all")

    def may_not_have_deregistered(self) -> set[IRI]:
        return set()


def a_run(**options: Any) -> tuple[int, FakeSupervisor, list[str], list[str]]:
    supervisor = options.pop("supervisor", FakeSupervisor())
    seeded: list[str] = []
    addresses: list[str] = []
    planner = options.pop(
        "planner",
        FakePlanner({SCREW: DONE_1, UNMEASURED_SCREW: REFUSED_3}, under_way=0),
    )

    def planner_at(address: str) -> FakePlanner:
        addresses.append(address)
        return planner

    code = play(
        supervisor,  # type: ignore[arg-type]
        lambda: seeded.append("seed"),
        main_module.participants("ds"),
        planner_at,
        out=io.StringIO(),
        wait_seconds=0.0,
        **options,
    )
    return code, supervisor, seeded, addresses


def test_the_run_seeds_starts_waits_presses_the_planner_and_stops_everything() -> None:
    code, supervisor, seeded, addresses = a_run()

    assert code == 0
    assert seeded == ["seed"]
    assert supervisor.calls[:5] == [
        "spawn Anomaly detector",
        "spawn Screwing resource",
        "spawn Robot controller",
        "spawn Learner",
        "spawn Planner",
    ]
    assert supervisor.calls[5:] == ["watch", "ready", "stop_all"]
    assert addresses == ["http://127.0.0.1:2/"]


def test_a_run_that_is_not_ready_exits_1_and_stops_everything() -> None:
    code, supervisor, _, addresses = a_run(
        supervisor=FakeSupervisor(ready_raises=NotReady("Planner is failed"))
    )

    assert code == 1
    assert supervisor.calls[-1] == "stop_all"
    assert addresses == []


def test_a_run_whose_demo_raises_stops_every_participant() -> None:
    supervisor = FakeSupervisor()

    class Breaking(FakePlanner):
        def press(self, screw: IRI) -> dict[str, Any]:
            raise RuntimeError("the planner is gone")

    with pytest.raises(RuntimeError):
        a_run(supervisor=supervisor, planner=Breaking({}))

    assert supervisor.calls[-1] == "stop_all"


def test_jms_uc1_infeasible_operation_runs_it_with_its_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        infeasible_operation,
        "run",
        lambda dataset, **options: calls.append((dataset, options)) or 0,
    )

    code = main_module.main(
        [
            "infeasible-operation",
            "--dataset",
            "ogm-disposable-x",
            "--force",
            "--record",
            str(tmp_path / "r.nq"),
            "--wait-seconds",
            "60",
        ]
    )

    assert code == 0
    assert calls == [
        (
            "ogm-disposable-x",
            {"force": True, "record": tmp_path / "r.nq", "wait_seconds": 60.0},
        )
    ]


def test_jms_uc1_infeasible_operation_takes_the_default_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        infeasible_operation,
        "run",
        lambda dataset, **options: calls.append((dataset, options)) or 0,
    )

    main_module.main(["infeasible-operation"])

    ((dataset, options),) = calls
    assert dataset == "jms-kapps"
    assert options["force"] is False and options["record"] is None
