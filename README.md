# jms-kapps

The two use cases of the paper *KAPPS: A knowledge-based CPPS Architecture for the Circular
Factory*. The use cases are built on
[`kapps-semantic-middleware`](https://pypi.org/project/kapps-semantic-middleware/), the semantic
middleware of the KAPPS architecture's reference implementation.

| Paper | Where |
|---|---|
| Section 5.1, use case 1 | [`docs/use-case-1.md`](docs/use-case-1.md) |
| Section 5.2, use case 2 | [`docs/use-case-2.md`](docs/use-case-2.md) |
| Table 3, use case 1 | [the requirements of use case 1](docs/use-case-1.md#overview) |
| Table 3, use case 2 | [the requirements of use case 2](docs/use-case-2.md#overview) |
| Listing 1 | [`docs/recorded-refusal.md`](docs/recorded-refusal.md) |
| Section 7 and Figure 10 | [`commit_time/README.md`](commit_time/README.md) |
| Section 4.4.1, the triple store access module | [`kapps-triplestore-interface`](https://github.com/circularfactory/kapps_triplestore_interface/blob/v2.3.0/README.md) |
| Section 4.4.2, the Object-Graph Mapper | [`kapps-ogm`](https://github.com/circularfactory/kapps_ogm/blob/v0.4.0/README.md) |
| Section 4.4.3, the semantic middleware | [`kapps-semantic-middleware`](https://github.com/circularfactory/kapps_semantic_middleware/blob/v0.3.0/README.md) |

In the KAPPS architecture, the knowledge graph is the authoritative representation of the
factory's information state, and every change to it is validated against declared constraints
before it is admitted. A service registers in the knowledge graph, finds the other
services there, and commits each change of the factory information state through the write path
of its middleware instance. The triple store admits a commit only when it passes SHACL validation
against the declared shapes, and otherwise refuses it in whole.

The packages and files map to the four layers of the paper. The three packages are the
components of the Interface Layer. The triple store, with InfluxDB as the time-series database
of use case 1, is the Knowledge Base Layer. The ontologies and shapes, those of
`kapps-semantic-middleware` and those under `src/jms_kapps/uc1/` and `src/jms_kapps/uc2/`, are
the Ontology Layer. The services of each use case are its Service Layer.

The [glossary](docs/glossary.md) maps the paper's concepts to their instances in the use cases,
and defines the words of these pages.

## Setup

### Requirements

- `git`.
- Python 3.12 or later, and [uv](https://docs.astral.sh/uv/), which installs Python where the
  machine has none and the dependencies from the lock file `uv.lock`.
- [Docker](https://docs.docker.com/get-docker/), which runs the local triple store and the
  InfluxDB of the blocks below.
- A triple store with a dataset that validates SHACL shapes. Without one of your own, the blocks
  below start a local one, with its dataset.
- For use case 1, an InfluxDB 3. The blocks below start a local one.
- Three packages from PyPI, which [`pyproject.toml`](pyproject.toml) pins and `uv.lock` locks:
  [`kapps-semantic-middleware`](https://pypi.org/project/kapps-semantic-middleware/), the semantic
  middleware; [`kapps-triplestore-interface`](https://pypi.org/project/kapps-triplestore-interface/),
  the triple store access module; and [`kapps-ogm`](https://pypi.org/project/kapps-ogm/), the
  Object-Graph Mapper (OGM).

### Linux and macOS

```bash
git clone https://github.com/circularfactory/jms_kapps.git
cd jms_kapps
docker compose up -d --wait
uv sync --locked
export TRIPLESTORE_URL=http://localhost:7200 TRIPLESTORE_USERNAME=admin TRIPLESTORE_PASSWORD=root
```

### Windows

```powershell
git clone https://github.com/circularfactory/jms_kapps.git
cd jms_kapps
docker compose up -d --wait
uv sync --locked
$env:TRIPLESTORE_URL = "http://localhost:7200"
$env:TRIPLESTORE_USERNAME = "admin"
$env:TRIPLESTORE_PASSWORD = "root"
```

The three variables stay set only in the terminal in which you set them. Run the use cases in that
terminal. [Setup and operation](docs/setup.md) gives the local stack, what a stop does,
the shared settings, and the special cases: a triple store of your own, a taken port, SSH, and the
common errors.

## Use Case 1: Anomaly detection and learning in robotic unscrewing

### Run

```shell
uv run jms-uc1
```

`jms-uc1` seeds the two angle grinders of use case 1, starts its four services and the robot
controller stand-in, and prints the address of the dashboard of each service, through the page's
port. It clears the dataset it runs on: `jms-kapps`, unless `--dataset` names another. It refuses
while another service is live there, unless `--force`. Its page, on <http://127.0.0.1:8091/>, shows
each service and links to its dashboard. Unscrew, on the planning service's dashboard, creates one
unscrewing operation. Ctrl+C stops all five processes.
[Use case 1](docs/use-case-1.md) gives the run, the pages and the demonstrations.

## Use Case 2: A Decentralized Conveyor Network Coordinated through a Constraint-Enforced Knowledge Graph

### Run

This line runs the shipped topology `cross`, five modules, first:

```shell
uv run jms-uc2
```

`jms-uc2` seeds and launches the topology, and prints the address of the Launcher page,
<http://127.0.0.1:8090/>. It runs on the triple store that `TRIPLESTORE_URL` names. It clears the
dataset it runs on: `jms-kapps`, unless `--dataset` names another. A *dataset* is the triple store's
unit of data, one set of graphs. Ctrl+C stops every process of the run.
[Use case 2](docs/use-case-2.md) gives the run, the pages and the demonstrations.

## Commit time

The commit time measurement in the paper's Section 7 is in
[`commit_time/`](commit_time/README.md): its experiment and figure scripts, and the recorded run
behind Figure 10.

## Tests

```shell
uv run pytest -m "not live"
uv run pytest --dataset <name>
```

The first command runs the tests that need no triple store. The second runs every test. It needs
the connection variables of the setup, and it clears the dataset `<name>`: stop any run of a use
case on that dataset first. On a triple store of your own, name a dataset that nothing else uses.

## Layout

```text
jms_kapps/
├── commit_time/    the commit time measurement: its experiment, its figure and its recorded run
├── compose.yaml    a triple store, with the dataset created, and an InfluxDB
├── docs/           setup, use case 1, use case 2, the recorded refusal and the glossary, with the screenshots, the video and site/, the documentation site's source
├── graphdb/        the dataset's configuration
├── scripts/        the script that records use case 2's refusal, and the repository's hygiene checks
├── tests/
└── src/jms_kapps/
    ├── common/      what both use cases use: the seed that loads the ontologies and shapes, the triple store setup, the process supervision
    ├── uc1/         use case 1, with its ontology, its shapes and its recordings
    ├── uc2/         use case 2, with its ontology, its shapes, its topologies and its pages
    └── commit_time/ the measurement harness
```

## Citation

Cite the paper, and the archived record of the software that you use:

- the paper: Hoffmann et al. (2026), *KAPPS: A knowledge-based CPPS Architecture for the
  Circular Factory*, as submitted to the Journal of Manufacturing Systems:
  [doi:10.48550/arXiv.2605.22457](https://doi.org/10.48550/arXiv.2605.22457);
- `jms-kapps`, the paper's supplementary material:
  [doi:10.5281/zenodo.23772468](https://doi.org/10.5281/zenodo.23772468);
- the reference implementation of the KAPPS architecture, `kapps-triplestore-interface`,
  `kapps-ogm` and `kapps-semantic-middleware`:
  [doi:10.5281/zenodo.23772750](https://doi.org/10.5281/zenodo.23772750).

Section, figure, table and listing numbers on these pages are those of the paper's revised
version.

## Licence

MIT. See [LICENSE](LICENSE).

## Acknowledgements

The semantic middleware of the reference implementation adopts three concepts of the
aas-middleware of Behrendt et al.
([doi:10.1016/j.ifacol.2025.12.315](https://doi.org/10.1016/j.ifacol.2025.12.315)): the data model,
the connector and the workflow.

This package is developed as part of the INF subproject of the CRC 1574: Circular Factory for the
Perpetual Product. This work is funded by the German Research Foundation (DFG) - SFB 1574 –
471687386.
