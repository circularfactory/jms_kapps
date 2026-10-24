"""The grid script of the measurement harness: a topology file of W columns and H rows, in the
pattern of the shipped 30-module grid (`uc2/topologies/grid.json`, 6 columns and 5 rows).
A script makes grids of W x H in the pattern of the shipped 30-module grid. Interim name.

**The pattern.** Modules are numbered along the rows, from `Module1` at the top left:

- Row 1 holds the entry modules (`EntryModule`), which link south.
- Rows 2 to H-1 hold plain modules (`FlexConveyorModule`). Each links south, and the rows alternate:
  row 2 also links west, row 3 east, row 4 west, and so on. The first column has no west
  neighbour and the last none to the east, so those modules link south only.
- Row H holds the exit modules (`ExitModule`), chained east to the last module, which has no
  connection.

So 6 x 5 is the shipped grid, byte for byte, and 12 x 5 and 24 x 5 are the 60- and 120-module grids
that `specs/iteration-one.toml` names as `../topologies/grid-60.json` and
`../topologies/grid-120.json`, committed in `topologies/`. The tests check the committed files
against this script.

The command (`main`): `python -m jms_kapps.evaluation.grid --width 12 --height 5 --output
grid-60.json`. The file has Unix line endings on every platform.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from jms_kapps.uc2.topology import (
    CONNECTION_KEY,
    CONNECTS_TO_KEY,
    DIRECTION_KEY,
    LABEL_KEY,
    PORT_KEY,
    TYPE_KEY,
)
from jms_kapps.uc2.vocabulary import FC_NS, FCI_NS

GENERATOR = "jms_kapps.evaluation.grid"

_ENTRY = "EntryModule"
_PLAIN = "FlexConveyorModule"
_EXIT = "ExitModule"


def grid_modules(width: int, height: int) -> list[dict]:
    """The module list of a grid of `width` columns and `height` rows, in the format of a topology
    file (`uc2.topology`).

    Raises:
        ValueError: If `width` is less than 1 or `height` less than 2: a grid needs a column, and
            an entry row and an exit row.
    """
    _checked(width, height)
    modules = []
    for row in range(1, height + 1):
        for column in range(1, width + 1):
            number = (row - 1) * width + column
            links: list[tuple[str, int]] = []
            if row == 1:
                kind = _ENTRY
                links.append(("South", number + width))
            elif row < height:
                kind = _PLAIN
                links.append(("South", number + width))
                if row % 2 == 0 and column > 1:
                    links.append(("West", number - 1))
                if row % 2 == 1 and column < width:
                    links.append(("East", number + 1))
            else:
                kind = _EXIT
                if column < width:
                    links.append(("East", number + 1))
            modules.append(_module(number, kind, links))
    return modules


def grid_text(width: int, height: int) -> str:
    """The text of the topology file of a grid: JSON, one module to a few lines and each list of
    types and each target on one line, as the shipped topologies are written, with a closing
    newline."""
    blocks = [_block(module) for module in grid_modules(width, height)]
    return "[\n" + ",\n".join(blocks) + "\n]\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Write one grid file, as the options say, and print what it holds. A refused size ends the
    command with status 2 and the reason."""
    parser = argparse.ArgumentParser(
        prog=f"python -m {GENERATOR}",
        description=(
            "Write a grid topology for the measurement harness, in the pattern of the shipped "
            "30-module grid (6 columns, 5 rows)."
        ),
    )
    parser.add_argument(
        "--width",
        required=True,
        type=int,
        help="the columns, 1 or more (6 for the shipped grid)",
    )
    parser.add_argument(
        "--height",
        required=True,
        type=int,
        help="the rows, 2 or more: an entry row and an exit row (5 for the shipped grid)",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="the path of the file to write"
    )
    args = parser.parse_args(argv)
    try:
        text = grid_text(args.width, args.height)
    except ValueError as refused:
        parser.error(str(refused))
    try:
        with args.output.open("w", encoding="utf-8", newline="\n") as file:
            file.write(text)
    except OSError as error:
        parser.error(f"cannot write {args.output}: {error}")
    print(
        f"{args.output}: {args.width * args.height} modules, {args.width} columns and "
        f"{args.height} rows"
    )
    return 0


def _checked(width: int, height: int) -> None:
    if width < 1 or height < 2:
        raise ValueError(
            f"a grid of {width} x {height} cannot be made: a grid needs a width of 1 or more "
            "columns and a height of 2 or more rows (an entry row and an exit row)"
        )


def _module(number: int, kind: str, links: list[tuple[str, int]]) -> dict:
    return {
        "id": f"{FCI_NS}Module{number}",
        TYPE_KEY: [f"{FC_NS}{kind}"],
        LABEL_KEY: [f"Module {number}"],
        CONNECTION_KEY: [
            {
                CONNECTS_TO_KEY: [f"{FCI_NS}Module{target}"],
                DIRECTION_KEY: [f"{FC_NS}{direction}"],
                PORT_KEY: [0],
            }
            for direction, target in links
        ],
    }


def _block(module: dict) -> str:
    """One module as the shipped files write it: the connections each on a few lines, everything
    else on one line each."""
    lines = [
        "  {",
        f'    "id": {json.dumps(module["id"])},',
        f"    {json.dumps(TYPE_KEY)}: {json.dumps(module[TYPE_KEY])},",
        f"    {json.dumps(LABEL_KEY)}: {json.dumps(module[LABEL_KEY])},",
    ]
    connections = module[CONNECTION_KEY]
    if not connections:
        lines.append(f"    {json.dumps(CONNECTION_KEY)}: []")
    else:
        lines.append(f"    {json.dumps(CONNECTION_KEY)}: [")
        for at, link in enumerate(connections):
            lines.append("      {")
            for key in (CONNECTS_TO_KEY, DIRECTION_KEY, PORT_KEY):
                comma = "," if key != PORT_KEY else ""
                lines.append(
                    f"        {json.dumps(key)}: {json.dumps(link[key])}{comma}"
                )
            lines.append("      }" + ("," if at < len(connections) - 1 else ""))
        lines.append("    ]")
    lines.append("  }")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
