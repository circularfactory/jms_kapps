"""A process's API docs by relative paths: they work at the process's own address, and through the
Launcher, under `/p/<id>/`.

FastAPI's own docs page loads the API's description from the root of the server, and sends each
request of its "Try it out" there too, which misses a process reached under a path. A browser
follows what the page and the description name; these tests resolve the same names the way the
browser and the docs' Swagger UI do.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from _html import reaches
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jms_kapps.common.api_docs import relative_api_docs

WORKFLOW = "/workflows/spawn_box/execute"


def documented() -> TestClient:
    app = FastAPI(title="A process")

    @app.post(WORKFLOW)
    def spawn_box() -> dict:
        return {}

    relative_api_docs(app)
    return TestClient(app)


def test_the_docs_load_the_api_s_description_next_to_themselves():
    client = documented()
    page = client.get("/docs")

    assert page.status_code == 200
    assert "A process" in page.text
    (description,) = re.findall(r"url: '([^']*)'", page.text)
    assert reaches("/docs", description, "/openapi.json"), description
    assert client.get("/openapi.json").status_code == 200


def test_the_docs_send_each_request_to_the_process_wherever_the_description_came_from():
    """Swagger UI resolves the description's first server against the address it loaded the
    description from, and puts each route's path behind it."""
    servers = documented().get("/openapi.json").json()["servers"]

    for at in ("", "/p/Process1"):
        loaded_from = f"http://127.0.0.1:8090{at}/openapi.json"
        base = urljoin(loaded_from, servers[0]["url"]).rstrip("/")
        assert base + WORKFLOW == f"http://127.0.0.1:8090{at}{WORKFLOW}"


def test_the_docs_still_list_every_route():
    paths = documented().get("/openapi.json").json()["paths"]

    assert list(paths) == [WORKFLOW]
