"""One approval covers the heavy runs of one spec in one period (jms_kapps 96, from the decisions of
jms_kapps 85, note 1864593): the approval file, and the three commands that start a heavy run,
`jms-eval-sweep`, `jms-eval-uc2` and `jms-eval-baseline`.

No test here makes a heavy run on a store, and none holds an approval that covers a real spec of the
repository: each approves a spec of a temporary folder, and a run is a stand-in that only keeps
what it was given. The connection variables of a store are removed first.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from _evaluation_approvals import approval_file, covered_for
from _repo import find_repo_root, repository_files
from test_evaluation_baseline import WARM_UP, Baseline, recorded_run
from test_evaluation_sweep_ramp import SPEC as RAMP_SPEC
from test_evaluation_sweep_ramp import FabricatedRuns

import jms_kapps
from jms_kapps.evaluation import baseline as baseline_module
from jms_kapps.evaluation import run_uc2, sweep
from jms_kapps.evaluation.approval import (
    HeavyRunRefused,
    approved,
    cover,
    read_approval,
    spec_key,
)
from jms_kapps.evaluation.configuration import DEFAULT_BASE_SEED, pick_seed
from jms_kapps.evaluation.spec import configurations, read_spec, resolved

EVALUATION = Path(jms_kapps.__file__).parent / "evaluation"

# A spec of two ramps, a sustained load and a configuration at normal load (jms_kapps 96).
SPEC = (
    RAMP_SPEC
    + """
# 8-9: a second ramp.
[[sweep]]
ramp = true
topology = "pair"
boxes_per_minute = [10, 20]
repetitions = 1

# 10: a sustained load, heavy without being a ramp.
[[sweep]]
topology = "cross"
module_strategy = "real"
boxes_per_minute = 50
heavy = true
repetitions = 1
"""
)
RAMP_NUMBERS = {2, 3, 4, 5, 6, 8, 9}
HEAVY_NUMBERS = RAMP_NUMBERS | {10}
NOW = datetime.now(UTC)

# A spec of one heavy configuration, for the commands of one run.
HEAVY_SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = true
repetitions = 2

[[sweep]]
topology = "cross"
"""


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    for prefix in ("GRAPHDB", "TRIPLESTORE"):
        for name in ("URL", "USERNAME", "PASSWORD"):
            monkeypatch.delenv(f"{prefix}_{name}", raising=False)


@pytest.fixture
def store(monkeypatch):
    """A store is configured, so that a refusal below is the approval's, not the missing store's."""
    for name in ("URL", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"TRIPLESTORE_{name}", "x")


def write_spec(folder: Path, text: str, name: str = "spec.toml") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


class Stand_in:
    """In place of a run of `jms-eval-uc2` or of the baseline on the configured store: it keeps
    what it was given and makes no run."""

    def __init__(self) -> None:
        self.made: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, what: Any, **options: Any) -> dict[str, Any]:
        self.made.append((what, options))
        return {"outcome": "completed", "run_id": "stand-in"}


# The approval file.


def test_an_approval_file_is_read_with_its_spec_key_period_approver_and_text(tmp_path):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    path = approval_file(
        tmp_path,
        spec,
        start=datetime(2026, 10, 13, 20, 0, tzinfo=UTC),
        end=datetime(2026, 10, 14, 6, 0, tzinfo=UTC),
        approver="Test Person",
        text="with the server's admins",
    )

    approval = read_approval(path)

    assert approval.record() == {
        "spec_key": spec_key(spec),
        "start": "2026-10-13T20:00:00+00:00",
        "end": "2026-10-14T06:00:00+00:00",
        "approver": "Test Person",
        "text": "with the server's admins",
    }


def test_the_spec_key_is_the_sha_256_of_the_spec_file_s_content(tmp_path):
    import hashlib

    spec = write_spec(tmp_path, HEAVY_SPEC)

    assert spec_key(spec) == hashlib.sha256(HEAVY_SPEC.encode()).hexdigest()


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (("approver", "approver_name"), "does not know: approver_name"),
        (("text = ", "# text = "), "no text"),
        (("start = ", "start = 'x' # "), "start is a time in UTC"),
        (('spec_key = "', 'spec_key = "x'), "64 hex digits"),
        (('approver = "Test Person"', 'approver = " "'), "approver is a text"),
    ],
)
def test_an_approval_file_that_is_not_one_is_refused_with_a_reason(
    tmp_path, change, named
):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    path = approval_file(tmp_path, spec)
    old, new = change
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))

    with pytest.raises(HeavyRunRefused, match=named):
        read_approval(path)


def test_a_period_that_ends_before_it_starts_is_refused(tmp_path):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    path = approval_file(tmp_path, spec, start=NOW, end=NOW - timedelta(hours=1))

    with pytest.raises(HeavyRunRefused, match="ends .* at or before it starts"):
        read_approval(path)


def test_a_time_without_its_zone_is_refused(tmp_path):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    path = approval_file(tmp_path, spec)
    path.write_text(
        re.sub(r"(start = .*)Z", r"\1", path.read_text(), count=1), encoding="utf-8"
    )

    with pytest.raises(HeavyRunRefused, match="start is a time in UTC with its zone"):
        read_approval(path)


def test_a_file_that_is_no_approval_is_refused(tmp_path):
    with pytest.raises(HeavyRunRefused, match="cannot be read"):
        read_approval(tmp_path / "none.toml")
    path = tmp_path / "bad.toml"
    path.write_text("this is = = no toml")
    with pytest.raises(HeavyRunRefused, match="is no TOML"):
        read_approval(path)


def test_a_run_at_normal_load_needs_no_approval():
    assert approved(False, None) is None


# The sweep.


def sweep_args(
    tmp_path: Path, spec: Path, *more: str, approval: Path | None = None
) -> list[str]:
    return [
        str(spec),
        "--runs",
        str(tmp_path / "runs"),
        *more,
        *([] if approval is None else ["--heavy-run-approval", str(approval)]),
    ]


def test_inside_the_period_the_sweep_runs_every_heavy_configuration_of_the_approved_spec(
    tmp_path, store, monkeypatch
):
    """Several ramps and a sustained load alike, under the one approval: each heavy run is given
    it, a run at normal load is not."""
    spec = write_spec(tmp_path, SPEC)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)
    approval = approval_file(tmp_path, spec)

    code = sweep.main(sweep_args(tmp_path, spec, approval=approval))

    assert code == 0
    given = {number: approval_ for number, _, approval_ in fabricated.made}
    assert set(given) == {1, 7} | HEAVY_NUMBERS
    assert all(given[number] is not None for number in HEAVY_NUMBERS)
    assert all(given[number] is None for number in (1, 7))
    assert {given[number].approval for number in HEAVY_NUMBERS} == {
        read_approval(approval)
    }


@pytest.mark.parametrize(
    ("start", "end", "named"),
    [
        (NOW - timedelta(days=2), NOW - timedelta(days=1), "outside the period"),
        (NOW + timedelta(days=1), NOW + timedelta(days=2), "outside the period"),
    ],
    ids=["the period has ended", "the period has not begun"],
)
def test_a_heavy_run_outside_the_period_is_skipped_with_a_reason(
    tmp_path, store, monkeypatch, capsys, start, end, named
):
    """Skipped and listed, and the sweep goes on with the normal runs (jms_kapps 112: until
    2026-10-14 refused before the first run)."""
    spec = write_spec(tmp_path, SPEC)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)
    approval = approval_file(tmp_path, spec, start=start, end=end)

    code = sweep.main(sweep_args(tmp_path, spec, approval=approval))

    assert code == 0
    out = capsys.readouterr().out
    assert named in out and "approved by Test Person" in out
    assert {number for number, _, _ in fabricated.made} == {1, 7}


def test_a_heavy_run_of_another_spec_is_skipped_with_a_reason(
    tmp_path, store, monkeypatch, capsys
):
    other = write_spec(tmp_path, SPEC + "\n# another spec\n", name="other.toml")
    spec = write_spec(tmp_path, SPEC)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = sweep.main(
        sweep_args(tmp_path, spec, approval=approval_file(tmp_path, other))
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "it is another spec" in out
    assert {number for number, _, _ in fabricated.made} == {1, 7}


def test_a_heavy_run_of_a_changed_spec_file_is_skipped_with_a_reason(
    tmp_path, store, monkeypatch, capsys
):
    spec = write_spec(tmp_path, SPEC)
    approval = approval_file(tmp_path, spec)
    spec.write_text(SPEC.replace("[10, 20, 40, 80, 160]", "[10, 20, 40, 80, 320]"))
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = sweep.main(sweep_args(tmp_path, spec, approval=approval))

    assert code == 0
    assert "changed since it was approved" in capsys.readouterr().out
    assert {number for number, _, _ in fabricated.made} == {1, 7}


def test_a_heavy_run_with_set_overrides_is_skipped_with_a_reason(
    tmp_path, store, monkeypatch, capsys
):
    """The approval covers the spec as written: the overrides make other configurations."""
    spec = write_spec(tmp_path, SPEC)
    approval = approval_file(tmp_path, spec)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = sweep.main(
        sweep_args(tmp_path, spec, "--set", "window_seconds=30", approval=approval)
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "--set overrides is not covered by an approval" in out
    assert {number for number, _, _ in fabricated.made} == {1, 7}


def test_set_overrides_at_normal_load_need_no_approval(tmp_path, store, monkeypatch):
    spec = write_spec(tmp_path, SPEC)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = sweep.main(
        sweep_args(
            tmp_path, spec, "--set", "window_seconds=30", "--configurations", "1,7"
        )
    )

    assert code == 0
    assert [approval for _, _, approval in fabricated.made] == [None] * 4


def test_a_heavy_run_without_an_approval_is_skipped_with_a_reason(
    tmp_path, store, monkeypatch, capsys
):
    spec = write_spec(tmp_path, SPEC)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = sweep.main(sweep_args(tmp_path, spec, "--configurations", "10"))

    assert code == 0
    out = capsys.readouterr().out
    assert "configuration 10, repetition 1 (heavy = true): no approval" in out
    assert "any person may approve" in out and "--heavy-run-approval" in out
    assert fabricated.made == []


def test_a_sweep_skips_a_heavy_run_when_the_period_ends(tmp_path):
    """The period is checked again at each heavy run's start (`bench.run`): the sweep skips that
    run and lists it, and nothing was sent for it (jms_kapps 112: until 2026-10-14 the sweep
    stopped there)."""
    spec = read_spec(write_spec(tmp_path, SPEC))
    found = configurations(spec)
    chosen = [c for c in found if c.number == 10]
    runs = sweep.plan_runs(
        spec,
        [(c, 1) for c in chosen],
        done={},
        prefills=tmp_path,
        approval=read_approval(approval_file(tmp_path, spec.path)),
    )
    assert runs[0].heavy is not None
    lines: list[str] = []

    def refuse(*args: object, **options: object) -> dict:
        raise HeavyRunRefused("it is 2026-10-14 06:00 UTC, outside the period")

    completed = sweep.run_sweep(
        spec,
        runs,
        runs=tmp_path / "runs",
        run_one=refuse,
        echo=lines.append,
        found=found,
    )

    assert completed
    assert any("configuration 10, repetition 1 (heavy = true)" in l for l in lines)
    assert any("outside the period" in l for l in lines)


# jms-eval-uc2.


def uc2_args(
    tmp_path: Path,
    spec: Path,
    number: int = 1,
    *more: str,
    approval: Path | None = None,
) -> list[str]:
    return [
        "--spec",
        str(spec),
        "--configuration",
        str(number),
        "--prefills",
        str(tmp_path / "prefills"),
        "--runs",
        str(tmp_path / "runs"),
        *more,
        *([] if approval is None else ["--heavy-run-approval", str(approval)]),
    ]


def test_uc2_makes_a_covered_heavy_run_from_the_spec_and_the_configuration_s_number(
    tmp_path, store, monkeypatch
):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    approval = approval_file(tmp_path, spec)
    stand_in = Stand_in()
    monkeypatch.setattr(run_uc2, "run_on_the_configured_store", stand_in)

    code = run_uc2.main(uc2_args(tmp_path, spec, 1, approval=approval))

    assert code == 0
    [(configuration, options)] = stand_in.made
    [chosen] = configurations(read_spec(spec))
    assert configuration.heavy
    assert configuration.wms_pick_seed == pick_seed(DEFAULT_BASE_SEED, 1)
    assert options["sweep"] == {
        "spec": str(spec),
        "configuration": 1,
        "key": chosen.key,
        "repetition": 1,
        "repetitions": 2,
    }
    assert options["approval"].approval == read_approval(approval)
    assert options["approval"].configuration_key == chosen.key


def test_a_run_from_a_spec_takes_the_repetition_s_pick_seed_as_a_sweep_s_run_does(
    tmp_path, store, monkeypatch
):
    spec = write_spec(tmp_path, HEAVY_SPEC.replace("heavy = true", "heavy = false"))
    stand_in = Stand_in()
    monkeypatch.setattr(run_uc2, "run_on_the_configured_store", stand_in)

    code = run_uc2.main(uc2_args(tmp_path, spec, 1, "--repetition", "2"))

    assert code == 0
    [(configuration, options)] = stand_in.made
    assert configuration.wms_pick_seed == pick_seed(DEFAULT_BASE_SEED, 2)
    assert options["sweep"]["repetition"] == 2
    assert options["approval"] is None


def test_a_heavy_run_of_a_bare_configuration_file_is_refused_even_under_an_approval(
    tmp_path, store, monkeypatch, capsys
):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    bare = tmp_path / "bare.toml"
    bare.write_text(
        (EVALUATION / "configurations" / "cross-short.toml")
        .read_text()
        .replace("heavy = false", "heavy = true")
    )
    stand_in = Stand_in()
    monkeypatch.setattr(run_uc2, "run_on_the_configured_store", stand_in)

    code = run_uc2.main(
        [str(bare), "--heavy-run-approval", str(approval_file(tmp_path, spec))]
    )

    assert code == 2
    assert "a bare configuration file, or no --spec" in capsys.readouterr().err
    assert stand_in.made == []


@pytest.mark.parametrize("case", ["none", "ended", "another spec", "changed spec"])
def test_uc2_refuses_a_heavy_run_that_no_approval_covers(
    tmp_path, store, monkeypatch, capsys, case
):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    other = write_spec(tmp_path, HEAVY_SPEC + "# other\n", name="other.toml")
    approval: Path | None = None
    if case == "ended":
        approval = approval_file(
            tmp_path, spec, start=NOW - timedelta(days=2), end=NOW - timedelta(days=1)
        )
    elif case == "another spec":
        approval = approval_file(tmp_path, other)
    elif case == "changed spec":
        approval = approval_file(tmp_path, spec)
    if case == "changed spec":
        spec.write_text(
            HEAVY_SPEC.replace("window_seconds = 60", "window_seconds = 61")
        )
    stand_in = Stand_in()
    monkeypatch.setattr(run_uc2, "run_on_the_configured_store", stand_in)

    code = run_uc2.main(uc2_args(tmp_path, spec, 1, approval=approval))

    assert code == 2
    error = capsys.readouterr().err
    assert {
        "none": "any person may approve",
        "ended": "outside the period",
        "another spec": "it is another spec",
        "changed spec": "changed since it was approved",
    }[case] in error
    assert stand_in.made == []
    assert not (tmp_path / "runs").exists()


def test_uc2_refuses_a_number_the_spec_does_not_have(tmp_path, store, capsys):
    spec = write_spec(tmp_path, HEAVY_SPEC)

    code = run_uc2.main(uc2_args(tmp_path, spec, 3))

    assert code == 2
    assert "there is no configuration 3" in capsys.readouterr().err


# jms-eval-baseline.


def spec_of(configuration: Any) -> str:
    """A spec text with one configuration: `configuration`'s values, heavy."""
    values = configuration.to_toml().replace("heavy = false", "heavy = true")
    return f"[defaults]\n{values}repetitions = 1\n\n[[sweep]]\n"


def heavy_recorded_run(
    tmp_path: Path,
    spec: Path | None,
    *,
    key: str | None,
    change: tuple[str, str] | None = None,
) -> Path:
    """The folder of a recorded run made heavy in its record, with the configuration key `key`
    in its sweep fields (None: a record that holds no key). `change` edits the record's
    configuration text, so that it no longer matches the key."""
    folder = recorded_run(tmp_path, lambda clock: [], WARM_UP)
    found = json.loads((folder / "record.json").read_text())
    found["configuration"] = found["configuration"].replace(
        "heavy = false", "heavy = true"
    )
    if change is not None:
        assert change[0] in found["configuration"]
        found["configuration"] = found["configuration"].replace(*change)
    found["sweep"] = None if key is None else {"spec": str(spec), "key": key}
    (folder / "record.json").write_text(json.dumps(found))
    return folder


def baseline_args(tmp_path: Path, recorded: Path, *more: str) -> list[str]:
    return [str(recorded), "--runs", str(tmp_path / "b"), *more]


def test_the_baseline_replays_a_heavy_run_whose_key_the_approval_covers(
    tmp_path, store, monkeypatch
):
    spec = write_spec(tmp_path, spec_of(WARM_UP))
    [chosen] = configurations(read_spec(spec))
    recorded = heavy_recorded_run(tmp_path, spec, key=chosen.key)
    approval = approval_file(tmp_path, spec)
    stand_in = Stand_in()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)

    code = baseline_module.main(
        baseline_args(
            tmp_path,
            recorded,
            "--spec",
            str(spec),
            "--heavy-run-approval",
            str(approval),
        )
    )

    assert code == 0
    [(_, options)] = stand_in.made
    assert options["approval"].approval == read_approval(approval)


@pytest.mark.parametrize(
    "case",
    [
        "no key",
        "a key the spec does not list",
        "a configuration that is not its key's",
        "no spec given",
        "no approval",
        "ended",
        "another spec",
    ],
)
def test_the_baseline_refuses_a_heavy_run_that_no_approval_covers(
    tmp_path, store, monkeypatch, capsys, case
):
    spec = write_spec(tmp_path, spec_of(WARM_UP))
    other = write_spec(tmp_path, spec_of(WARM_UP) + "# other\n", name="other.toml")
    [chosen] = configurations(read_spec(spec))
    key = {
        "no key": None,
        "a key the spec does not list": "0123456789abcdef",
    }.get(case, chosen.key)
    change = (
        ("boxes_per_minute = 6.0", "boxes_per_minute = 7.0")
        if case == "a configuration that is not its key's"
        else None
    )
    recorded = heavy_recorded_run(tmp_path, spec, key=key, change=change)
    approval = approval_file(
        tmp_path,
        other if case == "another spec" else spec,
        **(
            {"start": NOW - timedelta(days=2), "end": NOW - timedelta(days=1)}
            if case == "ended"
            else {}
        ),
    )
    stand_in = Stand_in()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)
    options = ["--spec", str(spec)] if case != "no spec given" else []
    if case != "no approval":
        options += ["--heavy-run-approval", str(approval)]

    code = baseline_module.main(baseline_args(tmp_path, recorded, *options))

    assert code == 2
    error = capsys.readouterr().err
    assert {
        "no key": "holds no configuration key",
        "a key the spec does not list": "is no configuration of the approved spec",
        "a configuration that is not its key's": "does not match its key",
        "no spec given": "no spec",
        "no approval": "any person may approve",
        "ended": "outside the period",
        "another spec": "it is another spec",
    }[case] in error
    assert stand_in.made == []
    assert not (tmp_path / "b").exists()


def test_the_baseline_run_takes_only_the_covered_record_of_its_own_configuration(
    tmp_path,
):
    """The library entry point, `run_baseline`: a raw approval and a covered record of another
    configuration are refused before anything is sent."""
    recorded = heavy_recorded_run(tmp_path, None, key=None)
    covered, _ = covered_for(tmp_path)
    for given, named in (
        (covered.approval, "nothing has checked"),
        (covered, "another configuration"),
    ):
        baseline = Baseline(tmp_path / named.split()[0])

        with pytest.raises(HeavyRunRefused, match=named):
            baseline.run(recorded, approval=given)

        assert baseline.store.requests == []
        assert baseline.replay_store.arrived == []
        assert not baseline.runs.exists()


def test_the_baseline_of_a_run_at_normal_load_needs_no_approval_and_no_spec(
    tmp_path, store, monkeypatch
):
    recorded = recorded_run(tmp_path, lambda clock: [], WARM_UP)
    stand_in = Stand_in()
    monkeypatch.setattr(baseline_module, "run_on_the_configured_store", stand_in)

    assert baseline_module.main(baseline_args(tmp_path, recorded)) == 0
    assert stand_in.made[0][1]["approval"] is None


# What the approval needs of the repository.


def test_cover_names_each_case_it_refuses(tmp_path):
    spec = write_spec(tmp_path, HEAVY_SPEC)
    parsed = read_spec(spec)
    [chosen] = configurations(parsed)
    configuration = resolved(chosen, spec=parsed, prefills=tmp_path)
    approval = read_approval(approval_file(tmp_path, spec))
    options: dict[str, Any] = {
        "spec": spec,
        "key": chosen.key,
        "configuration": configuration,
    }

    assert cover(approval, **options).approval.approver == "Test Person"
    with pytest.raises(HeavyRunRefused, match="--set overrides"):
        cover(approval, **options, overridden=True)
    with pytest.raises(HeavyRunRefused, match="no configuration key"):
        cover(approval, **(options | {"key": None}))
    with pytest.raises(HeavyRunRefused, match="outside the period"):
        cover(approval, **options, now=NOW + timedelta(days=1))
    changed = replace(configuration, boxes_per_minute=7.0)
    with pytest.raises(HeavyRunRefused, match="does not match its key"):
        cover(approval, **(options | {"configuration": changed}))


def test_no_file_of_the_repository_holds_the_key_of_a_shipped_spec():
    """No test, CI job or example gives an approval that covers a real spec (jms_kapps 96): such an
    approval would name the spec's SHA-256, which no tracked file holds."""
    root = find_repo_root(Path(__file__))
    keys = {spec_key(path): path.name for path in (EVALUATION / "specs").glob("*.toml")}
    assert keys, "the shipped specs are found"
    holding: list[str] = []
    for path in repository_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        name = path.relative_to(root).as_posix()
        holding.extend(f"{name}: {spec}" for key, spec in keys.items() if key in text)
    assert holding == []


def test_no_text_of_the_harness_names_etienne_as_the_approver():
    """Any person approves (jms_kapps 96). The clause about the server's admins stays."""
    texts = {
        path.name: path.read_text(encoding="utf-8")
        for path in [
            EVALUATION / "approval.py",
            EVALUATION / "sweep.py",
            EVALUATION / "run_uc2.py",
            EVALUATION / "baseline.py",
            EVALUATION / "ramp.py",
        ]
    }
    for name, text in texts.items():
        assert not re.search(
            r"Etienne\s+Hoffmann's approval|Etienne's approval", text
        ), name
        assert "needs Etienne" not in text, name
    for module in (sweep, run_uc2, baseline_module):
        parser_help = subprocess.run(
            [sys.executable, "-m", module.__name__, "--help"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "any person may" in parser_help, module.__name__
        assert "Etienne" not in parser_help, module.__name__
        assert "admins" in " ".join(parser_help.split()), module.__name__


AFTER_SERIES = EVALUATION / "specs" / "after-series.toml"


def test_the_draft_after_series_spec_has_no_heavy_cell():
    """The shipped draft holds no heavy cell, so no run of it needs an approval; one that
    turned `heavy` on would (next test)."""
    found = configurations(read_spec(AFTER_SERIES))

    assert found
    assert not any(c.values["heavy"] for c in found)


def test_a_heavy_cell_of_a_spec_with_the_precision_rule_is_skipped_without_an_approval(
    tmp_path, store, monkeypatch, capsys
):
    """Under the precision rule (jms_kapps 101) the sweep still covers each heavy run on its
    own: the draft after-series spec with heavy cells and no approval makes no run (jms_kapps
    112: each is skipped and listed, where until 2026-10-14 the call was refused)."""
    text = re.sub(
        r"(?m)^prefill = .*$",
        'prefill = "none"  # no prefill files in a test',
        AFTER_SERIES.read_text(encoding="utf-8")
        .replace("heavy = false", "heavy = true")
        # The topology files from the shipped folder: no heavy run is refused before the first
        # run any more, so each configuration resolves (jms_kapps 112).
        .replace('"../topologies/', f'"{EVALUATION / "topologies"}/'),
    )
    assert "[precision]" in text and "heavy = true" in text
    spec = write_spec(tmp_path, text)
    fabricated = FabricatedRuns()
    monkeypatch.setattr(sweep, "run_on_the_configured_store", fabricated)

    code = sweep.main(
        [
            str(spec),
            "--runs",
            str(tmp_path / "runs"),
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "(heavy = true): no approval" in out and "any person may approve" in out
    assert fabricated.made == []
