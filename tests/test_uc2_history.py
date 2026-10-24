"""The launch option `collect_history` in the use case 2 reset, and the reading of a past possession,
without a store.

A stand-in store records every request a reset sends, in order: each import with its content,
each clear, the shapes, each query and update, and each prefix request. A reset with the option
sends the history plugin's switch after it loaded the use case 2 ontology, and a reset without it
sends no request of the plugin's at all, neither on nor off. The plugin's IRIs are read from
`jms_kapps.uc2.history`, the one module that names them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import pytest
from _disposable import disposable
from _loopback import LOOPBACK_ENDPOINT
from kapps_semantic_middleware.vocabulary import CFC
from kapps_triplestore_interface import IRI

from jms_kapps.common.triplestore import read_asset
from jms_kapps.uc2 import history
from jms_kapps.uc2.launcher.backend import TripleStoreGraph as LauncherGraph
from jms_kapps.uc2.run_core import TripleStoreGraph
from jms_kapps.uc2.seed import reset_uc2, seed_topology
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import box_iri

UC2_ONTOLOGY = read_asset("jms_kapps.uc2", "ontology", "flexconveyor_usecase.ttl")


@dataclass
class StandIn:
    """A dataset as a reset sees it, through the store's contract, which keeps nothing and
    records every request in order: `("import", content)`, `("clear", graph)`,
    `("shapes", data)`, `("update", text)`, `("query", text)` and `("prefixes", operation)`. It
    maps no prefix, and takes every prefix request."""

    dataset: str = "stand-in"
    _endpoint = LOOPBACK_ENDPOINT
    sent: list[tuple[str, object]] = field(default_factory=list)

    def graph_clear(self, graph_iri=None) -> bool:
        self.sent.append(("clear", graph_iri))
        return True

    def graph_add(self, content, **kwargs) -> bool:
        self.sent.append(("import", content))
        return True

    def shapes_set(self, data, content_type) -> None:
        self.sent.append(("shapes", data))

    def query(self, query, update=False, **kwargs):
        self.sent.append(("update" if update else "query", str(query)))
        return True if update else {"boolean": False, "results": {"bindings": []}}

    def prefixes_get(self) -> dict[str, str]:
        self.sent.append(("prefixes", "get"))
        return {}

    def prefix_set(self, prefix: str, iri: str) -> None:
        self.sent.append(("prefixes", "set"))

    def prefix_delete(self, prefix: str) -> None:
        self.sent.append(("prefixes", "delete"))

    def index(self, kind: str, content: object) -> int:
        """Where the request `(kind, content)` was sent, in the order of every request."""
        return self.sent.index((kind, content))

    def of_the_plugin(self) -> list[tuple[str, object]]:
        """Every request that names the history plugin."""
        return [
            (kind, content)
            for kind, content in self.sent
            if isinstance(content, str) and history.HISTORY_NS in content
        ]


def test_the_switch_turns_the_collection_on_with_no_filter():
    assert (
        history.ENABLE_HISTORY
        == f"INSERT DATA {{ [] <{history.HISTORY_NS}enabled> true }}"
    )


# -- The reset -------------------------------------------------------------------------------- #


def test_a_reset_with_the_option_turns_the_history_on_after_the_ontology_is_loaded():
    store = StandIn()

    reset_uc2(store, collect_history=True)

    assert store.of_the_plugin() == [("update", history.ENABLE_HISTORY)]
    assert store.index("update", history.ENABLE_HISTORY) > store.index(
        "import", UC2_ONTOLOGY
    )


@pytest.mark.parametrize("reset", ["default", "off"])
def test_a_reset_without_the_option_sends_nothing_to_the_history(reset):
    """Neither on nor off: a dataset whose collection an earlier launch turned on keeps it."""
    store = StandIn()

    if reset == "default":
        reset_uc2(store)
    else:
        reset_uc2(store, collect_history=False)

    assert store.of_the_plugin() == []
    assert ("import", UC2_ONTOLOGY) in store.sent


def test_a_launch_s_seed_passes_the_option_to_its_reset():
    """As the run core's graph seeds a launch's topology: the history is turned on after the
    topology is imported too, the reset's last import."""
    store = StandIn()

    TripleStoreGraph(store).seed(load_topology("pair"), 0, collect_history=True)

    assert store.of_the_plugin() == [("update", history.ENABLE_HISTORY)]
    imports = [i for i, (kind, _) in enumerate(store.sent) if kind == "import"]
    assert store.index("update", history.ENABLE_HISTORY) > max(imports)


@pytest.mark.parametrize("collect_history", [False, True])
def test_the_launcher_s_graph_seeds_with_the_option_it_is_given(collect_history):
    store = StandIn()

    LauncherGraph(store).seed(load_topology("pair"), 0, collect_history=collect_history)

    assert bool(store.of_the_plugin()) is collect_history


def test_a_seed_without_the_option_sends_nothing_to_the_history():
    store = StandIn()

    seed_topology(store, load_topology("pair"), transit_seconds=0)

    assert store.of_the_plugin() == []


# -- The reading ------------------------------------------------------------------------------ #


def test_the_point_in_time_is_the_server_s_local_time_to_the_second():
    """The clock fields as they are: the zone the moment names is not converted to UTC."""
    moment = datetime(
        2026, 10, 1, 14, 3, 7, 999000, tzinfo=timezone(timedelta(hours=2))
    )

    assert history.point_in_time(moment) == IRI(history.HISTORY_NS + "20261001140307")


def test_the_query_of_a_past_possessor_names_its_subject_the_box():
    moment = datetime(2026, 10, 1, 14, 3, 7, tzinfo=timezone(timedelta(hours=2)))

    query = history.possessor_query(box_iri(1), moment)

    assert f"FROM <{history.HISTORY_NS}20261001140307>" in query
    assert f"<{box_iri(1)}> <{CFC.hasPossessedWorkpiece}> ?state" in query
    assert f"?possessor <{CFC.hasPossessor}> ?state" in query
    assert query.startswith("SELECT ?possessor ")


class Answers:
    """A store that answers every query with `rows`, and keeps each query text."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(self, query, **kwargs):
        self.queries.append(str(query))
        return {"results": {"bindings": self.rows}}


@pytest.mark.parametrize(("value", "enabled"), [("true", True), ("false", False)])
def test_the_store_reports_whether_it_collects_history(value, enabled):
    store = Answers(
        [{"enabled": {"type": "literal", "value": value, "datatype": "xsd:boolean"}}]
    )

    assert history.history_enabled(store) is enabled
    assert f"<{history.ENABLED}>" in store.queries[0]


def test_the_server_s_time_keeps_the_server_s_zone():
    store = Answers(
        [{"now": {"type": "literal", "value": "2026-10-01T14:03:07.123+02:00"}}]
    )

    now = history.server_time(store)

    assert now == datetime(
        2026, 10, 1, 14, 3, 7, 123000, tzinfo=timezone(timedelta(hours=2))
    )
    assert history.point_in_time(now) == IRI(history.HISTORY_NS + "20261001140307")


# -- The change list -------------------------------------------------------------------------- #


def change(transaction: str, timestamp: str, value: str, insert: bool) -> dict:
    """One row of the change list, as the store answers it."""
    return {
        "transaction": {"type": "uri", "value": history.HISTORY_NS + transaction},
        "timestamp": {"type": "literal", "value": timestamp},
        "object": {"type": "uri", "value": value},
        "insert": {"type": "literal", "value": "true" if insert else "false"},
    }


def test_the_change_list_query_names_the_subject_and_the_predicate():
    box = box_iri(1)

    query = history.change_list_query(box, CFC.hasPossessedWorkpiece)

    assert f"<{history.HISTORY_NS}listWithFilter>" in query
    assert f"<{box}> <{CFC.hasPossessedWorkpiece}>" in query
    for variable in ("?transaction", "?timestamp", "?object", "?insert"):
        assert variable in query.split("WHERE")[0]


def test_a_change_list_becomes_its_changes_in_the_order_of_their_time():
    """The store's rows in any order; the changes come in the order of their timestamps, each
    with its transaction, its value, and whether it was inserted or deleted."""
    box = box_iri(1)
    state_1, state_2 = (f"{box}-state-{n}" for n in (1, 2))
    store = Answers(
        [
            change("20261024090531246", "2026-10-24T11:05:31.246+02:00", state_2, True),
            change(
                "20261024090531246", "2026-10-24T11:05:31.246+02:00", state_1, False
            ),
            change("20261024090530484", "2026-10-24T11:05:30.484+02:00", state_1, True),
        ]
    )

    found = history.changes(store, box, CFC.hasPossessedWorkpiece)

    plus_two = timezone(timedelta(hours=2))
    assert found == [
        history.Change(
            IRI(history.HISTORY_NS + "20261024090530484"),
            datetime(2026, 10, 24, 11, 5, 30, 484000, tzinfo=plus_two),
            box,
            CFC.hasPossessedWorkpiece,
            state_1,
            True,
        ),
        history.Change(
            IRI(history.HISTORY_NS + "20261024090531246"),
            datetime(2026, 10, 24, 11, 5, 31, 246000, tzinfo=plus_two),
            box,
            CFC.hasPossessedWorkpiece,
            state_1,
            False,
        ),
        history.Change(
            IRI(history.HISTORY_NS + "20261024090531246"),
            datetime(2026, 10, 24, 11, 5, 31, 246000, tzinfo=plus_two),
            box,
            CFC.hasPossessedWorkpiece,
            state_2,
            True,
        ),
    ]
    assert store.queries == [history.change_list_query(box, CFC.hasPossessedWorkpiece)]


def test_a_change_s_time_in_utc_is_its_timestamp_converted():
    """The timestamps carry the server's offset; the transaction IRIs are in UTC."""
    store = Answers(
        [change("20261024090530484", "2026-10-24T11:05:30.484+02:00", "queued", True)]
    )

    [found] = history.changes(store, box_iri(1), CFC.hasPossessedWorkpiece)

    assert found.timestamp.astimezone(UTC) == datetime(
        2026, 10, 24, 9, 5, 30, 484000, tzinfo=UTC
    )
    assert found.transaction.endswith("20261024090530484")


# -- The live tests' guard -------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dataset", ["Tests", "jms-kapps", "disposable-ogm-jms52", "OGM-disposable-jms52"]
)
def test_the_live_tests_refuse_a_dataset_that_is_not_disposable(dataset):
    """`test_uc2_history_live.py` asks this before it connects: a reset with the option turns
    the history on for good, so never on `Tests`."""
    with pytest.raises(pytest.skip.Exception, match=f"refused on {dataset!r}: why"):
        disposable(dataset, "why")


def test_the_live_tests_take_a_disposable_dataset():
    assert disposable("ogm-disposable-jms52", "why") == "ogm-disposable-jms52"
