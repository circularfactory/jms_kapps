"""The rules clause and the messages clause of a refusal: what use case 2 adds to the middleware's
reader of a validation report.

When the store's shapes refuse a write, the triplestore interface raises `ShaclValidationError`,
and its `report` is the store's validation report, verbatim. jms_kapps reads no report itself: the
middleware's `kapps_semantic_middleware.validation_report` reads it, as RDF. What stays here is the
clause that a module's or the WarehouseManagementSystem's own log line of a refusal, and the reason
the WarehouseManagementSystem keeps of a transport that failed, put after what was refused, and the
clause of the rules' messages, which the reason the WarehouseManagementSystem shows of a refused
spawn or a failed transport puts after that. (The messages clause added on 2026-10-06, jms_kapps
41, decided by Etienne: "Last spawns" shows the store's reason.)

Changed on 2026-10-06 (jms_kapps 38, decided by Etienne): until then this module was
"the one reader of a validation report", jms_kapps' own, which the middleware's replaced.
"""

from __future__ import annotations

from kapps_semantic_middleware.validation_report import readable_results, rule_names


def rules_clause(report: str) -> str:
    """The clause that a log line of a refusal puts after the refused write: the rules `report`
    names, as `, by <shape> at <focus node>, ...`, or nothing when it names none."""
    rules = rule_names(report)
    return f", by {', '.join(rules)}" if rules else ""


def messages_clause(report: str) -> str:
    """The clause that says what the rules `report` names say: `: <message> <message>`, each
    message of its results once, in the order the reader gives the results, or nothing when the
    report gives none."""
    messages: list[str] = []
    for result in readable_results(report) or []:
        for message in result.message.short.splitlines() if result.message else []:
            if message not in messages:
                messages.append(message)
    return f": {' '.join(messages)}" if messages else ""
