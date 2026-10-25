"""The switch "Save measurements as uncertainty quantifications" of UC1's page in a run of
`jms-uc1`, on a live GraphDB and a live InfluxDB: it sets the anomaly detector's mode through the
page's port, and the page's Measured positions show what the detector saved.

The run starts with the mode off, as `jms-uc1` does. The test turns the mode on through the page's
passthrough, as the switch does, and presses Unscrew on Screw1: Measured positions show Screw1's
head position, with the standard deviation of the seeded robot and a literal that the store
returns as a `uq:gmmLiteral` and whose mean is the best estimate. It turns the mode off and
presses Unscrew on Screw2: the failed operation's end position is not saved, and Screw1's row
stays as it was.

**Only on a dataset you can lose.** The test clears the dataset `--dataset` names, as every graphdb
test does. It needs the local stack (`docker compose up -d --wait`) and runs on POSIX: it starts
`jms-uc1` in a session of its own, as `test_uc1_run_live.py` does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest
from _uc1_run import (
    POLL_SECONDS,
    await_all_live,
    await_ended,
    planner_address,
    press,
    started_uc1,
)
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import TripleStore

from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.page_lists import MEASURED_POSITIONS, SCREW_HEAD_POSITION
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

# How long the page's background read may take to show what a commit wrote.
SHOWN_WITHIN_SECONDS = 10.0


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
def jms_uc1(dataset, tmp_path, influxdb) -> Iterator[tuple[subprocess.Popen, Path, str]]:
    """`jms-uc1` with the mode off, as it starts without its option."""
    with started_uc1(dataset, tmp_path) as run:
        yield run


def _state(page_url: str) -> dict:
    return httpx.get(urljoin(page_url, "api/state"), timeout=5).json()


def _switch(page_url: str, route: str, on: bool) -> None:
    """Set the detector's mode as the switch does: a write to its route through the page's port."""
    answer = httpx.put(urljoin(page_url, route), json={"on": on}, timeout=10)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"on": on}


def _shown_after(page_url: str, ended: int) -> list[dict]:
    """The rows of Measured positions, from a read of the page's lists that already shows the
    `ended` operations of the run as ended."""

    def caught_up() -> bool:
        operations = _state(page_url)["operations"]["rows"]
        return sum(row["status"] in ("done", "failed") for row in operations) == ended

    eventually(caught_up, timeout=SHOWN_WITHIN_SECONDS, interval=POLL_SECONDS)
    measured = _state(page_url)["measured_positions"]
    assert measured["error"] is None
    assert measured["query"] == MEASURED_POSITIONS
    return measured["rows"]


def _datatypes(db: TripleStore) -> list[str]:
    """The datatype of each literal of a random variable in the graph, as the store returns it."""
    rows = bindings(
        db,
        f"SELECT ?literal WHERE {{ ?variable <{UQ.hasDistribution}> ?literal }}",
        convert=False,
    )
    return [row["literal"].get("datatype", "") for row in rows]


def test_the_switch_saves_measurements_from_the_next_classification_and_the_page_shows_them(
    jms_uc1, db
) -> None:
    run, log, page_url = jms_uc1
    await_all_live(run, log, page_url)
    route = _state(page_url)["mode_route"]
    assert route is not None
    assert httpx.get(urljoin(page_url, route), timeout=10).json() == {"on": False}
    planner = planner_address(page_url)

    _switch(page_url, route, True)
    press(planner, SCREW)
    await_ended(db, 1)

    (row,) = _shown_after(page_url, 1)
    assert (row["subject"], row["position"]) == (SCREW.fragment, SCREW_HEAD_POSITION)
    assert float(row["standard_deviation"]) == pytest.approx(POSITION_STANDARD_DEVIATION)
    mean, variance = read_measurement(row["literal"])
    assert mean == pytest.approx(float(row["estimate"]))
    assert variance == pytest.approx(POSITION_STANDARD_DEVIATION**2)
    assert _datatypes(db) == [str(UQ.gmmLiteral)]

    _switch(page_url, route, False)
    press(planner, SCREW_2)
    operations = await_ended(db, 2)

    assert {o["status"] for o in operations} == {"done", "failed"}
    assert _shown_after(page_url, 2) == [row]
    (end,) = bindings(
        db,
        f"SELECT (COUNT(?v) AS ?n) WHERE {{ ?op <{JMS.hasApproachEndPositionVariable}> ?v }}",
    )
    assert int(end["n"]) == 0
