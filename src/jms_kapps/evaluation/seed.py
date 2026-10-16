"""What a run of the bench launcher finds in the graph before any participant starts.

Use case 2's own seed first: the reset of the repository and the topology (`uc2.seed`). Then the
prefill file, the past journeys that give the store its size, loaded through the triple-store
interface in transactions of whole boxes, each with its possession and its transport, at most
`PREFILL_TRANSACTION_STATEMENTS` statements each (`prefill.transactions`), since a box, its
possession and its transport need each other in the store's shapes. Then the history collection,
if the run collects history, so that the prefill is in no history. The prefill is written before
any participant starts, as the seed is, so it is setup, and no participant's write: like
`uc2.seed`, this module writes to the store directly, and it is the only module of the evaluation
that does.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from kapps_triplestore_interface import TripleStore

from jms_kapps.evaluation.prefill import transactions
from jms_kapps.uc2.history import enable_history
from jms_kapps.uc2.seed import seed_topology
from jms_kapps.uc2.topology import Topology

# The most statements one transaction of the prefill holds: 1,000 boxes with their transports.
PREFILL_TRANSACTION_STATEMENTS = 10_000

# What resets the repository and seeds a topology, use case 2's seed: `seed_topology`, or a test's
# stand-in.
SeedUC2 = Callable[..., None]


class PrefillNotLoaded(RuntimeError):
    """The store did not load a transaction of the prefill file."""


def seed_with_prefill(
    db: TripleStore,
    topology: Topology,
    *,
    transit_seconds: float,
    prefill: Path | None,
    collect_history: bool,
    with_shapes: bool = True,
    seed_uc2: SeedUC2 = seed_topology,
) -> None:
    """Reset the repository of `db` and seed `topology`, each module with `transit_seconds` unless
    it has its own, then load `prefill`, a prefill file, if there is one, transaction by
    transaction, and only then turn on the history collection if `collect_history`. With
    `with_shapes` False, the reset sends no shapes: the SHACL-off twin has no SHACL validation,
    and refuses them (added on 2026-10-11, jms_kapps 62).

    Raises:
        PrefillNotLoaded: If the store did not load a transaction of the prefill file, naming the
            file and which transaction. The transactions before it stay loaded.
    """
    seed_uc2(
        db,
        topology,
        transit_seconds=transit_seconds,
        collect_history=False,
        with_shapes=with_shapes,
    )
    if prefill is not None:
        for number, document in enumerate(
            transactions(prefill, statements=PREFILL_TRANSACTION_STATEMENTS), start=1
        ):
            if not db.graph_add(document, content_type="application/x-turtle"):
                raise PrefillNotLoaded(
                    f"the store did not load transaction {number} of the prefill file "
                    f"{str(prefill)!r}"
                )
    if collect_history:
        enable_history(db)
