"""The one log line that a participant of use case 1 prints each time it uses a class scope.

A class scope tells the object-graph mapper which properties of an individual to read or to write,
and no others. Each participant uses small scopes of its own, one for each thing it needs. So that
a person who watches a run sees which scope is used where, and what it is for, every read or write
with a scope prints one INFO line, in this form:

    Planner reads UnscrewingOperation_1a2b with OUTCOME_SCOPE = [[hasDetectionResult]]: it wants the
    outcome that the detector committed (hasDetectionResult)

The scope is written out as its property chains, each chain in brackets: `[[a], [b, c]]` reads
the property `a`, and `c` of what `b` links to. An empty scope is `[]`: it reads nothing.

A participant's INFO lines appear in the terminal of `jms-uc1`, behind the participant's name, and
on its activity page, if it has one.
"""

from __future__ import annotations

import logging

from kapps_ogm import ClassScope
from kapps_triplestore_interface import IRI


def log_scope(
    logger: logging.Logger,
    who: str,
    verb: str,
    target: object,
    name: str,
    scope: ClassScope,
    wants: str,
) -> None:
    """Log that `who` `verb`s `target` with the class scope `name`, written out, and what it
    wants.

    Args:
        logger: The logger of the module that uses the scope.
        who: The participant, as a person calls it: "Planner", "Screwing resource".
        verb: "reads", "writes", or "receives".
        target: The individual, by its IRI; the line shows its local name.
        name: The name of the scope, as the code names it: "OPERATION_SCOPE".
        scope: The scope itself, which the line writes out as its property chains.
        wants: What the scope is for, in words, with the properties in parentheses.
    """
    logger.info(
        "%s %s %s with %s = %s: %s",
        who,
        verb,
        _local(target),
        name,
        written_out(scope),
        wants,
    )


def written_out(scope: ClassScope) -> str:
    """`scope` as its property chains, by the properties' local names: `[[a], [b, c]]`."""
    chains = scope.to_property_chains()
    return (
        "["
        + ", ".join(
            "[" + ", ".join(_local(prop) for prop in chain) + "]" for chain in chains
        )
        + "]"
    )


def _local(target: object) -> str:
    """The local name of `target`, or the whole of it when it has none."""
    text = str(getattr(target, "id", target))
    try:
        return IRI(text).fragment or text
    except Exception:  # noqa: BLE001 - a log line must never raise
        return text
