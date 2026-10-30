"""The loop end to end on a live GraphDB and a live InfluxDB: `jms-uc1` runs the press
through the loop by the real processes.

A press opens the operation on the planner; the screwing resource records the attempt's curves
in InfluxDB and gives the detector the reference; the detector reads them, classifies the attempt,
commits the outcome and the parameter copy, and ends the operation; the planner's dashboard
shows the outcome; the learner reads the new operation's curves, commits a new `:M4` value, and
the next snapshot holds it; Ctrl+C at the terminal stops all four processes and every service
deregisters.

**Only on a dataset you can lose.** The test clears the dataset `--dataset` names, as every graphdb
test does. On a shared store never `Tests`, which every library's live suite shares, nor
`jms-kapps`. On the local stack `jms-kapps` is its only dataset: the run clears it, and the next
demo seeds it again. The test takes the dataset by `--dataset`, needs the GraphDB connection
variables, and the frames go into InfluxDB's database `jms-kapps`, the runtime's own, so it adds no
database. It needs the local stack (`docker compose up -d --wait`). It runs on POSIX: it starts
`jms-uc1` in a session of its own and stops it with Ctrl+C to that session, as a terminal does.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest
from _html import rows
from _uc1_run import (
    OPERATION_WITHIN_SECONDS,
    POLL_SECONDS,
    await_all_live,
    m4_torque,
    operations,
    press,
    provenance,
    random_variables,
    started_uc1,
    state,
)
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.services import live_resources
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc1.anomaly_detector import RESOURCE as DETECTOR
from jms_kapps.uc1.screwing_resource import RESOURCE as SCREWING_RESOURCE
from jms_kapps.uc1.seed import M4_VALUES, SCREW, SCREW_2
from jms_kapps.uc1.timeseries import FrameReader
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, TYPE_LEVEL

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


def _seeded_values(max_dynamic_torque: float) -> str:
    """What the box shows of the seven values of `:M4`: the seed's, with `max_dynamic_torque`."""
    values = {**M4_VALUES, JMS.hasMaxDynamicTorque: max_dynamic_torque}
    return " ".join(f"{values[prop]:g}" for prop in TYPE_LEVEL)


def _type_values_of(box_url: str, screw: IRI) -> list[str]:
    """The cell "Type values" of each row of the box "Attempts" for `screw`."""
    return [
        row[5]
        for row in rows(httpx.get(box_url, timeout=5).text)
        if row[2] == screw.fragment
    ]


def _given_line(log_text: str, screw: IRI, fragment: str) -> bool:
    """Whether one line of the log says the attempt on `screw` is given the values of `:M4` with `fragment`."""
    return any(
        f"on {screw.fragment} is given the values of M4" in line
        and (f"{fragment}," in line or line.endswith(fragment))
        for line in log_text.splitlines()
    )


def _lists(page_url: str) -> dict:
    """Operations and Learned values, as the page's state gives them."""
    answer = httpx.get(f"{page_url}api/state", timeout=5).json()
    return {name: answer[name] for name in ("operations", "learned_values")}


def _local(value: dict | None) -> str | None:
    return None if value is None else value["value"].rsplit("#", 1)[-1]


def _operation_rows(db: TripleStore, query: str) -> list[tuple]:
    """The rows `query` answers on `db`: the operation, its Screw and its outcome by local name,
    and its issuer, status, reason and deciding resource as the store writes them."""
    return [
        (
            _local(row["operation"]),
            _local(row.get("screw")),
            row.get("issuer", {}).get("value"),
            row.get("status", {}).get("value"),
            _local(row.get("outcome")),
            row.get("reason", {}).get("value"),
            row.get("decidedBy", {}).get("value"),
        )
        for row in bindings(db, query, convert=False)
    ]


def _shown_operation(row: dict) -> tuple:
    return tuple(
        row[key]
        for key in (
            "operation",
            "screw",
            "issuer",
            "status",
            "outcome",
            "reason",
            "decided_by",
        )
    )


def _learned_rows(db: TripleStore, query: str) -> list[tuple[str, str]]:
    return [
        (_local(row["screwType"]) or "", row["maxDynamicTorque"]["value"])
        for row in bindings(db, query, convert=False)
    ]


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
def jms_uc1(
    dataset, tmp_path, uc1_saves_uq
) -> Iterator[tuple[subprocess.Popen, Path, str]]:
    """`jms-uc1` on a free page port, learning every second, in a session of its own, in the mode
    that `--uc1-save-measurements-as-uq` selects; stopped again at the end, whatever happens."""
    with started_uc1(dataset, tmp_path, saves_uq=uc1_saves_uq) as run:
        yield run


def test_a_press_runs_the_loop_end_to_end_and_the_learner_closes_it(
    db, dataset, jms_uc1, influxdb, uc1_saves_uq
):
    run, log, page_url = jms_uc1
    await_all_live(run, log, page_url)

    # The page serves its dashboard links.
    page = httpx.get(page_url, timeout=5)
    assert page.status_code == 200, page.text
    assert page.headers["content-type"].startswith("text/html")
    assert page.text.count(">dashboard</a>") == 4
    # Each link of the planner's row opens its page.
    (planner_row,) = [p for p in state(page_url) if p["name"] == "Planner"]
    assert [link["label"] for link in planner_row["links"]] == [
        "API docs",
        "activity",
        "dashboard",
        "logs",
    ]
    # Each link of every row opens its page through the page's own port, the activity page's
    # live stream included, and so does each dashboard of the printed block. The robot controller
    # stand-in is a device without a service: its row has no address, no links and no dashboard,
    # so it is skipped here.
    processes = state(page_url)
    assert [p["name"] for p in processes if p["address"] is None] == [
        "Robot controller"
    ]
    assert all(p["links"] == [] for p in processes if p["address"] is None)
    for process in processes:
        if process["address"] is None:
            continue
        for link in process["links"]:
            assert link["href"].startswith("p/"), link
            href = urljoin(page_url, link["href"])
            assert httpx.get(href, timeout=10).status_code == 200, link
            if link["label"] == "activity":
                stream = urljoin(href, "activity/stream")
                with httpx.stream("GET", stream, timeout=10) as events:
                    assert events.status_code == 200, stream
                    assert events.headers["content-type"].startswith(
                        "text/event-stream"
                    )
        dashboard = urljoin(page_url, process["dashboard"])
        eventually(
            lambda dashboard=dashboard: f"  {dashboard}\n" in log.read_text(),
            timeout=10,
            interval=POLL_SECONDS,
        )

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

    # The attempt was given the seven values of `:M4`, and the screwing resource's dashboard and
    # log line show them.
    attempts = urljoin(
        page_url, f"p/{SCREWING_RESOURCE.fragment}/dashboard/box/attempts"
    )
    eventually(
        lambda: _type_values_of(attempts, SCREW) == [_seeded_values(before)],
        timeout=10,
        interval=POLL_SECONDS,
    )
    eventually(
        lambda: _given_line(log.read_text(), SCREW, f"MaxDynamicTorque {before:g}"),
        timeout=10,
        interval=POLL_SECONDS,
    )

    # The decision carries its provenance: the snapshot names the detector's classification, with
    # its agent and its end time, in the shape the store enforces.
    (decided,) = provenance(db)
    assert decided["agent"] == str(DETECTOR), decided
    assert decided["generated"] == decided["snapshot"], decided
    assert decided["ended"], decided

    # The planner's box shows the outcome. The planner learns of the end from the middleware's
    # notification, which follows the commit that ended the operation, so the box may lag.
    box = urljoin(page_url, f"p/{JMS.Planner1.fragment}/dashboard/box/planner")
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

    # Screw2 replays `missing_screw`: MissingScrew, a failure outcome, which ends its operation
    # failed and names it. Its snapshot holds the learner's value.
    press(planner_address, SCREW_2)
    eventually(
        lambda: len([o for o in operations(db) if o["status"] == "failed"]) == 1,
        timeout=OPERATION_WITHIN_SECONDS,
        interval=POLL_SECONDS,
    )
    (second,) = [o for o in operations(db) if o["status"] == "failed"]
    assert second["outcome"].endswith("MissingScrew"), second
    assert second["result"] == "ANOMALY: MissingScrew", second
    assert second["torque"] == after, second
    assert len(provenance(db)) == 2

    # The planner is notified of the failed end as of the done one, and its box shows the outcome
    # and the reason of the failed press.
    eventually(
        lambda: 'title="ANOMALY: MissingScrew"' in httpx.get(box, timeout=5).text,
        timeout=10,
        interval=POLL_SECONDS,
    )
    assert "<td>MissingScrew</td>" in httpx.get(box, timeout=5).text

    # The next attempt on a Screw of the type is given the refined value (Screw1 has its operation
    # already, so Screw2 is that attempt).
    assert after != before
    eventually(
        lambda: _type_values_of(attempts, SCREW_2) == [_seeded_values(after)],
        timeout=10,
        interval=POLL_SECONDS,
    )
    eventually(
        lambda: _given_line(log.read_text(), SCREW_2, f"MaxDynamicTorque {after:g}"),
        timeout=10,
        interval=POLL_SECONDS,
    )

    # The page lists both operations, each with the anomaly detector as its deciding service, linked to
    # the detector's dashboard; and M4's value at the start and the one the learner committed.
    # Its background read runs every second, so the lists may lag.
    detector_dashboard = f"p/{DETECTOR.fragment}/dashboard"
    eventually(
        lambda: (
            [r["outcome"] for r in _lists(page_url)["operations"]["rows"]]
            == ["ScrewIntact", "MissingScrew"]
        ),
        timeout=10,
        interval=POLL_SECONDS,
    )
    shown = _lists(page_url)
    for row in shown["operations"]["rows"]:
        assert row["decided_by"] == str(DETECTOR), row
        assert row["decided_by_name"] == "Anomaly detector", row
        assert row["decided_by_dashboard"] == detector_dashboard, row
        assert row["issuer_name"] == "Planner", row
    assert [(r["screw"], r["status"]) for r in shown["operations"]["rows"]] == [
        (SCREW.fragment, "done"),
        (SCREW_2.fragment, "failed"),
    ]
    assert shown["operations"]["rows"][1]["reason"] == "ANOMALY: MissingScrew"
    (m4,) = shown["learned_values"]["rows"]
    assert m4["screw_type"] == "M4"
    assert float(m4["at_start"]) == before
    assert float(m4["now"]) == after
    # Each query the page shows, run on the dataset as it is shown, gives the rows the page shows
    # (Learned values: the value at the latest read; the one at the start is the page's memory).
    assert _operation_rows(db, shown["operations"]["query"]) == [
        _shown_operation(row) for row in shown["operations"]["rows"]
    ]
    assert _learned_rows(db, shown["learned_values"]["query"]) == [
        (row["screw_type"], row["now"]) for row in shown["learned_values"]["rows"]
    ]

    # With the default, off, the run saves no measurement as an uncertainty quantification. On,
    # it saves two: Screw1's head position at contact, and Screw2's end of its approach.
    assert random_variables(db) == (2 if uc1_saves_uq else 0)

    # Ctrl+C stops everything, and every service deregisters.
    os.killpg(run.pid, signal.SIGINT)
    assert run.wait(timeout=60) == 0
    assert live_resources(db) == set()
