"""The series prefix of the configuration numbers (jms_kapps 110, from Sören's decision of 2026-10-13
as the product owner of the tests, jms_kapps 85, note 1866296, the changed reading "the 60- and
120-module cells are configurations 7 to 14"): the numbers stay, but the report, the table, the
charts and the sweep's lines give each its series, "before 7" and "after 7", so that the 30-module
grid at 10M of the iteration-one spec and the 60-module grid at 1k of the "after" spec cannot be
confused. A spec names its series in its defaults, outside every configuration's key.

The seams: the spec (`read_spec`, `configurations`), the readers (`jms-eval-table`,
`jms-eval-report`) on fabricated records, and `jms-eval-sweep`'s `--list` and run lines with a
stand-in run. No store, no network, no run.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from _evaluation_records import record, request, write
from _repo import withheld_text
from test_evaluation_before_records import before
from test_evaluation_sweep_replay import SPEC as REPLAY_SPEC
from test_evaluation_sweep_replay import FabricatedReplays, FabricatedRuns
from test_evaluation_sweep_replay import call as replay_call

import jms_kapps
from jms_kapps.evaluation import report_charts, sweep
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.record_match import ConfigurationIndex
from jms_kapps.evaluation.report import main as report_main
from jms_kapps.evaluation.spec import SpecRefused, configurations, read_spec
from jms_kapps.evaluation.summary import summary_of
from jms_kapps.evaluation.table import main as table_main

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"
SPECS = EVALUATION / "specs"
ITERATION_ONE = SPECS / "iteration-one.toml"
AFTER_SERIES = SPECS / "after-series.toml"

# The keys of the configurations of the four shipped specs before the series names
# (g1/wave2 at 16073046): naming a series changes no key.
KEYS = {
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
    "validation.toml": [
        "b33da0d5cdce3d73", "3b2af7d46f65aa52", "921ed660fb7e31de", "3731cd155ecd487c",
        "d862a5201dad997a", "82501390091ba02c",
    ],
    "calibration-cross-short.toml": ["ae36c509ef4499a7", "31d637f996753e1c"],
}  # fmt: skip

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = false
repetitions = 1

[[sweep]]
topology = ["cross", "pair"]
"""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a reader opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


def spec_file(folder: Path, text: str = SPEC, name: str = "spec.toml") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# --- a spec names its series, outside every configuration's key -----------------------------


def test_a_spec_names_its_series_in_its_defaults_and_no_key_changes(tmp_path):
    named = spec_file(
        tmp_path,
        SPEC.replace("[defaults]\n", '[defaults]\nseries = "after"\n'),
        "a.toml",
    )
    unnamed = spec_file(tmp_path, SPEC, "b.toml")

    found = configurations(read_spec(named))

    assert read_spec(named).series == "after"
    assert [c.series for c in found] == ["after", "after"]
    assert [c.name for c in found] == ["after 1", "after 2"]
    assert "series" not in found[0].values
    assert [c.key for c in found] == [c.key for c in configurations(read_spec(unnamed))]


def test_a_spec_without_a_series_name_uses_its_file_name(tmp_path):
    found = configurations(read_spec(spec_file(tmp_path, name="night-7.toml")))

    assert [c.name for c in found] == ["night-7 1", "night-7 2"]


@pytest.mark.parametrize("value", ["", "  ", 7, ["after"]])
def test_a_series_name_that_is_no_text_is_refused(tmp_path, value):
    text = SPEC.replace("[defaults]\n", f"[defaults]\nseries = {json.dumps(value)}\n")

    with pytest.raises(SpecRefused, match="series"):
        read_spec(spec_file(tmp_path, text))


@pytest.mark.parametrize(
    ("name", "series"),
    [
        ("iteration-one.toml", "before"),
        ("after-series.toml", "after"),
        ("validation.toml", "validation"),
        ("calibration-cross-short.toml", "calibration"),
    ],
)
def test_the_shipped_specs_name_their_series_and_keep_their_keys(name, series):
    spec = read_spec(SPECS / name)

    assert spec.series == series
    assert [c.key for c in configurations(spec)] == KEYS[name]


def test_the_records_of_the_before_series_still_match(tmp_path):
    """A record of the "before" series holds the legacy key (jms_kapps 105)."""
    found = configurations(read_spec(ITERATION_ONE))
    seventh = found[6]
    made = before(record("before-7", seventh), seventh, spec="iteration-one.toml")

    matched = ConfigurationIndex(found).of(summary_of(made))

    assert matched is not None and matched.number == 7
    assert matched.name == "before 7"


# --- the readers: one spec each, two calls, two series ----------------------------------------


def seventh_of(spec: Path) -> Any:
    return configurations(read_spec(spec))[6]


def runs_of(folder: Path, spec: Path) -> Path:
    """A runs folder with two valid runs of configuration 7 of `spec`."""
    runs = folder / spec.stem
    seventh = seventh_of(spec)
    write(
        runs,
        *(
            record(
                f"{spec.stem}-7-{repetition}",
                seventh,
                repetition=repetition,
                commit_latencies=[0.1] * 20,
                requests=[request(0.01)] * 10,
                transports_ended_per_minute=20.0,
            )
            for repetition in (1, 2)
        ),
    )
    return runs


def test_the_table_reads_series_and_number_for_each_spec(tmp_path):
    before_runs = runs_of(tmp_path, ITERATION_ONE)
    after_runs = runs_of(tmp_path, AFTER_SERIES)

    for spec, runs in ((ITERATION_ONE, before_runs), (AFTER_SERIES, after_runs)):
        assert table_main([str(spec), "--runs", str(runs), "--resamples", "50"]) == 0

    before_md = (before_runs / "table.md").read_text(encoding="utf-8")
    after_md = (after_runs / "table.md").read_text(encoding="utf-8")
    assert "| before 7 |" in before_md and "| after 7 |" not in before_md
    assert "| after 7 |" in after_md and "| before 7 |" not in after_md
    # The two are different configurations: the 30-module grid at 10M, the 60-module grid at 1k.
    assert "grid-10M.ttl" in before_md.split("| before 7 |")[1].split("\n")[0]
    assert "grid-60-1k.ttl" in after_md.split("| after 7 |")[1].split("\n")[0]
    # table.json keeps the number and gains the series.
    for runs, series in ((before_runs, "before"), (after_runs, "after")):
        table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
        assert table["series"] == series
        [row] = [r for r in table["rows"] if r["configurations"] == [7]]
        assert row["series"] == series


def test_the_report_reads_series_and_number_for_each_spec(tmp_path, monkeypatch):
    labels: list[str] = []
    drawn = report_charts.category_chart

    def kept(frame: pd.DataFrame, *args: Any, **kwargs: Any) -> None:
        labels.extend(frame["label"])
        drawn(frame, *args, **kwargs)

    monkeypatch.setattr(report_charts, "category_chart", kept)
    before_runs = runs_of(tmp_path, ITERATION_ONE)
    after_runs = runs_of(tmp_path, AFTER_SERIES)

    for spec, runs in ((ITERATION_ONE, before_runs), (AFTER_SERIES, after_runs)):
        assert report_main([str(spec), "--runs", str(runs), "--resamples", "50"]) == 0

    before_md = (before_runs / "report" / "report.md").read_text(encoding="utf-8")
    after_md = (after_runs / "report" / "report.md").read_text(encoding="utf-8")
    assert "| before 7 |" in before_md and "after 7" not in before_md
    assert "| after 7 |" in after_md and "before 7" not in after_md
    # The chart labels read "<series> <number>".
    assert "before 7" in labels and "after 7" in labels
    # The CSV files keep the number and gain the series.
    for runs, series in ((before_runs, "before"), (after_runs, "after")):
        frame = pd.read_csv(runs / "report" / "runs.csv")
        assert list(frame["configuration"]) == [7, 7]
        assert list(frame["series"]) == [series, series]
        configured = pd.read_csv(runs / "report" / "configurations.csv")
        assert set(configured["series"]) == {series}


def test_the_records_of_both_series_in_one_runs_folder_read_before_7_and_after_7(
    tmp_path,
):
    """One runs folder holds the records of both series, configuration 7 of the
    iteration-one spec as the "before" series wrote it (its legacy key) and configuration 7 of the
    "after" spec. The table and the report, called once for each spec on that one folder, show
    "before 7" and "after 7" for the two configurations, and so does the report's client load."""
    runs = tmp_path / "runs"
    before_7 = seventh_of(ITERATION_ONE)
    after_7 = seventh_of(AFTER_SERIES)

    def made(name: str, configuration: Any, repetition: int) -> dict[str, Any]:
        return record(
            f"{name}-7-{repetition}",
            configuration,
            repetition=repetition,
            commit_latencies=[0.1] * 20,
            requests=[request(0.01)] * 10,
            transports_ended_per_minute=20.0,
        )

    write(
        runs,
        *(
            before(made("before", before_7, r), before_7, spec="iteration-one.toml")
            for r in (1, 2)
        ),
        *(made("after", after_7, r) for r in (1, 2)),
    )

    for spec, series, other, prefill in (
        (ITERATION_ONE, "before", "after", "grid-10M.ttl"),
        (AFTER_SERIES, "after", "before", "grid-60-1k.ttl"),
    ):
        assert table_main([str(spec), "--runs", str(runs), "--resamples", "50"]) == 0
        table = (runs / "table.md").read_text(encoding="utf-8")
        assert f"| {series} 7 |" in table and f"{other} 7" not in table, spec.name
        assert prefill in table.split(f"| {series} 7 |")[1].split("\n")[0]

        assert report_main([str(spec), "--runs", str(runs), "--resamples", "50"]) == 0
        report = (runs / "report" / "report.md").read_text(encoding="utf-8")
        assert f"| {series} 7 |" in report and f"{other} 7" not in report, spec.name
        client = report.split("## Client load", 1)[1].split("\n## ", 1)[0]
        assert f"| {series} 7 |" in client, spec.name
        assert "| 7 |" not in client, spec.name


def test_a_chart_label_with_a_run_kind_reads_the_series_number_and_kind(
    tmp_path, monkeypatch
):
    labels: list[str] = []
    monkeypatch.setattr(
        report_charts,
        "category_chart",
        lambda frame, *args, **kwargs: labels.extend(frame["label"]),
    )
    monkeypatch.setattr(report_charts, "pair_chart", lambda *args, **kwargs: None)
    text = SPEC.replace("[defaults]\n", '[defaults]\nseries = "after"\n').replace(
        'topology = ["cross", "pair"]', 'topology = "cross"\nwrapper = [true, false]'
    )
    spec = spec_file(tmp_path, text)
    [wrapped, unwrapped] = configurations(read_spec(spec))
    runs = tmp_path / "runs"
    write(
        runs,
        record("w", wrapped, commit_latencies=[0.1], requests=[request(0.01)]),
        record("u", unwrapped, run_kind="uc2-unwrapped"),
    )

    assert report_main([str(spec), "--runs", str(runs), "--resamples", "50"]) == 0

    assert "after 1" in labels and "after 2 unwrapped" in labels


# --- the sweep's lines --------------------------------------------------------------------


class Made:
    """In place of a run on the configured store: each run writes a fabricated record."""

    def __call__(
        self,
        configuration: Configuration,
        *,
        runs: Path,
        sweep: dict[str, Any],
        **_: object,
    ) -> dict[str, Any]:
        found = record(
            f"run-{sweep['configuration']}-{sweep['repetition']}",
            commit_latencies=[0.1] * 20,
        )
        found["sweep"] = dict(sweep)
        write(runs, found)
        return found


def test_the_sweep_s_list_and_run_lines_read_series_and_number(
    tmp_path, monkeypatch, capsys
):
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    monkeypatch.setattr(sweep, "run_on_the_configured_store", Made())
    spec = spec_file(
        tmp_path, SPEC.replace("[defaults]\n", '[defaults]\nseries = "after"\n')
    )
    runs = tmp_path / "runs"

    assert sweep.main([str(spec), "--runs", str(runs), "--list"]) == 0
    listed = capsys.readouterr().out
    assert "configuration after 1 (block 1)" in listed
    assert "configuration after 2 (block 1)" in listed

    assert sweep.main([str(spec), "--runs", str(runs)]) == 0
    out = capsys.readouterr().out
    assert "run 1 of 2: configuration after 1, repetition 1 of 1" in out
    assert "run 2 of 2: configuration after 2, repetition 1 of 1" in out
    # The records keep the number, and --configurations takes it.
    sweeps = [json.loads(p.read_text())["sweep"] for p in runs.glob("*/record.json")]
    assert sorted(s["configuration"] for s in sweeps) == [1, 2]


def test_the_replay_s_run_lines_read_series_and_number(tmp_path, monkeypatch, capsys):
    """The replay's run lines give the configuration's name with its series, as
    the other run lines do: a configuration with no valid run to replay, and a replay broken at a
    factor."""
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")
    text = REPLAY_SPEC.replace("[defaults]\n", '[defaults]\nseries = "after"\n')

    def sweep_of(folder: Path, runs: FabricatedRuns, replays: FabricatedReplays) -> str:
        folder.mkdir()
        monkeypatch.setattr(sweep, "run_on_the_configured_store", runs)
        monkeypatch.setattr(sweep, "run_replay_on_the_configured_store", replays)
        replay_call(folder, spec_path=spec_file(folder, text))
        return capsys.readouterr().out

    out = sweep_of(
        tmp_path / "no-valid-run",
        FabricatedRuns(invalid={(1, 1), (1, 2)}),
        FabricatedReplays(),
    )
    assert "configuration after 1 has no valid run of use case 2 to replay" in out

    out = sweep_of(
        tmp_path / "broken", FabricatedRuns(), FabricatedReplays(broken_from=4)
    )
    assert "the replay of configuration after 1 is broken at the factor 4" in out


def test_the_list_of_the_iteration_one_spec_reads_before(capsys):
    assert sweep.main([str(ITERATION_ONE), "--list"]) == 0

    out = capsys.readouterr().out
    assert "configuration before 7 (block " in out
    assert "configuration 7 (block" not in out


# --- the documentation -----------------------------------------------------------------------


def test_the_readme_says_how_a_spec_names_its_series_and_that_approvals_are_issued_again():
    text = " ".join((EVALUATION / "README.md").read_text(encoding="utf-8").split())

    assert 'series = "before"' in text
    assert "jms_kapps 110" in text
    assert "issued before the merge is issued again" in text


def test_the_glossary_lists_every_new_public_name_of_the_series():
    """The interim-names paragraph of the series lists the new `series` argument
    of `report.draw_hours` too."""
    glossary = " ".join(
        withheld_text(EVALUATION.parents[2] / "CONTEXT.md").split()
    )
    [paragraph] = [
        part
        for part in glossary.split("Interim names (")
        if "of the series of a spec (jms_kapps 110" in part
    ]
    assert "the argument `series` of `.report.draw_hours`" in paragraph
