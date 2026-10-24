"""`jms-eval`: one command takes a spec to the report and the table. Iteration one of the measurement
harness (interim names: the command, its options, its output and this module's API).

    jms-eval [spec.toml] [--output <folder>] [--prefills <folder>]
             [--heavy-run-approval <approval file>] [--stop-at <time>] [--repeat block3:2]
             [--dry-run]

Repeating the evaluation should take about as much effort as running a use case. The command
composes what the other commands do and does none of it twice:

1. **The spec.** Without an argument it is the validation spec, `specs/validation.toml`, which
   covers each claimed figure once with short windows. Every spec in `specs/` ships.
2. **The store**, picked as `./run-uc2` picks it, with its messages: the store that
   `TRIPLESTORE_URL` names; else the one `GRAPHDB_URL` names, with the deprecation message (the
   run reads the deprecated variables as if the new ones were set); else the local GraphDB of
   `compose.yaml`, which the command starts as `./run-uc2` does. A dry run starts and contacts
   nothing.
3. **The prefill files.** Each one the spec names that is missing in `--prefills` (default:
   `prefills`) is made before the first run, by the prefill generator (`prefill.write_prefill`),
   so the file is the one the generator makes alone: the topology of the configuration that names
   it (a shipped name, or a file next to the spec), the size that the file's name gives
   (`<topology>-1M.ttl` is 1M statements, and nothing else is read from the name), and the seed 1
   unless the spec's defaults give `prefill_seed`. A file that is there is never made again.
4. **The runs**, as `jms-eval-sweep` makes them (`sweep`): the runs and replay levels without a
   record, one by one, each record below `--output`, the report made again after each run. A
   spec with the precision rule runs in rounds, and the call goes on round after round until no
   configuration is open (`sweep.sweep_rounds`).
   It takes the sweep's stop time, `--stop-at`, and obeys its stop file, `STOP` in `--output`:
   before each run it stops at the stop time or where the stop file is there, and removes the
   stop file.
5. **The report and the table** (`report`, `table`) at the end, whatever the end of the runs:
   `--output`/`report/report.md`, `--output`/`table.md` and `table.json`.

**The heavy parts are a safety part.** A heavy configuration, a ramp's level and a replay level
above the factor 1 start only under an approval that covers the spec (`approval`). Without the
option `--heavy-run-approval` the command runs everything that needs none, and at the end it lists
the heavy parts it skipped, with how to give an approval. With an approval file the heavy parts run
too, each through `approval.cover` at its start (`sweep.make_runs`): a heavy run that the approval
does not cover then (another spec, a spec changed since, a period that is not now) is skipped, the
normal runs are made, and at the end the call lists each heavy run it skipped with the reason. This command adds no way to run a heavy part. In a spec with the precision
rule each heavy part runs one time, in round 1, and `--repeat` makes sure a target repetition of
a part exists, as the sweep's option does (`sweep.repetitions_on_request`).

**The dry run** (`--dry-run`) needs no store and makes nothing: it shows the sweep's listing
(`sweep.listing`), the store it would pick, the prefill files it would make and the runs it would
make, and the heavy parts it would skip or run.

**Removed configurations:** the
command has no selection, and it leaves out each configuration that `jms-eval-sweep --remove`
removed in `--output` (`sweep.REMOVED_FILE`), until `jms-eval-sweep --put-back` puts it back.

It exits with 0 once every run it made completed, its runs ended or stopped at the stop time or
the stop file, and the report and the table were made; 1 when a run it made failed, it stopped at a
run that a stop signal ended or at the third failed run in a row, a round had nothing to make, or
the report or the table could not be made; and 2 when it refused to start: nothing reached a store
then. A heavy part that it skipped changes no exit code.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from jms_kapps.evaluation import prefill as prefill_generator
from jms_kapps.evaluation.approval import (
    APPROVAL_FORM,
    OPTION,
    Approval,
    HeavyRunRefused,
    load_approval,
)
from jms_kapps.evaluation.configuration import NO_PREFILL
from jms_kapps.evaluation.replay_levels import level_key
from jms_kapps.evaluation.run_uc2 import (
    missing_store_variables,
    run_on_the_configured_store,
)
from jms_kapps.evaluation.spec import (
    Spec,
    SpecConfiguration,
    SpecRefused,
    configurations,
    read_spec,
)
from jms_kapps.evaluation.summary import read_summaries
from jms_kapps.evaluation.sweep import (
    Call,
    SweepRun,
    add_repeat_option,
    add_stop_time_option,
    choose_call,
    has_run,
    has_work,
    listing,
    now,
    plan_call,
    removed_line,
    repetitions_on_request,
    round_without_runs,
    run_replay_on_the_configured_store,
    stops_of,
    sweep_rounds,
)
from jms_kapps.evaluation.table import make_table, write_table
from jms_kapps.uc2.topology import topology_names

PROGRAM = "jms-eval"

# The spec the command runs without an argument.
VALIDATION_SPEC = Path(__file__).resolve().parent / "specs" / "validation.toml"

# The seed of a prefill file whose spec gives none: the evaluation README's loop takes 1.
DEFAULT_PREFILL_SEED = 1

# The local fallback's store, of `compose.yaml`, with its login (`./run-uc2` sets the same).
LOCAL_STORE_URL = "http://localhost:7200"
LOCAL_STORE_USERNAME = "admin"
LOCAL_STORE_PASSWORD = "root"

# The size a prefill file's name gives: the last `-<size>` before the suffix, `-1M` in
# `grid-60-1M.ttl`.
_SIZE_IN_NAME = re.compile(r"-([0-9]+[kM]?)\.[A-Za-z]+$")


class HarnessRefused(ValueError):
    """The command cannot start: the message is why, for a person to read."""


# --- The store -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreChoice:
    """Which store the run uses, as `./run-uc2` picks it: `configured` (`TRIPLESTORE_URL`),
    `deprecated` (only `GRAPHDB_URL`) or `local` (the fallback), and its address."""

    kind: Literal["configured", "deprecated", "local"]
    url: str

    @property
    def message(self) -> str:
        """The line that `./run-uc2` prints for this choice, with this command's name."""
        if self.kind == "configured":
            return f"{PROGRAM}: the run uses the store that TRIPLESTORE_URL names, {self.url}"
        if self.kind == "deprecated":
            return (
                f"{PROGRAM}: GRAPHDB_URL is deprecated; set TRIPLESTORE_URL, which replaces "
                f"it. The run uses the store that GRAPHDB_URL names, {self.url}"
            )
        return (
            f"{PROGRAM}: TRIPLESTORE_URL is not set, so the run uses the local GraphDB of "
            f"compose.yaml, {self.url}"
        )


def store_choice() -> StoreChoice:
    """The store the environment names, as `./run-uc2` picks it: `TRIPLESTORE_URL`; else
    `GRAPHDB_URL`; else the local fallback. Reads the environment and changes nothing."""
    if os.environ.get("TRIPLESTORE_URL"):
        return StoreChoice("configured", os.environ["TRIPLESTORE_URL"])
    if os.environ.get("GRAPHDB_URL"):
        return StoreChoice("deprecated", os.environ["GRAPHDB_URL"])
    return StoreChoice("local", LOCAL_STORE_URL)


def use_deprecated_variables() -> None:
    """Set each connection variable that is not set from its deprecated predecessor, for this
    process: a run reads only `TRIPLESTORE_*`, where `./run-uc2` leaves the deprecated variables to
    `jms-uc2`."""
    for new, old in (
        ("TRIPLESTORE_URL", "GRAPHDB_URL"),
        ("TRIPLESTORE_USERNAME", "GRAPHDB_USERNAME"),
        ("TRIPLESTORE_PASSWORD", "GRAPHDB_PASSWORD"),
    ):
        if not os.environ.get(new) and os.environ.get(old):
            os.environ[new] = os.environ[old]


def start_local_store(root: Path | None = None) -> None:
    """Start the local fallback, the services of `compose.yaml` in the checkout's root, as
    `./run-uc2` does (`docker compose up -d --wait`, or `docker-compose`), and set the
    connection variables for this process. Returns once the services answer.

    Raises:
        HarnessRefused: If this is no checkout (no `compose.yaml`), Docker Compose is missing, or
            the services do not start.
    """
    folder = root or Path(__file__).resolve().parents[3]
    if not (folder / "compose.yaml").is_file():
        raise HarnessRefused(
            "no store is configured, and the local fallback needs a checkout of jms_kapps, "
            f"which has the compose.yaml; there is none at {str(folder)!r}. Set TRIPLESTORE_URL, "
            "TRIPLESTORE_USERNAME and TRIPLESTORE_PASSWORD"
        )
    if _compose_works(["docker", "compose"]):
        compose = ["docker", "compose"]
    elif shutil.which("docker-compose"):
        compose = ["docker-compose"]
    else:
        raise HarnessRefused(
            "needs Docker Compose to start the local GraphDB, and finds neither "
            "`docker compose` nor `docker-compose`."
        )
    try:
        subprocess.run([*compose, "up", "-d", "--wait"], cwd=folder, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise HarnessRefused(f"the local GraphDB did not start: {error}") from None
    os.environ["TRIPLESTORE_URL"] = LOCAL_STORE_URL
    os.environ["TRIPLESTORE_USERNAME"] = LOCAL_STORE_USERNAME
    os.environ["TRIPLESTORE_PASSWORD"] = LOCAL_STORE_PASSWORD


def _compose_works(command: list[str]) -> bool:
    if shutil.which(command[0]) is None:
        return False
    try:
        return (
            subprocess.run(
                [*command, "version"], capture_output=True, check=False
            ).returncode
            == 0
        )
    except OSError:
        return False


# --- The prefill files -----------------------------------------------------------------------


@dataclass(frozen=True)
class PrefillToMake:
    """One prefill file the command makes: where, and the arguments of the generator."""

    path: Path
    topology: str
    size: int
    seed: int

    def describe(self) -> str:
        """The file with the arguments of the generator, for a person to read."""
        return f"{self.path} (topology {self.topology}, {self.size} statements, seed {self.seed})"


def prefill_path(prefills: Path, name: str) -> Path:
    """The file a configuration's prefill `name` is read from: below `prefills`, as a spec
    resolves it (`spec.resolved`); a name that is absolute is that file."""
    return Path(prefills).expanduser().absolute() / Path(name).expanduser()


def topology_for(spec: Spec, configuration: SpecConfiguration) -> str:
    """The topology the generator is given for `configuration`: a shipped name as it is, else the
    topology file, read from the spec's folder as a configuration reads it."""
    value = str(configuration.values["topology"])
    if value in topology_names():
        return value
    return str((spec.path.resolve().parent / Path(value).expanduser()).resolve())


def prefills_to_make(
    spec: Spec, wanted_by: Sequence[SpecConfiguration], prefills: Path
) -> list[PrefillToMake]:
    """The prefill files that `wanted_by` configurations name and that are not there, each once,
    in the order the configurations name them.

    Raises:
        HarnessRefused: If a missing file's name gives no size.
    """
    seed = DEFAULT_PREFILL_SEED if spec.prefill_seed is None else spec.prefill_seed
    found: dict[Path, PrefillToMake] = {}
    for configuration in wanted_by:
        name = configuration.values["prefill"]
        if name == NO_PREFILL:
            continue
        path = prefill_path(prefills, str(name))
        if path in found or path.is_file():
            continue
        size = _SIZE_IN_NAME.search(path.name)
        if size is None:
            raise HarnessRefused(
                f"the prefill file {str(path)!r} of configuration {configuration.number} is "
                "missing, and its name gives no size to make it with: name it "
                "<topology>-<size>.ttl, such as grid-100k.ttl, or make it with "
                "`python -m jms_kapps.evaluation.prefill`"
            )
        found[path] = PrefillToMake(
            path,
            topology_for(spec, configuration),
            prefill_generator.size_of(size.group(1)),
            seed,
        )
    return list(found.values())


def make_prefills(
    missing: Sequence[PrefillToMake], *, echo: Callable[[str], None] = print
) -> None:
    """Make each file of `missing` with the prefill generator, in a folder made if need be.

    Raises:
        HarnessRefused: If the generator refuses what it was asked for, or the file cannot be
            written.
    """
    for item in missing:
        echo(f"{PROGRAM}: making the prefill file {item.describe()}")
        try:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            prefill_generator.write_prefill(
                item.path, topology=item.topology, size=item.size, seed=item.seed
            )
        except prefill_generator.PrefillRefused as refused:
            raise HarnessRefused(f"the prefill file {item.path}: {refused}") from None
        except OSError as error:
            raise HarnessRefused(f"cannot write {item.path}: {error}") from None


# --- What a call does, and what it leaves out -------------------------------------------------


def configurations_to_make(call: Call) -> list[SpecConfiguration]:
    """The configurations of the call that have a run or a replay level without a record, in the
    spec's order, and the open configurations under the precision rule, which its later rounds
    make: the ones whose prefill files the call needs."""
    needed = {c.key for c, r in call.chosen if not has_run(call.done, c.key, r)}
    needed |= {c.key for c in call.open}
    needed |= {
        c.key
        for c in call.replays
        for factor in c.replay_factors
        if not has_run(call.done, level_key(c.key, factor), call.replay_repetition(c))
    }
    return [c for c in call.found if c.key in needed]


def heavy_lines(call: Call) -> list[str]:
    """The heavy parts a call withheld, one line each: a ramp, a heavy configuration, a replay."""
    lines: list[str] = []
    ramps: dict[int, list[SpecConfiguration]] = {}
    for configuration, _ in call.withheld:
        if configuration.ramp is not None:
            levels = ramps.setdefault(configuration.ramp, [])
            if configuration not in levels:
                levels.append(configuration)
    for block, levels in sorted(ramps.items()):
        rates = ", ".join(f"{float(c.values['boxes_per_minute']):g}" for c in levels)
        lines.append(
            f"the ramp of block {block}: {len(levels)} levels not run yet, at "
            f"{rates} boxes per minute (configurations "
            f"{', '.join(str(c.number) for c in levels)})"
        )
    heavy: dict[int, int] = {}
    for configuration, _ in call.withheld:
        if configuration.ramp is None:
            heavy[configuration.number] = heavy.get(configuration.number, 0) + 1
    for number, runs in sorted(heavy.items()):
        lines.append(f"configuration {number} (heavy = true): {runs} runs")
    levels_of: dict[int, list[float]] = {}
    for configuration, factor in call.withheld_levels:
        levels_of.setdefault(configuration.number, []).append(factor)
    for number, factors in sorted(levels_of.items()):
        lines.append(
            f"the replay of configuration {number} at the "
            f"{'factors' if len(factors) > 1 else 'factor'} {', '.join(f'{f:g}' for f in factors)}"
        )
    return lines


def how_to_approve(spec_path: Path) -> list[str]:
    """What a person does to let the heavy parts run."""
    why = (
        "A heavy part is a sustained load, a ramp or a replay compressed in time, and other "
        "people use the server. Any person may approve it, in agreement with the server's "
        "admins, for this spec and one period."
    )
    how = (
        f"Write {APPROVAL_FORM}, and run the command again with {OPTION} <the approval "
        f"file>: {PROGRAM} {spec_path} {OPTION} <approval file>. It runs the heavy parts and "
        "skips what has a record."
    )
    return [why, how]


def say_skipped(call: Call, spec: Spec, *, echo: Callable[[str], None] = print) -> None:
    """Tell the heavy parts the call skipped and how to give an approval; nothing where it
    skipped none."""
    lines = heavy_lines(call)
    if not lines:
        return
    echo(
        f"{PROGRAM}: skipped {len(lines)} heavy parts, which need an approval that covers "
        f"{spec.path}:"
    )
    for line in lines:
        echo(f"  - {line}")
    for line in how_to_approve(spec.path):
        echo(f"{PROGRAM}: {line}")


# --- The dry run -------------------------------------------------------------------------------


def dry_run(
    spec: Spec,
    call: Call,
    *,
    prefills: Path,
    approval: Approval | None,
    echo: Callable[[str], None] = print,
) -> None:
    """Show what a call would make and run: the sweep's listing, the store, the prefill files, the
    runs, and the heavy parts. Needs no store and makes nothing."""
    echo(
        f"{PROGRAM}: dry run of {spec.path}: nothing is made and nothing reaches a store"
    )
    for line in listing(call.found, call.done, spec, call.summaries, call.removed):
        echo(line)
    if call.the_round is not None:
        echo(
            f"{PROGRAM}: precision rule: this call would start at round "
            f"{call.the_round.number} and go on round after round"
        )
    choice = store_choice()
    echo(
        choice.message.replace("the run uses", "the run would use")
        + (" (a dry run starts it not)" if choice.kind == "local" else "")
    )
    missing = prefills_to_make(spec, configurations_to_make(call), prefills)
    echo(f"{PROGRAM}: {len(missing)} prefill files to make in {prefills}")
    for item in missing:
        echo(f"  - {item.describe()}")
    runs = sum(not has_run(call.done, c.key, r) for c, r in call.chosen)
    levels = sum(
        not has_run(call.done, level_key(c.key, factor), call.replay_repetition(c))
        for c in call.replays
        for factor in c.replay_factors
    )
    echo(
        f"{PROGRAM}: {runs} runs and {levels} replay levels to make without an approval"
    )
    if approval is None:
        say_skipped(call, spec, echo=echo)
    else:
        echo(
            f"{PROGRAM}: the heavy parts would run under the approval of {approval.approver}, "
            f"if it covers {spec.path}: the run checks that at the start of each heavy run, and "
            "skips and lists one that it does not cover then"
        )


# --- The command -------------------------------------------------------------------------------


def make_report_and_table(
    spec: Spec,
    found: Sequence[SpecConfiguration],
    runs: Path,
    *,
    echo: Callable[[str], None] = print,
) -> bool:
    """Make the report (`report.update_report`) and the table (`table.make_table`) of the runs
    below `runs`. Returns whether both were made; an error of either is told with `echo`."""
    made = True
    try:
        # Imported here: the report needs the optional extra `evaluation` (pandas, matplotlib).
        from jms_kapps.evaluation import report

        path = report.update_report(spec, found, runs)
        echo(f"{PROGRAM}: the report is at {path}")
    except Exception as error:  # noqa: BLE001 - told, and the table still follows
        made = False
        echo(
            f"{PROGRAM}: the report could not be made: {type(error).__name__}: {error}. "
            "Is the extra `evaluation` installed (pandas, matplotlib)?"
        )
    try:
        table = make_table(spec, found, read_summaries(runs), runs=runs)
        as_json, as_markdown = write_table(runs, table)
        echo(f"{PROGRAM}: the table is at {as_markdown} and {as_json}")
    except Exception as error:  # noqa: BLE001 - told
        made = False
        echo(f"{PROGRAM}: the table could not be made: {type(error).__name__}: {error}")
    return made


def main(argv: list[str] | None = None, *, clock: Callable[[], datetime] = now) -> int:
    """Run the command as the module docstring says; returns its exit code.

    Args:
        clock: The time now, which the stop time and the approval of each heavy run at its start
            are checked against: this machine's by default.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="One command from a spec to the report and the table (the measurement "
        "harness, iteration one): it picks the store as ./run-uc2 does, makes the prefill files "
        "that are missing, runs what has no record and makes the report and the table.",
    )
    parser.add_argument(
        "spec",
        type=Path,
        nargs="?",
        default=VALIDATION_SPEC,
        help="The spec, TOML (default: the validation spec, which covers each claimed figure "
        "once with short windows)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="The folder of the run: the runs' records, report/, table.md and table.json; a "
        "call goes on from the records in it (default: eval-<the spec's name>)",
    )
    parser.add_argument(
        "--prefills",
        type=Path,
        default=Path("prefills"),
        help="The folder of the prefill files; a missing one is made there (default: prefills)",
    )
    parser.add_argument(
        "--heavy-run-approval",
        metavar="FILE",
        default=None,
        type=Path,
        help="The approval of the heavy parts of this spec, which any person may give, in "
        f"agreement with the server's admins, for one spec and one period: {APPROVAL_FORM}. "
        "Without it the heavy parts are skipped and listed",
    )
    add_stop_time_option(parser, folder="--output")
    add_repeat_option(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be made and run, and make and run nothing; needs no store",
    )
    args = parser.parse_args(argv)
    runs: Path = args.output or Path(f"eval-{args.spec.stem}")
    try:
        spec = read_spec(args.spec)
        found = configurations(spec)
        approval = load_approval(args.heavy_run_approval)
        stops = stops_of(runs, args.stop_at, clock)
        requested, on_request = (
            ({}, [])
            if args.repeat is None
            else repetitions_on_request(spec, found, runs, args.repeat)
        )

        def choose(left_out: Collection[str] = ()) -> Call:
            return choose_call(
                spec,
                found,
                runs,
                withhold_heavy=approval is None,
                requested=requested,
                left_out=left_out,
            )

        def plan(chosen: Call) -> list[SweepRun]:
            return plan_call(spec, chosen, prefills=args.prefills, approval=approval)

        call = choose()
        for line in on_request:
            print(line.replace("jms-eval-sweep", PROGRAM, 1))
        if args.dry_run:
            dry_run(spec, call, prefills=args.prefills, approval=approval)
            return 0
        if has_work(call):
            choice = store_choice()
            print(choice.message)
            if choice.kind == "deprecated":
                use_deprecated_variables()
            if choice.kind != "local":
                missing = missing_store_variables()
                if missing:
                    raise HarnessRefused(
                        f"no GraphDB is configured: set {', '.join(missing)}"
                    )
            make_prefills(
                prefills_to_make(spec, configurations_to_make(call), args.prefills)
            )
            runs_to_make = plan(call)
            if choice.kind == "local":
                start_local_store()
        else:
            runs_to_make = []
    except (SpecRefused, HeavyRunRefused, HarnessRefused) as refused:
        print(f"{PROGRAM}: {refused}", file=sys.stderr)
        return 2
    to_make = sum(not run.skipped for run in runs_to_make)
    print(
        f"{PROGRAM}: {args.spec}: {len(found)} configurations; {to_make} runs and replay "
        f"levels to make, the output is {runs}"
        + (
            f"; precision rule: this call starts at round {call.the_round.number} and goes "
            "on round after round"
            if call.the_round is not None
            else ""
        )
    )
    gone = removed_line(call, runs)
    if gone is not None:
        print(gone.replace("jms-eval-sweep", PROGRAM, 1))
    empty = round_without_runs(call, runs_to_make, runs=runs)
    completed = True
    if empty is not None:
        print(empty.message.replace("jms-eval-sweep", PROGRAM, 1))
        completed = False
    elif to_make:
        try:
            completed = (
                sweep_rounds(
                    spec,
                    call,
                    runs_to_make,
                    runs=runs,
                    run_one=run_on_the_configured_store,
                    plan=plan,
                    choose=choose,
                    run_replay=run_replay_on_the_configured_store,
                    stops=stops,
                ).exit_code
                == 0
            )
        except KeyboardInterrupt:
            print(f"{PROGRAM}: stopped between two runs", file=sys.stderr)
            return 1
    made = make_report_and_table(spec, found, runs)
    if approval is None:
        say_skipped(call, spec)
    return 0 if completed and made else 1


if __name__ == "__main__":
    raise SystemExit(main())
