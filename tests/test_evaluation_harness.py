"""`jms-eval`: one command takes a spec to the report and the table.

No test here makes a run: the UC2 runs and the replay levels are fabricated records, written by
stand-ins for a run on the configured store, and the start of the local GraphDB is a stand-in too
(a heavy run is never a test or a CI job, and no test starts a container). The approvals are for
specs in a temporary folder, copies of a shipped spec included: none covers a shipped spec itself.
The connection variables of a store are removed first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _evaluation_approvals import approval_file
from _evaluation_records import record, request, write

from jms_kapps.evaluation import harness, prefill, sweep
from jms_kapps.evaluation.approval import Covered
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.harness import (
    VALIDATION_SPEC,
    HarnessRefused,
    main,
    start_local_store,
)
from jms_kapps.evaluation.spec import SpecRefused, configurations, read_spec

EVALUATION = Path(harness.__file__).parent

SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 1

[[sweep]]
topology = "cross"
prefill = "{topology}-1k.ttl"
"""

# A spec with a topology file next to it, a prefill seed and a heavy configuration.
FILE_SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 1
prefill_seed = 7

[[sweep]]
topology = "pair.json"
prefill = "{topology}-1k.ttl"

[[sweep]]
topology = "pair.json"
prefill = "{topology}-1k.ttl"
boxes_per_minute = 12
heavy = true
"""


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    # The command sets connection variables for its process: a copy keeps them from the tests
    # after this one.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


class Started:
    """In place of the start of the local GraphDB: it keeps that it was asked, and sets the local
    variables as the real start does."""

    def __init__(self, monkeypatch) -> None:
        self.times = 0
        self.monkeypatch = monkeypatch

    def __call__(self, *_: object) -> None:
        self.times += 1
        self.monkeypatch.setenv("TRIPLESTORE_URL", harness.LOCAL_STORE_URL)
        self.monkeypatch.setenv("TRIPLESTORE_USERNAME", "local-user")
        self.monkeypatch.setenv("TRIPLESTORE_PASSWORD", "local-secret")


@pytest.fixture
def started(monkeypatch):
    stub = Started(monkeypatch)
    monkeypatch.setattr(harness, "start_local_store", stub)
    return stub


class FabricatedRuns:
    """In place of a run on the configured store: each UC2 run writes a fabricated record with the
    sweep fields it was given, and keeps which run it was and the covered record it was given.
    Its throughput swings from one repetition to the next, so a configuration under the precision
    rule never meets it; with `zero_median` no transport ends, and a configuration stops at
    its minimum of valid runs."""

    def __init__(self, failing: set[int] | None = None) -> None:
        self.failing = failing or set()
        self.zero_median = False
        self.made: list[tuple[int, int]] = []
        self.covered: dict[int, Covered | None] = {}
        self.configurations: dict[int, Configuration] = {}
        self.log: list[str] = []

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        approval: Covered | None = None,
        **_: object,
    ) -> dict[str, Any]:
        pair = (sweep["configuration"], sweep["repetition"])
        self.made.append(pair)
        self.covered[pair[0]] = approval
        self.configurations[pair[0]] = configuration
        self.log.append(f"run {pair[0]}")
        failed = pair[0] in self.failing
        throughput = 0.0 if self.zero_median else (10.0 if pair[1] % 2 else 50.0)
        found = record(
            f"run-{pair[0]}-{pair[1]}",
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 100,
            transports_ended_per_minute=throughput,
            valid=not failed,
            outcome="failed: RuntimeError: the store went away"
            if failed
            else "completed",
        )
        found["sweep"] = dict(sweep)
        write(runs, found)
        return found


class FabricatedReplays:
    """In place of a replay level on the configured store."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.made: list[tuple[int, float, Covered | None]] = []

    def __call__(
        self,
        recorded: Path,
        *,
        runs: Path,
        factor: float,
        sweep: dict[str, Any],
        approval: Covered | None = None,
    ) -> dict[str, Any]:
        replayed = json.loads(recorded.read_text(encoding="utf-8"))
        self.made.append((sweep["configuration"], factor, approval))
        self.log.append(f"replay {sweep['configuration']} x{factor:g}")
        found = record(
            f"replay-{sweep['configuration']}-x{factor:g}",
            run_kind="baseline",
            replays=replayed["run_id"],
            requests=[request(0.01)] * 100,
        )
        found["sweep"] = dict(sweep)
        found["replay_level"] = {
            "factor": factor,
            "offered_per_minute": 100.0 * factor,
            "achieved_per_minute": 100.0 * factor,
            "requests_in_window": 100,
            "answered_in_window": 100,
            "refused_in_window": 0,
            "timed_out_in_window": 0,
            "heap_refusals_in_window": 0,
            "replay_span_seconds": 60.0 / factor,
            "broken": False,
            "signals": [],
        }
        write(runs, found)
        return found


@pytest.fixture
def stand_ins(monkeypatch):
    """The runs and the replays are stand-ins; returns both."""
    runs = FabricatedRuns()
    replays = FabricatedReplays(runs.log)
    monkeypatch.setattr(harness, "run_on_the_configured_store", runs)
    monkeypatch.setattr(harness, "run_replay_on_the_configured_store", replays)
    return runs, replays


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TRIPLESTORE_URL", "http://graphdb.test")
    monkeypatch.setenv("TRIPLESTORE_USERNAME", "harness-user-x7")
    monkeypatch.setenv("TRIPLESTORE_PASSWORD", "harness-secret-q9z")


def spec_file(folder: Path, text: str = SPEC, name: str = "spec.toml") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def validation_copy(folder: Path) -> Path:
    """A copy of the validation spec in `folder`: another file than the shipped spec, so that an
    approval of it covers no spec of the repository."""
    return spec_file(
        folder, VALIDATION_SPEC.read_text(encoding="utf-8"), "validation-copy.toml"
    )


def call(tmp_path: Path, spec: Path | None, *options: str) -> int:
    given = [] if spec is None else [str(spec)]
    return main(
        [
            *given,
            "--output",
            str(tmp_path / "out"),
            "--prefills",
            str(tmp_path / "prefills"),
            *options,
        ]
    )


# One command runs a spec and ends with the report and the table.


def test_one_command_runs_a_spec_and_ends_with_the_report_and_the_table(
    tmp_path, configured, stand_ins, capsys
):
    code = call(tmp_path, spec_file(tmp_path))

    assert code == 0
    out = tmp_path / "out"
    assert stand_ins[0].made == [(1, 1)]
    assert (out / "report" / "report.md").is_file()
    assert (out / "table.md").is_file()
    assert (out / "table.json").is_file()
    said = capsys.readouterr().out
    assert str(out / "report" / "report.md") in said
    assert str(out / "table.md") in said


def test_the_report_and_the_table_are_made_after_a_run_that_failed(
    tmp_path, configured, stand_ins
):
    stand_ins[0].failing = {1}

    code = call(tmp_path, spec_file(tmp_path))

    assert code == 1
    assert (tmp_path / "out" / "report" / "report.md").is_file()
    assert (tmp_path / "out" / "table.md").is_file()


def test_a_second_call_makes_no_run_and_makes_the_report_and_the_table_again(
    tmp_path, configured, stand_ins
):
    spec = spec_file(tmp_path)
    call(tmp_path, spec)
    (tmp_path / "out" / "table.md").unlink()

    code = call(tmp_path, spec)

    assert code == 0
    assert stand_ins[0].made == [(1, 1)]
    assert (tmp_path / "out" / "table.md").is_file()


def test_the_validation_spec_is_the_shipped_file():
    assert VALIDATION_SPEC == EVALUATION / "specs" / "validation.toml"
    assert VALIDATION_SPEC.is_file()


def test_without_an_argument_the_dry_run_is_of_the_validation_spec(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    code = main(["--dry-run"])

    assert code == 0
    assert str(VALIDATION_SPEC) in capsys.readouterr().out


# The store is picked as ./run-uc2 picks it.


def test_the_configured_store_is_used_with_run_uc2_s_message(
    tmp_path, configured, stand_ins, started, capsys
):
    call(tmp_path, spec_file(tmp_path))

    assert (
        "jms-eval: the run uses the store that TRIPLESTORE_URL names, http://graphdb.test"
        in capsys.readouterr().out
    )
    assert started.times == 0


def test_the_deprecated_variables_are_used_with_the_deprecation_message(
    tmp_path, monkeypatch, stand_ins, started, capsys
):
    monkeypatch.setenv("GRAPHDB_URL", "http://old.test")
    monkeypatch.setenv("GRAPHDB_USERNAME", "old-user")
    monkeypatch.setenv("GRAPHDB_PASSWORD", "old-secret")

    code = call(tmp_path, spec_file(tmp_path))

    assert code == 0
    said = capsys.readouterr().out
    assert (
        "jms-eval: GRAPHDB_URL is deprecated; set TRIPLESTORE_URL, which replaces it. "
        "The run uses the store that GRAPHDB_URL names, http://old.test" in said
    )
    assert started.times == 0
    assert os.environ["TRIPLESTORE_URL"] == "http://old.test"
    assert os.environ["TRIPLESTORE_USERNAME"] == "old-user"


def test_without_a_configured_store_the_local_fallback_is_started(
    tmp_path, stand_ins, started, capsys
):
    code = call(tmp_path, spec_file(tmp_path))

    assert code == 0
    assert started.times == 1
    assert (
        "jms-eval: TRIPLESTORE_URL is not set, so the run uses the local GraphDB of "
        "compose.yaml, http://localhost:7200" in capsys.readouterr().out
    )
    assert stand_ins[0].made == [(1, 1)]


def test_a_store_without_its_login_refuses_before_a_run(
    tmp_path, monkeypatch, stand_ins, started, capsys
):
    monkeypatch.setenv("TRIPLESTORE_URL", "http://graphdb.test")

    code = call(tmp_path, spec_file(tmp_path))

    assert code == 2
    assert "TRIPLESTORE_USERNAME" in capsys.readouterr().err
    assert stand_ins[0].made == []
    assert not (tmp_path / "prefills").exists()


def test_the_start_of_the_local_store_runs_compose_in_the_checkout(
    tmp_path, monkeypatch
):
    (tmp_path / "compose.yaml").write_text("name: x\n", encoding="utf-8")
    called: list[tuple[list[str], Path | None]] = []

    def run(command, **options):
        called.append((list(command), options.get("cwd")))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(harness.subprocess, "run", run)
    monkeypatch.setattr(harness.shutil, "which", lambda name: f"/usr/bin/{name}")

    start_local_store(tmp_path)

    assert (["docker", "compose", "up", "-d", "--wait"], tmp_path) in called
    assert os.environ["TRIPLESTORE_URL"] == "http://localhost:7200"
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"TRIPLESTORE_{name}")


def test_the_start_of_the_local_store_falls_back_to_the_standalone_compose(
    tmp_path, monkeypatch
):
    (tmp_path / "compose.yaml").write_text("name: x\n", encoding="utf-8")
    called: list[list[str]] = []

    def run(command, **options):
        called.append(list(command))
        code = 1 if command[:2] == ["docker", "compose"] else 0
        return subprocess.CompletedProcess(command, code)

    monkeypatch.setattr(harness.subprocess, "run", run)
    monkeypatch.setattr(harness.shutil, "which", lambda name: f"/usr/bin/{name}")

    start_local_store(tmp_path)

    assert ["docker-compose", "up", "-d", "--wait"] in called
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"TRIPLESTORE_{name}")


def test_the_local_store_needs_a_checkout_and_compose(tmp_path, monkeypatch):
    with pytest.raises(HarnessRefused, match="checkout"):
        start_local_store(tmp_path)

    (tmp_path / "compose.yaml").write_text("name: x\n", encoding="utf-8")
    monkeypatch.setattr(harness.shutil, "which", lambda name: None)
    with pytest.raises(HarnessRefused, match="neither `docker compose` nor"):
        start_local_store(tmp_path)


# The missing prefill files are made.


def test_a_missing_prefill_is_made_before_the_first_run_as_the_generator_makes_it(
    tmp_path, configured, monkeypatch
):
    present_at_the_run: list[bool] = []
    runs = FabricatedRuns()

    def checking(configuration: Configuration, **options: Any) -> dict[str, Any]:
        assert configuration.prefill is not None
        present_at_the_run.append(configuration.prefill.is_file())
        return runs(configuration, **options)

    monkeypatch.setattr(harness, "run_on_the_configured_store", checking)

    code = call(tmp_path, spec_file(tmp_path))

    assert code == 0
    assert present_at_the_run == [True]
    alone = tmp_path / "alone.ttl"
    prefill.write_prefill(alone, topology="cross", size=1000, seed=1)
    assert (tmp_path / "prefills" / "cross-1k.ttl").read_bytes() == alone.read_bytes()


def test_the_size_comes_from_the_name_the_topology_from_the_file_and_the_seed_from_the_spec(
    tmp_path, configured, stand_ins
):
    shutil.copy(EVALUATION / "topologies" / "grid-60.json", tmp_path / "pair.json")
    spec = spec_file(tmp_path, FILE_SPEC)

    code = call(
        tmp_path, spec, "--heavy-run-approval", str(approval_file(tmp_path, spec))
    )

    assert code == 0
    alone = tmp_path / "alone.ttl"
    prefill.write_prefill(
        alone, topology=str((tmp_path / "pair.json").resolve()), size=1000, seed=7
    )
    made = tmp_path / "prefills" / "pair-1k.ttl"
    assert made.read_bytes() == alone.read_bytes()
    assert "# seed: 7" in made.read_text(encoding="utf-8").splitlines()[:12]


def test_a_prefill_that_is_there_is_not_made_again(tmp_path, configured, stand_ins):
    folder = tmp_path / "prefills"
    folder.mkdir()
    (folder / "cross-1k.ttl").write_text(
        "# a file of the reader's own\n", encoding="utf-8"
    )

    code = call(tmp_path, spec_file(tmp_path))

    assert code == 0
    assert (folder / "cross-1k.ttl").read_text(encoding="utf-8") == (
        "# a file of the reader's own\n"
    )


def test_a_missing_prefill_whose_name_gives_no_size_is_refused_before_anything_runs(
    tmp_path, configured, stand_ins, capsys
):
    spec = spec_file(tmp_path, SPEC.replace("{topology}-1k.ttl", "mine.ttl"))

    code = call(tmp_path, spec)

    assert code == 2
    assert "gives no size" in capsys.readouterr().err
    assert stand_ins[0].made == []


def test_a_size_the_generator_refuses_is_refused_before_a_run(
    tmp_path, configured, stand_ins, capsys
):
    spec = spec_file(tmp_path, SPEC.replace("{topology}-1k.ttl", "{topology}-1.ttl"))

    code = call(tmp_path, spec)

    assert code == 2
    assert "cannot be written within 1 %" in capsys.readouterr().err
    assert stand_ins[0].made == []


def test_a_spec_gives_a_prefill_seed_outside_the_configuration_s_key(tmp_path):
    without = configurations(read_spec(spec_file(tmp_path)))
    seeded = read_spec(
        spec_file(
            tmp_path, SPEC.replace("[defaults]\n", "[defaults]\nprefill_seed = 5\n")
        )
    )

    assert seeded.prefill_seed == 5
    assert [c.key for c in configurations(seeded)] == [c.key for c in without]
    assert read_spec(spec_file(tmp_path)).prefill_seed is None
    with pytest.raises(SpecRefused, match="prefill_seed is a whole number from 0"):
        read_spec(
            spec_file(
                tmp_path,
                SPEC.replace("[defaults]\n", "[defaults]\nprefill_seed = -1\n"),
            )
        )


# The validation spec covers each claimed figure once.


def test_the_validation_spec_covers_each_claimed_figure_once():
    spec = read_spec(VALIDATION_SPEC)
    found = configurations(spec)
    centre, larger, no_shacl, *ramp = found

    assert len(found) == 6
    assert centre.values["wrapper"] is True  # write throughput and OGM latency
    assert (centre.values["prefill"], larger.values["prefill"]) == (
        "cross-1k.ttl",
        "cross-100k.ttl",
    )  # store scaling
    assert centre.values["shacl"] is True and no_shacl.values["shacl"] is False
    assert {k: v for k, v in centre.values.items() if k != "prefill"} == {
        k: v for k, v in larger.values.items() if k != "prefill"
    }
    assert {k: v for k, v in centre.values.items() if k != "shacl"} == {
        k: v for k, v in no_shacl.values.items() if k != "shacl"
    }
    assert centre.replay_factors == (1.0, 2.0)  # the compressed replay
    assert [c.ramp for c in ramp] == [4, 4, 4]  # the judgement of a ramp
    assert [c.values["boxes_per_minute"] for c in ramp] == [12, 24, 48]
    assert all(c.repetitions == 1 for c in found)
    assert all(c.values["window_seconds"] <= 60 for c in found)
    assert spec.precision is None


def test_the_spec_and_the_readme_state_the_run_time_with_and_without_the_heavy_parts():
    for text in (
        VALIDATION_SPEC.read_text(encoding="utf-8"),
        (EVALUATION / "README.md").read_text(encoding="utf-8"),
    ):
        assert "without the heavy parts: about 15 minutes" in text
        assert "with the heavy parts: about 27 minutes" in text


def test_the_readme_lists_every_shipped_spec_with_how_to_run_it():
    readme = (EVALUATION / "README.md").read_text(encoding="utf-8")
    shipped = sorted((EVALUATION / "specs").glob("*.toml"))

    assert len(shipped) >= 4
    for path in shipped:
        assert f"specs/{path.name}`" in readme, path.name
        how = (
            "`uv run jms-eval`"
            if path == VALIDATION_SPEC
            else f"jms-eval src/jms_kapps/evaluation/specs/{path.name}"
        )
        assert how in readme, path.name


# Without an approval the heavy parts are skipped and listed.


def test_without_an_approval_the_command_runs_what_needs_none_and_lists_the_heavy_parts(
    tmp_path, configured, stand_ins, capsys
):
    runs, replays = stand_ins

    code = call(tmp_path, validation_copy(tmp_path))

    assert code == 0
    assert runs.log == ["run 1", "replay 1 x1", "run 2", "run 3"]
    assert runs.made == [(1, 1), (2, 1), (3, 1)]
    assert [(n, f) for n, f, _ in replays.made] == [(1, 1.0)]
    assert all(approval is None for _, _, approval in replays.made)
    said = capsys.readouterr().out
    assert "skipped 2 heavy parts" in said
    assert (
        "the ramp of block 4: 3 levels not run yet, at 12, 24, 48 boxes per minute"
        in said
    )
    assert "the replay of configuration 1 at the factor 2" in said
    assert "--heavy-run-approval" in said
    assert "sha256sum" in said
    assert (tmp_path / "out" / "table.md").is_file()


def test_the_heavy_parts_run_under_an_approval_that_covers_the_spec(
    tmp_path, configured, stand_ins, capsys
):
    runs, replays = stand_ins
    spec = validation_copy(tmp_path)

    code = call(
        tmp_path, spec, "--heavy-run-approval", str(approval_file(tmp_path, spec))
    )

    assert code == 0
    assert runs.made == [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1)]
    assert [(n, f) for n, f, _ in replays.made] == [(1, 1.0), (1, 2.0)]
    # Through approval.cover only: a heavy run is given a covered record, a light one none.
    assert all(isinstance(runs.covered[n], Covered) for n in (4, 5, 6))
    assert all(runs.covered[n] is None for n in (1, 2, 3))
    assert [type(a) for _, f, a in replays.made if f == 2.0] == [Covered]
    assert "skipped" not in capsys.readouterr().out


def test_an_approval_of_another_spec_skips_the_heavy_parts_with_the_reason(
    tmp_path, configured, stand_ins, capsys
):
    """The normal runs are made, and each heavy run is listed as skipped ."""
    spec = validation_copy(tmp_path)
    other = spec_file(tmp_path, SPEC, "other.toml")

    code = call(
        tmp_path, spec, "--heavy-run-approval", str(approval_file(tmp_path, other))
    )

    assert code == 0
    assert stand_ins[0].made == [(1, 1), (2, 1), (3, 1)]
    assert [(n, f) for n, f, _ in stand_ins[1].made] == [(1, 1.0)]
    out = capsys.readouterr().out
    assert "skipped 4 heavy runs" in out
    assert "another or a changed spec" in out and "it is another spec" in out


def test_an_approval_outside_its_period_skips_the_heavy_parts_with_the_reason(
    tmp_path, configured, stand_ins, capsys
):
    from datetime import UTC, datetime, timedelta

    spec = validation_copy(tmp_path)
    later = datetime.now(UTC) + timedelta(days=2)

    code = call(
        tmp_path,
        spec,
        "--heavy-run-approval",
        str(approval_file(tmp_path, spec, start=later, end=later + timedelta(hours=1))),
    )

    assert code == 0
    assert stand_ins[0].made == [(1, 1), (2, 1), (3, 1)]
    out = capsys.readouterr().out
    assert "a period that is not now" in out and "outside the period" in out


def test_a_heavy_configuration_of_a_spec_is_skipped_without_an_approval(
    tmp_path, configured, stand_ins, capsys
):
    shutil.copy(EVALUATION / "topologies" / "grid-60.json", tmp_path / "pair.json")
    spec = spec_file(tmp_path, FILE_SPEC)

    code = call(tmp_path, spec)

    assert code == 0
    assert stand_ins[0].made == [(1, 1)]
    assert "configuration 2 (heavy = true): 1 runs" in capsys.readouterr().out


# The dry run.


def test_the_dry_run_of_the_validation_spec_needs_no_store_and_makes_nothing(
    tmp_path, monkeypatch, capsys
):
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a dry run reaches no store and starts nothing")

    monkeypatch.setattr(harness, "start_local_store", refuse)
    monkeypatch.setattr(harness, "run_on_the_configured_store", refuse)
    monkeypatch.setattr(harness, "run_replay_on_the_configured_store", refuse)
    monkeypatch.setattr(harness.subprocess, "run", refuse)

    code = call(tmp_path, None, "--dry-run")

    assert code == 0
    said = capsys.readouterr().out
    assert "6 configurations" in said  # the sweep's listing
    assert "the run would use the local GraphDB of compose.yaml" in said
    assert "2 prefill files to make" in said
    assert "cross-1k.ttl" in said and "cross-100k.ttl" in said
    assert "3 runs and 1 replay levels to make without an approval" in said
    assert "the ramp of block 4" in said
    assert "the replay of configuration 1 at the factor 2" in said
    assert not (tmp_path / "prefills").exists()
    assert not (tmp_path / "out").exists()


def test_the_dry_run_names_the_store_the_environment_picks(
    tmp_path, configured, capsys
):
    call(tmp_path, None, "--dry-run")

    assert (
        "the run would use the store that TRIPLESTORE_URL names, http://graphdb.test"
        in capsys.readouterr().out
    )


def test_the_dry_run_with_an_approval_says_the_heavy_parts_would_run(tmp_path, capsys):
    spec = validation_copy(tmp_path)

    call(
        tmp_path,
        spec,
        "--dry-run",
        "--heavy-run-approval",
        str(approval_file(tmp_path, spec)),
    )

    said = capsys.readouterr().out
    assert "the heavy parts would run under the approval of Test Person" in said
    assert "skipped" not in said


def test_the_dry_run_lists_the_runs_that_are_left_after_a_first_call(
    tmp_path, configured, stand_ins, capsys
):
    spec = validation_copy(tmp_path)
    call(tmp_path, spec)
    capsys.readouterr()

    call(tmp_path, spec, "--dry-run")

    said = capsys.readouterr().out
    assert "0 prefill files to make" in said
    assert "0 runs and 0 replay levels to make without an approval" in said


# The rounds: the command goes on round after round, takes the sweep's stop time
# and obeys its stop file.

PRECISION_SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false

[precision]
minimum_runs = 2
maximum_runs = 3

[[sweep]]
topology = ["cross", "pair"]
"""

START = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)


class Clock:
    """In place of this machine's clock: each run moves it on by 12 minutes."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def timed(monkeypatch, runs: FabricatedRuns, clock: Clock, during=None) -> None:
    """Each run of `runs` takes 12 minutes of `clock`; `during` is called in it with its place
    and the output folder, as a person who acts while it runs."""

    def run(configuration: Configuration, **options: Any) -> dict[str, Any]:
        if during is not None:
            during(len(runs.made) + 1, options["runs"])
        clock.now += timedelta(minutes=12)
        return runs(configuration, **options)

    monkeypatch.setattr(harness, "run_on_the_configured_store", run)


def rounds_call(tmp_path: Path, clock: Clock, *options: str) -> int:
    return main(
        [
            str(spec_file(tmp_path, PRECISION_SPEC)),
            "--output",
            str(tmp_path / "out"),
            "--prefills",
            str(tmp_path / "prefills"),
            *options,
        ],
        clock=clock,
    )


def test_the_command_goes_on_round_after_round_until_no_configuration_is_open(
    tmp_path, configured, stand_ins, monkeypatch, capsys
):
    clock = Clock()
    timed(monkeypatch, stand_ins[0], clock)

    assert rounds_call(tmp_path, clock) == 0

    # The throughput of a fabricated run swings, so no configuration meets the rule: 3 rounds.
    assert stand_ins[0].made == [(1, 1), (2, 1), (1, 2), (2, 2), (1, 3), (2, 3)]
    said = capsys.readouterr().out
    assert "this call starts at round 1 and goes on round after round" in said
    assert "no selected configuration under the precision rule is open" in said
    assert (tmp_path / "out" / "table.md").is_file()


def test_the_command_stops_a_configuration_whose_throughput_median_is_0(
    tmp_path, configured, stand_ins, monkeypatch, capsys
):
    """No transport ends in a run: each configuration stops at its minimum of 2 valid runs, the
    command makes no third round, and its table marks both."""
    clock = Clock()
    stand_ins[0].zero_median = True
    timed(monkeypatch, stand_ins[0], clock)

    assert rounds_call(tmp_path, clock) == 0

    assert stand_ins[0].made == [(1, 1), (2, 1), (1, 2), (2, 2)]
    assert "or stopped at a throughput median of 0" in capsys.readouterr().out
    table = (tmp_path / "out" / "table.md").read_text(encoding="utf-8")
    assert table.count("| stopped: throughput median 0 |") == 2


def test_the_command_stops_at_the_stop_time_and_makes_the_report_and_the_table(
    tmp_path, configured, stand_ins, monkeypatch, capsys
):
    clock = Clock()
    timed(monkeypatch, stand_ins[0], clock)

    code = rounds_call(tmp_path, clock, "--stop-at", "2026-10-13T20:30+02:00")

    assert code == 0
    # Runs start at 18:00, 18:12 and 18:24 UTC; the third ends after the stop time.
    assert stand_ins[0].made == [(1, 1), (2, 1), (1, 2)]
    assert "the stop time 2026-10-13 18:30 UTC has come" in capsys.readouterr().out
    assert (tmp_path / "out" / "table.md").is_file()


def test_the_command_obeys_the_stop_file_and_the_next_call_goes_on(
    tmp_path, configured, stand_ins, monkeypatch, capsys
):
    clock = Clock()

    def stop_in_run_2(place: int, output: Path) -> None:
        if place == 2:
            (output / sweep.STOP_FILE).write_text("", encoding="utf-8")

    timed(monkeypatch, stand_ins[0], clock, during=stop_in_run_2)

    assert rounds_call(tmp_path, clock) == 0
    assert stand_ins[0].made == [(1, 1), (2, 1)]
    assert not (tmp_path / "out" / sweep.STOP_FILE).exists()
    assert "the stop file" in capsys.readouterr().out

    timed(monkeypatch, stand_ins[0], clock)
    assert rounds_call(tmp_path, clock) == 0
    assert stand_ins[0].made[2:] == [(1, 2), (2, 2), (1, 3), (2, 3)]


def test_the_command_refuses_a_stop_time_without_a_zone(tmp_path, stand_ins, capsys):
    assert rounds_call(tmp_path, Clock(), "--stop-at", "2026-10-14T06:00") == 2
    assert "names its zone" in capsys.readouterr().err
    assert stand_ins[0].made == []


def test_the_command_refuses_a_precision_table_with_pass_sizes(
    tmp_path, configured, stand_ins, capsys
):
    spec = spec_file(
        tmp_path,
        PRECISION_SPEC.replace("[precision]\n", "[precision]\npass_sizes = [5, 5]\n"),
    )

    assert call(tmp_path, spec) == 2

    error = capsys.readouterr().err
    assert "pass_sizes" in error and "the rounds replaced them" in error
    assert stand_ins[0].made == []


def test_the_dry_run_says_the_round_the_call_would_start_at(tmp_path, capsys):
    call(tmp_path, spec_file(tmp_path, PRECISION_SPEC), "--dry-run")

    said = capsys.readouterr().out
    assert "precision rule: this call would start at round 1" in said
    assert "the next call starts at round 1: 2 runs of 2 open configurations" in said


def test_the_command_removes_a_stop_file_made_during_a_failed_run(
    tmp_path, configured, stand_ins, monkeypatch, capsys
):
    """A stop file never outlives the call: the next call is not stopped before its first run.
    The call ends at the third failed run in a row."""
    clock = Clock()
    stand_ins[0].failing = {1, 2}

    def stop_in_run_3(place: int, output: Path) -> None:
        if place == 3:
            (output / sweep.STOP_FILE).write_text("", encoding="utf-8")

    timed(monkeypatch, stand_ins[0], clock, during=stop_in_run_3)

    assert rounds_call(tmp_path, clock) == 1

    assert stand_ins[0].made == [(1, 1), (2, 1), (1, 2)]
    assert not (tmp_path / "out" / sweep.STOP_FILE).exists()
    assert "is removed" in capsys.readouterr().out


def test_the_command_leaves_out_a_configuration_that_the_sweep_removed(
    tmp_path, configured, stand_ins, monkeypatch, capsys
):
    """Removed configurations are stored in the output folder, and the one command, which has
    no selection, leaves them out until they are put back."""
    clock = Clock()
    timed(monkeypatch, stand_ins[0], clock)
    spec = spec_file(tmp_path, PRECISION_SPEC)
    output = str(tmp_path / "out")
    assert sweep.main([str(spec), "--runs", output, "--remove", "2"]) == 0

    assert rounds_call(tmp_path, clock) == 0
    assert stand_ins[0].made == [(1, 1), (1, 2), (1, 3)]
    assert "removed: configuration 2" in capsys.readouterr().out

    assert sweep.main([str(spec), "--runs", output, "--put-back", "2"]) == 0
    assert rounds_call(tmp_path, clock) == 0
    assert stand_ins[0].made[3:] == [(2, 1), (2, 2), (2, 3)]


def test_the_command_ends_a_round_with_nothing_to_make_with_its_own_message(
    tmp_path, configured, stand_ins, capsys
):
    """A record that the skip finds but whose summary cannot be made: the round plans it and
    has nothing to make."""
    spec = spec_file(tmp_path, PRECISION_SPEC)
    [first, _] = configurations(read_spec(spec))
    unreadable = record("unreadable", first, repetition=1, commit_latencies=[0.1] * 20)
    del unreadable["run_kind"]
    write(tmp_path / "out", unreadable)

    assert call(tmp_path, spec) == 1

    said = capsys.readouterr().out
    assert "round 1 has nothing to make" in said
    assert (tmp_path / "out" / "table.md").is_file()
