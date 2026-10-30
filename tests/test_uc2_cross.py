"""Several boxes at once on the 5-module cross, each module choosing its next module from the graph.

The cross names the roles of its modules: Modules 1 and 2 are entry modules and Modules 4 and 5
exit modules, each connected to Module 3 in the middle, both ways, and Module 3 has none. A box
starts on Module 1 or 2 and ends on Module 4 or 5, so it goes over Module 3, two hops, and boxes
that travel at the same time contend for Module 3. The
origin of a spawn looks ahead: it accepts the WMS's Reserve only once Module 3 has reserved itself
for the box, so of two spawns at once, one onto Module 1 and one onto Module 2, one is refused, and
is spawned again once Module 3 is free. The WMS
sends boxes between the four ordered pairs of an entry and an exit module.

The parties are served in this process (`_participants`), with a transit time of 0 and a retry
time of 0.1 s. Every call between them goes over REST, every notification through the middleware,
and every write through the middleware into the store.

These tests clear the dataset they run against. They run only when the store's connection
variables are set, against the dataset `--dataset` names (there is no default).
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from _bulk import bulk_validation_report, conforms
from _participants import Participants, participants
from _wait import eventually
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import Queryable, bindings, connect
from jms_kapps.uc2.discovery import boxes_held_by, workflow_endpoint
from jms_kapps.uc2.handshake import acceptance_record
from jms_kapps.uc2.topology import shipped_topology
from jms_kapps.uc2.transport import operation_status
from jms_kapps.uc2.vocabulary import FC, WMS, module_iri
from jms_kapps.uc2.wms import taken_back_record

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

M1, M2, M3, M4, M5 = (module_iri(n) for n in range(1, 6))
CROSS_TOPOLOGY = shipped_topology("cross")

# Four boxes over two hops each, through one module in the middle, on the shared remote store.
TRANSPORTS_SECONDS = 120.0
# A spawn left to the WMS is refused while both entry modules, Modules 1 and 2, hold a box.
NO_MODULE_FREE = "no entry module is free"
# The spawn rate of the test of the four ordered pairs, and how long the boxes it spawns may take
# to cover the four ordered pairs of an entry and an exit module of the cross, on the shared
# remote store.
BOXES_PER_MINUTE = 30
PAIRS_SECONDS = 420.0


@pytest.fixture
def records(caplog):
    """Every record the parties log, as its message, in the order they logged them."""
    caplog.set_level(logging.INFO, logger="jms_kapps")

    def records() -> list[str]:
        return [
            r.getMessage() for r in caplog.records if r.name.startswith("jms_kapps")
        ]

    return records


def origin_of(party: Participants, transport: IRI) -> IRI:
    rows = bindings(
        party.db, f"SELECT ?origin WHERE {{ <{transport}> <{FC.hasOrigin}> ?origin }}"
    )
    return IRI(str(rows[0]["origin"]))


def passes(spawned: dict) -> bool:
    """Whether a refused spawn may be asked again: no module was free, or the origin refused to
    reserve with a refusal that passes, as when Module 3 is reserved for another box."""
    return (
        spawned["reason"] == NO_MODULE_FREE or "refused to reserve" in spawned["reason"]
    )


def spawn_until_accepted(party: Participants, **request: str) -> dict:
    """Spawn a box as `request` says, asking again while the refusal passes."""
    deadline = time.monotonic() + TRANSPORTS_SECONDS
    while True:
        spawned = party.spawn(**request)
        if spawned["accepted"] or not passes(spawned):
            return spawned
        assert time.monotonic() < deadline, f"no spawn accepted: {spawned}"
        time.sleep(0.1)


def test_boxes_from_modules_1_and_2_reach_modules_4_and_5_over_module_3_and_contend_for_it(
    dataset, records
):
    with participants(dataset, CROSS_TOPOLOGY) as party:
        # The first two at once, one on Module 1 and one on Module 2, toward different modules:
        # Module 3 reserves itself for one box only, so the other spawn is refused.
        routes = [(M1, M4), (M2, M5)]
        with ThreadPoolExecutor(2) as pool:
            at_once = list(
                pool.map(
                    lambda route: party.spawn(
                        origin=str(route[0]), destination=str(route[1])
                    ),
                    routes,
                )
            )
        [refused] = [s for s in at_once if not s["accepted"]]
        [first] = [s for s in at_once if s["accepted"]]
        # Module 3 is reserved for the box of the spawn it accepted, which the WMS named by a
        # UUID.
        assert re.fullmatch(
            r"Module[12] refused to reserve: Module3, its next module, refused to reserve: "
            r"reserved for " + re.escape(IRI(first["box"]).fragment),
            refused["reason"],
        ), (refused, first)
        assert (refused["box"], refused["operation"]) == (None, None)
        again = spawn_until_accepted(
            party, origin=refused["origin"], destination=refused["destination"]
        )
        spawned = sorted(
            [first, again],
            key=lambda s: routes.index((IRI(s["origin"]), IRI(s["destination"]))),
        )
        # Each further box as soon as its origin is free again.
        spawned += [
            spawn_until_accepted(
                party, origin=str(origin), destination=str(destination)
            )
            for origin, destination in ((M1, M5), (M2, M4))
        ]

        assert all(s["accepted"] for s in spawned), spawned
        transports = [(IRI(s["box"]), IRI(s["operation"])) for s in spawned]
        for box, transport in transports:
            taken_back = taken_back_record(WMS, box, transport)
            eventually(
                lambda record=taken_back: record in records(),
                timeout=TRANSPORTS_SECONDS,
            )

        log = records()
        for (box, transport), exit_ in zip(transports, (M4, M5, M5, M4), strict=True):
            assert operation_status(party.db, transport) == OperationStatus.DONE
            origin = origin_of(party, transport)
            assert origin in (M1, M2)
            # Two hops: the entry to Module 3, then Module 3 to the exit.
            assert acceptance_record(M3, FC.ReceiveWorkflow, box, origin) in log
            assert acceptance_record(exit_, FC.ReceiveWorkflow, box, M3) in log
        assert {origin_of(party, t) for _, t in transports} == {M1, M2}
        assert sorted(boxes_held_by(party.db, WMS)) == sorted(b for b, _ in transports)
        report = bulk_validation_report(party.db)
        assert conforms(report), report.serialize()


def transport_ends(db: Queryable) -> list[tuple[IRI, IRI, str]]:
    """Every transport in the graph, as (origin, destination, status): one for each spawn the WMS
    accepted."""
    rows = bindings(
        db,
        f"""
        SELECT ?origin ?destination ?status WHERE {{
            ?transport a <{FC.BoxTransportOperation}> ;
                <{FC.hasOrigin}> ?origin ; <{FC.hasDestination}> ?destination .
            OPTIONAL {{ ?transport <{SVC.hasOperationStatus}> ?status }}
        }}
        """,
    )
    return [
        (IRI(str(r["origin"])), IRI(str(r["destination"])), str(r.get("status")))
        for r in rows
    ]


class FewestTravelled:
    """How the WMS picks a module out of several, for a test that must cover every ordered pair of
    an entry and an exit module of the cross in a few boxes: the origin with the most pairs that no box has travelled
    yet, then the destination fewest boxes have travelled to from it, the lowest IRI first. It
    reads the transports in `db` at each pick, and keeps what it was offered to pick from: each
    origin with the origins and the destinations offered.

    The WMS picks the origin first and then the destination, one spawn at a time, so the calls
    alternate.
    """

    def __init__(self, db: Queryable, exits: list[IRI]) -> None:
        self.db, self.exits = db, exits
        self.offered: list[tuple[list[IRI], IRI, list[IRI]]] = []
        self._origins: list[IRI] = []
        self._origin: IRI | None = None

    def __call__(self, options):
        options = sorted(options, key=str)
        travelled = Counter((o, d) for o, d, _ in transport_ends(self.db))
        if self._origin is None:
            self._origins = options
            self._origin = min(
                options,
                key=lambda o: (
                    -sum(1 for d in self.exits if d != o and not travelled[o, d])
                ),
            )
            return self._origin
        origin, self._origin = self._origin, None
        self.offered.append((self._origins, origin, options))
        return min(options, key=lambda d: travelled[origin, d])


def set_spawn_rate(party: Participants, boxes_per_minute: float) -> None:
    endpoint = workflow_endpoint(party.db, WMS, FC.SetSpawnRateWorkflow)
    assert endpoint is not None
    response = httpx.post(
        endpoint, json={"boxes_per_minute": boxes_per_minute}, timeout=60
    )
    response.raise_for_status()


def test_at_30_boxes_a_minute_left_to_the_wms_boxes_travel_between_every_ordered_pair(
    dataset,
):
    """On the cross, which names Modules 1 and 2 as entry modules and
    Modules 4 and 5 as exit modules, the WMS starts a box on a free entry module and sends it to an
    exit module, so boxes travel between every one of the four ordered pairs, never from or to
    Module 3, and all arrive. The WMS picks toward the pairs no box has travelled yet rather than
    at random (`FewestTravelled`), so that four pairs need few more than four boxes; what it picks
    from is its own, and is checked: the free entry modules as the origin, and the exit modules as
    the destination."""
    chooser = FewestTravelled(connect(dataset), [M4, M5])
    every_pair = {(o, d) for o in (M1, M2) for d in (M4, M5)}
    with participants(dataset, CROSS_TOPOLOGY, choose=chooser) as party:
        set_spawn_rate(party, BOXES_PER_MINUTE)
        started = time.monotonic()
        try:
            eventually(
                lambda: (
                    every_pair
                    <= {
                        (o, d)
                        for o, d, status in transport_ends(party.db)
                        if status == "done"
                    }
                ),
                timeout=PAIRS_SECONDS,
                interval=2.0,
            )
        finally:
            set_spawn_rate(party, 0)
        covered = time.monotonic() - started
        by_then = len(transport_ends(party.db))
        # A spawn under way when the rate went to 0 still ends; then every box arrives.
        time.sleep(5)
        eventually(
            lambda: all(status == "done" for _, _, status in transport_ends(party.db)),
            timeout=TRANSPORTS_SECONDS,
            interval=1.0,
        )
        travelled = transport_ends(party.db)
        held = boxes_held_by(party.db, WMS)

    assert all(origin != destination for origin, destination, _ in travelled)
    assert {(o, d) for o, d, _ in travelled} == every_pair
    assert len(held) == len(travelled)
    for origins, origin, destinations in chooser.offered:
        assert set(origins) <= {M1, M2} and origin in origins
        assert destinations == [M4, M5]
    assert any(origins == [M1, M2] for origins, _, _ in chooser.offered)
    assert M3 not in {m for o, d, _ in travelled for m in (o, d)}
    print(
        f"{by_then} boxes covered the {len(every_pair)} ordered pairs in {covered:.0f} s at "
        f"{BOXES_PER_MINUTE} boxes a minute offered: {by_then / covered * 60:.1f} a minute "
        f"carried; {len(travelled)} boxes in all, every one arrived"
    )
