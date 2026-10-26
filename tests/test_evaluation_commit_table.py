"""The per-commit table and the replayed-writes table (`commit_table`): read from fabricated run
folders with spans (`_section_7_runs`), as the §7 figure script reads them. No store, no run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _section_7_runs import (
    kapps_record,
    kapps_spans,
    replay_record,
    replay_writes,
    write_run,
)

from jms_kapps.evaluation import commit_table
from jms_kapps.evaluation.commit_table import (
    KAPPS_SHACL_OFF,
    KAPPS_SHACL_ON,
    PHASES,
    WRITES_ONLY,
    phase_column,
    read_runs,
)


def _rows(runs: Path, *, with_replay: bool = True) -> dict[str, dict]:
    found = read_runs([runs])
    [run] = found.kapps
    replay = found.replays.get(run.run_id) if with_replay else None
    rows = commit_table.commit_rows(run, replay)
    assert rows is not None
    return {row["commit_id"]: row for row in rows}


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    folder = tmp_path / "runs"
    write_run(folder, kapps_record("20261025-100000-aaaa"), kapps_spans())
    write_run(
        folder,
        replay_record("20261025-101000-bbbb", "20261025-100000-aaaa", replay_writes()),
        None,
    )
    return folder


def test_one_row_for_each_commit_with_the_columns(runs: Path) -> None:
    rows = _rows(runs)
    assert sorted(rows) == ["c1", "c2", "c3", "c4", "c5"]
    for row in rows.values():
        assert tuple(row) == commit_table.COMMIT_COLUMNS
    c1 = rows["c1"]
    assert c1["arm"] == KAPPS_SHACL_ON
    assert c1["heartbeats"] == "on"
    assert c1["store"] == "graphdb.test"
    assert c1["t"] == pytest.approx(10.0)
    assert c1["seconds"] == pytest.approx(1.0)
    assert c1["role"] == "module"
    assert rows["c3"]["role"] == "WMS"


def test_admitted_and_in_the_window(runs: Path) -> None:
    rows = _rows(runs)
    assert [rows[c]["admitted"] for c in ("c1", "c3")] == [True, False]
    assert rows["c3"]["outcome"] != "accepted"
    assert [rows[c]["in_window"] for c in ("c1", "c4")] == [True, False]


def test_each_store_request_is_in_one_phase_and_the_phases_sum_to_the_commit(
    runs: Path,
) -> None:
    c1 = _rows(runs)["c1"]
    expected = {
        "class_query": 0.03,
        "class_derivation": 0.08,
        "fetch_reads": 0.09,
        "fetch_client": 0.21,
        "diff": 0.05,
        "update": 0.2,
        "receipt_ask": 0.03,
        "receipt_removal": 0.02,
        "other_requests": 0.01,
        "client_remainder": 0.28,
    }
    assert {phase: c1[phase_column(phase)] for phase in PHASES} == pytest.approx(
        expected
    )
    for row in _rows(runs).values():
        parts = [row[phase_column(phase)] for phase in PHASES]
        assert min(parts) >= 0
        assert abs(sum(parts) - row["seconds"]) < 1e-6
    assert c1["reads_in_commit"] == 6


def test_a_fetch_inside_a_fetch_counts_once_under_the_outer_one(runs: Path) -> None:
    c1 = _rows(runs)["c1"]
    # The outer fetch of 0.3 s, its reads and its inner fetch within it: 0.3 s in all.
    assert c1[phase_column("fetch_reads")] + c1[
        phase_column("fetch_client")
    ] == pytest.approx(0.3)


def test_the_kind_and_the_checked_write(runs: Path) -> None:
    rows = _rows(runs)
    assert rows["c1"]["kind"] == "status: running"
    assert rows["c1"]["checked_writes"] == 1
    assert rows["c1"]["update_seconds"] == pytest.approx(0.2)
    assert rows["c1"]["update_outcome"] == "accepted"
    assert rows["c1"]["update_applied"] is True
    # The old spelling of the heartbeat predicate.
    assert rows["c2"]["kind"] == "heartbeat"
    assert rows["c5"]["kind"] == "no write"
    assert rows["c5"]["update_at"] is None


def test_the_current_heartbeat_spelling_is_a_heartbeat_too() -> None:
    write = {"text": "INSERT DATA { <s> <http://x#hasLastHeartbeat> 1 }", "writes": []}
    assert commit_table.commit_kind(write) == "heartbeat"


def test_the_replayed_checked_write_pairs_by_the_commit_never_the_removal(
    runs: Path,
) -> None:
    rows = _rows(runs)
    c1 = rows["c1"]
    assert c1["replay_run_id"] == "20261025-101000-bbbb"
    assert c1["replayed_update_seconds"] == pytest.approx(0.15)
    assert c1["outcome_match"] is True
    assert c1["difference_seconds"] == pytest.approx(0.85)
    # c2's write was recorded after the window's end: no replayed update.
    assert rows["c2"]["replayed_update_seconds"] is None
    assert rows["c2"]["replay_run_id"] is None


def test_a_replay_without_the_commit_pairs_by_participant_and_send_time(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "runs"
    write_run(folder, kapps_record("20261025-100000-aaaa"), kapps_spans())
    write_run(
        folder,
        replay_record(
            "20261025-101000-bbbb",
            "20261025-100000-aaaa",
            replay_writes(with_commit=False),
        ),
        None,
    )
    rows = _rows(folder)
    assert rows["c1"]["replayed_update_seconds"] == pytest.approx(0.15)
    assert rows["c4"]["replayed_update_seconds"] == pytest.approx(0.05)


def test_a_record_without_the_link_gives_the_same_table_as_one_with_it(
    tmp_path: Path, runs: Path
) -> None:
    # The record's commit link is made from the same spans; the table reads the spans.
    linked = kapps_record("20261025-100000-aaaa")
    linked["measures"]["commits"] = [{"span_id": "c1", "kind": "status: running"}]
    folder = tmp_path / "linked"
    write_run(folder, linked, kapps_spans())
    assert _rows(folder, with_replay=False) == _rows(runs, with_replay=False)


def test_the_replayed_writes_table(runs: Path) -> None:
    found = read_runs([runs])
    [run] = found.kapps
    rows = commit_table.replayed_write_rows(found.replays[run.run_id], run)
    assert len(rows) == 3
    assert tuple(rows[0]) == commit_table.REPLAYED_WRITE_COLUMNS
    assert rows[0]["sent_offset_seconds"] == pytest.approx(10.461)
    assert rows[0]["overtook_previous"] is True
    assert rows[0]["t"] == pytest.approx(10.46)
    assert rows[2]["t"] == pytest.approx(-4.9)
    assert rows[2]["sent_offset_seconds"] is None


def test_a_run_without_spans_gives_no_rows(tmp_path: Path) -> None:
    folder = tmp_path / "runs"
    write_run(folder, kapps_record("20261025-100000-aaaa"), None)
    [run] = read_runs([folder]).kapps
    assert commit_table.commit_rows(run, None) is None


def test_the_store_and_the_level_from_the_experiments_folders(tmp_path: Path) -> None:
    runs = tmp_path / "local" / "heartbeats-off" / "runs"
    write_run(
        runs, kapps_record("20261025-100000-aaaa", heartbeat_interval="absent"), None
    )
    [run] = read_runs([tmp_path / "local"]).kapps
    assert (run.store, run.heartbeats) == ("local", "off")


@pytest.mark.parametrize(
    ("interval", "level"), [(None, "off"), (0, "off"), (30.0, "on"), ("absent", "on")]
)
def test_the_heartbeat_level_from_the_run_settings(
    tmp_path: Path, interval: float | None | str, level: str
) -> None:
    write_run(
        tmp_path,
        kapps_record("20261025-100000-aaaa", heartbeat_interval=interval),
        None,
    )
    [run] = read_runs([tmp_path]).kapps
    assert run.heartbeats == level


def test_a_single_run_folder_is_an_input(tmp_path: Path) -> None:
    folder = write_run(
        tmp_path, kapps_record("20261025-100000-aaaa", shacl=False), None
    )
    [run] = read_runs([folder]).kapps
    assert run.arm == KAPPS_SHACL_OFF


def test_which_replays_count(tmp_path: Path) -> None:
    on, off = "20261025-100000-aaaa", "20261025-100500-cccc"
    write_run(tmp_path, kapps_record(on), None)
    write_run(tmp_path, kapps_record(off, shacl=False), None)
    write_run(tmp_path, replay_record("20261025-101000-r001", on, []), None)
    write_run(tmp_path, replay_record("20261025-102000-r002", on, []), None)
    write_run(
        tmp_path, replay_record("20261025-103000-r003", on, [], valid=False), None
    )
    write_run(tmp_path, replay_record("20261025-104000-r004", off, []), None)
    write_run(tmp_path, replay_record("20261025-105000-r005", "elsewhere", []), None)
    write_run(
        tmp_path,
        replay_record("20261025-106000-r006", on, [], mode="all requests"),
        None,
    )
    found = read_runs([tmp_path])
    assert found.replays[on].run_id == "20261025-101000-r001"
    assert found.replays[on].arm == WRITES_ONLY
    why = {left["run_id"]: left["why"] for left in found.left_out}
    assert "earliest" in why["20261025-102000-r002"]
    assert "invalid" in why["20261025-103000-r003"]
    assert "no SHACL-on run" in why["20261025-104000-r004"]
    assert "no SHACL-on run" in why["20261025-105000-r005"]
    assert "all-requests" in why["20261025-106000-r006"]


def test_the_categories_the_phases_name_are_the_steps_categories() -> None:
    from jms_kapps.evaluation.report_steps import READ_CATEGORIES, WRITE_CATEGORIES

    reads = [name for name, _ in READ_CATEGORIES]
    writes = [name for name, _ in WRITE_CATEGORIES]
    assert commit_table.RECEIPT_ASK_CATEGORY in reads
    assert commit_table.SCHEMA_CATEGORY in reads
    assert commit_table.RECEIPT_REMOVAL_CATEGORY in writes


def test_the_earliest_replay_is_the_one_that_began_first(tmp_path: Path) -> None:
    on = "20261025-100000-aaaa"
    write_run(tmp_path, kapps_record(on), None)
    late = replay_record("20261025-101000-r001", on, [])
    late["actions"] = [{"action": "run began", "at": "2026-10-25T10:30:00+00:00"}]
    early = replay_record("20261025-102000-r002", on, [])
    early["actions"] = [{"action": "run began", "at": "2026-10-25T10:20:00+00:00"}]
    write_run(tmp_path, late, None)
    write_run(tmp_path, early, None)
    assert read_runs([tmp_path]).replays[on].run_id == "20261025-102000-r002"
