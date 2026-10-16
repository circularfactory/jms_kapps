"""The entry point of the `jms-uc1` console script.

`jms-uc1` runs use case 1 in one of two ways. `jms-uc1` guards against a live instance, seeds the
dataset, starts the four participants of use case 1 in their order, waits until each is ready,
prints the dashboards, serves its page that links to each participant's dashboard, runs until a
stop signal, and stops all of them in order. It clears the dataset it seeds, so it refuses to
start while another service is live there, unless `--force`. Every stop signal is one stop
(`one_stop`): a second one does not cut the stop short.

`jms-uc1 seed` plays the other way: it clears the dataset (guarded as the run is), seeds it once,
starts nothing, and prints the command line of each participant. The seed exists because a
distributed run seeds once, and each participant then seeds nothing and reads its part from the
graph; each can be started by hand from its printed command line, on any computer that reaches
the triple store.

UC1's InfluxDB is the local one unless `INFLUXDB_URL` names another. The page binds a fixed port,
so a second `jms-uc1` on the same machine is refused at the start, before anything is cleared.
`jms-uc1 seed` binds no page port and starts no process.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Sequence

from kapps_semantic_middleware.credentials import endpoint_for
from kapps_triplestore_interface import TripleStore

from jms_kapps.common.platforms import PLATFORM
from jms_kapps.common.process import bind_socket, one_stop
from jms_kapps.common.services import reader
from jms_kapps.common.supervision import (
    LaunchFailed,
    LiveInstanceError,
    NotReady,
    Supervisor,
    display_names,
    refuse_if_live,
)
from jms_kapps.common.triplestore import DEFAULT_DATASET, connect
from jms_kapps.uc1.learner_process import DEFAULT_LEARN_SECONDS
from jms_kapps.uc1.page import PAGE_HOST, PAGE_PORT, PageServer, build_app
from jms_kapps.uc1.planner import DEFAULT_TIMEOUT_SECONDS
from jms_kapps.uc1.run_core import Participant, participants, start_run
from jms_kapps.uc1.run_core import seed as run_core_seed


def _parser() -> argparse.ArgumentParser:
    """The command line of `jms-uc1`."""
    parser = argparse.ArgumentParser(
        prog="jms-uc1",
        description="Use case 1, the closed loop of unscrewing, detection and learning.",
    )
    parser.add_argument(
        "--dataset", default=DEFAULT_DATASET, help="The dataset of the triple store"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear the dataset even while another service is live there",
    )
    parser.add_argument(
        "--learn-seconds",
        type=float,
        default=DEFAULT_LEARN_SECONDS,
        help=f"Seconds from one run of the learner to the next (default: {DEFAULT_LEARN_SECONDS:g})",
    )
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Seconds a press of the planner waits for its unscrewing operation to end "
            f"(default: {DEFAULT_TIMEOUT_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--page-port",
        type=int,
        default=PAGE_PORT,
        help="The port of the page of the run",
    )
    commands = parser.add_subparsers(dest="command")
    seed_parser = commands.add_parser(
        "seed", help="Seed the dataset once and print each participant's command line"
    )
    # Also accepted after `seed`. SUPPRESS keeps a value given before it.
    seed_parser.add_argument("--dataset", default=argparse.SUPPRESS)
    seed_parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start use case 1 against the named dataset, which it clears, until a stop signal stops it,
    or seed it once and print each participant's command (`jms-uc1 seed`).

    Returns the exit code: 0 when the run ended on a stop signal or the seed was written, 1 when
    it could not start.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "seed":
        return _seed(args)
    if not args.learn_seconds > 0 or args.learn_seconds == float("inf"):
        parser.error("--learn-seconds must be positive and finite")
    if not args.operation_timeout > 0 or args.operation_timeout == float("inf"):
        parser.error("--operation-timeout must be positive and finite")

    # Its own records from INFO up, as every participant's process shows them: the seed prints
    # the class scope of each individual it writes.
    logging.basicConfig(level=logging.WARNING, force=True)
    logging.getLogger("jms_kapps").setLevel(logging.INFO)

    try:
        sock = bind_socket(PAGE_HOST, args.page_port)
    except OSError as error:
        print(
            f"jms-uc1: the page's port {args.page_port} is in use: {error}",
            file=sys.stderr,
        )
        return 1

    db = connect(args.dataset)
    try:
        try:
            refuse_if_live(reader(db), dataset=args.dataset, force=args.force)
        except LiveInstanceError as error:
            print(
                f"jms-uc1: {error} Or pass --force to clear it anyway.",
                file=sys.stderr,
            )
            return 1
        except LaunchFailed as error:
            print(f"jms-uc1: {error}", file=sys.stderr)
            return 1

        supervisor = Supervisor(reader(db))
        stopping = threading.Event()

        def begin(signum: int) -> None:
            supervisor.begin_stop()
            stopping.set()

        the_participants = participants(
            args.dataset,
            learn_seconds=args.learn_seconds,
            operation_timeout=args.operation_timeout,
        )
        # The page names each participant's middleware by the class of its service.
        services = {
            p.resource: p.service for p in the_participants if p.service is not None
        }
        page = PageServer(build_app(supervisor.snapshot, services), sock)
        page.start()
        print(
            f"The page of use case 1: http://{PAGE_HOST}:{args.page_port}/",
            flush=True,
        )
        with one_stop(begin):
            try:
                print(
                    f"Seeding {args.dataset} on {endpoint_for(args.dataset).url}",
                    flush=True,
                )
                start_run(lambda: run_core_seed(db), supervisor, the_participants)
                if not stopping.is_set():
                    supervisor.wait_until_ready()
                if not stopping.is_set():
                    _print_dashboards(db, the_participants)
                    print(
                        "Use case 1 runs. Press Unscrew on the planner's dashboard. Ctrl+C stops it.",
                        flush=True,
                    )
                    while not stopping.wait(0.5):
                        pass
                code = 0
            except (LaunchFailed, NotReady) as error:
                print(f"jms-uc1: {error}", file=sys.stderr)
                code = 1
            finally:
                print("Stopping the four participants...", flush=True)
                supervisor.stop_all()
                left = supervisor.may_not_have_deregistered()
                if left:
                    print(
                        f"These may not have deregistered their services: {display_names(left)}",
                        flush=True,
                    )
                page.stop()
    finally:
        sock.close()
    return code


def _seed(args: argparse.Namespace) -> int:
    """Clear the dataset, seed it once, and print each participant's command line.

    The live-instance guard runs first, as for a run. Nothing binds a page port and no process
    starts; each participant runs from its printed line, in its own terminal, in any order.
    Returns 0, or 1 when the guard refuses or the seed raises.
    """
    logging.basicConfig(level=logging.WARNING, force=True)
    logging.getLogger("jms_kapps").setLevel(logging.INFO)

    dataset = args.dataset
    db = connect(dataset)
    try:
        refuse_if_live(reader(db), dataset=dataset, force=args.force)
    except LiveInstanceError as error:
        print(f"jms-uc1: {error} Or pass --force to clear it anyway.", file=sys.stderr)
        return 1
    except LaunchFailed as error:
        print(f"jms-uc1: {error}", file=sys.stderr)
        return 1

    try:
        print(f"Seeding {dataset} on {endpoint_for(dataset).url}", flush=True)
        run_core_seed(db)
    except Exception as error:  # noqa: BLE001
        print(f"jms-uc1: {error}", file=sys.stderr)
        return 1

    from jms_kapps.uc1.seed import GRINDER, GRINDER_2, SCREW, SCREW_2

    print(
        f"Seeded {dataset}: {GRINDER.fragment} with {SCREW.fragment}, "
        f"{GRINDER_2.fragment} with {SCREW_2.fragment}",
        flush=True,
    )
    print(
        "Start each participant by its command, on this computer or another one that reaches "
        "the triple store:",
        flush=True,
    )
    for participant in participants(dataset):
        print(PLATFORM.command_line(participant.command), flush=True)
    return 0


def _print_dashboards(db: TripleStore, the_participants: Sequence[Participant]) -> None:
    """Print each participant's dashboard address, or that it has none yet.

    A failed read prints one line and the run goes on.
    """
    try:
        live = reader(db)(())
    # Whatever the reader raises: the addresses are a convenience, and the run goes on.
    except Exception as error:  # noqa: BLE001
        print(f"Could not read the dashboards' addresses: {error}", flush=True)
        return
    by_resource = {service.resource: service for service in live}
    for participant in the_participants:
        service = by_resource.get(participant.resource)
        if service is not None and service.address is not None:
            print(
                f"{participant.name}: {service.address.rstrip('/')}/dashboard",
                flush=True,
            )
        else:
            print(f"{participant.name}: no address yet", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
