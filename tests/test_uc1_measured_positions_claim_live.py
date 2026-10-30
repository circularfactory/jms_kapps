"""Use case 1's measured positions as uncertainty quantifications on one run of `jms-uc1`, on a live
GraphDB and a live InfluxDB.

The run starts `jms-uc1` with the mode "Save measurements as uncertainty quantifications" on,
whatever `--uc1-save-measurements-as-uq` says. It presses Unscrew on the planner for Screw1 and
waits until its operation is `done`, then for Screw2 and waits until that operation has ended:
`failed`, by its MissingScrew. The test then reads each measured position the run left by plain
SPARQL: Screw1's head position at contact, and the end position of the approach of Screw2's
failed operation. Each holds its best estimate and one random variable, whose literal reads back
with the datatype `uq:gmmLiteral`, parses, and holds the best estimate as its mean.

**Only on a dataset you can lose.** The run clears the dataset `--dataset` names, as every graphdb
test does. On a shared store never `Tests`, which every library's live suite shares, nor
`jms-kapps`. The frames go into InfluxDB's database `jms-kapps`, the runtime's own, so the run adds
no database. It needs the local stack (`docker compose up -d --wait`). It runs on POSIX: it starts
`jms-uc1` in a session of its own and stops it with Ctrl+C to that session, as a terminal does.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator

import pytest
from _uc1_run import (
    await_all_live,
    await_ended,
    planner_address,
    press,
    random_variables,
    started_uc1,
)
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.seed import POSITION_STANDARD_DEVIATION, SCREW, SCREW_2
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.uncertainty import read_measurement
from jms_kapps.uc1.vocabulary import JMS, UQ

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.posix(
        reason="starts jms-uc1 in a session of its own and signals its process group"
    ),
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]


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
def run(dataset, influxdb, tmp_path_factory) -> Iterator[TripleStore]:
    """One run of `jms-uc1` with the mode on: Screw1, then Screw2. It stays up until the module's
    tests are done, then Ctrl+C stops it."""
    db = connect(dataset)
    with started_uc1(dataset, tmp_path_factory.mktemp("uc1"), saves_uq=True) as (
        process,
        log,
        page_url,
    ):
        await_all_live(process, log, page_url)
        planner = planner_address(page_url)
        press(planner, SCREW)
        await_ended(db, 1)
        press(planner, SCREW_2)
        await_ended(db, 2)
        yield db
        stop(process)


def stop(process: subprocess.Popen) -> None:
    """Ctrl+C to the run's session, as a terminal sends it."""
    os.killpg(process.pid, signal.SIGINT)
    process.wait(timeout=60)


def operation_status(db: TripleStore, screw: IRI) -> tuple[str, str]:
    """The IRI and the status of the one operation of `screw`."""
    (row,) = bindings(
        db,
        f"""
        SELECT DISTINCT ?op ?status WHERE {{
            ?op a <{JMS.UnscrewingOperation}> ;
                <{JMS.concernsScrew}> <{screw}> ;
                <{SVC.hasOperationStatus}> ?status .
        }}
        """,
    )
    return str(row["op"]), str(row["status"])


def measured(
    db: TripleStore, holder: str, estimate: IRI, variable: IRI
) -> tuple[float, dict]:
    """The best estimate that `holder` holds by `estimate`, and the literal of the one random
    variable it links by `variable`, as SPARQL's JSON results write it: its `datatype` and its
    lexical `value`."""
    rows = bindings(
        db,
        f"""
        SELECT DISTINCT ?estimate ?variable ?literal WHERE {{
            <{holder}> <{estimate}> ?estimate ;
                <{variable}> ?variable .
            ?variable a <{UQ.RandomVariable}> ;
                <{UQ.hasDistribution}> ?literal .
        }}
        """,
        convert=False,
    )
    assert len(rows) == 1, rows
    (row,) = rows
    return float(row["estimate"]["value"]), row["literal"]


def test_each_measured_position_reads_back_as_a_gaussian_mixture_literal(run):
    """A run of use case 1 commits each measured position as a random variable
    with a Gaussian-mixture literal beside its best estimate, and fetches it back cleanly. The
    literal of the failed operation's approach end position and that of Screw1's head position
    each read back with the datatype `uq:gmmLiteral`, parse, and hold the best estimate as their
    mean and the robot's standard deviation squared as their variance."""
    _, done = operation_status(run, SCREW)
    failed_operation, failed = operation_status(run, SCREW_2)
    assert (done, failed) == (OperationStatus.DONE, OperationStatus.FAILED)

    for holder, estimate, variable in (
        (
            failed_operation,
            JMS.hasMeasuredApproachEndPosition,
            JMS.hasApproachEndPositionVariable,
        ),
        (
            str(SCREW),
            JMS.hasMeasuredScrewHeadPosition,
            JMS.hasScrewHeadPositionVariable,
        ),
    ):
        best, literal = measured(run, holder, estimate, variable)
        assert literal.get("datatype") == str(UQ.gmmLiteral), literal
        assert read_measurement(literal["value"]) == pytest.approx(
            (best, POSITION_STANDARD_DEVIATION**2)
        )

    assert random_variables(run) == 2
