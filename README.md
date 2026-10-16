# jms-kapps

The two use cases of the paper *KAPPS: A knowledge-based CPPS Architecture for the Circular
Factory* (arXiv 2605.22457), built on
[`kapps-semantic-middleware`](https://pypi.org/project/kapps-semantic-middleware/).

In KAPPS, the parts of a cyber-physical production system do not call each other by fixed
addresses. Each part is a *participant*: a program with its own instance of the semantic
middleware. Participants find each other in a knowledge graph, and every change they make to the
factory's state is a write to that graph, which the triple store checks against SHACL shapes,
rules it validates at every commit, before it admits the write.

- **Use case 2 — the decentral FlexConveyor.** Each conveyor module runs as its own process. A
  module reserves the next module for a box, conveys the box, and hands it over. No central router
  exists: each module chooses the next module from the graph. Two faults that a person arms on a
  module show the store refusing a write the module should not have made.
- **Use case 1 — vertical integration.** Four participants work an unscrewing operation. A
  planner opens one operation per press. A screwing resource records the attempt's curves. An
  anomaly detector classifies the attempt. A learner refines the detection thresholds of each
  Screw type. They coordinate through the knowledge graph and find each other there. The
  notifications and the call of the detector go over REST, to addresses that the graph names; the
  curves go through InfluxDB.

Both use cases are in one repository, because they share the ontology bootstrap, the triple store
setup and the measurement harness, and because the paper cites them together.

## Run use case 2

On Linux or macOS, with [Docker](https://docs.docker.com/get-docker/) and
[uv](https://docs.astral.sh/uv/), the clone first, then in the repository root:

```bash
git clone https://github.com/circularfactory/jms_kapps.git && cd jms_kapps
docker compose up -d --wait   # a local triple store, with the dataset jms-kapps, and an InfluxDB
uv sync --locked              # the packages, into .venv
export TRIPLESTORE_URL=http://localhost:7200 TRIPLESTORE_USERNAME=admin TRIPLESTORE_PASSWORD=root
./run-uc2
```

`./run-uc2` starts use case 2 and opens the Launcher page on <http://127.0.0.1:8090/>. It runs on
the triple store that `TRIPLESTORE_URL` names; without that variable, it starts the local store
itself first. **It clears the dataset it runs on**: `jms-kapps`, unless `--dataset` names another.
A *dataset* is the store's unit, one set of graphs. Every argument goes on to `jms-uc2`, as in
`./run-uc2 --topology merge-fault-demo`. Ctrl+C stops every process of the run.

`uv sync` needs no account: `uv.lock` takes the three sibling packages from PyPI. jms_kapps itself
is not on PyPI; you run it from a clone.

The [reviewer guide](docs/reviewer-guide.md) is the one path from a clean machine to a running use
case 2: what to install, the setup, what each page shows, how to use the two faults, how to stop,
and how to run the tests.

## What you see

![The Launcher page of a run of the cross](docs/images/uc2-launcher.png)

**The Launcher page** starts one process for each module of a *topology*, the modules of a station
and their connections, plus the WarehouseManagementSystem (WMS), which spawns boxes and takes them
back, and a monitor, which only reads the graph. It lists each process with its state, links to
each one's dashboard, arms each module's two faults, and launches another topology.

Open the WMS and spawn a box. The box travels from module to module to its destination. Each step
of its journey is a *hop*: the giving module asks the next module to reserve itself, commits the
*handover*, the change of the box's possessor, and moves the box. The store admits or refuses each
commit before anything moves. The monitor draws the station from the graph alone, and shows each
write the store refused, with the shape that refused it.

## The paper's words and the demo's

| The paper says | The demo shows |
|---|---|
| service | a participant's process, with its own middleware instance |
| operation | a box's transport, in use case 2; an unscrewing operation, in use case 1 |
| fault injection | the two faults a person arms on a module: `skip-reserve` and `complete-without-releasing` |
| the OGM | the object-graph mapper, which turns the middleware's Python objects into triples and back |
| the knowledge graph | the graph: the dataset of the triple store that a run uses |

The [glossary](docs/reference/glossary.md) defines every other word these pages keep: dataset,
participant, hop, handover, heartbeat, deregister, stale, IRI, SSE and more.

## Run use case 1

With the same setup as use case 2:

```bash
uv run jms-uc1
```

`jms-uc1` seeds the demo's two angle grinders, starts the four participants of use case 1, and
prints the address of each one's dashboard. **It clears the dataset it runs on**: `jms-kapps`,
unless `--dataset` names another, and it refuses while another service is live there, unless
`--force`. Its page, on <http://127.0.0.1:8091/>, shows each participant and links to its
dashboard. Press Unscrew on the planner's dashboard to open one unscrewing operation. Ctrl+C stops
all four. `jms-uc1 seed` seeds once and prints each participant's command line instead, to run
them by hand. See [Use case 1](docs/reference/use-case-1.md).

## On Windows

`./run-uc2` is a bash script, so the one command needs Linux, macOS or WSL. On Windows itself,
take the long way: start the local store with `docker compose up -d --wait`, set the three
variables as [The triple store](docs/reference/triple-store.md#the-connection-variables) shows for
PowerShell or `cmd.exe`, and run `uv run jms-uc2`. Every process prints its command line in the
form of the platform's shell, so each also runs on its own there.

## Where to read what

| | |
|---|---|
| [Reviewer guide](docs/reviewer-guide.md) | **Start here.** One path from a clean machine to a running use case 2, and what each page shows. |
| [Glossary](docs/reference/glossary.md) | The words, and the paper's words beside the demo's. |
| [The triple store](docs/reference/triple-store.md) | The connection variables, the dataset, the prefixes, the local fallback. |
| [The Launcher and the pages of a run](docs/reference/launcher.md) | `jms-uc2` and its options, the Launcher page, the states of a process, Clear, a module's dashboard, the monitor. |
| [Topologies and the editor](docs/reference/topologies.md) | The module list, the load check, the transit time, the editor, topology files, the shipped topologies. |
| [A box's journey, the strategies and the faults](docs/reference/journey-and-faults.md) | Spawning, each hop, when a module cannot go on, the strategies, the two faults, the vertical slice. |
| [Stopping](docs/reference/stopping.md) | How a run stops, and what cannot deregister. |
| [Past possessions](docs/reference/past-possessions.md) | Reading who held a box at an earlier time, from the store's history. |
| [Use case 1](docs/reference/use-case-1.md) | `jms-uc1`, its page and the demo, the seed and the participants by hand, InfluxDB and the frames, and the learner. |
| [Tests](docs/reference/tests.md) | The markers, what runs without a store, the live tests. |
| [The assets](docs/reference/assets.md) | Where the ontology, the shapes, the recordings and the topologies come from. |
| [Changelog](CHANGELOG.md) | What each version changed. |
| [The measurement harness](src/jms_kapps/evaluation/README.md) | The evaluation of the paper, which the demonstration runs without. |

## Requirements

- Python 3.12 or later. uv installs it where the machine has none.
- [uv](https://docs.astral.sh/uv/). Do not use Poetry, and do not install into a global Python.
- A triple store with a dataset that validates SHACL shapes. Where none is configured,
  `docker compose up -d --wait` brings a local one up, dataset included: see
  [The triple store](docs/reference/triple-store.md).
- For use case 1, an InfluxDB 3. The same command brings a local one up: see
  [Use case 1](docs/reference/use-case-1.md).
- Three sibling packages from PyPI, pinned in `pyproject.toml` and locked in `uv.lock`:
  - `kapps-semantic-middleware` 0.2.1
  - `kapps-triplestore-interface` 2.2.1
  - `kapps-ogm` 0.3.1

## Console scripts

| Script | Runs |
|---|---|
| `jms-uc2` | Use case 2 from a topology, behind the Launcher page. **Clears the dataset it runs against**, `--dataset`. See [The Launcher](docs/reference/launcher.md). |
| `jms-uc2 slice` | Use case 2's vertical slice: two modules, a clean handover and a refused one, then exit. **Clears the dataset it runs against**. See [The vertical slice](docs/reference/journey-and-faults.md#the-vertical-slice). |
| `jms-uc1` | Use case 1: seeds the two demo grinders, starts its four participants and prints the dashboard of each; Unscrew is on the planner's. **Clears the dataset it runs against**, `--dataset`. See [Use case 1](docs/reference/use-case-1.md). |
| `jms-uc1 seed` | Seeds once, starts nothing, prints each participant's command. **Clears the dataset it runs against**, `--dataset`. See [Run use case 1 by hand](docs/reference/use-case-1.md#run-use-case-1-by-hand). |
| `jms-eval-uc2` | One UC2 run of one configuration of the measurement harness, on a dataset it creates and deletes, with its record. |
| `jms-eval` | One command from a spec of the harness to its report and table. |
| `jms-eval-sweep` | The runs of a spec, one by one, skipping a run whose record exists. |
| `jms-eval-table` | The numbers of the runs of a spec, as one table beside their records. |
| `jms-eval-report` | The report of the runs of a spec: tables and charts beside their records. |
| `jms-eval-baseline` | The store-only baseline of one recorded UC2 run. |
| `jms-live-store` | Development tooling: the token login, and the create, size and delete of a disposable dataset for a live check. |

The `jms-eval-*` scripts are the measurement harness: see its
[README](src/jms_kapps/evaluation/README.md). **A heavy run needs an approval that covers it: any
person may approve.**

## Layout

```text
jms_kapps/
├── compose.yaml    the local fallback: a triple store, with the dataset created, and an InfluxDB
├── run-uc2         the one command of use case 2
├── docs/           the reviewer guide, its screenshots, and the reference pages
├── graphdb/        the dataset's configuration
├── scripts/        the release's checks of this tree, which its hygiene workflow runs
├── tests/
└── src/jms_kapps/
    ├── common/     what both use cases use: the ontology bootstrap, the store setup, the process supervision
    ├── uc1/        use case 1, with its ontology and its recordings
    ├── uc2/        use case 2, with its ontology, its shapes, its topologies and its pages
    └── evaluation/ the measurement harness, which the use cases import nothing from
```

Each asset is below the package that uses it, so the wheel carries it, and an installed copy reads
it with `importlib.resources`.

## Development

```bash
uv sync --locked
uv run pytest -m "not live"          # the offline tier: no triple store, no InfluxDB
uv run ruff check && uv run mypy
uv run pytest --dataset <name>       # with the store's variables set: also the tests that clear <name>
```

A test marked `graphdb` **clears the dataset it runs against**, so name a throwaway one, never the
demo's. See [Tests](docs/reference/tests.md).

## Licence

MIT. See [LICENSE](LICENSE).

## Acknowledgements

The ontology, the SHACL shapes and the time-series recordings are the ones the paper describes.
They come from the paper's own artifact, the first iteration of these use cases:
[CPPS_Circular_factory_usecases_JMS](https://github.com/EHoffm/CPPS_Circular_factory_usecases_JMS),
`paper` branch, commit `d32011805be9199291eec0731acab241e5bd74f1`. They were copied byte for byte;
the ontologies and shapes have since been changed to run on the middleware, and the head of each
file lists what changed. The two recordings of use case 1 are legacy mock data and still
byte-identical. The Python of the artifact is not carried forward: it was written against frozen
libraries, so both use cases are written again from the start. See
[The assets](docs/reference/assets.md).

This package is developed as part of the INF subproject of the CRC 1574: Circular Factory for the
Perpetual Product. This work is therefore supported by the Deutsche Forschungsgemeinschaft (DFG,
German Research Foundation) [grant-number: SFB-1574-471687386].
