"""The approval of heavy runs.

A sustained-load or breaking-point run on the configured GraphDB is never a unit test or a CI job:
other people use that server, so such a run starts only under an approval that covers it. Any
person may approve, in agreement with the server's admins.

**An approval is a small file** (TOML), passed by the option `--heavy-run-approval`:

    spec_key = "<the SHA-256 of the spec file's content>"
    start = 2026-10-13T20:00:00Z
    end = 2026-10-14T06:00:00Z
    approver = "<who approved>"
    text = "<what was approved, and with whom: the server's admins>"

It covers **one spec for one period**. The spec key is the SHA-256 of the spec file's content
(`spec_key`; `sha256sum <spec>` shows it), so the approval covers the spec as written: another
spec, or the same file once it is changed, has another key. The period is `start` up to but not
including `end`, in UTC: a time without a zone is refused. Inside the period it covers every heavy
configuration of the spec, which is the spec's configurations (`spec.configurations`) by their
keys, several ramps and sustained loads alike.

**Refused, before anything reaches a store** (`HeavyRunRefused`, with the reason, and its short
reason for the sweep's list of skipped runs): a heavy run without an approval;
outside its period; of another spec or a changed spec file; with `--set` overrides, which the
approval does not cover; of a bare configuration file, which has no spec; and a replay of a run
whose record holds no configuration key. The sweep and `jms-eval` check each heavy run at its
start, and skip and list one that is refused then. A run at normal load needs no approval.
A replay of a recorded run compressed in time by a factor above 1 is a heavy run too (`replay_levels`): the baseline and the sweep's replay levels take a `Covered` for the replayed
configuration, as they do for a heavy run, and a refusal names the factor (`why`).
The record of each heavy run keeps the whole approval it ran under (`Approval.record`).

**The run entry points take only a covered record.** `cover` checks all of the above and makes a
`Covered`, which names the one configuration it covers (`fingerprint`: the key of the resolved
configuration, computed as the spec computes keys, over its values with a path by its file name).
`bench.run` and `baseline.run_baseline` take nothing else for a heavy run (`covered_run`): a raw
`Approval`, or a `Covered` of another configuration or spec, is refused, and so is a period that
does not hold at the run's start. Only `cover` makes a `Covered`. For a replay, `cover` is given the
configuration in the replayed record's text and refuses it where it is not the configuration its
stored key names in the approved spec.

No test, CI job or example gives an approval that covers a real spec of the repository
(`tests/test_evaluation_approval.py` checks that no file holds the key of a shipped spec).

Interim names: the approval file and its keys, and this module's API.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jms_kapps.evaluation.configuration import Configuration
from jms_kapps.evaluation.spec import (
    SpecRefused,
    configurations,
    fingerprint,
    lists,
    read_spec,
)

# What the approval file holds, for a person to read.
APPROVAL_FORM = (
    "an approval file (TOML) with spec_key (the SHA-256 of the spec file, `sha256sum <spec>`), "
    "start and end (UTC), approver and text"
)

# The keys of an approval file.
KEYS = ("spec_key", "start", "end", "approver", "text")

OPTION = "--heavy-run-approval"

# Why an approval does not cover a heavy run, in short, as the sweep lists a heavy run it skipped
# (interim names): no approval, another or a changed spec, a period that is not
# now, `--set` overrides, or another reason (a bare configuration file, a record without a key).
NO_APPROVAL = "no approval"
OTHER_SPEC = "another or a changed spec"
NOT_NOW = "a period that is not now"
OVERRIDDEN = "--set overrides"
NOT_COVERED = "not covered"


class HeavyRunRefused(RuntimeError):
    """A heavy run without an approval that covers it, or an approval that cannot be read.
    `reason` says why in short (`NO_APPROVAL`, `OTHER_SPEC`, `NOT_NOW`, `OVERRIDDEN` or
    `NOT_COVERED`; interim name); the message says it in full."""

    def __init__(self, message: str, reason: str = NOT_COVERED) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Approval:
    """An approval: any person's, for one spec and one period, in agreement with the server's
    admins."""

    spec_key: str
    start: datetime
    end: datetime
    approver: str
    text: str

    def record(self) -> dict[str, str]:
        """The whole approval as a run's record keeps it: spec key, period, approver and text."""
        return {
            "spec_key": self.spec_key,
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "approver": self.approver,
            "text": self.text,
        }


_ISSUER = object()


@dataclass(frozen=True)
class Covered:
    """An approval together with what it covers: the one heavy configuration of the one spec that
    `cover` found it to cover. Only `cover` makes one; the run entry points (`bench.run`,
    `baseline.run_baseline`) take nothing else for a heavy run, and check that it names the
    configuration they run (`fingerprint`) and that the approval's period holds at the run's
    start. (Interim names.)"""

    approval: Approval
    spec_key: str
    configuration_key: str
    fingerprint: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise HeavyRunRefused(
                "a covered record is made by approval.cover only: no approval is covered by "
                "being built"
            )


def spec_key(path: Path) -> str:
    """The key of the spec file at `path`: the SHA-256 of its content, in hex.

    Raises:
        HeavyRunRefused: If the file cannot be read.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise HeavyRunRefused(
            f"the spec {str(path)!r} cannot be read: {error}"
        ) from None


def read_approval(path: Path) -> Approval:
    """The approval in the file at `path`.

    Raises:
        HeavyRunRefused: If the file cannot be read, is no TOML, misses a key, has a key an
            approval does not know, a time without a zone, an end that is not after the start, or
            an approver or text that is empty.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise HeavyRunRefused(
            f"the approval {str(path)!r} cannot be read: {error}. The option {OPTION} "
            f"takes {APPROVAL_FORM}"
        ) from None
    except tomllib.TOMLDecodeError as error:
        raise HeavyRunRefused(
            f"the approval {str(path)!r} is no TOML: {error}. The option {OPTION} takes "
            f"{APPROVAL_FORM}"
        ) from None
    unknown = sorted(set(data) - set(KEYS))
    if unknown:
        raise HeavyRunRefused(
            f"the approval {str(path)!r} has keys an approval does not know: "
            f"{', '.join(unknown)}. An approval has {', '.join(KEYS)}"
        )
    missing = [key for key in KEYS if key not in data]
    if missing:
        raise HeavyRunRefused(
            f"the approval {str(path)!r} has no {', '.join(missing)}. An approval has "
            f"{', '.join(KEYS)}"
        )
    key = _text(data, "spec_key", path).lower()
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise HeavyRunRefused(
            f"the approval {str(path)!r}: spec_key is the 64 hex digits of the spec file's "
            "SHA-256 (`sha256sum <spec>`)"
        )
    start = _time(data, "start", path)
    end = _time(data, "end", path)
    if end <= start:
        raise HeavyRunRefused(
            f"the approval {str(path)!r}: its period ends ({end:%Y-%m-%d %H:%M} UTC) at or "
            f"before it starts ({start:%Y-%m-%d %H:%M} UTC)"
        )
    return Approval(
        spec_key=key,
        start=start,
        end=end,
        approver=_text(data, "approver", path),
        text=_text(data, "text", path),
    )


def load_approval(path: Path | None) -> Approval | None:
    """The approval in the file the option `--heavy-run-approval` names, or None where it is off.

    Raises:
        HeavyRunRefused: As `read_approval`.
    """
    return None if path is None else read_approval(path)


def approved(
    heavy: bool,
    approval: Approval | None,
    *,
    now: datetime | None = None,
    why: str = "heavy = true",
) -> dict[str, str] | None:
    """The approval of a run, as its record keeps it, or None for a run at normal load.

    Args:
        heavy: Whether the run's load is heavy: a sustained-load or breaking-point run.
        approval: The approval given with the option, or None where it is off.
        now: The time the run starts: this machine's if None.
        why: What makes the run heavy, for the refusal to name (a replay at a factor above 1,
            interim).

    Raises:
        HeavyRunRefused: If the run is heavy and there is no approval, or the time is outside the
            approval's period.
    """
    if not heavy:
        return None
    if approval is None:
        raise HeavyRunRefused(
            f"this is a heavy run ({why}). A sustained-load or breaking-point run runs "
            "only under an approval that covers it: any person may approve, in agreement with "
            f"the admins of the server. Give the approval with {OPTION}: {APPROVAL_FORM}",
            NO_APPROVAL,
        )
    now = now if now is not None else datetime.now(UTC)
    if not approval.start <= now < approval.end:
        raise HeavyRunRefused(
            f"it is {now.astimezone(UTC):%Y-%m-%d %H:%M} UTC, outside the period of the "
            f"approval ({approval.start.astimezone(UTC):%Y-%m-%d %H:%M} to "
            f"{approval.end.astimezone(UTC):%Y-%m-%d %H:%M} UTC, approved by {approval.approver})",
            NOT_NOW,
        )
    return approval.record()


def covered_run(
    heavy: bool,
    covered: Covered | None,
    configuration: Configuration,
    *,
    now: datetime | None = None,
    why: str = "heavy = true",
) -> dict[str, str] | None:
    """The approval of a run as its record keeps it, or None for a run at normal load: the check
    of the run entry points. A heavy run needs a `Covered` (made by `cover`) that names this
    configuration, in the approval's period.

    Raises:
        HeavyRunRefused: If the run is heavy and `covered` is None, a raw `Approval` or any other
            thing than a `Covered`; if it covers another configuration; or if the period does not
            hold at `now`.
    """
    if not heavy:
        return None
    if covered is None:
        return approved(True, None, why=why)
    if not isinstance(covered, Covered):
        raise HeavyRunRefused(
            "this heavy run was given an approval that nothing has checked against its spec and "
            "its configuration: a heavy run takes the covered record that approval.cover makes"
        )
    if covered.fingerprint != fingerprint(configuration):
        raise HeavyRunRefused(
            f"the approval covers the configuration with key {covered.configuration_key}, and "
            "this heavy run is of another configuration"
        )
    record = approved(True, covered.approval, now=now, why=why)
    assert record is not None
    return record


def cover(
    approval: Approval | None,
    *,
    spec: Path | None,
    key: str | None,
    configuration: Configuration,
    overridden: bool = False,
    now: datetime | None = None,
    why: str = "heavy = true",
) -> Covered:
    """The covered record, once the approval covers the heavy run of `configuration`, which is the
    configuration of `key` of `spec`, in its period.

    Args:
        approval: The approval given with the option, or None where it is off.
        spec: The spec file the run comes from, or None for a bare configuration file.
        configuration: The configuration to run, resolved. For a replay, the configuration in
            the replayed record's text: it must be the one its stored key names.
        key: The configuration's key (`spec.SpecConfiguration.key`), or None where there is none:
            a bare configuration file, or the record of a run that holds none.
        overridden: Whether the run is made with `--set` overrides of the spec.
        now: The time the run starts: this machine's if None.
        why: What makes the run heavy, for a refusal to name (`approved`).

    Raises:
        HeavyRunRefused: If the approval is off or its period is not now; or the run has no spec,
            overrides the spec, is of another spec or of a changed spec file, or has no
            configuration key or one that the spec does not list, or `configuration` is not the
            configuration that key names.
    """
    record = approved(True, approval, now=now, why=why)
    assert approval is not None and record is not None
    if overridden:
        raise HeavyRunRefused(
            "a run with --set overrides is not covered by an approval: it is not the spec as "
            "written. Write the values into a spec and have that spec approved",
            OVERRIDDEN,
        )
    if spec is None:
        raise HeavyRunRefused(
            "a heavy run with no spec (a bare configuration file, or no --spec) is not covered "
            "by an approval: an approval covers the configurations of one spec. Give the "
            "approved spec: jms-eval-uc2 takes it with --spec and the configuration's number, "
            "jms-eval-baseline with --spec"
        )
    if spec_key(spec) != approval.spec_key:
        raise HeavyRunRefused(
            f"the approval is for the spec with key {approval.spec_key[:12]}…, and the spec "
            f"{str(spec)!r} has key {spec_key(spec)[:12]}…: it is another spec, or the "
            "approved one changed since it was approved. Have this spec approved",
            OTHER_SPEC,
        )
    if key is None:
        raise HeavyRunRefused(
            "this run's record holds no configuration key, so no approval can say that it "
            "covers it: a run made from the approved spec records its key"
        )
    try:
        listed = {c.key: c for c in configurations(read_spec(spec))}
    except SpecRefused as refused:
        raise HeavyRunRefused(str(refused)) from None
    if key not in listed:
        raise HeavyRunRefused(
            f"the configuration with key {key} is no configuration of the approved spec "
            f"{str(spec)!r}"
        )
    if not lists(listed[key], configuration):
        raise HeavyRunRefused(
            f"the configuration to run is not the configuration with key {key} that the "
            "approved spec lists: a record whose configuration does not match its key, or a "
            "configuration changed after the key was made"
        )
    return Covered(
        approval=approval,
        spec_key=approval.spec_key,
        configuration_key=key,
        fingerprint=fingerprint(configuration),
        _issuer=_ISSUER,
    )


def _text(data: dict[str, Any], key: str, path: Path) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise HeavyRunRefused(
            f"the approval {str(path)!r}: {key} is a text that is not empty, not {value!r}"
        )
    return value.strip()


def _time(data: dict[str, Any], key: str, path: Path) -> datetime:
    value = data[key]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise HeavyRunRefused(
            f"the approval {str(path)!r}: {key} is a time in UTC with its zone, such as "
            f"2026-10-13T20:00:00Z, not {value!r}"
        )
    return value.astimezone(UTC)
