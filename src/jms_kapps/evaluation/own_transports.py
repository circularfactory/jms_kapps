"""The run's own transports: the harness learns them from the WarehouseManagementSystem's log, so
that none of its reads names the whole store (added on 2026-10-12, jms_kapps 90).

Right after the store admitted the create of a box, the WarehouseManagementSystem logs that the
box's transport is `queued` (`jms_kapps.uc2.transport.status_record`), and the line names the
transport. A spawn that fails after its create, as one whose origin cannot be told, has logged it
already. So the transports of a run are the ones that such lines name, in the order they came; the
prefill's past transports are none of them, since the WarehouseManagementSystem did not open them.

A transport is told by its local name alone. No number is parsed from it: a box may be named by a
number or by a UUID.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection

from kapps_semantic_middleware.vocabulary import OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.uc2.transport import status_record
from jms_kapps.uc2.vocabulary import FCI_NS, WMS

# The one place the name is told from the line: the WarehouseManagementSystem's own formatter makes
# the line, with a stand-in name where the transport goes.
_NAME = "TRANSPORT-NAME"
_QUEUED = re.compile(
    re.escape(
        status_record(WMS, IRI(_NAME, base=FCI_NS), OperationStatus.QUEUED)
    ).replace(re.escape(_NAME), r"(?P<transport>[A-Za-z0-9_.\-]+)")
    + "$"
)


def queued_transport(line: str) -> str | None:
    """The local name of the transport that `line` of the WarehouseManagementSystem's output says
    is `queued`, or None where it says nothing of the kind. The line may begin with the logger's
    prefix."""
    found = _QUEUED.search(line)
    return found["transport"] if found else None


class OwnTransports:
    """The transports a run has opened so far, as the WarehouseManagementSystem's log names them.

    Args:
        new_lines: The lines the WarehouseManagementSystem has written since the last call.
    """

    def __init__(self, new_lines: Callable[[], list[str]]) -> None:
        self._new_lines = new_lines
        self.names: list[str] = []
        self._known: set[str] = set()

    def refresh(self) -> list[str]:
        """Read what the log gained, and return the transports it names for the first time."""
        gained = []
        for line in self._new_lines():
            name = queued_transport(line)
            if name is not None and name not in self._known:
                self._known.add(name)
                self.names.append(name)
                gained.append(name)
        return gained

    def without(self, seen: Collection[str]) -> list[str]:
        """The transports named so far that are not in `seen`, in the order they came."""
        return [name for name in self.names if name not in seen]
