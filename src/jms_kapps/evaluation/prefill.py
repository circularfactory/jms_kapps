"""The prefill generator of the measurement harness: one Turtle file of past journeys of use case 2,
of a given size in statements, made from a seed for a topology.

**What the file holds** is what use case 2 writes when a journey has ended `done`: each box,
named by a UUID as the WarehouseManagementSystem names the boxes it spawns (`Box-<UUID>`,
`vocabulary.uuid_box_iri`), in the WarehouseManagementSystem's possession; its transport, `done`,
from its
origin to its destination, still assigned to the destination and implementing the destination's
BoxTransportCapability, as the last handover leaves it; and the links between the box and its
transport, both ways (`fc:hasTransport`, `fc:hasBox`). The WarehouseManagementSystem's one
possession state, which every box points at, comes first: in a run, the first box that comes back
creates it. Each box's origin and destination are a pair that the WarehouseManagementSystem's
`real` strategy may pick on the topology while every origin is free (`wms.spawn_candidates`): an
origin, and a destination that the origin reaches. Each transport has the IRI that the
WarehouseManagementSystem's opening gives it (`vocabulary.transport_iri`): like the seed, the
prefill creates transports, which no party does but the opening.

So a running use case 2 reads nothing in the file as work in flight: no box is on a module, no
transport is `queued` or `running`, and there is no reservation, which lives in a module's memory
and never in the graph. Neither the WarehouseManagementSystem nor a module reads every box in it
either: the WarehouseManagementSystem names a new box by a new UUID, and asks about a box that
comes back in one `ASK`. The header gives the number of boxes beside the size.

**When it is loaded.** After the seed, which writes the modules and the WarehouseManagementSystem
that the file names, and before any participant starts, as the seed is: it stands in for a factory
that has run, and is no participant's write. The store validates it against use case 2's shapes
like any other write. A box, its possession and its transport need each other there, so
`transactions` splits the file between boxes, each whole with its transport. A module's BoxTransportCapability comes into
the graph when the module registers its workflows, after the load; before it a transport names
one that is not there yet, and nothing reads it.

**The size** counts the statements in the file, before inference: 10 for each box with its
transport, and 2 for the WarehouseManagementSystem's possession state. The file holds the size asked for within 1 %, or the generator refuses the size.

**Deterministic and streaming.** One topology, size and seed give one file, byte for byte, with
Unix line endings on every platform. Each pick takes the next `random()` of
`random.Random(seed)`, a sequence that Python keeps the same for a seed across its versions, and
each box's UUID the next 128 bits of the same generator (`getrandbits(128)`), formatted as a UUID
of version 4: for each box, its origin, its destination, then its UUID. The
generator writes each box as it makes it and holds none, so a file of 10M statements needs no
more memory than one of 100k. `PREFILL_VERSION` changes whenever the file for the same topology,
size and seed changes.

**The header** is the file's first comment lines, one `# key: value` each: the topology as it was
given, the size asked for, the seed, the number of boxes, the generator and its version, and the
number of statements in the file, which the size counts.

The command (`main`): `python -m jms_kapps.evaluation.prefill --topology grid --size 100k --seed
79 --output prefill.ttl`.
"""

from __future__ import annotations

import argparse
import random
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from kapps_semantic_middleware.possession import mint_possession_state_iri
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI

from jms_kapps.common.prefixes import PREFIXES
from jms_kapps.uc2.topology import TopologyRefused, choices, load_topology
from jms_kapps.uc2.vocabulary import FC, FCI_NS, WMS, transport_iri, uuid_box_iri
from jms_kapps.uc2.wms import spawn_candidates

GENERATOR = "jms_kapps.evaluation.prefill"

PREFILL_VERSION = 2
"""The generator's version, in the header. It changes whenever the file for one topology, size
and seed changes. Version 2 names each box by a UUID from the seeded generator;
version 1 numbered the boxes from 1."""

BOX_TRANSPORT_WORKFLOW = "box_transport"
"""The name under which a module registers its BoxTransportWorkflow (`uc2.process`). The middleware
names the module's BoxTransportCapability after it, `<module>_capability_box_transport`, when the
module registers."""

STATEMENTS_PER_BOX = 10
STATEMENTS_OF_THE_STATE = 2

# Each vocabulary the file names, by the prefix a seed registers for it (`common.prefixes`).
_PREFIXES = ("fc", "fci", "cfc", "svc")

# The units a size may carry on the command line.
_UNITS = {"": 1, "k": 1_000, "M": 1_000_000}


class PrefillRefused(ValueError):
    """The generator refuses what it was asked for: the message is why, for a person to read."""


@dataclass(frozen=True)
class Prefill:
    """One prefill as it is asked for, and what it holds.

    `topology` is the topology as it was given, a shipped name or a path; `candidates` are the
    journeys it allows, each origin with its destinations, in the topology's order.
    """

    topology: str
    size: int
    seed: int
    boxes: int
    candidates: Mapping[IRI, Sequence[IRI]] = field(repr=False, compare=False)

    @property
    def statements(self) -> int:
        """The statements in the file."""
        return statements_of(self.boxes)

    def header(self) -> list[str]:
        """The file's first lines, each a comment `# key: value` with its line end."""
        return [
            f"# topology: {self.topology}\n",
            f"# size: {self.size}\n",
            f"# seed: {self.seed}\n",
            f"# boxes: {self.boxes}\n",
            f"# generator: {GENERATOR} {PREFILL_VERSION}\n",
            (
                f"# statements: {self.statements} in this file; the size counts the "
                "statements in this file, before inference\n"
            ),
        ]


def statements_of(boxes: int) -> int:
    """The statements in a prefill of `boxes` boxes."""
    return STATEMENTS_OF_THE_STATE + STATEMENTS_PER_BOX * boxes


def boxes_for(size: int) -> int:
    """The number of boxes whose prefill comes nearest to `size` statements.

    Raises:
        PrefillRefused: If that prefill misses `size` by more than 1 %, or holds no box.
    """
    boxes = max(
        1,
        (size - STATEMENTS_OF_THE_STATE + STATEMENTS_PER_BOX // 2)
        // STATEMENTS_PER_BOX,
    )
    nearest = statements_of(boxes)
    if size <= 0 or abs(nearest - size) * 100 > size:
        raise PrefillRefused(
            f"A prefill of {size} statements cannot be written within 1 %: each box with its "
            f"transport is {STATEMENTS_PER_BOX} statements and the WarehouseManagementSystem's "
            f"possession state {STATEMENTS_OF_THE_STATE}, so the nearest is {nearest}."
        )
    return boxes


def plan(topology: str, *, size: int, seed: int) -> Prefill:
    """The prefill of `size` statements on `topology`, a shipped name or a path, with `seed`.

    Raises:
        PrefillRefused: If the seed is negative, the size cannot be met within 1 %, or the
            topology does not load or allows no journey, naming why.
    """
    if seed < 0:
        raise PrefillRefused(
            f"The seed {seed} is negative: a seed is a whole number from 0, because Python's "
            "generator takes a negative seed as its absolute value, which would give two seeds "
            "one file."
        )
    boxes = boxes_for(size)
    try:
        loaded = load_topology(topology)
    except TopologyRefused as refused:
        raise PrefillRefused(str(refused)) from None
    origins, destinations = choices({m.resource: m.types for m in loaded.modules})
    connections = [
        (m.resource, IRI(c.to, base=FCI_NS))
        for m in loaded.modules
        for c in m.connections
    ]
    candidates = spawn_candidates(connections, origins.modules, destinations.modules)
    if not candidates:
        raise PrefillRefused(
            f"No box can journey on the topology {topology}: no origin reaches a destination "
            "over its connections."
        )
    return Prefill(topology, size, seed, boxes, candidates)


def write_prefill(path: Path, *, topology: str, size: int, seed: int) -> Prefill:
    """Write the prefill of `size` statements on `topology` with `seed` to `path`, and return it.

    The file is written beside `path` first, as `<path>.part`, and takes its name once it is
    whole, so a file at `path` is never a prefill cut short. A write that fails removes the part.

    Raises:
        PrefillRefused: As `plan` raises, before anything is written.
        OSError: If the file cannot be written.
    """
    prefill = plan(topology, size=size, seed=seed)
    path = Path(path)
    part = path.with_name(f"{path.name}.part")
    try:
        with part.open("w", encoding="utf-8", newline="\n") as out:
            _write(out, prefill)
        part.replace(path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return prefill


def journeys(prefill: Prefill) -> Iterator[tuple[IRI, IRI, IRI]]:
    """The box, the origin and the destination of each journey of `prefill`, in the file's order:
    an origin picked among the candidates, then a destination among its own, as `real` picks them
    while every origin is free; then the box, named by a UUID of version 4 made of the next 128
    bits of the same seeded generator."""
    picks = random.Random(prefill.seed)
    origins = list(prefill.candidates)
    for _ in range(prefill.boxes):
        origin = origins[int(picks.random() * len(origins))]
        ends = prefill.candidates[origin]
        destination = ends[int(picks.random() * len(ends))]
        box = uuid_box_iri(uuid.UUID(int=picks.getrandbits(128), version=4))
        yield box, origin, destination


def _write(out: TextIO, prefill: Prefill) -> None:
    """Write `prefill` to `out`: the header, the prefixes, the WarehouseManagementSystem's
    possession state, then one block for each box and its transport, each statement on a line of
    its own, and a blank line after each block."""
    # Every name the blocks take, before anything is written.
    state = _name(mint_possession_state_iri(WMS))
    modules = {
        m for origin, ends in prefill.candidates.items() for m in (origin, *ends)
    }
    names = {m: _name(m) for m in modules}
    capabilities = {
        m: f"{names[m]}_capability_{BOX_TRANSPORT_WORKFLOW}" for m in modules
    }
    out.writelines(prefill.header())
    out.write("\n")
    out.writelines(f"@prefix {p}: <{PREFIXES[p]}> .\n" for p in _PREFIXES)
    out.write("\n")
    out.write(
        f"{_name(WMS)} {_name(CFC.hasPossessor)} {state} .\n"
        f"{state} a {_name(CFC.PossessionState)} .\n\n"
    )
    # Every term but the box, its transport and the two modules is the same in every block.
    box_lines = (
        "{box} a " + _name(FC.Box) + " ;\n"
        "    " + _name(FC.hasTransport) + " {transport} ;\n"
        "    " + _name(CFC.hasPossessedWorkpiece) + f" {state} .\n"
    )
    transport_lines = (
        "{transport} a " + _name(FC.BoxTransportOperation) + " ;\n"
        "    " + _name(FC.hasBox) + " {box} ;\n"
        "    " + _name(FC.hasOrigin) + " {origin} ;\n"
        "    " + _name(FC.hasDestination) + " {destination} ;\n"
        "    " + _name(SVC.hasOperationStatus) + f' "{OperationStatus.DONE}" ;\n'
        "    " + _name(CFC.executedOn) + " {destination} ;\n"
        "    " + _name(CFC.implementsCapability) + " {capability} .\n\n"
    )
    block = box_lines + transport_lines
    out.writelines(
        block.format(
            box=_name(box),
            transport=_name(transport_iri(box)),
            origin=names[origin],
            destination=names[destination],
            capability=capabilities[destination],
        )
        for box, origin, destination in journeys(prefill)
    )


def _name(iri: str) -> str:
    """`iri` as the file writes it: by the prefix of its vocabulary."""
    for prefix in _PREFIXES:
        namespace = PREFIXES[prefix]
        if str(iri).startswith(namespace):
            return f"{prefix}:{str(iri)[len(namespace) :]}"
    raise ValueError(f"{iri} is in no vocabulary the prefill names")


def transactions(path: Path, *, statements: int) -> Iterator[str]:
    """The prefill at `path` as Turtle documents to load one transaction each, in the file's order:
    whole boxes, each with its possession and its transport, at most `statements` statements in
    each, each document with the file's prefixes. The first holds the WarehouseManagementSystem's possession state, which every box
    points at.

    It reads the file as `write_prefill` writes it: comment lines first, then the prefixes, then
    blocks separated by blank lines, each statement on a line of its own.

    Raises:
        ValueError: If `statements` is less than one box's with its transport.
    """
    if statements < STATEMENTS_PER_BOX:
        raise ValueError(
            f"a transaction holds at least one box with its transport, {STATEMENTS_PER_BOX} "
            f"statements, not {statements}"
        )
    prefixes: list[str] = []
    chunk: list[str] = []
    held = 0
    with Path(path).open(encoding="utf-8", newline="\n") as lines:
        for block in _blocks(lines):
            if all(line.startswith("#") for line in block):
                continue
            if all(line.startswith("@prefix") for line in block):
                prefixes.extend(block)
                continue
            if chunk and held + len(block) > statements:
                yield "".join([*prefixes, "\n", *chunk])
                chunk, held = [], 0
            chunk.extend([*block, "\n"])
            held += len(block)
    if chunk:
        yield "".join([*prefixes, "\n", *chunk])


def _blocks(lines: Iterable[str]) -> Iterator[list[str]]:
    """The blocks of `lines`: the runs of lines between blank lines."""
    block: list[str] = []
    for line in lines:
        if line.strip():
            block.append(line)
        elif block:
            yield block
            block = []
    if block:
        yield block


def size_of(text: str) -> int:
    """A size as the command takes it: a whole number of statements, or one with the unit `k`
    (thousand) or `M` (million), such as 1000, 1k, 100k, 1M or 10M.

    Raises:
        ValueError: If `text` is none of these.
    """
    match = re.fullmatch(r"([0-9]+)([kM]?)", text)
    if match is None:
        raise ValueError(
            f"{text!r} is no size: a size is a whole number of statements, or one with the "
            "unit k or M, such as 1000, 100k or 1M"
        )
    number, unit = match.groups()
    return int(number) * _UNITS[unit]


def _size_argument(text: str) -> int:
    try:
        return size_of(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def main(argv: Sequence[str] | None = None) -> int:
    """Write one prefill file, as the options say, and print what it holds. A refusal ends the
    command with status 2 and the reason."""
    parser = argparse.ArgumentParser(
        prog=f"python -m {GENERATOR}",
        description=(
            "Write a prefill for the measurement harness: past journeys of use case 2, each "
            "ended done, as Turtle."
        ),
    )
    parser.add_argument(
        "--topology",
        required=True,
        help="a shipped topology by its name, such as grid, or a topology file by its path",
    )
    parser.add_argument(
        "--size",
        required=True,
        type=_size_argument,
        help="the statements in the file, before inference: 1000, 100k, 1M or 10M",
    )
    parser.add_argument(
        "--seed",
        required=True,
        type=int,
        help="a whole number from 0; the same topology, size and seed give the same file",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="the path of the file to write"
    )
    args = parser.parse_args(argv)
    try:
        prefill = write_prefill(
            args.output, topology=args.topology, size=args.size, seed=args.seed
        )
    except PrefillRefused as refused:
        parser.error(str(refused))
    except OSError as error:
        parser.error(f"cannot write {args.output}: {error}")
    print(
        f"{args.output}: {prefill.boxes} boxes, {prefill.statements} statements, for a size "
        f"of {prefill.size} on {prefill.topology} with the seed {prefill.seed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
