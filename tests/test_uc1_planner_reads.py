"""The reads of the planner: the grinders it finds, the Screws it offers, and an operation's
outcome.

The grinders are found with one SPARQL query, which these tests fake with a Queryable that
answers `bindings`; the Screws and the outcome are object-graph projections, so these tests fake
the mapper's fetch. What the reads ask for, and what they make of the answer, is what is under
test. Neither needs a store.
"""

from __future__ import annotations

from kapps_semantic_middleware.vocabulary import CORE_NS, SVC
from kapps_triplestore_interface import IRI

from jms_kapps.uc1.planner_reads import (
    CONTAINS_WORKPIECE,
    CONTENTS_SCOPE,
    FAILURE_SCOPE,
    GRINDERS_QUERY,
    OPERATION_LINK_SCOPE,
    OUTCOME_SCOPE,
    ScrewOnProduct,
    read_failure_reason,
    read_grinders,
    read_has_operation,
    read_offer,
    read_outcome,
    read_screws,
)
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

PRODUCT = IRI("AngleGrinder1", base=JMS_NS)
PRODUCT_2 = IRI("AngleGrinder2", base=JMS_NS)
OPERATION = IRI("UnscrewingOperation1", base=JMS_NS)
SCREW_1 = IRI("Screw1", base=JMS_NS)
SCREW_2 = IRI("Screw2", base=JMS_NS)


def ref(name: str):
    """A nested value as the mapper gives it: a node that carries its IRI as `id`."""
    return type("Ref", (), {"id": str(IRI(name, base=JMS_NS))})()


class Queryable:
    """A triple store whose `query` answers every SELECT with `rows`, in the form of a SELECT's
    results, and records the query."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []
        self.queries: list[str] = []

    def query(self, query: str, **options: object) -> dict:
        self.queries.append(query)
        return {"results": {"bindings": self._rows}}


class Mapper:
    """A mapper whose fetch answers from `data` per instance, records the scope it was asked for,
    and raises `missing` where it is given one. It carries a `db` that is the `Queryable`."""

    def __init__(
        self,
        data: dict[IRI, dict | None] | None = None,
        missing: Exception | None = None,
        db: Queryable | None = None,
    ) -> None:
        self._data = data or {}
        self._missing = missing
        self.db = db or Queryable()
        self.scopes: list[object] = []
        self.asked: list[IRI] = []

    def fetch(self, *, instance_iri, class_scope=None, **rest):  # type: ignore[no-untyped-def]
        self.asked.append(instance_iri)
        self.scopes.append(class_scope)
        if self._missing is not None:
            raise self._missing
        return type("Node", (), {"data": self._data.get(instance_iri)})()


def a_product_holding(*names: str, db: Queryable | None = None) -> Mapper:
    return Mapper(
        {
            PRODUCT: {CONTAINS_WORKPIECE: [ref(name) for name in names]},
            SCREW_1: {},
            SCREW_2: {},
        },
        db=db,
    )


def test_the_core_property_is_built_from_the_middleware_s_namespace() -> None:
    """The middleware's vocabulary does not export `cfc:containsWorkpiece`, and this repository
    spells no Core IRI of its own, so the two cannot drift apart."""
    assert str(CONTAINS_WORKPIECE) == f"{CORE_NS}containsWorkpiece"


def test_the_grinders_query_asks_for_the_angle_grinder_class_in_order() -> None:
    assert GRINDERS_QUERY == (
        f"SELECT ?grinder WHERE {{ ?grinder a <{JMS.AngleGrinder}> }} ORDER BY ?grinder"
    )


def test_read_grinders_returns_the_rows_in_order_and_logs_the_query() -> None:
    """The planner reads what it works on from the graph: no `--product`. The mapper cannot
    enumerate a class, so this is the one SPARQL read, and the query is on one line in the log."""
    db = Queryable(
        [
            {"grinder": PRODUCT_2},
            {"grinder": PRODUCT},
        ]
    )

    assert read_grinders(db) == [PRODUCT_2, PRODUCT]
    assert db.queries == [GRINDERS_QUERY]


def test_read_grinders_of_an_empty_graph_gives_no_grinders() -> None:
    """An empty graph gives an empty offer; it does not raise."""
    assert read_grinders(Queryable()) == []


def test_every_screw_comes_back_with_the_product_it_sits_in() -> None:
    """A press names the operation's workpiece from this, without reading the graph again."""
    mapper = a_product_holding("Screw1", "Screw2")

    assert read_screws(mapper, PRODUCT) == [
        ScrewOnProduct(SCREW_1, PRODUCT),
        ScrewOnProduct(SCREW_2, PRODUCT),
    ]


def test_the_read_is_one_projection_from_the_product() -> None:
    """The planner reads each grinder's Screws with the mapper, one projection per grinder."""
    mapper = a_product_holding("Screw1")

    read_screws(mapper, PRODUCT)

    assert mapper.asked == [PRODUCT]
    assert mapper.scopes == [CONTENTS_SCOPE]


def test_the_screws_come_back_in_the_same_order_every_time() -> None:
    """The mapper gives them in no order of its own, so the select offers the same Screws in the
    same order on every start, which is what makes a demo reproducible."""
    forwards = a_product_holding("Screw1", "Screw2", "Screw9")
    jumbled = Mapper(
        {
            PRODUCT: {
                CONTAINS_WORKPIECE: [ref("Screw9"), ref("Screw1"), ref("Screw2")]
            },
            SCREW_1: {},
            SCREW_2: {},
            IRI("Screw9", base=JMS_NS): {},
        }
    )

    assert read_screws(forwards, PRODUCT) == read_screws(jumbled, PRODUCT)


def test_one_workpiece_named_twice_is_offered_once() -> None:
    mapper = a_product_holding("Screw1", "Screw1")

    assert read_screws(mapper, PRODUCT) == [ScrewOnProduct(SCREW_1, PRODUCT)]


def test_a_product_that_holds_nothing_gives_no_screws() -> None:
    assert read_screws(Mapper({PRODUCT: {}}), PRODUCT) == []
    assert read_screws(Mapper({PRODUCT: None}), PRODUCT) == []


def test_a_product_the_graph_does_not_hold_raises() -> None:
    """At the start, loudly: a planner that reads a grinder that is not there must not come up
    offering nothing."""
    mapper = Mapper(missing=RuntimeError("Could not determine class IRI for instance"))

    try:
        read_screws(mapper, PRODUCT)
    except RuntimeError as refusal:
        assert "class IRI" in str(refusal)
    else:  # pragma: no cover
        raise AssertionError("a missing product must raise")


def test_an_operation_gives_its_outcome_by_its_local_name() -> None:
    """The local name is what the dashboard shows."""
    mapper = Mapper({OPERATION: {JMS.hasOutcome: [ref("ScrewIntact")]}})
    mapper._data = {
        OPERATION: {JMS.hasOutcome: [type("Ref", (), {"id": str(JMS.ScrewIntact)})()]}
    }

    assert read_outcome(mapper, OPERATION) == "ScrewIntact"


def test_the_outcome_is_read_through_a_class_scope() -> None:
    """Never at the FULL level: a load under `cfc:Operation` recurses (kapps_ogm 35)."""
    mapper = Mapper({OPERATION: None})

    read_outcome(mapper, OPERATION)

    assert mapper.scopes == [OUTCOME_SCOPE]


def test_an_operation_with_no_outcome_gives_none() -> None:
    """An operation that ended `failed` holds none."""
    assert read_outcome(Mapper({OPERATION: None}), OPERATION) is None
    assert read_outcome(Mapper({OPERATION: {}}), OPERATION) is None
    assert read_outcome(Mapper({OPERATION: {JMS.hasOutcome: []}}), OPERATION) is None


def test_a_failed_operation_gives_its_reason() -> None:
    """The reason is what the screwing resource or the detector wrote as `svc:executionResult`."""
    mapper = Mapper({OPERATION: {SVC.executionResult: ["NOT_CLASSIFIED: no curves"]}})

    assert read_failure_reason(mapper, OPERATION) == "NOT_CLASSIFIED: no curves"
    assert mapper.scopes == [FAILURE_SCOPE]


def test_several_reasons_are_joined() -> None:
    mapper = Mapper({OPERATION: {SVC.executionResult: ["first", "second"]}})

    assert read_failure_reason(mapper, OPERATION) == "first; second"


def test_an_operation_with_no_reason_gives_none() -> None:
    assert read_failure_reason(Mapper({OPERATION: None}), OPERATION) is None
    assert read_failure_reason(Mapper({OPERATION: {}}), OPERATION) is None
    assert (
        read_failure_reason(Mapper({OPERATION: {SVC.executionResult: []}}), OPERATION)
        is None
    )


def test_a_screw_with_an_operation_has_operationalink() -> None:
    """Whatever the operation's status, `:hasOperation` says the Screw is taken."""
    mapper = Mapper({SCREW_1: {JMS.hasOperation: [ref("UnscrewingOperation1")]}})

    assert read_has_operation(mapper, SCREW_1) is True


def test_a_screw_without_an_operation_has_no_operationalink() -> None:
    assert read_has_operation(Mapper({SCREW_1: None}), SCREW_1) is False
    assert read_has_operation(Mapper({SCREW_1: {}}), SCREW_1) is False
    assert (
        read_has_operation(Mapper({SCREW_1: {JMS.hasOperation: []}}), SCREW_1) is False
    )


def test_has_operation_is_read_through_a_class_scope() -> None:
    """One fetch per Screw, with the one chain the Screw needs."""
    mapper = Mapper({SCREW_1: {}})

    read_has_operation(mapper, SCREW_1)

    assert mapper.asked == [SCREW_1]
    assert mapper.scopes == [OPERATION_LINK_SCOPE]


def test_the_offer_lists_every_screw_without_an_operation_in_product_order() -> None:
    """The offer is what a press may take: every Screw of the grinders it finds that has no
    operation yet. The grinders come from the SPARQL query, in the graph's order."""
    mapper = Mapper(
        {
            PRODUCT: {CONTAINS_WORKPIECE: [ref("Screw1"), ref("Screw2")]},
            PRODUCT_2: {CONTAINS_WORKPIECE: [ref("Screw3")]},
            SCREW_1: {},
            SCREW_2: {JMS.hasOperation: [ref("UnscrewingOperation1")]},
            IRI("Screw3", base=JMS_NS): {},
        },
        db=Queryable([{"grinder": PRODUCT_2}, {"grinder": PRODUCT}]),
    )

    assert read_offer(mapper) == [
        ScrewOnProduct(IRI("Screw3", base=JMS_NS), PRODUCT_2),
        ScrewOnProduct(SCREW_1, PRODUCT),
    ]


def test_the_offer_reads_from_each_product_and_each_screw() -> None:
    """The mapper types a nested object by its range, so one chain from the product would not
    reach `:hasOperation`; the offer reads per Screw instead. The grinders come from the one
    SPARQL query."""
    mapper = Mapper(
        {
            PRODUCT: {CONTAINS_WORKPIECE: [ref("Screw1")]},
            SCREW_1: {},
        },
        db=Queryable([{"grinder": PRODUCT}]),
    )

    read_offer(mapper)

    assert mapper.db.queries == [GRINDERS_QUERY]
    assert mapper.asked == [PRODUCT, SCREW_1]
    assert mapper.scopes == [CONTENTS_SCOPE, OPERATION_LINK_SCOPE]


def test_an_empty_graph_gives_an_empty_offer() -> None:
    """The seed clears the dataset; before it, or with nothing there, the planner offers nothing
    rather than raising."""
    mapper = Mapper(db=Queryable())

    assert read_offer(mapper) == []
