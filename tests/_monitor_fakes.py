"""The fakes the monitor's tests run on, in place of the graph and the participants' streams.

The reads are a fake with the cross topology, one box on module 1 whose transport is running, and
one box the WarehouseManagementSystem holds, whose transport is done. Every read counts, and each
read keeps the query it would have sent, as the real reads do. The handover event streams are
fakes too.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime

from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.handover_events import HandoverEvents
from jms_kapps.uc2.monitor.reads import BoxRow, ConnectionRow, ModuleRow, SentQuery
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import (
    FCI_NS,
    WMS,
    box_iri,
    module_iri,
    transport_iri,
)

M1, M2, M3 = module_iri(1), module_iri(2), module_iri(3)
STREAM_1 = "http://127.0.0.1:1/handover-events"
STREAM_WMS = "http://127.0.0.1:9/handover-events"


def cross() -> list[ModuleRow]:
    """The cross topology as the graph holds it: full IRIs, labels, and each module's class."""
    return [
        ModuleRow(
            resource=module.resource,
            label=module.label,
            # Each shipped module has one type.
            type=module.types[0],
            connections=tuple(
                ConnectionRow(c.direction, IRI(c.to, base=FCI_NS), c.port)
                for c in module.connections
            ),
        )
        for module in load_topology("cross").modules
    ]


class FakeReads:
    """The graph's answers, counting every read. Each read keeps a query of its own name as the
    query it sent, `SELECT ... # <read>`, with as many rows as it answers."""

    def __init__(self) -> None:
        self.rows: list[ModuleRow] = cross()
        self.live: set[IRI] = {module_iri(1), module_iri(3), WMS}
        self.located: dict[IRI, str] = {module_iri(1): STREAM_1, WMS: STREAM_WMS}
        self.warehouses: set[IRI] = {WMS}
        self.reads = 0
        # The name of each read, as it was made, in order.
        self.made: list[str] = []
        self._sent: dict[str, SentQuery] = {}

    def _keep(self, read: str, rows: int) -> None:
        """Count a read named `read` that answered `rows` rows, and keep the query it sent."""
        self.reads += 1
        self.made.append(read)
        self._sent[read] = SentQuery(
            read=read,
            text=f"SELECT ... # {read}",
            sent=datetime.now(UTC).isoformat(),
            duration=0.001,
            rows=rows,
            error=None,
        )

    def modules(self) -> list[ModuleRow]:
        self._keep("modules", len(self.rows))
        return self.rows

    def live_resources(self) -> set[IRI]:
        self._keep("live_resources", len(self.live))
        return self.live

    def boxes(self) -> list[BoxRow]:
        rows = [
            BoxRow(box_iri(2), OperationStatus.DONE, module_iri(1), module_iri(4), WMS),
            BoxRow(
                box_iri(1),
                OperationStatus.RUNNING,
                module_iri(1),
                module_iri(5),
                module_iri(1),
            ),
        ]
        self._keep("boxes", len(rows))
        return rows

    def wms(self) -> set[IRI]:
        self._keep("wms", len(self.warehouses))
        return self.warehouses

    def streams(self) -> dict[IRI, str]:
        self._keep("streams", len(self.located))
        return self.located

    def sent(self) -> list[SentQuery]:
        """What each read sent last. Not a read: it reads only what the reads kept."""
        return list(self._sent.values())


class FakeStreams:
    """The participants' streams by URL: each read delivers what `events` keeps after the last
    id, as the route does, and ends."""

    def __init__(self) -> None:
        self.events: dict[str, HandoverEvents] = {
            STREAM_1: HandoverEvents(M1, token="m1"),
            STREAM_WMS: HandoverEvents(WMS, token="wms"),
        }
        self.reads: list[tuple[str, str | None]] = []
        self.down: set[str] = set()
        # An error of any kind the next read raises, as a broken stream might.
        self.breaks_with: Exception | None = None

    def __call__(
        self, url: str, last_event_id: str | None
    ) -> Iterator[tuple[str, str]]:
        self.reads.append((url, last_event_id))
        if url in self.down:
            raise ConnectionError(f"{url} is down")
        if self.breaks_with is not None:
            error, self.breaks_with = self.breaks_with, None
            raise error
        for event in self.events[url].after(last_event_id):
            yield event.id, event.model_dump_json()
        time.sleep(0.01)


def refuse(events: HandoverEvents, n: int = 1, *, report: str = "the report") -> None:
    """`n` refusals of Box 1's handover to Module 2 on `events`, each with `report`."""
    for _ in range(n):
        events.commit_refused(
            box=box_iri(1),
            peer=M2,
            transport=transport_iri(box_iri(1)),
            reason="the triple store refused the handover",
            report=report,
        )
