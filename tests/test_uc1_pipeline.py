"""The time-series pipeline end to end, on a live InfluxDB and a live GraphDB.

A frame replayed from an example recording is written to InfluxDB. Its reference is stored on an unscrewing operation through
the middleware, as the screwing resource will store it: the operation is opened as the planner
opens it, a `cfc:Operation` under the middleware's operation scope, and the reference is one
`mw.commit()` on it. Then the reference is read back from the graph under a class scope, and the
curves through it.

The tests need both stores. They skip without the store's connection variables, and when no
InfluxDB answers at `INFLUXDB_URL` (without it, at the local service that
`docker compose up -d --wait` starts, the fallback). They clear the dataset that `--dataset`
names, and write their frames into the InfluxDB database `Tests`, whatever the dataset.
InfluxDB keeps those frames. Nothing in the cleared graph points at them.

The local stack is only the fallback.

All UC1 live tests share one InfluxDB database, `Tests` (`_uc1_influx.py`), since InfluxDB 3 Core
allows five databases only.
"""

from __future__ import annotations

import pytest
from _uc1_influx import INFLUX_TEST_DATABASE
from kapps_ogm import OGM, ClassScope, Update
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.credentials import triplestore_env_present
from kapps_semantic_middleware.operation import operation as operation_scope
from kapps_semantic_middleware.vocabulary import CFC, SVC, OperationStatus
from kapps_triplestore_interface import IRI
from rdflib import XSD

from jms_kapps.common.triplestore import bindings, reset
from jms_kapps.common.triplestore import connect as connect_store
from jms_kapps.uc1.replay import example_frame
from jms_kapps.uc1.seed import uc1_ontology
from jms_kapps.uc1.timeseries import FrameReader, FrameReference, FrameWriter
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS, JMS_NS

pytestmark = [
    pytest.mark.graphdb,
    pytest.mark.influxdb,
    pytest.mark.skipif(
        not triplestore_env_present(), reason="no triple store connection variables"
    ),
]

# The screwing resource, built and never served, because a commit needs only its object-graph
# mapper. Neither IRI is declared; use case 1's participants are not built yet.
SCREWING_RESOURCE = IRI("TestScrewingResource", base=JMS_NS)
SCREWING_SERVICE = IRI("TestScrewingService", base=JMS_NS)
# The Capability each operation implements: the screwing resource's.
UNSCREWING_CAPABILITY = IRI("TestUnscrewingCapability", base=JMS_NS)

# What a reader of an operation's curves reads of it.
SCOPE = ClassScope.from_property_chains(
    [[JMS.hasTimeSeriesReference], [CFC.implementsCapability]]
)

START = 1_758_800_000_123_456_789


@pytest.fixture(scope="module")
def influxdb():
    client = connect_influxdb()
    if not client.available():
        client.close()
        pytest.skip(
            "no InfluxDB answers; start the local one with `docker compose up -d --wait`"
        )
    yield client
    client.close()


@pytest.fixture(scope="module")
def mw(dataset) -> SemanticMiddleware:
    """The screwing resource's middleware, on use case 1's ontology and Core."""
    db = connect_store(dataset)
    reset(
        db,
        ontology=uc1_ontology(),
        shapes="",
    )
    return SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(SCREWING_RESOURCE),
        service_class=str(SCREWING_SERVICE),
        ogm=OGM(db=db),
    )


def store_on_operation(mw: SemanticMiddleware, reference: FrameReference) -> IRI:
    """Open an unscrewing operation, then commit `reference` on it, as the screwing resource
    does once its attempt is recorded."""
    operation = IRI(f"TestOperation_{reference.frame}", base=JMS_NS)
    with mw.create(
        str(JMS.UnscrewingOperation),
        operation_scope(ClassScope(), capability=str(UNSCREWING_CAPABILITY)),
        instance_iri=str(operation),
    ) as created:
        # The store's operation-status shape refuses an Operation created without a status.
        created.update({str(SVC.hasOperationStatus): [OperationStatus.RUNNING]})
    with mw.commit(
        entry=[
            Update(
                instance_iri=operation,
                data={str(JMS.hasTimeSeriesReference): [str(reference)]},
            )
        ]
    ):
        pass
    return operation


def reference_on(mw: SemanticMiddleware, operation: IRI) -> str:
    """The reference an operation holds, read back under a class scope."""
    read = mw.fetch(operation, class_scope=SCOPE)
    (reference,) = (read.data or {})[JMS.hasTimeSeriesReference]
    return str(reference)


def test_a_frame_is_written_its_reference_stored_on_an_operation_and_its_curves_read_back(
    influxdb, mw
):
    frame = example_frame("success", start=START)

    written = FrameWriter(influxdb, database=INFLUX_TEST_DATABASE).write(frame)
    operation = store_on_operation(mw, written)
    stored = reference_on(mw, operation)

    assert FrameReference.parse(stored) == written
    assert FrameReader(influxdb).read(stored) == frame


def test_the_operation_keeps_its_capability_beside_its_reference(influxdb, mw):
    written = FrameWriter(influxdb, database=INFLUX_TEST_DATABASE).write(
        example_frame("success")
    )
    operation = store_on_operation(mw, written)

    read = mw.fetch(operation, class_scope=SCOPE)
    assert read.class_spec.iri == JMS.UnscrewingOperation
    assert [str(c.id) for c in (read.data or {})[CFC.implementsCapability]] == [
        str(UNSCREWING_CAPABILITY)
    ]


def test_the_graph_holds_the_reference_as_a_string(influxdb, mw):
    written = FrameWriter(influxdb, database=INFLUX_TEST_DATABASE).write(
        example_frame("success")
    )
    operation = store_on_operation(mw, written)

    (row,) = bindings(
        mw.ogm.db,
        f"SELECT ?r WHERE {{ <{operation}> <{JMS.hasTimeSeriesReference}> ?r }}",
        convert=False,
    )
    assert row["r"]["value"] == str(written)
    assert row["r"].get("datatype", str(XSD.string)) == str(XSD.string)


def test_two_frames_at_the_same_time_are_read_back_apart(influxdb, mw):
    writer = FrameWriter(influxdb, database=INFLUX_TEST_DATABASE)
    success = example_frame("success", start=START)
    missing = example_frame("missing_screw", start=START)

    operations = {
        name: store_on_operation(mw, writer.write(frame))
        for name, frame in (("success", success), ("missing_screw", missing))
    }

    reader = FrameReader(influxdb)
    assert reader.read(reference_on(mw, operations["success"])) == success
    assert reader.read(reference_on(mw, operations["missing_screw"])) == missing
