"""The sweep of the measurement harness, iteration one (jms_kapps 78): `jms-eval-sweep` runs the
runs of a spec one by one, each a run of one configuration (part 1), skips a run whose record
exists, and runs only the configurations and the repetitions that a selection names.

Each run here is part 1's real run against the stand-ins of `_evaluation_fakes`: a GraphDB's REST
API in memory, a bench graph whose writes are events, a supervisor that starts no process, the
spawn rate workflow and a clock that moves only when the run waits. The connection variables of
a store are removed first, so that no test here can reach one.
"""

from __future__ import annotations

import json
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from _evaluation_fakes import (
    Events,
    FakeBenchGraph,
    FakeClock,
    FakeGraphDB,
    FakeSpawnRate,
    FakeSupervisor,
)

import jms_kapps
from jms_kapps.evaluation import bench, sweep, wrapper
from jms_kapps.evaluation.configuration import (
    DEFAULT_BASE_SEED,
    Configuration,
    pick_seed,
    read_configuration,
)
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.sweep import main

ITERATION_ONE = (
    Path(jms_kapps.__file__).parent / "evaluation" / "specs" / "iteration-one.toml"
)

SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 2

[[sweep]]
topology = ["cross", "pair"]
shacl = [true, false]
"""


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


class Runs:
    """Part 1's run against stand-ins, in place of a run on the configured store. Each run it
    makes is kept, with the configuration and the sweep fields it was given."""

    def __init__(
        self,
        *,
        failing: set[int] | None = None,
        interrupted: set[int] | None = None,
    ) -> None:
        self.made: list[tuple[Configuration, dict[str, Any]]] = []
        self.failing = failing or set()
        self.interrupted = interrupted or set()

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any] | None = None,
        **_: object,
    ) -> dict:
        assert sweep is not None
        self.made.append((configuration, sweep))
        events = Events()
        store = FakeGraphDB(events=events)
        clock = FakeClock(events)
        # Each run a minute after the one before, so that no two runs share a run id.
        clock.start += timedelta(minutes=len(self.made))
        failing = len(self.made) in self.failing
        interrupted = len(self.made) in self.interrupted

        def supervisor_for(services: object, echo: Any) -> FakeSupervisor:
            supervisor = FakeSupervisor(
                events, echo, not_ready="Module1 is failed" if failing else None
            )
            if interrupted:
                # A stop signal at the readiness gate, as Ctrl+C there: part 1 ends the run as
                # interrupted.
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

    def made_runs(self) -> list[tuple[int, int]]:
        """Each run made, by its configuration's number and its repetition."""
        return [(s["configuration"], s["repetition"]) for _, s in self.made]


@pytest.fixture
def configured(monkeypatch):
    """A store configured in the environment, and part 1's run against stand-ins in place of a
    run on it."""
    runs = Runs()
    monkeypatch.setenv("TRIPLESTORE_URL", "http://graphdb.test")
    monkeypatch.setenv("TRIPLESTORE_USERNAME", "harness-user-x7")
    monkeypatch.setenv("TRIPLESTORE_PASSWORD", "harness-secret-q9z")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", runs)
    return runs


def spec_file(folder: Path, text: str = SPEC) -> Path:
    path = folder / "spec.toml"
    path.write_text(text, encoding="utf-8")
    return path


def records(runs: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(runs.glob("*/record.json"))
    ]


def test_the_sweep_runs_each_repetition_of_each_configuration_in_the_spec_s_order(
    tmp_path, configured
):
    runs = tmp_path / "runs"

    code = main([str(spec_file(tmp_path)), "--runs", str(runs)])

    assert code == 0
    assert configured.made_runs() == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
        (3, 1),
        (3, 2),
        (4, 1),
        (4, 2),
    ]
    assert [(c.topology, c.shacl) for c, _ in configured.made[::2]] == [
        ("cross", True),
        ("cross", False),
        ("pair", True),
        ("pair", False),
    ]
    assert len(records(runs)) == 8


def test_each_record_says_which_run_of_which_spec_it_is(tmp_path, configured):
    runs = tmp_path / "runs"
    spec = spec_file(tmp_path)

    main(
        [str(spec), "--runs", str(runs), "--configurations", "3", "--repetitions", "2"]
    )

    [record] = records(runs)
    [third] = [c for c in configurations(read_spec(spec)) if c.number == 3]
    assert record["sweep"] == {
        "spec": str(spec),
        "configuration": 3,
        "key": third.key,
        "repetition": 2,
        "repetitions": 2,
    }
    assert record["outcome"] == "completed"


def test_a_run_whose_record_exists_is_skipped(tmp_path, configured, capsys):
    """So that a sweep continues in a later time window: a second call makes no run twice."""
    runs = tmp_path / "runs"
    spec = spec_file(tmp_path)
    main([str(spec), "--runs", str(runs), "--repetitions", "1"])
    first = configured.made_runs()

    code = main([str(spec), "--runs", str(runs)])

    assert code == 0
    assert first == [(1, 1), (2, 1), (3, 1), (4, 1)]
    assert configured.made_runs()[4:] == [(1, 2), (2, 2), (3, 2), (4, 2)]
    assert len(records(runs)) == 8
    assert "4 have a record and are skipped" in capsys.readouterr().out

    again = main([str(spec), "--runs", str(runs)])

    assert again == 0
    assert len(configured.made) == 8


def test_a_run_is_found_again_where_the_spec_lists_its_configuration_elsewhere(
    tmp_path, configured
):
    runs = tmp_path / "runs"
    main([str(spec_file(tmp_path)), "--runs", str(runs)])
    reordered = SPEC.replace('["cross", "pair"]', '["pair", "cross"]').replace(
        "[true, false]", "[false, true]"
    )

    code = main([str(spec_file(tmp_path, reordered)), "--runs", str(runs)])

    assert code == 0
    assert len(configured.made) == 8


def test_a_selection_runs_only_the_configurations_and_repetitions_it_names(
    tmp_path, configured
):
    runs = tmp_path / "runs"

    code = main(
        [
            str(spec_file(tmp_path)),
            "--runs",
            str(runs),
            "--configurations",
            "2,4",
            "--repetitions",
            "2",
        ]
    )

    assert code == 0
    assert configured.made_runs() == [(2, 2), (4, 2)]


def test_a_selection_of_a_configuration_the_spec_does_not_have_is_refused(
    tmp_path, configured, capsys
):
    code = main([str(spec_file(tmp_path)), "--configurations", "3-5"])

    assert code == 2
    assert "5" in capsys.readouterr().err
    assert configured.made == []


def test_a_flag_sets_a_value_of_every_configuration(tmp_path, configured):
    runs = tmp_path / "runs"

    main(
        [
            str(spec_file(tmp_path)),
            "--runs",
            str(runs),
            "--set",
            "boxes_per_minute=3",
            "--set",
            "repetitions=1",
        ]
    )

    assert {c.boxes_per_minute for c, _ in configured.made} == {3.0}
    assert configured.made_runs() == [(1, 1), (2, 1), (3, 1), (4, 1)]


@pytest.mark.parametrize(
    "missing",
    [
        "boxes_per_minute",
        "warm_up_seconds",
        "window_seconds",
        "drain_bound_seconds",
        "matcher_interval_seconds",
    ],
)
def test_a_spec_that_misses_a_value_is_refused_before_any_run_starts(
    tmp_path, configured, capsys, missing
):
    runs = tmp_path / "runs"
    text = "".join(
        line + "\n" for line in SPEC.splitlines() if not line.startswith(missing)
    )

    code = main([str(spec_file(tmp_path, text)), "--runs", str(runs)])

    assert code == 2
    assert missing in capsys.readouterr().err
    assert configured.made == []
    assert not runs.exists()


def test_a_file_a_selected_configuration_names_is_checked_before_the_first_run(
    tmp_path, configured, capsys
):
    """The second configuration names a prefill file that is not there: the sweep refuses before
    it runs the first."""
    text = SPEC.replace(
        "shacl = [true, false]", 'prefill = ["none", "{topology}-1k.ttl"]'
    )

    code = main(
        [str(spec_file(tmp_path, text)), "--prefills", str(tmp_path / "prefills")]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "configuration 2" in err and "cross-1k.ttl" in err
    assert configured.made == []


def test_a_file_only_an_unselected_configuration_names_is_not_needed(
    tmp_path, configured
):
    text = SPEC.replace(
        "shacl = [true, false]", 'prefill = ["none", "{topology}-1k.ttl"]'
    )

    code = main(
        [
            str(spec_file(tmp_path, text)),
            "--runs",
            str(tmp_path / "runs"),
            "--configurations",
            "1,3",
        ]
    )

    assert code == 0
    assert configured.made_runs() == [(1, 1), (1, 2), (3, 1), (3, 2)]


def test_the_prefill_comes_from_the_prefills_folder(tmp_path, configured):
    from jms_kapps.evaluation.prefill import write_prefill

    prefills = tmp_path / "prefills"
    prefills.mkdir()
    write_prefill(prefills / "cross-1k.ttl", topology="cross", size=1000, seed=78)
    text = SPEC.replace('["cross", "pair"]', '"cross"').replace(
        "shacl = [true, false]", 'prefill = "{topology}-1k.ttl"\nrepetitions = 1'
    )

    code = main(
        [
            str(spec_file(tmp_path, text)),
            "--runs",
            str(tmp_path / "runs"),
            "--prefills",
            str(prefills),
        ]
    )

    assert code == 0
    [(configuration, _)] = configured.made
    assert configuration.prefill == prefills / "cross-1k.ttl"
    [record] = records(tmp_path / "runs")
    assert "# seed: 78" in record["prefill"]["header"]


def test_a_heavy_configuration_is_skipped_without_an_approval(
    tmp_path, configured, capsys
):
    """A heavy run runs only under an approval that covers it (jms_kapps 96); without one it is
    skipped and listed, as if it was not selected (jms_kapps 112: until 2026-10-14 the call was
    refused before its first run)."""
    text = SPEC.replace("heavy = false", "heavy = true")

    code = main([str(spec_file(tmp_path, text)), "--runs", str(tmp_path / "runs")])

    assert code == 0
    out = capsys.readouterr().out
    assert "skipped 8 heavy runs" in out
    assert "--heavy-run-approval" in out and "any person may approve" in out
    assert configured.made == []


def test_without_opentelemetry_a_configuration_with_the_wrapper_is_refused_before_any_run(
    tmp_path, configured, monkeypatch, capsys
):
    """A plain install has no OpenTelemetry, the extra `evaluation` (jms_kapps 48): the spec's
    runs are observed by default, so the sweep refuses them before the first; a spec that turns
    the wrapper off runs."""
    monkeypatch.setattr(wrapper, "missing", lambda: ["opentelemetry.sdk"])

    code = main([str(spec_file(tmp_path)), "--runs", str(tmp_path / "runs")])

    assert code == 2
    assert "uv sync --extra evaluation" in capsys.readouterr().err
    assert configured.made == []

    unwrapped = SPEC.replace("heavy = false", "heavy = false\nwrapper = false")
    code = main(
        [str(spec_file(tmp_path, unwrapped)), "--runs", str(tmp_path / "unwrapped")]
    )

    assert code == 0
    assert configured.made
    assert all(not configuration.wrapper for configuration, _ in configured.made)


def test_without_a_configured_store_the_sweep_does_not_start(tmp_path, capsys):
    runs = tmp_path / "runs"

    code = main([str(spec_file(tmp_path)), "--runs", str(runs)])

    assert code == 2
    assert "TRIPLESTORE_URL" in capsys.readouterr().err
    assert not runs.exists()


def test_the_sweep_goes_on_after_a_run_that_fails(tmp_path, monkeypatch):
    """A run that fails has its record, so the next call skips it; the sweep goes on after it
    (jms_kapps 112: until 2026-10-14 it stopped at it, and the next call went on after it)."""
    runs = tmp_path / "runs"
    failing = Runs(failing={2})
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", failing)
    spec = spec_file(tmp_path)

    code = main([str(spec), "--runs", str(runs)])

    assert code == 1
    assert failing.made_runs() == [
        (1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2), (4, 1), (4, 2)
    ]  # fmt: skip
    assert sorted(r["outcome"].split(":")[0] for r in records(runs)) == [
        *["completed"] * 7,
        "failed",
    ]

    again = main([str(spec), "--runs", str(runs)])

    assert again == 0
    assert len(failing.made_runs()) == 8


def test_the_list_shows_each_configuration_and_its_runs_and_needs_no_store(
    tmp_path, capsys
):
    runs = tmp_path / "runs"

    code = main([str(spec_file(tmp_path)), "--runs", str(runs), "--list"])

    assert code == 0
    out = capsys.readouterr().out
    assert "4 configurations, 8 runs" in out
    assert "configuration spec 1" in out and "configuration spec 4" in out
    assert "topology = pair" in out
    assert not runs.exists()


def test_the_list_of_the_iteration_one_spec_needs_none_of_its_files(capsys):
    """The grid files and the prefill files are a person's work, and may not be there yet."""
    code = main([str(ITERATION_ONE), "--list", "--runs", "no-such-runs-folder"])

    assert code == 0
    assert "25 configurations, 125 runs" in capsys.readouterr().out


def test_a_record_of_no_sweep_or_no_json_is_no_run_of_the_sweep(tmp_path):
    runs = tmp_path / "runs"
    for name, text in [
        ("a", '{"run_id": "a", "sweep": null}'),
        ("b", "not json"),
        ("c", '{"sweep": {"key": "k1"}}'),
        ("d", '["a list"]'),
        ("e", '{"outcome": "completed", "sweep": {"key": "k2", "repetition": 3}}'),
    ]:
        (runs / name).mkdir(parents=True)
        (runs / name / "record.json").write_text(text, encoding="utf-8")

    assert sweep.recorded(runs) == {("k2", 3): "completed"}
    assert sweep.recorded(tmp_path / "no-runs-yet") == {}


def test_the_readme_says_how_to_make_each_file_the_iteration_one_spec_names():
    readme = (ITERATION_ONE.parent.parent / "README.md").read_text(encoding="utf-8")
    sweep_section = readme[readme.index("## The sweep") :]

    for named in (
        "jms-eval-sweep",
        "specs/iteration-one.toml",
        "src/jms_kapps/evaluation/topologies/grid-60.json",
        "src/jms_kapps/evaluation/topologies/grid-120.json",
        "python -m jms_kapps.evaluation.prefill",
        'prefills/$topology-$size.ttl"',
        'prefills/$grid-$size.ttl"',
        "for size in 1k 100k 1M 10M",
        "specs/after-series.toml",
        "The precision rule and the rounds",
        "two time windows",
        "no module possession states",
    ):
        assert named in sweep_section, named
    prefills = {c.values["prefill"] for c in configurations(read_spec(ITERATION_ONE))}
    assert prefills == {
        f"{topology}-{size}.ttl"
        for topology in ("cross", "grid", "grid-60", "grid-120")
        for size in ("1k", "100k", "1M", "10M")
    }


def test_the_list_shows_the_outcome_of_each_run_that_has_a_record(
    tmp_path, monkeypatch, capsys
):
    """A run that failed is skipped as a completed one is: the list says which is which."""
    runs = tmp_path / "runs"
    failing = Runs(failing={2})
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", failing)
    spec = spec_file(tmp_path)
    main([str(spec), "--runs", str(runs)])
    capsys.readouterr()

    main([str(spec), "--runs", str(runs), "--list"])

    out = capsys.readouterr().out
    assert "4 configurations, 8 runs, 8 with a record" in out
    assert "with a record: 1 completed, 2 failed:" in out


def store_configured(monkeypatch, runs: Runs) -> None:
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", runs)


def test_a_run_that_a_stop_signal_ended_runs_again_and_its_record_is_kept(
    tmp_path, monkeypatch
):
    """An interrupted run is no measurement: the next call makes it again (decided on
    2026-10-11, note on jms_kapps 78). Its record stays in its own folder, beside the new one."""
    runs = tmp_path / "runs"
    made = Runs(interrupted={2})
    store_configured(monkeypatch, made)
    spec = spec_file(tmp_path)

    first = main([str(spec), "--runs", str(runs), "--configurations", "1"])
    second = main([str(spec), "--runs", str(runs), "--configurations", "1"])

    assert (first, second) == (1, 0)
    assert made.made_runs() == [(1, 1), (1, 2), (1, 2)]
    outcomes = sorted(
        (r["sweep"]["repetition"], r["outcome"].split(":")[0]) for r in records(runs)
    )
    assert outcomes == [(1, "completed"), (2, "completed"), (2, "interrupted")]


@pytest.mark.parametrize(
    ("outcome", "made_again"),
    [
        ("completed", False),
        ("failed: StoreRefused: the store refused", False),
        ("interrupted", True),
    ],
)
def test_only_a_run_whose_record_says_interrupted_is_made_again(
    tmp_path, configured, outcome, made_again
):
    spec = spec_file(tmp_path)
    [first] = [c for c in configurations(read_spec(spec)) if c.number == 1]
    runs = tmp_path / "runs"
    (runs / "earlier").mkdir(parents=True)
    (runs / "earlier" / "record.json").write_text(
        json.dumps({"outcome": outcome, "sweep": {"key": first.key, "repetition": 1}}),
        encoding="utf-8",
    )

    main(
        [str(spec), "--runs", str(runs), "--configurations", "1", "--repetitions", "1"]
    )

    assert configured.made_runs() == ([(1, 1)] if made_again else [])
    assert (runs / "earlier" / "record.json").is_file()


def test_a_run_with_an_interrupted_and_a_completed_record_is_done(tmp_path):
    runs = tmp_path / "runs"
    for name, outcome in [("a", "completed"), ("b", "interrupted")]:
        (runs / name).mkdir(parents=True)
        (runs / name / "record.json").write_text(
            json.dumps({"outcome": outcome, "sweep": {"key": "k", "repetition": 1}}),
            encoding="utf-8",
        )

    found = sweep.recorded(runs)

    assert found == {("k", 1): "completed"}
    assert sweep.has_run(found, "k", 1)
    assert not sweep.has_run({("k", 1): "interrupted"}, "k", 1)
    assert not sweep.has_run({}, "k", 1)


# The report after each run (jms_kapps 86).


@pytest.fixture
def reports(monkeypatch):
    """The report's `update_report`, spied on: each call's folder and the record it was given."""
    from jms_kapps.evaluation import report

    calls: list[dict[str, Any]] = []
    real = report.update_report

    def spy(spec, found, runs, *, fresh=None, **options):
        calls.append({"runs": runs, "fresh": fresh, "found": list(found)})
        return real(spec, found, runs, fresh=fresh, **options)

    monkeypatch.setattr(report, "update_report", spy)
    return calls


def test_the_report_is_made_after_each_run_and_is_current_while_the_sweep_goes_on(
    tmp_path, configured, reports
):
    runs = tmp_path / "runs"

    code = main([str(spec_file(tmp_path)), "--runs", str(runs), "--configurations", "1,2"])

    assert code == 0
    assert [c["fresh"]["sweep"]["configuration"] for c in reports] == [1, 1, 2, 2]
    assert all(c["runs"] == runs for c in reports)
    rows = (runs / "report" / "runs.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 + 4
    assert (runs / "report" / "report.md").is_file()


def test_the_report_gets_the_sweeps_overrides(tmp_path, configured, reports):
    runs = tmp_path / "runs"

    main([str(spec_file(tmp_path)), "--runs", str(runs), "--configurations", "1", "--repetitions", "1", "--set", "boxes_per_minute=12"])

    [call] = reports
    assert {c.values["boxes_per_minute"] for c in call["found"]} == {12}


@pytest.mark.parametrize("end", ["failed", "interrupted"])
def test_the_report_is_made_after_a_run_that_does_not_complete_and_before_the_sweep_stops(
    tmp_path, monkeypatch, reports, end
):
    runs = tmp_path / "runs"
    made = Runs(failing={2} if end == "failed" else set(), interrupted={2} if end == "interrupted" else set())
    store_configured(monkeypatch, made)

    code = main([str(spec_file(tmp_path)), "--runs", str(runs)])

    # A failed run does not stop the sweep, a stop signal does (jms_kapps 112).
    assert code == 1
    assert made.made_runs()[:2] == [(1, 1), (1, 2)]
    assert len(made.made_runs()) == (8 if end == "failed" else 2)
    assert [c["fresh"]["outcome"].split(":")[0] for c in reports][:2] == [
        "completed",
        end,
    ]
    frame = (runs / "report" / "runs.csv").read_text(encoding="utf-8")
    assert end in frame


def test_the_report_is_made_after_an_invalid_run(tmp_path, monkeypatch, reports):
    runs = tmp_path / "runs"
    made = Runs()
    store_configured(monkeypatch, made)
    real = made.__call__

    def invalid(*args: Any, **kwargs: Any) -> dict:
        found = real(*args, **kwargs)
        found["valid"] = False
        found["failed_checks"] = [{"check": "deadlock alarm"}]
        return found

    monkeypatch.setattr(sweep, "run_on_the_configured_store", invalid)

    main([str(spec_file(tmp_path)), "--runs", str(runs), "--configurations", "1", "--repetitions", "1"])

    [call] = reports
    assert call["fresh"]["valid"] is False
    assert "invalid" in (runs / "report" / "runs.csv").read_text(encoding="utf-8")


def test_a_report_error_is_told_and_neither_stops_nor_fails_the_sweep(
    tmp_path, configured, monkeypatch, capsys
):
    from jms_kapps.evaluation import report

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the chart broke")

    monkeypatch.setattr(report, "update_report", broken)
    runs = tmp_path / "runs"

    code = main([str(spec_file(tmp_path)), "--runs", str(runs), "--configurations", "1"])

    assert code == 0
    assert configured.made_runs() == [(1, 1), (1, 2)]
    out = capsys.readouterr().out
    assert out.count("the report of the runs could not be made") == 2
    assert "RuntimeError: the chart broke" in out
    assert len(records(runs)) == 2


def test_a_report_error_does_not_hide_the_failure_of_the_run(
    tmp_path, monkeypatch, capsys
):
    from jms_kapps.evaluation import report

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the chart broke")

    monkeypatch.setattr(report, "update_report", broken)
    made = Runs(failing={1})
    store_configured(monkeypatch, made)

    code = main([str(spec_file(tmp_path)), "--runs", str(tmp_path / "runs")])

    assert code == 1
    assert "failed run 1 of 3 in a row" in capsys.readouterr().out


def test_the_run_just_made_is_summarised_from_the_record_the_run_returned(
    tmp_path, configured
):
    """The summary beside the record is the one a later report would make from the file."""
    from jms_kapps.evaluation import report_summary

    runs = tmp_path / "runs"
    main([str(spec_file(tmp_path)), "--runs", str(runs), "--configurations", "1", "--repetitions", "1"])

    [folder] = [p.parent for p in runs.glob("*/record.json")]
    kept = json.loads((folder / "report-summary.json").read_text(encoding="utf-8"))
    (folder / "report-summary.json").unlink()
    again = report_summary.read_figures(folder)
    assert again is not None
    assert json.loads((folder / "report-summary.json").read_text(encoding="utf-8")) == kept


# The pick seed per repetition, the transit time and the retry wait (jms_kapps 93; interim names,
# jms_kapps 83).


def test_repetition_k_has_the_same_seed_in_every_configuration_and_one_key_for_all(
    tmp_path, configured
):
    runs = tmp_path / "runs"

    main([str(spec_file(tmp_path)), "--runs", str(runs)])

    made = records(runs)
    assert len(made) == 8
    for record in made:
        repetition = record["sweep"]["repetition"]
        seed = pick_seed(DEFAULT_BASE_SEED, repetition)
        assert record["run_settings"]["wms_pick_seed"] == seed
        assert tomllib.loads(record["configuration"])["wms_pick_seed"] == seed
    for number in (1, 2, 3, 4):
        mine = [r for r in made if r["sweep"]["configuration"] == number]
        assert len({r["sweep"]["key"] for r in mine}) == 1
        assert len({r["run_settings"]["wms_pick_seed"] for r in mine}) == 2
    by_repetition = {
        repetition: {
            r["run_settings"]["wms_pick_seed"]
            for r in made
            if r["sweep"]["repetition"] == repetition
        }
        for repetition in (1, 2)
    }
    assert all(len(seeds) == 1 for seeds in by_repetition.values())
    assert by_repetition[1] != by_repetition[2]


def test_the_spec_s_base_seed_sets_the_seeds_of_the_repetitions(tmp_path, configured):
    runs = tmp_path / "runs"
    spec = spec_file(tmp_path, SPEC.replace("repetitions = 2", "repetitions = 2\npick_base_seed = 31"))

    main([str(spec), "--runs", str(runs), "--configurations", "1"])

    assert sorted(r["run_settings"]["wms_pick_seed"] for r in records(runs)) == sorted(
        [pick_seed(31, 1), pick_seed(31, 2)]
    )


def test_a_saved_configuration_of_a_sweep_run_makes_the_runs_seed_again(
    tmp_path, configured
):
    """The single-run command takes the seed from the configuration it is given: the saved
    configuration of repetition 2 gives repetition 2's seed, not repetition 1's default."""
    runs = tmp_path / "runs"
    main([str(spec_file(tmp_path)), "--runs", str(runs), "--configurations", "1", "--repetitions", "2"])
    [record] = records(runs)
    saved = read_configuration(runs / record["run_id"] / "configuration.toml")

    assert saved.wms_pick_seed == pick_seed(DEFAULT_BASE_SEED, 2)
    assert saved.wms_pick_seed != pick_seed(DEFAULT_BASE_SEED, 1)


def test_the_transit_time_and_the_retry_wait_of_a_spec_reach_the_runs(
    tmp_path, configured
):
    runs = tmp_path / "runs"
    text = SPEC.replace(
        'topology = ["cross", "pair"]\nshacl = [true, false]',
        'topology = "cross"\ntransit_seconds = [1.0, 3.0]\nretry_seconds = 0.5',
    ).replace("repetitions = 2", "repetitions = 1")

    main([str(spec_file(tmp_path, text)), "--runs", str(runs)])

    assert [
        (c.transit_seconds, c.retry_seconds) for c, _ in configured.made
    ] == [(1.0, 0.5), (3.0, 0.5)]
    for record in records(runs):
        saved = tomllib.loads(record["configuration"])
        assert saved["retry_seconds"] == 0.5 and saved["transit_seconds"] in (1.0, 3.0)


def test_a_run_of_the_before_series_is_not_found_under_the_new_keys(
    tmp_path, configured
):
    """The new keys change every configuration's key: a sweep finds no earlier run and makes all."""
    runs = tmp_path / "runs"
    spec = spec_file(tmp_path)
    old = runs / "20261012-000000-aaaa"
    old.mkdir(parents=True)
    (old / "record.json").write_text(
        json.dumps(
            {
                "outcome": "completed",
                "sweep": {"key": "0123456789abcdef", "repetition": 1},
            }
        ),
        encoding="utf-8",
    )

    code = main([str(spec), "--runs", str(runs)])

    assert code == 0
    assert len(configured.made) == 8
