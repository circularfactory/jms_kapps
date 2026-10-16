"""The Launcher's query for the boxes on the station, on a seeded dataset (jms_kapps 23).

The dataset is the one `--dataset` names, which the test clears and seeds: the cross, Box 1
on Module 1 and Box 2 on Module 3, each with its transport running, and Box 3 back in the
WarehouseManagementSystem's possession, its transport done. The query, sent as the Launcher sends
it, gives exactly the two boxes on modules, each with the module that possesses it.
"""

from __future__ import annotations

import pytest
from kapps_ogm import OGM
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.vocabulary import SVC, OperationStatus
from rdflib import Literal
from rdflib.namespace import RDF

from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.launcher.boxes import BoxOnStation, StationBoxes
from jms_kapps.uc2.seed import place_box, possession_triples, seed_topology
from jms_kapps.uc2.topology import load_topology
from jms_kapps.uc2.vocabulary import FC, WMS, box_iri, module_iri, transport_iri

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]


@pytest.fixture
def db(dataset):
    return connect(dataset)


def test_on_a_seeded_dataset_the_query_gives_exactly_the_boxes_on_modules(db):
    seed_topology(db, load_topology("cross"), transit_seconds=0)
    m1, m2, m3, m4 = (module_iri(n) for n in (1, 2, 3, 4))
    place_box(OGM(db=db), box_iri(1), on=m1, origin=m1, destination=m4)
    place_box(OGM(db=db), box_iri(2), on=m3, origin=m2, destination=m4)
    done = transport_iri(box_iri(3))
    assert db.triples_add(
        [
            *possession_triples(db, box_iri(3), WMS),
            (box_iri(3), RDF.type, FC.Box),
            (box_iri(3), FC.hasTransport, done),
            (done, RDF.type, FC.BoxTransportOperation),
            (done, FC.hasBox, box_iri(3)),
            (done, FC.hasOrigin, m1),
            (done, FC.hasDestination, m4),
            (done, SVC.operationStatus, Literal(OperationStatus.DONE)),
        ]
    )
    boxes = StationBoxes(db)

    boxes.read()

    found = boxes.last()
    assert found.error is None
    assert found.boxes == [
        BoxOnStation(
            box="Box1",
            module="Module1",
            transport="Box1_transport",
            status=OperationStatus.RUNNING,
        ),
        BoxOnStation(
            box="Box2",
            module="Module3",
            transport="Box2_transport",
            status=OperationStatus.RUNNING,
        ),
    ]
