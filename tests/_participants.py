"""The parties of use case 2 as middleware instances, served in this process, on a live GraphDB.

`Participants` seeds a topology, then builds the WarehouseManagementSystem and every module with
the same `build` their processes use, and serves each on a port of its own, on a thread of its
own. So every call between them goes over REST, every notification through the middleware, and
every write through the middleware into the store, as in a run of `jms-uc2`.
Only the log differs: their records reach this process's logging, where a test reads them.

Every move takes no time unless a test sets a transit time, and a test may give a module a move
of its own. A module asks an occupied or reserved module again after `RETRY_SECONDS`.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
import uvicorn
from kapps_ogm import OGM
from kapps_semantic_middleware import SemanticMiddleware
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.process import bind_socket, server_config
from jms_kapps.common.triplestore import connect
from jms_kapps.uc2 import process, wms_process
from jms_kapps.uc2.discovery import workflow_endpoint
from jms_kapps.uc2.handshake import Move
from jms_kapps.uc2.seed import seed_topology
from jms_kapps.uc2.topology import Connection, Module, Topology
from jms_kapps.uc2.vocabulary import FC, WMS, module_iri
from jms_kapps.uc2.wms import Choose

HOST = "127.0.0.1"
STARTED_WITHIN_SECONDS = 60.0
RETRY_SECONDS = 0.1

# Two modules, one hop: the smallest FlexConveyor station a box can cross. Module 1 is the only
# entry module, Module 2 the only exit module.
TWO_MODULES = Topology(
    modules=[
        Module(
            id="Module1",
            label="Module 1",
            types=["EntryModule"],
            connections=[Connection(direction="East", to="Module2")],
        ),
        Module(id="Module2", label="Module 2", types=["ExitModule"]),
    ]
)

M1, M2 = module_iri(1), module_iri(2)


@dataclass
class Served:
    """One party's middleware, and the server that serves it on its thread."""

    middleware: SemanticMiddleware
    server: uvicorn.Server
    thread: threading.Thread


@dataclass
class Participants:
    """The served parties, by resource, and a client of the store they write to."""

    db: TripleStore
    served: dict[IRI, Served] = field(default_factory=dict)

    def spawn(self, **request: str) -> dict:
        """Ask the WarehouseManagementSystem's `:SpawnBoxWorkflow`, found in the graph."""
        endpoint = workflow_endpoint(self.db, WMS, FC.SpawnBoxWorkflow)
        assert endpoint is not None, "the WMS has no spawn workflow in the graph"
        response = httpx.post(endpoint, json=request, timeout=60)
        response.raise_for_status()
        return response.json()


def _serve(middleware: SemanticMiddleware, sock) -> Served:
    server = uvicorn.Server(server_config(middleware.app))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    return Served(middleware, server, thread)


@contextmanager
def participants(
    dataset: str,
    topology: Topology = TWO_MODULES,
    *,
    moves: dict[IRI, Move] | None = None,
    transit_seconds: float = 0.0,
    before_start=None,
    elsewhere: Collection[IRI] = (),
    choose: Choose = random.choice,
) -> Iterator[Participants]:
    """Seed `topology` into `dataset`, which it clears, and serve the WarehouseManagementSystem
    and every module until the block ends.

    Args:
        elsewhere: The modules that the test runs itself, each as a process of its own, which
            the block does not serve: a module whose process the test ends, as a crash does.
        moves: A module's move, by module, in place of the simulated one.
        transit_seconds: The transit time that the seed gives each module, and so how long each
            simulated move takes.
        before_start: Called with the store's client after the seed and before any party
            starts: the setup, the only writes that may bypass the middleware.
        choose: How the WarehouseManagementSystem picks a module out of several: at random,
            as its process picks, unless a test says otherwise.
    """
    db = connect(dataset)
    seed_topology(db, topology, transit_seconds=0)
    if before_start is not None:
        before_start(db)
    party = Participants(db)
    try:
        for module in topology.modules:
            if module.resource in elsewhere:
                continue
            sock = bind_socket(HOST)
            middleware = process.build(
                str(module.resource),
                OGM(db=connect(dataset)),
                HOST,
                sock.getsockname()[1],
                retry_seconds=RETRY_SECONDS,
                move=(moves or {}).get(module.resource),
            )
            party.served[module.resource] = _serve(middleware, sock)
        sock = bind_socket(HOST)
        middleware = wms_process.build(
            OGM(db=connect(dataset)), HOST, sock.getsockname()[1], choose=choose
        )
        party.served[WMS] = _serve(middleware, sock)
        deadline = time.monotonic() + STARTED_WITHIN_SECONDS
        while not all(s.server.started for s in party.served.values()):
            assert time.monotonic() < deadline, "the parties did not start in time"
            time.sleep(0.05)
        yield party
    finally:
        for served in party.served.values():
            served.server.should_exit = True
        for served in party.served.values():
            served.thread.join(timeout=30)
