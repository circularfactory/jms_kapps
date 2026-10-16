"""The graphdb tests name their dataset: a run that would clear one without being told which
stops before any test runs.

Each case is a small pytest session in this process (pytest's own `pytester`), with the plugin
module of the option loaded and a test file that asks for the `dataset` fixture the way the
graphdb tests do. The connection variables of the cases point nowhere, and no test of the sessions
connects: no store is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kapps_semantic_middleware.credentials import REQUIRED_TRIPLESTORE_ENV

TESTS = Path(__file__).parent

# The deprecated variable of each connection variable, which the store reads where the variable
# itself is not set.
DEPRECATED = {
    "TRIPLESTORE_URL": "GRAPHDB_URL",
    "TRIPLESTORE_USERNAME": "GRAPHDB_USERNAME",
    "TRIPLESTORE_PASSWORD": "GRAPHDB_PASSWORD",
}

# The dataset names a run may give, among them a disposable one, as the live criteria run on.
NAMES = ("Tests", "jms-kapps", "ogm-disposable-jms62")

# What a graphdb test looks like to pytest, and one test that needs no dataset.
SESSION_TESTS = f"""
import pytest
from kapps_semantic_middleware.credentials import triplestore_env_present


@pytest.mark.graphdb
@pytest.mark.skipif(
    not triplestore_env_present(), reason="no triple store connection variables"
)
def test_live(dataset):
    assert dataset in {NAMES!r}


def test_offline():
    pass
"""


@pytest.fixture
def session(pytester: pytest.Pytester) -> pytest.Pytester:
    """A session with the option's plugin module and the two tests above."""
    pytester.syspathinsert(TESTS)
    pytester.makeini("[pytest]\nmarkers =\n    graphdb: needs a live GraphDB\n")
    pytester.makepyfile(SESSION_TESTS)
    return pytester


def connection_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variables that make the graphdb tests run, and point at no server."""
    for name in REQUIRED_TRIPLESTORE_ENV:
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("TRIPLESTORE_ALLOW_DESTRUCTIVE", raising=False)


def no_connection_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """No connection variable, neither one of the store's nor its deprecated one."""
    for name in REQUIRED_TRIPLESTORE_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(DEPRECATED[name], raising=False)


def run(session: pytest.Pytester, *args: str) -> pytest.RunResult:
    # Without pytest-playwright: its hooks need a browser run's state that these sessions lack.
    return session.runpytest_inprocess(
        "-p", "_dataset_option", "-p", "no:playwright", *args
    )


@pytest.mark.parametrize("selection", [(), ("-m", "graphdb")])
def test_a_run_with_the_variables_set_and_no_dataset_stops_before_any_test(
    session, monkeypatch, selection
):
    connection_variables(monkeypatch)
    result = run(session, *selection)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    message = (
        "*the graphdb tests clear the dataset they run against; name it with "
        "`--dataset <name>`, for example `--dataset Tests`; there is no default*"
    )
    result.stderr.fnmatch_lines([message])
    result.assert_outcomes()  # nothing passed, failed or skipped: no test ran


def test_a_run_that_selects_no_test_needing_the_dataset_runs_as_before(
    session, monkeypatch
):
    connection_variables(monkeypatch)
    result = run(session, "-m", "not graphdb")
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=1)


def test_a_run_without_the_variables_skips_the_graphdb_test_and_needs_no_dataset(
    session, monkeypatch
):
    no_connection_variables(monkeypatch)
    result = run(session)
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, skipped=1)


@pytest.mark.parametrize("name", NAMES)
def test_any_named_dataset_reaches_the_fixture(session, monkeypatch, name):
    """Offline: the test is not skipped for want of a store, because the variables are set, and
    asserts what the fixture returns. It cannot connect: it uses no store."""
    connection_variables(monkeypatch)
    result = run(session, "--dataset", name)
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=2)
