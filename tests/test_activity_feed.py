"""A process's own log on its activity page: what the shared process start adds to the feed.

The middleware's activity feed listens to the middleware's own logger only. The shared process start
feeds this package's records into it too, from INFO up, so that a module's or the WMS's activity
page shows that process's steps next to the middleware's own entries.

The first test runs `run_process` in a Python process of its own, because the process start sets
up the logging of its whole process. It needs no triple store: the child's middleware is a FastAPI
app with the middleware's activity feed on it, and its one route logs what the test asks for. The
second runs UC1's learner, whose middleware has an activity feed, as its own process on the
GraphDB, against the dataset `--dataset` names, which it clears.
"""

from __future__ import annotations

import json
import re
import signal
import time

import httpx
import pytest
from _script import WITHIN_SECONDS, Script
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present

from jms_kapps.common.services import live_resources
from jms_kapps.common.supervision import STOP_TIMEOUT_SECONDS
from jms_kapps.common.triplestore import connect, read_asset, reset

# A process started as every process of use case 2 is, by `run_process`, with the middleware's
# activity feed on its app. `POST /log` logs one record of a module's logger at each level, and
# one of the middleware's own logger.
CHILD = """
import logging
from types import SimpleNamespace

from fastapi import FastAPI
from kapps_semantic_middleware.activity import enable_activity_feed

from jms_kapps.common.process import run_process

middleware = SimpleNamespace(app=FastAPI())
enable_activity_feed(middleware)


@middleware.app.post("/log")
def log() -> None:
    module = logging.getLogger("jms_kapps.uc2.module")
    module.debug("Module1: a record below INFO")
    module.info("Module1: ReserveWorkflow accepted Box1 from Module2")
    module.warning("Module1: the triple store refused handing Box1 to Module3")
    logging.getLogger("kapps_semantic_middleware.middleware").info("the middleware's own entry")


run_process(
    "127.0.0.1",
    lambda port: middleware,
    lambda address: "port " + address.rstrip("/").rsplit(":", 1)[1],
)
"""


def feed_of(port: int) -> list[dict]:
    """Every record the activity page at `port` is handed when it opens: a stream that starts
    with everything buffered, and ends by itself."""
    with httpx.stream(
        "GET", f"http://127.0.0.1:{port}/activity/stream", timeout=10
    ) as stream:
        return [
            json.loads(line.removeprefix("data: "))
            for line in stream.iter_lines()
            if line.startswith("data: ")
        ]


@pytest.mark.posix(reason="SIGINT cannot be sent to a process on Windows")
def test_the_activity_page_shows_the_process_s_own_records_from_info_up_beside_the_middleware_s():
    child = Script(CHILD)
    try:
        port = child.port()
        before = time.time()
        httpx.post(f"http://127.0.0.1:{port}/log", timeout=WITHIN_SECONDS)

        shown = [
            (r["level"], r["logger"], r["message"])
            for r in feed_of(port)
            if r["timestamp"] >= before
        ]

        # Each once, in the order they were logged, and nothing below INFO.
        assert shown == [
            (
                "INFO",
                "jms_kapps.uc2.module",
                "Module1: ReserveWorkflow accepted Box1 from Module2",
            ),
            (
                "WARNING",
                "jms_kapps.uc2.module",
                "Module1: the triple store refused handing Box1 to Module3",
            ),
            (
                "INFO",
                "kapps_semantic_middleware.middleware",
                "the middleware's own entry",
            ),
        ]
        child.process.send_signal(signal.SIGINT)
        assert child.wait(timeout=STOP_TIMEOUT_SECONDS) == 0
        assert not child.printed("Traceback"), "\n".join(child.lines)
    finally:
        child.end()


# UC1's learner as its own process, as its console script starts it, with no run due while the
# test runs.
LEARNER = """
import runpy

runpy.run_module("jms_kapps.uc1.learner_process", run_name="__main__")
"""
_LEARNER_ADDRESS = re.compile(r"The learner runs on (http://\S+/),")


@pytest.mark.posix(reason="SIGINT cannot be sent to a process on Windows")
@pytest.mark.graphdb
@pytest.mark.skipif(not triplestore_env_present(), reason="no triple store connection variables")
def test_uc1_s_learner_starts_and_serves_its_dashboard_and_its_activity_page(
    dataset,
):
    db = connect(dataset)
    reset(
        db,
        ontology=read_asset("jms_kapps.uc1", "ontology", "integration_usecase.ttl"),
        shapes="",
    )
    learner = Script(LEARNER, "--dataset", dataset, "--learn-seconds", "3600")
    try:
        eventually(
            lambda: learner.printed("The learner runs on "), timeout=WITHIN_SECONDS
        )
        (address,) = [
            m.group(1) for line in learner.lines if (m := _LEARNER_ADDRESS.match(line))
        ]

        assert httpx.get(f"{address}dashboard", timeout=10).status_code == 200
        assert httpx.get(f"{address}activity", timeout=10).status_code == 200

        learner.process.send_signal(signal.SIGINT)
        assert learner.wait(timeout=STOP_TIMEOUT_SECONDS) == 0
        assert not learner.printed("Traceback"), "\n".join(learner.lines)
        assert live_resources(db) == set()
    finally:
        learner.end()
