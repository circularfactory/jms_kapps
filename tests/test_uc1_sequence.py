"""Offline tests of the UC1 sequence: the reference page, the copy that UC1's page serves, and the
route and the link that reach it.

No triple store, no network. The copy is a file below the package, so that an installed wheel
carries it; a test holds it to the page under `docs/reference/`, by reading both files.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from jms_kapps.uc1.page import build_app, render
from jms_kapps.uc1.sequence import render_markdown, sequence_html, sequence_markdown

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE = REPO_ROOT / "docs" / "reference" / "uc1-sequence.md"

STEPS = (
    "The design",
    "The decision to unscrew",
    "The attempt",
    "The classification",
    "The learning",
)
SEEDED = ("AG1", ":M4", "Screw1", "Screw2", "Grinder099", "Grinder100")


def test_the_copy_the_page_serves_is_the_reference_page() -> None:
    """The served copy is the reference page, byte for byte, so the two cannot drift."""
    assert sequence_markdown() == PAGE.read_text(encoding="utf-8")


def test_the_page_tells_the_five_steps_with_the_seeded_names() -> None:
    text = PAGE.read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
    for step in STEPS:
        assert any(heading.endswith(step) for heading in headings), step
    for name in SEEDED:
        assert name in text, name
    assert "ANOMALY: MissingScrew" in text


def test_the_page_ends_with_what_the_demo_simplifies() -> None:
    text = PAGE.read_text(encoding="utf-8").rstrip()
    assert (
        re.findall(r"^## (.+)$", text, re.MULTILINE)[-1] == "What the demo simplifies"
    )


def test_the_page_stands_alone_when_served() -> None:
    """A relative link would break at the page's route, so the page has none."""
    text = PAGE.read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)]+)\)", text)
    assert [link for link in links if not link.startswith("https://")] == []


def test_the_page_does_not_claim_the_one_place() -> None:
    text = PAGE.read_text(encoding="utf-8").lower()
    assert "the one place" not in text


def test_the_renderer_makes_headings_lists_code_and_escapes() -> None:
    html = render_markdown(
        "# Title\n\nA <b> & `code` and **bold**.\n\n- one\n- two\n\n```\nx < y\n```\n"
    )
    assert "<h1>Title</h1>" in html
    assert "A &lt;b&gt; &amp; <code>code</code> and <strong>bold</strong>." in html
    assert "<ul>" in html and "<li>one</li>" in html and "<li>two</li>" in html
    assert "<pre><code>x &lt; y\n</code></pre>" in html


def test_the_sequence_route_serves_the_page_without_reloading() -> None:
    client = TestClient(build_app(list))
    response = client.get("/sequence")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "refresh" not in response.text
    assert "<script" not in response.text
    for name in SEEDED:
        assert name in response.text
    assert response.text == sequence_html()


def test_the_page_of_a_run_links_the_sequence() -> None:
    assert 'href="sequence"' in render([])
