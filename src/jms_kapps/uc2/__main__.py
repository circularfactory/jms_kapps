"""The entry point of the `jms-uc2` console script.

`jms-uc2` runs use case 2 from a topology behind the Launcher page (`launcher/runner.py`). `jms-uc2
slice` plays the two acts of the vertical slice (`vertical_slice.py`). Both clear the dataset
they are given, `--dataset`, and both refuse to while another run is live there, unless `--force`
is given.
`--transit-time` sets the transit time of every module that has none of its own in the topology, and
the default of the Launcher page's field of the same name: a few seconds by default, so that a
person sees each box on each module, and 0 in the tests. The seed writes it into the graph, where
each module reads its own. `--retry-time` sets how long a
module waits before it asks again a module that refused to reserve itself, once its detour has
refused too: 1 s by default, and 0.1 s in the tests (changed on 2026-10-06, jms_kapps 43: until then
"an occupied or reserved module"). `--module-strategy` and `--wms-strategy` name the strategy of the
modules and of the WarehouseManagementSystem that the run starts with, `real` by default, and the
defaults of the Launcher page's two pickers, "Module strategy" and "WMS strategy"
(`strategies`). A name that no strategy of its kind has ends `jms-uc2` with the reason, before
anything is cleared (added on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07).
`--collect-history` has the first launch's reset turn on the store's history collection, so that
past possessions can be read, and sets the default of the Launcher page's checkbox "collect
history": off by default (added on 2026-10-10, jms_kapps 22, with the names Etienne decided on
2026-10-07). `--deadlock-alarm-seconds` sets how long a box a module holds may wait for a Reserve
before the module raises the deadlock alarm for it, and the default of the Launcher page's field
"deadlock alarm (s)": 300 s by default; a value that is not a positive number ends `jms-uc2` with
the reason (added on 2026-10-11, jms_kapps 77, decided by Etienne).
"""

from __future__ import annotations

import argparse
import math

from jms_kapps.common.triplestore import DEFAULT_DATASET
from jms_kapps.uc2.topology import topology_names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jms-uc2", description="Use case 2, the decentral FlexConveyor."
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"The dataset of the triple store to clear and use (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear the dataset even while another run is live there",
    )
    parser.add_argument(
        "--topology",
        default="cross",
        help=f"A shipped topology ({', '.join(topology_names())}) or the path of a JSON file "
        "that holds a module list, as the first iteration wrote one (default: cross)",
    )
    parser.add_argument(
        "--transit-time",
        type=float,
        default=None,
        help="Seconds a module takes to convey a box, unless the topology gives the module its "
        "own (default: a few, so that a person sees each move)",
    )
    parser.add_argument(
        "--retry-time",
        type=float,
        default=None,
        help="Seconds a module waits before it asks a module that refused to reserve "
        "itself again (default: 1)",
    )
    parser.add_argument(
        "--module-strategy",
        default="real",
        help="The strategy by which each module chooses the neighbours it asks for a box, by its "
        "name (default: real, the demonstration's behaviour)",
    )
    parser.add_argument(
        "--wms-strategy",
        default="real",
        help="The strategy by which the WarehouseManagementSystem picks the origin and the "
        "destination of a spawn, by its name (default: real, the demonstration's behaviour)",
    )
    parser.add_argument(
        "--deadlock-alarm-seconds",
        type=float,
        default=None,
        help="Seconds a box a module holds may wait for a Reserve before the module raises the "
        "deadlock alarm for it, a WARNING that starts with 'possible deadlock:' (default: 300)",
    )
    parser.add_argument(
        "--collect-history",
        action="store_true",
        help="Turn on the store's history collection in the reset, so that past possessions can "
        "be read; it stays on until it is turned off by hand (default: off)",
    )
    commands = parser.add_subparsers(dest="command")
    slice_parser = commands.add_parser(
        "slice", help="Play the vertical slice: two modules, one box, one handover"
    )
    # Also accepted after `slice`. SUPPRESS keeps a value given before it.
    slice_parser.add_argument("--dataset", default=argparse.SUPPRESS)
    slice_parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS)
    slice_parser.add_argument("--transit-time", type=float, default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.transit_time is not None and args.transit_time < 0:
        parser.error("--transit-time must not be negative")
    if args.retry_time is not None and args.retry_time <= 0:
        parser.error("--retry-time must be positive")
    alarm = args.deadlock_alarm_seconds
    if alarm is not None and not (math.isfinite(alarm) and alarm > 0):
        parser.error(
            f"--deadlock-alarm-seconds must be a positive number of seconds, not {alarm:g}"
        )
    # Imported here, so that `--help` answers without loading the middleware. Checked before
    # anything else, so that a name no strategy has clears nothing.
    from jms_kapps.uc2.strategies import (
        ModuleStrategy,
        UnknownStrategy,
        WmsStrategy,
        chosen,
        found,
    )

    try:
        chosen(ModuleStrategy, args.module_strategy, found(ModuleStrategy))
        chosen(WmsStrategy, args.wms_strategy, found(WmsStrategy))
    except UnknownStrategy as unknown:
        parser.error(str(unknown))
    # Left out, each run takes the default of the processes it starts.
    transit = (
        {} if args.transit_time is None else {"transit_seconds": args.transit_time}
    )

    # Imported here, so that `--help` answers without loading the middleware.
    if args.command == "slice":
        from jms_kapps.uc2.vertical_slice import run as run_slice

        return run_slice(args.dataset, force=args.force, **transit)
    from jms_kapps.uc2.launcher.runner import run

    retry = {} if args.retry_time is None else {"retry_seconds": args.retry_time}
    alarm_time = {} if alarm is None else {"deadlock_alarm_seconds": alarm}
    return run(
        args.dataset,
        topology=args.topology,
        force=args.force,
        module_strategy=args.module_strategy,
        wms_strategy=args.wms_strategy,
        collect_history=args.collect_history,
        **transit,
        **retry,
        **alarm_time,
    )


if __name__ == "__main__":
    raise SystemExit(main())
