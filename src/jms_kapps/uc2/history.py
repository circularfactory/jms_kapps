"""The store's history, from which a past possession is read: GraphDB's history plugin ("data
history and versioning"), and the one module that uses it.

A handover overwrites the box's link to its possessor's possession state, so the graph holds only
the present possessor. Possession history lives in the store's history mechanism, not in the
graph: the history plugin records every transactional change at triple granularity, with the time
of its transaction, and answers a query for the data as it was at one point in time, so that every
past state can be read, as the paper's requirements ask. The plugin is off by default.

**This module is the vendor exception.** The history plugin is GraphDB's own feature, above the
triple-store interface, and what is written here runs on GraphDB alone: Etienne Hoffmann decided on
2026-10-06 that the use case 2 reset may turn it on directly, and the interface gets no verb for
it, neither the switch nor the reading. So the plugin's IRIs are named here and nowhere else in
the source, and the source check of the store's own graphs lists this module as its one
exception. (Added on 2026-10-10, jms_kapps 22.)

- **The switch.** `enable_history` is what a launch with the option `collect_history` sends, the
  last step of its reset, after the reset loaded the ontology: the plugin's update, with no
  filter, so the store records every change. Nothing in jms_kapps turns the collection off: a
  clear while it is off is not logged, and the cleared data then disappears from every earlier
  point in time. With the collection on, GraphDB refuses only a clear of the whole repository,
  which the reset never sends: it clears the default graph and each graph it loads by name.
  (Measured on the KIT GraphDB 10.8.5 on 2026-10-01.)
- **The reading.** `possessor_query` is the query for the possessor of one box at one point in
  time, which `docs/reference/past-possessions.md` shows: the point is a graph of the plugin's,
  in the server's local time (`server_time` asks the server for it), and the query names its
  subject, the box, because a query that does not returns the live data. The plugin's transaction IRIs are in UTC. Only a
  person and the live tests read the history: no participant does.

KIT runs GraphDB 10.8.5. The plugin's own transaction IDs are used, never custom ones: 10.8.6
fixed major issues of the plugin with transaction IDs, and the default ones worked on 10.8.5.
"""

from __future__ import annotations

from datetime import datetime

from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import IRI, TripleStore

from jms_kapps.common.triplestore import Queryable, bindings

# The history plugin's namespace: its switch, its listing of the history, and a graph for each
# point in time, `<HISTORY_NS><yyyyMMddHHmmss>`.
HISTORY_NS = "http://www.ontotext.com/at/"

# Whether the store collects history: the plugin's switch, which an update sets and a query reads.
ENABLED = IRI(HISTORY_NS + "enabled")

# What a launch with `collect_history` sends: the collection on, with no filter.
ENABLE_HISTORY = f"INSERT DATA {{ [] <{ENABLED}> true }}"

# The point in time of a graph of the plugin's: the server's local time, to the second.
_POINT_IN_TIME = "%Y%m%d%H%M%S"


def enable_history(db: TripleStore) -> None:
    """Turn on the store's history collection, with no filter. The collection then records every
    change, until a person turns it off by hand.

    Raises:
        TripleStoreResponseError: If the store refuses it, as a store without the plugin may.
    """
    db.query(ENABLE_HISTORY, update=True)


def history_enabled(db: Queryable) -> bool:
    """Whether the store collects history, as it reports."""
    rows = bindings(
        db, f"SELECT ?enabled WHERE {{ [] <{ENABLED}> ?enabled }}", convert=False
    )
    return any(row["enabled"]["value"] == "true" for row in rows)


def server_time(db: Queryable) -> datetime:
    """The server's local time now, with its offset from UTC: SPARQL's `NOW()`, as the server
    answers it. Its clock and its zone are the ones the plugin's points in time are read in."""
    [row] = bindings(db, "SELECT (NOW() AS ?now) WHERE {}", convert=False)
    return datetime.fromisoformat(row["now"]["value"])


def point_in_time(moment: datetime) -> IRI:
    """The graph of the plugin's that holds the data as it was at `moment`, to the second.

    `moment` is the server's local time, as `server_time` gives it. Its clock fields are taken as
    they are, whatever zone it names: the plugin reads them in the server's zone.
    """
    return IRI(HISTORY_NS + moment.strftime(_POINT_IN_TIME))


def possessor_query(box: IRI, moment: datetime) -> str:
    """The query for the possessor of `box` at `moment`, the server's local time: one row, the
    possessor, or none where the box had no possessor then.

    It names its subject, the box, because a query that does not returns the live data (measured
    on the KIT GraphDB 10.8.5 on 2026-10-01). Its second pattern, the possessor's link to its own
    possession state, never changes, so the live data answers it right either way.
    """
    return (
        f"SELECT ?possessor FROM <{point_in_time(moment)}> WHERE {{\n"
        f"  <{box}> <{CFC.hasPossessedWorkpiece}> ?state .\n"
        f"  ?possessor <{CFC.hasPossessor}> ?state .\n"
        "}"
    )
