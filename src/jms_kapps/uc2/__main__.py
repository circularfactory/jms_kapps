"""The entry point of the `jms-uc2` console script.

`jms-uc2` runs use case 2 from a topology behind the Launcher page (`launcher/runner.py`). It clears
the dataset it is given, `--dataset`, and refuses to while another run is live there, unless
`--force` is given. `jms-uc2 audit` clears nothing: it validates the whole of `--dataset` against
both shape files and prints the report (`audit.py`). `jms-uc2 capability-resource` shows the store refuse a
capability that does not belong to exactly one resource, in three writes on `--dataset`, which must
be a disposable one, since it clears it (`capability_resource.py`). `jms-uc2 reachable-destination` shows the store
refuse the SHACL-SPARQL rule of the reachable destination at commit, in four steps on `--dataset`,
which must be a disposable one too (`reachable_destination.py`). `jms-uc2 trace-box` runs one box
over the topology `line` and reads every change of its possession back from the triple store's
history, on `--dataset`, a disposable one, whose history collection it turns on for good
(`trace_box.py`).
`--transit-time` sets the transit time of every module that has none of its own in the topology, and
the default of the Launcher page's field of the same name: a few seconds by default, so that a
person sees each box on each module, and 0 in the tests. The seed writes it into the graph, where
each module reads its own. `--retry-time` sets how long a
module waits before it asks again a module that refused to reserve itself, once its detour has
refused too: 1 s by default, and 0.1 s in the tests. `--module-strategy` and `--wms-strategy` name
the strategy of the
modules and of the WarehouseManagementSystem that the run starts with, `real` by default, and the
defaults of the Launcher page's two pickers, "Module strategy" and "WMS strategy"
(`strategies`). A name that no strategy of its kind has ends `jms-uc2` with the reason, before
anything is cleared.
`--collect-history` has the first launch's reset turn on the store's history collection, so that
past possessions can be read, and sets the default of the Launcher page's checkbox "collect
history": off by default. `--deadlock-alarm-seconds` sets how long a box a module holds may wait
for a Reserve
before the module raises the deadlock alarm for it, and the default of the Launcher page's field
"deadlock alarm (s)": 300 s by default; a value that is not a positive number ends `jms-uc2` with
the reason.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

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
    audit_parser = commands.add_parser(
        "audit",
        help="Validate the whole dataset against both shape files, the SHACL-SPARQL file "
        "included, and print the report; clears nothing",
    )
    audit_parser.add_argument("--dataset", default=argparse.SUPPRESS)
    owner_parser = commands.add_parser(
        "capability-resource",
        help="Show the store refuse a capability that does not belong to exactly one resource: "
        "three writes on a dataset whose name starts with ogm-disposable-, which it clears",
    )
    owner_parser.add_argument("--dataset", default=argparse.SUPPRESS)
    sparql_parser = commands.add_parser(
        "reachable-destination",
        help="Show the store refuse the SHACL-SPARQL rule of the reachable destination at commit: "
        "four steps on a dataset whose name starts with ogm-disposable-, which it clears",
        description="Show the store refuse the SHACL-SPARQL rule of use case 2 at commit: a "
        "transport may only be opened toward a module that the station's connections reach from "
        "its origin. Seeds three modules, opens a transport, and prints the store's answer to "
        "a transport toward a module no connection reaches and to a removed connection. Exits 0 "
        "when every step behaved as stated.",
    )
    sparql_parser.add_argument(
        "--dataset",
        default=argparse.SUPPRESS,
        help="The dataset to use; its name must start with ogm-disposable-, and the command "
        "clears it at its start and its end",
    )
    trace_parser = commands.add_parser(
        "trace-box",
        help="Run one box over the topology line and read every change of its possession back "
        "from the triple store's history: on a dataset whose name starts with ogm-disposable-, "
        "whose history collection it turns on for good",
        description="Seed the topology line with the triple store's history collection on, "
        "start its four modules and the WarehouseManagementSystem, spawn one box from Module1 "
        "to Module4, and read every change of its possession, its transport's status and its "
        "assignment back from the history, one row for each transaction. Prints the rows, "
        "writes them as JSON, and exits 0 when the possessors agree with the handovers the "
        "participants logged.",
    )
    trace_parser.add_argument(
        "--dataset",
        default=argparse.SUPPRESS,
        help="The dataset to use; its name must start with ogm-disposable-, and the command "
        "clears it and turns its history collection on for good",
    )
    trace_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write the trace as JSON (default: trace-<box>.json in the current "
        "folder)",
    )
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
    if args.command == "audit":
        from jms_kapps.uc2.audit import run as run_audit

        return run_audit(args.dataset)
    if args.command == "capability-resource":
        from jms_kapps.uc2.capability_resource import run as run_capability_resource

        return run_capability_resource(args.dataset)
    if args.command == "reachable-destination":
        from jms_kapps.uc2.reachable_destination import run as run_reachable_destination

        return run_reachable_destination(args.dataset)
    if args.command == "trace-box":
        from jms_kapps.uc2.trace_box import run as run_trace_box

        return run_trace_box(args.dataset, out_path=args.out, **transit)
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
