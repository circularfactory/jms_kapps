"""What a test reads out of a module's own dashboard boxes: what the drawing of the module shows,
and what the adjacency matrix shows."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


class _Drawn(HTMLParser):
    """What the drawing shows: each connection, as (direction, way, the module's name), and the
    name on the box it holds, if it draws one."""

    def __init__(self) -> None:
        super().__init__()
        self.connections: set[tuple[str, str, str]] = set()
        self.boxes: list[str] = []
        self._group: dict | None = None
        self._text: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "g" and ("connection" in classes or "held-box" in classes):
            self._group = attributes
        elif tag == "text" and self._group is not None:
            self._text = []

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "text" and self._text is not None and self._group is not None:
            text = "".join(self._text).strip()
            if "held-box" in self._group["class"].split():
                self.boxes.append(text)
            else:
                self.connections.add(
                    (self._group["data-direction"], self._group["data-way"], text)
                )
            self._text = None
        elif tag == "g":
            self._group = None


def drawn(fragment: str) -> _Drawn:
    parser = _Drawn()
    parser.feed(fragment)
    return parser


@dataclass
class Matrix:
    """What the adjacency matrix shows: the attributes of its drawing, the modules of its rows
    and of its columns in order, the text of each cell by (row, column), and the rows marked."""

    svg: dict[str, str | None] = field(default_factory=dict)
    rows: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    cells: dict[tuple[str, str], str] = field(default_factory=dict)
    marked: list[str] = field(default_factory=list)


class _Matrix(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.matrix = Matrix()
        self._cell: tuple[str, str] | None = None
        self._text: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "svg" and "adjacency-matrix" in classes:
            self.matrix.svg = attributes
        elif tag == "g" and "row" in classes:
            self.matrix.rows.append(attributes["data-module"])
            if "here" in classes:
                self.matrix.marked.append(attributes["data-module"])
        elif tag == "g" and "column" in classes:
            self.matrix.columns.append(attributes["data-module"])
        elif tag == "g" and "cell" in classes:
            self._cell = (attributes["data-from"], attributes["data-to"])
            self.matrix.cells[self._cell] = ""
        elif tag == "text" and self._cell is not None:
            self._text = []

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "text" and self._text is not None and self._cell is not None:
            self.matrix.cells[self._cell] = "".join(self._text).strip()
            self._text = None
        elif tag == "g":
            self._cell = None


def matrix_of(fragment: str) -> Matrix:
    parser = _Matrix()
    parser.feed(fragment)
    return parser.matrix
