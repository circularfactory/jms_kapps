"""The steps of the report of a spec: `jms-eval-report` also tells where the time goes,
step by step, from the spans of the runs: `report/steps.csv` and a steps chart in `report.md`. The
seam is the command on a runs folder made from fabricated records with spans added
(`_evaluation_records`); the tests check `steps.csv`, not how a chart looks. No store, no network,
no run.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
import pytest
from _evaluation_records import (
    WMS_IRI,
    record,
    span,
    store_span,
    write,
    write_spans,
)
from _repo import withheld_text
from kapps_triplestore_interface import IRI

import jms_kapps
from jms_kapps.evaluation import observation, report_steps, report_summary
from jms_kapps.evaluation.report import main
from jms_kapps.evaluation.report_steps import (
    GAP_STEP,
    HELD_BOX_ASK,
    OTHER_READ,
    OTHER_WRITE,
    category_of,
)
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.store_requests import RECEIPT_PREDICATE
from jms_kapps.uc2.discovery import holds_query
from jms_kapps.uc2.vocabulary import uuid_box_iri

ROOT = Path(jms_kapps.__file__).parents[2]
README = Path(jms_kapps.__file__).parent / "evaluation" / "README.md"

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 3

# 1-2: a family of two prefill sizes.
[[sweep]]
topology = "cross"
prefill = ["{topology}-1k.ttl", "{topology}-1M.ttl"]
"""

WMS = "TheWarehouse7"
ALL_BOXES = "SELECT ?box WHERE { ?box a <http://x.org/cfc#Box> }"
HELD = "SELECT DISTINCT ?box WHERE { ?box <http://x.org/cfc#hasPossessedWorkpiece> ?possession . }"
RESERVE = "http://127.0.0.1:9000/workflows/Module1/reserve/execute"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the report opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def spec_path(tmp_path) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    return path


@pytest.fixture
def by_number(spec_path):
    return {c.number: c for c in configurations(read_spec(spec_path))}


def reported(spec_path: Path, runs: Path) -> pd.DataFrame:
    """Run the command; return the steps table it wrote."""
    assert main([str(spec_path), "--runs", str(runs), "--resamples", "50"]) == 0
    return pd.read_csv(runs / "report" / "steps.csv")


def row(steps: pd.DataFrame, configuration: int, role: str, step: str) -> pd.Series:
    found = steps[
        (steps["configuration"] == configuration)
        & (steps["role"] == role)
        & (steps["step"] == step)
    ]
    assert len(found) == 1, steps[["configuration", "role", "step"]].to_string()
    return found.iloc[0]


def run_with(runs, by_number, run_id, number, spans, **options: Any) -> None:
    write(runs, record(run_id, by_number[number], **options))
    write_spans(runs, run_id, *spans)


def test_steps_csv_has_a_row_for_each_configuration_role_and_step(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    for run_id, scale in (("a1", 1.0), ("a2", 3.0)):
        run_with(
            runs,
            by_number,
            run_id,
            1,
            [
                store_span("Module1", "ASK { }", 1, 0.1 * scale, response_bytes=10),
                store_span("Module1", "ASK { }", 2, 0.3 * scale, response_bytes=30),
                store_span(WMS, ALL_BOXES, 3, 1.0 * scale, response_bytes=500),
                span("Module1", "kapps_ogm.OGM.fetch", 4, 0.2 * scale),
            ],
        )

    steps = reported(spec_path, runs)

    # The CSV keeps the number and gains the spec's series.
    assert list(steps.columns) == [
        "configuration",
        "series",
        "role",
        "step",
        "runs",
        "steps",
        "steps_per_minute",
        "median_seconds",
        "p95_seconds",
        "median_response_bytes",
    ]
    assert sorted(steps["configuration"].unique()) == [1]
    reads = row(steps, 1, "module", "store read: other read")
    assert reads["runs"] == 2
    assert reads["steps"] == 4
    # Four spans in 2 windows of 5 minutes.
    assert reads["steps_per_minute"] == pytest.approx(0.4)
    # The pool 0.1, 0.3, 0.3, 0.9: the median and the p95 by linear interpolation (0.3 + 0.85 * 0.6).
    assert reads["median_seconds"] == pytest.approx(0.3)
    assert reads["p95_seconds"] == pytest.approx(0.81)
    assert reads["median_response_bytes"] == pytest.approx(20.0)
    boxes = row(steps, 1, "WMS", "store read: all boxes (WMS next_box)")
    assert boxes["median_seconds"] == pytest.approx(2.0)
    assert boxes["median_response_bytes"] == pytest.approx(500.0)
    other = row(steps, 1, "module", "kapps_ogm.OGM.fetch")
    assert pd.isna(other["median_response_bytes"])


def test_a_span_outside_the_window_and_a_run_that_does_not_count_are_left_out(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [
            span("Module1", "step", -5, 9.0),  # before the window
            span("Module1", "step", 10, 1.0),
            span("Module1", "step", 400, 9.0),  # after it
        ],
    )
    run_with(
        runs,
        by_number,
        "a2",
        1,
        [span("Module1", "step", 10, 7.0)],
        valid=False,
        failed_checks=[{"check": "deadlock alarm"}],
    )

    steps = reported(spec_path, runs)

    found = row(steps, 1, "module", "step")
    assert found["runs"] == 1
    assert found["steps"] == 1
    assert found["median_seconds"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("read", "SELECT ?never WHERE { ?a ?b ?c }", OTHER_READ),
        ("read", "ASK {}", OTHER_READ),
        (
            "write",
            f"INSERT DATA {{ <urn:r:1> <{RECEIPT_PREDICATE}> true }}",
            "checked write (commit body)",
        ),
        ("write", "INSERT DATA { <a> <b> <c> }", OTHER_WRITE),
        (
            "write",
            "DELETE WHERE { GRAPH <urn:x-kapps:write-receipts> { ?s ?p ?o } }",
            "receipt removal",
        ),
        ("write", "INSERT { <a> <hasLastHeartbeat> 1 } WHERE { }", "heartbeat update"),
        ("read", "SELECT ?a WHERE { ?a ?b ?c }", "possession: state lookups"),
        (
            "read",
            "SELECT ?x WHERE { ?x ?heartbeat ?y }",
            "discovery: live workflow (heartbeat)",
        ),
    ],
)
def test_a_query_is_put_in_a_category_by_its_text(kind, text, expected):
    assert category_of(kind, text, WMS_IRI) == expected


def test_a_query_of_no_category_is_other_read_or_other_write_never_an_error(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [
            store_span("Module1", "SELECT ?unheard WHERE { ?of ?it ?before }", 1, 0.5),
            store_span(
                "Module1", "COPY DEFAULT TO <urn:elsewhere>", 2, 0.25, kind="write"
            ),
            store_span("Module1", "ASK {}", 3, 0.75, kind="write", outcome="refused"),
        ],
    )

    steps = reported(spec_path, runs)

    assert row(steps, 1, "module", "store read: other read")["steps"] == 1
    assert row(steps, 1, "module", "store write: other write")["steps"] == 1
    assert (
        row(steps, 1, "module", "store write: other write (not accepted)")["steps"] == 1
    )


def test_a_possession_read_is_told_by_the_wms_iri_of_the_record(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    mine = HELD.replace("WHERE {", f"WHERE {{ VALUES ?holder {{ <{WMS_IRI}> }}")
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [
            store_span(WMS, mine, 1, 0.5),
            store_span(WMS, HELD, 2, 0.25),
            store_span("Module1", HELD, 3, 0.125),
        ],
    )

    steps = reported(spec_path, runs)

    assert (
        row(steps, 1, "WMS", "store read: possession: boxes held by the WMS")["steps"]
        == 1
    )
    assert (
        row(steps, 1, "WMS", "store read: possession: boxes held by a module")["steps"]
        == 1
    )
    assert (
        row(steps, 1, "module", "store read: possession: boxes held by a module")[
            "steps"
        ]
        == 1
    )


def test_the_wms_is_found_by_its_role_not_by_a_fixed_name(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [span(WMS, "step", 1, 1.0), span("TheWarehouse", "step", 2, 2.0)],
        participants=[
            {"name": "A", "resource": "http://o.org#TheWarehouse", "command": ["m"]},
            {
                "name": "B",
                "resource": f"http://o.org#{WMS}",
                "command": ["w", "--wms-strategy", "real"],
            },
        ],
    )

    steps = reported(spec_path, runs)

    assert row(steps, 1, "WMS", "step")["median_seconds"] == pytest.approx(1.0)
    assert row(steps, 1, "module", "step")["median_seconds"] == pytest.approx(2.0)


def test_a_commit_is_split_by_its_status(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [
            span("Module1", "kapps_ogm.OGM.commit", 1, 0.5),
            span("Module1", "kapps_ogm.OGM.commit", 2, 1.5, status="ERROR"),
        ],
    )

    steps = reported(spec_path, runs)

    assert (
        row(steps, 1, "module", "kapps_ogm.OGM.commit (accepted)")["median_seconds"]
        == 0.5
    )
    assert (
        row(steps, 1, "module", "kapps_ogm.OGM.commit (error)")["median_seconds"] == 1.5
    )


def test_the_gap_step_is_the_wms_client_time_after_each_all_boxes_read(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    reserve = {"http.url": RESERVE}
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [
            store_span(WMS, ALL_BOXES, 10, 1.0),  # ends at 11
            span(WMS, "POST", 12.5, 0.05, kind="CLIENT", **reserve),  # gap 1.5
            span(
                "Module1", "POST", 11.2, 0.05, kind="CLIENT", **reserve
            ),  # a module's: not it
            store_span(WMS, ALL_BOXES, 20, 2.0),  # ends at 22
            span(WMS, "POST", 22.25, 0.05, kind="CLIENT", **reserve),  # gap 0.25
            store_span(WMS, ALL_BOXES, 30, 1.0),  # no Reserve after it: no gap
        ],
    )
    run_with(
        runs,
        by_number,
        "a2",
        1,
        [
            store_span(WMS, ALL_BOXES, 10, 1.0),
            span(WMS, "POST", 13.0, 0.05, kind="CLIENT", **reserve),  # gap 2.0
        ],
    )

    steps = reported(spec_path, runs)

    gap = row(steps, 1, "WMS", GAP_STEP)
    assert gap["runs"] == 2
    assert gap["steps"] == 3
    assert gap["median_seconds"] == pytest.approx(1.5)
    assert gap["p95_seconds"] == pytest.approx(1.95)
    # The reserve calls are steps too, named after their endpoint.
    assert row(steps, 1, "WMS", "HTTP call: reserve")["steps"] == 3


def test_the_wms_s_ask_for_a_returned_box_is_a_step_of_its_own_and_an_old_record_still_reads(
    tmp_path, spec_path, by_number
):
    """When a box comes back, the WMS asks the store about that one box, in one
    `ASK`, which is a step of its own. A run of the "before" series, whose WMS still read every box
    at each spawn, keeps its all-boxes read and its gap step beside it, and the table sorts all of
    them."""
    runs = tmp_path / "runs"
    box = uuid_box_iri(UUID("5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10"))
    asked = holds_query(IRI(WMS_IRI), box)
    run_with(
        runs,
        by_number,
        "after",
        1,
        [
            store_span(WMS, asked, 1, 0.004, response_bytes=40),
            store_span(WMS, asked, 2, 0.006, response_bytes=40),
        ],
    )
    run_with(
        runs,
        by_number,
        "before",
        1,
        [
            store_span(WMS, ALL_BOXES, 10, 1.0),
            span(WMS, "POST", 12.5, 0.05, kind="CLIENT", **{"http.url": RESERVE}),
        ],
    )

    steps = reported(spec_path, runs)

    ask = row(steps, 1, "WMS", f"store read: {HELD_BOX_ASK}")
    assert (ask["runs"], ask["steps"]) == (1, 2)
    assert ask["median_seconds"] == pytest.approx(0.005)
    assert ask["median_response_bytes"] == pytest.approx(40.0)
    assert row(steps, 1, "WMS", "store read: all boxes (WMS next_box)")["runs"] == 1
    assert row(steps, 1, "WMS", GAP_STEP)["median_seconds"] == pytest.approx(1.5)
    keys = list(zip(steps["configuration"], steps["role"], steps["step"], strict=True))
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    "text",
    [
        "ASK WHERE { {<http://x.org/i#Box1> ?p ?o} }",
        "ASK { GRAPH <urn:x-kapps:write-receipts> { <urn:r:1> ?p ?o } }",
        "ASK { ?box <http://x.org/cfc#hasPossessedWorkpiece> ?state }",
    ],
    ids=["the object-graph mapper's", "a receipt's", "no possession of one box"],
)
def test_no_other_ask_is_taken_for_the_wms_s_ask_for_a_returned_box(text):
    assert category_of("read", text, WMS_IRI) != HELD_BOX_ASK


def test_a_run_without_spans_has_no_steps(tmp_path, spec_path, by_number):
    runs = tmp_path / "runs"
    write(runs, record("a1", by_number[1], run_kind="uc2-unwrapped"))

    steps = reported(spec_path, runs)

    assert steps.empty
    assert "median_seconds" in steps.columns


def test_the_steps_chart_is_in_the_report_for_a_family_of_prefill_sizes(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(runs, by_number, "a1", 1, [span("Module1", "step", 1, 0.5)])
    run_with(runs, by_number, "b1", 2, [span("Module1", "step", 1, 5.0)])

    reported(spec_path, runs)

    chart = runs / "report" / "steps-family-1.png"
    assert chart.stat().st_size > 0
    assert "![" in (runs / "report" / "report.md").read_text(encoding="utf-8")
    assert "(steps-family-1.png)" in (runs / "report" / "report.md").read_text(
        encoding="utf-8"
    )


def test_the_spans_are_read_once_and_the_summary_keeps_the_steps(
    tmp_path, spec_path, by_number, monkeypatch
):
    runs = tmp_path / "runs"
    run_with(runs, by_number, "a1", 1, [span("Module1", "step", 1, 0.5)])
    calls = []
    real = observation.read_spans
    monkeypatch.setattr(
        report_steps, "read_spans", lambda folder: calls.append(folder) or real(folder)
    )

    first = reported(spec_path, runs)
    second = reported(spec_path, runs)

    assert len(calls) == 1
    pd.testing.assert_frame_equal(first, second)
    summary = json.loads((runs / "a1" / report_summary.SUMMARY_FILE).read_text("utf-8"))
    assert summary["format"] == report_summary.FORMAT
    assert summary["steps"]["minutes"] == 5.0
    assert {
        "role": "module",
        "step": "step",
        "seconds": [0.5],
        "response_bytes": [],
    } in summary["steps"]["steps"]


def test_a_summary_of_format_1_is_made_again_from_the_record_and_the_spans(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(runs, by_number, "a1", 1, [span("Module1", "step", 1, 0.5)])
    reported(spec_path, runs)
    path = runs / "a1" / report_summary.SUMMARY_FILE
    old = json.loads(path.read_text("utf-8"))
    old["format"] = 1
    del old["steps"]
    path.write_text(json.dumps(old), encoding="utf-8")

    steps = reported(spec_path, runs)

    assert row(steps, 1, "module", "step")["steps"] == 1
    assert json.loads(path.read_text("utf-8"))["format"] == report_summary.FORMAT


def test_a_run_that_does_not_count_keeps_no_steps_in_its_summary(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(
        runs,
        by_number,
        "a1",
        1,
        [span("Module1", "step", 1, 0.5)],
        valid=False,
        failed_checks=[{"check": "deadlock alarm"}],
    )

    reported(spec_path, runs)

    summary = json.loads((runs / "a1" / report_summary.SUMMARY_FILE).read_text("utf-8"))
    assert summary["steps"] is None


def test_a_spans_file_that_cannot_be_read_gives_the_run_no_steps(
    tmp_path, spec_path, by_number
):
    runs = tmp_path / "runs"
    run_with(runs, by_number, "a1", 1, [span("Module1", "step", 1, 0.5)])
    (runs / "a1" / "spans" / "Broken.jsonl").write_bytes(b"\xff\xfe\x00")

    steps = reported(spec_path, runs)

    assert steps.empty


def test_the_readme_and_the_context_name_the_steps_and_mark_them_interim():
    text = README.read_text(encoding="utf-8")
    for name in ("steps.csv", "steps-family-", "report_steps"):
        assert name in text
    context = withheld_text(ROOT / "CONTEXT.md")
    assert "jms_kapps 87" in context
    assert "steps.csv" in context
