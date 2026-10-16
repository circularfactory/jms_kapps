"""A run whose repository the store did not delete (jms_kapps 116, Sören's decisions of
2026-10-14 as the product owner of the tests): the outcome says how the measurement ended, and
only that; the deletion is a fact of its own in the record (`repository_deletion`); a completed run
gets its observation all the same; and where the store's answer to the deletion is lost, one list
of the server's repositories says whether it was deleted.

Each run here is part 1's real run, or a baseline's, against the stand-ins of `_evaluation_fakes`: a
GraphDB's REST API in memory, a bench graph whose writes are events, a supervisor that starts no
process, the spawn rate workflow and a clock that moves only when the run waits. The connection
variables of a store are removed first, so that no test here can reach one.
"""

from __future__ import annotations

import ast
import csv
import json
import os
import re
import signal
from collections.abc import Callable, Collection
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from _evaluation_fakes import (
    Events,
    FakeBenchGraph,
    FakeClock,
    FakeGraphDB,
    FakeSpawnRate,
    FakeSupervisor,
)
from _repo import withheld_text
from test_evaluation_baseline import Baseline, read, recorded_run
from test_evaluation_bench import CROSS, Bench
from test_evaluation_replay_levels import covering, four_requests
from test_evaluation_run_limit import LIMIT, LimitedBench
from test_evaluation_sweep import SPEC, spec_file
from test_evaluation_sweep_replay import FabricatedReplays, FabricatedRuns
from test_evaluation_sweep_replay import call as replay_call
from test_evaluation_sweep_replay import spec_file as replay_spec_file

import jms_kapps
from jms_kapps.evaluation import baseline, bench, run_uc2, sweep
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.precision import made_repetitions
from jms_kapps.evaluation.store import StoreRefused
from jms_kapps.evaluation.summary import read_summaries, summary_of
from jms_kapps.evaluation.table import main as table_main


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


def refused() -> httpx.Response:
    """The store refuses the deletion, and the repository stays."""
    return httpx.Response(500, text="the repository is in use")


def actions_of(record: dict) -> list[str]:
    return [action["action"] for action in record["actions"]]


def test_a_completed_run_whose_deletion_the_store_refuses_keeps_its_outcome_and_measures(
    tmp_path,
):
    bench = Bench(tmp_path, store=FakeGraphDB(on_delete=refused))

    record = bench.run()

    assert record["outcome"] == "completed"
    assert record["measures"] is not None
    assert record["valid"] is True
    assert record["failed_checks"] == [] and record["checks_made"]
    assert record["stuck"] is not None
    assert summary_of(record).counts
    assert record["repository_deletion"] == {
        "deleted": False,
        "why": f"the store did not delete {record['repository']}: 500 the repository is "
        "in use",
        "answer_lost": None,
    }
    [left] = [a for a in record["actions"] if a["action"] == "repository not deleted"]
    assert left["why"] == record["repository_deletion"]["why"]
    assert actions_of(record)[-2:] == ["repository not deleted", "run ended"]
    assert record["repository"] in bench.store.repositories


def test_a_completed_run_whose_repository_is_deleted_says_so(tmp_path):
    bench = Bench(tmp_path)

    record = bench.run()

    assert record["outcome"] == "completed"
    assert record["repository_deletion"] == {
        "deleted": True,
        "why": None,
        "answer_lost": None,
    }
    assert actions_of(record)[-2:] == ["repository deleted", "run ended"]
    assert bench.store.created() == []


# --- a lost answer is checked once ----------------------------------------------------------


def lost_answer(
    store: FakeGraphDB,
    *,
    deleted: bool,
    error: Exception | None = None,
    status: int | None = None,
) -> Callable[[], object]:
    """The store's answer to the deletion is lost, as the harness's client raises when no answer
    comes in the request's time (or raises `error`, or answers `status` from a gateway in front
    of the store); with `deleted`, the store deleted the repository before."""

    def on_delete() -> object:
        if deleted:
            for name in store.created():
                del store.repositories[name]
        if status is not None:
            return httpx.Response(status, text="from the proxy")
        raise error if error is not None else httpx.ReadTimeout("timed out")

    return on_delete


def requests_after_the_deletion(record: dict) -> list[str]:
    sent = record["server"]["requests"]
    [at] = [i for i, r in enumerate(sent) if r.startswith("DELETE rest/repositories/")]
    return sent[at + 1 :]


def test_a_lost_answer_and_a_repository_still_listed_is_not_deleted_with_the_timeout(
    tmp_path,
):
    store = FakeGraphDB()
    store.on_delete = lost_answer(store, deleted=False)
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed" and record["measures"] is not None
    assert record["repository_deletion"] == {
        "deleted": False,
        "why": "the store's answer to the deletion was lost (timed out), and the store "
        "still lists the repository",
        "answer_lost": "timed out",
    }
    assert requests_after_the_deletion(record) == ["GET rest/repositories"]
    assert actions_of(record)[-2:] == ["repository not deleted", "run ended"]
    assert record["repository"] in bench.server.created


def test_a_lost_answer_and_a_repository_no_longer_listed_is_deleted(tmp_path):
    store = FakeGraphDB()
    store.on_delete = lost_answer(store, deleted=True)
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed" and record["measures"] is not None
    assert record["repository_deletion"] == {
        "deleted": True,
        "why": None,
        "answer_lost": "timed out",
    }
    assert requests_after_the_deletion(record) == ["GET rest/repositories"]
    [deleted] = [a for a in record["actions"] if a["action"] == "repository deleted"]
    assert deleted["answer_lost"] == "timed out"
    assert record["repository"] not in bench.server.created
    assert store.created() == []


@pytest.mark.parametrize(
    ("listing", "why_unlisted"),
    [
        (
            lambda: httpx.Response(503, text="busy"),
            "the server's repositories could not be listed: 503 busy",
        ),
        (
            lambda: (_ for _ in ()).throw(httpx.ConnectError("connection dropped")),
            "the server's repositories could not be listed: connection dropped",
        ),
    ],
    ids=["refused", "no answer"],
)
def test_a_lost_answer_and_a_listing_that_fails_is_not_deleted_with_both_whys(
    tmp_path, listing, why_unlisted
):
    store = FakeGraphDB()
    lose = lost_answer(store, deleted=False)

    def on_delete() -> object:
        store.on_list = listing
        return lose()

    store.on_delete = on_delete
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed" and record["measures"] is not None
    assert record["repository_deletion"] == {
        "deleted": False,
        "why": f"the store's answer to the deletion was lost (timed out), and {why_unlisted}",
        "answer_lost": "timed out",
    }
    assert requests_after_the_deletion(record) == ["GET rest/repositories"]


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("no connection in time"),
        httpx.PoolTimeout("no free connection in time"),
    ],
    ids=["connect error", "connect timeout", "pool timeout"],
)
def test_a_deletion_request_that_did_not_reach_the_store_says_so_and_the_listing_decides(
    tmp_path, error
):
    store = FakeGraphDB()
    store.on_delete = lost_answer(store, deleted=False, error=error)
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed" and record["measures"] is not None
    assert record["repository_deletion"] == {
        "deleted": False,
        "why": f"the deletion request did not reach the store ({error}), and the store still "
        "lists the repository",
        "answer_lost": str(error),
    }
    assert requests_after_the_deletion(record) == ["GET rest/repositories"]


def test_a_request_that_did_not_reach_the_store_is_deleted_where_the_store_lists_it_no_more(
    tmp_path,
):
    store = FakeGraphDB()
    store.on_delete = lost_answer(
        store, deleted=True, error=httpx.ConnectError("connection refused")
    )
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["repository_deletion"] == {
        "deleted": True,
        "why": None,
        "answer_lost": "connection refused",
    }


# --- an answer that arrived but could not be read ---------------------------


@pytest.mark.parametrize("listed", [True, False], ids=["listed", "not listed"])
def test_an_answer_to_the_deletion_that_cannot_be_read_says_so_and_the_listing_decides(
    tmp_path, listed
):
    """A `DecodingError`: an answer arrived, but could not be read. It was not lost, so the
    record says what happened; the one listing still decides whether the store deleted it."""
    error = httpx.DecodingError("malformed gzip")
    store = FakeGraphDB()
    store.on_delete = lost_answer(store, deleted=not listed, error=error)
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    assert record["outcome"] == "completed" and record["measures"] is not None
    assert record["repository_deletion"] == {
        "deleted": not listed,
        "why": "an answer to the deletion arrived but could not be read (malformed gzip), and "
        "the store still lists the repository"
        if listed
        else None,
        "answer_lost": "malformed gzip",
    }
    assert "was lost" not in str(record["repository_deletion"]["why"])
    assert requests_after_the_deletion(record) == ["GET rest/repositories"]


def test_the_deletion_s_documentation_says_what_answer_lost_means_now():
    """`answer_lost` is why the deletion got no usable answer: none, or one that could not be
    read."""
    doc = " ".join((bench.delete_run_repository.__doc__ or "").split())
    assert "`answer_lost`, why the deletion got no usable answer" in doc
    assert "the store's answer to the deletion was lost" not in doc
    readme = " ".join(
        (Path(jms_kapps.__file__).parent / "evaluation" / "README.md")
        .read_text(encoding="utf-8")
        .split()
    )
    assert "`answer_lost`, why the deletion got no usable answer" in readme
    assert "an answer that arrived but could not be read" in readme


# --- a gateway's 502, 503 or 504 is a lost answer (Sören, 2026-10-14) -------------------------


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.parametrize("listed", [True, False], ids=["listed", "not listed"])
def test_a_gateway_s_answer_to_the_deletion_is_a_lost_answer_and_the_listing_decides(
    tmp_path, status, listed
):
    store = FakeGraphDB()
    store.on_delete = lost_answer(store, deleted=not listed, status=status)
    bench = Bench(tmp_path, store=store)

    record = bench.run()

    lost = f"the gateway in front of the store answered {status} from the proxy"
    assert record["outcome"] == "completed" and record["measures"] is not None
    assert record["repository_deletion"] == {
        "deleted": not listed,
        "why": f"the store's answer to the deletion was lost ({lost}), and the store still "
        "lists the repository"
        if listed
        else None,
        "answer_lost": lost,
    }
    assert requests_after_the_deletion(record) == ["GET rest/repositories"]
    assert (record["repository"] in bench.server.created) is listed


def test_a_500_is_a_refusal_and_gets_no_listing(tmp_path):
    bench = Bench(tmp_path, store=FakeGraphDB(on_delete=refused))

    record = bench.run()

    assert record["repository_deletion"]["deleted"] is False
    assert record["repository_deletion"]["answer_lost"] is None
    assert record["repository_deletion"]["why"].startswith(
        f"the store did not delete {record['repository']}: 500"
    )
    assert requests_after_the_deletion(record) == []


# --- a store-only baseline and a replay level -------------------------------------------------


def test_a_baseline_whose_deletion_fails_is_completed_with_its_measures_and_counts(
    tmp_path,
):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)
    baseline.store.on_delete = refused

    record = baseline.run(recorded)

    assert record["outcome"] == "completed"
    assert record["measures"] is not None and record["replay_level"] is not None
    assert record["repository_deletion"]["deleted"] is False
    assert "500 the repository is in use" in record["repository_deletion"]["why"]
    assert summary_of(record).counts
    assert record["repository"] in baseline.store.repositories


def test_a_replay_level_whose_deletion_fails_is_completed_with_its_level(tmp_path):
    recorded = four_requests(tmp_path)
    covered = covering(tmp_path, recorded)
    baseline = Baseline(tmp_path)
    baseline.store.on_delete = refused

    record = baseline.run(recorded, factor=2.0, approval=covered)

    assert record["outcome"] == "completed"
    assert record["measures"] is not None
    assert record["replay_level"]["factor"] == 2.0
    assert record["repository_deletion"]["deleted"] is False
    assert summary_of(record).counts


def test_a_baseline_whose_repository_is_deleted_says_so(tmp_path):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)

    record = baseline.run(recorded)

    assert record["outcome"] == "completed"
    assert record["repository_deletion"] == {
        "deleted": True,
        "why": None,
        "answer_lost": None,
    }


def test_a_run_that_created_no_repository_has_no_deletion(tmp_path, monkeypatch):
    def not_created(repository: str, config: str) -> None:
        raise StoreRefused(f"the store did not create {repository}: 403 forbidden")

    bench = Bench(tmp_path)
    monkeypatch.setattr(bench.server, "create_repository", not_created)
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline = Baseline(tmp_path)
    monkeypatch.setattr(baseline.server, "create_repository", not_created)

    for record in (bench.run(), baseline.run(recorded)):
        assert record["outcome"].startswith("failed: StoreRefused")
        assert record["repository_deletion"] is None
        assert "repository deleted" not in actions_of(record)


# --- a failed, an over-time and an interrupted run ------------------------------------------


def test_a_failed_run_whose_deletion_fails_keeps_its_outcome_without_a_suffix(tmp_path):
    bench = Bench(
        tmp_path,
        store=FakeGraphDB(on_delete=refused),
        not_ready="Module 1 is failed",
    )

    record = bench.run()

    assert record["outcome"].startswith("failed: ")
    assert "not deleted" not in record["outcome"]
    assert record["repository_deletion"]["deleted"] is False
    assert record["measures"] is None


def test_an_over_time_run_whose_deletion_fails_keeps_its_outcome_without_a_suffix(
    tmp_path,
):
    bench = LimitedBench(
        tmp_path,
        hang_at="ready",
        hang_for=LIMIT + 1,
        store=FakeGraphDB(on_delete=refused),
    )

    record = bench.run(run_limit_seconds=LIMIT)

    assert record["outcome"] == (
        "failed (over time): the run limit of 600 s came during the start"
    )
    assert record["repository_deletion"]["deleted"] is False
    assert actions_of(record)[-2:] == ["repository not deleted", "run ended"]


@pytest.mark.posix(reason="sends SIGTERM to this process")
def test_an_interrupted_run_whose_deletion_fails_stays_interrupted_and_is_made_again(
    tmp_path,
):
    bench = Bench(tmp_path, store=FakeGraphDB(on_delete=refused))
    bench.clock.at(
        CROSS.window_seconds / 2, lambda: os.kill(os.getpid(), signal.SIGTERM)
    )

    record = bench.run(sweep={"key": "k", "repetition": 1})

    assert record["outcome"] == "interrupted"
    assert record["repository_deletion"]["deleted"] is False
    # The next call makes the repetition again, and it does not count toward the 20.
    assert not sweep.has_run(sweep.recorded(bench.runs), "k", 1)
    assert made_repetitions([summary_of(record)]) == set()


# --- the sweep ------------------------------------------------------------------------------


class Runs:
    """Part 1's run against stand-ins, in place of a run on the configured store, as the sweep's
    own tests have it. The store refuses the deletion of the runs in `refusing`, and a stop signal
    at the readiness gate interrupts those in `interrupted`, each by its place among the runs
    made, from 1."""

    def __init__(
        self, *, refusing: Collection[int] = (), interrupted: Collection[int] = ()
    ) -> None:
        self.refusing = refusing
        self.interrupted = interrupted
        self.made: list[tuple[int, int]] = []

    def __call__(
        self, configuration: Configuration, *, runs: Path, sweep: dict, **_: object
    ) -> dict:
        self.made.append((sweep["configuration"], sweep["repetition"]))
        place = len(self.made)
        events = Events()
        store = FakeGraphDB(
            events=events, on_delete=refused if place in self.refusing else None
        )
        clock = FakeClock(events)
        # Each run a minute after the one before, so that no two runs share a run id.
        clock.start += timedelta(minutes=place)

        def supervisor_for(services: object, echo: Any) -> FakeSupervisor:
            supervisor = FakeSupervisor(events, echo)
            if place in self.interrupted:

                def stop_signal() -> None:
                    raise KeyboardInterrupt

                supervisor.wait_until_ready = stop_signal  # type: ignore[method-assign]
            return supervisor

        return bench.run(
            configuration,
            runs=runs,
            server=store.server(),
            graph_for=lambda repository, prefill, with_shapes: FakeBenchGraph(
                events, prefill=prefill, with_shapes=with_shapes
            ),
            spawn_rate=FakeSpawnRate(events, clock),
            supervisor_for=supervisor_for,  # type: ignore[arg-type]
            clock=clock,
            echo=lambda line: None,
            sweep=sweep,
        )


@pytest.fixture
def configured(monkeypatch):
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")


def test_a_sweep_goes_on_after_a_run_whose_deletion_fails_and_the_run_counts(
    tmp_path, configured, monkeypatch, capsys
):
    made = Runs(refusing={1})
    monkeypatch.setattr(sweep, "run_on_the_configured_store", made)
    runs = tmp_path / "runs"
    spec = spec_file(tmp_path, SPEC)

    code = sweep.main([str(spec), "--runs", str(runs), "--configurations", "1"])

    assert code == 0
    assert made.made == [(1, 1), (1, 2)]
    found = read_summaries(runs)
    assert [s.outcome for s in found] == ["completed", "completed"]
    assert all(s.counts for s in found)
    # The table and the report count both by their validity.
    assert table_main([str(spec), "--runs", str(runs)]) == 0
    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    [row] = [row for row in table["rows"] if 1 in row["configurations"]]
    assert len(row["run_kinds"]["uc2"]["runs"]) == 2
    assert table["left_out"] == []
    with (runs / "report" / "runs.csv").open(encoding="utf-8") as rows:
        statuses = [row["status"] for row in csv.DictReader(rows)]
    assert statuses == ["completed", "completed"]


def test_an_interrupted_run_whose_deletion_fails_is_made_again_by_the_next_call(
    tmp_path, configured, monkeypatch
):
    first = Runs(refusing={2}, interrupted={2})
    monkeypatch.setattr(sweep, "run_on_the_configured_store", first)
    runs = tmp_path / "runs"
    spec = spec_file(tmp_path, SPEC)

    assert sweep.main([str(spec), "--runs", str(runs), "--configurations", "1"]) == 1
    assert first.made == [(1, 1), (1, 2)]

    second = Runs()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", second)
    assert sweep.main([str(spec), "--runs", str(runs), "--configurations", "1"]) == 0
    assert second.made == [(1, 2)]


def test_the_sweep_goes_on_to_the_next_replay_level_after_one_whose_repository_is_left(
    tmp_path, configured, monkeypatch
):
    """Each replay level is a real baseline against the stand-ins: the store refuses the
    deletion of the level at the factor 1, and the sweep makes the factor 2 after it. (Until
    2026-10-14 the replay of the configuration ended at such a level.)"""
    recorded = four_requests(tmp_path / "recorded")
    covered = covering(tmp_path / "recorded", recorded)
    made: list[tuple[float, str, bool]] = []

    def replay_here(path: Path, *, factor: float, **options: Any) -> dict:
        level = Baseline(tmp_path / f"x{factor:g}")
        # The record lands where the sweep finds it: a level whose record the call cannot find
        # counts as a failed run (jms_kapps 112, note 1866704).
        level.runs = options["runs"]
        if factor == 1:
            level.store.on_delete = refused
        record = level.run(
            recorded,
            factor=factor,
            approval=None if factor == 1 else covered,
            sweep=options["sweep"],
        )
        made.append(
            (factor, record["outcome"], record["repository_deletion"]["deleted"])
        )
        return record

    monkeypatch.setattr(sweep, "run_on_the_configured_store", FabricatedRuns())
    monkeypatch.setattr(sweep, "run_replay_on_the_configured_store", replay_here)

    replay_call(tmp_path, "--configurations", "1", "--repetitions", "1")

    assert made[:2] == [(1.0, "completed", False), (2.0, "completed", True)]


# --- the lines that name a repository that was not deleted ----------------------------------

NOT_DELETED_LINE = ". The repository {} was not deleted: delete it on the server"


def test_the_last_line_of_jms_eval_uc2_names_a_repository_not_deleted(
    tmp_path, configured, monkeypatch, capsys
):
    bench_ = Bench(tmp_path, store=FakeGraphDB(on_delete=refused))
    made: list[dict] = []

    def run_here(configuration: Configuration, **_: object) -> dict:
        made.append(bench_.run())
        return made[-1]

    monkeypatch.setattr(run_uc2, "run_on_the_configured_store", run_here)
    path = tmp_path / "run.toml"
    path.write_text(CROSS.to_toml(), encoding="utf-8")

    code = run_uc2.main([str(path), "--runs", str(bench_.runs)])

    [record] = made
    assert code == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"jms-eval-uc2: completed. The record is at "
        f"{bench_.runs / record['run_id'] / 'record.json'}"
        + NOT_DELETED_LINE.format(record["repository"])
    )


def test_the_last_line_names_no_repository_where_it_was_deleted(
    tmp_path, configured, monkeypatch, capsys
):
    bench_ = Bench(tmp_path)
    monkeypatch.setattr(
        run_uc2, "run_on_the_configured_store", lambda *_, **__: bench_.run()
    )
    path = tmp_path / "run.toml"
    path.write_text(CROSS.to_toml(), encoding="utf-8")

    assert run_uc2.main([str(path), "--runs", str(bench_.runs)]) == 0
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("jms-eval-uc2: completed. The record is at ")
    assert last.endswith("record.json")


def test_the_last_line_of_jms_eval_baseline_names_a_repository_not_deleted(
    tmp_path, configured, monkeypatch, capsys
):
    recorded = recorded_run(tmp_path, lambda clock: [read(clock, 31.0, "SELECT ?x")])
    baseline_ = Baseline(tmp_path)
    baseline_.store.on_delete = refused
    made: list[dict] = []

    def run_here(recorded_run: object, **_: object) -> dict:
        made.append(baseline_.run(recorded))
        return made[-1]

    monkeypatch.setattr(baseline, "run_on_the_configured_store", run_here)

    code = baseline.main([str(recorded), "--runs", str(baseline_.runs)])

    [record] = made
    assert code == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"jms-eval-baseline: completed. The record is at "
        f"{baseline_.runs / record['run_id'] / 'record.json'}"
        + NOT_DELETED_LINE.format(record["repository"])
    )


def test_the_line_after_each_run_of_the_sweep_names_a_repository_not_deleted(
    tmp_path, configured, monkeypatch, capsys
):
    monkeypatch.setattr(sweep, "run_on_the_configured_store", Runs(refusing={1}))
    runs = tmp_path / "runs"

    sweep.main(
        [str(spec_file(tmp_path, SPEC)), "--runs", str(runs), "--configurations", "1"]
    )

    first, second = sorted(read_summaries(runs), key=lambda s: s.run_id)
    out = capsys.readouterr().out.splitlines()
    assert (
        f"jms-eval-sweep: completed. The record is at {runs / first.run_id / 'record.json'}"
        + NOT_DELETED_LINE.format(f"eval-{first.run_id}")
    ) in out
    assert (
        f"jms-eval-sweep: completed. The record is at "
        f"{runs / second.run_id / 'record.json'}"
    ) in out


def test_the_line_after_a_replay_level_of_the_sweep_names_a_repository_not_deleted(
    tmp_path, configured, monkeypatch, capsys
):
    class LeftReplays(FabricatedReplays):
        def __call__(self, recorded: Path, **options: Any) -> dict[str, Any]:
            found = super().__call__(recorded, **options)
            found["repository_deletion"] = {
                "deleted": False,
                "why": "the store did not delete it: 500",
                "answer_lost": None,
            }
            return found

    monkeypatch.setattr(sweep, "run_on_the_configured_store", FabricatedRuns())
    monkeypatch.setattr(sweep, "run_replay_on_the_configured_store", LeftReplays())
    runs = tmp_path / "runs"

    sweep.main(
        [
            str(replay_spec_file(tmp_path)),
            "--runs",
            str(runs),
            "--configurations",
            "3",
            "--repetitions",
            "1",
        ]
    )

    [level] = [s for s in read_summaries(runs) if s.run_kind == "baseline"]
    assert (
        f"jms-eval-sweep: completed. The record is at {runs / level.run_id / 'record.json'}"
        + NOT_DELETED_LINE.format(f"eval-{level.run_id}")
    ) in capsys.readouterr().out.splitlines()


# --- the documentation ----------------------------------------------------------------------


def test_the_documentation_of_the_run_s_end_states_the_rule_with_a_dated_note():
    """The UC2 run, the baseline, the harness README and the glossary each say the new rule,
    with a dated note that keeps the old one and names Sören's decision (jms_kapps 116; the
    baseline's notes and the form of the notes after a review)."""
    evaluation = Path(jms_kapps.__file__).parent / "evaluation"
    readme = (evaluation / "README.md").read_text(encoding="utf-8")

    def docstring(module: Any) -> str:
        return ast.get_docstring(ast.parse(Path(module.__file__).read_text())) or ""

    texts = {
        "bench": docstring(bench)
        .split("**The deletion of the repository**")[1]
        .split("\n\n")[0],
        "baseline": docstring(baseline).split("5. **The end")[1].split("\n\n")[0],
        "README": readme.split("**The deletion of the repository**")[1].split("\n\n**")[
            0
        ],
        "README, baseline": readme.split("### What a baseline run does")[1]
        .split("5. **The end**")[1]
        .split("\n\n")[0],
        "CONTEXT": withheld_text(Path(jms_kapps.__file__).parents[2] / "CONTEXT.md")
        .split("**Run outcome**, **left repository**")[1]
        .split("\n\n")[0],
    }
    for name, text in texts.items():
        flat = " ".join(text.split())
        assert "2026-10-14" in flat and "jms_kapps 116" in flat, name
        assert "Sören" in flat, name
        assert "the repository was not deleted" in flat, name
        assert "repository_deletion" in flat, name
        if name == "CONTEXT":
            assert (
                "(Added on 2026-10-14, jms_kapps 116," in flat and "Until then" in flat
            )
        else:
            assert re.search(
                r"\(changed on 2026-10-14, jms_kapps 116: until then ",
                flat,
                re.IGNORECASE,
            ), name
    flat = " ".join(texts["README"].split())
    assert "says how the measurement ended, and only that" in flat
    assert "A lost answer is checked once" in flat
    assert "502, 503 or 504" in flat
