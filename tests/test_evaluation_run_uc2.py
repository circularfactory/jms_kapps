"""`jms-eval-uc2`, the command of one UC2 run of the measurement harness (jms_kapps 47): what it
refuses before anything reaches a store. The GraphDB connection variables are removed first, so
that no test here can reach one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _evaluation_fakes import PASSWORD, USERNAME, FakeGraphDB

import jms_kapps
from jms_kapps.evaluation import run_uc2, wrapper
from jms_kapps.evaluation.configuration import KEYS, read_configuration
from jms_kapps.evaluation.run_uc2 import main

CONFIGURATION = """\
topology = "cross"
prefill = "none"
module_strategy = "real"
wms_strategy = "real"
shacl = true
collect_history = false
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
transit_seconds = 3.0
retry_seconds = 1.0
wrapper = {wrapper}
heavy = {heavy}
"""


@pytest.fixture(autouse=True)
def no_graphdb(monkeypatch):
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"TRIPLESTORE_{name}", raising=False)
        monkeypatch.delenv(f"GRAPHDB_{name}", raising=False)


def configuration(
    folder: Path, *, heavy: bool = False, wrapper: bool = True, drop: str = ""
) -> Path:
    path = folder / "run.toml"
    text = CONFIGURATION.format(
        heavy="true" if heavy else "false", wrapper="true" if wrapper else "false"
    )
    path.write_text(
        "".join(
            line + "\n"
            for line in text.splitlines()
            if not line.startswith(drop or "#")
        ),
        encoding="utf-8",
    )
    return path


def test_a_heavy_run_without_the_approval_is_refused(tmp_path, capsys):
    runs = tmp_path / "runs"

    code = main([str(configuration(tmp_path, heavy=True)), "--runs", str(runs)])

    assert code == 2
    assert "--heavy-run-approval" in capsys.readouterr().err
    assert not runs.exists()


def test_a_configuration_with_a_missing_value_is_refused_by_its_name(tmp_path, capsys):
    code = main([str(configuration(tmp_path, drop="window_seconds"))])

    assert code == 2
    assert "window_seconds" in capsys.readouterr().err


def test_without_a_configured_graphdb_the_run_does_not_start(tmp_path, capsys):
    runs = tmp_path / "runs"

    code = main([str(configuration(tmp_path)), "--runs", str(runs)])

    assert code == 2
    err = capsys.readouterr().err
    assert "TRIPLESTORE_URL" in err and "TRIPLESTORE_PASSWORD" in err
    assert not runs.exists()


def test_without_opentelemetry_a_run_with_the_wrapper_is_refused_before_it_starts(
    tmp_path, monkeypatch, capsys
):
    """A plain install of jms_kapps has no OpenTelemetry, the extra `evaluation` (jms_kapps
    48): a run with the wrapper is refused, and says how to install it; nothing is created."""
    monkeypatch.setattr(wrapper, "missing", lambda: ["opentelemetry.sdk"])
    runs = tmp_path / "runs"

    code = main([str(configuration(tmp_path)), "--runs", str(runs)])

    assert code == 2
    err = capsys.readouterr().err
    assert "opentelemetry.sdk" in err and "uv sync --extra evaluation" in err
    assert "wrapper = false" in err
    assert not runs.exists()


def test_without_opentelemetry_a_run_without_the_wrapper_goes_on(
    tmp_path, monkeypatch, capsys
):
    """It stops only where nothing is configured: the store's variables."""
    monkeypatch.setattr(wrapper, "missing", lambda: ["opentelemetry.sdk"])

    code = main([str(configuration(tmp_path, wrapper=False))])

    assert code == 2
    assert "TRIPLESTORE_URL" in capsys.readouterr().err


EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"


def test_the_readme_of_the_evaluation_states_iteration_one_and_the_approval():
    readme = (EVALUATION / "README.md").read_text(encoding="utf-8")

    assert "## Run one configuration" in readme
    assert "iteration one" in readme
    assert "--heavy-run-approval" in readme
    assert "needs an approval that covers it: any person may approve" in readme
    assert "Etienne Hoffmann's approval" not in readme
    assert "in agreement with the server's admins" in " ".join(readme.split())
    for key in KEYS:
        assert f"| `{key}` |" in readme, key


@pytest.mark.parametrize(
    "name", ["cross-short.toml", "cross-short-shacl-off.toml", "grid-short.toml"]
)
def test_the_shipped_short_runs_resolve_and_are_light(name):
    configuration = read_configuration(EVALUATION / "configurations" / name)

    assert configuration.topology == name.split("-")[0]
    assert not configuration.heavy
    assert configuration.shacl == ("off" not in name)
    assert configuration.wrapper
    assert configuration.boxes_per_minute <= 6 and configuration.window_seconds <= 60


@pytest.mark.parametrize(
    ("line", "named"),
    [
        ('topology = "no-such-grid.json"', "topology"),
        ('prefill = "no-such-prefill.ttl"', "prefill"),
        ('module_strategy = "no-such-strategy"', "module_strategy"),
        ('wms_strategy = "no-such-strategy"', "wms_strategy"),
    ],
)
def test_a_missing_file_or_strategy_is_refused_and_no_repository_is_created(
    tmp_path, monkeypatch, capsys, line, named
):
    """With a stand-in of the store configured, so that only the refusal stops the run."""
    store = FakeGraphDB()
    monkeypatch.setenv("TRIPLESTORE_URL", "http://graphdb.test")
    monkeypatch.setenv("TRIPLESTORE_USERNAME", USERNAME)
    monkeypatch.setenv("TRIPLESTORE_PASSWORD", PASSWORD)
    monkeypatch.setattr(run_uc2, "GraphDBServer", lambda *_, **__: store.server())
    key = line.split(" = ")[0]
    path = configuration(tmp_path, drop=key)
    path.write_text(path.read_text() + line + "\n", encoding="utf-8")

    code = main([str(path), "--runs", str(tmp_path / "runs")])

    assert code == 2
    assert named in capsys.readouterr().err
    assert store.requests == []
    assert store.created() == []
