"""The boxes on the FlexConveyor station, as the Launcher page lists them: from one SPARQL query,
whose text the page shows, so that a person can run it on the same dataset and get the same
list.

A box is on the station while its possessor is a module: from the spawn that creates it on its
origin until the handover that gives it back to the WarehouseManagementSystem, which possesses it
at the end of its journey. So the list holds every box whose possessor is not the
WarehouseManagementSystem, each with the module that possesses it, and with its transport and that
transport's status, which come with the same query at little cost. The query (`BOXES_ON_STATION`)
is standard SPARQL 1.1: it declares its own prefixes, and names no graph and calls no function
that only one store knows, such as one store's own graphs of the explicit or the inferred
statements, so it runs unchanged in the store's workbench and in any other SPARQL endpoint on the
dataset. A WMS
is whatever the graph types as one, as the monitor finds it. Any other possessor is listed: by the
glossary it is a module, and the query does not ask for a module's class, which only a store that
infers would find for a module typed by its role alone.

**The query runs in the Launcher's background read, never on a page poll.** `StationBoxes` sends
it once at its start and then every second, while the Launcher serves its page, whether a page is
open or not, and keeps what it answered; `Launcher.state()` renders that, so a page that polls
adds no store request. It keeps the text of the query exactly as it handed it to the store, and
the page shows that text. A read that fails keeps the boxes the last one read, and says why.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from kapps_semantic_middleware.vocabulary import CFC, CORE_NS, SVC, SVC_NS
from kapps_triplestore_interface import IRI
from pydantic import BaseModel

from jms_kapps.common.supervision import WATCH_INTERVAL_SECONDS
from jms_kapps.common.triplestore import Queryable, bindings
from jms_kapps.uc2.vocabulary import FC, FC_NS, local_name

# The prefixes the query declares for itself, so that it needs none of the dataset's.
_PREFIXES = {"fc": FC_NS, "cfc": CORE_NS, "svc": SVC_NS}


def _term(iri: IRI) -> str:
    """`iri` as the query writes it: by one of its own prefixes."""
    for prefix, namespace in _PREFIXES.items():
        if str(iri).startswith(namespace):
            return f"{prefix}:{str(iri).removeprefix(namespace)}"
    raise ValueError(f"the query declares no prefix for {iri}")


# The one query of the list, exactly as the Launcher sends it.
BOXES_ON_STATION = (
    "".join(
        f"PREFIX {prefix}: <{namespace}>\n" for prefix, namespace in _PREFIXES.items()
    )
    + f"""
# The boxes on the FlexConveyor station: each box whose possessor is not the
# WarehouseManagementSystem, with the module that possesses it, and the box's
# transport and that transport's status, where it has them.
SELECT ?box ?module ?transport ?status WHERE {{
  ?box a {_term(FC.Box)} ;
       {_term(CFC.hasPossessedWorkpiece)} ?possession .
  ?module {_term(CFC.hasPossessor)} ?possession .
  FILTER NOT EXISTS {{ ?module a {_term(FC.WarehouseManagementSystem)} }}
  OPTIONAL {{
    ?box {_term(FC.hasTransport)} ?transport .
    OPTIONAL {{ ?transport {_term(SVC.hasOperationStatus)} ?status }}
  }}
}}
ORDER BY ?box
"""
)


class BoxOnStation(BaseModel):
    """One row of the list: a box, the module that possesses it, and the box's transport and its
    status, each None where the box has none. Each IRI by its local name, as a person reads it."""

    box: str
    module: str
    transport: str | None
    status: str | None


class BoxesOnStation(BaseModel):
    """What the page shows of the list.

    `query` is the text of the latest read, exactly as it was handed to the store, and None before
    the first. `boxes` are the rows of the latest read that succeeded, in the query's order, and
    `read_at` is when that read was sent (UTC, ISO 8601), None before one has succeeded. `error`
    says why the latest read failed, and is None when it succeeded.
    """

    query: str | None
    boxes: list[BoxOnStation]
    read_at: str | None
    error: str | None


def _row(row: dict) -> BoxOnStation:
    """One row of the query's answer, as SPARQL's JSON results write it."""

    def value(name: str) -> str | None:
        return str(row[name]["value"]) if name in row else None

    def named(name: str) -> str | None:
        found = value(name)
        return None if found is None else local_name(found)

    return BoxOnStation(
        box=local_name(str(row["box"]["value"])),
        module=local_name(str(row["module"]["value"])),
        transport=named("transport"),
        status=value("status"),
    )


class StationBoxes:
    """The list of the boxes on the station, read from `store` in the background.

    Args:
        store: The store of the run's dataset: anything that answers a SPARQL query as a store
            does.
        interval: How long the background read waits between two reads, in seconds: as long as
            the supervisor's watch waits between two reads of the services.
    """

    def __init__(
        self, store: Queryable, *, interval: float = WATCH_INTERVAL_SECONDS
    ) -> None:
        self._store = store
        self._interval = interval
        self._lock = threading.Lock()
        self._last = BoxesOnStation(query=None, boxes=[], read_at=None, error=None)
        # Set while no background read runs: at a stop, and before the first start.
        self._stopped: threading.Event | None = None

    def last(self) -> BoxesOnStation:
        """What the latest reads found. Sends nothing."""
        with self._lock:
            return self._last

    def read(self) -> None:
        """Send the query once, and keep what it answered, or why it failed: a read that fails
        keeps the boxes the last one read. It raises nothing, so that the background read goes
        on whatever the store does."""
        query = BOXES_ON_STATION
        sent_at = datetime.now(UTC).isoformat()
        try:
            boxes = [_row(row) for row in bindings(self._store, query, convert=False)]
        # Whatever the store raises: it is shown on the page, and read again.
        except Exception as error:  # noqa: BLE001
            why = str(error) or type(error).__name__
            with self._lock:
                self._last = self._last.model_copy(
                    update={"query": query, "error": why}
                )
            return
        with self._lock:
            self._last = BoxesOnStation(
                query=query, boxes=boxes, read_at=sent_at, error=None
            )

    def start(self) -> None:
        """Read at once, and then every interval, on a daemon thread, until `stop`. A start
        while the reads run changes nothing."""
        with self._lock:
            if self._stopped is not None and not self._stopped.is_set():
                return
            stopped = self._stopped = threading.Event()

        def read_until_stopped() -> None:
            while not stopped.is_set():
                self.read()
                stopped.wait(self._interval)

        threading.Thread(
            target=read_until_stopped, name="launcher-boxes", daemon=True
        ).start()

    def stop(self) -> None:
        """Stop the background read: no read starts after this. One under way finishes, and
        keeps what it read."""
        with self._lock:
            if self._stopped is not None:
                self._stopped.set()
