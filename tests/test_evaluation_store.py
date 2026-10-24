"""The store of a run of the measurement harness.

A run creates its own repository, `eval-` plus its run id, from jms_kapps' repository config, with
the shapes or as the twin without them, and deletes only that repository. The guard refuses every
other name before a request leaves, `jms-kapps`, `kapps-demo` and `Tests` among them, and a ruleset
other than `rdfsplus-optimized`, in the config or as the store answers it. The store here is a
stand-in of GraphDB's REST API: nothing reaches a store.
"""

from __future__ import annotations

import pytest
from _evaluation_fakes import PASSWORD, USERNAME, FakeGraphDB
from rdflib import BNode, Graph
from rdflib.collection import Collection
from rdflib.namespace import RDF

from jms_kapps.evaluation.store import (
    GRAPHDB,
    REP,
    RULESET,
    SAIL,
    SR,
    StoreRefused,
    create_run_repository,
    guarded,
    jms_kapps_repository_config,
    monitorable,
    permit,
    repository_config,
    repository_name,
)

SHACL_SAIL = "rdf4j:ShaclSail"


def layers(config: str) -> dict[str, dict | None]:
    """A repository config, layer by layer: the repository, its implementation, the SHACL layer
    (None where it has none) and the store's own sail, each as its values by property. A list
    reads as a tuple, so that two configs compare whatever their blank nodes are called."""
    graph = Graph().parse(data=config, format="turtle")
    [repository] = graph.subjects(REP.repositoryID, None)

    def values(node, *skip) -> dict:
        found: dict = {}
        for predicate, value in graph.predicate_objects(node):
            if predicate in skip:
                continue
            if isinstance(value, BNode) and (value, RDF.first, None) in graph:
                found.setdefault(predicate, set()).add(
                    tuple(str(item) for item in Collection(graph, value))
                )
            else:
                found.setdefault(predicate, set()).add(str(value))
        return found

    implementation = graph.value(repository, REP.repositoryImpl)
    sail = graph.value(implementation, SR.sailImpl)
    shacl = None
    if str(graph.value(sail, SAIL.sailType)) == SHACL_SAIL:
        shacl = values(sail, SAIL.delegate)
        sail = graph.value(sail, SAIL.delegate)
    return {
        "repository": values(repository, REP.repositoryImpl),
        "implementation": values(implementation, SR.sailImpl),
        "shacl": shacl,
        "store": values(sail),
    }


@pytest.mark.parametrize(
    "name",
    [
        "jms-kapps",
        "kapps-demo",
        "Tests",
        "tests",
        "TESTS",
        "eval-",
        "evaluation",
        "my-eval-run",
        "Eval-20261011-120000-ab12",
        "eval-../Tests",
        "eval-a b",
        "",
    ],
)
def test_the_guard_refuses_every_name_that_is_not_eval_and_a_run_id(name):
    with pytest.raises(StoreRefused):
        guarded(name)


def test_the_guard_takes_eval_and_a_run_id():
    assert guarded("eval-20261011-120000-ab12") == "eval-20261011-120000-ab12"
    assert repository_name("20261011-120000-ab12") == "eval-20261011-120000-ab12"


@pytest.mark.parametrize("name", ["jms-kapps", "kapps-demo", "Tests", "other"])
def test_no_request_names_a_repository_the_guard_refuses(name):
    store = FakeGraphDB()
    server = store.server()

    with pytest.raises(StoreRefused):
        server.create_repository(name, jms_kapps_repository_config())
    with pytest.raises(StoreRefused):
        server.delete_repository(name)
    with pytest.raises(StoreRefused):
        server.repository(name)
    with pytest.raises(StoreRefused):
        server.statement_counts(name)

    assert store.requests == []
    assert set(store.repositories) == {"jms-kapps", "kapps-demo", "Tests"}


# The guard allows the read-only monitoring view of any repository, the protected
# ones included, and nothing else outside the run's own `eval-` repository: one test for each case.

OWN = "eval-20261011-120000-ab12"
OTHERS = ["jms-kapps", "kapps-demo", "Tests", "other-repository", "tests"]


@pytest.mark.parametrize("name", OTHERS)
def test_the_guard_allows_the_read_only_monitoring_view_of_any_other_repository(name):
    store = FakeGraphDB(repositories=[*OTHERS])
    server = store.server()

    view = server.repository_monitor(name)

    assert view["status"] == 200 and view["answer"] == {"activeTransactions": 0}
    assert store.requests == [
        "POST rest/login",
        f"GET rest/monitor/repository/{name}",
    ]


def test_the_monitoring_view_of_every_repository_the_server_lists_is_read_by_its_id():
    store = FakeGraphDB()
    server = store.server()

    monitors = server.repository_monitors(server.repositories())

    assert set(monitors) == {"jms-kapps", "kapps-demo", "Tests"}
    assert all(view["status"] == 200 for view in monitors.values())


def test_the_monitoring_views_of_a_refused_list_are_none_and_a_bad_id_is_kept_as_refused():
    store = FakeGraphDB(refusing={"rest/repositories": 403})
    server = store.server()

    assert server.repository_monitors(server.repositories()) == {}
    odd = server.repository_monitors(
        {
            "answer": [
                {"id": "a/b", "state": "RUNNING"},
                {"id": "ok", "state": "RUNNING"},
                {},
            ]
        }
    )
    assert odd["a/b"]["error"].startswith("the monitoring view names a repository")
    assert odd["ok"]["status"] == 200


@pytest.mark.parametrize(
    "name", ["", "a/b", "..", "../Tests", "a?b", "a b", ".x", "a#b"]
)
def test_the_monitoring_view_names_only_one_segment_of_a_path(name):
    store = FakeGraphDB()
    server = store.server()

    with pytest.raises(StoreRefused):
        monitorable(name)
    with pytest.raises(StoreRefused):
        server.repository_monitor(name)

    assert store.requests == []


@pytest.mark.parametrize("name", [*OTHERS, "eval-someone-elses"])
def test_the_guard_refuses_every_write_outside_the_run_s_own_repository(name):
    store = FakeGraphDB(repositories=[*OTHERS, "eval-someone-elses"])
    server = store.server()

    for method, path in [
        ("DELETE", f"rest/repositories/{name}"),
        ("PUT", f"rest/repositories/{name}"),
        ("POST", f"rest/repositories/{name}"),
        ("POST", f"repositories/{name}/statements"),
        ("PUT", f"repositories/{name}/statements"),
        ("DELETE", f"repositories/{name}/statements"),
        ("POST", f"repositories/{name}"),
        ("POST", f"rest/monitor/repository/{name}"),
        ("PUT", f"rest/monitor/repository/{name}"),
        ("DELETE", f"rest/monitor/repository/{name}"),
        ("DELETE", f"rest/monitor/repository/{name}/query?query=1"),
        ("POST", "rest/monitor/infrastructure/gc"),
        ("DELETE", "rest/graphdb-settings/license"),
        ("POST", "rest/graphdb-settings/license"),
        ("PUT", "rest/locations"),
    ]:
        with pytest.raises(StoreRefused):
            permit(method, path, [OWN])
        with pytest.raises(StoreRefused):
            server._send(method, path)
    with pytest.raises(StoreRefused):
        server.delete_repository(name)

    assert store.requests == []
    assert set(store.repositories) == {*OTHERS, "eval-someone-elses"}


@pytest.mark.parametrize("name", [*OTHERS, "eval-someone-elses"])
def test_the_guard_refuses_every_data_read_of_another_repository(name):
    store = FakeGraphDB(repositories=[*OTHERS, "eval-someone-elses"])
    server = store.server()

    for method, path in [
        ("GET", f"repositories/{name}"),
        ("POST", f"repositories/{name}"),
        ("GET", f"repositories/{name}/statements"),
        ("GET", f"repositories/{name}/size"),
        ("GET", f"repositories/{name}/contexts"),
        ("GET", f"rest/repositories/{name}/size"),
        ("GET", f"rest/repositories/{name}/export"),
        ("GET", f"rest/repositories/{name}"),
        ("GET", f"rest/monitor/repository/{name}/query/active"),
        ("GET", f"rest/monitor/repository/{name}/operations"),
        ("GET", f"rest/monitor/repository/{name}/query/count"),
    ]:
        with pytest.raises(StoreRefused):
            permit(method, path, [OWN])
        with pytest.raises(StoreRefused):
            server._send(method, path)
    with pytest.raises(StoreRefused):
        server.statement_counts(name)
    with pytest.raises(StoreRefused):
        server.repository(name)

    assert store.requests == []


def test_another_run_s_repository_is_refused_as_the_protected_ones_are():
    store = FakeGraphDB(repositories=["eval-someone-elses"])
    server = store.server()

    for call in (
        lambda: server.repository("eval-someone-elses"),
        lambda: server.statement_counts("eval-someone-elses"),
        lambda: server.delete_repository("eval-someone-elses"),
    ):
        with pytest.raises(StoreRefused):
            call()
    # Its monitoring view is the one read of it that is allowed.
    assert server.repository_monitor("eval-someone-elses")["status"] == 200
    assert store.requests == [
        "POST rest/login",
        "GET rest/monitor/repository/eval-someone-elses",
    ]


def test_the_run_s_own_repository_may_be_read_sized_and_deleted_and_nothing_else_may():
    for method, path in [
        ("GET", f"rest/repositories/{OWN}"),
        ("GET", f"rest/repositories/{OWN}/size"),
        ("DELETE", f"rest/repositories/{OWN}"),
        ("GET", f"rest/monitor/repository/{OWN}"),
        ("POST", "rest/repositories"),
        ("GET", "rest/repositories"),
        ("GET", "rest/info/version"),
        ("GET", "rest/monitor/infrastructure"),
        ("GET", "rest/monitor/structures"),
        ("GET", "rest/graphdb-settings/license"),
    ]:
        permit(method, path, [OWN])
    for method, path in [
        ("GET", f"rest/repositories/{OWN}/export"),
        ("PUT", f"rest/repositories/{OWN}"),
        ("GET", f"rest/repositories/{OWN}/size/more"),
        ("GET", "rest/monitor/repository"),
        ("GET", "rest/monitor/cluster"),
        ("POST", "rest/info/version"),
        ("DELETE", "rest/repositories"),
    ]:
        with pytest.raises(StoreRefused):
            permit(method, path, [OWN])


def test_the_store_deletes_only_a_repository_it_created_itself():
    store = FakeGraphDB(repositories=["eval-20261010-090000-0000"])
    server = store.server()

    with pytest.raises(StoreRefused, match="did not create"):
        server.delete_repository("eval-20261010-090000-0000")

    assert "eval-20261010-090000-0000" in store.repositories
    assert [r for r in store.requests if r.startswith("DELETE")] == []


def test_the_config_with_shacl_differs_from_jms_kapps_config_only_in_the_id():
    original = layers(jms_kapps_repository_config())
    with_shacl = layers(repository_config("eval-run-1", shacl=True))

    assert original["shacl"] is not None
    assert with_shacl == original | {
        "repository": original["repository"] | {REP.repositoryID: {"eval-run-1"}}
    }


def test_the_twin_differs_from_jms_kapps_config_only_in_the_shacl_layer_and_the_id():
    original = layers(jms_kapps_repository_config())
    twin = layers(repository_config("eval-run-1", shacl=False))

    assert original["shacl"] is not None
    assert twin["shacl"] is None
    assert twin["store"] == original["store"]
    assert twin["implementation"] == original["implementation"]
    assert twin["repository"] == original["repository"] | {
        REP.repositoryID: {"eval-run-1"}
    }


@pytest.mark.parametrize("shacl", [True, False])
def test_both_configs_run_the_ruleset_rdfsplus_optimized(shacl):
    store = layers(repository_config("eval-run-1", shacl=shacl))["store"]

    assert store[GRAPHDB.ruleset] == {RULESET} == {"rdfsplus-optimized"}


def test_a_config_with_another_ruleset_is_refused_before_anything_is_sent():
    owl_max = jms_kapps_repository_config().replace('"rdfsplus-optimized"', '"owl-max"')
    store = FakeGraphDB()

    with pytest.raises(StoreRefused, match="owl-max"):
        repository_config("eval-run-1", shacl=True, source=owl_max)

    assert store.requests == []


@pytest.mark.parametrize("shacl", [True, False])
def test_a_run_creates_its_repository_from_jms_kapps_config(shacl):
    store = FakeGraphDB()
    server = store.server()

    created = create_run_repository(server, "eval-run-1", shacl=shacl)

    assert store.created() == ["eval-run-1"]
    assert layers(store.repositories["eval-run-1"]) == layers(
        repository_config("eval-run-1", shacl=shacl)
    )
    assert created["ruleset"] == RULESET
    assert created["sent"] == repository_config("eval-run-1", shacl=shacl)
    assert created["answered"]["answer"]["id"] == "eval-run-1"


def test_a_store_that_answers_another_ruleset_is_refused():
    store = FakeGraphDB(ruleset="owl-horst-optimized")
    server = store.server()

    with pytest.raises(StoreRefused, match="owl-horst-optimized"):
        create_run_repository(server, "eval-run-1", shacl=True)

    # Created by this run, so this run may delete it, and the bench launcher does.
    server.delete_repository("eval-run-1")
    assert store.created() == []


def test_a_repository_that_exists_already_is_refused_and_left_alone():
    store = FakeGraphDB(repositories=["eval-run-1"])
    server = store.server()

    with pytest.raises(StoreRefused, match="eval-run-1"):
        create_run_repository(server, "eval-run-1", shacl=True)
    with pytest.raises(StoreRefused, match="did not create"):
        server.delete_repository("eval-run-1")

    assert "eval-run-1" in store.repositories


def test_a_view_records_what_the_store_answered_and_never_raises():
    store = FakeGraphDB()
    server = store.server()

    assert server.version() == {
        "request": "GET rest/info/version",
        "status": 200,
        "answer": {"productVersion": "10.8.5", "productType": "enterprise"},
    }
    server.create_repository("eval-gone", repository_config("eval-gone", shacl=True))
    del store.repositories["eval-gone"]
    missing = server.repository_monitor("eval-gone")
    assert missing["status"] == 200
    gone = server.repository("eval-gone")
    assert gone["status"] == 404
    assert "answer" not in gone and "No repository" in gone["error"]


def test_the_login_is_sent_once_and_never_kept_in_what_the_server_records():
    store = FakeGraphDB()
    server = store.server()

    server.version()
    server.repositories()
    server.resources()
    server.round_trip_ms(3)

    assert store.logins == [{"username": USERNAME, "password": PASSWORD}]
    kept = repr(server.requests)
    assert PASSWORD not in kept and USERNAME not in kept
    assert server.requests[0] == "POST rest/login"


def test_a_config_that_names_another_repository_is_refused_before_it_is_sent():
    store = FakeGraphDB()
    server = store.server()

    with pytest.raises(StoreRefused, match="eval-other"):
        server.create_repository(
            "eval-run-1", repository_config("eval-other", shacl=True)
        )

    assert [r for r in store.requests if r.startswith("POST rest/repositories")] == []


def test_a_repository_whose_creation_answer_was_lost_is_the_run_s_and_is_deleted():
    store = FakeGraphDB(lose_create_answer=True)
    server = store.server()

    with pytest.raises(StoreRefused, match="lost"):
        create_run_repository(server, "eval-run-1", shacl=True)

    assert server.created == {"eval-run-1"}
    server.delete_repository("eval-run-1")
    assert store.created() == []


def test_an_inactive_repository_is_kept_with_its_state_and_no_request_names_it():
    store = FakeGraphDB(
        repositories=["jms-kapps", "sleeper", "starting", "nostate"],
        states={"sleeper": "INACTIVE", "starting": "INITIALIZING"},
    )
    server = store.server()
    listed = server.repositories()
    del listed["answer"][3]["state"]

    monitors = server.repository_monitors(listed)

    assert monitors["jms-kapps"]["status"] == 200
    for name, state in (
        ("sleeper", "INACTIVE"),
        ("starting", "INITIALIZING"),
        ("nostate", None),
    ):
        assert monitors[name]["read"] is False
        assert monitors[name]["state"] == state
        assert monitors[name]["reason"].startswith("not read")
    assert not any(
        name in request
        for request in store.requests
        for name in ("sleeper", "starting", "nostate")
    )
