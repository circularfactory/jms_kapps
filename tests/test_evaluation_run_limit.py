"""The run limit (jms_kapps 106, decided by Sören on jms_kapps 85, note 1866296): a run older than
its limit ends as failed (over time), through the end that a stop signal takes: its participants
stop, its repository is deleted and its record is written.

The runs here are those of the bench launcher's and the baseline's tests, against stand-ins only
(`test_evaluation_bench.Bench`, `test_evaluation_baseline.Baseline`), with a clock that moves only
when the run waits. A step that does not return takes some time on that clock and then waits
without end: only the limit ends it. Nothing reaches a store or a network.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, NoReturn

import pytest
from _evaluation_fakes import FakeBenchGraph, FakeClock
from _evaluation_records import record as fabricated
from _evaluation_records import write
from _ps import children_of, shown_processes
from _services import live_services
from kapps_triplestore_interface import IRI
from test_evaluation_baseline import Baseline, read, recorded_run
from test_evaluation_bench import Bench
from test_evaluation_replay_levels import covering

import jms_kapps
from jms_kapps.common.supervision import Supervisor
from jms_kapps.evaluation import baseline, bench, sweep
from jms_kapps.evaluation.configuration import KEYS, Configuration
from jms_kapps.evaluation.run_limit import (
    LOOK_SECONDS,
    RUN_LIMIT_SECONDS,
    RunLimit,
    run_and_end,
)
from jms_kapps.evaluation.spec import configurations, read_spec

SPECS = Path(jms_kapps.__file__).parent / "evaluation" / "specs"

# A limit that the stand-in runs reach only with a step that does not return: their own steps
# take less than this on their clock.
LIMIT = 600.0


def does_not_return(clock: FakeClock, seconds: float) -> NoReturn:
    """A step that takes `seconds` on the run's clock and then does not return. It waits in short
    waits, so that the limit reaches it between two of them on every platform."""
    clock.sleep(seconds)
    while True:
        time.sleep(0.01)


# A stand-in participant: a process that sleeps until it is stopped.
SLEEPS = "import time; time.sleep(600)"


class SleepingParticipants(Supervisor):
    """The run core's own supervisor, whose every participant is a stand-in process that sleeps
    until it is stopped, with a service that is live from its start."""

    def __init__(self, echo: Callable[[str], None]) -> None:
        self.started: set[IRI] = set()
        super().__init__(
            lambda workflows: live_services(self.started, workflows),
            echo=echo,
            stop_timeout=5.0,
        )

    def spawn(
        self,
        resource: IRI,
        name: str,
        command: list[str],
        *,
        workflows: Collection[IRI] = (),
    ) -> None:
        super().spawn(
            resource, name, [sys.executable, "-c", SLEEPS], workflows=workflows
        )
        self.started.add(IRI(resource))


class LimitedBench(Bench):
    """A bench run against stand-ins, one of whose steps does not return once it has taken
    `hang_for` seconds on the run's clock: the readiness gate, `"ready"`, or the read of the spawn
    rate's workflow, `"spawn rate endpoint"`; with any other `hang_at`, none. With `processes`,
    its participants are processes (`SleepingParticipants`)."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        hang_at: str,
        hang_for: float,
        processes: bool = False,
        **options: object,
    ) -> None:
        super().__init__(tmp_path, **options)  # type: ignore[arg-type]
        self.hang_at = hang_at
        self.hang_for = hang_for
        self.processes = processes
        self.participants: list[SleepingParticipants] = []

    def supervisor_for(self, services: object, echo):  # type: ignore[no-untyped-def]
        if self.processes:
            participants = SleepingParticipants(echo)
            self.participants.append(participants)
            return participants
        supervisor = super().supervisor_for(services, echo)
        if self.hang_at == "ready":
            supervisor.wait_until_ready = lambda: does_not_return(  # type: ignore[method-assign]
                self.clock, self.hang_for
            )
        return supervisor

    def graph_for(
        self, repository: str, prefill: Path | None, with_shapes: bool
    ) -> FakeBenchGraph:
        graph = super().graph_for(repository, prefill, with_shapes)
        if self.hang_at == "spawn rate endpoint":
            graph.spawn_rate_endpoint = lambda: does_not_return(  # type: ignore[method-assign]
                self.clock, self.hang_for
            )
        return graph


def test_a_run_older_than_its_limit_ends_as_failed_over_time_naming_the_limit_and_the_step(
    tmp_path,
):
    bench = LimitedBench(tmp_path, hang_at="ready", hang_for=LIMIT + 1)

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the start"
    )
    [reached] = [a for a in record["actions"] if a["action"] == "run limit reached"]
    assert reached["limit_seconds"] == LIMIT
    assert reached["step"] == "the start"
    assert reached["seconds"] >= LIMIT
    assert record["run_settings"]["run_limit_seconds"] == LIMIT


@pytest.mark.posix(reason="reads this process's children with ps")
def test_a_run_over_its_limit_deletes_its_repository_and_leaves_no_participant_process(
    tmp_path,
):
    """The participants are processes here, under the run core's own supervisor, and the step
    that does not return comes once they are live."""
    bench = LimitedBench(
        tmp_path, hang_at="spawn rate endpoint", hang_for=LIMIT + 1, processes=True
    )

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the warm-up and the window"
    )
    assert bench.store.created() == []
    assert ("repository deleted", record["repository"]) in bench.events
    assert [a["action"] for a in record["actions"]][-4:] == [
        "participants stopped",
        "statement counts read",
        "repository deleted",
        "run ended",
    ]
    [participants] = bench.participants
    children = participants.snapshot()
    assert len(children) == 6, "the cross's five modules and the WMS"
    assert {child.state for child in children} == {"stopped"}
    assert participants.may_not_have_deregistered() == set()
    left = [
        line for line in children_of(os.getpid(), shown_processes()) if SLEEPS in line
    ]
    assert left == []


@pytest.mark.posix(reason="only POSIX cuts short a wait of the main thread")
def test_a_step_that_waits_without_end_is_cut_short_at_the_limit(tmp_path):
    """A step blocked in one wait that never returns by itself, as a lock that is never released:
    on POSIX the limit's signal ends the wait."""
    never = threading.Event()

    def blocks() -> NoReturn:
        bench.clock.sleep(LIMIT + 1)
        never.wait()
        raise AssertionError("the wait returned")

    bench = LimitedBench(tmp_path, hang_at="none", hang_for=0)
    bench.clock.at(10.0, blocks)

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the warm-up and the window"
    )
    assert bench.store.created() == []
    assert [e[1] for e in bench.events if e[0] == "spawn rate"] == [6.0, 0]


def test_the_end_of_a_run_is_outside_its_limit(tmp_path):
    """A run that reaches its end within its limit completes, though its end takes longer than
    the limit: the end stops the participants, deletes the repository and writes the record."""
    bench = LimitedBench(tmp_path, hang_at="none", hang_for=0)

    def slow_delete() -> None:
        bench.clock.sleep(LIMIT * 2)
        time.sleep(LOOK_SECONDS * 3)

    bench.store.on_delete = slow_delete

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == "completed"
    assert bench.store.created() == []
    assert [a["action"] for a in record["actions"]][-2:] == [
        "repository deleted",
        "run ended",
    ]


class SlowStop(LimitedBench):
    """A bench run whose participants' stop takes twice the limit on the run's clock, and longer
    than the limit's look in real time."""

    def supervisor_for(self, services: object, echo):  # type: ignore[no-untyped-def]
        supervisor = super().supervisor_for(services, echo)
        stop_all = supervisor.stop_all

        def slow_stop_all() -> None:
            self.clock.sleep(LIMIT * 2)
            time.sleep(LOOK_SECONDS * 3)
            stop_all()

        supervisor.stop_all = slow_stop_all  # type: ignore[method-assign]
        return supervisor


def test_the_participants_stop_is_outside_the_limit(tmp_path):
    """The stop of the participants belongs to the run's end: a limit that would come during it
    does not make a run whose measurement is complete over time."""
    bench = SlowStop(tmp_path, hang_at="none", hang_for=0)

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == "completed"
    assert "run limit reached" not in [a["action"] for a in record["actions"]]
    assert bench.events.count(("stop all",)) == 1


def test_a_cleanup_that_fails_while_the_limit_ends_the_run_keeps_the_over_time_outcome(
    tmp_path,
):
    """The window's cleanup sets the spawn rate to 0, also when the limit cuts the window short;
    a request that fails there does not hide why the run ended."""
    bench = LimitedBench(tmp_path, hang_at="none", hang_for=0)
    bench.clock.at(10.0, lambda: does_not_return(bench.clock, LIMIT))
    set_rate = bench.spawn_rate.set

    def failing_at_0(endpoint: str, boxes_per_minute: float) -> float:
        if boxes_per_minute == 0:
            raise RuntimeError("the WMS did not answer")
        return set_rate(endpoint, boxes_per_minute)

    bench.spawn_rate.set = failing_at_0  # type: ignore[method-assign]

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the warm-up and the window"
    )
    [reached] = [a for a in record["actions"] if a["action"] == "run limit reached"]
    assert reached["step"] == "the warm-up and the window"
    assert bench.store.created() == []


class StandingClock:
    """A clock that does not move: a limit on it never comes."""

    def monotonic(self) -> float:
        return 0.0


def end_once_with_a_late_interrupt(
    *, steps_fail: bool, at: int | None
) -> tuple[dict[str, Any], list[Any], int]:
    """Run steps that complete, or fail where `steps_fail`, through `run_and_end`, and let the
    stop signal's interrupt come at the `at`-th line that runs on the main thread once the steps
    have done their work, while the run's handler of the stop signals is in place: a trace
    function calls that handler there, as Python calls it between two steps of whatever runs. Returns the record, the outcome that each run of the end
    found, and how many lines ran after the steps' work. An interrupt that escapes the run is
    kept among the outcomes, as `escaped: ...`."""
    record: dict[str, Any] = {"outcome": None}
    ended: list[Any] = []
    lines = 0
    handler: Any = None

    def steps() -> None:
        nonlocal handler
        # The run's handler, from now on to where the run puts back the one before it.
        handler = signal.getsignal(signal.SIGINT)
        if steps_fail:
            raise RuntimeError("a step failed")

    def trace(frame: Any, event: str, _: Any) -> Any:
        nonlocal lines
        if event == "line" and handler and signal.getsignal(signal.SIGINT) is handler:
            lines += 1
            if lines == at:
                handler(signal.SIGINT, frame)
        return trace

    before = sys.gettrace()
    sys.settrace(trace)
    try:
        run_and_end(
            record,
            RunLimit(1e9, StandingClock()),
            steps,
            lambda: ended.append(record["outcome"]),
            first_step="the only step",
            interrupted="interrupted",
            add_action=lambda *_, **__: None,
        )
    except KeyboardInterrupt as escaped:
        # Not to pytest, which would take it for a Ctrl+C and stop every test.
        ended.append(f"escaped: {escaped!r}")
    finally:
        sys.settrace(before)
    return record, ended, lines


@pytest.mark.parametrize("steps_fail", [False, True], ids=["completed", "failed"])
def test_the_end_runs_once_wherever_the_stop_signal_s_interrupt_comes_after_the_steps(
    steps_fail,
):
    """Python may run a signal's handler after any instruction, so a stop signal, or the limit,
    may raise its one interrupt after the steps, as the run begins its end. Wherever it comes,
    the end runs once, the run's outcome is set before it, and nothing escapes the run."""
    _, _, after = end_once_with_a_late_interrupt(steps_fail=steps_fail, at=None)
    assert after > 5

    for at in range(1, after + 1):
        record, ended, _ = end_once_with_a_late_interrupt(steps_fail=steps_fail, at=at)

        own = "failed: RuntimeError: a step failed" if steps_fail else "completed"
        assert ended == [record["outcome"]], at
        assert record["outcome"] in (own, "interrupted"), at


def test_the_limit_is_a_run_setting_of_30_minutes_that_the_record_keeps(tmp_path):
    bench = Bench(tmp_path)

    record = bench.run()

    assert RUN_LIMIT_SECONDS == 30 * 60
    assert record["outcome"] == "completed"
    assert record["run_settings"]["run_limit_seconds"] == 1800
    # Outside the configuration and its key.
    assert not [key for key in KEYS if "limit" in key]
    assert "limit" not in record["configuration"]


# The configuration keys of the two shipped specs as `main` gave them before the run limit
# (13a0869), in the specs' order. The limit is a run setting, so none of them changes, and the
# runs the "after" series has made stay its repetitions (jms_kapps 85, note 1866296).
KEYS_BEFORE_THE_LIMIT = {
    "iteration-one.toml": [
        "c47c56a71876d187", "9fcf03ae9c495ab4", "fa129651371f0569", "10d538f2c44d2e00",
        "07a337990d73732f", "b796a2c6bd7615fc", "59fb3ec129a9a3af", "ad4766fa7bfa7175",
        "b1b668ee2c13934b", "17be14b11b0da1df", "ba59e70219aafc59", "fcde3dbeee9188aa",
        "4c22175617842976", "fa18f363d9650357", "289325ce7e6ef05a", "7e066d2aa8e8e020",
        "8000a9866d4c2fc9", "1eb3d47e3aadee17", "f1a8c329c4da0850", "3e36549b1fa4e7e2",
        "d833c7889d2730ac", "8dbe2636e7a0b446", "00515e887bcb5db5", "3c67b04df7e198d4",
        "d6ad35f809948324",
    ],
    "after-series.toml": [
        "c47c56a71876d187", "3b7e7bb3e73bbb83", "3e36549b1fa4e7e2", "d833c7889d2730ac",
        "ad4766fa7bfa7175", "8dbe2636e7a0b446", "fa18f363d9650357", "289325ce7e6ef05a",
        "fa129651371f0569", "7e066d2aa8e8e020", "8000a9866d4c2fc9", "1eb3d47e3aadee17",
        "10d538f2c44d2e00", "f1a8c329c4da0850", "17be14b11b0da1df", "ba59e70219aafc59",
        "fcde3dbeee9188aa", "9fcf03ae9c495ab4", "4c22175617842976", "07a337990d73732f",
        "b796a2c6bd7615fc", "59fb3ec129a9a3af", "00515e887bcb5db5", "3c67b04df7e198d4",
        "b1b668ee2c13934b", "d6ad35f809948324",
    ],
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(KEYS_BEFORE_THE_LIMIT))
def test_the_keys_of_the_iteration_one_and_the_after_spec_do_not_change(name):
    spec = read_spec(SPECS / name)

    assert [c.key for c in configurations(spec)] == KEYS_BEFORE_THE_LIMIT[name]


class LimitedBaseline(Baseline):
    """A baseline run against stand-ins whose preparation of the store, the seed, takes
    `hang_for` seconds on the run's clock and does not return, where `hang_for` is given."""

    def __init__(
        self, tmp_path: Path, *, hang_for: float | None = None, **options: object
    ) -> None:
        super().__init__(tmp_path, **options)  # type: ignore[arg-type]
        self.hang_for = hang_for

    def graph_for(
        self, repository: str, prefill: Path | None, with_shapes: bool
    ) -> FakeBenchGraph:
        graph = super().graph_for(repository, prefill, with_shapes)
        if self.hang_for is not None:
            hang_for = self.hang_for
            graph.seed = lambda *_, **__: does_not_return(self.clock, hang_for)  # type: ignore[method-assign]
        return graph


def one_before_and_one_in_the_window(tmp_path: Path) -> Path:
    """A recorded run with one request before its window and one in it."""
    return recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 2.0, "SELECT ?before"),
            read(clock, 31.0, "SELECT ?in_the_window"),
        ],
    )


def test_a_baseline_older_than_its_limit_ends_as_failed_over_time_and_deletes_its_repository(
    tmp_path,
):
    recorded = one_before_and_one_in_the_window(tmp_path)
    baseline = LimitedBaseline(tmp_path, hang_for=LIMIT + 1)

    record = baseline.run(recorded, run_limit_seconds=LIMIT)

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the store"
    )
    assert record["run_settings"]["run_limit_seconds"] == LIMIT
    assert baseline.store.created() == []
    assert ("repository deleted", record["repository"]) in baseline.events
    assert [a["action"] for a in record["actions"]][-3:] == [
        "run limit reached",
        "repository deleted",
        "run ended",
    ]


def test_a_replay_level_older_than_its_limit_ends_the_same_way(tmp_path):
    """A level at the factor 2, a heavy run under its approval, whose request before the window
    gets no answer and does not return."""
    recorded = one_before_and_one_in_the_window(tmp_path)
    covered = covering(tmp_path, recorded)
    baseline = LimitedBaseline(
        tmp_path,
        answers={
            "SELECT ?before": lambda clock, _: does_not_return(clock, LIMIT + 1),
        },
    )

    record = baseline.run(
        recorded, factor=2.0, approval=covered, run_limit_seconds=LIMIT
    )

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the replay"
    )
    assert record["run_settings"]["run_limit_seconds"] == LIMIT
    assert baseline.replay_store.texts() == ["SELECT ?before"]
    assert baseline.store.created() == []
    assert ("repository deleted", record["repository"]) in baseline.events


def test_a_baseline_keeps_the_limit_of_30_minutes_as_a_run_setting(tmp_path):
    recorded = one_before_and_one_in_the_window(tmp_path)

    record = LimitedBaseline(tmp_path).run(recorded)

    assert record["outcome"] == "completed"
    assert record["run_settings"]["run_limit_seconds"] == 1800


# A spec under the precision rule with one configuration: it runs until the rule holds, at most
# 20 times.
RULED_SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false

[precision]

[[sweep]]
topology = ["cross"]
"""


class FirstRunOverTime:
    """In place of a run on the configured store: the first run is a bench run against stand-ins
    whose readiness gate does not return, so that its limit ends it; every later run writes a
    fabricated record that completed, with a throughput that never meets the precision rule. Each
    keeps the sweep's fields it was given."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.made: list[dict[str, Any]] = []
        self.outcomes: list[str] = []

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        **options: Any,
    ) -> dict[str, Any]:
        self.made.append(sweep)
        if len(self.made) == 1:
            bench = LimitedBench(
                self.tmp_path / "bench", hang_at="ready", hang_for=LIMIT + 1
            )
            bench.runs = runs
            made = bench.run(
                configuration, sweep=sweep, run_limit_seconds=LIMIT, **options
            )
        else:
            made = fabricated(
                f"run-{len(self.made):04d}",
                commit_latencies=[0.1] * 20,
                transports_ended_per_minute=10.0 if len(self.made) % 2 else 50.0,
            )
            made["sweep"] = dict(sweep)
            write(runs, made)
        self.outcomes.append(made["outcome"])
        return made


def test_the_sweep_does_not_make_a_run_over_its_limit_again_and_counts_it_toward_the_20(
    tmp_path, monkeypatch
):
    """Calls of the sweep one after the other, until a call makes no run: the run over its limit
    is repetition 1, which no later call makes again, and with it the configuration has made its
    20 runs."""
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.setenv(f"{prefix}_{name}", "x")
    spec = tmp_path / "spec.toml"
    spec.write_text(RULED_SPEC, encoding="utf-8")
    runs = tmp_path / "runs"
    made = FirstRunOverTime(tmp_path)
    monkeypatch.setattr(sweep, "run_on_the_configured_store", made)

    for _ in range(40):
        before = len(made.made)
        sweep.main([str(spec), "--runs", str(runs)])
        if len(made.made) == before:
            break

    assert made.outcomes[0].startswith("failed (over time): ")
    assert made.outcomes[1:] == ["completed"] * 19
    assert [s["repetition"] for s in made.made] == list(range(1, 21))
    assert {s["configuration"] for s in made.made} == {1}


README = Path(jms_kapps.__file__).parent / "evaluation" / "README.md"


def section_of(text: str, heading: str) -> str:
    """The section of `text` under `heading`, up to the next heading of its level or above, on
    one line."""
    level = heading.split(" ")[0]
    start = text.index(f"\n{heading}\n")
    ends = [
        found
        for found in (
            text.find(f"\n{'#' * n} ", start + 1) for n in range(1, len(level) + 1)
        )
        if found != -1
    ]
    return " ".join(text[start : min(ends, default=len(text))].split())


@pytest.mark.parametrize(
    "heading", ["### What a run does", "### What a baseline run does"]
)
def test_the_readme_states_the_run_limit_where_it_says_what_a_run_does(heading):
    section = section_of(README.read_text(encoding="utf-8"), heading)

    assert "run limit" in section
    assert "30 minutes" in section
    assert "failed (over time)" in section


@pytest.mark.parametrize("module", [bench, baseline], ids=["bench", "baseline"])
def test_the_documentation_of_a_run_states_the_run_limit(module):
    documentation = " ".join(str(module.__doc__).split())

    assert "run limit" in documentation
    assert "failed (over time)" in documentation
