# evaluation

This folder holds the evaluation of the revised paper: the **measurement harness**, which measures
the triple store under the load that use case 2 makes, and the **benchmark strategies**, which make
that load known and repeatable. Each of them is built by a ticket of its own. The folder holds one
benchmark strategy so far, `southward` (below).

## The demonstration runs without it

Use case 1 and use case 2 import nothing from this folder, and a test checks that. `jms-uc2` finds
the strategies it may run with only through two Python entry-point groups, so with this folder
removed the demonstration runs as before: an entry that points into the folder cannot be imported,
it is skipped with one WARNING that names it, and the Launcher still offers `real`, "Real
behaviour", the demonstration's own strategy of each kind.

## How a strategy registers

A strategy chooses where a box goes, and nothing else: it never writes, never reads the store, and
never waits. The module or the WarehouseManagementSystem that asks it reads what it reads anyway,
and passes it in. There are two kinds, both in `jms_kapps.uc2.strategies`:

- A **module strategy** is an object of a subclass of `ModuleStrategy`. Its `neighbours_to_ask`
  is given the module, the box's destination, the module the box came from (or None), the module
  reserved ahead for the box (or None), and the station's connections as the module read them,
  each with its source, its target and its direction. It answers the neighbours the module asks,
  in order, at most two: the first, and the detour if the first declines; none where the box
  cannot go on. **After a crash it is asked again** with the same signature: when a neighbour it
  named cannot be reached, or refuses "no live route on", the module leaves that neighbour out for
  the box and asks the strategy again, with the connections to each module left out removed and
  with no module the box came from (None), so the module the box came from is then a valid
  choice. At a look-ahead, the module that holds the box stays the module it came from. A
  strategy that names only neighbours to which a connection it is given leads never names a
  module left out; one it names all the same is not asked, with one WARNING.
- A **WMS strategy** is an object of a subclass of `WmsStrategy`. Its `origin_and_destination` is
  given the candidates of a spawn (each free origin the spawn allows, with the destinations it
  allows that the origin reaches), the station's connections as the WarehouseManagementSystem
  read them, and the WarehouseManagementSystem's chooser, which picks one module out of several.
  It answers one candidate pair, an origin and a destination.

Each strategy has a `label`, the name the Launcher's picker shows. A benchmark strategy is
synthetic, so its label ends in "(synthetic)".

To register one, add **one entry in the group of its kind** to jms_kapps' `pyproject.toml`, named
as the strategy, which loads the strategy object:

```toml
[project.entry-points."jms_kapps.uc2.module_strategies"]
<name> = "jms_kapps.evaluation.<module>:<object>"

[project.entry-points."jms_kapps.uc2.wms_strategies"]
<name> = "jms_kapps.evaluation.<module>:<object>"
```

The entry's name is the strategy's name: the one that `jms-uc2 --module-strategy <name>` and
`jms-uc2 --wms-strategy <name>` take, and the one a launch from the Launcher page sends. Run
`uv sync` after a change of the entry points, so that the installed package registers them. An
entry whose object is not a strategy of its group's kind is skipped with one WARNING, and so is a
second strategy of one kind with a name already found.

## The southward strategy

`southward`, labelled "Southward (synthetic)", is one strategy of each kind, in `southward.py`, and
the first benchmark strategy (iteration one of the harness). It is made for a grid
with its entries on the top row and its exits on the bottom row, such as the shipped `grid`. Both
are registered under the name `southward`: `jms-uc2 --module-strategy southward --wms-strategy
southward`, the Launcher's two pickers and the harness's configuration choose them.

- The **module strategy** answers only the module's southern neighbour, from the directions of the
  connections, whatever the box's destination. It never answers a detour; where no connection
  leads south, it answers none.
- The **WMS strategy** sends each box to the exit straight below its origin: the module reached by
  going south from the origin until no connection leads south. With the WMS's chooser it picks an
  origin among the candidates whose exit straight below is one of their destinations. Where there
  is none, it answers the first candidate origin with its exit straight below, which the WMS
  refuses as no candidate, with a reason that names `southward`: such a spawn ends in a refusal,
  never in an error.

Neither writes, reads the store or waits.

## One command: `jms-eval`

(Interim names: the command, its options, its output folder and `harness.py`.) Repeating the
evaluation should take about as much effort as running a use case. `jms-eval` takes a spec to the
report and the table in one command:

```sh
uv run jms-eval                                   # the validation spec, into eval-validation/
uv run jms-eval src/jms_kapps/evaluation/specs/iteration-one.toml --output runs/iteration-one
uv run jms-eval <spec> --dry-run                  # what it would make and run; no store needed
uv run jms-eval <spec> --heavy-run-approval <approval file>
uv run jms-eval <spec> --stop-at 2026-10-14T06:00+02:00   # no run starts after this time
uv run jms-eval <spec> --heavy-run-approval <approval file> --repeat block3:2,7:2   # repetition 2
```

It composes the commands below and does none of their work twice:

1. **The store**, picked as `./run-uc2` picks it and with its messages: the store that
   `TRIPLESTORE_URL` names; else the one that `GRAPHDB_URL` names, with the deprecation message
   (the run reads the deprecated `GRAPHDB_*` variables as if the new ones were set); else the local
   GraphDB of `compose.yaml`, which the command starts (`docker compose up -d --wait`, as
   `./run-uc2` does) and which needs a checkout and Docker Compose. The login needs the rights
   that a run needs (above). A dry run starts and contacts nothing.
2. **The prefill files.** Each one that the spec names and that is not in `--prefills` (default:
   `prefills`) is made before the first run, by the prefill generator (`prefill.write_prefill`,
   the command `python -m jms_kapps.evaluation.prefill`), so the made file is byte for byte the
   file that the generator makes alone with the same topology, size and seed. The topology is the
   `topology` value of the configuration that names the file: a shipped name, or a file read from
   the spec's folder. The size is the only thing read from the file's name: `<topology>-1M.ttl`
   is 1M statements (`1k`, `100k`, `1M`, `10M` or a whole number). The seed is 1, as the loop in
   "The iteration-one spec" takes, unless the `[defaults]` of the spec give `prefill_seed`, a
   whole number from 0 (a setting of the spec outside a configuration's key). A file that is
   there is never made again; a missing one whose name gives no size is refused before anything
   runs. The header of a file made for a topology file names that file's absolute path.
3. **The runs**, as `jms-eval-sweep` makes them (`sweep`, above): each run and each replay level
   without a record, one by one, each record below `--output` (default `eval-<the spec's name>`),
   so that a call goes on from the records in the folder, and the report is made again after each
   run. **A spec with the precision rule runs in rounds**, and the call goes on round after round
   until no configuration is open (see "The precision rule and the rounds"). The command takes the
   sweep's stop time, `--stop-at`, and obeys its stop file, `STOP` in `--output`: before each run
   it stops at the stop time, or where the stop file is there, and removes the stop file (see
   "Selection, records and stops"). It has no selection: it leaves out each configuration that
   `jms-eval-sweep --remove` removed in `--output`, until `jms-eval-sweep --put-back` puts it back
   (see "The precision rule and the rounds"). It takes the sweep's `--repeat`, which makes sure a
   target repetition of a ramp, a replay or a sustained load exists (see "The heavy parts in the
   rounds").
4. **The report and the table** at the end, whatever the end of the runs: `report/report.md` and
   its files (`jms-eval-report`), `table.md` and `table.json` (`jms-eval-table`), in `--output`.

It exits with 0 once every run it made completed, its runs ended or stopped at the stop time or
the stop file, and the report and the table are made; with 1 when a run it made failed, when it
stopped at a run that a stop signal ended, at the third failed run in a row or at a round with
nothing to make, or could not make the report or the table; and with 2 when it refused to start:
nothing reached a store then. A heavy part that it skipped is as if it was not selected: it
changes no exit code.

**Heavy parts.** A heavy configuration, a ramp's level and a replay level above the factor 1 start
only under an approval that covers the spec (above). Without `--heavy-run-approval` the command
runs everything that needs none, and at its end it lists the heavy parts it skipped and says how
to give an approval: write the approval file and call the command again with it, and it runs the
heavy parts and skips what has a record. With an approval file, each heavy run is covered through
`approval.cover` at its start, as the sweep covers it: one that the approval does not cover then
(another spec, a spec changed since, a period that is not now) is skipped, and the command makes
the normal runs and, at its end, lists each heavy run it skipped with the reason (see "Selection,
records and stops"). The command adds no other way to start a heavy part, and no test, CI
job or example holds an approval of a shipped spec.

**The dry run** shows the sweep's `--list` (the configurations and the runs that have a record,
and, for a precision rule, the round the next call starts at and where each configuration stands),
the store it would pick, the prefill files it would make, how many runs and replay levels it would
make without an approval, and the heavy parts it would skip or run. It needs no store and makes
nothing.

### The shipped specs

Every spec in `src/jms_kapps/evaluation/specs/` ships, so that a reader can run it and compare the
result with the paper's tables. The **claimed figures** are: the *write throughput* (transports
ended per minute, the completed writes per minute: `runs.csv` and the table's throughput section),
the *OGM latency* (the commit latency p50 and p95 that the wrapper measures: the table's latency
section), the *store scaling* (throughput and commit p95 against the size of the prefill:
`prefill-family-<n>.png`), the *SHACL overhead* (the table's SHACL overhead section), the
*compressed replay* (the table's replay levels) and the *judgement of a ramp* (the table's ramps).
Which table of the paper each one is, the paper step names.

| Spec | Run it with | Covers | Run time |
|---|---|---|---|
| `specs/validation.toml` | `uv run jms-eval` | each claimed figure once, short windows, the 5-module cross at 1k and 100k | below |
| `specs/calibration-cross-short.toml` | `uv run jms-eval src/jms_kapps/evaluation/specs/calibration-cross-short.toml` | the harness's own overhead: a wrapped and an unwrapped run, compared by the calibration pair section of the table | about 6 minutes |
| `specs/iteration-one.toml` | `uv run jms-eval src/jms_kapps/evaluation/specs/iteration-one.toml --output runs/iteration-one` | write throughput, OGM latency, store scaling (4 topologies, 4 prefill sizes), SHACL overhead (SHACL off at each size), the `southward` strategy, the history comparison; 125 runs | about 25 hours, over two time windows |
| `specs/after-series.toml` | `uv run jms-eval src/jms_kapps/evaluation/specs/after-series.toml --output runs/after-series` | the same figures under the precision rule, 26 cells; a draft until the product owner of the tests fixes its values; no replay and no ramp yet | at least 260 runs of about 12 minutes, in rounds of about 5.2 hours; one call goes on round after round until its stop time or the stop file |

**The validation spec** is the default. It holds six configurations: the centre (the 5-module
cross, a 1k prefill, SHACL on, with the wrapper), the same at 100k (store scaling), the centre
with SHACL off (SHACL overhead), and a ramp of three rising box rates (12, 24 and 48 per minute),
whose runs are heavy. The centre's first valid run is replayed at the factors 1 and 2 (the factor 2
is heavy). The write throughput and the OGM latency come from every UC2 run. Every window is one
minute, with one repetition: it is no measurement, only a check that each figure can be made.

- *Run time*, estimated, the making of the two prefill files (a few seconds) included:
  without the heavy parts: about 15 minutes (at most 25), three UC2 runs of about 3 minutes and the
  replay at the factor 1 of about 2; with the heavy parts: about 27 minutes (at most 40), the three
  levels of the ramp (about 3 minutes each, fewer where it stops at a broken level) and the replay at
  the factor 2 (about 1.5 minutes) on top. A first live run replaces the estimate with a measured
  time.
- *What to see* after a run without an approval: the 3 UC2 runs and the replay at the factor 1
  `completed`, `report/report.md` and `table.md` made, and, at the end of the output, the ramp and
  the replay at the factor 2 listed as skipped, with how to give an approval.

## The prefill

A **prefill** is a Turtle file of past journeys of use case 2, which the measurement harness loads
into the store before a run, so that the run meets a store of a known size. It holds what use case
2 writes when a journey has ended `done`: each box, named by a UUID as the
WarehouseManagementSystem names the boxes it spawns (`Box-<UUID>`), in the
WarehouseManagementSystem's possession, its transport `done` from its origin to its destination,
and the links between the two, both ways. A running use case 2 reads nothing in it as work in
flight: no box is on a module, no transport is `queued` or `running`, and there is no reservation.

```sh
python -m jms_kapps.evaluation.prefill --topology grid --size 100k --seed 79 --output prefill.ttl
```

- `--topology` names a shipped topology, such as `grid`, or a topology file by its path, as
  `jms-uc2 --topology` does. The file is for that topology: load it after its seed.
- `--size` is the statements in the file, before inference: a whole number, or one with the unit
  `k` or `M`, such as `1000`, `100k`, `1M` or `10M`. Each box with its transport is 10 statements
  and the WarehouseManagementSystem's possession state 2, so the file holds the size within 1 %, or the
  command refuses it.
- `--seed` is a whole number from 0. The same topology, size and seed give the same file, byte for
  byte; another seed gives other journeys. The seeded generator also gives each box its UUID: 128
  random bits of it, formatted as a UUID of version 4.
- `--output` is the path of the file to write.

The file's first comment lines, one `# key: value` each, state the topology, the size asked for,
the seed, the number of boxes, the generator and its version, and the number of statements in the
file. The generator's version is 2 since the boxes are named by UUIDs (version 1
numbered them): a file of version 1 is no longer what the command writes for the same topology,
size and seed. Neither the WarehouseManagementSystem nor a module reads every box of the file
(the monitor does, which a run of the harness does not start): the WarehouseManagementSystem names a
new box by a new UUID, and asks about a box that comes back in one `ASK`. The generator writes each
box as it makes it, so a file of 10M statements needs no more memory than one of 100k.

**Loading it.** Load the file after the seed and before any participant starts, as the seed is
loaded. A box, its possession and its transport need each other in the store's shapes, so load
the file in whole boxes, each with its possession and its transport:
`jms_kapps.evaluation.prefill.transactions(path, statements=n)` gives
it as Turtle documents of at most `n` statements, one transaction each, the first with the
WarehouseManagementSystem's possession state.

## Run one configuration

This is **iteration one** of the measurement harness: it explores the limits of the store under
use case 2 and gives a first benchmark fast. A design of experiments, iteration two, follows once
the limits are known. Every value of a run comes from a configuration file, so that iteration two
changes files, not code.

**A run with heavy load needs an approval that covers it: any person may approve.** A
sustained-load or breaking-point run is never a unit test or a CI job, because other people use
the server. It runs only under an approval, which any person may give, in agreement with the
server's admins. Such a configuration says `heavy = true`, and a command that makes one run
(`jms-eval-uc2`, `jms-eval-baseline`) then refuses to start unless an approval covers the run; the
sweep and `jms-eval` skip and list such a run, and go on with the next (below).

**An approval is a small file** (interim names: the file and its keys), passed by
the option `--heavy-run-approval`. It covers **one spec for one period**:

```toml
spec_key = "<the SHA-256 of the spec file: sha256sum my-spec.toml>"
start = 2026-10-13T20:00:00Z    # UTC, with its zone; the period starts here
end = 2026-10-14T06:00:00Z      # and ends here (this time is no longer inside it)
approver = "<who approved>"
text = "<what was approved, and with whom: the server's admins>"
```

- **The spec key** is the SHA-256 of the spec file's content, so the approval covers the spec as
  written. Another spec, or the same file once it changed, has another key and is not covered.
- **Inside its period** the approval covers every heavy configuration of the spec: several ramps
  and sustained loads alike. Each heavy run checks the period at its start; a sweep that reaches
  the end of the period skips the heavy runs after it and goes on with the normal runs
  (it does not stop there, between two runs).
- **What no approval covers**, refused with the reason before anything reaches a store, or, in
  the sweep and in `jms-eval`, skipped and listed with the reason: a heavy run
  without an approval; outside its period; of another spec or a changed spec file; with `--set`
  overrides of the sweep; of a bare configuration file; and the baseline of a run whose record
  holds no configuration key. A run at normal load needs no approval, and `heavy = false`
  is the default. **A replay of a recorded run compressed in time by a factor above 1 is a heavy
  run too** (the store-only baseline, below): it needs the approval of the replayed
  run's configuration, as a heavy run does; a replay at the factor 1 is at normal load.
- **The record of each heavy run** keeps the whole approval it ran under in `heavy_run_approval`:
  `spec_key`, `start`, `end`, `approver` and `text`.
- No test, CI job or example here gives an approval that covers a real spec of the repository.

A heavy run of one configuration is made from the approved spec and the configuration's number
(the numbers `jms-eval-sweep <spec> --list` shows); the run records its configuration key, as a
sweep's run does, and takes repetition k's pick seed (`--repetition`, default 1):

```sh
uv run jms-eval-uc2 src/jms_kapps/evaluation/configurations/cross-short.toml
uv run jms-eval-uc2 --spec my-spec.toml --configuration 3 --heavy-run-approval my-approval.toml
```

### What a run needs

A GraphDB that the environment names, as for every use case: `TRIPLESTORE_URL`,
`TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD` (the deprecated `GRAPHDB_URL`, `GRAPHDB_USERNAME`
and `GRAPHDB_PASSWORD` are still read, with a deprecation message). The login needs the right to
create and delete repositories and to read the server's monitoring views. The repository never comes
from the environment. The command runs from a checkout of jms_kapps, because it reads
`graphdb/jms-kapps-repo-config.ttl` at its root.

A run with the wrapper (`wrapper = true`, below) needs OpenTelemetry, the extra `evaluation` of
jms_kapps, which a plain install leaves out: `uv sync --extra evaluation` (the development
environment of a checkout has it). Without it, the command refuses such a run before anything
reaches the server, and says so.

### The configuration

A TOML file with exactly these keys, each with a value: a configuration has no defaults, a missing
value is refused by its name, and so is a key a run does not know.

| Key | Value |
|---|---|
| `topology` | A shipped topology (`cross`, `grid`, ...) or the path of a topology file |
| `prefill` | The path of a prefill file in Turtle, or `"none"` |
| `module_strategy` | The module strategy, by its name (`real`, ...) |
| `wms_strategy` | The WMS strategy, by its name (`real`, ...) |
| `shacl` | `true`: the repository validates with the shapes. `false`: the twin without them |
| `collect_history` | Whether the store collects history during the run |
| `boxes_per_minute` | The WarehouseManagementSystem's spawn rate during the warm-up and the window |
| `warm_up_seconds` | How long the spawn rate is on before the measured window starts; 0 for no warm-up |
| `window_seconds` | How long the measured window is |
| `drain_bound_seconds` | How long the run then waits at most for every transport to end |
| `matcher_interval_seconds` | How often the completion matcher reads the run's ended transports from the graph, from the load's start to the drain's end |
| `transit_seconds` | The transit time of every module that has none of its own in the topology, in seconds, 0 or more (interim) |
| `retry_seconds` | How long a module waits before it asks a Reserve again, in seconds, more than 0 (interim) |
| `wrapper` | `true`: each participant runs through the wrapper, which observes it (below). `false`: no wrapper, and the run kind `uc2-unwrapped` |
| `heavy` | Whether the load is heavy: a sustained-load or breaking-point run |

**The pick seed** (interim names) is the one value a configuration may
leave out, and it is no key: `wms_pick_seed`, a whole number from 0, written after the keys. It
seeds the generator that the WarehouseManagementSystem picks its modules with, for both strategies,
so that the same seed and topology make the same sequence of picks. A run without one takes
repetition 1 of the default base seed, and every run writes the seed it used into its record
(`run_settings.wms_pick_seed`) and into its saved `configuration.toml`, so that
`jms-eval-uc2 runs/<run id>/configuration.toml` makes the same picks again. The seed is no part of
a configuration's key (below): all repetitions of one configuration keep one key. What "the same
picks" means on a real run: the same seed gives the same draws from the generator in the same
order. Concurrent spawns may reach the picking lock in another order, and then take the draws in
that other order and find other modules free; so the sequence of picks is the same where the
spawns arrive in the same order.

A relative path is read from the configuration file's folder. A topology file, a prefill file or a
strategy that cannot be found is refused before anything reaches the server.
`configurations/` holds two short runs on the 5-module cross, with SHACL on and off, which check
that a run works end to end; neither has a warm-up. A third, `grid-short.toml`, is a short run on
the 30-module grid with a warm-up of 30 s. The values of the measured runs come from the
run task. The warm-up and the matcher interval came with the sweep, and the wrapper with the
observation: a configuration written before them is refused with the name of the one it misses.

### What a run does

1. It creates a repository of its own, `eval-` plus its run id, from jms_kapps' repository config:
   with SHACL on, the config with its id changed; with SHACL off, the twin, which differs from it
   only in the SHACL layer and the id. Both run the ruleset `rdfsplus-optimized`, and a run
   refuses a repository for which the server answers another. A run never names another
   repository: not `jms-kapps`, not `kapps-demo`, not `Tests`, and no name without `eval-`.
2. It starts use case 2 on the run core, as the Launcher does, but without the monitor: the reset
   of the repository and the seed of the topology, which sets the shapes only with SHACL on (the
   twin has no SHACL validation and refuses them, so its seed sends none), then the prefill file, in
   transactions of whole boxes of at most 10 000 statements each (`prefill.transactions`), then the
   history collection if the configuration collects history, then a process for each module and one
   for the WarehouseManagementSystem, with the configuration's strategies, each through the wrapper
   where the configuration has it. It waits until each is ready.
3. The warm-up and the window: it sets the WarehouseManagementSystem's spawn rate through its
   REST workflow at the warm-up's start, waits `warm_up_seconds`, and the measured window starts
   when the warm-up ends; it waits `window_seconds` and sets the rate to 0. Load enters only
   through that workflow. A warm-up of 0 is a run without one: the window starts as the rate is
   set. From the load's start the completion matcher reads the run's ended transports every
   `matcher_interval_seconds`, until the drain's end, and the server watch samples the server
   every 5 seconds (below).
4. The drain: it waits until no transport of the run is open, or `drain_bound_seconds` have
   passed. The run's transports are those of the boxes it spawned, the warm-up's among them: right
   after the store admitted the create of a box, the WarehouseManagementSystem logs that its
   transport is `queued`, the line names the transport, and the harness collects those lines as the
   run goes on. So the prefill's past journeys are not among them, and a spawn that fails after its
   create is found all the same. No box number is read: a box may be named by a number or by a
   UUID. Every read of the harness names the run's transports, in steps of at most 200 (`READ_NAMES`),
   and the matcher and the drain leave out the transports they have seen ended; no read asks for
   every box or every transport of the store (interim names). From the warm-up's start to the
   drain's end the harness writes nothing to the graph.
5. It stops the processes in order, deletes its repository, also after a failure, and writes the
   record, with the observation's measures and checks for a run that completed (below). A run that the server refuses, or that fails on the way, deletes its repository all the
   same, and its record says why it failed. So does a run that a stop signal ends: Ctrl+C,
   SIGTERM or a closing terminal's SIGHUP; a second one does not cut the end short.

**The deletion of the repository** (interim names). A large repository after a heavy run can take
the store longer to delete than a request may take, and the store can refuse a deletion.

- **The outcome says how the measurement ended, and only that.** A run whose steps completed is
  `completed`, whatever the deletion did, and gets its observation: its measures, its validity,
  its checks and its stuck Operations. A failed, over-time or interrupted run keeps its outcome.
  So a completed run whose repository is left counts as any completed run does, by its validity:
  the sweep goes on after it, it is no failed run, and the store-only baseline replays it.
- **The deletion is a fact of its own in the record**, `repository_deletion`: `deleted` (true or
  false), `why` it was not deleted, and `answer_lost`, why the deletion got no usable answer, where
  it got none. It is null where the run created no repository. A repository that the store did not
  delete is also the action `repository not deleted`, with why.
- **A lost answer is checked once.** Where the deletion gets no usable answer, the harness lists
  the server's repositories one time, by a request that names none, as the creation does after a
  lost answer. A deletion gets no usable answer where its request did not reach the store (no
  connection, or none in time: the record says so), where no answer came in the request's time or
  the connection dropped, where a gateway in front of the store answered 502, 503 or 504 in its
  place, as the proxy in front of the KIT GraphDB can, and where an answer arrived but could not be
  read (httpx's `DecodingError`: the record says "an answer to the deletion arrived but could not
  be read", not that the answer was lost). For an
  answer that arrived but could not be read, as for the others, the one listing decides whether it
  was deleted. A repository that the store lists no more
  is deleted (`deleted` true, with `answer_lost`); one that it still lists is not. Where that list
  gets no answer or is refused, the repository counts as not deleted, with both whys: a
  repository whose state is not known is treated as left. Any other answer, a 500 for example, is
  a refusal: the repository is not deleted, and no list is read.
- **A person deletes a left repository.** The last line of `jms-eval-uc2` and of
  `jms-eval-baseline`, and the line that `jms-eval-sweep` prints after each run, name it:
  `... The record is at <record>. The repository eval-... was not deleted: delete it on the
  server`. The exit code follows the outcome: 0 for a completed run, also where its repository
  was not deleted.

A record made before the deletion became a fact of its own may carry `; the repository was not
deleted` in its outcome; it keeps its outcome text, and no reader takes such a record as completed.

**The run limit** (interim names). Each step has a limit of its own, and the whole run has one too:
30 minutes by default. A run of the "after" series takes about 12 minutes.

- The time counts from the run's start, the moment before it creates its repository, to the start
  of its end (step 5). The end itself, the stop of the processes, the deletion of the repository
  and the write of the record, is outside the limit. So a run over its limit still cleans up and
  writes its record, and a run whose measurement is complete is never over time.
- A run older than its limit ends as a stop signal ends it: its processes stop, its repository is
  deleted, and its record is written.
- Its outcome is `failed (over time)`, then the limit and the step at which the limit came, for
  example `failed (over time): the run limit of 1800 s came during the drain`. Its actions hold
  `run limit reached`, with the limit and the step.
- A failed run is a result: the sweep does not make it again, and it counts toward the maximum of
  20 runs of the precision rule.
- The limit is a run setting, outside the configuration's key, so no key changes. The record keeps
  it as `run_settings.run_limit_seconds`. The store-only baseline and each replay level have the
  same limit (below).

Every other setting of use case 2 stays at its default, the deadlock alarm time among them, and
the record holds each with its value. The transit time and the retry wait are keys of the
configuration, so a spec can sweep them and the record holds them in its
configuration, not under `run_settings`; the records of the "before" series hold them under
`run_settings`, and the store-only baseline and the report still read those.

### What a run leaves

A folder below `--runs` (default: `runs`), named by the run id:

- `record.json`, the record: its run kind, `uc2`, or `uc2-unwrapped` without the wrapper, and the
  words "iteration one"; the resolved
  configuration in the TOML form of a configuration file; where the run is one of a sweep, which
  one (`sweep`, below); the actions the run took, each with its time, the warm-up (none where it
  is 0) and the window; each run setting with its value; the prefill file's digest and header
  lines; the version and commit of jms_kapps, the middleware, the object-graph mapper and the
  triple-store interface, Python and the OS; the client's CPU, its memory and the round trip to
  the server; the client host's load (`client_load`, below); what the server's Workbench shows: its version and edition, the config and the
  ruleset of the run's repository, its repositories and its resource view at the start, at the
  window's start and at the drain's end, the run's repository's query view at the window's start
  and at the drain's end, and every request the harness sent to it; the server watch (below):
  the resource view and a round trip about every 5 seconds from the load's start to the drain's
  end, the query and transaction view of every repository of the server at the run's start and
  at the drain's end, the global page cache at the window's start and at the drain's end, the
  license, and the statement counts of the run's repository before the participants start and
  after they stop; each participant's command
  line; the run's transports at the drain's end, the warm-up's among them, as the
  WarehouseManagementSystem's log named them (`queued_by_the_wms` is their number, `queued_not_in_the_graph` those the graph does not hold; the records of
  the first iteration hold `highest_box_before_the_window` instead; the report and `jms-eval-table` read both):
  each one done, with its place, each one failed, with its place and
  reason, and each one still open, with its place (its box counts as stuck); the spawns the
  WarehouseManagementSystem refused, with their reasons; the services still registered after
  the stop; and the observation (below): the matcher's reads, the measures, whether the run is
  valid, the checks it failed and made, and its stuck Operations; and whether the store deleted
  the run's repository (`repository_deletion`, above). No secret: the login is not in it.
- **The client host's load** (`client_load` in the record; interim). The participants and the
  harness share the client host, so a slow step could be a busy client. From the load's start, the
  spawn rate set at the warm-up's start (the window's for a run without a warm-up), to the drain's
  end, the harness samples the host's CPU use and memory use every `client_load_interval_seconds` (a
  run setting, 1 s) from the operating system's own figures, `/proc/stat` and `/proc/meminfo`, with
  a thread that sleeps between two reads of two small files; it adds no package. `client_load` holds
  the `status` (`sampled`, or `not available` on a host without `/proc`, which then holds no
  sample), the `interval_seconds`, the host's `memory_total_bytes` and the `samples`, each with its
  time `at`, its `cpu_percent` (the share of the time that all CPUs were busy since the sample
  before, in percent of all of them; null where no time passed) and its `memory_used_bytes` (the
  total less what the host calls available). The store-only baseline samples the same way from
  before its first replayed request to its window's end. A record made before this (the "before"
  series) has no `client_load`.
- `configuration.toml`, the resolved configuration. `jms-eval-uc2 <run>/configuration.toml` runs it
  again.
- `output/`: each participant's output, one file each, every line stamped with the time it
  arrived (the time, two spaces, the line), and `harness.log`, the harness's own.
- `spans/`, with the wrapper: each participant's spans, one file each (below).

The facts of the server are those its Workbench shows, read through the REST views the Workbench
reads, each kept as the server answered it. A view the server refuses is kept with its status, and
the harness makes up none. The other activity on the server shows as its repositories, each with
its state, its resource view, and the server watch below.

### The server watch

Other use of the same GraphDB may overlap a run, and nothing in the run itself shows it. The
harness watches the server. Every name below is interim.

- **The series.** From the load's start to the drain's end, the warm-up, the window and the drain,
  the harness reads the resource view (`GET rest/monitor/infrastructure`) and measures one round
  trip (the time of `GET rest/info/version`) about every 5 seconds, the run setting
  `server_watch_interval_seconds`. The record's `server.watch` holds the interval, the count and
  each sample with its time (`at`), the resource view as the server answered it (`resources`),
  the round trip (`round_trip`: `milliseconds` and `status`) and how long the sample took
  (`seconds`). The first sample is at the load's start. A sample is at most one request for each
  view, and the next comes one interval after the one before: a sample that takes longer than the
  interval skips the ones it missed, so that a slow server is not asked more often. The samples
  are taken where the run waits, between its own steps, as the matcher's reads are. A read that
  fails or that the server refuses is kept with its status or its error, and the run goes on.
- **The counters of every repository.** At the run's start, the moment before the harness creates
  the run's repository, and at the drain's end, the query and transaction view of every
  repository the server lists (`GET rest/monitor/repository/<id>`), the protected ones,
  `jms-kapps`, `kapps-demo` and `Tests`, among them: `server.repository_monitors_at_the_start`
  and `server.repository_monitors_at_the_drain_end`, each by the repository's id. Only a repository the
  server lists as `RUNNING` is read: the view of an inactive repository might initialise it, which
  would load another user's repository into the shared server, and an inactive repository has no
  queries, transactions or connections to count. Any other repository is kept with its state and
  `read: false`, and no request names it.
- **The statement counts** of the run's repository, explicit, inferred and in all
  (`GET rest/repositories/<id>/size`, the size the Workbench shows): `server.statements_before_the_participants`,
  read after the prefill load and before the participants start, and
  `server.statements_after_the_participants`, read after they stop and before the repository is
  deleted. They are read only where no participant writes, because GraphDB 10.8.5 reports a wrong
  size inside an open transaction (GDB-12163). The record's actions show the order
  (`statement counts read`). A run whose participants did not stop reads no second count.
- **The global page cache** (`GET rest/monitor/structures`: the cache's hits and misses, and the
  entity pool) at the window's start and at the drain's end: `server.structures_at_the_window_start`
  and `server.structures_at_the_drain_end`. It is not in the series.
- **The license** (`GET rest/graphdb-settings/license`: the edition and the licensed cores), once
  for each run: `server.license`. A login that may not read it is refused, and the refusal is kept.

The store-only baseline gets the same watch, from the window's start to the drain's end, and
the same counters and reads. The report and `jms-eval-table` read the records of a run before the
watch too, which have none of it.

**The guard.** The harness sends the server only the requests on an explicit list (`store.permit`),
and a request that is not on it is not sent. Of the requests that name a repository, only the
query and transaction view (`GET rest/monitor/repository/<id>`) may name any repository of the
server. Everything else names the run's own `eval-` repository: its creation, its config, its
statement counts and its deletion. So every write outside the run's own repository, and every
read of another repository's data, a query, its statements, an export, or its size, is refused.
The record's list of requests shows it: the only requests that name another repository are
monitoring reads. The queries running on another repository are read as the Workbench's monitor
shows them, counters only.

## Observe a run

Part 2 of the harness observes each run from outside KSM, the
object-graph mapper and the triple-store interface: none of them has a probe, and the code of no
participant changes.

### The wrapper

With `wrapper = true`, the bench launcher starts each participant through the wrapper, its own
command line behind `python -m jms_kapps.evaluation.wrapper --spans <file> --`. In the
participant's own process, the wrapper instruments FastAPI, requests and httpx with OpenTelemetry,
wraps the object-graph mapper's public calls, and then runs the participant's module with its
arguments, as `python -m` would. Every span goes into `spans/<participant>.jsonl`, one JSON object
a line, written as the span ends.

- A **store request** is a request to the URL that `TRIPLESTORE_URL` names. A hook tags its span
  with its kind, its method and path, its text (none for the login, whose text holds the
  password), its size, the Operation, the box and the status it writes where it names them
  (`store.operation`, `store.box`, `store.status_written`), its receipt, and, once the store
  answered, the status, the size of the answer, and the outcome: accepted, or refused with the
  kind of the refusal (`shacl`, `heap`, `other`) and the store's reason, its answer as it gave it.
  A request with no answer has no outcome on its span, which holds the error instead; the harness
  reads the outcome from that error (below).
- **The mapper's calls** are spans of their own: `kapps_ogm.OGM.commit`,
  `kapps_ogm.OGM.get_class_spec` (the class-spec derivation), `kapps_ogm.OGM.fetch`,
  `kapps_ogm.OGM.create`, `kapps_ogm.OGM.delete` and `kapps_ogm.Node.diff`. A commit shows its
  class query, its class-spec derivation, its fetch, its diff and its update as separate spans
  below its own.
- **The store's refusals** are told as the triple-store interface tells them: a SHACL refusal by
  the media type of GraphDB's validation report, `application/shacl-validation-report+...`, and a
  refusal for want of heap by GraphDB's words, "Insufficient free Heap Memory ... for group by
  and distinct", which GraphDB answers with HTTP 500 when its free heap is under its floor.

With `wrapper = false` the participants start as they are, the run has no spans, and its record
has the run kind `uc2-unwrapped`. A calibration pair, one configuration run with and without the
wrapper, gives the wrapper's own overhead (part 3).

### The completion matcher

From the load's start, the warm-up's, until the drain's end, the harness reads the run's ended
transports from the graph every `matcher_interval_seconds`: the WarehouseManagementSystem answers
a spawn when it accepts it, so the end of a transport comes from the graph. Its last read comes
after the graph's last read at the drain's end. The record's `matcher` holds the interval, each
read with its time, how long it took and how many ended transports it read, their number and
their seconds in all (the harness's own load on the store), and the end it saw first of each
transport, with the time of the read that saw it.

### The measures

The record's `measures`, for a run that completed:

- every store request of every participant, with its time, latency, size, outcome, the store's
  reason of a refusal, the statuses it writes, whether the store applied it, and its text, those
  of the participants' start and of the warm-up included, because the store-only baseline replays
  them; and each commit of the mapper, with its latency and outcome;
- over the measured window only, from the warm-up's end to the window's end: the store requests,
  those refused (`refused_in_window`), those that timed out (`timed_out_in_window`) and those whose
  connection dropped (`connection_dropped_in_window`), and the share of the three together
  (`refused_or_unanswered_share`); the completed writes, the writes the
  store applied, each checked write counted once, by its update and not by the removal of its
  receipt, and their number per minute; the latencies of the commits the store admitted (every
  commit is kept with its outcome); the refusals for want of heap (all of them are listed, each
  with whether it came in the window); and the transports ended per minute, the ends the matcher
  saw in the window;
- **"timed out" and "connection dropped" are two outcomes** of a store request that got no answer
  (interim names). A request whose client gave
  up because the time ran out is `timed out` (requests' and httpx's `ReadTimeout`,
  `ConnectTimeout`, `WriteTimeout`, `PoolTimeout`); one whose connection closed, reset or failed
  before an answer is `connection dropped` (requests' `ConnectionError`, whose message names
  `RemoteDisconnected` for a connection the store closed; httpx's `RemoteProtocolError`,
  `ReadError` and `ConnectError`): a connection that the host refused is connection dropped, not
  refused, which is an answer of the store. The type of the error, the part before its first colon,
  decides (`store_requests.unanswered_outcome`); an error of neither class counts as connection
  dropped, and the report lists it with its error. By the same rule a request without an answer
  whose record holds no error is connection dropped too, in a new record and in an earlier one
  alike. Both count toward the 1 % of the broken rule of
  the ramp and of the replay, whose signal is `refused or unanswered`. A record written before
  keeps `timed out` for each request without an answer, with its error; the readers (`summary`,
  the table, the report) classify such a request by its error, and never change a record file. So
  the four unanswered requests of iteration one, dropped connections of 2 to 9 ms,
  count as connection dropped.
- the spawns of the measured window, wrapper or not (interim names): `spawns_offered_in_window`, the
  spawns the WarehouseManagementSystem spawned or refused, `spawns_accepted_in_window`, those it
  spawned, and `spawns_accepted_share`, accepted over offered (null where none was offered). They
  come from the WarehouseManagementSystem's time-stamped output lines, each counted by the time it
  arrived; a box's name is not read. The record's `spawns` still counts the whole run, the warm-up
  included.

A run without the wrapper holds only the measures that need none: the window, the transports
ended per minute and the spawns of the window, beside the matcher's reads, the transports after
the drain and the refused spawns.

### The checks

A run that fails a check is invalid (`valid` is false), and `failed_checks` names each check it
failed, with what failed:

- `one accepted done or failed`: each Operation that the graph shows ended after the drain has
  exactly one accepted `done` or `failed`;
- `at most one accepted running`;
- `no done before its running`: no accepted `done` comes before an accepted `running`. An
  Operation may have no accepted `running`, as "at most one" allows: a transport that the
  WarehouseManagementSystem ends `failed` while it is still queued has none, and a `running` whose
  answer timed out is not known to be accepted;
- `the matcher saw the graph's ends`: the transports that the matcher saw end are those that the
  graph shows ended after the drain;
- `deadlock alarm`: a module's output has a log message that starts with "possible deadlock:",
  after the level and the logger of the participants' log format, that arrived from the load's
  start to the drain's end. The check names the module and the alarm's line as written, which
  names the modules asked; a deadlock alarm makes the run useless.

An accepted write is one the store accepted and applied: the triple-store interface sends a
checked write with a receipt, and a write whose receipt the store did not find wrote nothing. A
refused write that its participant sent again counts once. The first three checks need the spans,
so a run without the wrapper makes only the last two; `checks_made` lists them. An Operation still
open after the drain counts as stuck, with its place (`stuck`), and that alone does not make a run
invalid.

## The sweep

`jms-eval-sweep` runs the runs of a **spec** one by one, each a run of one configuration, as
`jms-eval-uc2` makes it, and skips a run whose record exists, so that a sweep continues in a later
time window.

```sh
uv run jms-eval-sweep src/jms_kapps/evaluation/specs/iteration-one.toml --list
uv run jms-eval-sweep src/jms_kapps/evaluation/specs/iteration-one.toml --runs runs/iteration-one --repetitions 1
```

It needs what a run needs (above), and runs from a checkout of jms_kapps in the same way.

### The spec

A plain TOML file of **sweep blocks**, `[[sweep]]`, with defaults of its own, `[defaults]`:

```toml
[defaults]
boxes_per_minute = 30
warm_up_seconds = 60
window_seconds = 300
drain_bound_seconds = 300
matcher_interval_seconds = 2
transit_seconds = 3.0
retry_seconds = 1.0
heavy = false
repetitions = 5
pick_base_seed = 20261012       # optional, see "The pick seed of a sweep" below
series = "before"               # optional, see "The series" below

[[sweep]]
topology = ["cross", "grid"]
prefill = ["{topology}-1k.ttl", "{topology}-1M.ttl"]
```

- A block's keys are those of a configuration, and `repetitions`, each with one value or a list of
  values. A block is the **cross product** of its lists, the first key varying slowest; a block
  with no key is one configuration, of the defaults alone.
- **Precedence:** the command's defaults, then the spec's `[defaults]`, then its block, then the
  command's `--set KEY=VALUE` flags. The command's defaults are the strategies `real`, SHACL on,
  history collection off, no prefill, the wrapper on, the transit time 3.0 s, the retry wait 1.0 s
  and 1 repetition; every other value comes from the spec or a flag. A flag's value reads as TOML (a number, `true`, `false`, a quoted text), or else as the
  text it is.
- **The configurations** are the union of the blocks, in the listed order, with no duplicates: a
  configuration that a later block lists again keeps its first place, and is not run twice. One
  listed again with another number of repetitions is refused. So a spec lists the configurations
  it runs first in blocks of their own, and its design after them.
- **Refused before the first run:** a spec in which a configuration misses a value, naming the
  value; a key a spec does not know; and, among the selected configurations that have a run to
  make, one whose topology file, prefill file or strategy is not found, or one
  with the wrapper where OpenTelemetry, the extra `evaluation`, is not installed. A heavy run
  needs an approval that covers it (above): the sweep's
  option `--heavy-run-approval` gives the approval file, and the sweep then runs every heavy
  configuration of the approved spec, ramps and sustained loads alike, in the approval's period.
  A heavy run that no approval covers at its start, outside the period, of another spec or a
  changed spec file, or with `--set` overrides, is skipped and listed (see "Selection, records and
  stops").
- **The series** (interim names). The configuration numbers of two specs
  point to different configurations: configuration 7 of the iteration-one spec is the 30-module
  grid at 10M, configuration 7 of the "after" spec the 60-module grid at 1k. So a spec names its
  series in its `[defaults]`, `series = "before"`, a text that is not empty, and the report
  (`report.md`, its client load too), the table (`table.md`), the chart titles and labels, and the
  sweep's lines (`--list` and each run line, a replay level's lines too) read "<series> <number>",
  "before 7" and "after 7"; a chart label
  that joins the number and the run kind reads "after 7 unwrapped". A spec without the name takes
  its file name without the extension. The series is no key of a configuration and no part of its
  key, so the records match by their key as before, those of the "before" series by their legacy
  key too (below). The CSV files of the report and `table.json` keep the number and gain a field
  `series`; the run folders, the record files, `--configurations`, `--remove`, `--put-back` and
  `--repeat` stay by number. The shipped specs name theirs: `specs/iteration-one.toml` "before",
  `specs/after-series.toml` "after", `specs/validation.toml` "validation" and
  `specs/calibration-cross-short.toml` "calibration". The name is a line of each shipped spec file,
  so it is part of the file's SHA-256; an approval covers one spec as written.
- **Replay factors** (interim names). A block may list
  `replay_factors = [1, 2, 4, 8]`, numbers from 1: after the UC2 runs of each configuration of the
  block, the sweep replays the first valid UC2 run of that configuration with the store-only
  baseline (below), once per factor, in rising order, each on a repository of its own. The factors
  are a setting of the block, outside the configuration's key and no axis of its cross product; a
  configuration that a later block lists again gets the factors of both. A configuration with
  factors has the wrapper on, since a replay sends the store requests that only a run with the
  wrapper keeps. Every factor above 1 is a heavy run (above). See "Replay levels in a sweep".
- **Paths:** a topology file is read from the spec's folder, as a configuration reads its own. A
  prefill file is read from the prefills folder, `--prefills` (default: `prefills`, from the
  working directory), since a file of 10M statements is no file to keep beside a spec. A prefill
  is for one topology, so a prefill may name its topology with `{topology}`: a shipped
  topology's name, or a topology file's name without its suffix (`../topologies/grid-60.json`
  gives `grid-60`).

### Selection, records and stops

- `--configurations 1-9,12` and `--repetitions 1-3` select by number, from 1, in the order
  `--list` shows; without them every run of the spec is selected. The repetitions of one
  configuration run one after the other, the configurations in the spec's order. With the
  precision rule, the rounds choose the repetitions (below), and `--repetitions` narrows the
  rounds to those of its repetitions.
- **A run whose record exists is skipped.** Each record of a sweep says which run it is (`sweep`):
  the spec, the configuration's number in it, its key and the repetition of how many. The key is
  a digest of the configuration's values as the spec gives them, so a run is found again where the
  spec lists its configuration elsewhere, or where the checkout moved; keep each spec's runs in a
  folder of their own (`--runs`). **The key holds the transit time and the retry wait,** so a
  configuration's key differs from the key it had in the "before" series, and a sweep finds
  no run of the "before" series. That is intended: the "after" series starts fresh, in a runs
  folder of its own. The report and `jms-eval-table` still read the records of the "before"
  series (interim names): both match a record to a configuration in
  `record_match`. A record matches by the configuration's key, as before; or, for a record of the
  "before" series, by the legacy key (`spec.legacy_key`), the digest of the configuration's values
  without `transit_seconds` and `retry_seconds`, when the record's `run_settings` hold the
  configuration's transit time and retry wait and its `sweep.spec` names the spec being read, by
  file name. A record that matches neither way is of no configuration of the spec: the report
  counts it as left out. The readers never write a record file; the sweep's skip of existing
  records still keys by the configuration's key.
- **The pick seed of a sweep** (interim names). Repetition k of every
  configuration takes the seed derived from a base seed and k (a SHA-256 digest of both, so it is
  the same on every host and Python version): the spec's `pick_base_seed` in its `[defaults]`, a
  whole number from 0, or else the command's fixed default base seed, 20261012. So repetition k has
  the same seed in every configuration, and the repetitions of two configurations stay paired.
  The sweep writes the seed into each run's saved configuration as a run setting outside the
  configuration's key, and into its record. No block and no flag gives a base seed.
- **A run that a stop signal ended runs again.** Its record's `outcome` is `interrupted` (Ctrl+C,
  SIGTERM or a closing terminal's SIGHUP), and it is no measurement, so the next call makes that
  run again; a run that completed or failed is skipped, since a failure is a result. The
  interrupted attempt keeps its own folder and its record, as they are, beside the folder of the
  run made again: each run's folder is named by its own run id.
- **A failed run does not stop the sweep; 3 failed runs in a row do** (interim names). One failed
  run at 01:00 must not end the night. The sweep goes on after a failed run, and stops after 3
  failed runs one after the other, which still keeps a store that refuses every run from the rest of
  the spec; its message names the three runs. A run is failed where its outcome is `failed` or
  `failed (over time)`, a replay level too, or where the call cannot find its record below `--runs`
  after it made it: the call then leaves its configuration out for the rest of the call (a ramp's
  level: the whole ramp), so that it does not make the same run again all night. The call looks for
  the record twice: right after the run, in the run's own record file, and again when it chooses the
  next round, so that a record that was there after its run and is gone when the next round is
  chosen counts as a failed run the same way, at the end of its round. A failed run whose record is
  also missing says both: its outcome, and that its configuration is left out. A completed run whose
  repository the store did not delete counts in the statistics by its validity, but it counts toward
  the 3 and does not reset the count, so a server that leaves repositories behind ends the call
  after three such runs in a row. Any other completed run, valid or invalid, resets the count. A
  failed run ends its ramp or its replay, as before: no higher rate or factor is sent after a level
  that cannot be judged. **A stop signal still ends the call at once**: a run that a stop signal
  ended stops the call, and the next call makes it again. At its end the call names each of its runs
  that failed. The next call skips a failed run and goes on after it; to make a failed run again,
  move its folder out of `--runs`. A run that completed but whose repository the store did not
  delete is completed, and the line after the run names the repository. The second look for the
  record, between two rounds, guards the call where the records do not show a run that the call had
  just made: a run that it finds counts as a failed run, and the call goes on without its
  configuration, or stops where it is the third failed run in a row.
- **A heavy run that no approval covers is skipped and listed.** Each heavy run, a ramp's level, a
  sustained load and a replay level above the factor 1, is checked at its start against the approval
  (`approval.cover`), and one that the approval does not cover then is skipped, and the sweep goes
  on with the next run. No heavy run starts without an approval that covers it. A skipped level of a
  ramp or a replay skips its higher levels in that round, so a period that begins partway through a
  call never lets level 2 run after level 1 was skipped; under the precision rule a later round goes
  on with the ramp or the replay from its lowest level without a record, as with any unfinished
  heavy part. A replay level that cannot start (its recorded run cannot be replayed) is skipped and
  listed the same way. At its end the call lists each run it skipped, with the reason: no approval,
  another or a changed spec, a period that is not now, `--set` overrides, a lower level that was
  skipped, or why the replay level cannot start. A skip writes no record, so a later call whose
  approval covers the run makes it. A call that only skipped heavy runs exits as if they were not
  selected. So an approval that ends at 02:00 does not stop the normal runs after it.
- **The stop time and the stop file** (interim names). Before
  each run, a replay level too, the call checks its stop time, `--stop-at`, and the stop file,
  `STOP` in `--runs`. The stop time is a date and time with its zone, such as
  `2026-10-14T06:00+02:00` or `2026-10-14T04:00Z`; one without a zone, or one that has passed, is
  refused before the first run. At the stop time the call stops before its next run; a run that
  started before the stop time runs to its end. Where the stop file is there, the call stops
  before its next run and removes the file, so that the next call goes on; a stop file that is
  there before the first run stops the call before it. **A stop file never outlives its call:** a
  call that ends another way (a run that did not complete, the end of the rule, a round that
  cannot start or has nothing to make, Ctrl+C) removes a stop file that came meanwhile, and says
  so; such a file would otherwise stop the next call before its first run. Each end has its own
  message. A stop signal ends the call as before. Both hold for a spec of fixed repetitions too.
- **A person removes configurations at any run:** make the stop file (`touch <runs>/STOP`), wait
  until the call stops, remove the configurations with `--remove` (see "The precision rule and
  the rounds"), and call the sweep or `jms-eval` again. The next call goes on at the round that
  the records show, and the removed configurations keep their runs as they are. (A smaller
  `--configurations` leaves them out for one call only.)
- `--list` shows each configuration with its values, its number of repetitions and those that
  have a record, each with its outcome (`completed`, `failed` or `interrupted`), and runs nothing:
  it needs no store, and none of the files the spec names. For a spec with the precision rule it
  shows the rounds too: the round the next call starts at and, for each configuration, its runs
  made, its valid runs and whether it is open (below); and each heavy part with where it stands:
  no record yet, a repetition that has not ended, or the repetition that ended and how, with the
  `--repeat` value that asks for the next one (see "The heavy parts in the rounds").
- The command exits with 0 once every selected run has its record, or no selected configuration
  under the precision rule is open, or it stopped at the stop time or at the stop file, and every
  run it made completed; 1 when a run it made failed, when it stopped at a run that a stop signal
  ended, at the third failed run in a row, by a stop signal between two runs, or at a round with
  nothing to make; and 2 when it refused to start: nothing reached a store then. A heavy run that
  it skipped changes no exit code. `--remove` and `--put-back` run nothing and exit with 0.

### Replay levels in a sweep

(Interim names.) A block with `replay_factors` makes a **replay level** for
each factor, after the UC2 runs of each of its configurations. A level is one store-only baseline
(below) of the configuration's first valid UC2 run (the lowest repetition that completed and was
not found invalid), at that factor, in a fresh repository with the configuration's seed and
prefill. Its record is of the run kind `baseline`, and its `sweep.key` is the configuration's key
and the factor (`<key>@x4`), so a level has a record of its own, found again by a later call.

- **The rates, both per minute.** The offered rate is the number of the replayed window's requests
  over the window's length in minutes over the factor. The achieved rate is the number of
  answered requests, a refusal is an answer and a request that timed out or whose connection
  dropped is none, over the level's replay span in minutes, which runs from the level's window
  start to its last answer, the drain included. **The replay span is at least the window**
  (interim): the achieved rate uses the larger of the span and the window's length over the
  factor. So the achieved rate is never more than the offered rate, and 100 % means that the store
  kept up: a level whose every request is answered before the window's end achieves the offered
  rate, and one whose last answer comes after the window's end has the rate it had before. A level
  whose rate falls below 90 % only because of this bound names "achieved rate below offered" too;
  such a level has unanswered requests, so it is broken by more than 1 % refused or unanswered
  already, and no broken verdict changes. The record holds both rates in `replay_level`, with the
  factor, the requests, the refusals, the timeouts and the dropped connections in the window, the
  refusals for want of heap, the replay span as it is, from the window's start to the last answer,
  whether the level is broken, and the signals. (Read literally, the span would give a
  window of 300 s whose last answer came at 290 s, every request answered, an achieved rate
  of 103 % of the offered rate.)
- **Broken:** a level is broken when the store refuses a request for want of heap, when more than
  1 % of its window's requests are refused or unanswered (`refused or unanswered`: refused, timed
  out or with their connection dropped), or when the achieved rate is below 90 % of the offered
  rate. A replay has no commits, so the ramp's commit-latency and plateau signals do not apply. The
  thresholds are judgement settings outside the configuration's key.
- **The sweep stops the replay of a configuration at its first broken level**: the higher factors
  are not run, in this call or a later one, and the sweep goes on with the runs after them. A level
  that failed ends the replay: no higher factor is sent after a level that cannot be judged; it
  counts toward the 3 failed runs in a row, as any failed run does, and the sweep goes on. A level
  that a stop signal ended runs again.
- **Heavy:** every level above 1 needs the approval of the spec, as a ramp does (above). The sweep
  checks each such level at its start, and skips and lists one that no approval covers then, with
  the replay's higher levels. A level at the factor 1 needs none.
- **The rounds of the precision rule** make a replay one time, in round 1, right after its
  configuration's first valid run, and again on request (see "The heavy parts in the rounds"). A
  level's record says the replay's repetition in `sweep.repetition`, and every repetition replays
  the run that the first one replayed.
- **A configuration with no valid UC2 run** to replay keeps its levels, and the sweep says so. In
  a spec of fixed repetitions they wait for a later call, and the call exits with 1. Under the
  precision rule they run right after the configuration's first valid run, in the round that
  makes it; the call exits with 1 only where they still wait at its end.
- `--list` shows each configuration with factors and a line for its levels, with whether each is
  heavy or at normal load and its record. The table keeps the levels apart (below), and the
  repetitions of a replay too.

### The iteration-one spec

`specs/iteration-one.toml` holds the design of the first benchmark, with the run
timing of the run task as its defaults: a box rate of 30 boxes per
minute, which is not heavy, a warm-up of 1 minute, a measured window of 5 minutes, a drain bound of
5 minutes, a matcher interval of 2 seconds, a transit time of 3 s and a retry wait of 1 s. These are data in the spec, so iteration two changes
them without a build.

- **The design, three blocks, 24 configurations:** topology (the 5-module cross, `cross`, and the
  grids of 30, 60 and 120 modules) × store size (about 1k, 100k, 1M and 10M prefill statements),
  at `real` with SHACL on (16); SHACL off × the four store sizes on the 30-module grid (4); and
  `southward` × the four store sizes on the 30-module grid (4).
- **The history-on comparison** at the centre: 1 configuration.
- **Each configuration runs 5 times:** 125 runs. (The "after" series runs until a precision rule
  holds instead: `specs/after-series.toml`, below.)
- **The order:** the 9 configurations near the centre come first, so that a short week still gives
  a complete one-factor set: the centre itself (the 30-module grid, 1M, `real`, SHACL on); the
  cross, the 60-module and the 120-module grid, each at 1M; 1k, 100k and 10M, each on the
  30-module grid; SHACL off, and `southward`, each on the 30-module grid at 1M. The history-on
  comparison comes 10th, then the rest of the design.

**Files it names.** The spec names the grids, which are committed, and the prefill files, which a
person makes; the expansion, `--list` and the tests need none of them, and a run refuses a file
that is not there before anything reaches the store.

- The 30-module grid is the shipped `grid`, and the cross the shipped `cross`. The **60- and
  120-module grids** are committed as `src/jms_kapps/evaluation/topologies/grid-60.json` and
  `src/jms_kapps/evaluation/topologies/grid-120.json` (the spec names them as
  `../topologies/grid-60.json` and `../topologies/grid-120.json`). They are the 12 x 5 and 24 x 5
  grids that the grid script makes (interim), and a test checks that
  each file is what the script makes:

```sh
python -m jms_kapps.evaluation.grid --width 12 --height 5 \
  --output src/jms_kapps/evaluation/topologies/grid-60.json
python -m jms_kapps.evaluation.grid --width 24 --height 5 \
  --output src/jms_kapps/evaluation/topologies/grid-120.json
```

  The pattern is the shipped grid's, which 6 x 5 reproduces byte for byte: row 1 holds the entry
  modules, which link south; rows 2 to H-1 link south and alternate west and east; row H holds the
  exit modules, chained east to the last module, which has no connection. Modules are numbered
  along the rows. Every module is reachable from an entry module and reaches an exit module, and
  UC2's load check accepts each file (a test shows both). `--width` is 1 or more and `--height`
  2 or more.
- The **prefill files**, `<topology>-<size>.ttl` in the prefills folder: 16 of them, one for each
  topology and store size, about 2 GB in all (a 10M file is about 0.4 GB and takes about a minute
  to write). `jms-eval` makes the missing ones before its first run (above). By hand, from the
  checkout's root:

```sh
mkdir -p prefills
for size in 1k 100k 1M 10M; do
  for topology in cross grid; do
    uv run python -m jms_kapps.evaluation.prefill --topology "$topology" --size "$size" \
      --seed 1 --output "prefills/$topology-$size.ttl"
  done
  for grid in grid-60 grid-120; do
    uv run python -m jms_kapps.evaluation.prefill \
      --topology "src/jms_kapps/evaluation/topologies/$grid.json" --size "$size" \
      --seed 1 --output "prefills/$grid-$size.ttl"
  done
done
```

**The prefill writes no module possession states** (iteration one, a first benchmark fast): only
the WarehouseManagementSystem's one state, which every past box points at. The first box onto each
module creates that module's state in its commit, 2 statements, about 60 on the 30-module grid, as
in a run without a prefill. So the store is not warm in that respect: the first box onto a module
in each run writes 2 statements more than the boxes after it.

**Over two time windows.** A run takes about 12 minutes: the start, a warm-up of 1 minute, the
window of 5, a drain of up to 5 and the end. The 125 runs of this spec take about 25 hours, and the
5 repetitions of each configuration are spread over at least two time windows. For
a spec of fixed repetitions that is two calls, for example repetitions 1 to 3 in the first window
and 4 and 5 in the second, with `--repetitions`. A spec with the precision rule (below) does it by
itself: a round is a time window, and the 10 valid runs of a configuration come from at least 10
rounds.

A call that stops, at the third failed run in a row, at a run that a stop signal ended, by Ctrl+C,
at its stop time or at the stop file, is called again with the same arguments in the next time
window: it skips each run that completed or failed, makes the interrupted one again, and goes on. `--list` shows how far a sweep is, with each
run's outcome.

### The precision rule and the rounds

Interim names. A spec with a `[precision]` table does not give its
configurations a number of repetitions: each runs until the rule holds.

```toml
[precision]                     # every key is optional; these are the defaults
latency_tolerance = 0.10        # the bootstrap 95 % interval of the pooled commit p95: within 10 %
throughput_spread = 0.10        # the IQR of the transports ended per minute: at most 10 % of the median
minimum_runs = 10               # valid runs needed, from 2
maximum_runs = 20               # runs made at most
```

- **The rule.** A configuration meets it when it has at least 10 valid runs, both ends of the
  bootstrap 95 % interval of its pooled commit p95 lie within 10 % of the p95, and the IQR of its
  transports ended per minute is at most 10 % of their median. The numbers are those of the
  configurations table for the same runs (`table.rule_numbers` calls the table's own functions
  with its defaults, seed 0 and 10 000 resamples), so the rule has no second version of a
  statistic. A configuration without the wrapper (`uc2-unwrapped`) has no commit latency and is
  judged on the throughput alone. A median of 0 meets no spread.
- **The rounds.** A round makes one run of each selected configuration that is still **open**, in
  the spec's order: round k makes repetition k. A configuration is open while it does not meet the
  rule, has made fewer than 20 runs, and has no throughput median of 0 at its minimum of valid runs
  or later (`rounds.is_open`). A round is a time window: with the 26 cells of the draft spec it
  takes about 5.2 hours, and the 10 valid runs of a configuration come from at least 10 rounds, so
  from at least two time windows; the rule refuses a `minimum_runs` below 2. Repetition k keeps the
  pick seed of repetition k, so the repetitions of two configurations stay paired. A configuration
  whose run of a round was not valid is open in the next round, as any open configuration.
- **One call goes on round after round**, with no person present, until one of its ends: its stop
  time (`--stop-at`), the stop file (`STOP` in `--runs`), or the end of the rule, where each
  selected configuration meets the rule, has made 20 runs or stopped at a throughput median of 0.
  It checks the stop time and the stop file before each run (see "Selection, records and stops").
  A stop signal ends the call as before, and so does the third failed run in a row; a failed run
  alone does not.
- **The calls.** The state is in the records: the round of a call is the lowest round that has a
  run without a completed or failed record among the open selected configurations, so a call
  after a stop goes on at the round and the repetition that the records show, and an interrupted
  attempt is made again. A configuration with more records than the others is planned in the round
  of its next repetition. `--repetitions` narrows the rounds to those of its repetitions, so
  `--repetitions 1` makes at most one run of each selected configuration, and the call ends when
  they have a record. A round that plans runs whose records the rule cannot read (a record that
  names its run but has no run kind, for example) has nothing to make: the call ends with its own
  message and exit code 1, and names the records to repair or move away.
- **Removing configurations, and putting them back** (interim names). After some rounds a person
  removes the cells that are less important: `jms-eval-sweep <spec> --runs <runs> --remove 7-14`
  records them by their keys in `removed-configurations.json` in the runs' folder and runs nothing.
  Every later call of `jms-eval-sweep` and of `jms-eval` leaves them out, whatever it selects, and
  their runs stay as they are; a running call reads the file before each round. `--list` shows each
  as `removed since <time>`, and each call names them before its first run. `jms-eval-sweep <spec>
  --runs <runs> --put-back 9` puts configuration 9 back: the next round plans it again. To remove
  cells from a running call at once, make the stop file first. (A smaller `--configurations` leaves
  them out for one call only, and `jms-eval` has no selection.)
- **The counting.** Only valid runs count toward the rule and the minimum. Every run made counts
  toward the 20, valid or not, a failed run too. An attempt that a stop signal interrupted
  (`interrupted`) does not count, and the next call makes that repetition again. A configuration
  that has made 20 runs without meeting the rule stops, and the table marks it "precision not
  reached (20 runs)".
- **A throughput median of 0** (interim names). A median of 0 meets no spread, so such a
  configuration can never meet the rule. Thus a configuration whose throughput median is 0 after
  its minimum of valid runs (10) stops there: it is not open, and no later round plans a run of
  it. The throughput is the transports ended per minute, the number that the rule judges, and the
  median is that of the valid runs. The rounds judge it when the configuration has its minimum of
  valid runs, and again after each later run. The table and `--list` mark it "stopped:
  throughput median 0", apart from "precision not reached"; a configuration that made its 20
  runs before the stop, with a median of 0, has the same mark. The half of a calibration pair
  without the wrapper is judged on the throughput alone, so the same stop applies to it. A
  configuration whose throughput median is above 0 is judged as before. The stop is no setting of
  the spec and changes no key. A stopped configuration runs again only when its records are
  moved out of the runs' folder, for example after its cause is fixed: while they are there, no
  round plans it.
- **The records.** A record made under the rule says, in `sweep`, `repetitions` 20 (the maximum:
  "of how many") and `round`, the round that made it. A record made in a pass before the rounds
  keeps its `pass`, and its run counts as its repetition in the rounds: no key of a configuration
  changed, so the runs that the "after" series made in passes stay valid.
- **The scope.** The rule applies to the configurations at normal load. A ramp's levels, a heavy
  configuration and the replay levels are heavy parts, which the rounds make one time each, in
  round 1, and again on request (see "The heavy parts in the rounds", below); a spec that has the
  rule and gives `repetitions` to a configuration at normal load is refused, and so is one that
  gives a heavy configuration `repetitions` other than 1. A call that selects only heavy parts
  makes only them.
- **`--list`** shows the rounds, the round the next call starts at, and for each configuration its
  runs made, its valid runs and whether it is open; one that is not open says why: `met`,
  `precision not reached (20 runs)`, or `stopped: throughput median 0`.
- **The table** (`jms-eval-table`) has a section "Precision rule" for such a spec: for each
  configuration the runs made, the valid runs, the rounds they were made in, and the rule's
  status. A run made before the rounds shows its pass, as its record says (`pass 1`).
- **There are no passes.** A `[precision]` table that still gives `pass_sizes` is refused, with a
  message that names the rounds.

`specs/after-series.toml` is the **draft spec of the "after" series**: the cells
of the iteration-one spec under the rule, the calibration pair first (the centre with the wrapper
and without it), then the order of the product owner of the tests: SHACL off at each store size, the
60- and 120-module grids, the history-on comparison, the rest, and `southward` last. 26
configurations. Its header states what stays open: the values of the transit time and the retry
wait, and the baselines that need the compressed replay. An approval covers one spec as written, so
a heavy run of it needs an approval issued for the file as it is. One call runs the rounds, for
example until the next morning:

```sh
uv run jms-eval-sweep src/jms_kapps/evaluation/specs/after-series.toml --list --runs runs/after-series
uv run jms-eval-sweep src/jms_kapps/evaluation/specs/after-series.toml --runs runs/after-series \
  --stop-at 2026-10-14T07:00+02:00
touch runs/after-series/STOP   # stops the call before its next run, which removes the file
uv run jms-eval-sweep src/jms_kapps/evaluation/specs/after-series.toml --runs runs/after-series \
  --remove 7-14                # later calls leave these cells out; --put-back brings one back
uv run jms-eval-table src/jms_kapps/evaluation/specs/after-series.toml --runs runs/after-series
```

### The heavy parts in the rounds

Interim names. The heavy levels
are in the same spec as the configurations at normal load, inside the rounds, under the one
approval of the spec for its period. A **heavy part** is one of three:

- a **ramp**, named by its block: its levels in rising order of their box rates;
- a **replay**, named by its configuration's number: the levels of its first valid UC2 run, one
  for each factor in rising order. A replay at the factor 1 runs at normal load, but it follows the
  same rules;
- a **sustained load**, named by its configuration's number: a heavy configuration that is no
  ramp's level, one run. A heavy configuration with replay factors is a sustained load and a
  replay.

The rules:

- **Each heavy part runs one time, in round 1**, at its place in the spec's order: a ramp at its
  block's place, its rates rising, until it ends by its rule (see "A ramp: where the store
  breaks"); a sustained load at its configuration's place; a replay right after its
  configuration's first valid run, its factors rising, until its first broken level. A
  configuration without a valid run in round 1 keeps its replay: it runs right after the
  configuration's first valid run, in the round that makes it. Then a person reads the result and
  decides if a repetition is necessary, and how many: the aim is that the statement of the paper
  converges.
- **A later round makes no level of a heavy part that ended**: a ramp that broke, stopped at a
  level that cannot be judged or reached its top level; a replay that broke, failed or reached its
  highest factor; a sustained load with a record. One that a round left **unfinished** (a stop, a
  run that did not complete, a level skipped for want of an approval) goes on in the next round
  from its lowest level without a record.
- **A repetition on request.** `--repeat` of `jms-eval-sweep` and of `jms-eval` names each heavy
  part with a target repetition: ramps by their block (`block3:2`), replays and sustained loads by
  their configuration's number (`7:2`, or a range such as `7-9:2`); for a heavy configuration with
  replay factors the number names both. It makes sure that repetition exists: while the part's
  last repetition ended below the target, the next round makes one more, a ramp again from its
  lowest rate, a replay again from its lowest factor, each until it ends by its rule; a higher
  target makes one more in each round. A target at or below a repetition that exists or is under
  way makes nothing, so a restart with the same command line is safe and goes on where the last
  call stopped. A repetition has its own number in its records (`sweep.repetition` 2, then 3), and
  its runs take that repetition's pick seed. The call says what each target makes before its first
  run.
- **A repeated replay replays the same run** as its first repetition did, so the repetitions show
  the store and the time of day, with the same input.
- **Refused before the first run:** a request in a spec without the precision rule, one without
  a target repetition, one that names a block that is no ramp, a ramp's level by its number, or a
  configuration at normal load without replay factors, and one for a heavy part or a target
  repetition that `--configurations` or `--repetitions` leaves out; and a heavy configuration of a
  spec with the rule that gives `repetitions` other than 1. A request for a removed configuration
  leaves it out and says so.
- **A target whose next repetition `--repetitions` leaves out makes nothing in that call.** The
  check is against the repetition that the next round makes, not only against the target:
  `--repeat 6:3 --repetitions 3` after repetition 1 of part 6 would need repetition 2 first. The
  call says so in one line that names the part and the repetition left out, makes nothing for that
  part, and goes on with no refusal, so that a restart with the same line stays safe.
- **Every heavy level still runs only under an approval that covers it.** A repetition on request
  is covered exactly as the first one, at the start of each level; `jms-eval` without an approval
  skips its heavy levels and lists them, and a level that the approval does not cover at its start
  is skipped and listed, and goes on in a later round. No configuration key changes: a repetition
  has the key of its configuration, or of its level.
- **`--list`** shows each heavy part with where it stands and, for one that ended, the `--repeat`
  value that asks for its next repetition.
- **The table** shows the first repetition in its cells: the `baseline` and `baseline xF` columns
  and the row of a ramp's level hold repetition 1 only, the one the ramp comparison uses. The
  later repetitions appear apart: the section on ramps has each repetition of a ramp with its own
  judgement, compared with the replay's first repetition, and a heading that says how many the
  ramp has; the section "Replay levels" lists the levels by repetition and says how many each
  replay has.

## The table of a spec

`jms-eval-table` turns the records of a spec's runs into its numbers: one table, written beside
the records. It
needs no store, runs nothing, and may be made again at any time, as more runs have their records.

```sh
uv run jms-eval-table src/jms_kapps/evaluation/specs/iteration-one.toml --runs runs/iteration-one
```

- The spec, and `--runs` and each `--set` as the sweep was given them: a run is found by its
  configuration's key, which the flags change.
- `--seed` is the bootstrap's seed (default: 0), and `--resamples` how many resamples each
  interval draws (default: 10 000). The table records both; the same seed gives the same
  intervals.
- It writes `table.json`, the table, and `table.md`, the same for a person to read, into the
  `--runs` folder.

### What the table holds

**One row for each configuration at its box rate, its load level.** The configurations that
differ only in the wrapper are one row, and the run kind tells them apart: each row has the
columns of `uc2`, with the wrapper; `uc2-unwrapped`, without it; and `baseline`, the store-only
baseline that replays a run of the row, found by the run it names. A run kind with
no run that counts leaves its columns empty. **The replay levels have columns of their own**
(interim names): the `baseline` columns are the baselines at the factor 1, and those
of an earlier record, which says no factor, as they were; each factor above 1 has the same columns
under `baseline x2`, `baseline x4`, and so on (`replays` of the row in `table.json`). The section
"Replay levels" of `table.md` lists every baseline that says its level, by the repetition of the
replay and in rising order of the factor: the offered and the achieved rate per minute, the
requests, those refused, those that timed out and those whose connection dropped,
the refusals for want of heap, and the signals that broke the level, each by its name now: a level
recorded before the split of the two outcomes keeps `refused or timed out` in its file, and the
table shows `refused or unanswered` for it as for a new one. The `baseline` and `baseline xF`
columns hold the first repetition of a replay only, the one the ramp comparison uses. The section
says how many repetitions each replay has (`replay_repetitions` of the row, and `repetition` of each
level, in `table.json`; interim names).

**Which runs count.** A run counts when it completed and its record does not say it is invalid.
The table lists every run left out, with why: invalid, with the checks it failed; failed, with
why; or interrupted, which the sweep makes again. A baseline of a run left out is left out too. Every number below comes from the runs that
count and from their measured window alone, which is all that a baseline measures.

- **Latency:** the commit latency, of the commits the store admitted, and the store-request
  latency, of the store requests the store accepted, logins left out since a baseline replays
  none. Each as the pooled p50, p95 and p99 of the runs' latencies, with a bootstrap 95 %
  interval. The bootstrap resamples whole repetitions: one resample draws as many runs as
  counted, with replacement, and pools their latencies; the interval is the 2.5 % and the 97.5 %
  quantile of the resamples' quantiles. A single run gives an interval of one point. Every
  quantile is read by linear interpolation between the two closest ranks (as numpy reads it by
  default).
- **Throughput:** the completed writes per minute and the transports ended per minute, each as
  the median and the interquartile range of the runs.
- **The store:** the store requests of the window, those refused, those that timed out and those
  whose connection dropped, apart (`refused_in_window`, `timed_out_in_window`,
  `connection_dropped_in_window` of a cell in `table.json`, and the section "Store requests refused
  or unanswered" of `table.md`), the three together and their share
  (`refused_or_unanswered_in_window`, `refused_or_unanswered_share`), and the
  refusals for want of heap under the load, in the warm-up, the window and the drain: a
  refusal while the load is on or drains is a signal of the store at that load; one before the
  load's start, as at the participants' start, says how large the store is. A baseline has
  neither a warm-up nor a drain, and the requests it sends before its window are no load: its
  window alone counts.
- **The SHACL overhead** of a row with SHACL on: its mean commit latency less that of the row
  that differs from it only in SHACL, off.
- **At each load level:** each stuck box, with its place and status; each failed box, with its
  place and reason; and each refused spawn, with its reason and its origin or destination where
  known; each with its run.
- **The completion matcher's reads:** their number per run, their interval and their time in all
  per run, the harness's own load on the store.

### A calibration pair: the harness's own overhead

A **calibration pair** is one configuration run twice: with the wrapper (`uc2`) and without it
(`uc2-unwrapped`). In a spec it is a block with both values of `wrapper`:

```toml
[[sweep]]
topology = "grid"
prefill = "{topology}-1M.ttl"
wrapper = [true, false]
```

The table compares the pair in its row by the measures that need no wrapper, the transports ended
per minute and the refused spawns per run, each with the difference and its share, which give the
wrapper's own overhead; beside them, the matcher's reads of both. The calibration pairs of the
measured runs run on the same hardware and the same versions as those runs, so that the overhead is
that of those runs. `specs/calibration-cross-short.toml` is a short calibration pair on the 5-module
cross, at the short run's values, which checks that a pair and its table work end to end; it is no
measurement:

```sh
uv run jms-eval-sweep src/jms_kapps/evaluation/specs/calibration-cross-short.toml --runs runs/calibration-cross-short
uv run jms-eval-table src/jms_kapps/evaluation/specs/calibration-cross-short.toml --runs runs/calibration-cross-short
```

### A ramp: where the store breaks

A **ramp** is a sweep block marked as a ramp, `ramp = true`, whose only axis is its box rates,
rising, and which runs each rate once:

```toml
[[sweep]]
ramp = true
topology = "grid"
prefill = "{topology}-1M.ttl"
boxes_per_minute = [10, 20, 40, 80, 160, 320, 640]
repetitions = 1
```

The run task's ramp doubles the rate from 10 boxes per minute, one window for each step. Each rate
is one **load level**. A ramp's runs are heavy: its configurations take `heavy = true` whatever the
spec's defaults say, and a ramp that its block or a flag says is not heavy is refused. It runs with
the wrapper, since the broken rule reads the measures that need it. A ramp's configurations are its
own: one that another block lists too is refused, and so is a ramp with another axis, with rates
that do not rise, or with more than 1 repetition.

**A ramp runs only under an approval that covers its spec,** which any person may give, in
agreement with the server's admins (above). The sweep's option `--heavy-run-approval` gives the
approval file; the record of each of the ramp's runs keeps the whole approval. The option is off
by default, and no test, CI job or example here gives an approval that covers a real spec. Without
an approval that covers it, a selection with a level of a ramp to make is refused before its first
run. One approval covers the ramps of its spec for its period, so a selection with levels of
several ramps to make runs under it. A selection with a level of a ramp whose lower levels have no
record and are not selected is refused, since a ramp judges each rate before the next. Select a
ramp's levels with `--configurations`, from its lowest level without a record, as in `jms-eval-sweep
<spec> --runs <folder> --configurations <the ramp's numbers> --heavy-run-approval <the approval
file>`.

**The broken rule.** A load level is broken at the first store-side signal, from the measured
window of its runs, but the refusals for want of heap, which count under the whole load, the
warm-up and the drain included:

- `heap refusal`: a refusal for want of heap;
- `refused or unanswered`: more than 1 % of the store requests refused, timed out or with their
  connection dropped;
- `p95 commit latency`: a p95 commit latency more than 10 times its value at the ramp's lowest
  rate.

A fourth signal, `plateau` (the completed writes per minute rose by less than 5 % from the rate
step before), is still found and shown in the record of the level, but it breaks no level and stops
no ramp. Stuck or failed boxes alone never break a level; the table counts them at each level,
beside it.

**UC2-limited.** A level is **UC2-limited** when fewer than 90 % of the spawns offered in its window
are accepted. The offered spawns are the spawned and the refused ones, the accepted share is the
spawned over the offered (the record's `spawns_offered_in_window` and the others, above), and
the threshold, 0.9, is a judgement setting outside the configuration's key (`ramp.ACCEPTED_SHARE`).
A UC2-limited level breaks nothing and stops nothing: the WarehouseManagementSystem was the limit
there, and the store's limit lies higher.

**The end of a ramp.** The sweep runs a ramp one rate per run, in rising order. After each run it
judges the level, and it stops the ramp at the first level broken by a heap refusal, by more than
1 % refused or unanswered, or by the commit p95: its higher rates are not run, and the sweep goes on
with the runs after them. A ramp none of whose levels broke runs up to its top level. The table
reports the level before the broken one as the ramp's **scale**; a ramp broken at its lowest rate
has none. A ramp also stops at a level that has a run but none that counts, a failed or an invalid
one: that level cannot be judged, so no higher rate goes to the store, and the table gives no scale.
A ramp that ended in an earlier call sends no higher rate in a later one. **In a spec with the
precision rule** a ramp runs one time, in round 1, at its block's place, and again on request
(`--repeat block3:2`): each repetition starts again from the lowest rate, ends by the same rule, and
is judged apart, from its own runs (see "The heavy parts in the rounds"). The table's section on
ramps shows each repetition of each ramp, with how many the ramp has: each level judged, with its
numbers, the signals it showed and those it could not show for want of a number (no commit at the
lowest rate, say), where the ramp ended and why. The row of a ramp's level in the other sections
holds its first repetition only.

(Interim names.) The plateau breaks no level: UC2 itself can be the limit, as the
WarehouseManagementSystem was at 10M in iteration one, and a UC2-limited level usually plateaus,
so a ramp that stopped at a plateau would stop where the plateau says nothing about the store or
KAPPS.

**The judgement of a ramp.** The table gives each ramp one of three judgements:

- *UC2-limited at level X*: the first UC2-limited level comes before the first broken level, or no
  level broke;
- *broken at level Y (signal)*: a level broke before any UC2-limited level;
- *cannot tell*: neither happened (only a plateau, or the top level reached, or records without the
  spawn counts), or both happened at the same level.

**The comparison with the replay.** Beside UC2's achieved store request rate per minute at the
ramp's first UC2-limited level, the table puts the offered store request rate at the first level
where the store-only baseline's replay of the matching UC2 configuration broke (below). The matching
configuration is the one that is no ramp's level and has the same topology, prefill, SHACL setting,
module strategy, WMS strategy and history collection; where several match, the first in spec order
counts. Where none matches, or the matching one has no replay level that counts, the table says
"no replay"; where the replay broke at no level, it says "not broken up to factor F", with F the
highest factor replayed. Each repetition of a ramp is compared with the replay's first repetition
(an interim reading). A record made before the spawn counts holds none: its level is neither
UC2-limited nor not (`uc2_limited` is null), and the ramp cannot be told UC2-limited from it. A ramp
that has not run all its levels is judged on the levels it has, so its judgement may change when the
higher levels run. In `table.json` the ramp holds `judgement`, `uc2_limited_at` and `comparison`.

**The licensed cores** (interim names). Beside the
replay's first broken level, the comparison gives the licensed cores of the server: GraphDB
Enterprise runs at most one concurrent query for each licensed core (its license is priced per CPU
core, "with each core supporting one concurrent query"). The compressed replay is an open loop, so
its first broken level can be this limit of the license, not the capacity of the store; on
2026-10-12 the server's license view showed GraphDB Enterprise with 4 licensed cores. Each record
keeps the license view whole, as the server answered it (`server.license`, read once per run, see
"The server watch"); the licensed cores are its answer's maximum of CPU cores (`maxCpuCores`),
from the records of the replay level that the comparison uses: the first broken level, else the
highest level. Records of that level with different values show each value, in rising order;
where none of them has the view, the read failed, or the answer has no maximum of CPU cores, the
table says "not read". A ramp with "no replay" shows none. In `table.json` the replay side of
`comparison` holds `licensed_cores` (the values, a list) and `licensed_cores_text` (what
`table.md` shows), and each level of `replay_levels` holds the `licensed_cores` of its record;
`table.md` adds "Licensed cores: N." to the comparison sentence, and says what the number means in
the section on ramps.

## The store-only baseline

`jms-eval-baseline` sends the store requests of one recorded UC2 run again, straight to the store:
the same writes and queries, at the same store size, with nothing of KSM, the object-graph mapper
or the triple-store interface on the way, beside the network's round trip from the client host
It is the store-only baseline: its numbers stand beside the UC2 run's, so that a reader sees what
the store costs on its own. It is a measurement store client, as the seed is, and no participant: it
starts none.

```sh
uv run jms-eval-baseline runs/<run id>
uv run jms-eval-baseline runs/iteration-one/<run id>/record.json --runs runs/baselines
```

- The argument is the recorded run: its folder, or its `record.json`. `--runs` (default: `runs`)
  is where the baseline makes its own folder, named by its own run id. For a heavy run,
  `--heavy-run-approval` gives the approval file and `--spec` the approved spec (below).
- `--factor N` (default: 1; interim name) replays the run compressed in time: the
  same requests in the same order, each offset from the window's start divided by the factor, so
  each gap between two requests, and the window itself, is divided by it. A factor of 1 is the
  replay on the recorded timeline, as above. **A factor above 1 is a heavy run**: it starts only
  under the approval file and the approved spec that cover the replayed run's configuration key,
  as the baseline of a heavy run does. A factor below 1 is refused.
  `uv run jms-eval-baseline runs/<run id> --factor 4 --spec my-spec.toml --heavy-run-approval my-approval.toml`
- It needs what a run needs (above): the store's `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME` and
  `TRIPLESTORE_PASSWORD` (each `GRAPHDB_*` variable is read where its successor is not set, with a
  warning, as the triple-store interface reads them), a login that may create and delete
  repositories and read the monitoring views, and a checkout of jms_kapps. The recorded run's
  topology file and prefill file must still be where its configuration names them, unchanged.
- **Refused before anything reaches the store** (exit code 2): a recorded run without the wrapper,
  of the run kind `uc2-unwrapped`, whose record holds no text of its requests; a record of any
  other run kind than `uc2`; a run that did not complete, which has no measures; a prefill file
  whose digest is not the one in the recorded run's record; and the baseline of a heavy run
  that no approval covers. A baseline sends the recorded load at its recorded rate, so it is as
  heavy as the run it replays, and needs an approval as well: the approval file and the approved
  spec (`--spec`), and the record of the replayed run must hold a configuration key that the spec
  lists (a run made from a spec, by the sweep or by `jms-eval-uc2 --spec`, records it), and the
  configuration in that record's text must be the one the key names in the spec. A replayed run
  whose record holds no key, or whose configuration does not match its key, is refused, as is one
  outside the approval's period.

### What a baseline run does

1. **The store**, through part 1's store of a run: a repository of its own, `eval-` plus its run
   id, with the recorded run's SHACL setting; then the reset and the seed of the recorded run's
   topology, its prefill file in transactions of whole boxes, and the history collection if the
   recorded run collected history. This preparation goes through the triple-store interface, as
   a run's does, and none of its requests is measured.
2. **Its own login**, with the credentials of the environment. A recorded login holds no secret,
   so none is sent again.
3. **Before the window**: the requests recorded before the recorded window's start, those of the
   participants' start and of the warm-up, in their order, each after the answer to the one
   before. They build the store's state and are not measured.
4. **The window**: each request recorded in the window goes out at its recorded offset from the
   window's start (divided by the factor, with `--factor`). The replay never waits for an answer before it sends the next: requests whose
   offsets overlap go out at the same time. The window lasts as long as the recorded one, and the
   requests recorded after its end are not sent. Then the replay waits until each request has its
   answer, or has had none for 60 seconds, the triple-store interface's own limit, and is timed
   out. The measured window lasts as long as the recorded one, shortened by the factor.
5. **The end**, also after a failure or a stop signal: it deletes its repository and no other, and
   writes its record. As for a UC2 run, the outcome says nothing of the deletion: a baseline or a
   replay level whose replay completed is `completed`, with its measures and its level, also where
   the store did not delete its repository; its record says so (`repository_deletion`), and the last
   line names the repository.

**The run limit** (interim names): a baseline, at every factor, has
the run limit of a UC2 run (above), 30 minutes by default. The time counts from its start to the
start of its end (step 5). A baseline older than its limit ends there too, as a stop signal ends
it. Its outcome is `failed (over time)`, then the limit and the step at which the limit came: the
facts before the run, the store or the replay. The record keeps the limit as
`run_settings.run_limit_seconds`.

Every request goes to the baseline's own repository, whatever repository the recording names,
through part 1's guard. A record keeps no request's media type, so each is sent as the
triple-store interface sends it: a POST to a repository as a SPARQL query, a POST to its
statements as a SPARQL update, each accepting SPARQL's JSON results. A UC2 run's participants
reach the store with these two and their login only.

### What a baseline run leaves

`record.json` in its folder, with the run kind `baseline`; the run it replays (`replayed_run`: its
run id, its record, its repository, its window, and whether it was valid); the recorded
configuration; the actions it took, each with its time, and its own window; the facts of part 1's
record: the prefill file's digest and header, the client with the round trip to the server, the
software, and the server's views at the start, at the window's start and at the drain's end, with
the config of its repository and every request the harness sent to the REST API, and the server
watch of a UC2 run (above): the samples from the window's start to the drain's end, the counters
of every repository, the page cache, the license and the statement counts, read after the
store's preparation and after the replay; its settings (`run_settings`: the server watch's
interval, the transit time of the seed, which a record of the "after" series holds in its
configuration and one of the "before" series under `run_settings`, the prefill's transaction size, the request timeout,
`client_load_interval_seconds` and `run_limit_seconds`); the client host's load from before its first
replayed request to its window's end (`client_load`, sampled as a run's is, see "What a run
leaves"); how many requests the replay sent (`replay`: the recorded requests, the logins left
out, those before the window, those of the window, and those after it left out); and its
`measures` in the form of part 2's, so that part 3 puts them beside the UC2 run's: every request
it sent, each with when it left, its latency, its size, its outcome (accepted; refused, with the
kind of the refusal and the store's reason; timed out or connection dropped, with the error), the
statuses it writes and whether the store applied them, its text, and whether it is one of the
window's, which alone count; those of the window with their offset (`offset_seconds`) and how late
they left against it (`late_seconds`); and over the window, the requests, those refused, timed out
and with their connection dropped and their share, the completed writes and their number per minute,
and the refusals for want of heap, each kept with whether it came in the window; and `replay_level`,
the numbers of the replay level (below) and, in a sweep, `sweep`, which says the level's key and
factor. The requests before the window are kept, as part 2 keeps those of the warm-up, so that a
refusal that left the store in another state than the recorded run's shows. A baseline has no
commits of the object-graph mapper and no transports.

**The replay level.** `replay_level` holds the `factor`, the `offered_per_minute` and the
`achieved_per_minute`, the `requests_in_window`, the `answered_in_window`, the refusals, the
timeouts and the dropped connections in the window (`refused_in_window`, `timed_out_in_window`,
`connection_dropped_in_window`), the refusals for want of heap
(`heap_refusals_in_window`), the `replay_span_seconds`, `broken` and its `signals`, and the
`thresholds` (`refused_or_unanswered_share`). The rates, the rule that breaks a level and what the
sweep does with it are in "Replay levels in a sweep" above. At a factor above 1 the `offset_seconds`
of a request is its compressed offset, which its `late_seconds` is measured against.

**Reading it.** The replay is open: each request leaves at its time, whatever the store answered
before. A checked write's query for its receipt leaves at its recorded offset too, so where the
store answers the update later than in the recorded run, the query may come first and find no
receipt; the write then counts as not applied, as its record shows.

## The report of a spec

`jms-eval-report` turns the runs of a spec into tables and charts that a person reads without any
other tool, in a folder `report/` beside the records. It needs
no store and no network, runs nothing, and may be made again at any time. The sweep makes it after
each run, so the report is current while a sweep goes on. Its tables are made with pandas and its
charts with matplotlib's headless backend, both in the extra `evaluation`, which a plain install
of jms_kapps leaves out.

```sh
uv run jms-eval-report src/jms_kapps/evaluation/specs/iteration-one.toml --runs runs/iteration-one
```

- The spec, and `--runs` and each `--set` as the sweep was given them, and `--seed` and
  `--resamples` as `jms-eval-table` takes them (the same defaults).
- `--live-checks <log>` names the orchestrator's live-check log (below); without it, the marks of
  the live checks say `no data` (interim).
- It writes into `<runs>/report/`: `report.md`, the tables and the charts for a person;
  `runs.csv`; `configurations.csv`; `steps.csv`; `unanswered.csv`; `marks.csv`, `hours.csv`,
  `page_cache.csv`, `deadlock_alarms.csv` and `dead_heartbeats.csv` (the report points, below);
  and one PNG for each chart. A report made again
  replaces them and removes the charts it no longer draws.

### What the report holds

- **The runs table** (`runs.csv`, and in `report.md`): one row for each run. The configuration's
  number and its setting (topology, prefill, SHACL, the strategies, the box rate, the wrapper), the
  run kind and the repetition, the **status** and its **reason**, the transports ended per
  minute, the commits and their p50, p95 and p99, the p50 and p95 of the reads and of the writes,
  kept apart, the requests the store refused, those that timed out and those whose connection
  dropped, inside the window and outside it, kept apart (`refused_in_window`,
  `timed_out_in_window`, `connection_dropped_in_window` and the same `_outside_window`), the stuck
  boxes, and the client host's load in the window (below).
- **The status** is `completed` for a valid run, `invalid` for one that a check found invalid
  (its values are shown, with the checks as its reason), `failed` and `interrupted` (a stop signal
  ended it, and a later call of the sweep makes it again). A failed or an interrupted run has no
  values: it is listed with its reason, and not plotted.
- **The latencies** are those of `jms-eval-table`: the accepted store requests of the measured
  window, logins left out. The reads and the writes are told apart by the request's kind. A
  request that got no answer keeps its real wait in the record (`seconds`, with its `outcome` and
  its `reason`): the report counts it in the column of its outcome, timed out or connection
  dropped (a request that a record written before the split says "timed out" by its error),
  leaves it out of the latencies, and lists it (next item).
- **The unanswered store requests** (`unanswered.csv`, and a section of `report.md`):
  one row for each store request that got no answer, in the window and outside it, with its
  configuration, run kind, repetition and run, `where` (`in the window` or `outside the window`),
  its `outcome` (`timed out` or `connection dropped`), its `time`, its
  `wait_seconds` (the real wait until the failure), its `reason` (the exception as the record
  keeps it, as `ConnectionError: ... RemoteDisconnected(...)` for a dropped connection), its
  `kind` and its `participant`. A baseline run's are listed too. An error of neither class shows
  `connection dropped` with its error. (The outcome tells a timeout from a dropped connection, not
  the wait and the reason alone.)
- **The client load** (`runs.csv` columns `client_load`, `client_cpu_median_percent`,
  `client_cpu_max_percent` and `client_memory_max_gib`, and a section of `report.md`): the median
  and the highest CPU use of the client host, in percent of all its CPUs, and its highest memory use
  in GiB, over the record's samples that lie in the measured window. `client_load` is `measured`,
  `no data` (the record has no sample: a run of the "before" series; or none lies in the window) or
  `not available` (the host had no `/proc`); `report.md` shows those words in place of the numbers.
- **The configurations table** (`configurations.csv`, and in `report.md` with fewer columns) is
  `jms-eval-table`'s table (`table.make_table`, not a second version), for the runs that count: one
  row for each configuration at its box rate and each run kind, with each statistic as a column
  (`commit_latency_seconds_p95_value`, `commit_latency_seconds_p95_interval_low`, and so on; a list
  of findings, as the stuck boxes, is its length). The same records with the same seed and
  resamples give the same numbers as `table.json`.
- **The charts:** `throughput.png`, `commit-p95.png` and `read-write-p95.png`, each for every
  configuration, a dot for each run (filled if valid, a ring if invalid) and a tick at the median
  of the valid runs; and `prefill-family-<n>.png` for each family of configurations that differ
  only in the size of their prefill (the size is the one the file's name gives, as the `1M` of
  `grid-1M.ttl`), the throughput and the commit p95 against that size, on a log scale. `<n>` is
  the family's lowest configuration.
- **The steps** (`steps.csv`, and `steps-family-<n>.png` in `report.md`; module `report_steps`) tell
  where the time goes, from the spans, in the measured window, pooled over the valid runs of a
  configuration. A step is keyed by its role (`WMS` or `module`) and its span name; a commit is
  split by its status (`(accepted)` or `(error)`); every store request has the same span name, so it
  is keyed by its kind and a category taken from its query text (`store read: all boxes (WMS
  next_box)`, `store write: receipt removal`, ...). The categories are the two tables
  `READ_CATEGORIES` and `WRITE_CATEGORIES`, one line each, tried in order; a query that matches none
  is `store read: other read` or `store write: other write`, never an error. A store request the
  store did not accept is its own step, `(not accepted)`. One step has no span, `all-boxes read end
  -> next Reserve call`: the WMS's client time after its read of all boxes, measured from the spans
  around it. Only the runs of the "before" series have that read and that step: the WMS reads no box
  to name a new one, and when a box comes back it asks the store about that one box, in one `ASK`, a
  category of its own, `store read: possession: ASK for one box (WMS return)`; the old records still
  read and sort as before. The WMS is the participant of the record whose command takes
  `--wms-strategy`, and its IRI comes from the record. `steps.csv` has one row for each
  configuration, role and step: `runs`, `steps`, `steps_per_minute`, `median_seconds`, `p95_seconds`
  (`stats.quantile`, as the rest of the report) and `median_response_bytes` (the store's answer,
  where the span records one). `steps-family-<n>.png` compares the configurations of one family of
  prefill sizes step by step, a dot at the median and a tick at the p95, on a log time scale.

### The report points

The module `report_points` (all names interim). The runs of the "after" series go on around the
clock, and other use of the KIT GraphDB may overlap them. None of these files changes a column of
`runs.csv`, `configurations.csv` or `steps.csv`. A run of the "before" series has no server watch
and no log is given for it: every mark then says `no data`, and the report still builds.

- **By hour of day** (`hours.csv`, `hours-<n>.png` for each configuration, a section of
  `report.md`): the valid runs of a configuration and run kind grouped by the hour of day, UTC, of
  their window's start. For each hour: `runs`, the median of `transports_ended_per_minute` and of
  `completed_writes_per_minute` over the runs, and `commit_p95` and `store_request_p95`, the p95 of
  the commit latencies and of the accepted store-request latencies of the window, pooled over the
  runs of the hour (as `jms-eval-table` pools them).
- **The marks** (`marks.csv`, the section "Runs marked"): one row for each run. `live_check` is
  `overlap` when the run's window shares a moment with a live check of the log, `no overlap`, or
  `no data` (no log, or an unreadable one); `live_checks` names the checks. `other_activity` is
  `activity` when the server watch shows other activity, `none`, or `no data`, and
  `other_activity_why` says which repository and which figure.
- **The live-check log** is a file of JSON lines the orchestrator writes on the run host
  (`~/.claude/orchestrator/live-checks.jsonl`): one line at the start and one at the end of each
  live check on the KIT GraphDB, with exactly `event` (`start` or `end`), `at` (ISO 8601, UTC,
  seconds), `check` (free text) and `repository`. The report reads it from `--live-checks` and
  from nowhere else. A `start` pairs with the next `end` that has the same `check` and
  `repository`; a `start` without an `end` counts as running until the report is built; a line
  that is no event is left out.
- **Other activity** reads the server watch's monitoring views of every repository
  (`server.repository_monitors_at_the_start` and `server.repository_monitors_at_the_drain_end`).
  A repository other than the run's own counts when a cumulative counter (`epoolReads`,
  `epoolWrites`) rose between the two reads, or a gauge (`openConnections`, `activeTransactions`)
  was above zero at either. A repository that was not read (`read: false`, an inactive one) is no
  activity, and so is a view the server refused or a counter that fell. If no other repository
  could be read at all, the mark is `no data`.
- **The global page cache** (`page_cache.csv`, a section of `report.md`): for each prefill and
  run kind, the deltas of the cache's hits and misses between `server.structures_at_the_window_start`
  and `server.structures_at_the_drain_end`, summed over the valid runs (`hits`, `misses`), the
  runs that gave data (`runs`) and those that did not (`runs_without_data`: a negative delta,
  because the counters reset when the server restarted, a refused view, or no watch), and the
  `hit_share`, `hits` over `hits` plus `misses`, or `no data`. The view's counters are read as
  `cacheHit` and `cacheMiss`, the form the KIT server answers (seen in a live check),
  and also as `cache.hit`/`cache.miss` and `graphdb_cache_hit`/`graphdb_cache_miss`; one more form
  is a line in `_cache_counts`.
- **The deadlock alarms** (`deadlock_alarms.csv`, a section of `report.md`, the column
  `deadlock_alarms` of `marks.csv`): a run with a deadlock alarm stays `invalid`. The runs are
  listed apart with the number of alarms, the modules and the time of the first, and the section
  counts them for each configuration (`runs_with_alarm` of `runs`).
- **The dead heartbeats** (`dead_heartbeats.csv`, a section of `report.md`, the column
  `dead_heartbeats` of `marks.csv`): a heartbeat is a participant's write of its last-heartbeat
  time (a store write the store accepted, whose step category is `heartbeat update`); there are no
  spans of their own. It is dead when the last such write started more than the live-heartbeat age
  (`live_heartbeat_age_seconds` of the record's run settings, 90 s) before the start of the
  participant's last other span. A row has the participant, the time of its `last_heartbeat`, that
  of its `last_other_span` and the `gap_seconds`. A participant that wrote no heartbeat is not
  listed, and a run without spans has `no data`. The test fixture
  (`tests/reports/dead-heartbeat/`) is synthetic and shaped like the known case of iteration one,
  configuration 7 repetition 4, where eight participants stopped at 03:54:33 UTC while their
  other spans went on to 04:05:35.

### The summary of a run

A run's `record.json` is large (24 to 90 MB, mostly its store requests), so the report reads each
record once and keeps what it needs in a small file beside it, `report-summary.json`. Its `format`
is a whole number, which is raised when the file's keys change; a summary of another `format`, or
one made from a record of another size or age, is made again from the record. A later report reads
the summaries only. The sweep gives the report the record the run just returned, so a new run is
not read from its file at all. The summary holds the numbers the table of a spec reads of a
record, the latencies of the reads, the writes and the other accepted requests of the window, the
refused, the timed-out and the dropped requests inside the window and outside it, each unanswered
store request (`unanswered`: its outcome, its time, its real wait, its reason, and whether it was
in the window), the client
host's load in the window (`client_load`: the median and the highest CPU use, the highest memory
use and the number of samples, or `no data` or `not available`), and, for a run that counts and has
spans, its steps (`steps`: the minutes of the window and, for each role and step, the seconds of
its spans in the window and the sizes of the store's answers, so that the runs can be pooled and
the spans are read once), and the report points of the run (`points`: the
window and its hour, the verdict on other activity and its reasons, the page cache's deltas, the
deadlock alarms, and the dead heartbeats, or `null` for a run without spans). Its `format` is 5,
which counts the dropped connections apart; a summary of an older format is made again from its
record and its spans, so a summary that counted a dropped connection as timed out is made again.

### The report in the sweep

The sweep calls the report once after each run, right after the run returns and before it judges
how the run ended, whatever the end: completed, invalid, failed or interrupted. It gives the
report its own `--set` overrides. An error of the report (a missing library, a full disk) is
told on the sweep's output and never stops or fails the sweep.

## The per-commit table (interim)

`jms_kapps.evaluation.commit_table` reads recorded run folders, their records and their spans, into
two tidy tables, which the §7 figure script (`evaluation/section_7/figure.py`, its README beside
it) writes as `commits.csv` and `replayed_writes.csv` and plots. It changes no record and nothing
of the table or the report of a spec.

- **One row per commit** of the object-graph mapper of a run that counts: its store, heartbeat
  level and arm (KAPPS with SHACL on or off), run, repetition and pick seed, participant and role,
  span id, send time and seconds since the window's start, latency, outcome, whether it was
  admitted and is in the window, its kind (`heartbeat` in either spelling of the predicate,
  `status: ` and the statuses it sets, `other update`, `no write`), its checked write, its reads,
  and the time of each of its ten phases, which sum to its latency to the microsecond: the
  checked write, the receipt query, the receipt removal, the class queries, the fetch reads, the
  other requests, and the client's own time in the class-spec derivation, the fetch, the diff and
  the rest of the commit.
- **Given the run's writes-only replay** (a store-only baseline in the mode "writes only"), each
  commit's row holds its replayed checked write's latency, lateness, outcome and match, paired by
  the commit's span id, or, in a replay record without it, by the participant and the recorded
  send time; and the difference commit minus replayed update.
- **One row per replayed write** of a writes-only replay: its recorded and sent offset, lateness,
  latency, status code, outcome and recorded outcome, match, and whether it overtook its
  participant's previous write.

The commit link comes from the spans, by the same walk the record's link is made with, so a
record made before the link gives the same table. A run without spans gives no table.
`stats.difference_of_means` gives the interval of the difference of two pooled means, each side's
repetitions resampled independently.
