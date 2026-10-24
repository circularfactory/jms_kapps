"""A stand-in of a participant of use case 2, for the observation's tests: a script that the wrapper
runs as it runs a module's or the WarehouseManagementSystem's process, and that talks to a store as
a participant does, through the triple-store interface and the object-graph mapper, unchanged.

    python _fake_participant.py <store url> <dataset> <step> [<step> ...]

The store is the stand-in of `_fake_store.py`, with the data of `ONTOLOGY`. Each step is one thing
a participant does, in the given order:

- `running`, `done`, `failed`: commit that status for `Box1_transport` through the mapper, as a
  module does (`transport.Transports._set`): its class query, its class-spec derivation, its
  fetch, its diff and its update. A commit that the store refuses is caught, as a module catches
  a refusal, and the script goes on.
- `heap`: a query with a DISTINCT, which the store may refuse for want of heap.
- `late`: a query that the store may answer only after the client gave up waiting.
- `alarm`: one deadlock alarm line, as a module's log writes it.
- `served`: serve one call of a workflow on a FastAPI app that the framework of the middleware
  builds, as a participant's own app is built, on the loopback, and make the call with httpx, as
  a participant calls another one.

The client waits at most `CLIENT_TIMEOUT_SECONDS` for an answer.
"""

from __future__ import annotations

import logging
import sys

from kapps_ogm import OGM
from kapps_semantic_middleware.vocabulary import SVC_NS
from kapps_triplestore_interface.auth import GDBTokenAuth
from kapps_triplestore_interface.exceptions import TripleStoreInterfaceError
from kapps_triplestore_interface.graph_db import GraphDB, _probe
from kapps_triplestore_interface.store import TripleStoreEndpoint

FC_NS = "http://w3id.org/circularfactory/FlexConveyor#"
FCI_NS = "http://w3id.org/circularfactory/FlexConveyorInstances#"
OPERATION = f"{FCI_NS}Box1_transport"
BOX = f"{FCI_NS}Box1"

# The data of the store: a class of an Operation with its status, as use case 2's ontology and the
# middleware's give them, and one transport of one box, queued.
ONTOLOGY = f"""\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix svc: <{SVC_NS}> .
@prefix fc: <{FC_NS}> .
@prefix fci: <{FCI_NS}> .

fc:BoxTransportOperation a owl:Class .
svc:hasOperationStatus a owl:DatatypeProperty , owl:FunctionalProperty ;
    rdfs:domain fc:BoxTransportOperation ;
    rdfs:range xsd:string .
fc:hasBox a owl:ObjectProperty .
fci:Box1_transport a fc:BoxTransportOperation ;
    svc:hasOperationStatus "queued" ;
    fc:hasBox fci:Box1 .
"""

CLIENT_TIMEOUT_SECONDS = 1

ALARM = "possible deadlock: Module3 has waited 300 s to hand Box1 on, asking Module4 and Module5"


def served() -> None:
    """One call of a workflow on an app of the middleware's framework, served by uvicorn on the
    loopback, as a participant serves its own, and made with httpx."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    from transitional_sync_middleware.middleware import middleware as framework

    app = framework.FastAPI()

    @app.post("/workflows/reserve/execute")
    def reserve() -> dict[str, bool]:
        return {"accepted": True}

    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    port = listening.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listening]})
    thread.start()
    try:
        while not server.started:
            time.sleep(0.01)
        answer = httpx.post(f"http://127.0.0.1:{port}/workflows/reserve/execute")
        assert answer.json() == {"accepted": True}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main(argv: list[str]) -> int:
    url, dataset, *steps = argv
    logging.basicConfig(level=logging.WARNING)
    store = GraphDB(
        endpoint=TripleStoreEndpoint(url=url, dataset=dataset),
        auth=GDBTokenAuth("participant", "participant-secret-k3"),
        timeout=CLIENT_TIMEOUT_SECONDS,
    )
    _probe(store)
    ogm = OGM(db=store)
    for step in steps:
        try:
            if step in ("running", "done", "failed"):
                ogm.commit(
                    instance_iri=OPERATION,
                    data={f"{SVC_NS}hasOperationStatus": [step]},
                )
            elif step == "heap":
                store.query(
                    f"SELECT DISTINCT ?status WHERE {{ <{OPERATION}> ?p ?status }}"
                )
            elif step == "late":
                store.query("ASK { ?late ?answer ?here }")
            elif step == "alarm":
                logging.getLogger("jms_kapps.uc2.module").warning("%s", ALARM)
            elif step == "served":
                served()
            else:
                raise SystemExit(f"no step {step!r}")
        except TripleStoreInterfaceError as error:
            print(f"refused or unanswered: {type(error).__name__}", flush=True)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
