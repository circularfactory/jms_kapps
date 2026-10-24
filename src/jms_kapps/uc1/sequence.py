"""The UC1 sequence, as the page of a run serves it at `/sequence`.

The text is the reference page "The UC1 sequence" (`docs/reference/uc1-sequence.md`), kept as
`sequence.md` next to this module so that an installed copy carries it. The two files are
identical, and a test holds them to it. The page is written in the small part of Markdown that
`render_markdown` reads: headings, paragraphs, flat bullet lists, fenced code, and inline code,
bold and absolute links. It has no relative link, since one would break at this route.

The route adds no runtime dependency: the renderer is here, and the page needs no JavaScript.
"""

from __future__ import annotations

import html
import re
from importlib.resources import files

# The inline forms, applied to text that is already escaped. Code goes first, and its text is not
# read again for bold or links.
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")


def sequence_markdown() -> str:
    """The text of the page, from the file that ships with the package."""
    return files("jms_kapps.uc1").joinpath("sequence.md").read_text(encoding="utf-8")


def _inline(text: str) -> str:
    """One run of text as HTML: every character escaped, then code, bold and links marked."""
    parts = _CODE.split(html.escape(text, quote=False))
    out: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            out.append(f"<code>{part}</code>")
            continue
        part = _BOLD.sub(r"<strong>\1</strong>", part)
        part = _LINK.sub(r'<a href="\2" rel="noopener">\1</a>', part)
        out.append(part)
    return "".join(out)


def render_markdown(text: str) -> str:
    """The body HTML of `text`, in the part of Markdown the page is written in. Every character
    of the text is escaped."""
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    code: list[str] | None = None

    def end_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def end_list() -> None:
        if items:
            blocks.append(
                "<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>"
            )
            items.clear()

    for line in text.splitlines():
        if code is not None:
            if line.startswith("```"):
                body = "".join(f"{html.escape(c, quote=False)}\n" for c in code)
                blocks.append(f"<pre><code>{body}</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        if line.startswith("```"):
            end_paragraph()
            end_list()
            code = []
        elif heading := re.match(r"(#{1,3}) (.+)$", line):
            end_paragraph()
            end_list()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.startswith("- "):
            end_paragraph()
            items.append(line[2:])
        elif line.startswith("  ") and items:
            items[-1] += " " + line.strip()
        elif not line.strip():
            end_paragraph()
            end_list()
        else:
            end_list()
            paragraph.append(line.strip())
    end_paragraph()
    end_list()
    return "\n".join(blocks)


STYLE = """
:root { --ink: #1c1c1c; --muted: #6b6b6b; --line: #d5d5d2; --paper: #fff; --ground: #f7f7f5;
  --link: #0b5cad; }
* { box-sizing: border-box; }
body { font: 15px/1.55 system-ui, sans-serif; color: var(--ink); background: var(--ground);
  margin: 0; padding: 16px; }
article { max-width: 54rem; margin: 0 auto; background: var(--paper); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 24px 20px; }
nav { max-width: 54rem; margin: 0 auto 8px; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin-top: 28px; } h3 { font-size: 15px; }
code { font: 13px ui-monospace, monospace; background: #f0f0ee; padding: 0 3px; border-radius: 3px; }
pre { background: #f0f0ee; padding: 10px 12px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
a { color: var(--link); }
"""


def sequence_html() -> str:
    """The complete page that `/sequence` serves: the text, rendered, under a link back to the
    page of the run. It does not reload itself, so that a reader keeps their place."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The UC1 sequence</title>
<style>{STYLE}</style>
</head>
<body>
<nav><a href="./">&larr; Use case 1</a></nav>
<article>
{render_markdown(sequence_markdown())}
</article>
</body>
</html>"""
