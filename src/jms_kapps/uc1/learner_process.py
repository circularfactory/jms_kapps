"""The learner of use case 1 as its own process, behind its own middleware instance.

The process registers `:LearnerService` for the learner's resource, `:Learner1`, and serves its
middleware over REST. So the learner is a participant like the others: the graph names its service,
the service has a heartbeat, and it deregisters on the way out. It offers no workflow, and no
notification starts it. Every `--learn-seconds` it makes one run of the learner (`learner.Learner`),
in a worker thread, so the server and the heartbeat go on while it reads and commits. A run that
fails is logged, and the next one comes when it is due. The first run comes one interval after the
start.

A notification that reaches the learner all the same is logged, and nothing is done with it: the
learner takes no Operation. The handler also keeps the middleware from warning at each start that
the instance has neither a notification handler nor a callback.

The process serves the middleware's state dashboard at `/dashboard`: its boxes of the
instance in the tab "System", and the learner's own box in the tab "Learner" (`learner_dashboard`).
That box shows the last run and when the next comes, how many attempts the learner learned from,
each value it changed, and the last thing that went wrong, all from the learner's memory.

The curves are read from the InfluxDB that `INFLUXDB_URL` names, and only without it from the local
service, the fallback. Changed on 2026-10-05, after Etienne Hoffmann decided that the local stack
is only the fallback (the ticket on jms_kapps' demos on the configured live store,
kapps_semantic_middleware 422); this sentence ended "names, by default the local service."

Run it as `python -m jms_kapps.uc1.learner_process --dataset <name> --learn-seconds <s>`. It
starts and serves as every participant's process does, through `common.process`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from kapps_ogm import OGM, Node
from kapps_semantic_middleware import Mode, SemanticMiddleware
from kapps_semantic_middleware.dashboard import enable_dashboard
from kapps_triplestore_interface import IRI

from jms_kapps.common.process import process_parser, run_process
from jms_kapps.common.triplestore import connect
from jms_kapps.uc1.learner import Learner
from jms_kapps.uc1.learner_dashboard import register_box
from jms_kapps.uc1.learner_memory import LearnerMemory
from jms_kapps.uc1.timeseries import FrameReader, InfluxDB
from jms_kapps.uc1.timeseries import connect as connect_influxdb
from jms_kapps.uc1.vocabulary import JMS

# Named, not `__name__`: the process runs this module as `python -m`, where `__name__` is
# `__main__`. A logger of that name is not below `jms_kapps`, so its INFO lines would be
# dropped at the root's WARNING (`common.process` lets `jms_kapps` log from INFO up).
logger = logging.getLogger("jms_kapps.uc1.learner_process")

# How often the learner runs, unless the command line sets it.
DEFAULT_LEARN_SECONDS = 10.0


async def learn_every(
    seconds: float, learn: Callable[[], object], memory: LearnerMemory
) -> None:
    """Make one run of `learn` every `seconds`, each in a worker thread, until cancelled.

    A run that raises is logged, and the next run comes when it is due. Before each wait, it tells
    `memory` when the next run comes.
    """
    while True:
        memory.next_run_in(seconds)
        await asyncio.sleep(seconds)
        try:
            await asyncio.to_thread(learn)
        except Exception:
            logger.exception("A run of the learner failed. The next run tries again.")


def run_periodically(
    middleware: SemanticMiddleware,
    learn: Callable[[], object],
    seconds: float,
    memory: LearnerMemory,
) -> None:
    """Run `learn` every `seconds` while `middleware` serves: from its start to its shutdown.
    `memory` is told when each next run comes."""
    task: asyncio.Task[None] | None = None

    async def start() -> None:
        nonlocal task
        task = asyncio.create_task(learn_every(seconds, learn, memory))

    async def stop() -> None:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    middleware.add_callback("on_start_up", start)
    middleware.add_callback("on_shutdown", stop)


def build(
    ogm: OGM,
    host: str,
    port: int,
    *,
    influxdb: InfluxDB,
    learn_seconds: float = DEFAULT_LEARN_SECONDS,
) -> SemanticMiddleware:
    """The learner's middleware instance, with its runs scheduled, its notification handler and
    its dashboard.

    Args:
        influxdb: The time-series store that the learner reads the curves from.
        learn_seconds: How often the learner runs.
    """
    middleware = SemanticMiddleware(
        mode=Mode.RESOURCE,
        resource_iri=str(JMS.Learner1),
        service_class=str(JMS.LearnerService),
        ogm=ogm,
        host=host,
        port=port,
    )
    memory = LearnerMemory()
    learner = Learner(middleware, FrameReader(influxdb), memory)

    def not_taken(operation: Node) -> None:
        logger.warning(
            "The learner takes no Operation, and does nothing with %s",
            IRI(str(operation.id)).fragment,
        )

    middleware.on_notification(not_taken)
    register_box(enable_dashboard(middleware), memory)
    run_periodically(middleware, learner.learn, learn_seconds, memory)
    return middleware


def main() -> None:
    parser = process_parser("The learner of use case 1")
    parser.add_argument(
        "--learn-seconds",
        type=float,
        default=DEFAULT_LEARN_SECONDS,
        help=f"Seconds from one run of the learner to the next (default: {DEFAULT_LEARN_SECONDS:g})",
    )
    args = parser.parse_args()
    if not 0 < args.learn_seconds < float("inf"):
        parser.error("--learn-seconds must be a positive number")
    with connect_influxdb() as influxdb:
        run_process(
            args.host,
            lambda port: build(
                OGM(db=connect(args.dataset)),
                args.host,
                port,
                influxdb=influxdb,
                learn_seconds=args.learn_seconds,
            ),
            lambda address: (
                f"The learner runs on {address}, every {args.learn_seconds:g} s. "
                f"Its dashboard: {address}dashboard"
            ),
        )


if __name__ == "__main__":
    main()
