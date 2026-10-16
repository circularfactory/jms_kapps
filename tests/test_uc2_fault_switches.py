"""A module's two fault switches, on the route its process serves: `POST /faults/{name}`.

The route is mounted onto a bare app over a module built on the fakes of `_station`, because
the module's own app needs a live store at startup. That the process mounts it is the live tests'
job to prove.
"""

from __future__ import annotations

import pytest
from _station import MiddlewareHandover, Moves, Network, Reads, Records, World
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jms_kapps.uc2.module import FlexConveyorModule
from jms_kapps.uc2.process import mount_fault_switches
from jms_kapps.uc2.vocabulary import module_iri

M1 = module_iri(1)


@pytest.fixture
def module() -> FlexConveyorModule:
    world = World()
    return FlexConveyorModule(
        M1,
        graph=Reads(world, M1),
        handover=MiddlewareHandover(world, M1),
        call=Network().call,
        records=Records(world),
        notify=lambda operation, resource: None,
        move=Moves(world).move,
    )


@pytest.fixture
def client(module) -> TestClient:
    app = FastAPI()
    mount_fault_switches(app, module)
    return TestClient(app)


def test_arming_one_fault_arms_that_fault_alone(client, module):
    response = client.post("/faults/skip-reserve", json={"armed": True})

    assert response.status_code == 200
    assert response.json() == {"armed": True}
    assert module.faults == {"skip-reserve": True, "complete-without-releasing": False}


def test_a_fault_disarmed_is_off_again(client, module):
    client.post("/faults/complete-without-releasing", json={"armed": True})

    response = client.post("/faults/complete-without-releasing", json={"armed": False})

    assert response.json() == {"armed": False}
    assert module.faults == {"skip-reserve": False, "complete-without-releasing": False}


def test_a_fault_the_module_does_not_have_is_not_found_and_changes_nothing(
    client, module
):
    response = client.post("/faults/drop-the-box", json={"armed": True})

    assert response.status_code == 404
    assert "skip-reserve, complete-without-releasing" in response.json()["detail"]
    assert module.faults == {"skip-reserve": False, "complete-without-releasing": False}


def test_the_switches_are_not_in_the_api_docs_that_the_launcher_page_links(client):
    """The Launcher writes the switches and holds what it commanded, so a person switches a fault
    there, not from a module's API docs."""
    paths = client.get("/openapi.json").json()["paths"]

    assert not [path for path in paths if path.startswith("/faults")]
