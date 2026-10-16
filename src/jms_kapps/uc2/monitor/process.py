"""The monitor as its own process, behind its own middleware instance.

The middleware registers the monitor as a Resource with a plain Service and no workflows, and sends
its heartbeat, so the Launcher sees it live; that is all it writes. The monitor's background task
starts with the process, and its routes and page are grafted onto the middleware's app. Uvicorn
runs on the main thread, so that a signal reaches it and the middleware deregisters on the way
out.

Run it as `python -m jms_kapps.uc2.monitor.process`, the command line `monitor_command` builds. The
Launcher does.
"""

from __future__ import annotations

import sys

from kapps_ogm import OGM
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.vocabulary import SVC

from jms_kapps.common.triplestore import connect
from jms_kapps.uc2.monitor.api import mount_onto
from jms_kapps.uc2.monitor.memory import MonitorMemory
from jms_kapps.uc2.monitor.reads import GraphReads
from jms_kapps.uc2.process import process_parser, run_process
from jms_kapps.uc2.vocabulary import MONITOR


def monitor_command(dataset: str) -> list[str]:
    """The command line that runs the monitor against `dataset`."""
    return [
        sys.executable,
        "-m",
        "jms_kapps.uc2.monitor.process",
        "--dataset",
        dataset,
    ]


def build(dataset: str, host: str, port: int) -> SemanticMiddleware:
    """The monitor's middleware instance, with its routes and page grafted onto its app, and its
    background task started. The app keeps the task as `app.state.memory`."""
    db = connect(dataset)
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(MONITOR),
        # The use case's ontology names no class for a monitor's service.
        service_class=str(SVC.Service),
        ogm=OGM(db=db),
        host=host,
        port=port,
        activity_feed=True,
    )
    memory = MonitorMemory(GraphReads(db))
    mount_onto(middleware.app, memory=memory)
    middleware.app.state.memory = memory
    memory.start()
    return middleware


def main() -> None:
    args = process_parser("The monitor of use case 2").parse_args()
    run_process(
        args.host,
        lambda port: build(args.dataset, args.host, port),
        lambda address: f"The monitor runs on {address}",
    )


if __name__ == "__main__":
    main()
