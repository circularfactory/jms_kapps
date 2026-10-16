"""Every query the monitor sends. Each one reads; none writes.

The monitor does not choose what to show by reading the ontology: the classes and properties it
reads are the use case's own, named here, and no `rdfs:subClassOf` walk decides what is a module.
A module is whatever the graph types as one of the three module classes.

Every IRI stays whole. A module, a box or a possessor is compared by its IRI as the graph holds it,
never by a local name turned back into one; a local name is made only where a person reads it.

Each read keeps the query it sent last, exactly as the store received it, with when it went, how
long the store took to answer and how many rows came back (`GraphReads.sent`): the monitor's page
shows them as "Queries sent". (Added on 2026-10-08, jms_kapps 24, decided by Etienne on
2026-10-05: "we should at least then be able to expose the sparql queries that are sent".)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kapps_semantic_middleware.vocabulary import CFC, SVC
from kapps_triplestore_interface import IRI, TripleStore
from rdflib.namespace import RDFS

from jms_kapps.common.services import live_resources
from jms_kapps.common.triplestore import bindings, result_rows
from jms_kapps.uc2.topology import Direction, ModuleType
from jms_kapps.uc2.vocabulary import FC

# The three module classes, the most specific first: a module typed as one of the first two is
# shown as that, whatever else the store infers for it.
_MODULE_TYPES: dict[IRI, ModuleType] = {
    FC.EntryModule: "EntryModule",
    FC.ExitModule: "ExitModule",
    FC.FlexConveyorModule: "FlexConveyorModule",
}

# The read that finds the participants' streams: the one read that "graph only" does not make.
STREAMS_READ = "streams"

_DIRECTIONS: dict[IRI, Direction] = {
    FC.North: "North",
    FC.East: "East",
    FC.South: "South",
    FC.West: "West",
}


@dataclass(frozen=True)
class ConnectionRow:
    """One connection of a module, toward `to`, whatever `to` is."""

    direction: Direction
    to: IRI
    port: int


@dataclass(frozen=True)
class ModuleRow:
    """One module, as the graph holds it. `label` is None when the graph gives it none."""

    resource: IRI
    label: str | None
    type: ModuleType
    connections: tuple[ConnectionRow, ...]


@dataclass(frozen=True)
class BoxRow:
    """One box, as the graph holds it. `holder` is whoever possesses it, if anyone does.

    A box has no state of its own. `status` is its transport's `svc:operationStatus`, and `origin`
    and `destination` are its transport's, if it has a transport.
    """

    box: IRI
    status: str | None
    origin: IRI | None
    destination: IRI | None
    holder: IRI | None


@dataclass(frozen=True)
class SentQuery:
    """A SPARQL query the monitor sent, the last time it sent it.

    `read` is the read of the monitor that sent it: the name of its method in `GraphReads`.
    `text` is the query exactly as the store received it. `sent` is when (UTC, ISO 8601),
    `duration` how many seconds the store took to answer, and `rows` how many rows it answered;
    `rows` is None and `error` says why when the query failed.
    """

    read: str
    text: str
    sent: str
    duration: float
    rows: int | None
    error: str | None


def _optional(row: dict, key: str) -> IRI | None:
    return IRI(str(row[key])) if key in row else None


class _Sender:
    """The client as one read of the monitor sees it: it sends each query on to `db`, and hands
    `keep` what it sent under the read's name, whether the store answers or fails. A read hands
    it to `bindings` and `live_resources` in place of the client, so that what is kept is exactly
    the text they send."""

    def __init__(
        self, db: TripleStore, read: str, keep: Callable[[SentQuery], None]
    ) -> None:
        self._db = db
        self._read = read
        self._keep = keep

    def query(self, query: str, *, convert_bindings: bool = True) -> Any:
        sent = datetime.now(UTC).isoformat()
        started = time.monotonic()
        try:
            result = self._db.query(query, convert_bindings=convert_bindings)
        except Exception as error:
            why = str(error) or type(error).__name__
            self._kept(query, sent, started, rows=None, error=why)
            raise
        self._kept(query, sent, started, rows=len(result_rows(result)), error=None)
        return result

    def _kept(
        self,
        text: str,
        sent: str,
        started: float,
        *,
        rows: int | None,
        error: str | None,
    ) -> None:
        duration = time.monotonic() - started
        self._keep(SentQuery(self._read, text, sent, duration, rows, error))


class GraphReads:
    """The monitor's reads, bound to one client, and the query each of them sent last."""

    def __init__(self, db: TripleStore) -> None:
        self.db = db
        self._lock = threading.Lock()
        # By read, in the order the reads were first made.
        self._sent: dict[str, SentQuery] = {}

    def sent(self) -> list[SentQuery]:
        """The query each read sent last, in the order the reads were first made. Sends nothing."""
        with self._lock:
            return list(self._sent.values())

    def _keep(self, query: SentQuery) -> None:
        with self._lock:
            self._sent[query.read] = query

    def _sender(self, read: str) -> _Sender:
        """The client as the read `read` sends through it, keeping what it sends here."""
        return _Sender(self.db, read, self._keep)

    def modules(self) -> list[ModuleRow]:
        """The modules and their connections the graph holds now, as far as it holds them.

        A connection without a direction the use case names is left out, and so is nothing
        else: a connection may lead to anything.
        """
        types = " ".join(f"<{iri}>" for iri in _MODULE_TYPES)
        rows = bindings(
            self._sender("modules"),
            f"""
            SELECT ?module ?class ?label ?direction ?to ?port WHERE {{
                VALUES ?class {{ {types} }}
                ?module a ?class .
                OPTIONAL {{ ?module <{RDFS.label}> ?label }}
                OPTIONAL {{
                    ?module <{FC.hasConnection}> ?connection .
                    ?connection <{FC.connectsTo}> ?to ; <{FC.hasDirection}> ?direction .
                    OPTIONAL {{ ?connection <{FC.onPort}> ?port }}
                }}
            }} ORDER BY ?module
            """,
        )
        classes: dict[IRI, set[IRI]] = {}
        labels: dict[IRI, str] = {}
        connections: dict[IRI, dict[Direction, ConnectionRow]] = {}
        for row in rows:
            module = IRI(str(row["module"]))
            classes.setdefault(module, set()).add(IRI(str(row["class"])))
            connections.setdefault(module, {})
            if "label" in row:
                labels.setdefault(module, str(row["label"]))
            direction = (
                _DIRECTIONS.get(IRI(str(row["direction"])))
                if "direction" in row
                else None
            )
            if direction is not None:
                connections[module][direction] = ConnectionRow(
                    direction, IRI(str(row["to"])), int(row.get("port", 0))
                )
        return [
            ModuleRow(
                resource=module,
                label=labels.get(module),
                type=next(t for iri, t in _MODULE_TYPES.items() if iri in found),
                connections=tuple(connections[module].values()),
            )
            for module, found in classes.items()
        ]

    def live_resources(self) -> set[IRI]:
        """The participants whose service has an address and a fresh heartbeat."""
        return live_resources(self._sender("live_resources"))

    def boxes(self) -> list[BoxRow]:
        """Every box, with its transport's status and journey, and who possesses it."""
        rows = bindings(
            self._sender("boxes"),
            f"""
            SELECT ?box ?status ?origin ?destination ?holder WHERE {{
                ?box a <{FC.Box}> .
                OPTIONAL {{
                    ?transport a <{FC.BoxTransportOperation}> ; <{FC.hasBox}> ?box .
                    OPTIONAL {{ ?transport <{SVC.operationStatus}> ?status }}
                    OPTIONAL {{ ?transport <{FC.hasOrigin}> ?origin }}
                    OPTIONAL {{ ?transport <{FC.hasDestination}> ?destination }}
                }}
                OPTIONAL {{
                    ?box <{CFC.hasPossessedWorkpiece}> ?possession .
                    ?holder <{CFC.hasPossessor}> ?possession .
                }}
            }}
            """,
        )
        return [
            BoxRow(
                IRI(str(row["box"])),
                str(row["status"]) if "status" in row else None,
                _optional(row, "origin"),
                _optional(row, "destination"),
                _optional(row, "holder"),
            )
            for row in rows
        ]

    def wms(self) -> set[IRI]:
        """Every WarehouseManagementSystem: whatever the graph types as one."""
        rows = bindings(
            self._sender("wms"),
            f"SELECT ?wms WHERE {{ ?wms a <{FC.WarehouseManagementSystem}> }}",
        )
        return {IRI(str(row["wms"])) for row in rows}

    def streams(self) -> dict[IRI, str]:
        """Where each participant serves its handover event stream, by participant."""
        rows = bindings(
            self._sender(STREAMS_READ),
            f"SELECT ?participant ?stream WHERE {{ "
            f"?participant <{FC.hasHandoverEventStream}> ?stream }}",
        )
        return {IRI(str(row["participant"])): str(row["stream"]) for row in rows}
