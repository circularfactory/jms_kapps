"""`jms-live-store` (jms_kapps 121): the four chores of a live check against a stand-in of the
GraphDB REST API, the refusals before any request, and no secret in any output. Nothing reaches a
store.
"""

from __future__ import annotations

import httpx
import pytest
from _evaluation_fakes import PASSWORD, USERNAME, FakeGraphDB
from rdflib import Graph

from jms_kapps.evaluation.live_store import main, run
from jms_kapps.evaluation.store import REP, StoreRefused, guarded

URL = "http://graphdb.test"
NAME = "ogm-disposable-x1"
ENV = {
    "TRIPLESTORE_URL": URL,
    "TRIPLESTORE_USERNAME": USERNAME,
    "TRIPLESTORE_PASSWORD": PASSWORD,
}


def client(store: FakeGraphDB) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(store.handle), base_url=URL)


def call(store: FakeGraphDB, *argv: str, env: dict[str, str] | None = None):
    return run(argv, ENV if env is None else env, client=client(store))


def test_login_reports_success_in_one_line():
    store = FakeGraphDB()
    code, line = call(store, "login")
    assert code == 0 and line == f"logged in at {URL}"
    assert store.logins == [{"username": USERNAME, "password": PASSWORD}]


def test_a_refused_login_is_one_line_without_the_password():
    class Refusing(FakeGraphDB):
        def handle(self, request):
            if request.url.path == "/rest/login":
                return httpx.Response(401, text=f"bad {PASSWORD}")
            return super().handle(request)

    code, line = call(Refusing(), "login")
    assert code == 1 and "\n" not in line and "401" in line
    assert PASSWORD not in line


def test_create_sends_the_config_with_the_id_changed_shacl_and_ruleset():
    store = FakeGraphDB()
    code, line = call(store, "create", NAME)
    assert code == 0 and NAME in line and "rdfsplus-optimized" in line
    assert "POST rest/repositories" in store.requests
    config = store.repositories[NAME]
    ids = [
        str(o)
        for o in Graph()
        .parse(data=config, format="turtle")
        .objects(None, REP.repositoryID)
    ]
    assert ids == [NAME]
    assert "ShaclSail" in config and "rdfsplus-optimized" in config


def test_size_reads_the_shared_repositories_read_only():
    store = FakeGraphDB()
    for name in ("jms-kapps", "Tests"):
        code, line = call(store, "size", name)
        assert (code, line) == (0, f"{name}: explicit=7 inferred=3 total=10")
    assert [r for r in store.requests if r.startswith("POST")] == [
        "POST rest/login",
        "POST rest/login",
    ]
    assert not [r for r in store.requests if r.startswith(("PUT", "DELETE"))]


def test_delete_removes_a_repository_created_by_another_process():
    store = FakeGraphDB(repositories=("jms-kapps", NAME))
    code, line = call(store, "delete", NAME)
    assert (code, line) == (0, f"deleted {NAME}")
    assert NAME not in store.repositories and "jms-kapps" in store.repositories


@pytest.mark.parametrize("command", ["create", "delete"])
@pytest.mark.parametrize(
    "name",
    [
        "jms-kapps",
        "JMS-KAPPS",
        "Tests",
        "tests",
        "kapps-demo",
        "Kapps-Demo",
        "eval-x",
        "x",
    ],
)
def test_create_and_delete_refuse_other_names_before_any_request(command, name):
    store = FakeGraphDB()
    code, line = call(store, command, name)
    assert code == 1 and "ogm-disposable-" in line
    assert store.requests == []


def test_a_missing_variable_is_one_line_naming_it():
    store = FakeGraphDB()
    env = {k: v for k, v in ENV.items() if k != "TRIPLESTORE_PASSWORD"}
    code, line = call(store, "login", env=env)
    assert code == 1 and "TRIPLESTORE_PASSWORD" in line and "\n" not in line
    assert store.requests == []


def test_the_deprecated_names_are_read_as_a_fallback():
    env = {
        "GRAPHDB_URL": URL,
        "GRAPHDB_USERNAME": USERNAME,
        "GRAPHDB_PASSWORD": PASSWORD,
    }
    assert call(FakeGraphDB(), "login", env=env)[0] == 0


def test_no_output_carries_the_password_or_the_token():
    store = FakeGraphDB()
    lines = [
        call(store, *argv)[1]
        for argv in (("login",), ("create", NAME), ("size", NAME), ("delete", NAME))
    ]
    assert all(PASSWORD not in line and "fake-token" not in line for line in lines)


def test_a_lost_connection_is_one_line_without_secrets():
    def boom(request):
        raise httpx.ConnectError(f"cannot reach {PASSWORD}", request=request)

    http = httpx.Client(transport=httpx.MockTransport(boom), base_url=URL)
    code, line = run(["login"], ENV, client=http)
    assert code == 1 and PASSWORD not in line and "\n" not in line


def test_create_of_an_existing_name_is_a_one_line_refusal():
    store = FakeGraphDB(repositories=("jms-kapps", NAME))
    code, line = call(store, "create", NAME)
    assert code == 1 and "\n" not in line and NAME in line


def test_an_unexpected_error_is_one_line_without_a_traceback():
    code, line = run(["login"], ENV | {"TRIPLESTORE_URL": "http://[bad"})
    assert code == 1 and "\n" not in line and PASSWORD not in line


def test_main_prints_the_refusal_to_stderr_and_returns_one(monkeypatch, capsys):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    assert main(["delete", "jms-kapps"]) == 1
    assert "ogm-disposable-" in capsys.readouterr().err


def test_the_harness_still_refuses_a_non_eval_name_after_the_seam():
    assert guarded("eval-run-1") == "eval-run-1"
    for name in (NAME, "jms-kapps", "Tests"):
        with pytest.raises(StoreRefused):
            guarded(name)
    server = FakeGraphDB().server()
    with pytest.raises(StoreRefused):
        server.create_repository(NAME, "x")
    with pytest.raises(StoreRefused):
        server.statement_counts("jms-kapps")
    assert server.requests == []
