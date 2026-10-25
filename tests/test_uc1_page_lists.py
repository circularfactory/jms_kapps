"""The three lists of UC1's page, Operations, Learned values and Measured positions, as their
background read finds them.

Each list comes from one SPARQL query, which `PageLists` sends in its background read and keeps
exactly as it handed it to the store. Most tests run the queries on rdflib's SPARQL engine, which
infers nothing, over a small graph that holds what the planner, the anomaly detector and the
learner commit; the others use a stand-in that answers fixed rows and keeps every query text it
receives. Nothing reaches a real store. The page's routes and rendering are in `test_uc1_page.py`,
the page in a browser in `test_uc1_page_browser.py`, and the queries on a seeded dataset after both
presses in `test_uc1_run_live.py`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from _wait import eventually
from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface import IRI
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

from jms_kapps.uc1.page_lists import (
    LEARNED_VALUES,
    MEASURED_POSITIONS,
    OPERATIONS,
    LearnedValue,
    MeasuredPositionRow,
    OperationRow,
    PageLists,
)
from jms_kapps.uc1.uncertainty import measurement_literal
from jms_kapps.uc1.vocabulary import JMS, JMS_NS, PROV, UQ

PLANNER = IRI("Planner1", base=JMS_NS)
DETECTOR = IRI("AnomalyDetector1", base=JMS_NS)
SCREW_1 = IRI("Screw1", base=JMS_NS)
SCREW_2 = IRI("Screw2", base=JMS_NS)


class InMemoryStore:
    """A store on rdflib's own SPARQL engine, which infers nothing: it answers a query over
    `graph` as SPARQL's JSON results, and keeps every query text it receives. While `away` holds
    a text, it raises that instead."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.received: list[str] = []
        self.away: str | None = None

    def query(self, query: str, *, convert_bindings: bool = False) -> Any:
        self.received.append(query)
        if self.away is not None:
            raise ConnectionError(self.away)
        answer = self.graph.query(query).serialize(format="json")
        assert answer is not None
        return json.loads(answer)


def _add(graph: Graph, subject: str, predicate: str, value: Any) -> None:
    graph.add(
        (
            URIRef(subject),
            URIRef(predicate),
            value if isinstance(value, Literal) else URIRef(value),
        )
    )


def seeded() -> Graph:
    """The Screw type M4 with its seeded maximum dynamic torque, and the two Screws of it."""
    graph = Graph()
    _add(graph, JMS.M4, str(RDF.type), JMS.ScrewType)
    _add(graph, JMS.M4, JMS.hasMaxDynamicTorque, Literal(1.5, datatype=XSD.double))
    for screw in (SCREW_1, SCREW_2):
        _add(graph, screw, str(RDF.type), JMS.Screw)
        _add(graph, screw, JMS.hasScrewType, JMS.M4)
    return graph


def issued(graph: Graph, name: str, screw: IRI, status: str) -> IRI:
    """An unscrewing operation on `screw` that the planner issued, with `status`."""
    operation = IRI(name, base=JMS_NS)
    _add(graph, operation, str(RDF.type), JMS.UnscrewingOperation)
    _add(graph, operation, JMS.concernsScrew, screw)
    _add(graph, operation, SVC.issuedBy, PLANNER)
    _add(graph, operation, SVC.hasOperationStatus, Literal(status))
    return operation


def classified(graph: Graph, operation: IRI, outcome: str) -> None:
    """What the anomaly detector commits on `operation`: the outcome, the parameter copy it used,
    the classification that generated the copy, and the detector as that classification's agent."""
    copy = f"{operation}_copy"
    classification = f"{operation}_classification"
    _add(graph, operation, JMS.hasDetectionResult, IRI(outcome, base=JMS_NS))
    _add(graph, operation, JMS.classifiedWith, copy)
    _add(graph, copy, str(RDF.type), JMS.ParameterCopy)
    _add(graph, copy, PROV.wasGeneratedBy, classification)
    _add(graph, classification, str(RDF.type), PROV.Activity)
    _add(graph, classification, PROV.wasAssociatedWith, DETECTOR)


def after_both_presses() -> Graph:
    """The graph after Unscrew on Screw1 and on Screw2: Screw1 done and ScrewIntact, Screw2 failed
    and MissingScrew, and the learner's new value for M4."""
    graph = seeded()
    first = issued(graph, "UnscrewingOperation_b1", SCREW_1, "done")
    classified(graph, first, "ScrewIntact")
    second = issued(graph, "UnscrewingOperation_a2", SCREW_2, "failed")
    classified(graph, second, "MissingScrew")
    _add(graph, second, SVC.hasExecutionResult, Literal("ANOMALY: MissingScrew"))
    graph.remove((URIRef(JMS.M4), URIRef(JMS.hasMaxDynamicTorque), None))
    _add(graph, JMS.M4, JMS.hasMaxDynamicTorque, Literal(1.8, datatype=XSD.double))
    return graph


def saved(graph: Graph, subject: IRI, estimate_of: str, variable_of: str, literal: Literal) -> None:
    """A measured position of `subject` saved as an uncertainty quantification: its best estimate
    and a random variable whose distribution is `literal`."""
    variable = f"{subject}_variable"
    _add(graph, subject, estimate_of, Literal(12.25, datatype=XSD.double))
    _add(graph, subject, variable_of, variable)
    _add(graph, variable, str(RDF.type), UQ.RandomVariable)
    _add(graph, variable, UQ.hasDistribution, literal)


def with_saved_positions() -> Graph:
    """After both presses with the mode on: Screw1's head position and the failed operation's
    approach end position, each saved with its literal; and an operation that failed with the
    mode off, with its end position and no random variable."""
    graph = after_both_presses()
    literal = measurement_literal(12.25, 0.1)
    saved(
        graph,
        SCREW_1,
        JMS.hasMeasuredScrewHeadPosition,
        JMS.hasScrewHeadPositionVariable,
        literal,
    )
    saved(
        graph,
        IRI("UnscrewingOperation_a2", base=JMS_NS),
        JMS.hasMeasuredApproachEndPosition,
        JMS.hasApproachEndPositionVariable,
        literal,
    )
    off = issued(graph, "UnscrewingOperation_d4", SCREW_2, "failed")
    _add(graph, off, JMS.hasMeasuredApproachEndPosition, Literal(3.5, datatype=XSD.double))
    return graph


def test_measured_positions_lists_each_saved_position_with_its_standard_deviation_and_literal():
    """One row for Screw1's head position and one for the failed operation's end position, with
    the standard deviation as the square root of the literal's variance. An end position without
    a random variable, written with the mode off, has no row."""
    lists = PageLists(InMemoryStore(with_saved_positions()))

    lists.read()

    shown = lists.last().measured_positions
    assert shown.error is None
    literal = str(measurement_literal(12.25, 0.1))
    assert shown.rows == [
        MeasuredPositionRow(
            subject="Screw1",
            position="screw-head position",
            estimate="12.25",
            standard_deviation="0.1",
            literal=literal,
        ),
        MeasuredPositionRow(
            subject="UnscrewingOperation_a2",
            position="approach end position",
            estimate="12.25",
            standard_deviation="0.1",
            literal=literal,
        ),
    ]


def test_a_literal_that_does_not_parse_shows_with_no_standard_deviation():
    graph = after_both_presses()
    malformed = Literal('{"n_components":1}', datatype=URIRef(UQ.gmmLiteral))
    saved(
        graph,
        SCREW_1,
        JMS.hasMeasuredScrewHeadPosition,
        JMS.hasScrewHeadPositionVariable,
        malformed,
    )
    lists = PageLists(InMemoryStore(graph))

    lists.read()

    shown = lists.last().measured_positions
    assert shown.error is None
    [row] = shown.rows
    assert row.standard_deviation is None
    assert row.literal == '{"n_components":1}'


def test_a_run_with_the_mode_off_lists_no_measured_position():
    lists = PageLists(InMemoryStore(after_both_presses()))

    lists.read()

    shown = lists.last().measured_positions
    assert (shown.error, shown.rows) == (None, [])
    assert shown.read_at is not None


def test_operations_lists_each_operation_with_its_screw_issuer_status_outcome_reason_and_decider():
    """One row for each operation, ordered by its Screw: the issuer is the planner's resource,
    and the deciding service of both is the anomaly detector, the agent of the classification
    that generated the values the operation used."""
    lists = PageLists(InMemoryStore(after_both_presses()))

    lists.read()

    operations = lists.last().operations
    assert operations.error is None
    assert operations.rows == [
        OperationRow(
            operation="UnscrewingOperation_b1",
            screw="Screw1",
            issuer=str(PLANNER),
            status="done",
            outcome="ScrewIntact",
            reason=None,
            decided_by=str(DETECTOR),
        ),
        OperationRow(
            operation="UnscrewingOperation_a2",
            screw="Screw2",
            issuer=str(PLANNER),
            status="failed",
            outcome="MissingScrew",
            reason="ANOMALY: MissingScrew",
            decided_by=str(DETECTOR),
        ),
    ]


def test_an_operation_that_was_never_classified_has_its_reason_and_no_decider():
    """An operation that failed before the detector classified it holds no parameter copy: its
    row has no outcome and no deciding service, and says why it failed."""
    graph = seeded()
    operation = issued(graph, "UnscrewingOperation_c3", SCREW_1, "failed")
    _add(
        graph,
        operation,
        SVC.hasExecutionResult,
        Literal("the anomaly detector could not be reached"),
    )
    lists = PageLists(InMemoryStore(graph))

    lists.read()

    [row] = lists.last().operations.rows
    assert row.outcome is None
    assert row.decided_by is None
    assert row.reason == "the anomaly detector could not be reached"


def test_learned_values_shows_the_value_at_the_start_and_the_value_now():
    """The value at the start is the one the first read that succeeded found; the value now is
    the latest read's, after the learner committed its own."""
    store = InMemoryStore(seeded())
    lists = PageLists(store)
    lists.read()

    store.graph = after_both_presses()
    lists.read()

    learned = lists.last().learned_values
    assert learned.error is None
    assert learned.rows == [LearnedValue(screw_type="M4", now="1.8", at_start="1.5")]


def test_a_first_read_that_finds_no_screw_type_sets_no_value_at_the_start():
    """The value at the start is taken from the first read that finds the Screw types: a read
    of a dataset that holds none yet keeps it open."""
    store = InMemoryStore(Graph())
    lists = PageLists(store)
    lists.read()

    store.graph = seeded()
    lists.read()

    assert lists.last().learned_values.rows == [
        LearnedValue(screw_type="M4", now="1.5", at_start="1.5")
    ]


def test_each_list_keeps_its_query_exactly_as_the_store_received_it():
    store = InMemoryStore(after_both_presses())
    lists = PageLists(store)

    lists.read()

    found = lists.last()
    assert found.operations.query == OPERATIONS
    assert found.learned_values.query == LEARNED_VALUES
    assert found.measured_positions.query == MEASURED_POSITIONS
    assert store.received == [OPERATIONS, LEARNED_VALUES, MEASURED_POSITIONS]


def test_each_query_declares_its_own_prefixes():
    """Each query runs unchanged in the store's workbench: every prefix it uses, it declares."""
    for query in (OPERATIONS, LEARNED_VALUES, MEASURED_POSITIONS):
        declared = {
            line.split()[1].rstrip(":")
            for line in query.splitlines()
            if line.startswith("PREFIX ")
        }
        body = "\n".join(
            line for line in query.splitlines() if not line.startswith(("PREFIX ", "#"))
        )
        used = {
            word.split(":")[0]
            for word in body.replace("(", " ").replace(")", " ").split()
            if ":" in word and not word.startswith(("<", '"', "?"))
        }
        assert used <= declared, (used - declared, query)


def test_last_sends_nothing():
    store = InMemoryStore(after_both_presses())
    lists = PageLists(store)
    lists.read()
    sent = len(store.received)

    for _ in range(20):
        lists.last()

    assert len(store.received) == sent


def test_a_read_that_fails_keeps_the_last_answer_and_says_why():
    store = InMemoryStore(after_both_presses())
    lists = PageLists(store)
    lists.read()
    before = lists.last()

    store.away = "the store is away"
    lists.read()

    after = lists.last()
    for shown, kept in [
        (after.operations, before.operations),
        (after.learned_values, before.learned_values),
        (after.measured_positions, before.measured_positions),
    ]:
        assert shown.error == "the store is away"
        assert shown.rows == kept.rows
        assert shown.read_at == kept.read_at
        assert shown.query == kept.query


def test_before_the_first_read_each_list_is_empty_and_has_no_query():
    found = PageLists(InMemoryStore(seeded())).last()

    for shown in (found.operations, found.learned_values, found.measured_positions):
        assert shown.query is None
        assert shown.rows == []
        assert shown.read_at is None
        assert shown.error is None


def test_the_background_read_goes_on_until_its_stop():
    store = InMemoryStore(after_both_presses())
    lists = PageLists(store, interval=0.05)

    lists.start()
    try:
        eventually(lambda: len(store.received) >= 9)
    finally:
        lists.stop()
    # A read under way at the stop may still arrive.
    time.sleep(0.2)
    sent = len(store.received)
    time.sleep(0.3)

    assert len(store.received) == sent
    assert set(store.received) == {OPERATIONS, LEARNED_VALUES, MEASURED_POSITIONS}
