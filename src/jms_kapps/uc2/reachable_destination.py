"""`jms-uc2 reachable-destination`: the store refuses a SHACL-SPARQL rule at commit.

SHACL Core covers every physical invariant of use case 2 but one: a transport may only be opened
toward a module that the station's connections reach from its origin (`:ReachableDestinationShape`,
in the SHACL-SPARQL shapes file). Core cannot say it, and the store enforces it at commit like a
Core rule. A launch does not load it, because its SPARQL target costs every commit. This command
shows it firing, apart from use case 2 at full scale. On a disposable dataset of the configured
store it seeds three modules, with the Core and the SHACL-SPARQL shapes as one shapes document, where
the first is connected to the second and no connection reaches the third, and plays four steps,
printing what happens at each:

1. the seed;
2. a transport from the first module to the second: admitted;
3. a second box on the second module, with a running transport toward the third: refused, by the
   reachable-destination rule alone (a box on the first module would be refused by the Core rule of
   one box per module, for the wrong reason);
4. the connection from the first module to the second removed while the transport of step 2 is
   open: refused, although the write does not touch the transport, since the rule's target is a
   SPARQL query that the store evaluates over the whole dataset at every commit.

A refusal counts only when its report names the reachable-destination rule (in step 3, that rule
alone). The command clears the dataset at its end, and takes only a name that starts with
`ogm-disposable-`, since it clears the dataset at its start too. It exits 0 when every step behaved
as stated, and 1 otherwise, naming the steps that did not, or when the store cannot be reached.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from kapps_ogm import OGM
from kapps_semantic_middleware.validation_report import validation_results
from kapps_triplestore_interface import IRI, ShaclValidationError, TripleStore
from kapps_triplestore_interface.exceptions import TripleStoreInterfaceError

from jms_kapps.common.triplestore import ClearFailed, clear_dataset, connect
from jms_kapps.uc2.seed import place_box, seed_three_modules
from jms_kapps.uc2.validation_report import messages_clause, rules_clause
from jms_kapps.uc2.vocabulary import FC, box_iri, connection_iri

PROGRAM = "jms-uc2 reachable-destination"

# The datasets the command clears: the store helper's disposable ones.
DISPOSABLE_PREFIX = "ogm-disposable-"

RULE = str(FC.ReachableDestinationShape)

FIRST_BOX = box_iri(1)
SECOND_BOX = box_iri(2)

WHY_AT_EVERY_COMMIT = (
    "The write touched no transport: the rule's target is a SPARQL query that the store "
    "evaluates over the whole dataset at every commit, so it finds the open transport again."
)


def _refusal(write: Callable[[], object]) -> ShaclValidationError | None:
    """Make `write`, and return the store's refusal of it, or None when the store admitted it."""
    try:
        write()
    except ShaclValidationError as refused:
        return refused
    return None


def _print_refusal(refused: ShaclValidationError, out: TextIO) -> None:
    print(
        f"   the store: refused{rules_clause(refused.report)}{messages_clause(refused.report)}",
        file=out,
    )
    print("   the store's validation report:", file=out)
    print(
        "\n".join(f"     {line}" for line in refused.report.strip().splitlines()),
        file=out,
    )


def _names_the_rule(refused: ShaclValidationError | None, *, alone: bool) -> bool:
    """Whether `refused` is a refusal whose report names the reachable-destination rule, and with
    `alone` no other."""
    if refused is None:
        return False
    shapes = {result.shape for result in validation_results(refused.report)}
    return shapes == {RULE} if alone else RULE in shapes


def demo(db: TripleStore, *, out: TextIO = sys.stdout) -> int:
    """Seed three modules into `db`, which it clears, play steps 2 to 4, and print each with the
    store's answer to `out`. Returns 0 when every step behaved as stated, and 1 when it did not,
    after naming the steps that did not."""
    failed: list[str] = []
    print("1. Seed three modules and the Core and SHACL-SPARQL shapes", file=out)
    first, second, third = seed_three_modules(db, transit_seconds=0, sparql_shapes=True)
    print(
        f"   {first.fragment} is connected to {second.fragment}; "
        f"no connection reaches {third.fragment}",
        file=out,
    )
    ogm = OGM(db=db)

    print(f"2. Open a transport from {first.fragment} to {second.fragment}", file=out)
    refused = _refusal(
        lambda: place_box(ogm, FIRST_BOX, on=first, origin=first, destination=second)
    )
    if refused is None:
        print("   the store: admitted", file=out)
    else:
        _print_refusal(refused, out)
        failed.append(
            "step 2 did not behave as stated: the store refused the transport"
        )

    print(
        f"3. Place a second box on {second.fragment}, with a running transport toward "
        f"{third.fragment}, which no connection reaches",
        file=out,
    )
    refused = _refusal(
        lambda: place_box(ogm, SECOND_BOX, on=second, origin=second, destination=third)
    )
    if refused is None:
        print("   the store: admitted", file=out)
    else:
        _print_refusal(refused, out)
    if not _names_the_rule(refused, alone=True):
        failed.append(
            "step 3 did not behave as stated: expected the store to refuse the write by "
            "the reachable-destination rule alone"
        )

    print(
        f"4. Remove the connection from {first.fragment} to {second.fragment} "
        "while the transport of step 2 is open",
        file=out,
    )
    connection: IRI = connection_iri("Module1", "East")
    refused = _refusal(lambda: db.triple_delete((first, FC.hasConnection, connection)))
    if refused is None:
        print("   the store: admitted", file=out)
    else:
        _print_refusal(refused, out)
        print(f"   {WHY_AT_EVERY_COMMIT}", file=out)
    if not _names_the_rule(refused, alone=False):
        failed.append(
            "step 4 did not behave as stated: expected the store to refuse the write by "
            "the reachable-destination rule"
        )

    for line in failed:
        print(line, file=out)
    return 1 if failed else 0


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
