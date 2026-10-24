"""`jms-uc2 capability-resource` makes its three writes, prints each with the store's answer, and
clears the dataset it was given, which must be a disposable one.

Without a store: a stand-in refuses the writes that a seeded store refuses, with a report that names
the rule, and records every update. `test_capability_shape_live.py` makes the writes on a live
store.
"""

from __future__ import annotations

import io

import pytest
from kapps_triplestore_interface import ShaclValidationError

from jms_kapps.uc2 import __main__ as main
from jms_kapps.uc2 import capability_resource
from jms_kapps.uc2.capability_resource import WRITES, demo
from jms_kapps.uc2.vocabulary import FC_NS

SH = "http://www.w3.org/ns/shacl#"
MESSAGE = "A Capability must belong to exactly one resource."
# A report as the store writes one: N-Quads.
REPORT = f"""
_:report <{SH}result> _:result .
_:result <{SH}focusNode> <{FC_NS}Orphan> .
_:result <{SH}sourceShape> <{FC_NS}CapabilityResourceShape> .
_:result <{SH}resultMessage> "{MESSAGE}" .
"""


class StandIn:
    """A dataset that admits the updates `admits` says and refuses every other one with `REPORT`,
    and records each update, and each seed and clear of the demonstration."""

    def __init__(self, admits) -> None:
        self.admits = admits
        self.done: list[str] = []

    def query(self, query, update=False, **kwargs):
        self.done.append(str(query))
        if not self.admits(str(query)):
            raise ShaclValidationError("refused", REPORT, status=400)
        return True


@pytest.fixture(autouse=True)
def no_seed(monkeypatch):
    monkeypatch.setattr(
        capability_resource, "reset_uc2", lambda db: db.done.append("seed")
    )
    monkeypatch.setattr(
        capability_resource, "clear_dataset", lambda db: db.done.append("clear")
    )


def as_seeded_store(update: str) -> bool:
    """What a store seeded with the shapes answers: it admits the first write only."""
    return update == WRITES[0].update()


def test_the_demo_seeds_then_makes_each_write_in_one_update() -> None:
    store = StandIn(as_seeded_store)

    assert demo(store, out=io.StringIO()) == 0

    assert store.done == ["seed"] + [write.update() for write in WRITES]


def test_the_demo_prints_each_write_and_the_rule_of_a_refusal() -> None:
    out = io.StringIO()

    demo(StandIn(as_seeded_store), out=out)

    text = out.getvalue()
    for number, write in enumerate(WRITES, start=1):
        assert f"{number}. {write.title}" in text
        for s, p, o in write.triples:
            assert f"<{s}> <{p}> <{o}> ." in text
    assert text.count("the store: admitted") == 1
    assert (
        text.count(
            f"the store: refused, by CapabilityResourceShape at Orphan: {MESSAGE}"
        )
        == 2
    )


def test_the_demo_exits_1_when_the_store_answers_a_write_otherwise() -> None:
    out = io.StringIO()

    assert demo(StandIn(lambda update: True), out=out) == 1
    assert "expected the store to refuse it" in out.getvalue()


def test_the_command_plays_the_demo_on_the_dataset_it_is_given_and_clears_it(
    monkeypatch,
) -> None:
    store = StandIn(as_seeded_store)
    asked: list[str] = []

    def connect(dataset: str) -> StandIn:
        asked.append(dataset)
        return store

    monkeypatch.setattr(capability_resource, "connect", connect)

    assert main.main(["capability-resource", "--dataset", "ogm-disposable-demo"]) == 0
    assert asked == ["ogm-disposable-demo"]
    assert store.done[0] == "seed"
    assert store.done[-1] == "clear"


def test_the_command_clears_the_dataset_when_the_demo_fails(monkeypatch) -> None:
    store = StandIn(lambda update: True)
    monkeypatch.setattr(capability_resource, "connect", lambda dataset: store)

    assert main.main(["capability-resource", "--dataset", "ogm-disposable-demo"]) == 1
    assert store.done[-1] == "clear"


def test_the_command_takes_only_a_disposable_dataset(monkeypatch, capsys) -> None:
    monkeypatch.setattr(capability_resource, "connect", pytest.fail)

    assert main.main(["capability-resource", "--dataset", "jms-kapps"]) == 1
    assert "starts with ogm-disposable-" in capsys.readouterr().err


def test_the_command_reports_a_store_that_refuses_the_clear(
    monkeypatch, capsys
) -> None:
    def refused(db) -> None:
        raise ValueError("set TRIPLESTORE_ALLOW_DESTRUCTIVE=1 to clear it")

    monkeypatch.setattr(
        capability_resource, "connect", lambda dataset: StandIn(as_seeded_store)
    )
    monkeypatch.setattr(capability_resource, "reset_uc2", refused)
    monkeypatch.setattr(capability_resource, "clear_dataset", refused)

    assert main.main(["capability-resource", "--dataset", "ogm-disposable-demo"]) == 1
    assert "TRIPLESTORE_ALLOW_DESTRUCTIVE=1" in capsys.readouterr().err
