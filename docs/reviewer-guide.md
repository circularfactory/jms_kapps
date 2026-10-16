# Reviewer guide

This guide is for a person who did not build the use cases. It gives one path from a clean Linux
machine to a running use case 2: what to install, the setup, what each page of the run shows, how
to use the two faults, how to stop, and how to run the tests. What the run means is the paper's
to say. The [glossary](reference/glossary.md) explains the other words it
uses.

The paper is *KAPPS: A knowledge-based CPPS Architecture for the Circular Factory*, arXiv
2605.22457.

## The paper's words and the demo's

| The paper says | The demo shows |
|---|---|
| service | a participant's process, with its own middleware instance |
| operation | a box's transport, in use case 2; an unscrewing operation, in use case 1 |
| fault injection | the two faults a person arms on a module: `skip-reserve` and `complete-without-releasing` |
| the OGM | the object-graph mapper, which turns the middleware's Python objects into triples and back |
| the knowledge graph | the graph: the dataset of the triple store that a run uses |

- [What you need](#what-you-need)
- [Set up, once](#set-up-once)
- [Use case 2: the decentral FlexConveyor](#use-case-2-the-decentral-flexconveyor)
- [Where to read it in the code](#where-to-read-it-in-the-code)
- [Look at the graph yourself](#look-at-the-graph-yourself)
- [Use case 1: anomaly detection and learning](#use-case-1-anomaly-detection-and-learning)
- [Run the tests](#run-the-tests)

## What you need

- A Linux machine (macOS works the same way), with `git` and `curl`.
- Docker with Compose v2, so that `docker compose version` answers. It runs the local fallback: a
  triple store and an InfluxDB.
- [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`. uv
  installs Python 3.12 itself where the machine has no Python 3.12 or later.
- A clone of the code, from GitHub:
  `git clone https://github.com/circularfactory/jms_kapps`, then `cd jms_kapps`. The use cases
  are not a package on PyPI, so there is no `pip install` of them: you run them from the clone.
  Their three libraries come from PyPI: `uv sync` installs the versions that `uv.lock` pins,
  kapps-semantic-middleware 0.2.1, kapps-ogm 0.3.1 and kapps-triplestore-interface 2.2.1.
- Only for the screenshots: Playwright's Chromium, `uv run playwright install chromium`.

## Set up, once

In the repository root:

```bash
docker compose up -d --wait   # the local fallback: the store, with the dataset jms-kapps, and InfluxDB
uv sync --locked              # the packages, into .venv
export TRIPLESTORE_URL=http://localhost:7200
export TRIPLESTORE_USERNAME=admin
export TRIPLESTORE_PASSWORD=root
```

The first command starts the local fallback of `compose.yaml`: a pinned triple store on
<http://localhost:7200>, with the dataset `jms-kapps`, which validates SHACL shapes and runs the
`rdfsplus-optimized` ruleset, and an InfluxDB. It returns once the dataset answers, and running it
again is safe. The store's workbench, on the same address, runs the queries of
[Look at the graph yourself](#look-at-the-graph-yourself): choose the dataset `jms-kapps` at the
top right, then open *SPARQL*. The three variables name that store to every command below, in this
shell; set them again in a new shell. The local store is the fallback for a machine on which no
store is configured, as a reviewer's. On a store of your own, set the variables to it instead, and
create a dataset there from `graphdb/jms-kapps-repo-config.ttl` (see
[The triple store](reference/triple-store.md)). A run clears and seeds its dataset, and the
middleware clears a dataset on a store that is not on loopback, such as a hosted one, only when
you allow it: `export TRIPLESTORE_ALLOW_DESTRUCTIVE=1`. The local store needs nothing of the kind.

```bash
docker compose stop      # stops the store and InfluxDB, and keeps their data
docker compose down -v   # removes both, and their data
```

**InfluxDB.** Use case 2 does not use InfluxDB. Use case 1 keeps its curves there, and its tests
write their frames into one database, `Tests`, whatever dataset they run on. The local InfluxDB
holds at most five databases and refuses a sixth; one shared database keeps runs on new dataset
names from adding any. To clear the local InfluxDB, and only it:

```bash
docker compose rm --stop --force influxdb
docker volume rm jms-kapps_influxdb-data
docker compose up -d --wait
```

## Use case 2: the decentral FlexConveyor

### What it shows

Each FlexConveyor module runs as its own process, behind its own instance of the semantic
middleware: in the paper's words, each is a service. The WarehouseManagementSystem (WMS) spawns a
box onto an origin module, the box travels from module to module to its destination, and the
destination gives it back to the WMS. No central router exists: each module chooses the next module
of a box from the graph. A module holds no address of a peer: before each call, it fetches the
endpoint of the peer's workflow from the graph. The graph is the authoritative state of the
station: who possesses which box, and where each box's transport stands. Each change of that state
takes one write path, a commit of the participant's middleware: a commit that the triple store admits or refuses against SHACL shapes before the work it
describes. Two faults of a module's own logic show the store refusing a write that the module
should not have made.

### Run it

```bash
./run-uc2
```

It runs `jms-uc2` on the store that `TRIPLESTORE_URL` names, the local one above, and opens the
Launcher page on <http://127.0.0.1:8090/> once it answers, or prints its address. Its first line
says which store the run uses. **The run clears the dataset it runs on**, `jms-kapps`. The shipped
topology `cross`, five modules, runs first; every argument goes to `jms-uc2`, as in
`./run-uc2 --topology merge-fault-demo` or `./run-uc2 --transit-time 1`. Without
`TRIPLESTORE_URL`, `./run-uc2` starts the local fallback itself and runs on its store. The terminal
of `./run-uc2` shows each process's log, each line starting with the process's name. It prints each
process's command line too: a module's names its own IRI and no peer.

**One port is enough, over SSH, WSL or VS Code.** Every page of the run goes through the Launcher's
port, 8090: the Launcher passes each process's pages through under a path of its own, `/p/<id>/`,
such as `http://127.0.0.1:8090/p/WarehouseManagementSystem1/dashboard`. So over SSH,
`ssh -L 8090:127.0.0.1:8090 <host>` is the one forward; VS Code's remote extensions forward 8090 by
themselves; and under WSL, a browser on Windows needs `localhost:8090` alone.

### What the pages show

**The Launcher page**, <http://127.0.0.1:8090/>, sets the run up and steers it; it is no
middleware instance and never appears in the graph. It lists every process with its state
(`starting`, `live`, `stopped`, `killed`, `failed`) and its recent output, and links each module's
and the WMS's API docs, activity page, dashboard and logs. A module shows `live` once its service
has registered its three workflows: Reserve, Receive and its box transport. Each module's row arms
and disarms its two faults. Below, it lists the boxes on the station with the SPARQL query that
finds them, which runs unchanged in the store's workbench. It launches any shipped topology, or
one built in its topology editor, which clears the dataset and restarts every process. **Clear**
stops every process and empties the dataset; **Stop all** stops every process.

![The Launcher page of a run of the cross](images/uc2-launcher.png)

**The WMS's dashboard**, the button *Open the WMS*, opens on its tab *WMS*. *Spawn a box* spawns one
box, from an origin to a destination that you pick or leave to the WMS. *Spawn rate* spawns boxes
on its own, so many a minute, until you set it to 0. *Last spawns*, *Held boxes* and *Transports*
show what the WMS did and holds.

**The monitor**, the button *Open the monitor*, is a participant that only reads the graph: its own
code writes nothing. It draws the modules as the graph connects them, each box on the module that
possesses it, and a disc on the box's destination. A box is named by a UUID of its own,
`Box-<UUID>`: the disc, and the box's square, show the first four characters of the UUID, and the
box keeps one colour, which comes from its UUID. Below are its tables of the boxes and of the
handovers it saw, and *Queries sent*, each SPARQL query it sends, exactly as the store received
it. Its view *Refusals* shows each write that the store refused, with the refusing shape's
message, the focus node, the path and the value, and the whole validation report behind *report*.
The switch *graph only* shows what a page sees that reads only the graph: the refusals go, because
a refused write leaves no trace in the graph.

![The monitor of a run of the cross](images/uc2-monitor.png)

**Each module's pages**, linked in its row of the Launcher page: its activity page shows each step
the module takes as it happens (a Reserve asked and answered, a box conveyed, a Receive, a
refusal); its dashboard has the module's tab, which draws it from above with the box it holds, its
routing decisions and the connections it read, and the middleware's tab *System*, whose box
*Refused writes* lists each write the store refused.

**One hop, in the logs** of the terminal and of each module's activity page. Spawn a box on the WMS's dashboard and follow it from Module1 to Module3.
The giver logs `Module1: Module3's ReserveWorkflow accepted Box-<UUID>`, then
`Module1: conveyed Box-<UUID> to Module3`; the receiver logs
`Module3: ReserveWorkflow accepted Box-<UUID> from Module1` and
`Module3: ReceiveWorkflow accepted Box-<UUID> from Module1`. `Box-<UUID>` is the box's name, the
same in every line of its journey. Between the Reserve and the move, the giver commits the change
of possession; the box moves only once the store has admitted it. The monitor's *Handovers* lists
the hop.

### See the store refuse a write: the two faults

The paper's fault injection is two switches in each module's row of the Launcher page. Both are
faults of the module's own logic, which the store's SHACL shapes refuse; a module starts with both
off.

1. **Fault scenario 1, `skip-reserve`:** the module hands a box on without asking the next
   module's Reserve. Launch the topology `merge-fault-demo` on the Launcher page, a merge of two
   streams that is made for this fault, and arm `skip-reserve` on Module5. Open the WMS and set
   *Spawn rate* to 30 boxes a minute. Module5 soon hands a box to Module6 while Module6 still holds
   the box before it: the store refuses that handover by `FlexConveyorModulePossessionShape`, "A
   FlexConveyorModule may possess at most one Box at a time.", and the monitor's *Refusals* shows
   it. Module5 logs a WARNING, `Module5: the triple store refused handing Box-<UUID> to Module6
   [...]`, with the report, and its dashboard's tab *System* lists the write in *Refused writes*.
   Right after, the monitor still shows the box on Module5 and Module6's own box on Module6:
   nothing moved. Module5 then asks Module6's Reserve and waits its turn,
   `Module5: Module6 refused to reserve: occupied by [...]; asking again in 1 s`. It logs
   `Module5: conveyed [...] to Module6` only once Module6 has accepted and the store has admitted
   the next handover, and the box arrives. Set the rate back to 0.
2. **Fault scenario 2, `complete-without-releasing`:** the module, as the destination of a box,
   commits the box's transport `done` before it gives the box back. Arm it on Module4, the exit of
   `merge-fault-demo`, and spawn a box toward Module4. The store refuses the commit by
   `DoneTransportBoxReleasedShape`, "The Box of a done BoxTransportOperation must not be possessed
   by a FlexConveyorModule."; then the module gives the box back properly.

### Stop it

Ctrl+C in the terminal of `./run-uc2`, or closing that terminal, stops every process of the run;
each one deregisters its service on the way out. The services and their workflows stay in the
graph, with no address and no endpoint, so that no participant calls them: the query Q2 below
shows it after a stop. The local store and InfluxDB keep running. The next `./run-uc2`, or a launch
from the Launcher page, clears the dataset and starts again.

### How the screenshots were taken

From the running pages of use case 2: the Launcher page and the monitor, each opened in a
headless browser through the Launcher's port once every process showed live, and saved whole.

## Where to read it in the code

Use case 2's package is under `src/`, in the folder `uc2/`. Every write path of a module and of the
WMS is a commit of its own middleware, which writes through the OGM. The middleware itself writes
a participant's registration, heartbeat and deregistration; use case 2's own writes are in three
modules.

- **`uc2/transport.py`, the transport.** `TransportRecords.open` makes one `mw.create()`: the box,
  its transport, `queued` and assigned to the origin, and the box's first possession, in one
  commit. `start`, `complete`, the write of fault scenario 2, and `fail` are each a `mw.commit()`
  of the transport's status; the normal `done` joins the last handover's commit. You see these
  writes in the WMS's *Transports* and in the
  monitor's table of the boxes.
- **`uc2/handshake.py`, the handover.** `Giver.hand_over` opens the middleware's `handover()`. Its
  entry commits the change of possession together with the transport's new assignment; the move of
  the box runs inside it, only once the store has admitted that commit. A refusal arrives here as
  the triple-store interface's `ShaclValidationError`, with the store's report, and is logged. You
  see these writes in the monitor's *Handovers*, and their refusals in its *Refusals* and in a
  module's *Refused writes*.
- **`uc2/process.py`, the start of a participant.** `open_handover_event_stream` commits, once at
  the start, where the participant's handover event stream lives, so that the monitor finds the
  stream through the graph. You see it in the monitor's mode *graph + streams*.

The reads are in `uc2/discovery.py`: each answer is fetched from the graph at the moment it is
asked, as a peer's endpoint, a connection or what a module holds. Before any participant runs, the
reset and the setup write directly: the clear of the dataset, the shared ontologies, the seeds,
the Launcher's Clear and the switch of the store's history. One direct write remains in the
middleware: when a module registers a workflow, the middleware's `register_workflow` links the
module to the capability, `cfc:hasCapability`, with one triple through the triple-store
interface, which the store's SHACL still validates; the OGM's commit replaces a property's whole
set of values, and several middleware instances register on one resource at once.
`tests/test_uc2_writes.py` reads every module of `src/` and finds no other direct write.

## Look at the graph yourself

Two queries for the store's workbench, on the dataset of the run. **Q1** lists every module of the
running topology, as a Core resource and a FlexConveyor module, only where the ontology states
that a FlexConveyor module is a Core resource:

```sparql
PREFIX fc: <http://w3id.org/circularfactory/FlexConveyor#>
PREFIX cfc: <https://w3id.org/circularfactory/Core#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?module WHERE {
  fc:FlexConveyorModule rdfs:subClassOf cfc:Resource .
  ?module a cfc:Resource , fc:FlexConveyorModule .
}
ORDER BY ?module
```

**Q2** lists each module's service, its address and its workflows with their endpoints. The
endpoints are on ports that each process takes at its start, and change at every launch. A column
stays empty where the graph holds no value: after a stop, the address and the endpoints.

```sparql
PREFIX fc: <http://w3id.org/circularfactory/FlexConveyor#>
PREFIX svc: <https://w3id.org/circularfactory/Service#>
SELECT ?module ?address ?workflow ?endpoint WHERE {
  ?module a fc:FlexConveyorModule .
  ?service svc:isServiceOf ?module .
  OPTIONAL { ?service svc:address ?address }
  ?step svc:isWorkflowOf ?service ;
        a ?workflow .
  FILTER(STRSTARTS(STR(?workflow), STR(fc:)))
  OPTIONAL { ?step svc:endpoint ?endpoint }
}
ORDER BY ?module ?workflow
```

## Use case 1: anomaly detection and learning

Use case 1 is built and runs with one command, `uv run jms-uc1 --dataset <name>` (the dataset
`jms-kapps` by default). It seeds the dataset, starts its four participants (the screwing
resource, the anomaly detector, the learner and the planner), prints the dashboard of each, and
runs until Ctrl+C, which stops them in order. On the planner's dashboard, Unscrew starts the
unscrewing of one screw that you choose. `uv run jms-uc1 --help` lists its options.

*What use case 1 shows and what its pages show will follow here in the shape of use case 2's.*

## Run the tests

With the variables of [Set up, once](#set-up-once) unset, the tests that need no store run:

```bash
uv run pytest
```

With them set, the tests of what the use cases show run against the store, by their marker:

```bash
uv run pytest -m paper_claim --dataset jms-kapps
```

**The run clears the dataset that `--dataset` names**, here `jms-kapps`, the dataset of the demo:
stop the demo first, since a test and a run on one dataset disturb each other. The next
`./run-uc2` seeds the dataset again. On a store of your own, name a dataset that nothing else
uses; the tests have no default dataset, so that no run clears one it was not told to. The run
takes a few minutes, and every test passes. Without the variables, every test that needs the store
is skipped, and the run proves nothing about a graph. [Tests](reference/tests.md) lists the
markers and the other runs.

The object-graph mapper's own tests, of the types and maximum counts it reads from OWL, run in a
checkout of `kapps_ogm`: `uv run pytest -m "not live" tests/unit/test_class_restriction_maximum.py
tests/unit/test_single_valued_property.py tests/unit/test_owl_minimums_not_required.py`.
