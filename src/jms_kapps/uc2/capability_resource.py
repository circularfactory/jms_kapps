"""`jms-uc2 capability-resource`: the store refuses a capability that does not belong to exactly one
resource.

A capability cannot exist without a resource, and a resource can have several. The shapes that
every use case seeds hold that rule: exactly one resource links to each capability
(`cfc:hasCapability`), checked at every commit. The domain code never breaks it, because the
middleware registers each capability with its resource's link in one commit, so in a normal run the
rule never fires. This command shows it firing. On a disposable dataset of the configured store it
seeds use case 2's ontology and shapes, makes three writes, and prints for each what it wrote and
what the store answered:

1. a resource that registers two capabilities: admitted;
2. a capability with no resource: refused;
3. a capability that two resources claim: refused.

Each write is plain triples in one SPARQL update, not a write of the middleware, so that it can
break the rule. The command clears the dataset at its end, and takes only a name that starts with
`ogm-disposable-`, since it clears the dataset at its start too. It exits 0 when the store answers
each write as above, and 1 when it does not, or when the store cannot be reached.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO

from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import (
    IRI,
    ShaclValidationError,
    SPARQLQuery,
    TripleStore,
)
from kapps_triplestore_interface.exceptions import TripleStoreInterfaceError
from rdflib.namespace import RDF

from jms_kapps.common.triplestore import ClearFailed, bindings, clear_dataset, connect
from jms_kapps.uc2.seed import Triple, reset_uc2
from jms_kapps.uc2.validation_report import messages_clause, rules_clause
from jms_kapps.uc2.vocabulary import FC, FCI_NS

PROGRAM = "jms-uc2 capability-resource"

# The datasets the command clears: the store helper's disposable ones.
DISPOSABLE_PREFIX = "ogm-disposable-"

RESOURCE_1 = IRI("CapabilityDemoModule1", base=FCI_NS)
RESOURCE_2 = IRI("CapabilityDemoModule2", base=FCI_NS)
TRANSPORT_1 = IRI("CapabilityDemoModule1_capability_transport", base=FCI_NS)
RESERVE_1 = IRI("CapabilityDemoModule1_capability_reserve", base=FCI_NS)
NO_RESOURCE = IRI("CapabilityDemoCapability_of_no_resource", base=FCI_NS)
TWO_RESOURCES = IRI("CapabilityDemoCapability_of_two_resources", base=FCI_NS)

TYPE = IRI(str(RDF.type))


@dataclass(frozen=True)
class Write:
    """One write of the demonstration: what it shows, its triples, and whether a store seeded with
    the shapes admits it."""

    title: str
    triples: tuple[Triple, ...]
    should_be_admitted: bool

    def update(self) -> str:
        """The SPARQL update that makes the write, in one commit."""
        return f"INSERT DATA {{\n{_lines(self.triples)}\n}}"


WRITES = (
    Write(
        "A resource that registers two capabilities",
        (
            (RESOURCE_1, TYPE, FC.FlexConveyorModule),
            (TRANSPORT_1, TYPE, FC.BoxTransportCapability),
            (RESERVE_1, TYPE, FC.ReserveCapability),
            (RESOURCE_1, CFC.hasCapability, TRANSPORT_1),
            (RESOURCE_1, CFC.hasCapability, RESERVE_1),
        ),
        should_be_admitted=True,
    ),
    Write(
        "A capability with no resource",
        ((NO_RESOURCE, TYPE, FC.BoxTransportCapability),),
        should_be_admitted=False,
    ),
    Write(
        "A capability that two resources claim",
        (
            (RESOURCE_2, TYPE, FC.FlexConveyorModule),
            (TWO_RESOURCES, TYPE, FC.BoxTransportCapability),
            (RESOURCE_1, CFC.hasCapability, TWO_RESOURCES),
            (RESOURCE_2, CFC.hasCapability, TWO_RESOURCES),
        ),
        should_be_admitted=False,
    ),
)


@dataclass(frozen=True)
class Answer:
    """What the store answered to a write: whether it admitted it, and the report of a refusal."""

    admitted: bool
    report: str = ""


def make(db: TripleStore, write: Write) -> Answer:
    """Make `write` against `db`, and return what the store answered."""
    try:
        db.query(write.update(), update=True)
    except ShaclValidationError as refused:
        return Answer(admitted=False, report=refused.report)
    return Answer(admitted=True)


def stored(db: TripleStore, triples: tuple[Triple, ...]) -> list[Triple]:
    """The ones of `triples` that `db` holds as explicit statements."""
    values = " ".join(f"(<{s}> <{p}> <{o}>)" for s, p, o in triples)
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["?s", "?p", "?o"],
            where_clauses=[f"VALUES (?s ?p ?o) {{ {values} }}", "?s ?p ?o"],
            include_implicit=False,
        ),
        convert=False,
    )
    return [
        (IRI(row["s"]["value"]), IRI(row["p"]["value"]), IRI(row["o"]["value"]))
        for row in rows
    ]


def demo(db: TripleStore, *, out: TextIO = sys.stdout) -> int:
    """Seed use case 2's ontology and shapes into `db`, which it clears, make the three writes, and
    print each with the store's answer to `out`. Returns 0 when the store answers each write as
    `WRITES` says, and 1 when it does not."""
    reset_uc2(db)
    code = 0
    for number, write in enumerate(WRITES, start=1):
        print(f"{number}. {write.title}", file=out)
        print("   wrote:", file=out)
        print(_lines(write.triples, indent="     "), file=out)
        answer = make(db, write)
        if answer.admitted:
            print("   the store: admitted", file=out)
        else:
            print(
                f"   the store: refused{rules_clause(answer.report)}"
                f"{messages_clause(answer.report)}",
                file=out,
            )
        if answer.admitted != write.should_be_admitted:
            expected = "admit" if write.should_be_admitted else "refuse"
            print(f"   expected the store to {expected} it", file=out)
            code = 1
    return code


def run(dataset: str) -> int:
    """Play the demonstration on `dataset` of the configured store, then clear it. Returns the exit
    code of `demo`, or 1 with the reason on stderr when the name is not a disposable one, or the
    store cannot be reached, seeded or cleared."""
    if not dataset.startswith(DISPOSABLE_PREFIX):
        print(
            f"{PROGRAM}: clears the dataset it is given, so it takes only a name that starts with "
            f"{DISPOSABLE_PREFIX}, not {dataset}",
            file=sys.stderr,
        )
        return 1
    try:
        db = connect(dataset)
        try:
            return demo(db)
        finally:
            clear_dataset(db)
    except (ValueError, TripleStoreInterfaceError, ClearFailed) as error:
        print(f"{PROGRAM}: {error}", file=sys.stderr)
        return 1


def _lines(triples: tuple[Triple, ...], *, indent: str = "  ") -> str:
    return "\n".join(f"{indent}<{s}> <{p}> <{o}> ." for s, p, o in triples)
