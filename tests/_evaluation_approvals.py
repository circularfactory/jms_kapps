"""Approval files for tests (jms_kapps 96). Each approves a spec of a temporary folder, for a period
around the time of the test: none approves a spec of the repository, and no test makes a heavy run
on a store (`tests/test_evaluation_approval.py` checks that no file holds a shipped spec's key)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from jms_kapps.evaluation.approval import (
    Approval,
    Covered,
    cover,
    read_approval,
    spec_key,
)
from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.spec import configurations, read_spec, resolved


def zulu(moment: datetime) -> str:
    """A time as an approval file writes it, a TOML time in UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def approval_file(
    folder: Path,
    spec: Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    approver: str = "Test Person",
    text: str = "approved for a test, with no server",
    name: str = "approval.toml",
    key: str | None = None,
) -> Path:
    """The approval file `name` in `folder` for the spec at `spec`: by default for the hour before
    now to the hour after."""
    now = datetime.now(UTC)
    start = start if start is not None else now - timedelta(hours=1)
    end = end if end is not None else now + timedelta(hours=1)
    path = folder / name
    path.write_text(
        f'spec_key = "{key if key is not None else spec_key(spec)}"\n'
        f"start = {zulu(start)}\n"
        f"end = {zulu(end)}\n"
        f'approver = "{approver}"\n'
        f'text = "{text}"\n',
        encoding="utf-8",
    )
    return path


def approval_of(folder: Path, spec: Path, **options: object) -> Approval:
    """The approval `approval_file` writes, read."""
    return read_approval(approval_file(folder, spec, **options))  # type: ignore[arg-type]


# A spec of two heavy configurations, for the tests of the run entry points.
TWO_HEAVY_SPEC = """\
[defaults]
boxes_per_minute = 6
warm_up_seconds = 0
window_seconds = 60
drain_bound_seconds = 120
matcher_interval_seconds = 2
heavy = true
repetitions = 1

[[sweep]]
topology = ["cross", "pair"]
"""


def covered_for(
    folder: Path,
    *,
    number: int = 1,
    spec_text: str = TWO_HEAVY_SPEC,
    name: str = "spec.toml",
    now: datetime | None = None,
    **approval_options: object,
) -> tuple[Covered, Configuration]:
    """The covered record that `cover` makes for configuration `number` of the spec `spec_text`
    (written to `name` in `folder`), with that configuration resolved. The approval is checked at
    `now`: the start of its period by default."""
    spec_path = folder / name
    spec_path.write_text(spec_text, encoding="utf-8")
    approval = approval_of(
        folder, spec_path, name=f"approval-{name}", **approval_options
    )
    spec = read_spec(spec_path)
    chosen = configurations(spec)[number - 1]
    configuration = resolved(chosen, spec=spec, prefills=folder)
    covered = cover(
        approval,
        spec=spec_path,
        key=chosen.key,
        configuration=configuration,
        now=now if now is not None else approval.start,
    )
    return covered, configuration
