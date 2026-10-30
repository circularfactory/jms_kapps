"""The triple store both use cases run against, and how an ontology or a shapes file gets into it.

Every store is built by the triple-store interface's `connect()`, through the middleware's
`triplestore_for`, and is typed by the interface's contract, `TripleStore`. The store's URL and
the identity come from the environment, exactly as the middleware's own examples read them:
`TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD`. The dataset never does. A
seed clears the dataset it is pointed at, so the caller always names it, and the name defaults to
the one dataset these use cases own. Every demo runs on the configured store. On it, create that
dataset once from `graphdb/jms-kapps-repo-config.ttl`. The local store that the project's
`compose.yaml` brings up, only the fallback for a machine on which no store is configured, has it
already.

The store is built by `connect()` and named in the interface's terms, the dataset and the
`TRIPLESTORE_*` variables; the reset sets the shapes with the store's `shapes_set`.

The reset seeds a demonstration or test dataset: a productive store already holds the
ontologies and shapes, and no service calls the reset (services use this module only to connect
and to read).

A dataset is emptied whole by `clear_graph_by_graph`, the Launcher's Clear: every graph, one
by one, and then the count of the statements it still holds.
"""

from __future__ import annotations

from importlib import resources
from typing import Any, Protocol

from kapps_semantic_middleware.credentials import triplestore_for
from kapps_semantic_middleware.seeding import (
    CORE_GRAPH,
    MES_GRAPH,
    SERVICE_GRAPH,
    clear_dataset,
    load_shared_ontologies,
    read_service_shapes,
)
from kapps_semantic_middleware.validation_report import rule_names
from kapps_triplestore_interface import (
    IRI,
    ShaclValidationError,
    SPARQLQuery,
    TripleStore,
    TripleStoreNotSupportedError,
)

from jms_kapps.common.prefixes import register_prefixes

DEFAULT_DATASET = "jms-kapps"

# The media type of the shapes a reset sets: Turtle, as the use cases ship them.
SHAPES_CONTENT_TYPE = "text/turtle"

# The named graphs of the middleware's shared ontologies, Core, `svc:` and `mes:`, which every
# reset loads again (`load_shared_ontologies`), in the order it loads them.
ONTOLOGY_GRAPHS: tuple[IRI, ...] = (CORE_GRAPH, SERVICE_GRAPH, MES_GRAPH)

# Every named graph of the dataset that holds a statement, in standard SPARQL.
_NAMED_GRAPHS = "SELECT DISTINCT ?graph WHERE { GRAPH ?graph { ?s ?p ?o } }"


def connect(dataset: str = DEFAULT_DATASET) -> TripleStore:
    """A store bound to `dataset`, from the interface's `connect()`, with the store's URL and the
    identity read from the environment. It logs in and probes the dataset now.

    Raises:
        ValueError: If a connection variable is missing, naming the variable.
        TripleStoreInterfaceError: The error of the interface's `connect()`, unchanged: the store
            cannot be reached, refuses the identity, or does not have the dataset.
    """
    return triplestore_for(dataset)


class Queryable(Protocol):
    """Anything that answers a SPARQL query, as a store does. What a reader takes."""

    def query(self, query: str, *, convert_bindings: bool = ...) -> Any: ...


def bindings(
    db: Queryable, sparql: str | SPARQLQuery, *, convert: bool = True
) -> list[dict]:
    """The rows a SELECT answers, each a dict by variable name. `sparql` is the query's text, or
    a query the interface's builder made, which is sent as its rendered text.

    The values are converted to Python and rdflib types. With `convert=False` each value is the
    term as SPARQL's JSON results write it, a dict with its `type` and its lexical `value`.
    """
    text = sparql.to_string() if isinstance(sparql, SPARQLQuery) else sparql
    return result_rows(db.query(text, convert_bindings=convert))


def result_rows(result: Any) -> list[dict]:
    """The rows of what a client's `query` answered: none where it answered no SELECT's
    results."""
    return (
        result.get("results", {}).get("bindings", [])
        if isinstance(result, dict)
        else []
    )


def read_asset(package: str, *path: str) -> str:
    """Return a Turtle file shipped inside `package`."""
    return resources.files(package).joinpath(*path).read_text(encoding="utf-8")


def with_operation_status_shape(shapes: str) -> str:
    """`shapes` with the middleware's operation-status shape in front of it: every Operation has
    exactly one status, one of four values. An empty `shapes` gives that shape alone.

    The store's `shapes_set` replaces every shape, so the two go as one document. Each Turtle file
    declares its own prefixes, which the joined document repeats.
    """
    return read_service_shapes() + "\n" + shapes


def reset(
    store: TripleStore, *, ontology: str, shapes: str | None, individuals: str = ""
) -> None:
    """Empty the dataset, load one use case's ontology and shapes into it, then a seed's
    `individuals`, and register jms_kapps' prefixes in it.

    The order matters. The old data goes first, because a store validates the data it already
    holds against its shapes the moment the shapes change. Then the shapes, which replace every
    shape the dataset had, through the store's `shapes_set`; a dataset without SHACL validation
    refuses them. The middleware's operation-status shape joins every shapes document the reset
    sends, an empty one too (`with_operation_status_shape`): the demonstrations seed it, the same
    way they load the middleware's service ontology, and a productive knowledge graph already
    holds it. With `shapes` None, the reset sends no shapes at all, for a dataset without SHACL
    validation that a caller seeds on purpose, as the measurement harness seeds its SHACL-off
    twin. Then Core, `svc:` and `mes:`, then the use case's ontology into the default graph, which is where the
    middleware looks for its alignment axioms, then the individuals, also as Turtle, into the
    default graph. The prefixes come last, after every import, because an import adds back the
    names nobody chose that `register_prefixes` deletes.

    Core, `svc:` and `mes:` each live in a named graph of their own, which the clear leaves
    standing, and each seed loads them again from the installed middleware. A dataset seeded
    under an earlier release of the middleware would otherwise keep that release's `svc:`, and the
    object-graph mapper refuses a write of a term that a newer release added, such as the issuer
    that the middleware's `issue_operation()` writes.

    Raises:
        TripleStoreNotSupportedError: If the dataset has no SHACL validation and `shapes` is not
            None, so that the shapes would validate nothing. Only the old data is cleared then.
    """
    clear_dataset(store)
    if shapes is not None:
        store.shapes_set(with_operation_status_shape(shapes), SHAPES_CONTENT_TYPE)
    load_shared_ontologies(store, reload=True)
    store.graph_add(ontology, content_type="application/x-turtle")
    if individuals:
        store.graph_add(individuals, content_type="application/x-turtle")
    register_prefixes(store)


class ClearFailed(RuntimeError):
    """A clear of a dataset stopped part-way: the store did not clear one of its graphs or its
    shapes, did not list its graphs, or did not say how many statements are left. The message
    says which and why, for a person to read. `nothing_cleared` is whether it stopped before any
    graph was cleared, so that the dataset holds what it held."""

    def __init__(self, message: str, *, nothing_cleared: bool = False) -> None:
        super().__init__(message)
        self.nothing_cleared = nothing_cleared


def clear_graph_by_graph(store: TripleStore) -> int:
    """Clear every graph of the dataset, one by one, and its shapes, and return how many
    statements it holds then: 0 where it is empty.

    The store lists its named graphs first, by a standard SPARQL query, so that a store that
    cannot list them has nothing cleared. Then the default graph is cleared, then the named graphs
    of the shared ontologies, each whether it holds anything or not, then the shapes, through the
    store's `shapes_set` with an empty document; then every other named graph the store listed,
    such as one a person loaded or another program wrote, or the one in which the middleware keeps
    the receipts of writes that a stopped process left, `urn:x-kapps:write-receipts`. That is the
    order in which a reset clears the first three (`reset`), which a store that validates with
    shapes takes: the data goes before the ontologies and the shapes it is checked against. A
    dataset without SHACL validation has no shapes to remove, and its store refuses `shapes_set`
    as not supported: the clear goes on. One request for each graph, through the interface's and
    the middleware's own calls, and never one request for the whole dataset: the store refuses
    that while it collects history, and clears each graph. Nothing here turns the history collection off: a clear while it is off is not
    recorded, and the cleared data would then disappear from every earlier point in time.

    The interface takes every absolute IRI as the name of a graph, so the receipts graph is listed
    and cleared as any other.

    The count is `statement_count`, of the statements written, after the last clear.

    Raises:
        ClearFailed: If the store did not list its named graphs, and nothing was cleared. If it
            did not clear a graph or the shapes, naming which, the dataset and why; nothing later
            is cleared then. Or if it did not say how many statements are left.
    """
    try:
        listed = [
            IRI(row["graph"]["value"])
            for row in bindings(store, _NAMED_GRAPHS, convert=False)
        ]
    except Exception as error:
        raise ClearFailed(
            f"the triple store did not list the named graphs of {store.dataset}, so nothing was "
            f"cleared: {_why(error)}",
            nothing_cleared=True,
        ) from error
    # The default graph first: a refusal of it leaves the dataset as it was.
    _clear(store, None, nothing_cleared=True)
    for graph in ONTOLOGY_GRAPHS:
        _clear(store, graph, nothing_cleared=False)
    _clear_shapes(store)
    for graph in (g for g in listed if g not in ONTOLOGY_GRAPHS):
        _clear(store, graph, nothing_cleared=False)
    try:
        return statement_count(store)
    except Exception as error:
        raise ClearFailed(
            f"every graph of {store.dataset} was cleared, but the triple store did not say how "
            f"many statements it holds: {_why(error)}"
        ) from error


def statement_count(db: Queryable) -> int:
    """How many statements the dataset holds as written: in its default graph and in every named
    graph, leaving out what its reasoner infers, since a dataset that holds none still infers the
    axioms of its ruleset.

    The scope of the written statements is stated through the interface's query builder
    (`include_implicit=False`), which writes it in the form its store understands. On the remote
    store that scope is the written statements of the default graph alone, beside every named
    graph of the dataset (the documentation of its query behaviour), so each named graph is
    counted on its own. A statement of the default scope that a named graph holds too is counted
    there only, so the count is the same where a store's default scope takes in its named graphs,
    and a statement that the default graph and a named graph both hold is counted once.
    """
    rows = bindings(
        db,
        SPARQLQuery.select(
            variables=["(COUNT(*) AS ?statements)"],
            where_clauses=[
                "{ ?s ?p ?o FILTER NOT EXISTS { GRAPH ?in { ?s ?p ?o } } }",
                "UNION { GRAPH ?g { ?s ?p ?o } }",
            ],
            include_implicit=False,
        ),
        convert=False,
    )
    return int(rows[0]["statements"]["value"]) if rows else 0


def _clear(store: TripleStore, graph: IRI | None, *, nothing_cleared: bool) -> None:
    """Clear `graph` of the dataset, the default graph if None, as the interface names it:
    the middleware's own clear of the default graph, which refuses to go on where the store did
    not clear it, and the interface's of a named graph.

    Raises:
        ClearFailed: If the store did not clear it, naming it and why, with `nothing_cleared`
            as given: whether no graph was cleared before it.
    """
    what = "the default graph" if graph is None else f"the graph <{graph}>"
    try:
        if graph is None:
            clear_dataset(store)
            return
        cleared = store.graph_clear(graph)
    except Exception as error:
        raise ClearFailed(
            f"the triple store did not clear {what} of {store.dataset}: {_why(error)}",
            nothing_cleared=nothing_cleared,
        ) from error
    if not cleared:
        raise ClearFailed(
            f"the triple store did not clear {what} of {store.dataset}: "
            "it answered that it did not",
            nothing_cleared=nothing_cleared,
        )


def _clear_shapes(store: TripleStore) -> None:
    """Remove every shape of the dataset, through the store's `shapes_set` with an empty
    document. A dataset without SHACL validation has none, and its store refuses the call as not
    supported: nothing is removed then, and the clear goes on.

    Raises:
        ClearFailed: If the store did not remove them, naming why. A graph was cleared before.
    """
    try:
        store.shapes_set("", SHAPES_CONTENT_TYPE)
    except TripleStoreNotSupportedError:
        return
    except Exception as error:
        raise ClearFailed(
            f"the triple store did not remove the shapes of {store.dataset}: {_why(error)}",
            nothing_cleared=False,
        ) from error


def _why(error: Exception) -> str:
    """Why the store refused, for a person to read: the rules a validation report names, by
    their shape at their focus node, rather than the report itself, or else the error's own
    message."""
    if isinstance(error, ShaclValidationError):
        rules = rule_names(error.report)
        return "its shapes refused it" + (f", by {', '.join(rules)}" if rules else "")
    return str(error)
