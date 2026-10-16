"""The Launcher page shows a wipe the middleware's guard refused, readable (jms_kapps 129).

On a store whose host is neither loopback nor a container alias, such as the KIT GraphDB, the
middleware's wipe guard refuses a seed's clear unless `TRIPLESTORE_ALLOW_DESTRUCTIVE` is `1`. The
Launcher adds no guard: it passes the refusal through its launch and clear errors, and the page
says what was refused and that the variable states the consent, in the page and not only in a
log. The page is the real one, served by the Launcher's real app, on the run core's real graph
over a fake store whose URL is the KIT host, kept where the guard reads it, `_endpoint.url`; the
processes only sleep, and nothing reaches a store.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Collection, Iterator
from types import SimpleNamespace

import pytest
from _services import live_services
from _serving import serving
from kapps_triplestore_interface import IRI
from playwright.sync_api import Dialog, Page, expect

from jms_kapps.common.services import Service
from jms_kapps.uc2.launcher.api import create_app
from jms_kapps.uc2.launcher.backend import Launcher, TripleStoreGraph

pytestmark = pytest.mark.browser

RUNS = [sys.executable, "-c", "import time; time.sleep(60)"]
KIT_URL = "https://store.example.org"


class KitStore:
    """A dataset on the KIT host. It keeps no statement and answers no row; the guard refuses
    before any wipe reaches it, and `wiped` records any that did."""

    dataset = "test-dataset"

    def __init__(self) -> None:
        self._endpoint = SimpleNamespace(url=KIT_URL)
        self.wiped = 0

    def graph_clear(self, graph_iri=None) -> bool:
        self.wiped += 1
        return True

    def query(self, query, **kwargs):
        return {"results": {"bindings": []}}


class Graph(TripleStoreGraph):
    """The Launcher's real graph, whose seed and clear are the real ones; it reads no service
    from the store, which has none."""

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        return live_services(set(), workflows)


@pytest.fixture
def store() -> KitStore:
    return KitStore()


@pytest.fixture
def url(store, monkeypatch) -> Iterator[str]:
    monkeypatch.delenv("TRIPLESTORE_ALLOW_DESTRUCTIVE", raising=False)
    monkeypatch.setenv("TRIPLESTORE_URL", KIT_URL)
    launcher = Launcher(
        graph=Graph(store),
        dataset="test-dataset",
        module_command=lambda resource: RUNS,
        wms_command=RUNS,
        monitor_command=RUNS,
        echo=lambda line: None,
        stop_timeout=5.0,
    )
    try:
        with serving(create_app(launcher)) as address:
            yield f"{address}/"
    finally:
        launcher.stop_all()


def accepting(page: Page) -> None:
    page.on("dialog", lambda dialog: Dialog.accept(dialog))


def shows_the_refusal(page: Page, prefix: str) -> None:
    message = page.locator("#command-message")
    expect(message).to_have_class(re.compile(r"\berror\b"))
    expect(message).to_contain_text(prefix)
    expect(message).to_contain_text("Refusing to clear the default graph")
    expect(message).to_contain_text("'test-dataset'")
    expect(message).to_contain_text(KIT_URL)
    expect(message).to_contain_text("TRIPLESTORE_ALLOW_DESTRUCTIVE=1")


def test_a_start_on_the_kit_host_without_the_variable_shows_the_refusal_in_the_page(
    page: Page, url, store
):
    page.goto(url)
    accepting(page)

    page.locator("#launch").click()

    shows_the_refusal(page, "Not launched: The launch failed:")
    assert store.wiped == 0


def test_a_clear_on_the_kit_host_without_the_variable_shows_the_refusal_in_the_page(
    page: Page, url, store
):
    page.goto(url)
    accepting(page)

    page.get_by_role("button", name="Clear", exact=True).click()

    shows_the_refusal(page, "Not cleared:")
    assert store.wiped == 0
