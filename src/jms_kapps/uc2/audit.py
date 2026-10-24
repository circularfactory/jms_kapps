"""`jms-uc2 audit`: validate a whole dataset against use case 2's shapes, and print the report.

The store refuses a write that breaks a shape at commit, but a rule that the store evaluates only
when the whole dataset is validated, and a violation that an earlier write admitted, are found
only by validating the dataset as a whole. The audit does that, through the triple-store
interface's `validate`, against both shape files: the SHACL Core file that every launch seeds, and
the SHACL-SPARQL file that holds the reachable-destination rule. It reads the dataset and clears
nothing, so it runs beside a live run.

The report is the store's own, printed as the store wrote it. The audit exits 0 when the report
says the dataset conforms, and 1 when it does not, or when the store cannot validate it.
"""

from __future__ import annotations

import sys
from typing import TextIO

from kapps_triplestore_interface import TripleStore
from kapps_triplestore_interface.exceptions import TripleStoreInterfaceError
from rdflib import Graph, Literal
from rdflib.namespace import SH

from jms_kapps.common.triplestore import SHAPES_CONTENT_TYPE, connect
from jms_kapps.uc2.seed import shapes_document


def conforms(report: str) -> bool:
    """Whether the Turtle `report` says that the validated data conforms."""
    return list(
        Graph().parse(data=report, format="turtle").objects(None, SH.conforms)
    ) == [Literal(True)]


def audit(db: TripleStore, *, out: TextIO = sys.stdout) -> int:
    """Validate the whole of `db` against both shape files, print the report to `out`, and return
    the exit code: 0 when the dataset conforms, 1 when it does not."""
    report = db.validate(shapes_document(sparql_shapes=True), SHAPES_CONTENT_TYPE)
    print(report, file=out)
    return 0 if conforms(report) else 1


def run(dataset: str) -> int:
    """Audit `dataset` of the configured store. Returns the exit code of `audit`, or 1 with the
    reason on stderr when the store cannot be reached or cannot validate."""
    try:
        return audit(connect(dataset))
    except (ValueError, TripleStoreInterfaceError) as error:
        print(f"jms-uc2 audit: {error}", file=sys.stderr)
        return 1
