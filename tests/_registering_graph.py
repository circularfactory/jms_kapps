"""A stand-in of a run's graph whose participants are real child processes, each a few lines of
Python, for the tests of a caller of the run core without a store.

A process registers its service by creating a file named after its resource, then, a moment
later, its workflows, one line each, as a middleware writes its service and its first heartbeat
before its workflows; it then prints the lines the test gave it, as a participant logs, and it
deregisters on SIGTERM by removing the file.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, unquote

from _services import live_services
from kapps_triplestore_interface import IRI

from jms_kapps.common.services import Service
from jms_kapps.uc2.process import MODULE_WORKFLOWS
from jms_kapps.uc2.topology import Topology
from jms_kapps.uc2.vocabulary import WMS
from jms_kapps.uc2.wms_process import WMS_WORKFLOWS

# How long after its service a stand-in process registers its workflows, in seconds.
WORKFLOWS_LATER = 2.0


class RegisteringGraph:
    """A graph in which a process's service is live from its start until it is asked to stop, and
    has its workflows from `workflows_later` after its start. It records each topology it seeds,
    with the collection option it was seeded with.

    Args:
        registry: The folder of the processes' files.
        says: The lines each resource's process prints once its workflows are registered.
        workflows_later: How long after its service a process registers its workflows.
    """

    def __init__(
        self,
        registry: Path,
        *,
        says: Mapping[IRI, Sequence[str]] | None = None,
        workflows_later: float = WORKFLOWS_LATER,
    ) -> None:
        self.registry = registry
        self.says = dict(says or {})
        self.workflows_later = workflows_later
        self.seeded: list[Topology] = []
        self.collect_history: list[bool] = []

    def seed(
        self,
        topology: Topology,
        transit_seconds: float,
        *,
        collect_history: bool = False,
    ) -> None:
        self.seeded.append(topology)
        self.collect_history.append(collect_history)

    def command(self, resource: IRI) -> list[str]:
        """The process of `resource`, which registers the workflows its participant registers."""
        marker = self.registry / quote(str(resource), safe="")
        workflows = WMS_WORKFLOWS if resource == WMS else MODULE_WORKFLOWS
        lines = list(self.says.get(IRI(resource), ()))
        return [
            sys.executable,
            "-c",
            (
                "import pathlib, signal, sys, time\n"
                f"marker = pathlib.Path({str(marker)!r})\n"
                "def deregister(*_):\n"
                "    marker.unlink(missing_ok=True)\n"
                "    sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, deregister)\n"
                "marker.touch()\n"
                f"time.sleep({self.workflows_later})\n"
                f"marker.write_text({chr(10).join(str(w) for w in workflows)!r})\n"
                f"for line in {lines!r}:\n"
                "    print(line, flush=True)\n"
                "time.sleep(60)\n"
            ),
        ]

    def registered(self) -> dict[IRI, set[IRI]]:
        """Each resource whose process has registered its service, with its workflows so far."""
        return {
            IRI(unquote(marker.name)): {
                IRI(line) for line in marker.read_text().split()
            }
            for marker in self.registry.iterdir()
        }

    def service_of(self, resource: IRI, workflows: Collection[IRI]) -> list[Service]:
        return []

    def services(self, workflows: Collection[IRI]) -> list[Service]:
        found = []
        for resource, registered in self.registered().items():
            [service] = live_services([resource], set(workflows) & registered)
            found.append(service)
        return found
