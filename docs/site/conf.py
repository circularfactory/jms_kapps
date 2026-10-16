"""Sphinx configuration for the jms-kapps documentation site.

Copied from ``kapps_triplestore_interface``'s ``docs/site/conf.py``, as the release mechanism's
adoption steps say: the same toolchain, theme and version switcher, with the ``project`` block and
the icon links changed: the GitHub link only, since jms_kapps is not published on PyPI.

These things differ from that file. The first two follow from this repository having no
hand-written pages; the last two keep its pages building with no warning:

- **The landing page is the README.** ``index.md`` is a stub, and ``setup()`` below hands
  Sphinx ``README.md`` in its place while it reads that page. The page is the artefact: the two
  cannot drift, and a README edit is a published-page edit. A link the README writes relative
  to the repository root is pointed at the same file in the public repository, at this
  release's tag, because a page on the documentation site has no repository root beside it.
- **The reference lists undocumented members too** (``undoc-members``). Not every public class
  and function here carries a docstring, and a reference that silently left them out would
  read as the whole API while showing part of it. They appear with their signatures. A
  deprecated name is left out: the reference shows the name that replaces it.
- **Heading anchors** (``myst_heading_anchors``), for the README's links to its own sections.
- **Instance-variable fields** (``napoleon_use_ivar``), for the ``Attributes:`` sections.
- **The evaluation extra mocked** (``autodoc_mock_imports``): the build installs the package without
  its optional ``evaluation`` extra, so the run report's modules document against stand-ins for
  OpenTelemetry, pandas and matplotlib.

The footer's copyright text is read from the ``LICENSE`` two directories above this file, so a
vendored copy needs a ``Copyright (c) <year> <holder>`` line there, or the build raises.
"""

import logging
import re
from importlib.metadata import version as _installed_version
from pathlib import Path

# -- Project ----------------------------------------------------------------

project = "jms-kapps"
author = "Etienne Hoffmann, Sören Weindel"


def _copyright_from_license(license_path: Path) -> str:
    """The footer's copyright text, read from the copyright lines of the LICENSE.

    ``Copyright (c) 2026 Etienne Hoffmann`` becomes ``2026, Etienne Hoffmann``, the form Sphinx
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
    "sphinx.ext.autodoc",
    # Google-style `Args:` sections render as a parameter table, not a literal block.
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    # myst_nb registers myst_parser itself. Loading BOTH raises
    # "extension myst_parser is already registered" -- so only this one.
    "myst_nb",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "**.ipynb_checkpoints"]

# -- autodoc ----------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    # Source order, not alphabetical: a module read top to bottom stays in that order.
    "member-order": "bysource",
}
# Types stay in the signature, so a reader sees the shape of a call without
# scrolling down to the parameter table.
autodoc_typehints = "signature"
# The docs build installs the package without its optional `evaluation` extra; the modules of the
# measurement harness that import it are documented against stand-ins instead of failing to import.
autodoc_mock_imports = ["opentelemetry", "pandas", "matplotlib"]

# -- MyST -------------------------------------------------------------------

myst_enable_extensions = ["colon_fence", "deflist", "fieldlist"]
# The README links its own sections by their GitHub anchors (`#the-triple-store`). The libraries'
# READMEs do not, so their sites need no anchors; this one does.
myst_heading_anchors = 3

# -- napoleon -----------------------------------------------------------------

# A class's `Attributes:` section renders as instance-variable fields. Rendered as attribute
# entries, each would describe the class's annotated attribute a second time, since
# `undoc-members` lists those too, and the duplicate fails the build.
napoleon_use_ivar = True

# -- intersphinx ------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

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

# -- The landing page is the README ------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent.parent

# A Markdown link or image with an inline target: `[text](target)` or `![alt](target)`.
_INLINE_TARGET = re.compile(r"(!?\[[^\]\n]*\]\()([^)\s]+)(\))")
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")

# The README has no toctree of its own, so the reference is attached here. Hidden:
# the navigation bar lists it, and the README reads as it does on GitHub.
_TOCTREE = """

```{toctree}
:hidden:
:maxdepth: 2

reference/index
```
"""


def _public_target(match: re.Match[str]) -> str:
    opening, target, closing = match.groups()
    if _URL_SCHEME.match(target) or target.startswith(("#", "/")):
        return match.group(0)
    if opening.startswith("!"):
        url = f"https://raw.githubusercontent.com/{_REPOSITORY}/v{release}/{target}"
    else:
        url = f"https://github.com/{_REPOSITORY}/blob/v{release}/{target}"
    return f"{opening}{url}{closing}"


def _landing_page() -> str:
    lines, fenced = [], False
    for line in (_ROOT / "README.md").read_text(encoding="utf-8").splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        lines.append(line if fenced else _INLINE_TARGET.sub(_public_target, line))
    return "".join(lines) + _TOCTREE


def _readme_as_landing_page(app, docname, source):
    if docname == "index":
        source[0] = _landing_page()


def _skip_loggers(app, what, name, obj, skip, options):
    # A module-level logger becomes a member once undocumented members are listed.
    # It is not API.
    if isinstance(obj, logging.Logger):
        return True
    return None


def _skip_deprecated(app, what, name, obj, skip, options):
    # A deprecated name carries `__deprecated__` (PEP 702), and a deprecated property carries it
    # on its getter. The reference shows only the names that replace them; the changelog's
    # Deprecated section lists the old names.
    marked = obj.fget if isinstance(obj, property) else obj
    if getattr(marked, "__deprecated__", None):
        return True
    return None


def setup(app):
    app.connect("source-read", _readme_as_landing_page)
    app.connect("autodoc-skip-member", _skip_loggers)
    app.connect("autodoc-skip-member", _skip_deprecated)
