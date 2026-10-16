"""The prefill generator of the measurement harness (jms_kapps 79), offline: the file it writes, read
back with rdflib, and the commands that write it. No store is involved.

A prefill holds past journeys of use case 2 as use case 2 writes them when a journey has ended: each
box in the WarehouseManagementSystem's possession, its transport `done`, and the links between the
two, both ways. The live criterion, the store admitting a 100k file in transactions, is in
`test_evaluation_prefill_live.py`.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import subprocess
import sys
import tracemalloc
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from uuid import UUID

import pytest
from kapps_semantic_middleware.possession import mint_possession_state_iri
from kapps_semantic_middleware.registration import mint_capability_iri
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from rdflib import OWL, RDF, Graph, Literal, URIRef

import jms_kapps
from jms_kapps.evaluation import prefill
from jms_kapps.evaluation.prefill import (
    PrefillRefused,
    plan,
    transactions,
    write_prefill,
)
from jms_kapps.uc2.topology import Topology, choices, shipped_topology
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, transport_iri
from jms_kapps.uc2.wms import spawn_candidates

PACKAGE = Path(jms_kapps.__file__).parent

# The ontologies a seed loads: use case 2's own, and the middleware's three shared ones.
ONTOLOGIES = [
    ("jms_kapps.uc2", "ontology/flexconveyor_usecase.ttl"),
    ("kapps_semantic_middleware", "ontology/core.ttl"),
    ("kapps_semantic_middleware", "ontology/service.ttl"),
    ("kapps_semantic_middleware", "ontology/mes.ttl"),
]

HEADER_KEYS = ["topology", "size", "seed", "boxes", "generator", "statements"]

WMS_STATE = URIRef(mint_possession_state_iri(WMS))


def made(tmp_path: Path, size: int, *, seed: int = 7, topology: str = "grid") -> Path:
    """The prefill of `size` statements on `topology` with `seed`, written below `tmp_path`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"prefill-{Path(topology).stem}-{size}-{seed}.ttl"
    write_prefill(path, topology=topology, size=size, seed=seed)
    return path


def header(path: Path) -> dict[str, str]:
    """The header of the file at `path`: its first comment lines, each `# key: value`."""
    found: dict[str, str] = {}
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if not line.startswith("# "):
                break
            key, _, value = line[2:].rstrip("\n").partition(": ")
            found[key] = value
    return found


def body(path: Path) -> bytes:
    """The file at `path` without its header."""
    return b"".join(
        line
        for line in path.read_bytes().splitlines(keepends=True)
        if not line.startswith(b"#")
    )


def parsed(path: Path) -> Graph:
    return Graph().parse(path, format="turtle")


class _Counted(Graph):
    """A graph that only counts the statements a parse gives it, distinct or not, and the boxes
    among them, and holds none: a parse of a 1M file into it needs no more memory than one of a
    small file."""

    def __init__(self) -> None:
        super().__init__()
        self.statements = 0
        self.boxes = 0

    def add(self, triple):  # type: ignore[override]
        self.statements += 1
        if triple[1] == RDF.type and triple[2] == URIRef(FC.Box):
            self.boxes += 1
        return self


def counted(path: Path) -> _Counted:
    graph = _Counted()
    graph.parse(path, format="turtle")
    return graph


@pytest.fixture(scope="module")
def ontologies() -> Graph:
    graph = Graph()
    for package, path in ONTOLOGIES:
        graph.parse(
            data=resources.files(package).joinpath(path).read_text(encoding="utf-8"),
            format="turtle",
        )
    return graph


@pytest.fixture(scope="module")
def grid_10k(tmp_path_factory) -> tuple[Path, Graph]:
    """A prefill of 10k statements on the grid, and its parse."""
    path = made(tmp_path_factory.mktemp("grid"), 10_000)
    return path, parsed(path)


def boxes_in(graph: Graph) -> set[URIRef]:
    return set(graph.subjects(RDF.type, URIRef(FC.Box)))


def transports_in(graph: Graph) -> set[URIRef]:
    return set(graph.subjects(RDF.type, URIRef(FC.BoxTransportOperation)))


# --- Deterministic -------------------------------------------------------------------------------


def test_the_same_topology_size_and_seed_give_a_byte_identical_file(tmp_path):
    first = made(tmp_path / "first", 10_000, seed=3)
    second = made(tmp_path / "second", 10_000, seed=3)

    assert first.read_bytes() == second.read_bytes()


def test_another_seed_gives_other_data(tmp_path):
    one = made(tmp_path, 10_000, seed=3)
    other = made(tmp_path, 10_000, seed=4)

    assert body(one) != body(other)
    assert header(one)["boxes"] == header(other)["boxes"]


def test_the_file_has_unix_line_endings_on_every_platform(tmp_path):
    """Byte-identical across machines too: a text file written on Windows would end its lines
    with CRLF."""
    assert b"\r" not in made(tmp_path, 1_000).read_bytes()


# --- Size and header -----------------------------------------------------------------------------


@pytest.mark.parametrize("size", [1_000, 100_000, 1_000_000], ids=["1k", "100k", "1M"])
def test_a_parse_counts_the_size_asked_for_within_one_percent(tmp_path, size):
    """And every header line is there, with the number of boxes in the file."""
    path = made(tmp_path, size)

    parse = counted(path)
    found = header(path)

    assert abs(parse.statements - size) <= size / 100, parse.statements
    assert list(found) == HEADER_KEYS
    assert found["size"] == str(size)
    assert int(found["statements"].split()[0]) == parse.statements
    assert int(found["boxes"]) == parse.boxes


@pytest.mark.parametrize("size", [1_000, 100_000], ids=["1k", "100k"])
def test_every_statement_in_the_file_is_distinct(tmp_path, size):
    """The count above counts each statement as the parser meets it; a store keeps one of two
    equal ones. So the file holds none twice, and the store holds the size too."""
    path = made(tmp_path, size)

    assert len(parsed(path)) == counted(path).statements


def test_every_header_line_is_present_in_its_order(tmp_path):
    path = made(tmp_path, 100_000, seed=79)

    found = header(path)

    assert list(found) == HEADER_KEYS
    assert found["topology"] == "grid"
    assert found["size"] == "100000"
    assert found["seed"] == "79"
    assert (
        found["generator"] == f"jms_kapps.evaluation.prefill {prefill.PREFILL_VERSION}"
    )
    assert found["statements"].endswith(
        "in this file; the size counts the statements in this file, before inference"
    )


def test_the_number_of_boxes_in_the_header_is_the_number_in_the_file(grid_10k):
    path, graph = grid_10k

    assert int(header(path)["boxes"]) == len(boxes_in(graph)) == 1_000


def test_every_box_is_named_by_a_uuid_of_its_own_from_the_seed(tmp_path, grid_10k):
    """As the WarehouseManagementSystem names a box it spawns, `Box-<UUID>` (jms_kapps 92, decided
    by Sören on jms_kapps 85: "past boxes get UUIDs from the seeded generator"). The seed gives the
    names: the same seed the same, as the file is byte for byte, and another seed others. (Changed
    on 2026-10-12. Until then the boxes were numbered from one.)"""
    _, graph = grid_10k

    names = [str(box).removeprefix(f"{FCI_NS}Box-") for box in boxes_in(graph)]
    assert len(set(names)) == 1_000
    for name in names:
        assert str(UUID(name)) == name, name
    other = boxes_in(parsed(made(tmp_path, 10_000, seed=4)))
    assert boxes_in(graph).isdisjoint(other)


def test_the_generator_s_version_is_2_since_the_boxes_are_named_by_uuids(tmp_path):
    """Version 1 numbered the boxes from one: the same topology, size and seed give another file
    since jms_kapps 92."""
    assert (
        header(made(tmp_path, 1_000))["generator"] == "jms_kapps.evaluation.prefill 2"
    )


def test_the_header_names_a_topology_file_as_it_was_given(tmp_path):
    topology = tmp_path / "mine.json"
    topology.write_text(
        (PACKAGE / "uc2" / "topologies" / "tee.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    path = made(tmp_path, 1_000, topology=str(topology))

    assert header(path)["topology"] == str(topology)


# --- Content -------------------------------------------------------------------------------------


def test_every_class_and_property_in_the_file_is_declared(grid_10k, ontologies):
    _, graph = grid_10k
    classes = set(ontologies.subjects(RDF.type, OWL.Class))
    properties = set(ontologies.subjects(RDF.type, OWL.ObjectProperty)) | set(
        ontologies.subjects(RDF.type, OWL.DatatypeProperty)
    )

    used_classes = set(graph.objects(None, RDF.type))
    used_properties = set(graph.predicates()) - {RDF.type}

    assert used_classes - classes == set()
    assert used_properties - properties == set()
    assert used_classes == {
        URIRef(FC.Box),
        URIRef(FC.BoxTransportOperation),
        URIRef(CFC.PossessionState),
    }


def test_every_box_links_to_its_transport_and_each_transport_back_to_its_box(grid_10k):
    _, graph = grid_10k
    has_transport, has_box = URIRef(FC.hasTransport), URIRef(FC.hasBox)

    for box in boxes_in(graph):
        transports = list(graph.objects(box, has_transport))
        assert transports == [URIRef(transport_iri(IRI(str(box))))]
        assert list(graph.objects(transports[0], has_box)) == [box]
    for transport in transports_in(graph):
        assert len(list(graph.objects(transport, has_box))) == 1
    assert len(transports_in(graph)) == len(boxes_in(graph))


def test_no_box_is_possessed_by_a_module(grid_10k):
    _, graph = grid_10k

    for box in boxes_in(graph):
        states = list(graph.objects(box, URIRef(CFC.hasPossessedWorkpiece)))
        assert states == [WMS_STATE]
    assert list(graph.subjects(URIRef(CFC.hasPossessor), WMS_STATE)) == [URIRef(WMS)]
    assert set(graph.subjects(RDF.type, URIRef(CFC.PossessionState))) == {WMS_STATE}


def test_every_transport_is_done(grid_10k):
    _, graph = grid_10k

    statuses = {
        transport: list(graph.objects(transport, URIRef(SVC.operationStatus)))
        for transport in transports_in(graph)
    }

    assert statuses
    assert all(found == [Literal(OperationStatus.DONE)] for found in statuses.values())


def test_no_reservation_is_in_the_file(grid_10k):
    """A reservation lives in a module's memory, and the graph has no term for one. So the file
    holds nothing about one: none of the use case's workflows or capabilities, of Reserve or any
    other, and no subject but a box, its transport and the WarehouseManagementSystem's state."""
    path, graph = grid_10k
    subjects = set(graph.subjects())

    assert "Reserve" not in path.read_text(encoding="utf-8")
    assert subjects == boxes_in(graph) | transports_in(graph) | {URIRef(WMS), WMS_STATE}


def test_each_transport_ran_as_the_real_wms_would_send_it_and_ended_on_its_destination(
    grid_10k,
):
    """Each journey goes from an origin to a destination that the WarehouseManagementSystem's
    `real` strategy may pick, and its transport stays assigned where use case 2 leaves it: on the
    destination, whose BoxTransportCapability it implements."""
    _, graph = grid_10k
    topology = shipped_topology("grid")
    candidates = candidates_of(topology)

    def one(subject, predicate):
        return next(graph.objects(subject, URIRef(predicate)))

    pairs = set()
    for transport in transports_in(graph):
        origin, destination = (
            one(transport, FC.hasOrigin),
            one(transport, FC.hasDestination),
        )
        assert str(destination) in candidates[str(origin)]
        assert one(transport, CFC.executedOn) == destination
        assert one(transport, CFC.implementsCapability) == URIRef(
            mint_capability_iri(destination, "box_transport")
        )
        pairs.add((origin, destination))
    assert len(pairs) > len(candidates)


def candidates_of(topology: Topology) -> dict[str, list[str]]:
    origins, destinations = choices({m.resource: m.types for m in topology.modules})
    connections = [
        (m.resource, FCI_NS + c.to) for m in topology.modules for c in m.connections
    ]
    found = spawn_candidates(connections, origins.modules, destinations.modules)
    return {str(o): [str(d) for d in ends] for o, ends in found.items()}


def test_the_capability_is_the_one_a_module_registers_for_its_box_transport_workflow():
    """The middleware names a workflow's capability after the function that a module registers as
    its workflow; the prefill names the BoxTransportCapability so too, without starting a
    module."""
    source = (PACKAGE / "uc2" / "process.py").read_text(encoding="utf-8")

    registered = [
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and any(
            keyword.arg == "capability_class"
            and ast.unparse(keyword.value) == "FC.BoxTransportCapability"
            for keyword in decorator.keywords
        )
    ]

    assert registered == [prefill.BOX_TRANSPORT_WORKFLOW]


# --- Memory --------------------------------------------------------------------------------------


def peak_bytes(tmp_path: Path, size: int) -> int:
    """The peak of what Python allocated while the prefill of `size` statements was made."""
    tracemalloc.start()
    try:
        made(tmp_path, size)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_the_peak_memory_for_1m_is_at_most_twice_that_for_100k(tmp_path):
    peak_100k = peak_bytes(tmp_path, 100_000)
    peak_1m = peak_bytes(tmp_path, 1_000_000)

    assert peak_1m <= 2 * peak_100k, (peak_100k, peak_1m)


# --- Refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 5, 11, 107])
def test_a_size_that_cannot_be_met_within_one_percent_is_refused(size):
    with pytest.raises(PrefillRefused, match="within 1 %"):
        plan("grid", size=size, seed=1)


def test_a_negative_seed_is_refused():
    """Python's generator takes a negative seed as its absolute value, so -3 and 3 would give one
    file."""
    with pytest.raises(PrefillRefused, match="seed"):
        plan("grid", size=1_000, seed=-3)


def test_a_topology_on_which_no_box_can_journey_is_refused(tmp_path):
    lonely = tmp_path / "lonely.json"
    lonely.write_text(f'[{{"id": "{FCI_NS}Module1"}}]', encoding="utf-8")

    with pytest.raises(PrefillRefused, match="no origin reaches a destination"):
        plan(str(lonely), size=1_000, seed=1)


# --- Transactions --------------------------------------------------------------------------------


def chunks(path: Path, statements: int) -> list[str]:
    return list(transactions(path, statements=statements))


def test_the_transactions_hold_every_statement_of_the_file_once(grid_10k):
    path, graph = grid_10k

    together = Graph()
    count = 0
    for chunk in chunks(path, 1_000):
        part = Graph().parse(data=chunk, format="turtle")
        count += len(part)
        together += part

    assert count == len(graph)
    assert set(together) == set(graph)


def test_each_transaction_holds_whole_journeys_and_at_most_the_statements_asked(
    grid_10k,
):
    """A box, its possession and its transport need each other, so the store would refuse any one
    of them alone: no box is split from its possession or its transport."""
    path, _ = grid_10k

    for chunk in chunks(path, 1_000):
        part = Graph().parse(data=chunk, format="turtle")
        assert len(part) <= 1_000
        assert boxes_in(part) == set(part.objects(None, URIRef(FC.hasBox)))
        assert transports_in(part) == set(part.objects(None, URIRef(FC.hasTransport)))
        for box in boxes_in(part):
            assert (box, URIRef(CFC.hasPossessedWorkpiece), WMS_STATE) in part


def test_the_first_transaction_holds_the_state_the_boxes_point_at(grid_10k):
    path, _ = grid_10k

    first = Graph().parse(data=chunks(path, 1_000)[0], format="turtle")

    assert (URIRef(WMS), URIRef(CFC.hasPossessor), WMS_STATE) in first
    assert (WMS_STATE, RDF.type, URIRef(CFC.PossessionState)) in first


def test_a_transaction_smaller_than_a_journey_is_refused(grid_10k):
    path, _ = grid_10k

    with pytest.raises(ValueError, match="at least"):
        chunks(path, 9)


# --- The command ---------------------------------------------------------------------------------


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "jms_kapps.evaluation.prefill", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_writes_the_file_it_is_given(tmp_path):
    out = tmp_path / "prefill.ttl"

    done = run(
        "--topology", "grid", "--size", "1k", "--seed", "5", "--output", str(out)
    )

    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == made(tmp_path, 1_000, seed=5).read_bytes()
    assert str(out) in done.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "prefill-grid-1000-5.ttl",
        "prefill.ttl",
    ]


def test_a_write_that_fails_leaves_no_file(tmp_path, monkeypatch):
    def fails(out, made_prefill):
        out.write("# topology: grid\n")
        raise OSError("the disk is full")

    monkeypatch.setattr(prefill, "_write", fails)

    with pytest.raises(OSError, match="the disk is full"):
        write_prefill(tmp_path / "prefill.ttl", topology="grid", size=1_000, seed=1)
    assert list(tmp_path.iterdir()) == []


def test_the_command_refuses_a_folder_that_is_not_there(tmp_path):
    out = tmp_path / "missing" / "prefill.ttl"

    done = run(
        "--topology", "grid", "--size", "1k", "--seed", "5", "--output", str(out)
    )

    assert done.returncode == 2
    assert "cannot write" in done.stderr


@pytest.mark.parametrize(
    ("given", "size"),
    [
        ("1000", 1_000),
        ("1k", 1_000),
        ("100k", 100_000),
        ("1M", 1_000_000),
        ("10M", 10_000_000),
    ],
)
def test_the_command_reads_a_size_with_or_without_its_unit(given, size):
    assert prefill.size_of(given) == size


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (["--size", "2x"], "size"),
        (["--size", "5"], "within 1 %"),
        (["--seed", "-1"], "seed"),
        (["--topology", "no-such-topology"], "no-such-topology"),
    ],
)
def test_the_command_refuses_with_the_reason(tmp_path, args, why):
    out = tmp_path / "prefill.ttl"
    given = {"--topology": "grid", "--size": "1k", "--seed": "1"}
    given.update(dict(zip(args[::2], args[1::2], strict=True)))

    done = run(*[x for pair in given.items() for x in pair], "--output", str(out))

    assert done.returncode == 2
    assert why in done.stderr
    assert not out.exists()


# --- A plain install -----------------------------------------------------------------------------


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def plain_closure(distribution: str) -> set[str]:
    """`distribution` and every distribution a plain install of it may bring, by normalized name.

    A requirement that only an extra brings is left out. One that only some platforms or Pythons
    bring is kept, so the closure is, if anything, too wide, never too narrow.
    """
    seen: set[str] = set()
    todo = [distribution]
    while todo:
        name = todo.pop()
        if normalized(name) in seen:
            continue
        seen.add(normalized(name))
        try:
            requirements = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for requirement in requirements:
            _, _, marker = requirement.partition(";")
            if re.search(r"\bextra\b", marker):
                continue
            todo.append(re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip())[0])
    return seen


def loaded_distributions(module: str) -> Iterator[tuple[str, str]]:
    """Each top-level module that importing `module` loads, with the distribution that installed
    it; the standard library's are left out."""
    script = f"import sys, {module}\nprint('\\n'.join(sys.modules))\n"
    names = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    ).stdout.split()
    owners = importlib.metadata.packages_distributions()
    for top in sorted({name.split(".")[0] for name in names}):
        if top in sys.stdlib_module_names or top.startswith("_") or top == "jms_kapps":
            continue
        for distribution in owners.get(top, []):
            yield top, distribution


def test_the_prefill_needs_nothing_a_plain_install_lacks():
    """The generator runs on a plain install of jms_kapps: what it loads comes with jms_kapps'
    own dependencies, and needs no harness-only one."""
    plain = plain_closure("jms-kapps")

    outside = {
        top: distribution
        for top, distribution in loaded_distributions("jms_kapps.evaluation.prefill")
        if normalized(distribution) not in plain
    }

    assert outside == {}
