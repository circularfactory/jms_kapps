"""What the tests of a running use case 2 share: the possession shape's path, a fault switch, and
the lines of a child out of a supervisor's echo."""

from __future__ import annotations

from collections.abc import Iterable

from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.uc2.discovery import service_address
from jms_kapps.uc2.module import Fault
from jms_kapps.uc2.process import set_fault_switch

# The path of the shape that allows a module one box at a time: from the module to its one
# possession state, then back from the state to each box that points at it. A handover changes
# only the box's link, so a second box handed to the module shows up here.
POSSESSION_PATH = f"(<{CFC.hasPossessor}>/^<{CFC.hasPossessedWorkpiece}>)"


def lines_of(echoed: Iterable[str], child: str) -> list[str]:
    """The lines `child` wrote, out of what a supervisor echoed: it puts the name in front."""
    prefix = f"{child}  "
    return [line.removeprefix(prefix) for line in echoed if line.startswith(prefix)]


def set_fault(db: TripleStore, module: IRI, name: Fault, armed: bool) -> None:
    """Arm or disarm the fault `name` on `module`, at the address its service gives."""
    address = service_address(db, module)
    assert address is not None
    set_fault_switch(address, name, armed)
