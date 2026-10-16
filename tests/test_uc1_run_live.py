"""The KSM 268 loop end to end on a live GraphDB and a live InfluxDB: `jms-uc1` runs the press
through the loop by the real processes.

A press opens the operation on the planner; the screwing resource records the attempt's curves
in InfluxDB and gives the detector the reference; the detector reads them, classifies the attempt,
commits the outcome and the decision snapshot, and ends the operation; the planner's dashboard
shows the outcome; the learner reads the new operation's curves, commits a new `:M4` value, and
the next snapshot holds it; Ctrl+C at the terminal stops all four processes and every service
deregisters.

**Only on a disposable dataset.** The test clears the dataset `--dataset` names, as every graphdb
test does. Never `Tests`, which every library's live suite shares, nor `jms-kapps`, the demo's.
The test takes the dataset by `--dataset`, needs the GraphDB connection variables, and the frames
go into InfluxDB's database `jms-kapps`, the runtime's own, so it adds no database (jms_kapps
103). It needs the local stack (`docker compose up -d --wait`). It runs on POSIX: it starts
`jms-uc1` in a session of its own and stops it with Ctrl+C to that session, as a terminal does.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.services import live_resources
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.seed import SCREW, SCREW_2
from jms_kapps.uc1.timeseries import FrameReader
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, PROV

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

# How long a run's four processes may take to turn live. Each check reads the page, so the test
# asks only every half second.
LIVE_WITHIN_SECONDS = 90.0
POLL_SECONDS = 0.5

# How long one attempt may take, from the press to its operation done.
OPERATION_WITHIN_SECONDS = 60.0


@pytest.fixture
def db(dataset) -> TripleStore:
    return connect(dataset)


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


@pytest.fixture
def jms_uc1(dataset, tmp_path) -> Iterator[tuple[subprocess.Popen, Path, str]]:
    """`jms-uc1` on a free page port, learning every second, in a session of its own; stopped
    again at the end, whatever happens."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = shutil.which("jms-uc1")
    assert command is not None, "the jms-uc1 console script is not installed"
    log = tmp_path / "uc1.log"
    with log.open("w") as output:
        run = subprocess.Popen(
            [
                command,
                *("--dataset", dataset),
                *("--learn-seconds", "1"),
                *("--page-port", str(port)),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        yield run, log, f"http://127.0.0.1:{port}/"
    finally:
        if run.poll() is None:
            os.killpg(run.pid, signal.SIGTERM)
            run.wait(timeout=30)


def state(page_url: str) -> list[dict]:
    return httpx.get(f"{page_url}api/state", timeout=5).json()["processes"]


def await_all_live(run: subprocess.Popen, log: Path, page_url: str) -> None:
    """Wait until the run's four processes are all live. A `jms-uc1` that has exited never gets
    there, so that fails at once, with what it printed."""

    def all_live() -> bool:
        if run.poll() is not None:
            pytest.fail(f"jms-uc1 exited with {run.returncode}:\n{log.read_text()}")
        states = [p["state"] for p in state(page_url)]
        return len(states) == 4 and set(states) == {"live"}

    eventually(all_live, timeout=LIVE_WITHIN_SECONDS, interval=POLL_SECONDS)


def press(planner_address: str, screw: IRI) -> dict:
    """Press Unscrew on the planner's dashboard and assert it started an operation."""
    started = httpx.post(
        f"{planner_address.rstrip('/')}/workflows/unscrew/execute",
        json={"screw": str(screw)},
        timeout=30,
    )
    assert started.status_code == 200, started.text
    assert started.json()["started"] is True, started.text
    return started.json()


def operations(db: TripleStore) -> list[dict]:
    """The operations of the run and how each ended: its status, its outcome, whether it holds a
    time-series reference, and its snapshot's max dynamic torque."""
    rows = bindings(
        db,
        f"""
        SELECT ?op ?status ?outcome ?reference ?torque WHERE {{
            ?op a <{JMS.UnscrewingOperation}> .
            OPTIONAL {{ ?op <{SVC.operationStatus}> ?status }}
            OPTIONAL {{ ?op <{JMS.hasOutcome}> ?outcome }}
            OPTIONAL {{ ?op <{JMS.hasTimeSeriesReference}> ?reference }}
            OPTIONAL {{
                ?op <{JMS.usedValues}> ?snapshot .
                ?snapshot <{JMS.hasMaxDynamicTorque}> ?torque
            }}
        }}
        """,
    )
    return [
        {
            "status": str(row.get("status") or ""),
            "outcome": str(row.get("outcome") or ""),
            "reference": str(row.get("reference") or ""),
            "torque": float(row["torque"]) if row.get("torque") is not None else None,
        }
        for row in rows
    ]


def m4_torque(db: TripleStore) -> float:
    """`:M4`'s max dynamic torque, as the seed wrote it or the learner refined it."""
    (row,) = bindings(
        db,
        f"SELECT ?value WHERE {{ <{JMS.M4}> <{JMS.hasMaxDynamicTorque}> ?value }}",
    )
    return float(row["value"])


def provenance(db: TripleStore) -> list[dict]:
    """The provenance of each decision snapshot of the run: the classification that generated
    it, that classification's agent and end time, and the snapshot that the store infers back
    from the classification (`prov:generated`). (Added on 2026-10-16, jms_kapps 59.)"""
    rows = bindings(
        db,
        f"""
        SELECT ?snapshot ?classification ?agent ?ended ?generated WHERE {{
            ?op <{JMS.usedValues}> ?snapshot .
            ?snapshot <{PROV.wasGeneratedBy}> ?classification .
            ?classification a <{PROV.Activity}> ;
                <{PROV.wasAssociatedWith}> ?agent ;
                <{PROV.endedAtTime}> ?ended .
            OPTIONAL {{ ?classification <{PROV.generated}> ?generated }}
        }}
        """,
    )
    return [{name: str(value) for name, value in row.items()} for row in rows]


def test_a_press_runs_the_loop_end_to_end_and_the_learner_closes_it(
    db, dataset, jms_uc1, influxdb
):
    run, log, page_url = jms_uc1
    await_all_live(run, log, page_url)

    # The page serves its dashboard links.
    page = httpx.get(page_url, timeout=5)
    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert page.text.count("Open dashboard</a>") == 4

    # The planner's address is the address of the process named "Planner" in the state.
    planners = [p for p in state(page_url) if p["name"] == "Planner"]
    assert len(planners) == 1
    planner_address = planners[0]["address"]
    assert planner_address is not None

    before = m4_torque(db)

    press(planner_address, SCREW)
    eventually(
        lambda: len([o for o in operations(db) if o["status"] == "done"]) == 1,
        timeout=OPERATION_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    (first,) = [o for o in operations(db) if o["status"] == "done"]
    assert first["outcome"].endswith("ScrewIntact"), first
    assert first["torque"] == before, first
    assert first["reference"].startswith("influxdb:///jms-kapps/"), first
    FrameReader(influxdb).read(first["reference"])

    # The decision carries its provenance: the snapshot names the detector's classification, with
    # its agent and its end time, in the shape the store enforces (jms_kapps 59).
    (decided,) = provenance(db)
    assert decided["agent"] == str(DETECTOR), decided
    assert decided["generated"] == decided["snapshot"], decided
    assert decided["ended"], decided

    # The planner's box shows the outcome. The planner learns of the end from the middleware's
    # notification, which follows the commit that ended the operation, so the box may lag.
    box = f"{planner_address.rstrip('/')}/dashboard/box/planner"
    eventually(
        lambda: "ScrewIntact" in httpx.get(box, timeout=5).text,
        timeout=10,
        interval=POLL_SECONDS,
    )

    # The learner commits a new `:M4` value, and the next snapshot holds it.
    eventually(lambda: m4_torque(db) != before, timeout=30, interval=POLL_SECONDS)
    after = m4_torque(db)

    # Screw1 has an operation now: the planner no longer offers it, and refuses a press on it.
    assert f'value="{SCREW}"' not in httpx.get(box, timeout=5).text
    refused = httpx.post(
        f"{planner_address.rstrip('/')}/workflows/unscrew/execute",
        json={"screw": str(SCREW)},
        timeout=30,
    ).json()
    assert refused["started"] is False, refused

    # Screw2 replays `missing_screw`: MissingScrew, and its snapshot holds the learner's value.
    press(planner_address, SCREW_2)
    eventually(
        lambda: len([o for o in operations(db) if o["status"] == "done"]) == 2,
        timeout=OPERATION_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    done = [o for o in operations(db) if o["status"] == "done"]
    (second,) = [o for o in done if o["outcome"].endswith("MissingScrew")] or [None]
    assert second is not None, done
    assert second["torque"] == after, second

    # Ctrl+C stops everything, and every service deregisters.
    os.killpg(run.pid, signal.SIGINT)
    assert run.wait(timeout=60) == 0
    assert live_resources(db) == set()
