"""The middleware's wipe guard at jms_kapps' shared triple-store module: its reset and its clear.

The middleware refuses to clear a dataset, or to reload its shared ontologies, on a store whose
host is neither loopback nor a container alias, unless `TRIPLESTORE_ALLOW_DESTRUCTIVE` is `1`
(the wipe guard). jms_kapps adds no guard of its own: the person's
environment carries the consent, as for the other store variables. These tests
drive `reset` and `clear_graph_by_graph` with a fake store that keeps its URL where the
interface's own store keeps it, `_endpoint.url`, because that is where the guard reads it. The
variable is set only through `monkeypatch`, here, and never by any code of jms_kapps.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jms_kapps.common.triplestore import ClearFailed, clear_graph_by_graph, reset

VARIABLE = "TRIPLESTORE_ALLOW_DESTRUCTIVE"
KIT_URL = "https://store.example.org"
LOOPBACK_URL = "http://localhost:7200"
ONTOLOGY = "@prefix ex: <http://example.org/> .\nex:a ex:b ex:c .\n"


class WipingStore:
    """A dataset as a reset and a clear see it. It keeps no statement, holds no graph, and
    records each graph it was told to clear, `None` being the default graph."""

    dataset = "test-dataset"

    def __init__(self, url: str) -> None:
        self._endpoint = SimpleNamespace(url=url)
        self.cleared: list[object] = []

    def graph_clear(self, graph_iri=None) -> bool:
        self.cleared.append(graph_iri)
        return True

    def graph_add(self, content, **kwargs) -> bool:
        return True

    def shapes_set(self, data, content_type) -> None:
        pass

    def query(self, query, **kwargs):
        if "COUNT" in query:  # the statements left
            return {"results": {"bindings": [{"statements": {"value": "0"}}]}}
        return {"results": {"bindings": []}}  # no named graph beyond the shared ones

    def prefixes_get(self) -> dict[str, str]:
        return {}

    def prefix_set(self, prefix: str, iri: str) -> None:
        pass

    def prefix_delete(self, prefix: str) -> None:
        pass


def without_consent(monkeypatch) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)


def reset_it(store: WipingStore) -> None:
    reset(store, ontology=ONTOLOGY, shapes=None)


def test_a_reset_on_the_kit_host_without_the_variable_is_refused_and_says_what_and_how(
    monkeypatch,
):
    without_consent(monkeypatch)
    store = WipingStore(KIT_URL)

    with pytest.raises(ValueError) as refused:
        reset_it(store)

    text = str(refused.value)
    assert "Refusing to clear the default graph" in text
    assert "'test-dataset'" in text
    assert KIT_URL in text
    assert f"set {VARIABLE}=1" in text
    assert store.cleared == []


def test_a_clear_on_the_kit_host_without_the_variable_is_refused_and_says_what_and_how(
    monkeypatch,
):
    without_consent(monkeypatch)
    store = WipingStore(KIT_URL)

    with pytest.raises(ClearFailed) as refused:
        clear_graph_by_graph(store)

    text = str(refused.value)
    assert "Refusing to clear the default graph" in text
    assert KIT_URL in text
    assert f"set {VARIABLE}=1" in text
    assert refused.value.nothing_cleared
    assert store.cleared == []


def test_a_reset_on_the_kit_host_with_the_variable_proceeds(monkeypatch):
    monkeypatch.setenv(VARIABLE, "1")
    store = WipingStore(KIT_URL)

    reset_it(store)

    assert store.cleared[0] is None  # the default graph first
    assert len(store.cleared) == 4  # then Core, svc: and mes:


def test_a_clear_on_the_kit_host_with_the_variable_proceeds(monkeypatch):
    monkeypatch.setenv(VARIABLE, "1")
    store = WipingStore(KIT_URL)

    left = clear_graph_by_graph(store)

    assert left == 0
    assert store.cleared[0] is None


@pytest.mark.parametrize("url", [LOOPBACK_URL, "http://127.0.0.1:7200"])
def test_a_reset_and_a_clear_on_a_loopback_host_proceed_without_the_variable(
    monkeypatch, url
):
    without_consent(monkeypatch)
    store = WipingStore(url)

    reset_it(store)
    assert clear_graph_by_graph(store) == 0

    assert store.cleared.count(None) == 2


def test_the_variable_must_be_exactly_one(monkeypatch):
    monkeypatch.setenv(VARIABLE, "true")
    store = WipingStore(KIT_URL)

    with pytest.raises(ValueError, match=f"set {VARIABLE}=1"):
        reset_it(store)
