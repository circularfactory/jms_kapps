"""The records of the "before" series (jms_kapps 105): their keys were made before the transit time
and the retry wait joined a configuration's key (jms_kapps 93), so the report and the configurations
table match them by a legacy key. The seam is the commands (`jms-eval-report`, `jms-eval-table`) on a
runs folder made from fabricated records; the legacy key is built here by the rule, with a digest of
its own. No store, no network, no run.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from _evaluation_records import record, request, span, store_span, write, write_spans

from jms_kapps.evaluation.report import main as report_main
from jms_kapps.evaluation.spec import SpecConfiguration, configurations, read_spec
from jms_kapps.evaluation.table import main as table_main

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 2

[[sweep]]
topology = "cross"
prefill = ["{topology}-1k.ttl", "{topology}-1M.ttl"]
"""

# The two values that joined the key.
LATER = ("transit_seconds", "retry_seconds")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a reader opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def spec_path(tmp_path) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    return path


@pytest.fixture
def by_number(spec_path) -> dict[int, SpecConfiguration]:
    return {c.number: c for c in configurations(read_spec(spec_path))}


def old_key(configuration: SpecConfiguration) -> str:
    """The key a record of the "before" series holds: the digest of the configuration's values
    without the transit time and the retry wait, each number as a float, the first 16 digits of
    the SHA-256 of the sorted JSON."""
    canonical = {
        key: float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else value
        for key, value in configuration.values.items()
        if key not in LATER
    }
    text = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def before(
    found: dict[str, Any],
    configuration: SpecConfiguration,
    *,
    transit: float = 3.0,
    retry: float = 1.0,
    spec: str | None = None,
) -> dict[str, Any]:
    """The record `found` as the "before" series wrote it: the old key, and the transit time and
    the retry wait under `run_settings`."""
    found["sweep"] = {
        **found["sweep"],
        "key": old_key(configuration),
        # The sweep of the "before" series names the spec by the path it was given.
        "spec": f"specs/{'spec.toml' if spec is None else spec}",
    }
    found["run_settings"] = {"transit_seconds": transit, "retry_seconds": retry}
    return found


def numbers(n: int) -> dict[str, Any]:
    return {
        "commit_latencies": [1.0 * n, 2.0 * n, 3.0 * n],
        "requests": [request(0.1 * n, kind="read"), request(0.2 * n, kind="write")],
        "transports_ended_per_minute": 10.0 + n,
        "completed_writes_per_minute": 20.0 * n,
    }


def series(by_number, *, old: bool, prefix: str = "r") -> list[dict[str, Any]]:
    """Two repetitions of each configuration, with or without the old key."""
    made = []
    for number, configuration in by_number.items():
        for repetition in (1, 2):
            found = record(
                f"{prefix}{number}-{repetition}",
                configuration,
                repetition=repetition,
                **numbers(number + repetition),
            )
            made.append(before(found, configuration) if old else found)
    return made


def reported(spec_path: Path, runs: Path) -> dict[str, pd.DataFrame]:
    assert report_main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0
    folder = runs / "report"
    return {
        name: _csv(folder / f"{name}.csv")
        for name in ("runs", "steps", "configurations")
    }


def _csv(path: Path) -> pd.DataFrame:
    """The table in the file; an empty frame where the file is empty (no run, no header)."""
    try:
        return pd.read_csv(path, dtype={"configurations": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def with_spans(runs: Path, found: list[dict[str, Any]]) -> None:
    for each in found:
        write_spans(
            runs,
            each["run_id"],
            store_span("Module1", "ASK { }", 1, 0.1, response_bytes=10),
            span("Module1", "kapps_ogm.OGM.fetch", 4, 0.2),
        )


def equal(left: dict[str, pd.DataFrame], right: dict[str, pd.DataFrame]) -> None:
    assert left.keys() == right.keys()
    for name, frame in left.items():
        pd.testing.assert_frame_equal(frame, right[name], check_exact=True)


def test_records_with_the_old_key_are_matched_to_the_configuration_with_the_same_values(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    found = series(by_number, old=True)
    write(runs, *found)
    with_spans(runs, found)

    tables = reported(spec_path, runs)

    runs_csv = tables["runs"]
    assert len(runs_csv) == 4
    assert sorted(runs_csv["configuration"].unique()) == [1, 2]
    assert len(tables["steps"]) > 0
    assert sorted(tables["steps"]["configuration"].unique()) == [1, 2]
    assert sorted(tables["configurations"]["configurations"].unique()) == ["1", "2"]
    assert "records are of no configuration" not in (
        runs / "report" / "report.md"
    ).read_text(encoding="utf-8")


def test_the_tables_of_old_records_equal_those_of_the_same_runs_keyed_as_today(
    tmp_path, spec_path, by_number
):
    old_runs, new_runs = tmp_path / "old", tmp_path / "new"
    old, new = series(by_number, old=True), series(by_number, old=False)
    for runs, found in ((old_runs, old), (new_runs, new)):
        write(runs, *found)
        with_spans(runs, found)

    equal(reported(spec_path, old_runs), reported(spec_path, new_runs))


def test_records_keyed_as_today_are_matched_as_before(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    found = series(by_number, old=False)
    write(runs, *found)
    with_spans(runs, found)

    tables = reported(spec_path, runs)

    assert len(tables["runs"]) == 4
    assert len(tables["steps"]) > 0
    # The key of today's records is not the old key: they are matched by their own.
    assert found[0]["sweep"]["key"] != old_key(by_number[1])


def test_a_folder_of_both_series_gives_the_tables_of_both_in_one(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    write(
        runs,
        *series(by_number, old=True, prefix="o"),
        *series(by_number, old=False, prefix="n"),
    )

    tables = reported(spec_path, runs)

    assert len(tables["runs"]) == 8
    assert set(tables["configurations"]["runs"]) == {4}


@pytest.mark.parametrize(
    "settings",
    [{"transit": 4.0}, {"retry": 2.0}, {"spec": "after-series.toml"}],
    ids=["transit", "retry", "spec"],
)
def test_an_old_record_with_another_transit_retry_or_spec_is_left_out(
    tmp_path, spec_path, by_number, settings
):
    runs = tmp_path / "runs"
    other = before(
        record("odd", by_number[1], repetition=1, **numbers(1)),
        by_number[1],
        **settings,
    )
    write(runs, *series(by_number, old=True), other)

    tables = reported(spec_path, runs)

    assert "odd" not in set(tables["runs"]["run"])
    assert len(tables["runs"]) == 4
    assert "1 records are of no configuration of the spec" in (
        runs / "report" / "report.md"
    ).read_text(encoding="utf-8")


def test_an_old_record_that_says_no_run_settings_is_left_out(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    bare = before(record("bare", by_number[1], **numbers(1)), by_number[1])
    del bare["run_settings"]
    write(runs, bare)

    tables = reported(spec_path, runs)

    assert tables["runs"].empty


def test_a_record_of_no_configuration_either_way_is_left_out(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    stray = record("stray", by_number[1], **numbers(1))
    stray["sweep"]["key"] = "0123456789abcdef"
    write(runs, *series(by_number, old=True), stray)

    tables = reported(spec_path, runs)

    assert len(tables["runs"]) == 4
    assert "1 records are of no configuration of the spec" in (
        runs / "report" / "report.md"
    ).read_text(encoding="utf-8")


def test_the_configurations_table_command_matches_old_records(
    tmp_path, spec_path, by_number, capsys
):
    runs = tmp_path / "runs"
    write(runs, *series(by_number, old=True))

    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    assert [row["configurations"] for row in table["rows"]] == [[1], [2]]
    for row in table["rows"]:
        assert len(row["run_kinds"]["uc2"]["runs"]) == 2
    assert table["left_out"] == []


def test_the_configurations_table_leaves_out_an_old_record_of_another_spec(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    other = before(
        record("odd", by_number[1], **numbers(1)), by_number[1], spec="other.toml"
    )
    write(runs, other)

    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    assert all(row["run_kinds"]["uc2"] is None for row in table["rows"])


def test_a_baseline_of_an_old_record_follows_the_run_it_replays(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run = before(record("r1", by_number[1], **numbers(1)), by_number[1])
    baseline = record(
        "b1", by_number[1], run_kind="baseline", replays="r1", **numbers(1)
    )
    baseline["sweep"]["key"] = old_key(by_number[1])
    write(runs, run, baseline)

    tables = reported(spec_path, runs)

    assert sorted(tables["runs"]["run_kind"]) == ["baseline", "uc2"]
    assert set(tables["runs"]["configuration"]) == {1}


def test_the_readers_write_no_record_file(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    write(runs, *series(by_number, old=True))
    before_read = {
        path: path.read_bytes() for path in sorted(runs.glob("*/record.json"))
    }

    reported(spec_path, runs)
    table_main([str(spec_path), "--runs", str(runs), "--resamples", "50"])

    assert before_read == {
        path: path.read_bytes() for path in sorted(runs.glob("*/record.json"))
    }


def test_two_configurations_that_differ_only_in_transit_time_are_told_apart_by_the_settings(
    tmp_path,
):
    path = tmp_path / "spec.toml"
    path.write_text(
        SPEC.replace("repetitions = 2", "repetitions = 1")
        + '\n[[sweep]]\ntopology = "cross"\nprefill = "{topology}-1k.ttl"\n'
        "transit_seconds = 5.0\n",
        encoding="utf-8",
    )
    found = configurations(read_spec(path))
    by_values = {(c.values["prefill"], c.values["transit_seconds"]): c for c in found}
    slow = by_values[("cross-1k.ttl", 5.0)]
    quick = by_values[("cross-1k.ttl", 3.0)]
    assert old_key(slow) == old_key(quick)
    runs = tmp_path / "runs"
    write(
        runs,
        before(record("q", quick, **numbers(1)), quick, transit=3.0),
        before(record("s", slow, **numbers(2)), slow, transit=5.0),
    )

    runs_csv = reported(path, runs)["runs"].set_index("run")["configuration"]

    assert runs_csv["q"] == quick.number
    assert runs_csv["s"] == slow.number


@pytest.mark.parametrize("settings", [{"transit": 4.0}, {"retry": 2.0}])
def test_the_configurations_table_leaves_out_an_old_record_of_another_transit_or_retry(
    tmp_path, spec_path, by_number, settings
):
    runs = tmp_path / "runs"
    write(
        runs,
        before(record("odd", by_number[1], **numbers(1)), by_number[1], **settings),
    )

    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    assert all(row["run_kinds"]["uc2"] is None for row in table["rows"])


def test_the_configurations_table_gives_a_baseline_of_an_old_record_its_configuration(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run = before(record("r1", by_number[1], **numbers(1)), by_number[1])
    baseline = record(
        "b1", by_number[1], run_kind="baseline", replays="r1", **numbers(1)
    )
    write(runs, run, baseline)

    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    [first] = [r for r in table["rows"] if r["configurations"] == [1]]
    assert len(first["run_kinds"]["baseline"]["runs"]) == 1
