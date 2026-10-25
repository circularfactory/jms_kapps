"""A run of `jms-uc1` for a live test: start it, wait until its five processes are live, press the
planner, and read what the run left in the graph.

`test_uc1_run_live.py` drives the loop end to end with these, and `test_uc1_claims_live.py` proves
use case 1's paper claims on one such run.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from _wait import eventually
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import bindings
from jms_kapps.uc1.anomaly_detector import SAVE_UQ_OPTION
from jms_kapps.uc1.vocabulary import JMS, PROV, UQ

# How long a run's five processes may take to turn live. Each check reads the page, so the test
# asks only every half second.
LIVE_WITHIN_SECONDS = 90.0
POLL_SECONDS = 0.5

# How long one attempt may take, from the press to the end of its operation.
OPERATION_WITHIN_SECONDS = 60.0


@contextmanager
def started_uc1(
    dataset: str, directory: Path, *, saves_uq: bool = False
) -> Iterator[tuple[subprocess.Popen, Path, str]]:
    """`jms-uc1` on a free page port, learning every second, in a session of its own, with its
    output in `directory`; stopped again at the end, whatever happens. With `saves_uq`, it saves
    measurements as uncertainty quantifications from its start (`SAVE_UQ_OPTION`); off by
    default, as `jms-uc1` is."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = shutil.which("jms-uc1")
    assert command is not None, "the jms-uc1 console script is not installed"
    log = directory / "uc1.log"
    with log.open("w") as output:
        run = subprocess.Popen(
            [
                command,
                *("--dataset", dataset),
                *("--learn-seconds", "1"),
                *("--page-port", str(port)),
                *([SAVE_UQ_OPTION] if saves_uq else []),
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
    """Wait until the run's five processes are all live. A `jms-uc1` that has exited never gets
    there, so that fails at once, with what it printed."""

    def all_live() -> bool:
        if run.poll() is not None:
            pytest.fail(f"jms-uc1 exited with {run.returncode}:\n{log.read_text()}")
        states = [p["state"] for p in state(page_url)]
        return len(states) == 5 and set(states) == {"live"}

    eventually(all_live, timeout=LIVE_WITHIN_SECONDS, interval=POLL_SECONDS)


def planner_address(page_url: str) -> str:
    """The planner's address: the address of the process named "Planner" in the state."""
    planners = [p for p in state(page_url) if p["name"] == "Planner"]
    assert len(planners) == 1
    address = planners[0]["address"]
    assert address is not None
    return address


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
    """The operations of the run and how each ended: its status, its result, its outcome, whether
    it holds a time-series reference, and its snapshot's max dynamic torque."""
    rows = bindings(
        db,
        f"""
        SELECT ?op ?status ?result ?outcome ?reference ?torque WHERE {{
            ?op a <{JMS.UnscrewingOperation}> .
            OPTIONAL {{ ?op <{SVC.hasOperationStatus}> ?status }}
            OPTIONAL {{ ?op <{SVC.hasExecutionResult}> ?result }}
            OPTIONAL {{ ?op <{JMS.hasDetectionResult}> ?outcome }}
            OPTIONAL {{ ?op <{JMS.hasTimeSeriesReference}> ?reference }}
            OPTIONAL {{
                ?op <{JMS.classifiedWith}> ?snapshot .
                ?snapshot <{JMS.hasMaxDynamicTorque}> ?torque
            }}
        }}
        """,
    )
    return [
        {
            "status": str(row.get("status") or ""),
            "result": str(row.get("result") or ""),
            "outcome": str(row.get("outcome") or ""),
            "reference": str(row.get("reference") or ""),
            "torque": float(row["torque"]) if row.get("torque") is not None else None,
        }
        for row in rows
    ]


def ended(db: TripleStore) -> list[dict]:
    """The operations of the run that have ended, `done` or `failed`."""
    return [o for o in operations(db) if o["status"] in ("done", "failed")]


def await_ended(db: TripleStore, count: int) -> list[dict]:
    """Wait until `count` operations of the run have ended, `done` or `failed`, and give those.
    A failure outcome ends its operation `failed`."""
    eventually(
        lambda: len(ended(db)) == count,
        timeout=OPERATION_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    return ended(db)


def m4_torque(db: TripleStore) -> float:
    """`:M4`'s max dynamic torque, as the seed wrote it or the learner refined it."""
    (row,) = bindings(
        db,
        f"SELECT ?value WHERE {{ <{JMS.M4}> <{JMS.hasMaxDynamicTorque}> ?value }}",
    )
    return float(row["value"])


def provenance(db: TripleStore) -> list[dict]:
    """The provenance of each parameter copy of the run: the classification that generated
    it, that classification's agent and end time, and the snapshot that the store infers back
    from the classification (`prov:generated`)."""
    rows = bindings(
        db,
        f"""
        SELECT ?snapshot ?classification ?agent ?ended ?generated WHERE {{
            ?op <{JMS.classifiedWith}> ?snapshot .
            ?snapshot <{PROV.wasGeneratedBy}> ?classification .
            ?classification a <{PROV.Activity}> ;
                <{PROV.wasAssociatedWith}> ?agent ;
                <{PROV.endedAtTime}> ?ended .
            OPTIONAL {{ ?classification <{PROV.generated}> ?generated }}
        }}
        """,
    )
    return [{name: str(value) for name, value in row.items()} for row in rows]


def random_variables(db: TripleStore) -> int:
    """How many random variables (`uq:RandomVariable`) the graph holds: none after a run with
    the mode "Save measurements as uncertainty quantifications" off."""
    (row,) = bindings(
        db, f"SELECT (COUNT(?v) AS ?n) WHERE {{ ?v a <{UQ.RandomVariable}> }}"
    )
    return int(row["n"])
