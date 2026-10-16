"""A stand-in of the store for the Launcher's list of the boxes on the station.

It answers every query with the same rows, as a SPARQL endpoint writes them in its JSON results,
and keeps each query text exactly as it received it, so a test can tell what the Launcher sent,
and how often. It can be made unreachable (`away`), so that every query raises. Nothing reaches a
real store.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Collection
from typing import Any
from uuid import UUID

from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.backend import Launcher
from jms_kapps.uc2.launcher.boxes import StationBoxes
from jms_kapps.uc2.topology import Topology
from jms_kapps.uc2.vocabulary import FCI_NS, box_iri, module_iri, uuid_box_iri

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]

# Long enough that no test waits for a second read: the read at the app's start is the only one.
ONCE = 3600.0

# Box 1 on Module 1, its transport running; a box the WMS spawned, named by a UUID as it names
# every box it spawns (jms_kapps 92), on Module 3, its transport queued. What the query on a
# seeded station answers: no box in the WMS's possession. (Changed on 2026-10-12, jms_kapps 92.
# Until then the second box was Box 3.)
SPAWNED = uuid_box_iri(UUID("5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10"))
ROWS: list[dict[str, Any]] = [
    {
        "box": {"type": "uri", "value": str(box_iri(1))},
        "module": {"type": "uri", "value": str(module_iri(1))},
        "transport": {"type": "uri", "value": FCI_NS + "Transport7"},
        "status": {"type": "literal", "value": OperationStatus.RUNNING},
    },
    {
        "box": {"type": "uri", "value": str(SPAWNED)},
        "module": {"type": "uri", "value": str(module_iri(3))},
        "transport": {"type": "uri", "value": FCI_NS + "Transport9"},
        "status": {"type": "literal", "value": OperationStatus.QUEUED},
    },
]

# The two rows as the Launcher's state lists them, each by its local name, as a person reads it.
LISTED = [
    {
        "box": "Box1",
        "module": "Module1",
        "transport": "Transport7",
        "status": "running",
    },
    {
        "box": "Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10",
        "module": "Module3",
        "transport": "Transport9",
        "status": "queued",
    },
]


class StandInStore:
    """Answers each query with `rows`, and keeps each query text it received."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = ROWS if rows is None else rows
        self.away: str | None = None
        self._received: list[str] = []
        self._lock = threading.Lock()

    @property
    def received(self) -> list[str]:
        """Every query text received, in the order it came."""
        with self._lock:
            return list(self._received)

    def query(self, query: str, *, convert_bindings: bool = False) -> Any:
        with self._lock:
            self._received.append(query)
        if self.away is not None:
            raise ConnectionError(self.away)
        assert not convert_bindings, "the stand-in answers SPARQL's JSON results only"
        return {
            "head": {"vars": ["box", "module", "transport", "status"]},
            "results": {"bindings": self.rows},
        }


class NoServices:
    """A graph with no live service, that seeds nothing: the Launcher launches nothing here."""

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        pass

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return []


def listing(store: StandInStore, interval: float = ONCE) -> Launcher:
    """A Launcher that has launched nothing, whose list of the boxes on the station reads `store`
    every `interval` seconds while its app serves."""
    return Launcher(
        graph=NoServices(),
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        echo=lambda line: None,
        boxes=StationBoxes(store, interval=interval),
    )
