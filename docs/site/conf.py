"""Sphinx configuration for the jms-kapps documentation site.

Copied from ``kapps_triplestore_interface``'s ``docs/site/conf.py``, as the release mechanism's
adoption steps say: the same toolchain, theme and version switcher, with the ``project`` block and
the icon links changed: the GitHub link only, since jms_kapps is not published on PyPI.

These things differ from that file. The site publishes the README, the pages under ``docs/`` and
the glossary, and no generated module pages:

- **The pages are the repository's Markdown files.** ``index.md`` and, for each page under ``docs/``, a
  file of the same name here (``setup.md``, ...) are stubs, and ``setup()`` below hands Sphinx ``README.md`` and
  ``docs/<page>.md`` in their place while it reads them. The files are the artefact: a page and its
  file cannot drift, and an edit of the file is a published-page edit. A link between two of these
  files becomes a link between their pages; an image or a video is loaded from the public
  repository, and a link to any other file points at that file in the public repository, at this
  release's tag, because a page on the documentation site has no repository root beside it.
- **No API reference.** The paper's reader runs the use cases and reads the pages; the code is read
  in the repository, which the pages point into.
- **Heading anchors** (``myst_heading_anchors``), for the pages' links to their own sections and to
  each other's.

The footer's copyright text is read from the ``LICENSE`` two directories above this file, so a
vendored copy needs a ``Copyright (c) <year> <holder>`` line there, or the build raises.
"""

import posixpath
import re
from importlib.metadata import version as _installed_version
from pathlib import Path

# -- Project ----------------------------------------------------------------

project = "jms-kapps"
author = "Etienne Hoffmann, Sören Weindel"


def _copyright_from_license(license_path: Path) -> str:
    """The footer's copyright text, read from the copyright lines of the LICENSE.

    ``Copyright (c) <year> <holder>`` becomes ``<year>, <holder>``, the form Sphinx
    prints. The LICENSE is the only place the holder and year are written; several lines join
    with ``; ``.
    """
    entries = re.findall(
        r"^Copyright(?: \(c\))? (\S+) (.+?)\s*$",
        license_path.read_text(encoding="utf-8"),
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if not entries:
        raise ValueError(
            f"{license_path} has no 'Copyright (c) <year> <holder>' line, so the docs footer "
            "cannot be derived from it."
        )
    return "; ".join(f"{year}, {holder}" for year, holder in entries)


copyright = _copyright_from_license(  # Sphinx requires this name
    Path(__file__).parent.parent.parent / "LICENSE"
)

release = _installed_version("jms-kapps")
version = ".".join(release.split(".")[:2])

# The public repository, which the README's relative links resolve against.
_REPOSITORY = "circularfactory/jms_kapps"

# -- Extensions -------------------------------------------------------------

extensions = [
    # myst_nb registers myst_parser itself. Loading BOTH raises
    # "extension myst_parser is already registered" -- so only this one.
    "myst_nb",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "**.ipynb_checkpoints"]
# A fence of the reference names a language that Pygments has no lexer for, `nquads`. It renders as
# plain text, as on GitHub, and is no reason to fail the build.
suppress_warnings = ["misc.highlighting_failure"]

# -- MyST -------------------------------------------------------------------

myst_enable_extensions = ["colon_fence", "deflist", "fieldlist"]
# The pages link their own sections and each other's by their GitHub anchors (`#the-triple-store`).
# The libraries' READMEs do not, so their sites need no anchors; this one does.
myst_heading_anchors = 3

# -- HTML -------------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_title = project

html_theme_options = {
    "logo": {
        # The SFB1574 mark, as on the three libraries' sites: one asset for both themes, given a
        # white chip in dark mode by custom.css.
        "image_light": "_static/sfb-logo.png",
        "image_dark": "_static/sfb-logo.png",
        "alt_text": "SFB 1574 Circular Factory",
        "text": project,
    },
    "navbar_end": ["theme-switcher", "version-switcher", "navbar-icon-links"],
    "switcher": {
        "json_url": "https://circularfactory.github.io/jms_kapps/latest/_static/switcher.json",
        "version_match": release,
    },
    # The switcher JSON is served by the site being built. Checking it at build
    # time fails the very first publication and every local build; the docs job
    # writes it beside `latest/` from the versions the site holds.
    "check_switcher": False,
    "show_version_warning_banner": True,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/circularfactory/jms_kapps",
            "icon": "fa-brands fa-github",
        },
    ],
}

# -- The pages are the repository's Markdown files -------------------------------

_ROOT = Path(__file__).resolve().parent.parent.parent

# A Markdown link or image with an inline target: `[text](target)` or `![alt](target)`. Its text may
# wrap onto the next line, as the guide's do, but not across a blank line.
_INLINE_TARGET = re.compile(r"(!?\[(?:[^\]\n]|\n(?!\n))*\]\()([^)\s]+)(\))")
# A fenced block, whose text is code and is left as it is.
_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.MULTILINE | re.DOTALL)
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
# What a page shows rather than links to: loaded from the public repository's raw files.
_MEDIA = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".mp4")


# Each published page by its file in the repository: the site's name of the page, in the order of
# the navigation.
PAGES: dict[str, str] = {
    "README.md": "index",
    "docs/setup.md": "setup",
    "docs/use-case-1.md": "use-case-1",
    "docs/use-case-2.md": "use-case-2",
    "docs/recorded-refusal.md": "recorded-refusal",
    "docs/glossary.md": "glossary",
}

# The README has no toctree of its own, so the pages are attached here, under the names the README's
# links give them. Hidden: the navigation bar lists them, and the README reads as it does on GitHub.
_SECTIONS = (
    ("Setup and operation", ("setup",)),
    ("Use case 1", ("use-case-1",)),
    ("Use case 2", ("use-case-2", "recorded-refusal")),
    ("Glossary", ("glossary",)),
)
_TOCTREE = "".join(
    f"\n```{{toctree}}\n:hidden:\n:caption: {caption}\n:maxdepth: 1\n\n"
    + "\n".join(pages)
    + "\n```\n"
    for caption, pages in _SECTIONS
)


def site_target(source: str, docname: str, target: str) -> str:
    """Where `target`, a link written in the repository file `source`, points from the page
    `docname`: the page of another published file, the raw file of an image or a video, or else
    the file in the public repository."""
    if _URL_SCHEME.match(target) or target.startswith(("#", "/")):
        return target
    path, hash_, anchor = target.partition("#")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    if resolved in PAGES:
        here = posixpath.dirname(docname) or "."
        return posixpath.relpath(PAGES[resolved] + ".md", here) + hash_ + anchor
    if resolved.lower().endswith(_MEDIA):
        return f"https://raw.githubusercontent.com/{_REPOSITORY}/v{release}/{resolved}"
    return f"https://github.com/{_REPOSITORY}/blob/v{release}/{resolved}{hash_}{anchor}"


def page_text(source: str, docname: str) -> str:
    """The text of the page `docname`: the repository file `source` with its links pointed at the
    site or the public repository, and on the landing page the toctrees."""

    def pointed(match: re.Match[str]) -> str:
        opening, target, closing = match.groups()
        return opening + site_target(source, docname, target) + closing

    raw = (_ROOT / source).read_text(encoding="utf-8")
    parts, start = [], 0
    for fence in _FENCE.finditer(raw):
        parts.append(_INLINE_TARGET.sub(pointed, raw[start : fence.start()]))
        parts.append(fence.group(0))
        start = fence.end()
    parts.append(_INLINE_TARGET.sub(pointed, raw[start:]))
    text = "".join(parts)
    return text + _TOCTREE if docname == "index" else text


_SOURCES = {page: source for source, page in PAGES.items()}


def _repository_file_as_page(app, docname, source):
    if docname in _SOURCES:
        source[0] = page_text(_SOURCES[docname], docname)


def setup(app):
    app.connect("source-read", _repository_file_as_page)
