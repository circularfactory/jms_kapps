"""The Launcher's Clear on a live GraphDB: `jms-uc2` runs the cross, Clear through the page's API
stops every process and empties the dataset, graph by graph, to 0 statements, and a launch
after it runs again.

**Only on a disposable dataset.** Clear empties every graph of the dataset, not only what a
launch seeds, such as any graph a person loaded. So this test runs only on a dataset whose name starts with `ogm-disposable-`, created from
`graphdb/jms-kapps-repo-config.ttl` with only its ID changed and deleted afterwards, and refuses
any other with a skip that says why: never `Tests`, which every library's live suite shares, nor
`jms-kapps`, the demo's. It launches with the option
`--collect-history`, so that the store collects history while Clear runs, as it refuses a clear of
the whole dataset in one request then; the collection stays on afterwards, as a launch with the
option leaves it.

It takes the dataset by `--dataset`, as every graphdb test does, needs the GraphDB
connection variables, and the Launcher's port 8090 free. It runs on POSIX: it starts `jms-uc2` in
a session of its own and stops it with Ctrl+C to that session, as a terminal does.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from _disposable import disposable
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import connect, statement_count
from jms_kapps.uc2.discovery import service_addresses
from jms_kapps.uc2.history import history_enabled
from jms_kapps.uc2.launcher.runner import LAUNCHER_PORT

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.posix(
        reason="starts jms-uc2 in a session of its own and signals its process group"
    ),
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

WHY = (
    "Clear empties every graph of the dataset, not only what a launch seeds, and the "
    "launch turns the store's history collection on"
)

PAGE = f"http://127.0.0.1:{LAUNCHER_PORT}"

# A named graph that no run makes, as one a person loaded: Clear empties it too.
BY_HAND = IRI("http://example.org/jms-kapps/clear-test")
BY_HAND_TURTLE = (
    "<http://example.org/jms-kapps/clear-test#note> "
    '<http://www.w3.org/2000/01/rdf-schema#comment> "loaded by hand" .\n'
)

# How long a run's processes may take to turn live: the cross's five modules, the WMS and the
# monitor. Each check reads the page, so the test asks only every half second.
LIVE_WITHIN_SECONDS = 90.0
POLL_SECONDS = 0.5


def page_state() -> dict:
    return httpx.get(f"{PAGE}/api/state", timeout=5).json()


def await_all_live(run: subprocess.Popen, log: Path) -> None:
    """Wait until the cross's seven processes are all live. A `jms-uc2` that has exited never
    gets there, so that fails at once, with what it printed."""

    def all_live() -> bool:
        if run.poll() is not None:
            pytest.fail(f"jms-uc2 exited with {run.returncode}:\n{log.read_text()}")
        states = [p["state"] for p in page_state()["processes"]]
        return len(states) == 7 and set(states) == {"live"}

    eventually(all_live, timeout=LIVE_WITHIN_SECONDS, interval=POLL_SECONDS)


@pytest.fixture
def db(dataset) -> TripleStore:
    return connect(disposable(dataset, WHY))


@pytest.fixture
def jms_uc2(db, dataset, tmp_path) -> Iterator[tuple[subprocess.Popen, Path]]:
    """`jms-uc2` on the cross, collecting history, with a transit time of 0, in a session of its
    own; stopped again at the end, whatever happens."""
    command = shutil.which("jms-uc2")
    assert command is not None, "the jms-uc2 console script is not installed"
    log = tmp_path / "launcher.log"
    with log.open("w") as output:
        run = subprocess.Popen(
            [
                command,
                *("--dataset", dataset),
                *("--transit-time", "0"),
                "--collect-history",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        yield run, log
    finally:
        if run.poll() is None:
            os.killpg(run.pid, signal.SIGTERM)
            run.wait(timeout=30)


def test_clear_empties_every_graph_to_0_statements_and_a_launch_after_it_runs(
    db, dataset, jms_uc2
):
    run, log = jms_uc2
    await_all_live(run, log)
    assert history_enabled(db)
    db.graph_add(BY_HAND_TURTLE, graph_iri=BY_HAND, content_type="application/x-turtle")
    assert statement_count(db) > 0

    cleared = httpx.post(f"{PAGE}/api/clear", timeout=120)

    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"dataset": dataset, "statements": 0}
    # What the store says, read apart from the Launcher: no statement, no named graph, and no
    # service, since every process stopped and deregistered first.
    assert statement_count(db) == 0
    assert db.graphs_get() == []
    assert service_addresses(db) == {}
    state = page_state()
    assert {p["state"] for p in state["processes"]} == {"stopped"}
    assert state["topology"] is None

    relaunched = httpx.post(f"{PAGE}/api/launch", json={"name": "cross"}, timeout=120)

    assert relaunched.status_code == 200, relaunched.text
    await_all_live(run, log)
    assert statement_count(db) > 0

    # Ctrl+C stops everything, and every service deregisters.
    os.killpg(run.pid, signal.SIGINT)
    assert run.wait(timeout=60) == 0
    assert service_addresses(db) == {}
