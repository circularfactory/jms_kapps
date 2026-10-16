""" "Timed out" and "connection dropped" are two outcomes of a store request (jms_kapps 109, from
Sören's decision of 2026-10-13 as the product owner of the tests, jms_kapps 85, note 1866296,
decision 2): a request whose client gave up because the time ran out is timed out, one whose
connection closed, reset or failed before an answer is connection dropped. Both count toward the
1 % of the broken rule, whose signal is now "refused or unanswered".

The seams: a UC2 run of part 1 and part 2 against their stand-ins, whose wrapper wrote the spans;
the store-only baseline against its stand-in store; and the readers of records (`summary`,
`jms-eval-table`, `jms-eval-report`) on fabricated records. No store, no network, no run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
from _evaluation_fakes import store_span
from _evaluation_records import record, request, write
from _repo import withheld_text
from test_evaluation_baseline import Baseline, accepted, no_answer, read, recorded_run
from test_evaluation_bench import Bench
from test_evaluation_observation import ENDED_AT, ENDS, WARM_UP, module_spans, wms_spans

import jms_kapps
from jms_kapps.evaluation import report_summary, store_requests
from jms_kapps.evaluation.ramp import REFUSED_OR_UNANSWERED, judge, steps_of
from jms_kapps.evaluation.report import main as report_main
from jms_kapps.evaluation.spec import configurations, read_spec
from jms_kapps.evaluation.summary import summary_of
from jms_kapps.evaluation.table import main as table_main

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"
ROOT = EVALUATION.parents[2]

# The reason of iteration one's four unanswered requests (jms_kapps 91), as their records keep it.
ITERATION_ONE_REASON = (
    "ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed "
    "connection without response'))"
)
READ_TIMEOUT = (
    "ReadTimeout: HTTPConnectionPool(host='graphdb', port=7200): Read timed out. "
    "(read timeout=60)"
)
REFUSED_CONNECTION = (
    "ConnectionError: HTTPConnectionPool(host='graphdb', port=7200): Max retries exceeded "
    "with url: /repositories/eval-x (Caused by NewConnectionError('<urllib3.connection."
    "HTTPConnection object>: Failed to establish a new connection: [Errno 111] Connection "
    "refused'))"
)

SPEC = """\
[defaults]
boxes_per_minute = 30
warm_up_seconds = 0
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
heavy = false
repetitions = 3

[[sweep]]
topology = "cross"
prefill = "none"
"""


@pytest.fixture
def spec_path(tmp_path) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(SPEC, encoding="utf-8")
    return path


@pytest.fixture
def one(spec_path):
    return configurations(read_spec(spec_path))[0]


# --- the two outcomes, from the error -------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (READ_TIMEOUT, "timed out"),
        ("ConnectTimeout: timed out", "timed out"),
        ("ReadTimeout: read timed out", "timed out"),
        ("WriteTimeout: write timed out", "timed out"),
        ("PoolTimeout: no connection", "timed out"),
        (ITERATION_ONE_REASON, "connection dropped"),
        (REFUSED_CONNECTION, "connection dropped"),
        (
            "RemoteProtocolError: Server disconnected without sending a response.",
            "connection dropped",
        ),
        ("ReadError: [Errno 104] Connection reset by peer", "connection dropped"),
        ("ConnectError: [Errno 111] Connection refused", "connection dropped"),
        ("ChunkedEncodingError: Response ended prematurely", "connection dropped"),
        ("SomethingElse: the client broke", "connection dropped"),
        (None, "connection dropped"),
    ],
)
def test_an_unanswered_request_is_timed_out_or_connection_dropped_by_its_error(
    error, outcome
):
    assert store_requests.unanswered_outcome(error) == outcome


def test_an_error_of_no_known_class_is_told_apart():
    """It counts as connection dropped, and the report lists it with its error."""
    assert store_requests.known_error(READ_TIMEOUT)
    assert store_requests.known_error(ITERATION_ONE_REASON)
    assert not store_requests.known_error("SomethingElse: the client broke")
    assert not store_requests.known_error(None)


# --- a new record: a UC2 run's spans and the replay's requests --------------------------------


def uc2_run(tmp_path: Path, *errors: tuple[float, str | None]) -> dict[str, Any]:
    """A UC2 run against the stand-ins, whose Module1 wrote the spans of the observation's tests
    (one read timed out at 45 s) and a read with no answer at each time of `errors`, with its
    error, or with none. The window runs from 30 s to 90 s."""
    bench = Bench(tmp_path, ends=ENDS, ended_at=ENDED_AT, output_at=[])
    spans = module_spans(bench.clock)
    for at, error in errors:
        spans.append(
            store_span(
                bench.clock,
                "Module1",
                at,
                0.005,
                kind="read",
                text=f"ASK {{ ?unanswered{int(at)} ?p ?o }}",
                outcome=None,
                error=error,
            )
        )
    bench.spans = {
        "Module1": spans,
        "WarehouseManagementSystem1": wms_spans(bench.clock),
    }
    found = bench.run(WARM_UP)
    assert found["outcome"] == "completed", found["outcome"]
    return found


def test_a_uc2_run_gives_each_unanswered_request_one_of_the_two_outcomes(tmp_path):
    found = uc2_run(
        tmp_path,
        (46.0, READ_TIMEOUT),
        (47.0, ITERATION_ONE_REASON),
        (48.0, REFUSED_CONNECTION),
    )

    by_text = {r["text"]: r for r in found["measures"]["store_requests"]}
    assert by_text["ASK { ?late ?answer ?here }"]["outcome"] == "timed out"
    assert by_text["ASK { ?unanswered46 ?p ?o }"]["outcome"] == "timed out"
    dropped = by_text["ASK { ?unanswered47 ?p ?o }"]
    assert dropped["outcome"] == "connection dropped"
    assert "RemoteDisconnected" in dropped["reason"]
    # A connection that the host refused is no refusal of the store.
    refused_host = by_text["ASK { ?unanswered48 ?p ?o }"]
    assert refused_host["outcome"] == "connection dropped"
    assert refused_host["refusal"] is None
    measures = found["measures"]
    assert measures["store_requests_in_window"] == 12
    assert measures["refused_in_window"] == 2
    assert measures["timed_out_in_window"] == 2
    assert measures["connection_dropped_in_window"] == 2
    # Both count toward the 1 %, as the sum did before.
    assert measures["refused_or_unanswered_share"] == pytest.approx(6 / 12)


def raising(error: Exception, at: float):
    """No answer: the client finds `error` at the clock's `at`."""

    def answer(clock: Any, arrival: Any) -> httpx.Response:
        clock.answer_at(at)
        raise error

    return answer


def test_the_replay_gives_each_unanswered_request_one_of_the_two_outcomes(tmp_path):
    recorded = recorded_run(
        tmp_path,
        lambda clock: [
            read(clock, 31.0, "SELECT ?accepted"),
            read(clock, 32.0, "SELECT ?timed_out"),
            read(clock, 33.0, "SELECT ?disconnected"),
            read(clock, 34.0, "SELECT ?refused_by_the_host"),
        ],
    )
    baseline = Baseline(
        tmp_path,
        answers={
            "SELECT ?accepted": accepted(at=1.25),
            "SELECT ?timed_out": no_answer(at=10.0),
            "SELECT ?disconnected": raising(
                httpx.RemoteProtocolError(
                    "Server disconnected without sending a response."
                ),
                at=3.01,
            ),
            "SELECT ?refused_by_the_host": raising(
                httpx.ConnectError("[Errno 111] Connection refused"), at=4.01
            ),
        },
    )

    found = baseline.run(recorded, factor=1.0)

    requests = {r["text"]: r for r in found["measures"]["store_requests"]}
    assert requests["SELECT ?timed_out"]["outcome"] == "timed out"
    assert "ReadTimeout" in requests["SELECT ?timed_out"]["reason"]
    assert requests["SELECT ?disconnected"]["outcome"] == "connection dropped"
    assert "RemoteProtocolError" in requests["SELECT ?disconnected"]["reason"]
    assert requests["SELECT ?refused_by_the_host"]["outcome"] == "connection dropped"
    measures = found["measures"]
    assert (
        measures["refused_in_window"],
        measures["timed_out_in_window"],
        measures["connection_dropped_in_window"],
    ) == (0, 1, 2)
    level = found["replay_level"]
    assert (level["timed_out_in_window"], level["connection_dropped_in_window"]) == (
        1,
        2,
    )
    # 3 of 4 unanswered: broken by the renamed signal, the same sum as before.
    assert [s["signal"] for s in level["signals"]][0:1] == [REFUSED_OR_UNANSWERED]
    assert level["signals"][0]["share"] == pytest.approx(3 / 4)


# --- an earlier record: classified by its error, never changed ------------------------------


def iteration_one_record(configuration) -> dict[str, Any]:
    """A record as iteration one wrote it: four requests "timed out" with its reason, two in the
    window and two outside it, among 96 answered ones in the window."""

    def unanswered(window: bool, at: str) -> dict[str, Any]:
        return request(
            0.004,
            kind="read",
            outcome="timed out",
            window=window,
            at=at,
            reason=ITERATION_ONE_REASON,
        )

    return record(
        "before-1",
        configuration,
        commit_latencies=[0.1] * 20,
        requests=[
            *[request(0.01, kind="read")] * 96,
            unanswered(True, "2026-10-12T10:02:30+00:00"),
            unanswered(True, "2026-10-12T10:04:30+00:00"),
            unanswered(False, "2026-10-12T10:00:30+00:00"),
            unanswered(False, "2026-10-12T10:07:30+00:00"),
        ],
        timed_out_in_window=2,
    )


def test_an_earlier_record_s_timed_out_requests_are_classified_by_their_error(one):
    summary = summary_of(iteration_one_record(one))

    assert summary.timed_out_in_window == 0
    assert summary.connection_dropped_in_window == 2
    assert summary.refused_in_window == 0
    assert summary.refused_or_unanswered_in_window == 2


def test_the_table_counts_the_two_apart_in_the_window(tmp_path, spec_path, one, capsys):
    runs = tmp_path / "runs"
    write(runs, iteration_one_record(one))
    path = runs / "before-1" / "record.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    [cell] = [row["run_kinds"]["uc2"] for row in table["rows"]]
    assert cell["refused_in_window"] == 0
    assert cell["timed_out_in_window"] == 0
    assert cell["connection_dropped_in_window"] == 2
    assert cell["refused_or_unanswered_in_window"] == 2
    assert cell["refused_or_unanswered_share"] == pytest.approx(2 / 98)
    text = (runs / "table.md").read_text(encoding="utf-8")
    section = text.split("## Store requests refused or unanswered", 1)[1].split(
        "\n## "
    )[0]
    assert "| Refused | Timed out | Connection dropped |" in section
    assert "| 98 | 0 | 0 | 2 | 2 | 2.04% |" in section
    # No record file is written or changed.
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_the_report_counts_the_two_apart_in_the_window_and_outside_it(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(runs, iteration_one_record(one))
    path = runs / "before-1" / "record.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    assert report_main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0

    row = pd.read_csv(runs / "report" / "runs.csv").iloc[0]
    assert row["timed_out_in_window"] == 0
    assert row["timed_out_outside_window"] == 0
    assert row["connection_dropped_in_window"] == 2
    assert row["connection_dropped_outside_window"] == 2
    unanswered = pd.read_csv(runs / "report" / "unanswered.csv")
    assert list(unanswered["outcome"]) == ["connection dropped"] * 4
    section = (
        (runs / "report" / "report.md")
        .read_text(encoding="utf-8")
        .split("## Unanswered store requests", 1)[1]
        .split("\n## ", 1)[0]
    )
    assert "connection dropped" in section
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_a_new_record_s_two_outcomes_are_counted_apart_by_the_report(
    tmp_path, spec_path, one
):
    runs = tmp_path / "runs"
    write(
        runs,
        record(
            "after-1",
            one,
            requests=[
                request(0.01, kind="read"),
                request(
                    9.0,
                    kind="read",
                    outcome="timed out",
                    reason=READ_TIMEOUT,
                    at="2026-10-12T10:03:00+00:00",
                ),
                request(
                    0.003,
                    kind="read",
                    outcome="connection dropped",
                    reason=ITERATION_ONE_REASON,
                    at="2026-10-12T10:02:00+00:00",
                ),
                request(
                    0.003,
                    kind="read",
                    outcome="connection dropped",
                    reason="SomethingElse: the client broke",
                    window=False,
                    at="2026-10-12T10:08:00+00:00",
                ),
            ],
        ),
    )

    assert report_main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0

    row = pd.read_csv(runs / "report" / "runs.csv").iloc[0]
    assert (row["timed_out_in_window"], row["connection_dropped_in_window"]) == (1, 1)
    assert row["connection_dropped_outside_window"] == 1
    unanswered = pd.read_csv(runs / "report" / "unanswered.csv")
    assert list(unanswered["outcome"]) == [
        "connection dropped",
        "timed out",
        "connection dropped",
    ]
    # An error of no known class is listed with its error.
    assert "SomethingElse: the client broke" in list(unanswered["reason"])


def test_a_summary_that_the_report_kept_before_is_made_again(tmp_path, spec_path, one):
    assert report_summary.FORMAT == 5
    runs = tmp_path / "runs"
    write(runs, iteration_one_record(one))
    report_main([str(spec_path), "--runs", str(runs), "--resamples", "200"])
    path = runs / "before-1" / report_summary.SUMMARY_FILE
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale["format"] = 4
    stale["requests"]["timed_out_in_window"] = 2
    path.write_text(json.dumps(stale), encoding="utf-8")

    report_main([str(spec_path), "--runs", str(runs), "--resamples", "200"])

    assert json.loads(path.read_text(encoding="utf-8"))["format"] == 5
    row = pd.read_csv(runs / "report" / "runs.csv").iloc[0]
    assert (row["timed_out_in_window"], row["connection_dropped_in_window"]) == (0, 2)


# --- a request with neither an answer nor an error --------------------------


def test_a_request_with_neither_an_answer_nor_an_error_is_connection_dropped_in_a_new_record(
    tmp_path,
):
    """The ticket's rule for an error that fits neither class: connection dropped."""
    found = uc2_run(tmp_path, (46.0, None))

    by_text = {r["text"]: r for r in found["measures"]["store_requests"]}
    unanswered = by_text["ASK { ?unanswered46 ?p ?o }"]
    assert unanswered["outcome"] == "connection dropped"
    assert unanswered["reason"] is None
    assert found["measures"]["connection_dropped_in_window"] == 1


def test_a_request_with_neither_an_answer_nor_an_error_is_connection_dropped_in_an_earlier_record(
    tmp_path, spec_path, one
):
    """An earlier record's "timed out" without an error gets the same outcome as a new record's
    request without an error: connection dropped, in the summary, the table and the report alike.
    No record file is changed."""
    no_error = request(
        0.004,
        kind="read",
        outcome="timed out",
        at="2026-10-12T10:02:30+00:00",
        reason=None,
    )
    assert store_requests.outcome_of(no_error) == "connection dropped"
    earlier = record(
        "before-1",
        one,
        commit_latencies=[0.1] * 20,
        requests=[*[request(0.01, kind="read")] * 99, no_error],
        timed_out_in_window=1,
    )
    summary = summary_of(earlier)
    assert (summary.timed_out_in_window, summary.connection_dropped_in_window) == (0, 1)
    assert summary.refused_or_unanswered_in_window == 1

    runs = tmp_path / "runs"
    write(runs, earlier)
    path = runs / "before-1" / "record.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0
    assert report_main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    [cell] = [row["run_kinds"]["uc2"] for row in table["rows"]]
    assert (cell["timed_out_in_window"], cell["connection_dropped_in_window"]) == (0, 1)
    row = pd.read_csv(runs / "report" / "runs.csv").iloc[0]
    assert (row["timed_out_in_window"], row["connection_dropped_in_window"]) == (0, 1)
    assert list(pd.read_csv(runs / "report" / "unanswered.csv")["outcome"]) == [
        "connection dropped"
    ]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


# --- the broken rule counts both, as the sum did before ---------------------------------------


def ramp_summaries(found, *levels):
    """The summaries of one run at each level of a ramp: `levels` holds the store requests of
    each and the counts of its measures, as its record holds them."""
    return [
        summary_of(
            record(
                f"level-{n}",
                level,
                commit_latencies=[0.1] * 20,
                requests=requests,
                completed_writes_per_minute=10.0 * (n + 1),
                **counts,
            )
        )
        for n, (level, (requests, counts)) in enumerate(zip(found, levels, strict=True))
    ]


def test_a_ramp_level_is_broken_by_refused_timed_out_and_dropped_requests_together(
    tmp_path,
):
    text = SPEC.replace("repetitions = 3", "repetitions = 1") + (
        '\n[[sweep]]\nramp = true\ntopology = "cross"\nboxes_per_minute = [10, 20]\n'
        "repetitions = 1\n"
    )
    path = tmp_path / "ramp.toml"
    path.write_text(text, encoding="utf-8")
    levels = [c for c in configurations(read_spec(path)) if c.ramp is not None]
    answered = [request(0.01, kind="read")] * 98
    healthy = ([*answered, *[request(0.01, kind="read")] * 2], {})
    summaries = ramp_summaries(
        levels,
        healthy,
        (
            [
                *answered,
                request(0.01, kind="read", outcome="refused", reason="HTTP 500"),
                request(0.01, kind="read", outcome="timed out", reason=READ_TIMEOUT),
            ],
            {"refused_in_window": 1, "timed_out_in_window": 1},
        ),
    )
    # The second level: 1 refused and 1 timed out of 100, 2 %: broken.
    judged = judge(steps_of(levels, summaries))
    assert judged.broken_at == 20
    assert REFUSED_OR_UNANSWERED in judged.why

    # An earlier record's two "timed out" by a dropped connection count just the same.
    summaries = ramp_summaries(
        levels,
        healthy,
        (
            [
                *answered,
                *[
                    request(
                        0.004,
                        kind="read",
                        outcome="timed out",
                        reason=ITERATION_ONE_REASON,
                    )
                ]
                * 2,
            ],
            {"timed_out_in_window": 2},
        ),
    )
    assert (
        summaries[1].timed_out_in_window,
        summaries[1].connection_dropped_in_window,
    ) == (
        0,
        2,
    )
    judged = judge(steps_of(levels, summaries))
    assert judged.broken_at == 20
    assert judged.levels[1]["numbers"]["refused_or_unanswered_share"] == pytest.approx(
        0.02
    )


def test_an_earlier_replay_level_shows_the_new_name_of_its_signal(
    tmp_path, spec_path, one
):
    """Its record keeps the old name; the table shows the new one."""
    runs = tmp_path / "runs"
    replayed = record("a1", one, requests=[request(0.01)] * 100)
    level = record(
        "b1",
        run_kind="baseline",
        replays="a1",
        requests=[request(0.01)] * 98
        + [request(0.004, outcome="timed out", reason=ITERATION_ONE_REASON)] * 2,
        timed_out_in_window=2,
    )
    level["sweep"] = {
        "spec": "spec.toml",
        "configuration": 1,
        "key": f"{one.key}@x2",
        "replayed_key": one.key,
        "replay_factor": 2.0,
        "repetition": 1,
        "repetitions": 1,
    }
    level["replay_level"] = {
        "factor": 2.0,
        "offered_per_minute": 40.0,
        "achieved_per_minute": 39.2,
        "requests_in_window": 100,
        "answered_in_window": 98,
        "refused_in_window": 0,
        "timed_out_in_window": 2,
        "heap_refusals_in_window": 0,
        "replay_span_seconds": 150.0,
        "broken": True,
        "signals": [{"signal": "refused or timed out", "share": 0.02}],
    }
    write(runs, replayed, level)

    assert table_main([str(spec_path), "--runs", str(runs), "--resamples", "200"]) == 0

    table = json.loads((runs / "table.json").read_text(encoding="utf-8"))
    [shown] = table["rows"][0]["replay_levels"]
    assert [s["signal"] for s in shown["signals"]] == [REFUSED_OR_UNANSWERED]
    assert (shown["timed_out_in_window"], shown["connection_dropped_in_window"]) == (
        0,
        2,
    )
    text = (runs / "table.md").read_text(encoding="utf-8")
    assert "refused or unanswered" in text
    assert "refused or timed out" not in text
    assert json.loads((runs / "b1" / "record.json").read_text())["replay_level"][
        "signals"
    ] == [{"signal": "refused or timed out", "share": 0.02}]


# --- the documentation -----------------------------------------------------------------------


def test_the_readme_and_the_glossary_say_the_two_outcomes_and_the_new_signal_name():
    readme = " ".join((EVALUATION / "README.md").read_text(encoding="utf-8").split())
    glossary = " ".join(withheld_text(ROOT / "CONTEXT.md").split())
    for text in (readme, glossary):
        assert "connection dropped" in text
        assert "refused or unanswered" in text
    assert "jms_kapps 109" in readme and "jms_kapps 109" in glossary
    assert "connection_dropped_in_window" in readme
    # A request with neither an answer nor an error.
    assert (
        "a request without an answer whose record holds no error is connection dropped too, in "
        "a new record and in an earlier one alike" in readme
    )
