"""One UC2 run of one configuration, on its own repository of the configured GraphDB, with its
record: the KAPPS run of the commit time experiment script (`run_on_the_configured_store`).

The run (`bench.run`) makes a folder of its own below `runs`, named by its run id, with
`record.json`, the record; `configuration.toml`, the resolved configuration; and `output/`, each
participant's output and the harness's own log. The run's two heartbeat settings are no keys of
the configuration: the heartbeat interval (None: no heartbeat loop) and how old a live service's
heartbeat may be.

The connection to the configured GraphDB comes from `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD`, as for every use case; the login needs the right to create and delete
repositories and to read the server's monitoring views. The repository never comes from the
environment: each run creates its own, `commit-time-` plus its run id, and deletes it at its end.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jms_kapps.commit_time.bench import (
    GraphDBBenchGraph,
    HTTPSpawnRate,
    run,
)
from jms_kapps.commit_time.configuration import Configuration
from jms_kapps.commit_time.store import GraphDBServer
from jms_kapps.common.heartbeats import (
    DEFAULT_LIVE_HEARTBEAT_AGE_SECONDS,
    MIDDLEWARE_HEARTBEAT_INTERVAL_SECONDS,
)
from jms_kapps.common.triplestore import connect


def graph_for(
    repository: str, prefill: Path | None, with_shapes: bool
) -> GraphDBBenchGraph:
    """The run's graph on `repository` of the configured GraphDB, which loads `prefill`, and
    whose reset sets the shapes only `with_shapes`: the configuration's `shacl`."""
    return GraphDBBenchGraph(
        connect(repository), prefill=prefill, with_shapes=with_shapes
    )


def run_on_the_configured_store(
    configuration: Configuration,
    *,
    runs: Path,
    sweep: Mapping[str, Any] | None = None,
    heartbeat_interval_seconds: float | None = MIDDLEWARE_HEARTBEAT_INTERVAL_SECONDS,
    live_heartbeat_age_seconds: float = DEFAULT_LIVE_HEARTBEAT_AGE_SECONDS,
) -> dict[str, Any]:
    """One run of `configuration` (`bench.run`) on the configured GraphDB, with a client of its
    own, and its record, in a folder of its own below `runs`. `sweep` says which run of the spec
    it is (`spec.sweep_fields`), or is None. The two heartbeat settings are the run's
    (`bench.run`)."""
    # One line for each HTTP request would bury the run's own lines; the record keeps every
    # request the harness sent to the server.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = GraphDBServer(
        os.environ["TRIPLESTORE_URL"],
        username=os.environ["TRIPLESTORE_USERNAME"],
        password=os.environ["TRIPLESTORE_PASSWORD"],
    )
    try:
        return run(
            configuration,
            runs=runs,
            server=server,
            graph_for=graph_for,
            spawn_rate=HTTPSpawnRate(),
            sweep=sweep,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            live_heartbeat_age_seconds=live_heartbeat_age_seconds,
        )
    finally:
        server.close()
