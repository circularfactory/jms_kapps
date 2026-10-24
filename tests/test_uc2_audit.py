"""`jms-uc2 audit` validates a dataset against both shape files through the store's contract.

Without a store: a stand-in answers `validate` with a report and records the call.
"""

from __future__ import annotations

import io

import pytest

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc2 import __main__ as main
from jms_kapps.uc2 import audit as audit_module
from jms_kapps.uc2.audit import audit, conforms

CORE = read_asset("jms_kapps.uc2", "shapes", "flexconveyor_usecase_shapes.ttl")
SPARQL = read_asset("jms_kapps.uc2", "shapes", "flexconveyor_usecase_sparql_shapes.ttl")

SH = "http://www.w3.org/ns/shacl#"
CONFORMING = (
    f'_:r <{SH}conforms> "true"^^<http://www.w3.org/2001/XMLSchema#boolean> .\n'
)
VIOLATED = f'_:r <{SH}conforms> "false"^^<http://www.w3.org/2001/XMLSchema#boolean> .\n'


class StandIn:
    """A store that validates nothing itself: it answers with `report` and records the call."""

    def __init__(self, report: str) -> None:
        self.report = report
        self.calls: list[tuple[str, str]] = []

    def validate(self, shapes: str, content_type: str) -> str:
        self.calls.append((shapes, content_type))
        return self.report


def test_the_audit_validates_against_both_shape_files_and_prints_the_report():
    store, out = StandIn(CONFORMING), io.StringIO()

    code = audit(store, out=out)

    ((shapes, content_type),) = store.calls
    assert CORE in shapes
    assert SPARQL in shapes
    assert content_type == "text/turtle"
    assert out.getvalue().strip() == CONFORMING.strip()
    assert code == 0


def test_the_audit_exits_1_when_the_dataset_does_not_conform():
    assert audit(StandIn(VIOLATED), out=io.StringIO()) == 1


@pytest.mark.parametrize(
    ("report", "expected"), [(CONFORMING, True), (VIOLATED, False)]
)
def test_a_report_conforms_when_it_says_so(report, expected):
    assert conforms(report) is expected


def test_the_command_audits_the_dataset_it_is_given(monkeypatch):
    store = StandIn(CONFORMING)
    asked: list[str] = []

    def connect(dataset: str) -> StandIn:
        asked.append(dataset)
        return store

    monkeypatch.setattr(audit_module, "connect", connect)

    assert main.main(["audit", "--dataset", "some-dataset"]) == 0
    assert main.main(["--dataset", "other-dataset", "audit"]) == 0
    assert asked == ["some-dataset", "other-dataset"]
    assert len(store.calls) == 2


def test_the_command_reports_a_store_that_cannot_validate(monkeypatch, capsys):
    from kapps_triplestore_interface import TripleStoreNotSupportedError

    class NoValidation:
        def validate(self, shapes: str, content_type: str) -> str:
            raise TripleStoreNotSupportedError("no validation")

    monkeypatch.setattr(audit_module, "connect", lambda dataset: NoValidation())

    assert main.main(["audit"]) == 1
    assert "jms-uc2 audit: no validation" in capsys.readouterr().err
