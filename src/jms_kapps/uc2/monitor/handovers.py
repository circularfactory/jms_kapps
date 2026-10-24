"""The handovers the monitor sees: a box whose possessor changed between two of its reads.

The monitor sees a handover by its commit, which changes the box's possessor before the box moves.
It finds one by comparing two of its own successive reads of the graph, as the monitor of the
paper's artifact did. It asks no participant and needs no event: the possessor of every box is in
each read.

- **The WarehouseManagementSystem's handover to the origin.** The WarehouseManagementSystem
  creates a box directly on its origin, so a box that a read finds for the first time on its
  transport's origin was handed there by the WarehouseManagementSystem. A box found for the first
  time anywhere else was not seen handed over.
- **Which WarehouseManagementSystem.** The one the graph types as one, found by its class in
  the same read, not an IRI the code knows. Where the graph names none, or several, who handed a
  new box to its origin is not known, and no handover is listed for it. All information at the
  monitor stems from graph polling.
- **Two handovers between two reads.** A box whose possessor changes twice between two reads, a
  second apart, shows as one handover from the first possessor to the last. At the default
  transit time of 3 s that does not happen, because a box's next handover waits for its move;
  with a transit time under a second it can, and that is accepted.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from kapps_triplestore_interface import IRI

from jms_kapps.uc2.monitor.reads import BoxRow


@dataclass(frozen=True)
class SeenHandover:
    """A box whose possessor changed from `giver` to `receiver`, as the monitor saw it at `seen`
    (UTC, ISO 8601)."""

    seen: str
    box: IRI
    giver: IRI
    receiver: IRI


def handovers_between(
    before: Iterable[BoxRow],
    after: Iterable[BoxRow],
    seen: str,
    wms: Collection[IRI],
) -> list[SeenHandover]:
    """The handovers from the read `before` to the read `after`, in the order of the boxes' IRIs,
    each seen at `seen`. `wms` is every WarehouseManagementSystem the graph names.

    A box that `before` holds with a possessor was handed over if `after` gives it another one. A
    box that `before` does not hold was handed to its origin by the WarehouseManagementSystem if
    `after` finds it there and the graph names one WarehouseManagementSystem. A box that nobody
    possesses in one of the two reads, or that `after` no longer holds, was not seen handed over.
    """
    the_wms = next(iter(wms)) if len(wms) == 1 else None
    was = {row.box: row.holder for row in before}
    seen_handovers = []
    for row in sorted(after, key=lambda row: str(row.box)):
        if row.holder is None:
            continue
        if row.box not in was:
            giver: IRI | None = the_wms if row.holder == row.origin else None
        else:
            giver = was[row.box]
        if giver is not None and giver != row.holder:
            seen_handovers.append(SeenHandover(seen, row.box, giver, row.holder))
    return seen_handovers
