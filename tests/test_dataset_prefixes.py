"""A seed registers jms_kapps' prefixes in the dataset it seeds, and deletes the names nobody
chose that only repeat one of them, so a person who reads the dataset in the store's workbench
reads `fc:Box`, not `ns1:Box`. Only the seed changes a dataset's prefixes. A store without a
prefix registry gets none: the seed logs one line and goes on, because the prefixes serve only
the workbench's display.

Both use cases seed through one reset. The offline tests run each seed against a fake store, whose
prefix registry behaves as the KIT store's does. The live test seeds the dataset `--dataset`
names, and reads its prefixes back; it clears that dataset, and plants two prefixes in it, which
it deletes again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from _loopback import LOOPBACK_ENDPOINT
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_triplestore_interface import TripleStoreNotSupportedError

import jms_kapps
from jms_kapps.common.prefixes import PREFIXES
from jms_kapps.common.triplestore import connect, reset
from jms_kapps.uc1.seed import uc1_ontology
from jms_kapps.uc2.seed import seed_topology
from jms_kapps.uc2.topology import load_topology

# The prefixes and namespaces jms_kapps writes, spelled out as the ontologies declare them.
EXPECTED = {
    "fc": "http://w3id.org/circularfactory/FlexConveyor#",
    "fci": "http://w3id.org/circularfactory/FlexConveyorInstances#",
    "cfc": "http://w3id.org/circularfactory/Core#",
    "svc": "http://w3id.org/circularfactory/Service#",
    "mes": "http://w3id.org/circularfactory/MES#",
    "uns": "http://w3id.org/circularfactory/Unscrewing#",
    "uq": "http://w3id.org/circularfactory/Uncertainty#",
    "sh": "http://www.w3.org/ns/shacl#",
}

# A prefix declaration of a Turtle document.
DECLARATION = re.compile(r"@prefix\s+(?P<prefix>[^\s:]*):\s*<(?P<namespace>[^>]*)>")


class FakeStore:
    """A dataset as a seed sees it, through the store's contract. Its graphs and its shapes take
    any document and keep no statement. Its prefix registry behaves as the KIT store's does:
    `prefixes_get` answers every prefix, `prefix_set` maps a prefix to a namespace, and
    `prefix_delete` removes a prefix, the empty one included and a missing one without complaint;
    and an import, or a setting of the shapes, which the store sends to its graph store as it sends
    an import, keeps each prefix the document declares that the dataset has no mapping for yet,
    even one whose namespace another prefix maps already."""

    dataset = "fake"
    _endpoint = LOOPBACK_ENDPOINT

    def __init__(self, namespaces: dict[str, str] | None = None):
        self.namespaces = dict(namespaces or {})

    def graph_clear(self, graph_iri=None) -> bool:
        return True

    def graph_add(self, content, **kwargs) -> bool:
        for found in DECLARATION.finditer(content):
            self.namespaces.setdefault(found["prefix"], found["namespace"])
        return True

    def shapes_set(self, data, content_type) -> None:
        self.graph_add(data)

    def query(self, query, **kwargs):
        return {"boolean": False, "results": {"bindings": []}}

    def prefixes_get(self) -> dict[str, str]:
        return dict(self.namespaces)

    def prefix_set(self, prefix: str, iri: str) -> None:
        self.namespaces[prefix] = iri

    def prefix_delete(self, prefix: str) -> None:
        self.namespaces.pop(prefix, None)


class StoreWithoutPrefixes(FakeStore):
    """A store whose dataset has no prefix registry: each of its prefix operations raises, as the
    contract says such a store's do."""

    def prefixes_get(self) -> dict[str, str]:
        raise TripleStoreNotSupportedError("no prefix registry, so no prefixes_get")

    def prefix_set(self, prefix: str, iri: str) -> None:
        raise TripleStoreNotSupportedError("no prefix registry, so no prefix_set")

    def prefix_delete(self, prefix: str) -> None:
        raise TripleStoreNotSupportedError("no prefix registry, so no prefix_delete")


def launch_uc2(db) -> None:
    """What a launch of use case 2 seeds."""
    seed_topology(db, load_topology("cross"), transit_seconds=0)


def seed_uc1(db, individuals: str = "") -> None:
    """What use case 1 seeds: its ontology, no shapes yet, and `individuals`."""
    reset(
        db,
        ontology=uc1_ontology(),
        shapes="",
        individuals=individuals,
    )


@pytest.mark.parametrize("seed", [launch_uc2, seed_uc1], ids=["uc2", "uc1"])
def test_a_seed_registers_each_prefix_in_the_dataset(seed):
    store = FakeStore()

    seed(store)

    assert {p: store.namespaces.get(p) for p in EXPECTED} == EXPECTED


def test_a_launch_keeps_a_prefix_the_dataset_maps_elsewhere_and_logs_it(caplog):
    store = FakeStore({"fc": "http://example.org/another-vocabulary#"})

    with caplog.at_level("WARNING"):
        launch_uc2(store)

    assert store.namespaces["fc"] == "http://example.org/another-vocabulary#"
    assert store.namespaces["fci"] == EXPECTED["fci"]
    assert any(
        "fc:" in message
        and "http://example.org/another-vocabulary#" in message
        and EXPECTED["fc"] in message
        for message in caplog.messages
    ), caplog.messages


@pytest.mark.parametrize(
    "own",
    [{}, {"fc": EXPECTED["fc"], "mes": EXPECTED["mes"]}],
    ids=["registered-by-the-launch", "registered-before"],
)
def test_a_launch_deletes_the_names_an_import_made_up_for_its_namespaces(own):
    # What KIT's `jms-kapps` held after the launches before this one, with or without the
    # launch's own prefixes for the same namespaces.
    store = FakeStore({**own, "ns1": EXPECTED["fc"], "ns2": EXPECTED["mes"]})

    launch_uc2(store)

    # The use case's ontology declares the empty prefix for its own vocabulary, so the launch's
    # imports add it again: it is gone too.
    assert {
        prefix: namespace
        for prefix, namespace in store.namespaces.items()
        if namespace in EXPECTED.values()
    } == EXPECTED


def test_a_launch_keeps_a_made_up_name_when_no_prefix_of_its_own_maps_that_namespace():
    # `fc` maps another namespace, so after the registration no prefix of the launch's own maps
    # the FlexConveyor's: `ns1` is then not redundant, and stays.
    store = FakeStore(
        {"fc": "http://example.org/another-vocabulary#", "ns1": EXPECTED["fc"]}
    )

    launch_uc2(store)

    assert store.namespaces["ns1"] == EXPECTED["fc"]
    # So does the empty prefix the ontology's import declares for the same namespace.
    assert store.namespaces[""] == EXPECTED["fc"]


def test_a_seed_deletes_a_name_its_last_import_made_up():
    store = FakeStore()

    seed_uc1(
        store,
        individuals=f"@prefix ns5: <{EXPECTED['uns']}> .\nns5:M5 a ns5:ScrewType .\n",
    )

    assert "ns5" not in store.namespaces


@pytest.mark.parametrize(
    ("prefix", "namespace"),
    [
        # As on KIT's `Tests`, which other projects share.
        ("", "http://www.sfb1574.kit.edu/ontologies/TransferUnit#"),
        ("ns3", "http://example.org/another-vocabulary#"),
        ("flex", EXPECTED["fc"]),
        ("ns", EXPECTED["fc"]),
        ("ns1a", EXPECTED["fc"]),
    ],
    ids=[
        "empty-elsewhere",
        "ns3-elsewhere",
        "chosen-ours",
        "ns-without-digits-ours",
        "ns1a-ours",
    ],
)
def test_a_launch_keeps_every_other_prefix(prefix, namespace):
    store = FakeStore({prefix: namespace})

    launch_uc2(store)

    assert store.namespaces.get(prefix) == namespace


@pytest.mark.parametrize("seed", [launch_uc2, seed_uc1], ids=["uc2", "uc1"])
def test_a_seed_on_a_store_without_a_prefix_registry_logs_one_line_and_goes_on(seed, caplog):
    store = StoreWithoutPrefixes({"ns1": EXPECTED["fc"]})

    with caplog.at_level("INFO", logger="jms_kapps.common.prefixes"):
        seed(store)

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "jms_kapps.common.prefixes"
    ]
    assert len(lines) == 1, lines
    assert "prefix" in lines[0] and "fake" in lines[0], lines


# Where each call on the way to a change of a dataset's prefixes may stand: the store's prefix
# operations only in the registration, the registration only in the reset, and the reset only in
# a use case's seed. No participant, and no other code path, changes the prefixes.
NAMESPACE_CALLERS = {
    "prefix_set": {Path("common/prefixes.py")},
    "prefix_delete": {Path("common/prefixes.py")},
    "register_prefixes": {Path("common/triplestore.py")},
    "reset": {Path("uc1/seed.py"), Path("uc2/seed.py")},
}


def _calls(tree: ast.AST, names: set[str]) -> list[str]:
    """The names among `names` that `tree` calls, as a function or as a method."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in names:
                found.append(name)
    return found


def test_only_the_seed_changes_a_datasets_prefixes():
    source = Path(jms_kapps.__file__).parent
    misplaced = {
        f"{path.relative_to(source)}: {name}"
        for path in sorted(source.rglob("*.py"))
        for name in _calls(
            ast.parse(path.read_text(encoding="utf-8")), set(NAMESPACE_CALLERS)
        )
        if path.relative_to(source) not in NAMESPACE_CALLERS[name]
    }

    assert misplaced == set()


@pytest.mark.graphdb
@pytest.mark.skipif(
    not triplestore_env_present(), reason="no triple store connection variables"
)
def test_a_launch_on_a_live_dataset_registers_each_prefix_and_deletes_a_made_up_one(
    dataset,
):
    db = connect(dataset)
    elsewhere = "http://example.org/not-a-jms-kapps-vocabulary#"
    # As an earlier import leaves them: a made-up name for one of the launch's namespaces, and one
    # for a namespace of somebody else's.
    planted = {"ns9": EXPECTED["fc"], "ns8": elsewhere}
    for prefix, namespace in planted.items():
        db.prefix_set(prefix, namespace)
    before = db.prefixes_get()
    try:
        launch_uc2(db)

        after = db.prefixes_get()
        assert {p: after.get(p) for p in EXPECTED} == EXPECTED
        assert "ns9" not in after
        # Every prefix that maps another namespace stays: the planted `ns8`, and whatever else
        # shares the dataset, such as an empty prefix of another project's.
        elsewhere_before = {
            p: ns for p, ns in before.items() if ns not in PREFIXES.values()
        }
        assert {p: after.get(p) for p in elsewhere_before} == elsewhere_before
    finally:
        for prefix in planted:
            db.prefix_delete(prefix)
