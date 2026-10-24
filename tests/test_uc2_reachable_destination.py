"""`jms-uc2 reachable-destination` plays its scripted sequence, prints each step with the store's answer, and
clears the dataset it was given, which must be a disposable one.

Without a store: a stand-in refuses the writes that a store seeded with the SHACL-SPARQL shapes
refuses, with a report that names the shape, and records every write. `test_uc2_shapes.py` proves
the rule on a live store; the landing runs the command on one.
"""

from __future__ import annotations

import io

import pytest
from kapps_semantic_middleware.vocabulary import MES
from kapps_triplestore_interface import ShaclValidationError
from rdflib import Graph
from rdflib.namespace import RDF, RDFS, SH

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc2 import __main__ as main
from jms_kapps.uc2 import reachable_destination
from jms_kapps.uc2.reachable_destination import demo
from jms_kapps.uc2.seed import MODULES
from jms_kapps.uc2.vocabulary import FC, FCI_NS, connection_iri, module_iri

M1, M2 = MODULES
M3 = module_iri(3)
RULE = str(FC.ReachableDestinationShape)
OTHER_RULE = str(FC.BoxHasOnePossessorShape)
SPARQL = Graph().parse(
    data=read_asset(
        "jms_kapps.uc2", "shapes", "flexconveyor_usecase_sparql_shapes.ttl"
    ),
    format="turtle",
)
MESSAGE = str(next(SPARQL.objects(None, SH.message)))


def report_of(*shapes: str) -> str:
    """A report as the store writes one (N-Quads), with one result per shape in `shapes`, each with
    the shape's message."""
    lines = []
    for n, shape in enumerate(shapes):
        lines += [
            f"_:report <{SH.result}> _:result{n} .",
            f"_:result{n} <{SH.focusNode}> <{FCI_NS}Focus{n}> .",
            f"_:result{n} <{SH.sourceShape}> <{shape}> .",
            f'_:result{n} <{SH.resultMessage}> "{MESSAGE}" .',
        ]
    return "\n".join(lines) + "\n"


class StandIn:
    """A dataset that refuses a write as `refuses` says, with the report it returns, and records
    every write, and each seed and clear of the demonstration.

    `refuses` is given the opening of the third module's box (the one that has a destination of
    module 3) and the removal of the connection, as `"unreachable-destination"` and
    `"connection-removal"`.
    """

    def __init__(self, refuses: dict[str, str | None]) -> None:
        self.refuses = refuses
        self.done: list[str] = []

    def query(self, query, update=False, **kwargs):
        return {"results": {"bindings": []}}

    def triples_add(self, triples) -> bool:
        triples = list(triples)
        step = (
            "unreachable-destination"
            if any(p == FC.hasDestination and o == M3 for _, p, o in triples)
            else "admitted-transport"
        )
        self.done.append(step)
        self._answer(step)
        return True

    def triple_delete(self, triple) -> bool:
        assert triple == (M1, FC.hasConnection, connection_iri("Module1", "East"))
        self.done.append("connection-removal")
        self._answer("connection-removal")
        return True

    def _answer(self, step: str) -> None:
        report = self.refuses.get(step)
        if report is not None:
            raise ShaclValidationError("refused", report, status=400)


@pytest.fixture(autouse=True)
def no_seed(monkeypatch):
    def seed(db, *, transit_seconds, sparql_shapes=False):
        assert sparql_shapes
        db.done.append("seed")
        return M1, M2, M3

    monkeypatch.setattr(reachable_destination, "seed_three_modules", seed)
    monkeypatch.setattr(
        reachable_destination, "clear_dataset", lambda db: db.done.append("clear")
    )


def as_the_store() -> StandIn:
    """What a store seeded with both shape files answers: it refuses the unreachable destination
    by the rule alone, and the removed connection by the rule."""
    return StandIn(
        {
            "unreachable-destination": report_of(RULE),
            "connection-removal": report_of(RULE),
        }
    )


def test_the_demo_seeds_then_opens_a_transport_places_a_box_and_removes_the_connection() -> (
    None
):
    store = as_the_store()

    assert demo(store, out=io.StringIO()) == 0

    assert store.done == [
        "seed",
        "admitted-transport",
        "unreachable-destination",
        "connection-removal",
    ]


def test_the_demo_prints_each_step_with_the_shape_s_message_and_the_report() -> None:
    out = io.StringIO()
    store = as_the_store()

    demo(store, out=out)

    text = out.getvalue()
    for number in range(1, 5):
        assert f"{number}. " in text
    assert "the store: admitted" in text
    assert text.count("the store: refused") == 2
    assert text.count(MESSAGE) >= 2
    # The report as the interface returns it, verbatim.
    for line in report_of(RULE).splitlines():
        assert f"     {line}" in text
    assert "at every commit" in text


def test_the_demo_exits_1_and_names_step_2_when_the_store_refuses_the_first_transport() -> (
    None
):
    store = as_the_store()
    store.refuses["admitted-transport"] = report_of(RULE)
    out = io.StringIO()

    assert demo(store, out=out) == 1
    assert "step 2 did not behave as stated" in out.getvalue()


def test_the_demo_exits_1_and_names_step_3_when_the_store_admits_the_unreachable_transport() -> (
    None
):
    store = StandIn({"connection-removal": report_of(RULE)})
    out = io.StringIO()

    assert demo(store, out=out) == 1
    assert "step 3 did not behave as stated" in out.getvalue()
    assert "step 4 did not behave as stated" not in out.getvalue()


def test_the_demo_exits_1_and_names_step_4_when_the_store_admits_the_removal() -> None:
    store = StandIn({"unreachable-destination": report_of(RULE)})
    out = io.StringIO()

    assert demo(store, out=out) == 1
    assert "step 4 did not behave as stated" in out.getvalue()
    assert "step 3 did not behave as stated" not in out.getvalue()


def test_the_demo_names_every_step_that_failed() -> None:
    out = io.StringIO()

    assert demo(StandIn({}), out=out) == 1
    assert "step 3 did not behave as stated" in out.getvalue()
    assert "step 4 did not behave as stated" in out.getvalue()


@pytest.mark.parametrize("shapes", [(OTHER_RULE,), (RULE, OTHER_RULE)])
def test_step_3_counts_a_refusal_only_when_the_rule_alone_is_named(shapes) -> None:
    store = as_the_store()
    store.refuses["unreachable-destination"] = report_of(*shapes)
    out = io.StringIO()

    assert demo(store, out=out) == 1
    assert "step 3 did not behave as stated" in out.getvalue()
    assert "step 4 did not behave as stated" not in out.getvalue()


def test_step_4_counts_a_refusal_only_when_it_names_the_rule() -> None:
    store = as_the_store()
    store.refuses["connection-removal"] = report_of(OTHER_RULE)
    out = io.StringIO()

    assert demo(store, out=out) == 1
    assert "step 4 did not behave as stated" in out.getvalue()
    assert "step 3 did not behave as stated" not in out.getvalue()


def test_step_4_accepts_a_report_that_names_the_rule_beside_another() -> None:
    store = as_the_store()
    store.refuses["connection-removal"] = report_of(RULE, OTHER_RULE)

    assert demo(store, out=io.StringIO()) == 0


def test_the_command_plays_the_demo_on_the_dataset_it_is_given_and_clears_it(
    monkeypatch,
) -> None:
    store = as_the_store()
    asked: list[str] = []

    def connect(dataset: str) -> StandIn:
        asked.append(dataset)
        return store

    monkeypatch.setattr(reachable_destination, "connect", connect)

    assert main.main(["reachable-destination", "--dataset", "ogm-disposable-demo"]) == 0
    assert asked == ["ogm-disposable-demo"]
    assert store.done[0] == "seed"
    assert store.done[-1] == "clear"


def test_the_command_clears_the_dataset_when_the_demo_fails(monkeypatch) -> None:
    store = StandIn({})
    monkeypatch.setattr(reachable_destination, "connect", lambda dataset: store)

    assert main.main(["reachable-destination", "--dataset", "ogm-disposable-demo"]) == 1
    assert store.done[-1] == "clear"


def test_the_command_takes_only_a_disposable_dataset(monkeypatch, capsys) -> None:
    def connect(dataset: str):
        raise AssertionError("the command connected")

    monkeypatch.setattr(reachable_destination, "connect", connect)

    assert main.main(["reachable-destination", "--dataset", "jms-kapps"]) == 1
    assert "ogm-disposable-" in capsys.readouterr().err


def test_the_help_says_what_the_command_shows_and_that_it_clears_the_dataset(
    capsys,
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main.main(["reachable-destination", "--help"])

    assert stopped.value.code == 0
    # The help wraps its lines, also at a hyphen.
    text = "".join(capsys.readouterr().out.split())
    assert "SHACL-SPARQL" in text
    assert "clears" in text
    assert "ogm-disposable-" in text


def test_the_third_module_is_seeded_like_the_others_and_connected_to_none(
    monkeypatch,
) -> None:
    from jms_kapps.uc2 import seed

    asked: dict = {}

    def two_modules(db, *, transit_seconds, sparql_shapes=False):
        asked.update(transit=transit_seconds, sparql=sparql_shapes)
        return M1, M2

    class Triples:
        def __init__(self) -> None:
            self.added: list = []

        def triple_add(self, triple) -> bool:
            self.added.append(triple)
            return True

    monkeypatch.setattr(seed, "seed_two_modules", two_modules)
    db = Triples()

    assert seed.seed_three_modules(db, transit_seconds=2, sparql_shapes=True) == (
        M1,
        M2,
        M3,
    )

    assert asked == {"transit": 2, "sparql": True}
    own = [(p, o) for s, p, o in db.added if s == M3]
    assert {str(p) for p, _ in own} == {
        str(RDF.type),
        str(RDFS.label),
        str(FC.hasTransitTime),
        str(MES.hasHandoverAbility),
    }
    assert len(own) == 5
    assert all(p != FC.hasConnection and o != M3 for _, p, o in db.added)
