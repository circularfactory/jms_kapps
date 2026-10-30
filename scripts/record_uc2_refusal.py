"""Record the triple store refusing one handover onto an occupied module, through the real write
path of use case 2, and save the recording to the repository.

    TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python scripts/record_uc2_refusal.py --dataset <dataset>

It takes the dataset to record on, which it clears, as every live test does, and writes three
files below the repository root (`--root`, the checkout this script is in by default):

- `docs/recorded-refusal.md`, the page: the commit that was attempted, the store's
  validation report as the store sent it and as the module received it, the state after the
  refusal, and the provenance of the recording;
- `tests/reports/skip-reserve.nq`, the report verbatim, as the store answered it;
- `tests/reports/skip-reserve.ttl`, the same report as Turtle, with the use case's prefixes.

The recording is deterministic, apart from the blank-node labels the store gives its report, the
time, the commit and the versions on the page. It seeds two modules, module 1 connected east to
module 2, and serves each as a middleware instance, as a run does, so that each registers its
BoxTransportCapability. Then it places box A on module 2, with its transport executed on module 2,
and box B on module 1, with its transport from module 1 to module 2, running, executed on
module 1. Then module 1 hands box B to module 2 with its giving side, `Giver.hand_over`, with the
same entry a hop commits, the transport's reassignment to module 2, and without asking module 2
to reserve itself: what a module does with `skip-reserve` armed. The store refuses the commit,
nothing moves, and the recording keeps what every layer saw of the refusal.

The seed writes directly, as a seed does, before the handover. The refused commit itself goes
through the middleware's handover, the object-graph mapper and the triple store access module;
the recording only observes it: it keeps the triples the object-graph mapper hands the access
module, and the access module's own log record of the store's answer.

`record(dataset)` is the recording without the files, for a test that compares a rerun with the
committed report; `report_graph` reads a report as RDF, and `turtle` writes its Turtle copy.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import uvicorn
from kapps_ogm import OGM
from kapps_semantic_middleware import SemanticMiddleware, possession
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.validation_report import RefusedWrite
from kapps_semantic_middleware.vocabulary import CFC, CORE_NS, SVC_NS
from kapps_triplestore_interface import IRI, ShaclValidationError, TripleStore
from rdflib import Dataset, Graph, URIRef
from rdflib.namespace import RDF, SH, XSD
from rdflib.term import Node

from jms_kapps.commit_time.store import GraphDBServer
from jms_kapps.common.process import bind_socket, server_config
from jms_kapps.common.triplestore import bindings, connect
from jms_kapps.uc2 import process
from jms_kapps.uc2.discovery import capability
from jms_kapps.uc2.handover_events import HandoverEvent
from jms_kapps.uc2.handshake import Giver, MiddlewareHandover, StepResult
from jms_kapps.uc2.seed import place_box, seed_two_modules
from jms_kapps.uc2.transport import reassignment
from jms_kapps.uc2.vocabulary import FC, FC_NS, FCI_NS, module_iri

HOST = "127.0.0.1"
STARTED_WITHIN_SECONDS = 60.0
REGISTERED_WITHIN_SECONDS = 60.0
STOPPED_WITHIN_SECONDS = 30.0

PAGE = Path("docs/recorded-refusal.md")
# The command as the page shows it, with the dataset left to the reader: the page names no dataset
# of the store the recording ran on.
COMMAND = (
    "TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run python scripts/record_uc2_refusal.py "
    "--dataset <dataset>"
)
REPORT = Path("tests/reports/skip-reserve.nq")
TURTLE = Path("tests/reports/skip-reserve.ttl")

# The three sibling packages, by distribution name, whose locked versions the page names.
LIBRARIES = ("kapps-semantic-middleware", "kapps-ogm", "kapps-triplestore-interface")

M1, M2 = module_iri(1), module_iri(2)
BOX_A = IRI("BoxA", base=FCI_NS)
BOX_B = IRI("BoxB", base=FCI_NS)

# The prefixes the page and the Turtle copy write IRIs with: the use case's own, Core's, the
# middleware's service vocabulary, SHACL, and the two namespaces of the store's own terms in its
# report, so that no prefix is invented for them.
PREFIXES: dict[str, str] = {
    "": FC_NS,
    "fci": FCI_NS,
    "cfc": CORE_NS,
    "svc": SVC_NS,
    "sh": str(SH),
    "rdf": str(RDF),
    "xsd": str(XSD),
    "rdf4j": "http://rdf4j.org/schema/rdf4j#",
    "rsx": "http://rdf4j.org/shacl-extensions#",
}

# The logger of the triple store access module's adapter, which records the store's error
# answer, status and body unchanged, at DEBUG; and the logger a module's giving side warns on.
ACCESS_MODULE_LOGGER = "GraphDB"
MODULE_LOGGER = "jms_kapps.uc2.module"

Triple = tuple[Any, Any, Any]


class NotRecorded(RuntimeError):
    """The recording could not be made. The message says why, for a person to read."""


# -- What the recording keeps --------------------------------------------------------------- #


@dataclass(frozen=True)
class AttemptedUpdate:
    """The one update the object-graph mapper handed the access module for the handover's
    commit: the triples to remove and the triples to add, as one write."""

    removed: tuple[Triple, ...]
    added: tuple[Triple, ...]


@dataclass(frozen=True)
class Query:
    """One SPARQL query of the state, as the page shows it, and its rows: each a dict by
    variable name, with the value as the store wrote it."""

    title: str
    sparql: str
    rows: tuple[tuple[tuple[str, str], ...], ...]


@dataclass(frozen=True)
class Recording:
    """What one run kept: the refused commit, the report at every layer, and the state."""

    dataset: str
    store: str
    time: datetime
    capabilities: dict[IRI, IRI]
    update: AttemptedUpdate
    report: str
    status: int | None
    store_answer: str
    middleware_record: RefusedWrite
    module_result: StepResult
    module_warning: str
    event: HandoverEvent
    before: tuple[Query, ...]
    after: tuple[Query, ...]


@dataclass(frozen=True)
class Provenance:
    """Where the recording comes from: the source, the libraries, the store and the command.
    `uncommitted` lists the files of the checkout that were changed and not committed when the
    recording ran, apart from the three files the recording writes."""

    commit: str
    uncommitted: tuple[str, ...]
    versions: dict[str, str]
    store: str
    time: datetime
    command: str


# -- Observing the write path --------------------------------------------------------------- #


class ObservedStore:
    """The store of module 1's middleware instance, as the access module's contract reaches it,
    with one addition: it keeps each `triples_update` it is sent, and sends it on unchanged. The
    object-graph mapper writes a commit of several changes with exactly one of them."""

    def __init__(self, store: TripleStore) -> None:
        self._store = store
        self.updates: list[AttemptedUpdate] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def triples_update(self, *args: Any, **kwargs: Any) -> bool:
        old = kwargs["old_triples"] if "old_triples" in kwargs else args[0]
        new = kwargs["new_triples"] if "new_triples" in kwargs else args[1]
        self.updates.append(AttemptedUpdate(removed=_sorted(old), added=_sorted(new)))
        return bool(self._store.triples_update(*args, **kwargs))


def _writes(update: AttemptedUpdate, subject: IRI) -> bool:
    """Whether `update` removes or adds a triple of `subject`."""
    return any(
        str(triple[0]) == str(subject) for triple in (*update.removed, *update.added)
    )


def _sorted(triples: Any) -> tuple[Triple, ...]:
    return tuple(sorted(triples, key=lambda t: tuple(str(term) for term in t)))


@contextlib.contextmanager
def _capturing(name: str, level: int) -> Iterator[list[logging.LogRecord]]:
    """Every record logged on `name` at `level` or above while the block runs, kept and not
    passed on: the logger's level and propagation are restored after."""
    logger = logging.getLogger(name)
    kept: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            kept.append(record)

    handler = Keep(level)
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    try:
        yield kept
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def _observed_handover(
    middleware: SemanticMiddleware, raised: list[ShaclValidationError]
) -> MiddlewareHandover:
    """The middleware's handover of `middleware`, as a module's giving side opens it, which
    also keeps the store's refusal as the middleware raises it, and raises it on unchanged."""

    @contextlib.contextmanager
    def handover(**arguments: Any) -> Iterator[None]:
        try:
            with possession.handover(middleware, **arguments):
                yield
        except ShaclValidationError as refused:
            raised.append(refused)
            raise

    return handover


# -- The parties ---------------------------------------------------------------------------- #


@dataclass
class Served:
    """One module's middleware instance, and the server that serves it on its thread."""

    middleware: SemanticMiddleware
    server: uvicorn.Server
    thread: threading.Thread


def _no_move(box: IRI, receiver: IRI) -> None:
    """A move that moves nothing: the refused handover never runs it."""


def _serve(module: IRI, ogm: OGM) -> Served:
    """Build `module`'s middleware instance over `ogm`, as its process builds it, and serve it
    on a port of its own, on a thread of its own."""
    sock = bind_socket(HOST)
    middleware = process.build(
        str(module), ogm, HOST, sock.getsockname()[1], move=_no_move
    )
    server = uvicorn.Server(server_config(middleware.app))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    return Served(middleware, server, thread)


@contextlib.contextmanager
def _modules(dataset: str, observed: ObservedStore) -> Iterator[dict[IRI, Served]]:
    """Module 1, over `observed`, and module 2, each served until the block ends, both started
    and both registered in the graph."""
    served: dict[IRI, Served] = {}
    try:
        served[M1] = _serve(M1, OGM(db=cast(TripleStore, observed)))
        served[M2] = _serve(M2, OGM(db=connect(dataset)))
        _wait_until(
            lambda: all(s.server.started for s in served.values()),
            STARTED_WITHIN_SECONDS,
            "the modules did not start",
        )
        yield served
    finally:
        for s in served.values():
            s.server.should_exit = True
        for s in served.values():
            s.thread.join(timeout=STOPPED_WITHIN_SECONDS)


def _wait_until(done: Callable[[], bool], timeout: float, failure: str) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            raise NotRecorded(f"{failure} within {timeout:g} s")
        time.sleep(0.1)


def _capabilities(db: TripleStore) -> dict[IRI, IRI]:
    """Each module's BoxTransportCapability, once both have registered it."""
    found: dict[IRI, IRI] = {}

    def registered() -> bool:
        for module in (M1, M2):
            if module not in found:
                iri = capability(db, module, FC.BoxTransportCapability)
                if iri is not None:
                    found[module] = iri
        return len(found) == 2

    _wait_until(
        registered,
        REGISTERED_WITHIN_SECONDS,
        "the modules did not register their BoxTransportCapability",
    )
    return found


# -- The state ------------------------------------------------------------------------------ #


def _prefix_lines(*prefixes: str) -> str:
    return "".join(f"PREFIX {p}: <{PREFIXES[p]}>\n" for p in prefixes)


def state_queries() -> tuple[tuple[str, str], ...]:
    """The three queries of the state, each with its title: who possesses box B, what module 2
    possesses, and where box B's transport runs."""
    return (
        (
            "Box B is still possessed by module 1",
            _prefix_lines("cfc", "fci") + "SELECT ?possessor WHERE {\n"
            "  fci:BoxB cfc:hasPossessedWorkpiece ?state .\n"
            "  ?possessor cfc:hasPossessor ?state .\n"
            "} ORDER BY ?possessor",
        ),
        (
            "Module 2 still possesses box A only",
            _prefix_lines("cfc", "fci") + "SELECT ?box WHERE {\n"
            "  fci:Module2 cfc:hasPossessor ?state .\n"
            "  ?box cfc:hasPossessedWorkpiece ?state .\n"
            "} ORDER BY ?box",
        ),
        (
            "The transport of box B is still executed on module 1, and still running",
            _prefix_lines("cfc", "svc", "fci") + "SELECT ?module ?status WHERE {\n"
            "  fci:BoxB_transport cfc:executedOn ?module ;\n"
            "    svc:hasOperationStatus ?status .\n"
            "} ORDER BY ?module",
        ),
    )


def _state(db: TripleStore) -> tuple[Query, ...]:
    """The three queries, answered by `db` now."""
    answered: list[Query] = []
    for title, sparql in state_queries():
        rows = tuple(
            tuple((variable, term["value"]) for variable, term in row.items())
            for row in bindings(db, sparql, convert=False)
        )
        answered.append(Query(title, sparql, rows))
    return tuple(answered)


# -- The recording -------------------------------------------------------------------------- #


def _store_version() -> str:
    """The store's version and edition, from its own REST API, or why it is unknown."""
    url = os.environ.get("TRIPLESTORE_URL") or os.environ.get("GRAPHDB_URL") or ""
    username = (
        os.environ.get("TRIPLESTORE_USERNAME")
        or os.environ.get("GRAPHDB_USERNAME")
        or ""
    )
    password = (
        os.environ.get("TRIPLESTORE_PASSWORD")
        or os.environ.get("GRAPHDB_PASSWORD")
        or ""
    )
    server = GraphDBServer(url, username=username, password=password, size_of_any=True)
    try:
        view = server.version()
    except Exception as error:  # noqa: BLE001 - the version is provenance, not the recording
        return f"unknown: {type(error).__name__}"
    finally:
        server.close()
    answer = view.get("answer")
    if not isinstance(answer, dict):
        return f"unknown: {view.get('error') or view.get('status')}"
    version = str(answer.get("productVersion", ""))
    edition = str(answer.get("productType", ""))
    return f"GraphDB {version} ({edition})".replace(" ()", "")


def record(dataset: str) -> Recording:
    """Make the recording on `dataset`, which it clears, and return what it kept.

    Raises:
        NotRecorded: If the store admitted the handover, or a party did not start or register
            in time, or a layer's copy of the report differs from another's.
    """
    if not triplestore_env_present():
        raise NotRecorded("the triple store's connection variables are not set")
    store = _store_version()
    db = connect(dataset)
    observed = ObservedStore(connect(dataset))
    seed_two_modules(db, transit_seconds=0)
    with _modules(dataset, observed) as served:
        capabilities = _capabilities(db)
        # The setup: box A on module 2, box B on module 1, each transport implementing the
        # capability of the module it is executed on, as a transport does in a run.
        transport_a = place_box(OGM(db=db), BOX_A, on=M2, origin=M1, destination=M2)
        db.triple_add((transport_a, CFC.implementsCapability, capabilities[M2]))
        transport_b = place_box(OGM(db=db), BOX_B, on=M1, origin=M1, destination=M2)
        db.triple_add((transport_b, CFC.implementsCapability, capabilities[M1]))
        before = _state(db)

        middleware = served[M1].middleware
        raised: list[ShaclValidationError] = []
        giver = Giver(
            handover=_observed_handover(middleware, raised),
            resource=M1,
            move=_no_move,
            logger=logging.getLogger(MODULE_LOGGER),
            events=middleware.app.state.handover_events,
        )
        observed.updates.clear()
        with (
            _capturing(ACCESS_MODULE_LOGGER, logging.DEBUG) as access_records,
            _capturing(MODULE_LOGGER, logging.WARNING) as module_records,
        ):
            moment = datetime.now(UTC)
            result = giver.hand_over(
                BOX_B,
                M2,
                on_refusal="the recording keeps the report",
                transport=transport_b,
                entry=reassignment(transport_b, M2, capabilities[M2]),
            )
        after = _state(db)
        events = middleware.app.state.handover_events.after(None)
        refused_writes = middleware.refused_writes

    if result.accepted or result.report is None:
        raise NotRecorded(
            f"the store admitted the handover of {BOX_B.fragment} onto {M2.fragment}, "
            f"so nothing was refused: {result}"
        )
    report = result.report
    # The handover's own update is the one that writes box B. The middleware instance writes
    # on its own beside it, as in a run: its registrations settle after its server has started,
    # and its heartbeat goes on; none of those names the box.
    handover_updates = [u for u in observed.updates if _writes(u, BOX_B)]
    if len(handover_updates) != 1:
        raise NotRecorded(
            f"the handover sent {len(handover_updates)} updates that write "
            f"{BOX_B.fragment} to the store, not one"
        )
    store_answers = [
        r.getMessage() for r in access_records if r.getMessage().endswith(report)
    ]
    if len(store_answers) != 1:
        raise NotRecorded(
            f"the access module logged {len(store_answers)} answers of the store that "
            "end with the report, not one"
        )
    if len(raised) != 1 or raised[0].report != report:
        raise NotRecorded(
            "the report the middleware raised is not the one the module got"
        )
    if len(refused_writes) != 1 or refused_writes[0].report != report:
        raise NotRecorded(
            "the middleware's record of the refusal is not the module's report"
        )
    if (
        len(events) != 1
        or events[0].refusal is None
        or events[0].refusal.report != report
    ):
        raise NotRecorded(
            "the event on the handover event stream is not the module's report"
        )
    warnings = [r.getMessage() for r in module_records]
    if len(warnings) != 1 or not warnings[0].endswith(report):
        raise NotRecorded("the module's warning does not end with the report")
    # An empty answer would make "the same before and after" hold for any state.
    unanswered = [query.title for query in before if not query.rows]
    if unanswered:
        raise NotRecorded(f"no row before the handover: {', '.join(unanswered)}")
    if before != after:
        raise NotRecorded(f"the state changed:\nbefore {before}\nafter {after}")
    return Recording(
        dataset=dataset,
        store=store,
        time=moment,
        capabilities=capabilities,
        update=handover_updates[0],
        report=report,
        status=raised[0].status,
        store_answer=store_answers[0],
        middleware_record=refused_writes[0],
        module_result=result,
        module_warning=warnings[0],
        event=events[0],
        before=before,
        after=after,
    )


# -- The report as RDF ---------------------------------------------------------------------- #


def report_graph(report: str) -> Graph:
    """The validation report `report`, N-Quads as the store sends it, as one graph of its
    triples, whatever graph each quad names."""
    quads = Dataset(default_union=True)
    quads.parse(data=report, format="nquads")
    graph = Graph(bind_namespaces="none")
    for triple in quads.triples((None, None, None)):
        graph.add(triple)
    return graph


def turtle(report: str) -> str:
    """The Turtle copy of `report`, with the prefixes of `PREFIXES`."""
    graph = report_graph(report)
    for prefix, namespace in PREFIXES.items():
        graph.bind(prefix, namespace, replace=True)
    return graph.serialize(format="turtle")


# -- The page ------------------------------------------------------------------------------- #


def _manager() -> Graph:
    graph = Graph(bind_namespaces="none")
    for prefix, namespace in PREFIXES.items():
        graph.bind(prefix, namespace, replace=True)
    return graph


def _n3(term: object, graph: Graph) -> str:
    """`term` as Turtle writes it, with the page's prefixes."""
    node = term if isinstance(term, Node) else URIRef(str(term))
    return node.n3(graph.namespace_manager)


def _short(value: str, graph: Graph) -> str:
    """A value of a query's row, as the page writes it: an IRI by its prefix, a literal as it
    is."""
    if value.startswith(("http://", "https://")):
        return _n3(URIRef(value), graph)
    return f'"{value}"'


def _triples_block(triples: Sequence[Triple], graph: Graph) -> str:
    if not triples:
        return "```turtle\n# none\n```\n"
    lines = [" ".join(_n3(term, graph) for term in triple) + " ." for triple in triples]
    return "```turtle\n" + "\n".join(lines) + "\n```\n"


def _table(query: Query, graph: Graph) -> str:
    """A query's rows as a table; one row that says so where the query answered none."""
    variables = [v for v, _ in query.rows[0]] if query.rows else ["(no row)"]
    head = "| " + " | ".join(f"?{v}" for v in variables) + " |\n"
    head += "|" + "---|" * len(variables) + "\n"
    if not query.rows:
        return head + "| none |\n"
    return head + "".join(
        "| " + " | ".join(_short(value, graph) for _, value in row) + " |\n"
        for row in query.rows
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prefix_declarations(update: AttemptedUpdate) -> str:
    """The prefixes the triples of `update` are written with, declared as SPARQL does."""
    terms = [
        str(term) for triple in (*update.removed, *update.added) for term in triple
    ]
    return "\n".join(
        f"PREFIX {p}: <{ns}>"
        for p, ns in PREFIXES.items()
        if any(term.startswith(ns) for term in terms)
    )


def render_page(recording: Recording, provenance: Provenance, listing: str) -> str:
    """The page of the recording, Markdown for `docs/`, with `listing`, the Turtle copy
    of its report as written beside it, so that the two carry the same blank-node labels."""
    graph = _manager()
    r = recording
    report_lines = r.report.count("\n") + (0 if r.report.endswith("\n") else 1)
    first_warning_line = r.module_warning.splitlines()[0]
    cap_1, cap_2 = _n3(r.capabilities[M1], graph), _n3(r.capabilities[M2], graph)
    state_sections = "".join(
        f"**{q.title}.**\n\n```sparql\n{q.sparql}\n```\n\n{_table(q, graph)}\n"
        for q in r.after
    )
    versions = "".join(
        f"| `{name}` | {version} |\n" for name, version in provenance.versions.items()
    )
    uncommitted = (
        "; also changed and not committed when it ran: "
        + ", ".join(f"`{path}`" for path in provenance.uncommitted)
        if provenance.uncommitted
        else ""
    )
    return f"""# A recorded refusal: a handover onto an occupied module

One refused commit, recorded from a real run of the write path of use case 2: module 1 hands box B
to module 2 while module 2 holds box A, without asking module 2 to reserve itself, as a module
does with `skip-reserve` armed (see [Fault refusals](use-case-2.md#fault-refusals)). The
handover is the middleware's: its entry commits the change of possession and the transport's
reassignment through the Object-Graph Mapper (OGM) and the triple store access module, and the
triple store validates the commit against the shapes of use case 2 at commit. The store refuses it at
`:FlexConveyorModulePossessionShape`, at most one box per module, with the validation report
below, and nothing changes.

The page, the report verbatim ([`skip-reserve.nq`](../tests/reports/skip-reserve.nq)) and
its Turtle copy ([`skip-reserve.ttl`](../tests/reports/skip-reserve.ttl)) are written by one
script, `scripts/record_uc2_refusal.py`, in one run on the dataset it is given, which it
clears. The run is deterministic: a rerun gives the same report, apart from
the blank-node labels the store gives it, and `tests/test_uc2_recorded_refusal_live.py` reruns
it and compares the two as RDF graphs.

**The setup.** The seed of two modules, module 1 connected east to module 2, each served as a
middleware instance as in a run, so that each registers its BoxTransportCapability ({cap_1} and
{cap_2}). Then, written directly as a seed writes, box A on module 2, with its transport executed
on module 2, and box B on module 1, with its transport from module 1 to module 2, `running` and
executed on module 1; each transport implements the capability of the module it is executed on.
Then module 1's giving side hands box B to module 2, with the entry a hop commits, and without a
reservation.

## Attempted commit

The handover's entry is one commit of three changes: the change of possession, which points box
B at module 2's possession state, and the reassignment of box B's transport to module 2 and to
module 2's capability. The OGM reads each changed instance, and hands the triple
store access module one update, which the store applies whole or not at all. The update removes

{_triples_block(r.update.removed, graph)}
and adds

{_triples_block(r.update.added, graph)}
with the prefixes

```sparql
{_prefix_declarations(r.update)}
```

## Validation report

The store answered the update with this report, {report_lines} lines of N-Quads, and committed
nothing of it:

```nquads
{r.report.rstrip()}
```

It names `:FlexConveyorModulePossessionShape` as `sh:sourceShape`,
`sh:MaxCountConstraintComponent` as the constraint, and `fci:Module2` as `sh:focusNode`.

**As Turtle, as the paper's Listing 1 prints it.** The same graph, with the prefixes of use case
2, as the Turtle copy [`skip-reserve.ttl`](../tests/reports/skip-reserve.ttl) holds it; only
the blank-node labels differ from the N-Quads above:

```turtle
{listing.rstrip()}
```

**As the store returned it, and as the module received it.** The report above is the same text,
{len(r.report)} characters, SHA-256 `{_digest(r.report)}`, at every layer of the write path,
byte for byte:

1. The triple store access module's own record of the store's answer, which it logs at DEBUG:
   the status, {r.status}, and the body unchanged, so that the record ends with the report.
2. The error the access module raises, `ShaclValidationError`, whose `report` is the body of the
   store's answer, as the middleware raised it out of its handover.
3. The middleware's record of the refused write, `refused_writes`, with the verb
   `{r.middleware_record.verb}` and the rules `{", ".join(r.middleware_record.rules)}`.
4. The answer the module's giving side returns, `StepResult.report`, with the reason
   `{r.module_result.reason}`, and the module's warning, whose first line reads
   `{first_warning_line}` and whose last lines are the report.
5. The event `{r.event.kind}` on module 1's handover event stream, which the monitor shows, with
   the report in its refusal.

The recording checked the five copies against each other, and keeps the first one verbatim as
[`skip-reserve.nq`](../tests/reports/skip-reserve.nq).

## State after the refusal

The same three queries, answered before the handover and after it, give the same rows: box B is
still possessed by module 1, module 2 still possesses only box A, and the transport of box B is
still executed on module 1, and still running.

{state_sections}
## Provenance

| | |
|---|---|
| jms_kapps | commit `{provenance.commit}`; the three files of the recording were written by this run and committed after it{uncommitted} |
{versions}| triple store | {provenance.store} |
| dataset | a disposable dataset, which the run cleared first |
| recorded | {provenance.time.strftime("%Y-%m-%d %H:%M:%S UTC")} |
| command | `{provenance.command}` |

The versions are those of `uv.lock`, which `uv sync --locked` installs.

## Make it again

On a triple store of your own, with `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD` set, and with the name of a dataset that may be cleared in place of
`<dataset>`:

```
{COMMAND}
```

It rewrites the page, the report and its Turtle copy. The live test makes the recording again
without writing them, and compares its report with the committed one as RDF graphs:

```
TRIPLESTORE_ALLOW_DESTRUCTIVE=1 uv run pytest tests/test_uc2_recorded_refusal_live.py --dataset <dataset>
```
"""


# -- The provenance and the files ----------------------------------------------------------- #


def _git(root: Path, *arguments: str) -> str:
    """What `git` prints for `arguments` in the checkout at `root`, as it prints it."""
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def locked_versions(lock: Path) -> dict[str, str]:
    """The version of each of the three libraries, from the lock file `lock`."""
    packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    versions = {
        p["name"]: str(p["version"]) for p in packages if p.get("name") in LIBRARIES
    }
    missing = [name for name in LIBRARIES if name not in versions]
    if missing:
        raise NotRecorded(f"{lock} does not lock {', '.join(missing)}")
    return {name: versions[name] for name in LIBRARIES}


def provenance_of(root: Path, recording: Recording, command: str) -> Provenance:
    """The provenance of `recording`, read from the checkout at `root`."""
    # Each line of the porcelain status is two status letters, a space and the path.
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    written = {str(path) for path in (PAGE, REPORT, TURTLE)}
    uncommitted = tuple(
        sorted(
            line[3:] for line in status.splitlines() if line and line[3:] not in written
        )
    )
    return Provenance(
        commit=_git(root, "rev-parse", "HEAD").strip(),
        uncommitted=uncommitted,
        versions=locked_versions(root / "uv.lock"),
        store=recording.store,
        time=recording.time,
        command=command,
    )


def write_recording(root: Path, recording: Recording, command: str) -> list[Path]:
    """Write the three files of `recording` below `root`, and return their paths."""
    for path in (REPORT, TURTLE, PAGE):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / REPORT).write_text(recording.report, encoding="utf-8")
    listing = turtle(recording.report)
    (root / TURTLE).write_text(listing, encoding="utf-8")
    page = render_page(recording, provenance_of(root, recording, command), listing)
    (root / PAGE).write_text(page, encoding="utf-8")
    return [root / REPORT, root / TURTLE, root / PAGE]


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="record_uc2_refusal.py",
        description=(
            "Record the triple store refusing a handover onto an occupied module, and write "
            f"{PAGE}, {REPORT} and {TURTLE}."
        ),
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="the dataset to record on, which the recording clears",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="the checkout to write the files into (default: the one this script is in)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse(argv)
    logging.basicConfig(level=logging.WARNING)
    command = COMMAND
    try:
        recording = record(arguments.dataset)
        written = write_recording(arguments.root, recording, command)
    except NotRecorded as error:
        print(f"record_uc2_refusal.py: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
