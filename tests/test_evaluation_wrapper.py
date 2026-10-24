"""The wrapper of the measurement harness: a participant started through it runs as
it would without it, and its spans go into a file of the run's folder. FastAPI, requests and httpx
are instrumented with OpenTelemetry from outside, and the object-graph mapper's public calls are
wrapped, not edited: KSM, the mapper and the triple-store interface stay as they are.

The participant here is `_fake_participant.py`, which talks to a store as a participant does,
through the interface and the mapper; the store is `_fake_store.py` on this machine's loopback,
which words its refusals as GraphDB does. The environment's store variables are removed for the
participant, and `TRIPLESTORE_URL` names the stand-in, so nothing reaches a real store.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests
from _fake_participant import ALARM, OPERATION
from _fake_participant import ONTOLOGY as PARTICIPANT_DATA
from _fake_store import HEAP_REFUSAL, SHACL_REPORT, FakeStore
from _repo import find_repo_root
from test_evaluation_bench import CROSS, Bench

from jms_kapps.evaluation import wrapper
from jms_kapps.evaluation.bench import TransportEnd
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.observation import (
    Window,
    commit_records,
    measures,
    read_spans,
    store_request_records,
)

PARTICIPANT = Path(__file__).parent / "_fake_participant.py"
PASSWORD = "participant-secret-k3"


def participant_environment(store: FakeStore | None) -> dict[str, str]:
    """This environment without a store's variables, and with `TRIPLESTORE_URL` naming the
    stand-in, if there is one."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("TRIPLESTORE_", "GRAPHDB_", "INFLUXDB_"))
    }
    if store is not None:
        environment["TRIPLESTORE_URL"] = store.url
    return environment


def run_wrapped(
    folder: Path, store: FakeStore, *steps: str, participant: str = "Module1"
) -> Path:
    """Run the stand-in participant through the wrapper against `store`, making `steps`, and
    return the file of its spans."""
    spans = folder / "spans" / f"{participant}.jsonl"
    command = wrapper.wrapped(
        [sys.executable, str(PARTICIPANT), store.url, store.dataset, *steps], spans
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        env=participant_environment(store),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return spans


def writes_done(text: str) -> bool:
    """Whether the update `text` inserts the status `done`."""
    return '"done"' in text.split("WHERE")[0].split("INSERT")[-1]


def a_store(refuse_update: Callable[[str], bool] = writes_done) -> FakeStore:
    """The stand-in store with the participant's data, which refuses a commit of `done` as a
    SHACL shape does, a query with a DISTINCT of the statuses for want of heap, and answers the
    `late` query only after the participant gave up waiting."""
    return FakeStore(
        data=PARTICIPANT_DATA,
        refuse_update=refuse_update,
        refuse_for_heap=lambda text: "DISTINCT ?status" in text,
        answer_late=lambda text: "?late" in text,
        late_seconds=2.0,
    )


def test_a_commit_in_a_wrapped_participant_shows_its_class_query_class_spec_fetch_diff_and_update(
    tmp_path,
):
    with a_store() as store:
        spans_file = run_wrapped(tmp_path, store, "running")

    spans = read_spans(spans_file.parent)
    [commit] = [s for s in spans if s.name == "kapps_ogm.OGM.commit"]
    below = sorted(
        (s for s in spans if s.parent_id == commit.span_id), key=lambda s: s.start
    )

    def first(found: list) -> object:
        assert found, [(s.name, s.attributes.get("store.text", "")[:40]) for s in below]
        return found[0]

    class_query = first(
        [
            s
            for s in below
            if s.attributes.get("store.kind") == "read"
            and "SELECT ?class" in s.attributes.get("store.text", "")
        ]
    )
    derivation = first([s for s in below if s.name == "kapps_ogm.OGM.get_class_spec"])
    fetch = first([s for s in below if s.name == "kapps_ogm.OGM.fetch"])
    diff = first([s for s in below if s.name == "kapps_ogm.Node.diff"])
    update = first(
        [s for s in below if s.attributes.get("store.status_written") == ["running"]]
    )
    ordered = [class_query, derivation, fetch, diff, update]
    assert len({s.span_id for s in ordered}) == 5
    assert [s.start for s in ordered] == sorted(s.start for s in ordered)
    assert update.attributes["store.kind"] == "write"
    assert update.attributes["store.operation"] == [OPERATION]
    # The class-spec derivation's and the fetch's own store requests are spans below them.
    assert any(s.parent_id == derivation.span_id for s in spans)
    assert any(s.parent_id == fetch.span_id for s in spans)


def test_a_workflow_call_served_by_the_middleware_s_framework_and_made_with_httpx_has_its_spans(
    tmp_path,
):
    """FastAPI is instrumented before the middleware's framework is loaded, so the app it builds
    for a participant is observed; the call to it, made with httpx, is observed too, and is no
    store request."""
    with a_store() as store:
        spans_file = run_wrapped(tmp_path, store, "served")

    spans = read_spans(spans_file.parent)
    served = [s for s in spans if s.name.startswith("POST /workflows/reserve/execute")]
    assert served, sorted({s.name for s in spans})
    [call] = [
        s
        for s in spans
        if s.kind == "CLIENT"
        and s.name == "POST"
        and not s.attributes.get("store.request")
    ]
    # The call's trace context travels with it, so the served span is below the call's.
    assert any(s.parent_id == call.span_id for s in served)


def test_each_store_request_is_tagged_and_the_store_s_refusals_are_worded_as_graphdb_words_them(
    tmp_path,
):
    """A commit of `running` that the store applies, one of `done` that it refuses by SHACL, a
    query it refuses for want of heap, and one it answers too late."""
    with a_store() as store:
        spans_file = run_wrapped(tmp_path, store, "running", "done", "heap", "late")
        sent = list(store.requests)

    spans = read_spans(spans_file.parent)
    window = Window(
        spans[0].start - timedelta(seconds=1), spans[-1].end + timedelta(seconds=1)
    )
    requests_ = store_request_records(spans, window)
    assert len(requests_) == len(sent)
    for request, (method, path, body) in zip(requests_, sent, strict=True):
        assert (request["method"], request["path"]) == (method, path)
        assert request["seconds"] > 0
        if path == "rest/login":
            # A login keeps no text: it holds the password.
            assert request["kind"] == "login" and request["text"] is None
        else:
            assert request["text"] == body
            assert request["request_bytes"] == len(body.encode())
    assert PASSWORD not in spans_file.read_text(encoding="utf-8")

    writes = [r for r in requests_ if r["writes"]]
    assert [(r["writes"][0]["status"], r["outcome"], r["applied"]) for r in writes] == [
        ("running", "accepted", True),
        ("done", "refused", False),
    ]
    assert writes[0]["writes"] == [
        {"operation": "Box1_transport", "box": None, "status": "running"}
    ]
    refused = [r for r in requests_ if r["outcome"] == "refused"]
    assert [(r["refusal"], r["reason"]) for r in refused] == [
        ("shacl", SHACL_REPORT),
        ("heap", HEAP_REFUSAL),
    ]
    [late] = [r for r in requests_ if r["outcome"] == "timed out"]
    assert late["text"] == "ASK { ?late ?answer ?here }"
    assert "ReadTimeout" in late["reason"]
    assert late["response_bytes"] is None

    commits = commit_records(spans, window)
    assert [c["outcome"] for c in commits] == ["accepted", "ShaclValidationError"]
    found = measures(window, [], requests=requests_, commits=commits)
    assert found["refused_or_unanswered_share"] == pytest.approx(3 / len(requests_))
    assert [h["reason"] for h in found["heap_refusals"]] == [HEAP_REFUSAL]
    # The update of `running`, and not the removal of its receipt, nor the refused `done`.
    assert found["completed_writes_in_window"] == 1
    assert found["commit_latencies_seconds"] == [commits[0]["seconds"]]


def test_a_run_s_measures_come_from_a_wrapped_participant_s_spans_and_the_matcher_s_reads(
    tmp_path,
):
    """The whole path offline: the stand-in participant, through the wrapper, commits `running`,
    `done` (refused once by SHACL, then admitted), a query refused for want of heap and one with
    no answer, against the stand-in store; a run of the bench launcher then reads its spans, with
    the matcher's reads of a graph in which its transport ends in the window. The run's clock
    starts so that the participant's requests fall in the window, after a warm-up of 30 s."""
    refused: list[str] = []

    def refuse_the_first_done(text: str) -> bool:
        if writes_done(text) and not refused:
            refused.append(text)
            return True
        return False

    began = datetime.now(UTC)
    with a_store(refuse_update=refuse_the_first_done) as store:
        spans_file = run_wrapped(
            tmp_path / "recorded", store, "running", "done", "done", "heap", "late"
        )
    recorded = [json.loads(line) for line in spans_file.read_text().splitlines()]
    bench = Bench(
        tmp_path,
        ends=[
            TransportEnd("Box1_transport", "done", "WarehouseManagementSystem1", None)
        ],
        ended_at={"Box1_transport": 40.0},
    )
    bench.clock.start = began - timedelta(seconds=30)
    bench.spans = {"Module1": recorded}

    record = bench.run(Configuration(**(vars(CROSS) | {"warm_up_seconds": 30.0})))

    assert record["outcome"] == "completed"
    measures = record["measures"]
    requests_ = measures["store_requests"]
    assert len(requests_) == len(store.requests) and all(r["window"] for r in requests_)
    assert measures["refused_in_window"] == 2 and measures["timed_out_in_window"] == 1
    assert measures["refused_or_unanswered_share"] == pytest.approx(3 / len(requests_))
    # `running` and the admitted `done`.
    assert measures["completed_writes_in_window"] == 2
    assert measures["completed_writes_per_minute"] == pytest.approx(2.0)
    assert [h["reason"] for h in measures["heap_refusals"]] == [HEAP_REFUSAL]
    assert [c["outcome"] for c in measures["commits"]] == [
        "accepted",
        "ShaclValidationError",
        "accepted",
    ]
    assert len(measures["commit_latencies_seconds"]) == 2
    assert measures["transports_ended_per_minute"] == pytest.approx(1.0)
    # Box1's `running` and `done` were each admitted once; the matcher saw its end.
    assert record["valid"] is True, record["failed_checks"]


def test_a_wrapped_participant_keeps_its_arguments_its_output_and_its_exit_status(
    tmp_path,
):
    script = tmp_path / "participant.py"
    script.write_text(
        "import sys\nprint('arguments', sys.argv[1:], flush=True)\nraise SystemExit(3)\n",
        encoding="utf-8",
    )
    spans = tmp_path / "spans" / "Module9.jsonl"

    completed = subprocess.run(
        wrapper.wrapped([sys.executable, str(script), "--retry-time", "1.0"], spans),
        capture_output=True,
        text=True,
        timeout=60,
        env=participant_environment(None),
        check=False,
    )

    assert completed.returncode == 3, completed.stderr
    assert completed.stdout == "arguments ['--retry-time', '1.0']\n"
    assert spans.is_file()


@pytest.mark.parametrize(
    "plain",
    [
        ["-m", "calendar", "2026", "10"],
        ["-m", "jms_kapps.uc2.process", "--help"],
        ["-m", "jms_kapps.uc2.wms_process", "--help"],
    ],
    ids=["a module", "a module's process", "the WMS's process"],
)
def test_a_wrapped_module_runs_as_python_m_runs_it(tmp_path, plain):
    """Use case 2's own processes too, as the bench launcher starts them, here only up to their
    usage, since they need a store to run."""
    spans = tmp_path / "spans" / "Module9.jsonl"
    command = [sys.executable, *plain]

    def output(run: list[str]) -> str:
        return subprocess.run(
            run,
            capture_output=True,
            text=True,
            timeout=60,
            env=participant_environment(None),
            check=True,
        ).stdout

    assert output(wrapper.wrapped(command, spans)) == output(command)


def test_the_wrapper_s_command_line_keeps_the_participant_s_python_and_arguments():
    command = ["/usr/bin/python3", "-m", "jms_kapps.uc2.process", "--dataset", "eval-1"]

    assert wrapper.wrapped(command, Path("runs/1/spans/Module1.jsonl")) == [
        "/usr/bin/python3",
        "-m",
        "jms_kapps.evaluation.wrapper",
        "--spans",
        str(Path("runs/1/spans/Module1.jsonl")),
        "--",
        "-m",
        "jms_kapps.uc2.process",
        "--dataset",
        "eval-1",
    ]


def test_the_wrapping_is_taken_back_and_leaves_the_libraries_as_they_were(tmp_path):
    """In this process: the mapper's calls and requests' sending are the libraries' own again
    once the observation is closed."""
    from kapps_ogm.node.core import Node
    from kapps_ogm.ogm import OGM

    from jms_kapps.evaluation.instrumentation import instrument

    before = {
        name: OGM.__dict__[name] for name in ("commit", "fetch", "get_class_spec")
    }
    diff, send = Node.__dict__["diff"], requests.Session.send

    observed = instrument(
        tmp_path / "Module1.jsonl", participant="Module1", store_url=None
    )
    try:
        assert OGM.__dict__["commit"] is not before["commit"]
        assert OGM.__dict__["commit"].__wrapped__ is before["commit"]
        assert requests.Session.send is not send
    finally:
        observed.close()

    assert {name: OGM.__dict__[name] for name in before} == before
    assert Node.__dict__["diff"] is diff
    assert requests.Session.send is send


def test_a_deadlock_alarm_of_a_wrapped_participant_is_written_in_the_participants_log_format(
    tmp_path,
):
    """The wrapper changes nothing of a participant's output: the alarm line reads as a module
    writes it."""
    spans = tmp_path / "spans" / "Module3.jsonl"
    with a_store() as store:
        completed = subprocess.run(
            wrapper.wrapped(
                [sys.executable, str(PARTICIPANT), store.url, store.dataset, "alarm"],
                spans,
            ),
            capture_output=True,
            text=True,
            timeout=120,
            env=participant_environment(store),
            check=True,
        )

    assert f"WARNING:jms_kapps.uc2.module:{ALARM}" in completed.stderr.splitlines()


def test_a_plain_install_of_jms_kapps_resolves_without_opentelemetry():
    """OpenTelemetry is the extra `evaluation`: the lock gives a plain install none of it."""
    root = find_repo_root(Path(__file__))
    if root is None:
        pytest.skip("an installed copy has no manifest and no lock")
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert not [
        r for r in manifest["project"]["dependencies"] if "opentelemetry" in r.lower()
    ]
    # The extra also holds the run report's pandas and matplotlib.
    assert all(
        r.startswith(("opentelemetry-", "pandas", "matplotlib"))
        for r in manifest["project"]["optional-dependencies"]["evaluation"]
    )
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    [jms_kapps] = [p for p in lock["package"] if p["name"] == "jms-kapps"]
    plain = {d["name"] for d in jms_kapps["dependencies"]}
    assert not {name for name in plain if name.startswith("opentelemetry")}
    assert not plain & {"pandas", "matplotlib"}
    extra = {d["name"] for d in jms_kapps["optional-dependencies"]["evaluation"]}
    assert "opentelemetry-sdk" in extra


def test_without_opentelemetry_the_harness_imports_and_says_what_the_wrapper_needs():
    """With every module of OpenTelemetry unimportable, as in a plain install: the harness's
    commands import, a run without the wrapper is possible, and one with it is refused before it
    starts, naming the extra."""
    script = """
import importlib.abc, json, sys

class NoOpenTelemetry(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ModuleNotFoundError(name)
        return None

sys.meta_path.insert(0, NoOpenTelemetry())
import jms_kapps.evaluation.bench, jms_kapps.evaluation.run_uc2, jms_kapps.evaluation.sweep
import jms_kapps.evaluation.observation, jms_kapps.evaluation.matcher
from jms_kapps.evaluation import wrapper
try:
    wrapper.available()
    refused = None
except wrapper.WrapperUnavailable as error:
    refused = str(error)
print(json.dumps({"missing": wrapper.missing(), "refused": refused,
                  "otel": [m for m in sys.modules if m.startswith("opentelemetry")]}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env=participant_environment(None),
        check=True,
    )
    found = json.loads(completed.stdout)

    assert found["missing"] == list(wrapper.REQUIRED)
    assert "uv sync --extra evaluation" in found["refused"]
    assert found["otel"] == []
