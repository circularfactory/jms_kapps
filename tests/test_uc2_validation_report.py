"""The rules clause and the messages clause of a refusal, read with the middleware's reader of a
validation report.

The store answers a write that its shapes refuse with the report as N-Quads, and the triplestore
interface hands it over verbatim. The middleware reads it, as RDF, never as text; its own tests
cover the reader. A module's and the WarehouseManagementSystem's log line of a refusal names the
rules the report names, after what was refused.
"""

from __future__ import annotations

from pathlib import Path

from rdflib.namespace import RDF, SH, XSD

from jms_kapps.uc2.validation_report import messages_clause, rules_clause

HEAD = (
    f"_:report <{RDF.type}> <{SH.ValidationReport}> .\n"
    f'_:report <{SH.conforms}> "false"^^<{XSD.boolean}> .\n'
)

# Reports as GraphDB sent them, recorded in a live run of each of §5.2's faults on the cross: the
# store refusing Module 1's `skip-reserve` handover of Box2 onto Module 3 while it holds another
# box, and Module 4's `complete-without-releasing` of Box1's transport.
RECORDED = Path(__file__).parent / "reports"


def recorded(fault: str) -> str:
    return (RECORDED / f"{fault}.nq").read_text()


def test_the_clause_names_each_shape_at_its_focus_node_by_local_name_in_order():
    """For a person to read."""
    report = HEAD + (
        f"_:report <{SH.result}> _:first .\n"
        f"_:first <{SH.focusNode}> <http://example.org/Module2> .\n"
        f"_:first <{SH.sourceShape}> <http://example.org/PossessionShape> .\n"
        f"_:report <{SH.result}> _:second .\n"
        f"_:second <{SH.focusNode}> <http://example.org/Box1> .\n"
        f"_:second <{SH.sourceShape}> <http://example.org/shapes#BoxShape> .\n"
    )

    assert rules_clause(report) == ", by BoxShape at Box1, PossessionShape at Module2"


def test_the_clause_of_each_recorded_refusal_names_its_rules():
    assert rules_clause(recorded("skip-reserve")) == (
        ", by FlexConveyorModulePossessionShape at Module3"
    )
    assert rules_clause(recorded("complete-without-releasing")) == (
        ", by BoxOnModuleHasTransportShape at Box1, DoneTransportBoxReleasedShape at Box1"
    )


def test_a_report_that_names_no_rule_adds_no_clause():
    """The refusal is the error's type, not what its report says: a text that is no RDF names no
    rule, and still travels verbatim."""
    assert rules_clause("Unknown dataset: jms-kapps") == ""
    assert rules_clause(HEAD) == ""


def test_the_messages_clause_says_what_each_rule_the_store_names_says_once():
    """For a person to read why, beside where: the message of each result, in the order the
    middleware's reader gives the results, each message once (jms_kapps 41)."""
    assert messages_clause(recorded("skip-reserve")) == (
        ": A FlexConveyorModule may possess at most one Box at a time."
    )


def test_a_report_with_no_message_adds_no_messages_clause():
    assert messages_clause("Unknown dataset: jms-kapps") == ""
    assert messages_clause(HEAD) == ""
