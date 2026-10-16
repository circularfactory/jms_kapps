"""jms_kapps names no triple store vendor, and calls the store's unit a dataset.

No layer above the triple-store interface names a vendor: not in its types, its errors, how it
builds a store, or its texts. A new backend then changes only the interface, and use case 1 and
use case 2 run unchanged. This module checks jms_kapps' side of it, offline.

**The terms.** Each is matched in any case, also inside a longer word.

- `GraphDB`: the vendor's store. The match covers `GRAPHDB_`, the prefix of the deprecated
  environment variables, and every name built on it.
- `GDB`: the vendor's short name, as in the name of its token.
- `RDF4J`: the framework of the vendor's store, whose names a store's dataset carries, such as the
  graph that holds its SHACL shapes. A caller sets the shapes with the store's `shapes_set`.
- `Ontotext`: the vendor.
- `repository`, `repositories`: the store's unit, which the triple-store interface calls a
  *dataset*: "dataset X on <url>". A git repository is not a vendor term. Each line that means
  one is listed in `GIT_SENSE`, by its file and a fragment of the line.

`InfluxDB` is not a triple store vendor, and is not a term: the `INFLUXDB_*` variables and the
README's InfluxDB section stay.

**What is scanned.**

- The command line: the help of `jms-uc2`, of `jms-uc2 slice` and of the command line that every
  process with a middleware shares.
- The text of every file under `SCANNED`: the package's source, which holds its command lines,
  the environment variables it reads, its names and its messages; the Launcher page and the
  monitor page; the guide of the `evaluation` package; the README; `run-uc2`; the reviewer
  guide with its screenshot script; and the reference pages under `docs/reference/`. Of `scripts/`
  only that script: the release renders the mechanism's own checker there, whose text is the tool's.
- The importable names of the package and their signatures: every public name that a module of
  the package defines, each parameter of each public function, class and method, and each field
  of each public class, as the keys of the Launcher's state.

**What is kept.**

- `KEPT`: the items that keep the word GraphDB, because they name the backend that the live tier
  runs on. Each spelling is removed from a line before the line is matched: the `graphdb` test
  marker, and the folder of the dataset's configuration, `graphdb/`, which the local fallback's
  compose file loads.
- `EXEMPT`: two paths, each with its reason. The possession-history exception, `uc2/history.py`.
  Use case 2's reset turns on the store's history plugin directly, and a person reads past
  possessions with a query of that plugin: the one store feature above the interface (Etienne,
  2026-10-06), so the module names the vendor and its plugin, and the store's unit as the
  plugin's measurements name it. And the measurement harness, the folder `evaluation/` and its
  modules, which belong to the map of the paper's evaluation, outside the scope of the build that added this check
  (jms_kapps 62): its store layer creates and deletes each run's own dataset and reads the
  server's monitoring views through the backend's own REST API, on purpose, so its texts and
  names name the vendor and its unit. What it takes from the rest of the package and from the
  siblings, it takes by their new names. Its own names are not renamed here (decided on
  2026-10-11, by Etienne's standing decisions, through the orchestrator).
- `REGIONS`: in each file listed there, the lines from a `vendor-check: off` marker to the next
  `vendor-check: on`, for the reason given there. A marker in any other file fails.

The decision records, the agents' docs, the tests and the local fallback's compose file and
dataset configuration are not scanned: they do not ship, or they name the backend that runs.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import io
import pkgutil
import re
from pathlib import Path

import pytest

import jms_kapps

REPO_ROOT = Path(__file__).resolve().parents[1]

SCANNED = (
    "src/jms_kapps",
    "README.md",
    "run-uc2",
    "docs/reviewer-guide.md",
    "scripts/take_screenshots.py",
    "docs/reference",
)

VENDOR = re.compile(r"graphdb|gdb|rdf4j|ontotext", re.IGNORECASE)
REPOSITORY = re.compile(r"repositor(?:y|ies)", re.IGNORECASE)

KEPT = (
    "`graphdb`",
    "not graphdb",
    "graphdb/",
)
"""The spellings of the items that keep the word: the `graphdb` test marker, as the README names
it and as `-m "not graphdb"` deselects it, and the folder of the dataset's configuration."""

EXEMPT = {
    "src/jms_kapps/uc2/history.py": "the possession-history exception",
    "src/jms_kapps/evaluation/": (
        "the measurement harness of the map of the paper's evaluation, outside this check's build: its store layer drives "
        "the backend's own REST API on purpose"
    ),
}
"""Each exempt path: a file, or a folder, ending in `/`, with everything below it."""

# The modules of the exempt folders, by the dotted name of the folder's package.
EXEMPT_PACKAGES = ("jms_kapps.evaluation",)

REGIONS = {
    "docs/reference/triple-store.md": (
        "the local fallback's setup steps, which start the local GraphDB of the compose file; "
        "and the deprecated variables, which the store reads until the contract release"
    ),
    "docs/reference/past-possessions.md": (
        "the possession-history exception: the store's history plugin, and its queries"
    ),
    "run-uc2": (
        "the start of the local fallback, the local GraphDB of the compose file; and the "
        "deprecated variable, which the store reads until the contract release"
    ),
    "src/jms_kapps/uc2/shapes/flexconveyor_usecase_shapes.ttl": (
        "the backend that the shapes were measured on, which decides how they are written"
    ),
}

OFF = "vendor-check: off"
ON = "vendor-check: on"

GIT_SENSE: dict[str, tuple[str, ...]] = {
    "README.md": (
        "Both use cases are in one repository",
        "in the repository root:",
    ),
    "docs/reference/assets.md": (
        "| This repository |",
        "the name of the repository already says it",
    ),
    "docs/reference/tests.md": ("named anywhere in the repository",),
    "docs/reference/topologies.md": ("its repository. The *first iteration*",),
    "docs/reference/triple-store.md": ("repository root:",),
    "docs/reviewer-guide.md": (
        "In the repository root:",
        "shell in the repository root:",
    ),
}
"""The lines that mean a git repository, by file: a fragment of each line."""


def _exempt(relative: str) -> bool:
    return any(
        relative == path or (path.endswith("/") and relative.startswith(path))
        for path in EXEMPT
    )


def _files() -> list[Path]:
    found: list[Path] = []
    for entry in SCANNED:
        path = REPO_ROOT / entry
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            relative = candidate.relative_to(REPO_ROOT).as_posix()
            if not candidate.is_file() or _exempt(relative):
                continue
            if "__pycache__" in candidate.parts:
                continue
            found.append(candidate)
    return found


def _kept_removed(line: str) -> str:
    for spelling in KEPT:
        line = line.replace(spelling, "")
    return line


def offences_in(relative: str, text: str) -> list[str]:
    """Each line of `text` that names a term and that nothing keeps or exempts."""
    offences: list[str] = []
    git_sense = GIT_SENSE.get(relative, ())
    off = False
    for number, line in enumerate(text.splitlines(), start=1):
        if OFF in line or ON in line:
            if relative not in REGIONS:
                offences.append(f"{relative}:{number}: a marker outside REGIONS")
            off = OFF in line
            continue
        if off:
            continue
        checked = _kept_removed(line)
        git_repository = any(fragment in line for fragment in git_sense)
        if VENDOR.search(checked) or (REPOSITORY.search(checked) and not git_repository):
            offences.append(f"{relative}:{number}: {line.strip()}")
    if off:
        offences.append(f"{relative}: a region that no `{ON}` ends")
    return offences


def test_no_text_names_the_vendor_or_its_unit():
    offences: list[str] = []
    for path in _files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if VENDOR.search(relative) or REPOSITORY.search(relative):
            offences.append(f"{relative}: the path")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # not a text file
        offences.extend(offences_in(relative, text))
    assert not offences, (
        "These lines name a triple store vendor or call the store's unit a repository. Say "
        "'the store' and 'dataset', or exempt the line in this module, with its reason:\n"
        + "\n".join(offences)
    )


def test_each_exemption_still_exempts_something():
    """An exemption that matches nothing is a stale one, and hides the next real offence."""
    for path in EXEMPT:
        assert (REPO_ROOT / path).exists(), f"EXEMPT names {path}, which does not exist"
    for path in REGIONS:
        assert OFF in (REPO_ROOT / path).read_text(encoding="utf-8"), (
            f"REGIONS names {path}, which has no region"
        )
    for path, fragments in GIT_SENSE.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        for fragment in fragments:
            assert fragment in text, f"GIT_SENSE names {fragment!r}, which {path} lacks"


def test_a_region_a_kept_spelling_and_a_git_repository_pass_and_nothing_else_does():
    """The rules of the check itself, on lines made up for it."""
    text = (
        'Run `pytest -m "not graphdb"`; the `graphdb` tests clear their dataset.\n'
        "Create it from graphdb/jms-kapps-repo-config.ttl.\n"
        "In the repository root:\n"
        "Set GRAPHDB_URL.\n"
        "The repository is cleared.\n"
        "<!-- vendor-check: off -->\n"
        "The local GraphDB of the compose file.\n"
        "<!-- vendor-check: on -->\n"
        "An RDF4J graph, and a GDB token.\n"
    )

    offences = offences_in("docs/reference/triple-store.md", text)

    assert [offence.split(":")[1] for offence in offences] == ["4", "5", "9"]
    assert offences_in("elsewhere.md", "vendor-check: off\nvendor-check: on") == [
        "elsewhere.md:1: a marker outside REGIONS",
        "elsewhere.md:2: a marker outside REGIONS",
    ]


def _cli_help() -> dict[str, str]:
    """The help that each command line of the package prints."""
    from jms_kapps.common.process import process_parser
    from jms_kapps.uc2.__main__ import main

    helps = {"a process's command line": process_parser("A process.").format_help()}
    for name, argv in (("jms-uc2", ["--help"]), ("jms-uc2 slice", ["slice", "--help"])):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as stopped:
            main(argv)
        assert stopped.value.code == 0
        helps[name] = out.getvalue()
    return helps


def test_no_command_line_names_the_vendor_or_its_unit():
    offences = [
        f"{command}: {line.strip()}"
        for command, text in _cli_help().items()
        for line in text.splitlines()
        if VENDOR.search(line) or REPOSITORY.search(line)
    ]

    assert not offences, "These help lines name a vendor or the store's unit:\n" + "\n".join(
        offences
    )


def test_every_command_line_takes_the_dataset():
    helps = _cli_help()

    assert all("--dataset" in text for text in helps.values()), helps


def _modules() -> list[str]:
    package = jms_kapps
    names = [package.__name__]
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}."):
        names.append(module.name)
    return names


def _own(module_name: str, obj: object) -> bool:
    """Whether `obj` is defined in the module, not imported into it. A value that is neither a
    class nor a function, such as a constant, counts as the module's own."""
    if inspect.isclass(obj) or inspect.isfunction(obj):
        return obj.__module__ == module_name
    return True


def _parameters(obj: object) -> list[str]:
    try:
        return list(inspect.signature(obj).parameters)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []


def _fields(cls: type) -> list[str]:
    try:
        return list(inspect.get_annotations(cls))
    except TypeError:
        return []


def test_no_importable_name_or_parameter_names_the_vendor_or_its_unit():
    offences: list[str] = []
    for module_name in _modules():
        if any(
            module_name == package or module_name.startswith(f"{package}.")
            for package in EXEMPT_PACKAGES
        ):
            continue
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if name.startswith("_") or inspect.ismodule(obj) or not _own(module_name, obj):
                continue
            where = f"{module_name}.{name}"
            names = [(where, name)]
            if callable(obj):
                names += [(f"{where}({p})", p) for p in _parameters(obj)]
            if inspect.isclass(obj):
                names += [(f"{where}.{field}", field) for field in _fields(obj)]
                for member, value in vars(obj).items():
                    if member.startswith("_") and member != "__init__":
                        continue
                    names.append((f"{where}.{member}", member))
                    if callable(value):
                        names += [(f"{where}.{member}({p})", p) for p in _parameters(value)]
            offences += [
                place for place, n in names if VENDOR.search(n) or REPOSITORY.search(n)
            ]
    assert not offences, "These names name a vendor or the store's unit:\n" + "\n".join(
        offences
    )
