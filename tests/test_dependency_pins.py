"""The smoke test.

It proves that the pinned siblings import, that their installed versions satisfy
the pins, that the manifest and the lock take every package from PyPI,
that every console script resolves, that the RDF, CSV and JSON assets and
both pages reach an installed copy, and that a deprecated name of the
triple-store interface or of the middleware fails the test that uses it.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.resources
import tomllib
from pathlib import Path

import pytest
from _repo import find_repo_root

SIBLING_DISTRIBUTIONS = [
    "kapps-semantic-middleware",
    "kapps-triplestore-interface",
    "kapps-ogm",
]

# The registry that uv.lock names for a package it resolved from PyPI.
PYPI = {"registry": "https://pypi.org/simple"}

SIBLING_IMPORTS = [
    "kapps_semantic_middleware",
    "kapps_triplestore_interface",
    "kapps_ogm",
]

OWN_IMPORTS = [
    "jms_kapps",
    "jms_kapps.common",
    "jms_kapps.uc1",
    "jms_kapps.uc2",
    "jms_kapps.uc2.launcher.runner",
    "jms_kapps.uc2.monitor.process",
]

ASSET_PATHS = [
    "uc1/ontology/integration_usecase.ttl",
    "uc1/data/unscrewing_timeseries/success.csv",
    "uc1/data/unscrewing_timeseries/missing_screw.csv",
    "uc1/static/page.js",
    "uc1/static/style.css",
    "uc2/ontology/flexconveyor_usecase.ttl",
    "uc2/shapes/flexconveyor_usecase_shapes.ttl",
    "uc2/topologies/cross.json",
    "uc2/launcher/static/index.html",
    "uc2/monitor/static/index.html",
]


@pytest.fixture(scope="module")
def repo_root() -> Path:
    """Locate the repository root, or skip the whole module without it."""
    root = find_repo_root(Path(__file__))
    if root is None:
        pytest.skip(
            "No pyproject.toml found above the test file; an installed copy has no manifest"
        )
    return root


@pytest.fixture(scope="module")
def manifest(repo_root: Path) -> dict:
    """Read and parse `pyproject.toml` with the standard library."""
    assert repo_root is not None  # for type checkers
    with (repo_root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


@pytest.fixture(scope="module")
def lock(repo_root: Path) -> dict:
    """Read and parse `uv.lock`, or skip the tests that need it without one."""
    lock_path = repo_root / "uv.lock"
    if not lock_path.is_file():
        pytest.skip("No uv.lock next to the manifest")
    with lock_path.open("rb") as handle:
        return tomllib.load(handle)


def parse_tilde_pin(pin: str) -> tuple[tuple[int, ...], int]:
    """Turn a `~=X.Y.Z` pin into `(floor, prefix_length)`.

    The floor is `(X, Y, Z)` as a tuple of integers. The prefix length is
    `len(parts) - 1`, so a version satisfies the pin when it is greater than
    or equal to the floor and its first `prefix_length` components equal the
    floor's. Patch releases, which add a component beyond the prefix, are
    admitted.
    """
    assert pin.startswith("~="), f"Expected a ~= pin, got {pin!r}"
    parts = pin[2:].split(".")
    floor = tuple(int(part) for part in parts)
    return floor, len(parts) - 1


def leading_numeric_components(version: str) -> tuple[int, ...]:
    """Read the leading numeric components of a version string.

    `importlib.metadata.version` can return suffixes such as `0.1.1b1`,
    `2.1.1.post1` or `1.0.0.dev3`. This stops at the first component that is
    not all digits.
    """
    components: list[int] = []
    for part in version.split("."):
        if not part.isdigit():
            break
        components.append(int(part))
    return tuple(components)


def version_satisfies(installed: str, pin: str) -> bool:
    """Compare an installed version against a `~=X.Y.Z` pin by hand."""
    floor, prefix_length = parse_tilde_pin(pin)
    installed_parts = leading_numeric_components(installed)
    if len(installed_parts) < prefix_length:
        return False
    if installed_parts < floor:
        return False
    return installed_parts[:prefix_length] == floor[:prefix_length]


def sibling_pins(manifest: dict) -> dict[str, str]:
    """Map each sibling distribution to the `~=` pin the manifest declares for it."""
    return {
        declaration.split("~=")[0]: f"~={declaration.split('~=')[1]}"
        for declaration in manifest["project"]["dependencies"]
        if declaration.startswith("kapps-") and "~=" in declaration
    }


def test_version_satisfies() -> None:
    """The comparator implements `~=` correctly over representative cases."""
    cases = [
        ("2.1.1", "~=2.1.1", True),
        ("2.1.2", "~=2.1.1", True),
        ("2.1.0", "~=2.1.1", False),
        ("2.2.0", "~=2.1.1", False),
        ("3.0.0", "~=2.1.1", False),
        ("0.1.1", "~=0.1.1", True),
        ("0.2.0", "~=0.1.1", False),
    ]
    for installed, pin, expected in cases:
        assert version_satisfies(installed, pin) is expected, (
            f"version_satisfies({installed!r}, {pin!r}) != {expected}"
        )


def test_version_satisfies_with_suffix() -> None:
    """A suffix after the numeric components does not crash the comparator.

    A post-release is a release of the same version, so it satisfies the pin.
    A pre-release comes before the floor, so it does not. That is the rule of
    PEP 440, and reading only the leading numeric components gives it.
    """
    assert version_satisfies("0.1.1.post1", "~=0.1.1") is True
    assert version_satisfies("0.1.2.post1", "~=0.1.1") is True
    assert version_satisfies("0.1.1b1", "~=0.1.1") is False


def test_parse_tilde_pin() -> None:
    """The prefix length is one less than the number of components."""
    floor, prefix_length = parse_tilde_pin("~=2.1.1")
    assert floor == (2, 1, 1)
    assert prefix_length == 2


def test_sibling_imports() -> None:
    """Every pinned sibling imports without error."""
    for name in SIBLING_IMPORTS:
        importlib.import_module(name)


def test_own_imports() -> None:
    """Every module of this repository imports without error."""
    for name in OWN_IMPORTS:
        importlib.import_module(name)


def test_installed_versions_satisfy_pins(manifest: dict) -> None:
    """The installed version of each sibling satisfies its declared pin."""
    pins = sibling_pins(manifest)
    for distribution in SIBLING_DISTRIBUTIONS:
        assert distribution in pins, f"No ~= pin declared for {distribution}"
        installed = importlib.metadata.version(distribution)
        assert version_satisfies(installed, pins[distribution]), (
            f"{distribution}: installed {installed} does not satisfy {pins[distribution]}"
        )


def test_pins_admit_patch_releases_only(manifest: dict) -> None:
    """Each sibling's pin is `~=X.Y.Z`, with all three parts.

    Such a pin admits the patch releases of `X.Y` and nothing else, so a later
    minor release reaches an install only after its pin here is raised. A pin
    with two parts, `~=X.Y`, would admit every later minor release of `X`.
    """
    pins = sibling_pins(manifest)
    for distribution in SIBLING_DISTRIBUTIONS:
        assert distribution in pins, f"No ~= pin declared for {distribution}"
        floor, _ = parse_tilde_pin(pins[distribution])
        assert len(floor) == 3, (
            f"{distribution}: pin {pins[distribution]} does not name all three version parts"
        )


def locked_source_not_from_pypi(package: dict) -> str | None:
    """Why `uv.lock` may not resolve `package` from where it does, or None when it resolves it
    from PyPI."""
    source = package["source"]
    if source == PYPI:
        return None
    return f"{package['name']}: uv.lock resolves it from {source}, not from PyPI"


def test_only_pypi_passes_the_guard_of_the_lock() -> None:
    """A package from PyPI passes; a sibling from a Git repository fails, at any commit or
    branch, and so does any other source."""
    git = {"git": "https://example.invalid/kapps_semantic_middleware.git?rev=main#597d7b6"}

    assert locked_source_not_from_pypi({"name": "rdflib", "source": PYPI}) is None
    assert locked_source_not_from_pypi({"name": "kapps-semantic-middleware", "source": git})
    assert locked_source_not_from_pypi(
        {"name": "kapps-ogm", "source": {"registry": "https://example.invalid/simple"}}
    )


def test_manifest_names_no_source_besides_pypi(manifest: dict) -> None:
    """The manifest has no `[tool.uv.sources]` table.

    Such a table takes a sibling from a branch of its repository instead of a published
    release, and the release mechanism refuses a manifest that has one.
    """
    sources = manifest.get("tool", {}).get("uv", {}).get("sources", {})
    assert sources == {}, f"The manifest names the sources {sources}"


def test_lock_resolves_siblings_at_their_pins_and_every_package_from_pypi(
    lock: dict, manifest: dict
) -> None:
    """`uv.lock` resolves each sibling at a version its pin admits, and every package from
    PyPI."""
    locked = {package["name"]: package for package in lock["package"]}
    pins = sibling_pins(manifest)
    for distribution in SIBLING_DISTRIBUTIONS:
        assert distribution in locked, f"{distribution} is not in uv.lock"
        version = locked[distribution]["version"]
        assert version_satisfies(version, pins[distribution]), (
            f"{distribution}: uv.lock holds {version}, "
            f"which does not satisfy {pins[distribution]}"
        )
    refused = [
        why
        for package in lock["package"]
        if package["name"] != "jms-kapps"
        and (why := locked_source_not_from_pypi(package)) is not None
    ]
    assert refused == []


def test_console_scripts_resolve(manifest: dict) -> None:
    """Every console script in `[project.scripts]` resolves to a callable."""
    scripts = manifest["project"].get("scripts", {})
    assert scripts, "No console scripts declared"

    entry_points = importlib.metadata.entry_points(group="console_scripts")
    by_name = {ep.name: ep for ep in entry_points}

    for name in scripts:
        assert name in by_name, f"Console script {name!r} not found"
        loaded = by_name[name].load()
        assert callable(loaded), f"Console script {name!r} does not load to a callable"


def test_assets_reach_installed_copy() -> None:
    """Each RDF and CSV asset exists below the installed `jms_kapps` package."""
    package_root = importlib.resources.files("jms_kapps")
    for relative in ASSET_PATHS:
        # Split on `/` and chain the segments one at a time. The protocol does
        # not promise that a path with separators in one string works.
        asset = package_root
        for segment in relative.split("/"):
            asset = asset / segment
        assert asset.is_file(), f"Asset not found: {relative}"


def _a_deprecated_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the store's URL where only its deprecated variable is set."""
    from kapps_triplestore_interface import TripleStoreEndpoint

    monkeypatch.delenv("TRIPLESTORE_URL", raising=False)
    monkeypatch.setenv("GRAPHDB_URL", "http://127.0.0.1:9")
    TripleStoreEndpoint.from_env(dataset="any")


def _a_deprecated_name_of_the_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    from kapps_semantic_middleware import credentials

    credentials.graphdb_env_present()


def _a_deprecated_name_of_the_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    from kapps_triplestore_interface import exceptions

    exceptions.GraphDbException  # noqa: B018 - the read is the use that warns


@pytest.mark.parametrize(
    "use",
    [
        _a_deprecated_variable,
        _a_deprecated_name_of_the_middleware,
        _a_deprecated_name_of_the_interface,
    ],
    ids=["variable", "middleware", "interface"],
)
def test_a_deprecated_name_of_the_interface_or_the_middleware_fails_the_test_that_uses_it(
    use, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest's `filterwarnings` turns each of their deprecation warnings into an error,
    so that the suite proves that jms_kapps uses none of their deprecated names."""
    with pytest.raises(DeprecationWarning, match="is deprecated; "):
        use(monkeypatch)
