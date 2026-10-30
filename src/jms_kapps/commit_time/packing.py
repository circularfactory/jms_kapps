"""The packing of a finished commit time run: what the published repository holds of
it, a raw-data part and a reports part.

    python commit_time/pack.py <run folder> --out <folder> --release <tag>
                               (--release-config <config.toml> | --ban S ...)

The run folder is the output folder of the experiment script (`commit_time/experiment.py`):
`parameters.json` and the spec beside it, and one store folder per store, each with
`heartbeats-<level>/runs/<run id>/` per run and replay. The packing reads only that folder, sends
no request to a store and changes nothing in it. It writes two folders into `--out`:

- **`raw_data/`**: one tar.xz per used repetition and store, `<store>-repetition-<NN>.tar.xz`,
  with the folder of each run and replay of that repetition, as the runs left them, below
  `<store>/heartbeats-<level>/runs/`; the run's records unpacked, `parameters.json` and the
  spec, under the names the run wrote; `MANIFEST.json`; and `README.md`, the part's contents.
  Unpacking every archive in `raw_data/` gives the store folders back, with the run folders
  where the experiment wrote them.
- **`reports/`**: what the scripts derive from the used repetitions, read from the unpacked
  archives in `raw_data/`: per store the index of runs and the results page
  (`jms_kapps.commit_time.results`), the figure script's whole output in `figures/`, and
  `REPRODUCE.md`. Nothing in it is copied from the run folder, whose derived files may hold the
  repetitions that `parameters.json` excludes. A file over `WARNING_BYTES`, GitHub's warning
  size, ships as `<name>.tar.xz` instead.

**Excluded repetitions.** `parameters.json` lists them under `excluded`, each with its
repetition, its stores, its runs and why. A run it names, or a run of a repetition and store it
names, is not packed; the manifest lists each entry.

**One commit.** The run is packed only when every packed run's record names the same jms_kapps
commit. Otherwise the packing refuses it and names the runs of each commit; a run whose record
names none is named as "no commit".

**The banned strings** are the literal ones of the release's hygiene checks: the private hosts,
the banned path fragments and the record folders that never ship, from the `[checks]` table of
the release configuration, and any given with `--ban`. Each match, in any case, in a file's
content and in each name the parts hold, is replaced by a placeholder that names its list and
index, `[redacted private-hosts 0]`; the manifest lists each replacement by archive, file and
part (`content` or `name`), never the string. Pattern matches, such as a ticket reference, stay
as the run wrote them.

**A file that is not text** is copied byte for byte: an edit would corrupt it. The manifest lists
each as "not text: copied unchanged, not redacted", with its SHA-256 digest. A banned string in
its bytes refuses the run and names the file. The same scan guards every file of the reports
part, which is derived from redacted records and should hold none.

Nothing is written when the packing is refused: both parts are made in a folder of their own
beside them and moved into place at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import re
import shutil
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jms_kapps.commit_time import results
from jms_kapps.commit_time.commit_table import WRITES_ONLY, arm_of
from jms_kapps.commit_time.record import DISTRIBUTIONS

# The two parts, and the files the packing writes into them.
RAW_DATA = "raw_data"
REPORTS = "reports"
MANIFEST = "MANIFEST.json"
README = "README.md"
PAGE = "REPRODUCE.md"
FIGURES = "figures"

# The run's records beside its store folders.
PARAMETERS = "parameters.json"

# A heartbeat level's folder in a store folder, and the runs' folder in it.
LEVEL_PREFIX = "heartbeats-"
RUNS = "runs"

# GitHub warns from 50 MB a file; a report this large ships as tar.xz.
WARNING_BYTES = 50 * 1024 * 1024

# xz at its highest preset, as measured on the final run (16 MB for one repetition and store).
XZ_PRESET = 9 | lzma.PRESET_EXTREME

# The lists of literal strings in the `[checks]` table of the release configuration.
BANNED_LISTS = ("private-hosts", "banned-path-fragments", "dead-reference-dirs")
BAN_OPTION = "--ban"

# Where a reader gets the release from; the project's Homepage.
PUBLISH_REPOSITORY = "https://github.com/circularfactory/jms_kapps"

NO_COMMIT = "no commit"
NOT_TEXT = "not text: copied unchanged, not redacted"

# The spec's name the experiment writes today; a run that wrote another predates the rename.
CURRENT_SPEC = "commit_time.toml"

DrawFigures = Callable[[list[Path], Path], None]
"""Draws the figures of the store folders into a folder: the figure script."""


class Refused(Exception):
    """The run cannot be packed as it is; the message says why."""


@dataclass(frozen=True)
class Banned:
    """One banned string, where it comes from (a list and its index), and its placeholder."""

    value: str
    source: str

    @property
    def placeholder(self) -> str:
        return f"[redacted {self.source}]"


def banned_strings(
    release_config: Path | None = None, extra: Sequence[str] = ()
) -> list[Banned]:
    """The literal strings that the release's hygiene checks ban, from the `[checks]` table of
    `release_config`, followed by `extra`, the strings given with `--ban`."""
    found: list[Banned] = []
    if release_config is not None:
        try:
            checks = tomllib.loads(release_config.read_text(encoding="utf-8")).get(
                "checks", {}
            )
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise Refused(
                f"the release configuration {str(release_config)!r} cannot be read: {error}"
            ) from error
        for name in BANNED_LISTS:
            found += [
                Banned(str(value), f"{name} {index}")
                for index, value in enumerate(checks.get(name, []))
            ]
    found += [
        Banned(value, f"{BAN_OPTION} {index}") for index, value in enumerate(extra)
    ]
    return [b for b in found if b.value]


class _Redactor:
    """Replaces each banned string, in any case, the longest first, and counts each."""

    def __init__(self, banned: Sequence[Banned]) -> None:
        self._by_value = {b.value.lower(): b for b in banned}
        ordered = sorted(self._by_value, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(map(re.escape, ordered)), re.IGNORECASE)
            if ordered
            else None
        )

    def text(self, text: str) -> tuple[str, dict[Banned, int]]:
        counts: dict[Banned, int] = {}
        if self._pattern is None:
            return text, counts

        def placeholder(match: re.Match[str]) -> str:
            banned = self._by_value[match.group(0).lower()]
            counts[banned] = counts.get(banned, 0) + 1
            return banned.placeholder

        return self._pattern.sub(placeholder, text), counts

    def found_in(self, content: bytes) -> bool:
        lowered = content.lower()
        return any(value.encode("utf-8") in lowered for value in self._by_value)


@dataclass
class _Run:
    """One run or replay of the run folder: where it lies, and what the manifest says of it."""

    folder: Path
    store: str
    record: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.record.get("run_id") or self.folder.name)

    @property
    def repetition(self) -> int | None:
        sweep = self.record.get("sweep")
        value = sweep.get("repetition") if isinstance(sweep, Mapping) else None
        return value if isinstance(value, int) else None


@dataclass
class _Ledger:
    """What the manifest collects while the parts are written."""

    replacements: list[dict[str, Any]] = field(default_factory=list)
    not_text: list[dict[str, Any]] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)

    def replaced(
        self, archive: str | None, where: str, part: str, counts: Mapping[Banned, int]
    ) -> None:
        self.replacements += [
            {
                "archive": archive,
                "file": where,
                "in": part,
                "placeholder": b.placeholder,
                "source": b.source,
                "count": count,
            }
            for b, count in sorted(counts.items(), key=lambda i: i[0].source)
        ]


def _facts(record: Mapping[str, Any]) -> dict[str, Any]:
    """The few fields of a record the packing needs; a record can be large."""
    keep = (
        *("run_id", "run_kind", "outcome", "valid", "failed_checks", "sweep"),
        *("software", "configuration", "replay", "replayed_run", "actions"),
    )
    found = {key: record.get(key) for key in keep}
    actions = found["actions"] or []
    found["actions"] = actions[:1] + actions[-1:]
    settings = record.get("run_settings")
    found["run_settings"] = dict(settings) if isinstance(settings, Mapping) else {}
    return found


def _runs(run_folder: Path) -> list[_Run]:
    """Every run and replay below the run folder's store folders, in store and run-id order."""
    found: list[_Run] = []
    for path in sorted(run_folder.glob(f"*/{LEVEL_PREFIX}*/{RUNS}/*/{results.RECORD_FILE}")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise Refused(f"the record of {path.parent.name} cannot be read: {error}") from error
        store = path.relative_to(run_folder).parts[0]
        found.append(_Run(path.parent, store, _facts(record)))
    return found


def _excluded(run: _Run, excluded: Sequence[Mapping[str, Any]]) -> bool:
    for entry in excluded:
        if run.run_id in (entry.get("runs") or []):
            return True
        if run.repetition == entry.get("repetition") and run.store in (
            entry.get("stores") or []
        ):
            return True
    return False


def _commit(run: _Run) -> str:
    software = run.record.get("software")
    facts = software.get("jms-kapps") if isinstance(software, Mapping) else None
    commit = facts.get("commit") if isinstance(facts, Mapping) else None
    return str(commit) if commit else NO_COMMIT


def _the_commit(runs: Sequence[_Run]) -> str:
    """The one jms_kapps commit that every run names; else refused, with the runs of each."""
    by_commit: dict[str, list[str]] = {}
    for run in runs:
        by_commit.setdefault(_commit(run), []).append(run.run_id)
    if not by_commit:
        raise Refused("the run folder holds no run's record below a store folder")
    if len(by_commit) > 1 or NO_COMMIT in by_commit:
        named = "; ".join(f"{c}: {', '.join(r)}" for c, r in sorted(by_commit.items()))
        raise Refused(
            "the runs do not all name the same jms_kapps commit, so no one release repeats "
            f"them ({named})"
        )
    [commit] = by_commit
    return commit


def _run_row(run: _Run, archive: str, folder: str) -> dict[str, Any]:
    sweep = run.record.get("sweep")
    if not isinstance(sweep, Mapping):
        sweep = {}
    software = run.record.get("software")
    if not isinstance(software, Mapping):
        software = {}
    return {
        "run_id": run.run_id,
        "archive": archive,
        "folder": folder,
        "store": run.store,
        "run_kind": run.record.get("run_kind"),
        "outcome": run.record.get("outcome"),
        "configuration": sweep.get("configuration"),
        "configuration_key": sweep.get("key"),
        "repetition": run.repetition,
        "valid": run.record.get("valid"),
        "failed_checks": [
            c.get("check") if isinstance(c, Mapping) else str(c)
            for c in run.record.get("failed_checks") or []
        ],
        "software": {
            name: {
                key: (software.get(name) or {}).get(key)
                for key in ("version", "commit", "modified")
            }
            for name in DISTRIBUTIONS
        },
    }


def _redacted_content(
    content: bytes, name: str, redactor: _Redactor, ledger: _Ledger, archive: str | None
) -> bytes:
    """`content` with each banned string replaced, or as it is when it is not text."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        if redactor.found_in(content):
            raise Refused(
                f"{name} holds a banned string and is no UTF-8 text, so it cannot be "
                "rewritten; remove the string or the file"
            ) from None
        ledger.not_text.append(
            {
                "archive": archive,
                "file": name,
                "note": NOT_TEXT,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        return content
    text, counts = redactor.text(text)
    ledger.replaced(archive, name, "content", counts)
    return text.encode("utf-8")


def _tar_info(name: str, size: int, like: Path) -> tarfile.TarInfo:
    """A member's header that depends on the file alone, so that one run packs to the same bytes."""
    info = tarfile.TarInfo(name)
    info.size = size
    stat = like.stat()
    info.mtime = int(stat.st_mtime)
    info.mode = stat.st_mode & 0o777
    return info


@contextmanager
def _xz_tar(path: Path) -> Iterator[tarfile.TarFile]:
    """A tar file to write at `path`, compressed with xz at `XZ_PRESET`."""
    with lzma.open(path, "wb", preset=XZ_PRESET) as xz, tarfile.open(
        fileobj=xz, mode="w"
    ) as tar:
        yield tar


def _write_archive(
    path: Path,
    runs: Sequence[_Run],
    run_folder: Path,
    redactor: _Redactor,
    ledger: _Ledger,
) -> list[str]:
    """One tar.xz of the folders of `runs`, each below its path in the run folder, redacted;
    the archived folder of each run."""
    folders: list[str] = []
    with _xz_tar(path) as tar:
        for run in runs:
            name, counts = redactor.text(run.folder.relative_to(run_folder).as_posix())
            ledger.replaced(path.name, name, "name", counts)
            folders.append(name)
            for file in sorted(run.folder.rglob("*")):
                relative = file.relative_to(run_folder).as_posix()
                if file.is_symlink():
                    left, counts = redactor.text(relative)
                    ledger.replaced(None, left, "name", counts)
                    ledger.left_out.append(left)
                    continue
                if not file.is_file():
                    continue
                name, counts = redactor.text(relative)
                ledger.replaced(path.name, name, "name", counts)
                content = _redacted_content(
                    file.read_bytes(), name, redactor, ledger, path.name
                )
                tar.addfile(_tar_info(name, len(content), file), io.BytesIO(content))
    return folders


def _records(run_folder: Path) -> list[Path]:
    """The run's records beside its store folders: `parameters.json` and the spec."""
    return [
        path
        for path in sorted(run_folder.iterdir())
        if path.is_file()
        and not path.is_symlink()
        and (path.name == PARAMETERS or path.suffix == ".toml")
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _index(store_folder: Path) -> list[dict[str, Any]]:
    """The index of the runs in an unpacked store folder, as the experiment writes it, in the
    order the runs began. A replay is of its replayed run's repetition."""
    entries: list[tuple[str, dict[str, Any]]] = []
    repetitions: dict[str, Any] = {}
    replays: list[tuple[Path, dict[str, Any], str, str]] = []
    for path in sorted(store_folder.glob(f"{LEVEL_PREFIX}*/{RUNS}/*/{results.RECORD_FILE}")):
        record = _facts(json.loads(path.read_text(encoding="utf-8")))
        arm, _ = arm_of(record)
        if arm is None:
            continue
        level = path.relative_to(store_folder).parts[0].removeprefix(LEVEL_PREFIX)
        if arm == WRITES_ONLY:
            replays.append((path.parent, record, arm, level))
            continue
        repetitions[str(record.get("run_id"))] = (record.get("sweep") or {}).get("repetition")
        entries.append((_began(record), _entry(record, path.parent, store_folder, arm, level, None)))
    for folder, record, arm, level in replays:
        replayed = str((record.get("replayed_run") or {}).get("run_id"))
        repetition = repetitions.get(replayed, (record.get("sweep") or {}).get("repetition"))
        entries.append((_began(record), _entry(record, folder, store_folder, arm, level, repetition)))
    return [entry for _, entry in sorted(entries, key=lambda e: e[0])]


def _began(record: Mapping[str, Any]) -> str:
    actions = record.get("actions") or []
    return str(actions[0].get("at")) if actions else str(record.get("run_id"))


def _entry(
    record: Mapping[str, Any],
    folder: Path,
    store_folder: Path,
    arm: str,
    level: str,
    repetition: Any,
) -> dict[str, Any]:
    if repetition is None:
        repetition = (record.get("sweep") or {}).get("repetition")
    return results.index_entry(
        record,
        folder=folder,
        store_folder=store_folder,
        arm=arm,
        heartbeats=level,
        repetition=repetition,
    )


def write_store_reports(store_folder: Path, into: Path, store: str) -> None:
    """The index of the runs of an unpacked store folder and its results page, into `into`."""
    index = _index(store_folder)
    into.mkdir(parents=True)
    results.write_index(into, index)
    facts = results.results_facts(store_folder, store=store, entries=index)
    (into / results.RESULTS_JSON).write_text(
        json.dumps(facts, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    (into / results.RESULTS_MARKDOWN).write_text(
        results.results_markdown(facts), encoding="utf-8"
    )


def _shrink(reports: Path) -> None:
    """Each file of the reports over `WARNING_BYTES`, packed as `<name>.tar.xz` beside it."""
    for path in sorted(reports.rglob("*")):
        if path.is_file() and path.stat().st_size > WARNING_BYTES:
            packed = path.with_name(f"{path.name}.tar.xz")
            with _xz_tar(packed) as tar, path.open("rb") as handle:
                tar.addfile(_tar_info(path.name, path.stat().st_size, path), handle)
            path.unlink()


def _guard(part: Path, top: Path, redactor: _Redactor) -> None:
    """Refuse when a file of `part` (not an archive) holds a banned string."""
    for path in sorted(part.rglob("*")):
        if (
            path.is_file()
            and not path.name.endswith(".tar.xz")
            and redactor.found_in(path.read_bytes())
        ):
            name = path.relative_to(top).as_posix()
            raise Refused(f"{name} would hold a banned string")


def pack(
    run_folder: Path,
    out: Path,
    *,
    release: str,
    banned: Sequence[Banned],
    draw_figures: DrawFigures,
) -> dict[str, Any]:
    """Write the raw-data part and the reports part of the finished run in `run_folder` into
    `out` (see the module), and return the manifest. Nothing is written when the run is refused
    (`Refused`).

    Args:
        run_folder: The output folder of the experiment script.
        out: The folder the two parts go into, outside the run folder; neither part may exist
            in it with content.
        release: The release that holds the parts, which the page names.
        banned: The strings no file of either part may hold (`banned_strings`).
        draw_figures: The figure script, called with the unpacked store folders and the
            reports' `figures/` folder (`figure_drawer`).
    """
    run_folder = run_folder.resolve()
    out = out.absolute()
    if out.resolve().is_relative_to(run_folder):
        raise Refused(f"the parts would go into the run, {str(out)!r}, which stays as it is")
    for part in (RAW_DATA, REPORTS):
        if (out / part).exists() and any((out / part).iterdir()):
            raise Refused(f"{str(out / part)!r} exists already")
    if not (run_folder / PARAMETERS).is_file():
        raise Refused(f"{str(run_folder)!r} holds no {PARAMETERS}")
    parameters = json.loads((run_folder / PARAMETERS).read_text(encoding="utf-8"))
    excluded = list(parameters.get("excluded") or [])
    runs = _runs(run_folder)
    packed = [run for run in runs if not _excluded(run, excluded)]
    for run in packed:
        if run.repetition is None:
            raise Refused(f"the record of {run.run_id} names no repetition")
    commit = _the_commit(packed)

    redactor = _Redactor(banned)
    ledger = _Ledger()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".packing-", dir=out))
    try:
        raw = work / RAW_DATA
        reports = work / REPORTS
        raw.mkdir()
        reports.mkdir()
        groups: dict[tuple[str, int], list[_Run]] = {}
        for run in packed:
            groups.setdefault((run.store, int(run.repetition or 0)), []).append(run)
        rows: list[dict[str, Any]] = []
        archives: list[dict[str, Any]] = []
        for (store, repetition), members in sorted(groups.items()):
            members.sort(key=lambda r: _began(r.record))
            store_name, counts = redactor.text(store)
            name = f"{store_name}-repetition-{repetition:02d}.tar.xz"
            ledger.replaced(None, name, "name", counts)
            path = raw / name
            print(f"packing {name}: {', '.join(r.run_id for r in members)}", flush=True)
            folders = _write_archive(path, members, run_folder, redactor, ledger)
            rows += [_run_row(r, name, f) for r, f in zip(members, folders, strict=True)]
            archives.append(
                {
                    "archive": name,
                    "store": store_name,
                    "repetition": repetition,
                    "runs": [r.run_id for r in members],
                    "sha256": _sha256(path),
                    "bytes": path.stat().st_size,
                }
            )
        records = []
        for path in _records(run_folder):
            name, counts = redactor.text(path.name)
            ledger.replaced(None, name, "name", counts)
            (raw / name).write_bytes(
                _redacted_content(path.read_bytes(), name, redactor, ledger, None)
            )
            records.append(name)
        release, counts = redactor.text(release)
        ledger.replaced(None, "--release", "name", counts)
        manifest = {
            "release": release,
            "jms_kapps_commit": commit,
            "records": records,
            "archives": archives,
            "runs": rows,
            "excluded": json.loads(redactor.text(json.dumps(excluded))[0]),
            "replacements": ledger.replacements,
            "not_text": ledger.not_text,
            "left_out": ledger.left_out,
        }
        (raw / MANIFEST).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (raw / README).write_text(raw_data_readme(manifest), encoding="utf-8")
        _guard(raw, work, redactor)

        # The reports are read from the archives unpacked at their place, as a reader unpacks
        # them; the raw-data part goes out again when the reports are refused.
        placed = out / RAW_DATA
        if placed.exists():
            placed.rmdir()
        raw.rename(placed)
        try:
            for entry in archives:
                with tarfile.open(placed / entry["archive"], "r:xz") as tar:
                    tar.extractall(placed, filter="data")
            stores = sorted({entry["store"] for entry in archives})
            for store in stores:
                write_store_reports(placed / store, reports / store, store)
            draw_figures([placed / store for store in stores], reports / FIGURES)
            _shrink(reports)
            (reports / PAGE).write_text(
                reproduction_page(release, commit, stores, rows), encoding="utf-8"
            )
            _guard(reports, work, redactor)
            if (out / REPORTS).exists():
                (out / REPORTS).rmdir()
            reports.rename(out / REPORTS)
        except BaseException:
            shutil.rmtree(placed, ignore_errors=True)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return manifest


def figure_drawer(main: Callable[[list[str]], int]) -> DrawFigures:
    """The figure script's `main` as `pack` calls it."""

    def draw(folders: list[Path], output: Path) -> None:
        main([*(str(folder) for folder in folders), "--output", str(output)])

    return draw


def raw_data_readme(manifest: Mapping[str, Any]) -> str:
    """The raw-data part's page: what it holds."""
    records = ", ".join(f"`{name}`" for name in manifest["records"])
    return f"""\
# Raw data of the commit time run

- One tar.xz per used repetition and store, `<store>-repetition-<NN>.tar.xz`, with the folder
  of each run and replay of that repetition, as the run left it, below
  `<store>/heartbeats-<level>/runs/<run id>/`. Unpacking every archive here gives the store
  folders back.
- The run's records as it wrote them: {records}.
- `MANIFEST.json`: each run with its archive, configuration, repetition, validity and the
  software its record names; the repetitions excluded, with why; each replaced string.

A string that the release's hygiene checks ban is replaced by a placeholder that names its list,
as `MANIFEST.json` lists. The reports part beside this one is derived from these archives.
"""


def reproduction_page(
    release: str, commit: str, stores: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> str:
    """The page on how to unpack the run, redraw its figure and repeat one of its runs."""
    folders = " ".join(f"commit_time/results/{RAW_DATA}/{store}" for store in stores)
    example = next((r for r in rows if r.get("run_kind") == "uc2"), None)
    run = (
        "<run folder>"
        if example is None
        else f"commit_time/results/{RAW_DATA}/{example['folder']}"
    )
    return f"""\
# How to reproduce the commit time run

The release **{release}** of jms_kapps ({PUBLISH_REPOSITORY}) holds this run: its raw data in
`commit_time/results/{RAW_DATA}/`, one tar.xz per used repetition and store, and what the scripts
derive from it here, in `commit_time/results/{REPORTS}/`. The runs ran the jms_kapps commit
`{commit}`, a development commit; the release publishes that code's measurement.

## Unpack the raw data

With `tar`:

```sh
cd commit_time/results/{RAW_DATA}
for archive in *.tar.xz; do tar -xJf "$archive"; done
```

or with Python's `tarfile`:

```sh
uv run python -c "import pathlib, tarfile; [tarfile.open(a).extractall('.', filter='data') for a in sorted(pathlib.Path('.').glob('*.tar.xz'))]"
```

Either gives one folder per store, each with `heartbeats-<level>/runs/<run id>/`.

## Redraw the figure

From the repository's root:

```sh
uv sync --locked --extra commit-time
uv run python commit_time/figure.py {folders} --output figures-again
```

The CSVs in `figures-again/` are the ones in `{REPORTS}/{FIGURES}/`, byte for byte. A file of
`{REPORTS}/{FIGURES}/` that ends in `.tar.xz` is a table larger than GitHub's warning size; it
unpacks the same way.

## Repeat one run

1. Point the release at a GraphDB whose login may create and delete datasets and read the
   server's monitoring views: set `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
   `TRIPLESTORE_PASSWORD`. A run creates its own dataset and deletes it at its end.
2. Make the preload file again. The run's record gives the topology, the size and the seed in
   `prefill.header`, and the file's digest in `prefill.sha256`:

   ```sh
   uv run python -m jms_kapps.commit_time.prefill --topology <topology> --size <size> --seed <seed> --output preload.ttl
   ```

3. Run the run's configuration again, with the preload file made in step 2:

   ```sh
   uv run python -c "import sys, tomllib; from pathlib import Path; from jms_kapps.commit_time.configuration import resolve; from jms_kapps.commit_time.run_uc2 import run_on_the_configured_store; values = tomllib.loads(Path(sys.argv[1]).read_text()); values['prefill'] = str(Path(sys.argv[2]).absolute()); run_on_the_configured_store(resolve(values, base=Path.cwd()), runs=Path('repeated'))" {run}/configuration.toml preload.ttl
   ```

   The new record is in `repeated/<run id>/record.json`; it runs at heartbeats on, the level of
   the published run. The record of a run gives its heartbeat settings in `run_settings`.
4. Compare it with the archived record, or draw the figure of the repeated runs with
   `commit_time/figure.py`.

The whole run is repeated with `commit_time/experiment.py`; its README gives the time it takes.
The store's server is not part of the release: the record's `server` and `client` sections say
what the runs ran on.
"""


def main(argv: Sequence[str] | None = None, *, draw_figures: DrawFigures) -> int:
    """The command (`commit_time/pack.py`), with the figure script it calls."""
    parser = argparse.ArgumentParser(
        prog="pack.py",
        description="Pack a finished commit time run into the raw-data part and the reports "
        "part of the published repository. It reads only the run's folder and changes nothing "
        "in it.",
    )
    parser.add_argument("run_folder", type=Path, help="The output folder of experiment.py")
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help=f"The folder the parts {RAW_DATA}/ and {REPORTS}/ go into",
    )
    parser.add_argument(
        "--release", required=True, help="The release that holds the parts, by its tag"
    )
    parser.add_argument(
        "--release-config",
        type=Path,
        default=None,
        metavar="CONFIG",
        help="The release configuration, TOML, whose [checks] lists name the banned strings",
    )
    parser.add_argument(
        BAN_OPTION,
        action="append",
        default=[],
        metavar="STRING",
        help="A string no file of the parts may hold, beside the release configuration's. "
        "May be given more than once",
    )
    args = parser.parse_args(argv)
    if not args.run_folder.is_dir():
        print(f"pack.py: there is no run folder {str(args.run_folder)!r}", file=sys.stderr)
        return 2
    try:
        banned = banned_strings(args.release_config, args.ban)
        if not banned:
            raise Refused(f"no banned string is given: pass --release-config or {BAN_OPTION}")
        manifest = pack(
            args.run_folder,
            args.out,
            release=args.release,
            banned=banned,
            draw_figures=draw_figures,
        )
    except Refused as refused:
        print(f"pack.py: {refused}", file=sys.stderr)
        return 2
    print(
        f"pack.py: {len(manifest['archives'])} archives of {len(manifest['runs'])} runs, "
        f"{sum(r['count'] for r in manifest['replacements'])} replacements, into "
        f"{args.out / RAW_DATA} and {args.out / REPORTS}"
    )
    return 0
