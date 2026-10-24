"""`jms-uc1 infeasible-operation` on a live GraphDB and a live InfluxDB.

Paper claim 19: admission refuses a planned operation that the factory information state cannot
carry out. The command seeds the dataset with a Screw whose Screw type holds none of the seven
values an attempt needs, starts use case 1's five participants, presses Unscrew on `:Screw1` and
then on that Screw, and stops them. The press is the planning decision: the planner issues the
unscrewing operation, and the store refuses the planner's commit by
`:UnscrewingOperationScrewShape`, with its message. The normal operation before it is admitted and
ends `done`.

The test runs the command as a person runs it, reads its exit code, its output and the report it
records, and then the graph it leaves: the refused operation is nowhere in it.

**Only on a dataset you can lose.** The command clears the dataset `--dataset` names, as every
graphdb test does. On a shared store never `Tests`, which every library's live suite shares, nor
`jms-kapps`. The attempt's frames go into InfluxDB's database `jms-kapps`, the runtime's own, so
the run adds no database. It needs the local stack (`docker compose up -d --wait`).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import (
    readable_results,
    validation_results,
)
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.infeasible_operation import RULE
from jms_kapps.uc1.seed import SCREW, UNMEASURED_SCREW
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.posix(reason="stops the command's process group with SIGTERM"),
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# How long the whole command may take: the start of five processes, one attempt and the refusal.
COMMAND_WITHIN_SECONDS = 300.0

# What the shape says, as the shapes file gives it.
MESSAGE = (
    "An unscrewing operation concerns exactly one Screw, whose Screw type holds each of the "
    "seven values the attempt needs (the six detection thresholds and the preparation turning "
    "angle), once each."
)


@pytest.fixture(scope="module")
def influxdb():
    client = connect_influxdb()
    if not client.available():
        client.close()
        pytest.skip(
            "no InfluxDB answers; start the local one with `docker compose up -d --wait`"
        )
    yield client
    client.close()


@pytest.fixture(scope="module")
def ran(
    dataset, influxdb, tmp_path_factory
) -> tuple[subprocess.CompletedProcess, Path]:
    """One run of the command, to its end, with the report it records. The command runs in a
    session of its own, so a timeout stops it and the five participants it started."""
    command = shutil.which("jms-uc1")
    assert command is not None, "the jms-uc1 console script is not installed"
    record = tmp_path_factory.mktemp("infeasible") / "refusal.nq"
    arguments = [
        command,
        "infeasible-operation",
        *("--dataset", dataset),
        *("--record", str(record)),
        *("--wait-seconds", "120"),
    ]
    run = subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = run.communicate(timeout=COMMAND_WITHIN_SECONDS)
    finally:
        if run.poll() is None:
            os.killpg(run.pid, signal.SIGTERM)
            run.communicate(timeout=30)
    done = subprocess.CompletedProcess(arguments, run.returncode, stdout, stderr)
    return done, record


@pytest.mark.paper_claim
def test_the_store_refuses_the_infeasible_operation_by_the_shape(ran) -> None:
    """Paper claim 19: the command exits 0, and prints the refusal by
    `:UnscrewingOperationScrewShape` with its message, after the normal operation was admitted."""
    done, record = ran

    assert done.returncode == 0, done.stdout + done.stderr
    assert "the store: admitted" in done.stdout
    assert "the store: refused, by UnscrewingOperationScrewShape" in done.stdout
    assert MESSAGE in done.stdout

    report = record.read_text(encoding="utf-8")
    assert {result.shape for result in validation_results(report)} == {str(RULE)}
    (result,) = readable_results(report) or []
    assert result.value is not None and result.value.full == str(UNMEASURED_SCREW)
    assert result.message is not None and result.message.short == MESSAGE


def test_the_refused_operation_left_nothing_in_the_graph(ran, dataset) -> None:
    """The store admits all of the planner's commit or none of it: no operation names the
    refused Screw, and the Screw has no back-link. The admitted one ended `done`."""
    db = connect(dataset)

    refused = bindings(
        db,
        f"SELECT ?op WHERE {{ {{ ?op <{JMS.concernsScrew}> <{UNMEASURED_SCREW}> }} "
        f"UNION {{ <{UNMEASURED_SCREW}> <{JMS.hasOperation}> ?op }} }}",
    )
    assert refused == []

    admitted = bindings(
        db,
        f"SELECT ?status WHERE {{ ?op <{JMS.concernsScrew}> <{SCREW}> ; "
        f"<{SVC.hasOperationStatus}> ?status }}",
    )
    assert [str(row["status"]) for row in admitted] == [OperationStatus.DONE]
