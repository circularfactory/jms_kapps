"""What a test reads out of an HTML fragment of a dashboard box: its selects, the rows of its
tables, the line of the rate in force, the paths it asks for, and the tab a page opens on; and
whether such a path reaches its route through the Launcher too."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin


class _Selects(HTMLParser):
    """Each `<select>` of a fragment, by name, as the value and the text of each option."""

    def __init__(self) -> None:
        super().__init__()
        self.options: dict[str, list[tuple[str, str]]] = {}
        self._select: str | None = None
        self._value: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "select":
            self._select = attributes["name"]
            self.options[self._select] = []
        elif tag == "option" and self._select is not None:
            self._value = attributes.get("value", "")

    def handle_data(self, data):
        if self._select is not None and self._value is not None and data.strip():
            self.options[self._select].append((self._value, data.strip()))
            self._value = None

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


def selects(fragment: str) -> dict[str, list[tuple[str, str]]]:
    parser = _Selects()
    parser.feed(fragment)
    return parser.options


class _Rows(HTMLParser):
    """The rows of a fragment's tables, as the text of each cell, the header rows left out."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cells: list[str] | None = None
        self._text: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells = []
        elif tag == "td" and self._cells is not None:
            self._text = []

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._cells is not None and self._text is not None:
            self._cells.append(" ".join("".join(self._text).split()))
            self._text = None
        elif tag == "tr" and self._cells:
            self.rows.append(self._cells)
            self._cells = None


def rows(fragment: str) -> list[list[str]]:
    parser = _Rows()
    parser.feed(fragment)
    return parser.rows


class _InForce(HTMLParser):
    """The text of the element of class `rate-in-force`, and its htmx attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []
        self.attributes: dict[str, str | None] = {}
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self._depth:
            self._depth += 1
        elif "rate-in-force" in (attributes.get("class") or "").split():
            self._depth, self.attributes = 1, attributes

    def handle_data(self, data):
        if self._depth:
            self.text.append(data)

    def handle_endtag(self, tag):
        if self._depth:
            self._depth -= 1


def in_force(fragment: str) -> tuple[str, dict[str, str | None]]:
    parser = _InForce()
    parser.feed(fragment)
    return " ".join("".join(parser.text).split()), parser.attributes


class _Attributes(HTMLParser):
    """The values of one attribute, on every element of a fragment that has it."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.values += [value or "" for key, value in attrs if key == self.name]


def attribute_values(fragment: str, name: str) -> list[str]:
    """The value of the attribute `name` on each element of `fragment` that has it, in order."""
    parser = _Attributes(name)
    parser.feed(fragment)
    return parser.values


def reaches(page: str, path: str, route: str) -> bool:
    """Whether `path`, asked for by the page that a process serves at `page`, reaches its `route`
    both at the process's own address and through the Launcher, under `/p/<id>/`."""
    return all(
        urljoin(f"http://127.0.0.1:8090{at}{page}", path)
        == f"http://127.0.0.1:8090{at}{route}"
        for at in ("", "/p/Process1")
    )


# A path from the root of the server, in quotes: `"/api/state"`, `'/static/x.css'`.
_ABSOLUTE = re.compile(r"""["'`](/[A-Za-z_][^"'`\s]*)["'`]""")

# A comment of JavaScript or CSS to the end of its line, but not the `//` of a URL.
_LINE_COMMENT = re.compile(r"(?<![:\w])//.*$", re.MULTILINE)


def absolute_paths(text: str) -> list[str]:
    """Each path from the root of the server that a page's HTML, script or style names, outside
    its comments. Such a path misses a process that is reached under `/p/<id>/`."""
    code = re.sub(r"<!--.*?-->|/\*.*?\*/", "", text, flags=re.DOTALL)
    return _ABSOLUTE.findall(_LINE_COMMENT.sub("", code))


class _OpenedTabs(HTMLParser):
    """The ids of the checked radios of the dashboard's tab bar."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if (
            tag == "input"
            and attributes.get("name") == "tab"
            and "checked" in attributes
        ):
            self.ids.append(attributes.get("id") or "")


def opened_tabs(page: str) -> list[str]:
    """The id of each tab of the middleware's dashboard `page` that the page opens on, such as
    `tab-wms`: one, unless the page has no tab."""
    parser = _OpenedTabs()
    parser.feed(page)
    return parser.ids
