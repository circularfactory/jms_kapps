"""`jms-eval-uc2`: one UC2 run of one configuration, on its own repository of the configured
GraphDB, with its record. Iteration one of the measurement harness. Its command line::

    jms-eval-uc2 <configuration.toml> [--runs <folder>]
    jms-eval-uc2 --spec <spec.toml> --configuration <number> [--repetition <k>] [--prefills <folder>]
                 [--runs <folder>] [--heavy-run-approval <approval file>]

It reads the configuration (`configuration`): from a configuration file, or from a spec and the
configuration's number, as the spec's sweep resolves it (`spec`; jms_kapps 96, interim). A run made
from a spec records its configuration key and which run of the spec it is, as a sweep's run does,
and takes repetition k's pick seed (default 1), so it is the sweep's run. It refuses a heavy run
that no approval covers (`approval`): an approval covers the configurations of one spec for one
period, so a heavy run is made from the approved spec and the configuration's number, and a heavy
run of a bare configuration file is refused. It also refuses a run with the wrapper where
OpenTelemetry, the extra `evaluation`, is not installed (`wrapper`), and makes the run
(`bench.run`). The run makes a folder of its own below
`--runs`, named by its run id, with `record.json`, the record; `configuration.toml`, the resolved
configuration, which `jms-eval-uc2` runs again; and `output/`, each participant's output and the
harness's own log. It exits with 0 once the run completed, 1 if it failed, and 2 if it refused to
start: nothing reached a store then. A run that completed exits with 0 also where the store did
not delete its repository; its last line then names the repository, so that a person deletes it
(changed on 2026-10-14, jms_kapps 116: until then such a run exited with 1).

The connection to the configured GraphDB comes from `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
`TRIPLESTORE_PASSWORD`, as for every use case (changed on 2026-10-11, jms_kapps 62: until then
`GRAPHDB_URL`, `GRAPHDB_USERNAME` and `GRAPHDB_PASSWORD`); the login needs the right to create and delete
repositories and to read the server's monitoring views. The repository never comes from the
environment: each run creates its own, `eval-` plus its run id, and deletes it at its end.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kapps_semantic_middleware.credentials import REQUIRED_TRIPLESTORE_ENV

from jms_kapps.common.triplestore import connect
from jms_kapps.evaluation import wrapper
from jms_kapps.evaluation.approval import (
    APPROVAL_FORM,
    Covered,
    HeavyRunRefused,
    cover,
    load_approval,
)
from jms_kapps.evaluation.bench import (
    GraphDBBenchGraph,
    HTTPSpawnRate,
    outcome_line,
    run,
)
from jms_kapps.evaluation.configuration import (
    Configuration,
    ConfigurationRefused,
    read_configuration,
)
from jms_kapps.evaluation.spec import (
    SpecRefused,
    configurations,
    for_repetition,
    read_spec,
    resolved,
    sweep_fields,
)
from jms_kapps.evaluation.store import GraphDBServer

PROGRAM = "jms-eval-uc2"


def graph_for(
    repository: str, prefill: Path | None, with_shapes: bool
) -> GraphDBBenchGraph:
    """The run's graph on `repository` of the configured GraphDB, which loads `prefill`, and
    whose reset sets the shapes only `with_shapes`: the configuration's `shacl`."""
    return GraphDBBenchGraph(
        connect(repository), prefill=prefill, with_shapes=with_shapes
    )


def missing_store_variables() -> list[str]:
    """The connection variables of the configured GraphDB that the environment does not set:
    none where a run can reach it."""
    return [name for name in REQUIRED_TRIPLESTORE_ENV if not os.environ.get(name)]


def run_on_the_configured_store(
    configuration: Configuration,
    *,
    runs: Path,
    approval: Covered | None = None,
    sweep: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One run of `configuration` (`bench.run`) on the configured GraphDB, with a client of its
    own, and its record, in a folder of its own below `runs`. `sweep` says which run of a sweep
    it is (`jms-eval-sweep`), or is None."""
    # One line for each HTTP request would bury the run's own lines; the record keeps every
    # request the harness sent to the server.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = GraphDBServer(
        os.environ["TRIPLESTORE_URL"],
        username=os.environ["TRIPLESTORE_USERNAME"],
        password=os.environ["TRIPLESTORE_PASSWORD"],
    )
    try:
        return run(
            configuration,
            runs=runs,
            server=server,
            graph_for=graph_for,
            spawn_rate=HTTPSpawnRate(),
            approval=approval,
            sweep=sweep,
        )
    finally:
        server.close()


def configuration_to_run(
    args: argparse.Namespace,
) -> tuple[Configuration, Path | None, dict[str, Any] | None, str | None]:
    """The configuration to run, the spec it comes from, what its record says of that (the
    sweep's fields), and its key: from the configuration file, or from the spec and the
    configuration's number, resolved as the spec's sweep resolves it.

    Raises:
        ConfigurationRefused: If the configuration file is refused.
        SpecRefused: If the spec is refused, or the options do not say one configuration.
    """
    if args.spec is None:
        if args.configuration is None:
            raise SpecRefused(
                "give a configuration file, or a spec with --spec and the configuration's "
                "number with --configuration"
            )
        if args.number is not None or args.repetition is not None:
            raise SpecRefused(
                "--configuration and --repetition go with --spec, a configuration from a spec"
            )
        return read_configuration(args.configuration), None, None, None
    if args.configuration is not None:
        raise SpecRefused(
            "give a configuration file or --spec with --configuration, not both"
        )
    if args.number is None:
        raise SpecRefused(
            "--spec takes the configuration's number: --configuration <number>"
        )
    spec = read_spec(args.spec)
    found = configurations(spec)
    if not 1 <= args.number <= len(found):
        raise SpecRefused(
            f"the spec has {len(found)} configurations: there is no configuration {args.number}"
        )
    chosen = found[args.number - 1]
    repetition = 1 if args.repetition is None else args.repetition
    if not 1 <= repetition <= chosen.repetitions:
        raise SpecRefused(
            f"configuration {chosen.number} of the spec has {chosen.repetitions} repetitions: "
            f"there is no repetition {repetition}"
        )
    return (
        for_repetition(
            spec, resolved(chosen, spec=spec, prefills=args.prefills), repetition
        ),
        args.spec,
        sweep_fields(spec, chosen, repetition),
        chosen.key,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="One UC2 run of one configuration, on its own repository of the "
        "configured GraphDB, with its record (the measurement harness, iteration one).",
    )
    parser.add_argument(
        "configuration",
        type=Path,
        nargs="?",
        default=None,
        help="The configuration file, TOML: a configuration of your own, or the "
        "configuration.toml of an earlier run, which runs it again. A heavy run is not made "
        "from it: no approval covers a bare configuration file",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="Instead of a configuration file: the spec, TOML, of the configuration to run, "
        "with --configuration. A heavy run is made this way, under an approval of this spec",
    )
    parser.add_argument(
        "--configuration",
        dest="number",
        type=int,
        default=None,
        metavar="NUMBER",
        help="With --spec: the configuration's number in the spec, from 1, as "
        "`jms-eval-sweep --list` shows",
    )
    parser.add_argument(
        "--repetition",
        type=int,
        default=None,
        metavar="K",
        help="With --spec: the repetition of the configuration, which gives the pick seed "
        "(default: 1)",
    )
    parser.add_argument(
        "--prefills",
        type=Path,
        default=Path("prefills"),
        help="With --spec: the folder from which a prefill file that the spec names is read "
        "(default: prefills)",
    )
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("runs"),
        help="The folder in which the run makes its own folder (default: runs)",
    )
    parser.add_argument(
        "--heavy-run-approval",
        metavar="FILE",
        type=Path,
        default=None,
        help="The approval of the heavy runs (heavy = true) of a spec, which any person may "
        "give, in agreement with the server's admins, for one spec and one period: "
        f"{APPROVAL_FORM}. Off by default; the record keeps it",
    )
    args = parser.parse_args(argv)
    try:
        configuration, spec_path, sweep, key = configuration_to_run(args)
        approval = load_approval(args.heavy_run_approval)
        covered = (
            cover(
                approval,
                spec=spec_path,
                key=key,
                configuration=configuration,
            )
            if configuration.heavy
            else None
        )
        if configuration.wrapper:
            wrapper.available()
    except (
        ConfigurationRefused,
        HeavyRunRefused,
        SpecRefused,
        wrapper.WrapperUnavailable,
    ) as refused:
        print(f"{PROGRAM}: {refused}", file=sys.stderr)
        return 2
    missing = missing_store_variables()
    if missing:
        print(
            f"{PROGRAM}: no GraphDB is configured: set {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    record = run_on_the_configured_store(
        configuration, runs=args.runs, approval=covered, sweep=sweep
    )
    print(outcome_line(PROGRAM, record, args.runs))
    return 0 if record["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
