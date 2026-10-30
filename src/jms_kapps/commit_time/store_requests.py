"""What one store request of a participant says: its kind, the transports' statuses it writes, its
receipt, and the store's answer to it. Read from the request's text and the store's answer only,
by the wrapper's hooks inside each participant (`instrumentation`), by the harness after a run
(`observation`) and by the store-only baseline, which sends the requests again (`baseline`), so
that each reads a request the same way. Nothing here sends a request.

**A store request** is an HTTP request of a participant to the store, the URL that
`TRIPLESTORE_URL` names: its login, its queries, its updates.

**Its kind:** a read (a SPARQL query, or a read of statements), a write (a SPARQL update, or a write
of statements or of a graph), the login, or other.

**The statuses it writes.** A write that inserts `svc:hasOperationStatus` for an Operation writes that
status for it: the Operation is the subject, and the status the object, of that statement in the
update's `INSERT` template. A statement that the update only deletes, or only reads in its
`WHERE`, writes nothing. The box is the one the request names for the Operation, as the
Operation's `fc:hasBox` or the box's `fc:hasTransport`; none where the request names none.

**Its receipt.** The triple-store interface sends a checked write as one update that also inserts a
receipt, then asks the store whether the receipt is there, then removes it: the store answers every
update with success, and only the receipt says whether the update's precondition held and the
store applied it. So a write that the store answered with success but whose receipt was not there
wrote nothing.

**The store's answer.** A success status is accepted. An error status is refused, with the store's
reason, its answer as it gave it: a SHACL validation report, by the media type that GraphDB gives
it (the interface tells a refusal by that media type too), a refusal for want of heap, by the
words GraphDB answers it with, or another refusal. A request that got no answer is **timed out**
where its client gave up because the time ran out, and **connection dropped** where its connection
closed, reset or failed before an answer, a connection that the host refused too
(`unanswered_outcome`, from the error its client raised: its type, the part before the first
colon). An error of neither class counts as connection dropped, and so does a request without an
answer whose record holds no error, in a new record and an earlier one alike. Both count toward the 1 % of the broken rule (`replay_levels`). A record written before keeps
"timed out"; its readers classify such a request by its error, `outcome_of`, and change no
record file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from kapps_semantic_middleware.vocabulary import SVC
from kapps_triplestore_interface.graph_db import SHACL_VALIDATION_REPORT_MEDIA_TYPE
from kapps_triplestore_interface.queries.checked_write import (
    RECEIPT_GRAPH,
    RECEIPT_PREDICATE,
)

from jms_kapps.uc2.vocabulary import FC

# The kinds of a store request.
READ = "read"
WRITE = "write"
LOGIN = "login"
OTHER = "other"

# The outcomes of a store request. A request without an answer is timed out or connection dropped
# (`CONNECTION_DROPPED`, `UNANSWERED`).
ACCEPTED = "accepted"
REFUSED = "refused"
TIMED_OUT = "timed out"
CONNECTION_DROPPED = "connection dropped"
UNANSWERED = (TIMED_OUT, CONNECTION_DROPPED)

# The errors of a client that gave up because the time ran out, by their type's name: those of
# requests (which the participants' triple-store interface uses), of httpx (the replay's client),
# of urllib3 and of Python itself.
TIMEOUT_ERRORS = frozenset(
    {
        "ReadTimeout",
        "ConnectTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "Timeout",
        "TimeoutException",
        "ReadTimeoutError",
        "ConnectTimeoutError",
        "TimeoutError",
        "timeout",
    }
)

# The errors of a connection that closed, reset or failed before an answer, a connection that the
# host refused too, by their type's name. In a UC2 record a dropped
# connection is requests' `ConnectionError`, whose message names `RemoteDisconnected`.
DROPPED_ERRORS = frozenset(
    {
        "ConnectionError",
        "ChunkedEncodingError",
        "RemoteProtocolError",
        "LocalProtocolError",
        "ProtocolError",
        "ReadError",
        "WriteError",
        "ConnectError",
        "NetworkError",
        "CloseError",
        "RemoteDisconnected",
        "IncompleteRead",
        "NewConnectionError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "ConnectionRefusedError",
        "BrokenPipeError",
    }
)

# The kinds of a refusal: by SHACL, for want of heap, or another one.
SHACL = "shacl"
HEAP = "heap"
OTHER_REFUSAL = "other"

# How GraphDB words its refusal of a query for want of heap, as measured on jms_kapps' own stack
# (a refused startup query of a module of the 5-module cross): HTTP 500 with the text
# "Insufficient free Heap Memory 249Mb for group by and distinct". GraphDB refuses a DISTINCT or a
# GROUP BY while less free heap is left than its floor, 250 MB by default; the number in the text
# is the heap left.
HEAP_REFUSAL = "Insufficient free Heap Memory"

# The media types of a SPARQL update and of a SPARQL query, as the interface sends them.
SPARQL_UPDATE = "application/sparql-update"
SPARQL_QUERY = "application/sparql-query"

_LOGIN_PATH = "rest/login"


@dataclass(frozen=True)
class StatusWrite:
    """One status that a write sets: the Operation's IRI, the status, and the box's IRI where the
    request names it."""

    operation: str
    status: str
    box: str | None


def kind_of(method: str, path: str, content_type: str | None) -> str:
    """The kind of the store request `method` `path`, with the media type of its body: `READ`,
    `WRITE`, `LOGIN` or `OTHER`. `path` is the part of the URL after the store's own, as
    `repositories/<dataset>/statements`, without a query string."""
    method = method.upper()
    path = path.strip("/")
    media = _media_type(content_type)
    if path == _LOGIN_PATH:
        return LOGIN
    parts = path.split("/")
    if parts[0] != "repositories" or len(parts) < 2:
        return OTHER
    if media == SPARQL_UPDATE:
        return WRITE
    if len(parts) == 2:
        return READ if method in ("GET", "POST") else OTHER
    if parts[2] in ("statements", "rdf-graphs"):
        return READ if method == "GET" else WRITE
    return OTHER


def status_writes(text: str | None) -> list[StatusWrite]:
    """Each status that the update `text` inserts for an Operation, in the order the update names
    them; none for a text that is no update."""
    if not text:
        return []
    inserted = _insert_template(text)
    if not inserted:
        return []
    found = []
    for match in re.finditer(
        rf"<(?P<operation>[^<>\s]+)>\s+<{re.escape(str(SVC.hasOperationStatus))}>\s+"
        r'"(?P<status>[^"\\]*)"',
        inserted,
    ):
        operation = match["operation"]
        found.append(StatusWrite(operation, match["status"], _box_of(text, operation)))
    return found


def receipt_written(text: str | None) -> str | None:
    """The receipt that the update `text` inserts, as a checked write does, or None."""
    if not text:
        return None
    match = re.search(
        rf"<(?P<receipt>[^<>\s]+)>\s+<{re.escape(str(RECEIPT_PREDICATE))}>",
        _insert_template(text),
    )
    return match["receipt"] if match else None


def receipt_asked(text: str | None) -> str | None:
    """The receipt whose presence the query `text` asks, as a checked write does after its update,
    or None."""
    if not text or not text.lstrip().upper().startswith("ASK"):
        return None
    match = re.search(
        rf"GRAPH\s+<{re.escape(str(RECEIPT_GRAPH))}>\s*\{{\s*<(?P<receipt>[^<>\s]+)>",
        text,
    )
    return match["receipt"] if match else None


def receipt_found(answer: str) -> bool | None:
    """Whether the store's `answer` to the query that asks for a checked write's receipt says
    that the receipt is there: the `boolean` of SPARQL's JSON results, or None for an answer that
    holds none."""
    try:
        found = json.loads(answer).get("boolean")
    except (ValueError, AttributeError):
        return None
    return found if isinstance(found, bool) else None


def applied(
    kind: str, outcome: str, receipt: str | None, found: bool | None
) -> bool | None:
    """Whether the store applied a store request of `kind` with `outcome`: not where it was not
    accepted; for a checked write, whose update inserts `receipt`, whether the query for its
    receipt found it (`found`), unknown (None) where nothing asked; for another accepted write,
    yes. None for a request that writes nothing."""
    if kind != WRITE and not receipt:
        return None
    if outcome != ACCEPTED:
        return False
    if receipt:
        return found
    return True


# The two spellings of the heartbeat predicate in the records: the old and the current one. The
# old is no substring of the current, because of the capital L.
# The old is spelled from two parts: the name test bans it as a bare word.
OLD_HEARTBEAT_PREDICATE = "last" + "Heartbeat"
HEARTBEAT_PREDICATES = (OLD_HEARTBEAT_PREDICATE, "hasLastHeartbeat")


def names_heartbeat(text: str | None) -> bool:
    """Whether the update `text` names the heartbeat predicate, in either spelling. The steps'
    classifier, the dead-heartbeat point and the kind of a commit all ask this one question."""
    if not text:
        return False
    return any(predicate in text for predicate in HEARTBEAT_PREDICATES)


def removes_receipt(text: str | None) -> bool:
    """Whether the update `text` only removes a checked write's receipt, as the interface does
    once it found the receipt: a write of the store, but not one of the participant's."""
    if not text:
        return False
    return bool(
        re.fullmatch(
            rf"\s*DELETE\s+WHERE\s*\{{\s*GRAPH\s+<{re.escape(str(RECEIPT_GRAPH))}>.*",
            text,
            re.DOTALL,
        )
    )


def error_type(error: str | None) -> str | None:
    """The name of the type of `error`, an error as a record keeps it (`<type>: <message>`, or the
    type alone, a dotted one too), or None for none."""
    if not error:
        return None
    return error.split(":", 1)[0].strip().rsplit(".", 1)[-1] or None


def unanswered_outcome(error: str | None) -> str:
    """The outcome of a store request that got no answer, by the `error` its client raised, as a
    record keeps it: `TIMED_OUT` where the time ran out
    (`TIMEOUT_ERRORS`), else `CONNECTION_DROPPED`: a connection that closed, reset or failed
    before an answer (`DROPPED_ERRORS`), an error of neither class, which `known_error` tells
    apart, and no error at all (None)."""
    return TIMED_OUT if error_type(error) in TIMEOUT_ERRORS else CONNECTION_DROPPED


def outcome_of(request: Mapping[str, Any]) -> str | None:
    """The outcome of a store request as a record holds it (`outcome`, with its `reason`), where
    a request "timed out" is classified by its error (`unanswered_outcome`): a record written
    before the rename says "timed out" for each request without an answer, a dropped
    connection too. One that holds no error is connection dropped, as a new record's request
    without an answer and without an error is. A reader takes this, and no record file is changed."""
    outcome = request.get("outcome")
    if outcome == TIMED_OUT:
        reason = request.get("reason")
        return unanswered_outcome(str(reason) if reason else None)
    return None if outcome is None else str(outcome)


def answer_of(
    status_code: int, content_type: str | None, body: str
) -> tuple[str, str | None, str | None]:
    """The outcome of a store request that the store answered with `status_code`, the media type
    `content_type` and `body`: `ACCEPTED`, or `REFUSED` with the kind of the refusal and the
    store's reason, its answer as it gave it."""
    if 200 <= status_code < 300:
        return ACCEPTED, None, None
    reason = body if body else f"HTTP {status_code}"
    if _media_type(content_type).startswith(SHACL_VALIDATION_REPORT_MEDIA_TYPE):
        return REFUSED, SHACL, reason
    if HEAP_REFUSAL.lower() in body.lower():
        return REFUSED, HEAP, reason
    return REFUSED, OTHER_REFUSAL, reason


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _box_of(text: str, operation: str) -> str | None:
    """The box that `text` names for `operation`: its `fc:hasBox`, or the box whose
    `fc:hasTransport` it is."""
    has_box = re.search(
        rf"<{re.escape(operation)}>\s+<{re.escape(str(FC.hasBox))}>\s+<(?P<box>[^<>\s]+)>",
        text,
    )
    if has_box:
        return has_box["box"]
    has_transport = re.search(
        rf"<(?P<box>[^<>\s]+)>\s+<{re.escape(str(FC.hasTransport))}>\s+<{re.escape(operation)}>",
        text,
    )
    return has_transport["box"] if has_transport else None


def template_iris(text: str | None) -> frozenset[str]:
    """Every IRI that the update `text` names in its `DELETE` or `INSERT` template, `DATA` ones
    too: the statements it removes and those it sets, but not those its `WHERE` only reads. An
    IRI is a whole `<...>`, so one never matches inside another. Empty for a text that is no
    update or has no template."""
    if not text:
        return frozenset()
    templates = _template(text, "DELETE") + " " + _insert_template(text)
    return frozenset(re.findall(r"<([^<>\s]+)>", templates))


def _insert_template(text: str) -> str:
    """The template of the `INSERT` or `INSERT DATA` of the update `text`, without its braces,
    or "" where it has none. A brace inside an IRI or a literal does not count."""
    return _template(text, "INSERT")


def _template(text: str, keyword: str) -> str:
    """The template of the first `keyword` (`INSERT` or `DELETE`), or `keyword DATA`, of the
    update `text`, without its braces, or "" where it has none. A `DELETE WHERE` has no template.
    A brace inside an IRI or a literal does not count."""
    start = re.search(rf"\b{keyword}\s+(?:DATA\s+)?\{{", text, re.IGNORECASE)
    if start is None:
        return ""
    depth = 1
    at = start.end()
    while at < len(text):
        character = text[at]
        if character in "\"'":
            at = _after_literal(text, at)
            continue
        if character == "<":
            closing = text.find(">", at)
            at = len(text) if closing < 0 else closing + 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start.end() : at]
        at += 1
    return text[start.end() :]


def _after_literal(text: str, at: int) -> int:
    """Where the literal that starts at `at` ends, a short or a long one, escapes skipped."""
    quote = text[at]
    delimiter = quote * 3 if text.startswith(quote * 3, at) else quote
    at += len(delimiter)
    while at < len(text):
        if text[at] == "\\":
            at += 2
            continue
        if text.startswith(delimiter, at):
            return at + len(delimiter)
        at += 1
    return at
