"""The prefixes a seed registers in the dataset it seeds, one for each vocabulary jms_kapps
writes, and how it registers them.

A store's workbench writes an IRI with a prefix the dataset maps to its namespace. An import adds
each prefix its Turtle declares that the dataset has no mapping for yet, so a namespace can also
carry a name nobody chose for it: `ns1`, which rdflib declares for a namespace it has no prefix
for, or the empty prefix, which an ontology declares for its own vocabulary. The workbench then
writes `ns1:Box`. With these prefixes registered and such names deleted where they only repeat one
of them, a person reads `fc:Box` and `fci:Module1` there. Only the seed changes a dataset's
prefixes, through the store's prefix registry: `prefixes_get`, `prefix_set` and `prefix_delete`
of the triple-store interface's contract. A store without one gets no prefixes, and the seed goes
on: the prefixes serve only the workbench's display. (Changed on 2026-10-11, jms_kapps 62: until
then the seed sent the store's own namespace requests past the interface, which had no call for
them.)

The vocabularies use case 2 names by prefix are its `PREFIXES`. The dataset carries two more:
the FlexConveyor's individuals, which a page names by local name, and use case 1's vocabulary.

Changed on 2026-10-06, when the monitor's refusal cards moved to the middleware's reader of a
validation report, which names every term by its local name (jms_kapps 38, decided by
Etienne). Until then the first sentence of this paragraph read: "The vocabularies a page names by
prefix are use case 2's `PREFIXES`, so a page and the Workbench write the same prefix for each."
"""

from __future__ import annotations

import logging
import re

from kapps_triplestore_interface import TripleStore, TripleStoreNotSupportedError

from jms_kapps.uc1.vocabulary import JMS_NS
from jms_kapps.uc2.vocabulary import FCI_NS
from jms_kapps.uc2.vocabulary import PREFIXES as UC2_PREFIXES

logger = logging.getLogger(__name__)

PREFIXES: dict[str, str] = {**UC2_PREFIXES, "fci": FCI_NS, "jms": JMS_NS}

# A name nobody chose for a namespace: the empty prefix, or `ns` followed by digits.
_MADE_UP_PREFIX = re.compile(r"(ns[0-9]+)?")


def register_prefixes(store: TripleStore) -> None:
    """Map each prefix of `PREFIXES` to its namespace in the dataset, and delete the names nobody
    chose that only repeat one of those mappings.

    A prefix the dataset maps to that namespace already is not written again. One it maps to
    another namespace stays as it is, and the seed logs it as a warning. A name nobody chose is
    the empty prefix or `ns` followed by digits, such as `ns1`. It is deleted only when, after the
    registration, a prefix of `PREFIXES` maps the same namespace, so that the name is redundant:
    while `fc` maps another namespace, an `ns1` that maps the FlexConveyor's stays. Every other
    prefix stays.

    A store without a prefix registry, whose prefix operations raise
    `TripleStoreNotSupportedError`, gets no prefixes: the registration logs one line and returns,
    and the seed goes on, because the prefixes serve only the display of a store's workbench.

    Only the seed calls this, the demonstration's reset of its own dataset, after its last
    import, because an import adds each prefix its Turtle declares that the dataset has no
    mapping for, even where another prefix maps the same namespace. No participant, and no other
    code path, deletes or changes a dataset's prefixes: a real factory never allows it.

    Raises:
        TripleStoreResponseError: If the store does not answer the prefixes, or refuses to set
            or delete a prefix, as the interface raises it.
    """
    try:
        _register(store)
    except TripleStoreNotSupportedError as unsupported:
        logger.info(
            "The dataset %r gets no prefixes, because its store has no prefix registry, and the "
            "seed goes on: %s",
            store.dataset,
            unsupported,
        )


def _register(store: TripleStore) -> None:
    """The registration of `register_prefixes`, which raises where the store has no registry."""
    registered = store.prefixes_get()
    # The namespaces a prefix of `PREFIXES` maps once the registration is done.
    covered: set[str] = set()
    for prefix, namespace in PREFIXES.items():
        if registered.get(prefix) == namespace:
            covered.add(namespace)
            continue
        if prefix in registered:
            logger.warning(
                "The dataset %r maps the prefix %s: to %s, not to %s. The seed leaves it as "
                "it is.",
                store.dataset,
                prefix,
                registered[prefix],
                namespace,
            )
            continue
        store.prefix_set(prefix, namespace)
        covered.add(namespace)
    for prefix, namespace in registered.items():
        if namespace in covered and _MADE_UP_PREFIX.fullmatch(prefix):
            store.prefix_delete(prefix)
            logger.info(
                "The seed deleted the prefix %s: of the dataset %r, which mapped %s.",
                prefix,
                store.dataset,
                namespace,
            )
