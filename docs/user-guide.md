# User guide

The two use cases are the ones in the paper *KAPPS: A knowledge-based CPPS Architecture for the
Circular Factory*, arXiv 2605.22457. The
[glossary](reference/glossary.md) explains the words this guide uses and
[maps the paper's concepts to their instances in the use cases](reference/glossary.md#the-papers-concepts-and-their-instances-in-the-use-cases).

- [What you need](#what-you-need)
- [Set up](#set-up)
- [Use case 2: a decentralized conveyor network](#use-case-2-a-decentralized-conveyor-network)
- [Where to read it in the code](#where-to-read-it-in-the-code)
- [Query the knowledge graph](#query-the-knowledge-graph)
- [Use case 1: anomaly detection and learning in robotic unscrewing](#use-case-1-anomaly-detection-and-learning-in-robotic-unscrewing)
- [Protocol mediation: the transferunits demo of kapps_semantic_middleware](#protocol-mediation-the-transferunits-demo-of-kapps_semantic_middleware)
- [Run the tests](#run-the-tests)

## What you need

The [requirements in the README](../README.md#requirements) name Python, uv, the triple store,
InfluxDB and the packages, with their versions. The use cases run from a clone. A machine also
needs:

- A Linux machine (macOS works the same way), with `git` and `curl`.
- Docker with Compose v2, so that `docker compose version` answers. It runs the local fallback: a
  triple store and an InfluxDB.
- [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- Only for the screenshots: Playwright's Chromium, `uv run playwright install chromium`.

## Set up

The setup is the block of [Run use case 2](../README.md#run-use-case-2) in the README: the local
triple store, the packages and `./run-uc2`. The rest of this section covers a triple store of your
own, the settings, a port that is taken, and the common errors.

To stop the local triple store and its InfluxDB, or to remove them with their data:

```bash
docker compose stop      # keeps their data
docker compose down -v   # removes both, and their data
```

### Your own triple store

Every command runs on the triple store that three variables name. Set them in the shell that runs
the commands, and set them again in a new shell:

```bash
export TRIPLESTORE_URL=https://your-store.example
export TRIPLESTORE_USERNAME=...
export TRIPLESTORE_PASSWORD=...
```

Create the dataset once on that triple store, from `graphdb/jms-kapps-repo-config.ttl` in the
clone: a dataset that validates SHACL shapes and runs the `rdfsplus-optimized` ruleset of
inference. [The triple store](reference/triple-store.md#the-dataset) says why those two. Windows sets the variables as
[The triple store](reference/triple-store.md#the-connection-variables) shows.

A run clears and seeds its dataset, `jms-kapps` unless `--dataset` names another, so name a dataset
that nothing else uses. The middleware clears a dataset on a triple store that is not on this
machine, such as a hosted one, only when this is allowed, for the command that clears:

```bash
export TRIPLESTORE_ALLOW_DESTRUCTIVE=1
```

<!-- vendor-check: off -- the variable that moves the local store's port, which carries the vendor's name, and a GraphDB that already runs on the default port -->
### The settings

| Setting | What it does |
|---|---|
| `TRIPLESTORE_URL`, `TRIPLESTORE_USERNAME`, `TRIPLESTORE_PASSWORD` | The triple store of every command. Without `TRIPLESTORE_URL`, `./run-uc2` starts the local one and uses it for that run. |
| `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` | Allows a command to clear a dataset on a triple store that is not on this machine. |
| `INFLUXDB_URL`, and `INFLUXDB_TOKEN` where the server asks for one | The InfluxDB of use case 1. Without `INFLUXDB_URL`, use case 1 uses the local InfluxDB on <http://localhost:8181>. |
| `JMS_KAPPS_GRAPHDB_PORT` | The port of this machine on which the local triple store answers, 7200 without it. See [A taken port](#a-taken-port). |
| `--dataset <name>` | The dataset a command runs on and clears, `jms-kapps` without it. `jms-uc2`, `jms-uc1` and their subcommands all take it. |

The options of `jms-uc2` are in [The Launcher and the pages of a run](reference/launcher.md), and
`uv run jms-uc2 --help` lists them. The options of `jms-uc1` are in
[Use case 1](reference/use-case-1.md), and `uv run jms-uc1 --help` lists them.

### A taken port

The local triple store answers on port 7200 of this machine. When something else holds 7200,
`docker compose up -d --wait` stops (see [Port 7200 is taken](#the-common-errors)). Two ways out:

- **Move the local triple store.** Name a free port in `JMS_KAPPS_GRAPHDB_PORT`:

  ```bash
  export JMS_KAPPS_GRAPHDB_PORT=7201
  unset TRIPLESTORE_URL   # the README's block set it to port 7200
  ./run-uc2
  ```

  `docker compose up -d --wait` publishes the local triple store on 7201, and `./run-uc2` without
  `TRIPLESTORE_URL` starts it there and runs on it. Its workbench is on <http://localhost:7201>.
  A command you run by hand, such as `uv run jms-uc1`, runs on the triple store its variables
  name, so set `TRIPLESTORE_URL=http://localhost:7201` for it, with the username `admin` and the
  password `root`.
- **Use the one on 7200.** A GraphDB that already runs on 7200 can be the triple store of the run:
  set the three variables to it as in [Your own triple store](#your-own-triple-store), and create
  the dataset `jms-kapps` in it. `./run-uc2` then starts no local triple store.

### The common errors

A name in angle brackets differs from run to run.

**Port 7200 is taken.** `docker compose up -d --wait`, or `./run-uc2` without `TRIPLESTORE_URL`,
fails with Docker's message about the port, such as
`Bind for 127.0.0.1:7200 failed: port is already allocated` where another container holds it, or
a message with `address already in use` where another program does. Free the port, or follow [A taken port](#a-taken-port).

**The dataset is missing on your own triple store.** A command ends with a traceback, whose last
line is

```text
kapps_triplestore_interface.exceptions.InvalidDatasetError: The store at <TRIPLESTORE_URL> has no repository '<dataset>' (404) - <the store's answer>
```

Create the dataset as in [Your own triple store](#your-own-triple-store), or name one the store has
with `--dataset`.
<!-- vendor-check: on -->

**A triple store that is not on this machine refuses the clear.** A command ends with a traceback,
whose last line starts with `kapps_semantic_middleware.errors.DeploymentValueError:` and goes on:

```text
Refusing to clear the default graph in dataset '<dataset>' on '<TRIPLESTORE_URL>'.
That host is neither loopback nor a container alias, so nothing here can tell that it is disposable.
Point TRIPLESTORE_URL at a local store or a service alias, or set TRIPLESTORE_ALLOW_DESTRUCTIVE=1 to state that the target is disposable.
```

Nothing was cleared. If the dataset is one that only this demo uses, set
`TRIPLESTORE_ALLOW_DESTRUCTIVE=1` for that command, as in
[Your own triple store](#your-own-triple-store).

**`jms-uc1` refuses while another service is live.** It exits with status 1, having printed

```text
jms-uc1: <services> carry a fresh heartbeat in <dataset>, so nothing was cleared. No process of this launcher serves them: another launcher runs there, or a process runs on its own from its command line. Stop that first. A process that was killed counts until its last heartbeat is 90 s old. Or pass --force to clear it anyway.
```

For one service the message reads "carries" and "serves it". Stop the run that holds the dataset,
such as `jms-uc2` or another `jms-uc1`, with Ctrl+C, or wait 90 s after a process was killed. `--force`
clears the dataset anyway, which breaks a run that is still live there. `jms-uc2` refuses the same
way, starting its line with `jms-uc2:`.

**The local InfluxDB refuses a sixth database.** It holds at most five databases. A run of
use case 1 that has to create a new one ends its operation *failed*, with the reason

```text
the unscrewing attempt failed: POST /api/v3/configure/database answered <status>: <InfluxDB's reason>
```

Clear the local InfluxDB as [InfluxDB](#influxdb) shows.

## Use case 2: a decentralized conveyor network

### What it shows

Use case 2 is a decentralized conveyor network coordinated through a constraint-enforced knowledge
graph. Each FlexConveyor module runs as its own process, a service (in the code, a *participant*:
one process with its own middleware instance). The WarehouseManagementSystem (WMS) spawns a box
onto an origin module, the box travels from module to module to its destination, and the
destination gives it back to the WMS. There is no central router: each module chooses the next
module of a box from the knowledge graph. Before each call, a module finds the peer's workflow
endpoint in the knowledge graph. The knowledge graph is
the authoritative representation of the station's information state: which module possesses which
box, and where the transport of each box stands. Each change of that state takes one write path, a
commit of the service's middleware instance through the OGM, and the triple store admits or
refuses the commit by SHACL validation before the act it describes. Two faults of a module's own
logic make the module commit a write that breaks a physical invariant, and the triple store
refuses that write.

A module's conveying of a box is a wait of its transit time; the conveyors are simulated.

### Run it

```bash
./run-uc2
```

It runs `jms-uc2` on the triple store that `TRIPLESTORE_URL` names, the local one above, and opens
the Launcher page on <http://127.0.0.1:8090/> once it answers, or prints its address. Its first
line says which triple store the run uses. The run clears the dataset it runs on, `jms-kapps`. The
shipped topology `cross`, five modules, runs first; every argument goes to `jms-uc2`, as in
`./run-uc2 --topology merge-fault-demo` or `./run-uc2 --transit-time 1`. Without
`TRIPLESTORE_URL`, `./run-uc2` starts the local fallback itself and runs on its triple store. The
terminal of `./run-uc2` shows the log of each process, each line starting with the process's name.
It prints the command line of each process too: a module's names its own IRI and no peer.
After the first launch, and again after each launch from the Launcher page, it prints one block of
addresses between blank lines: the Launcher page, the WMS dashboard on its tab WMS, the monitor, and
the form of a module's pages, all through port 8090. Use these. The address that a process prints
for itself, `<IRI> on http://127.0.0.1:<port>/`, is direct and changes at each launch, since every
launch gives each process a new port; it serves a process that you run by hand, without the
Launcher.

**One port is enough, over SSH, WSL or VS Code.** Every page of the run goes through the Launcher's
port, 8090: the Launcher passes the pages of each process through under a path of its own,
`/p/<id>/`, such as `http://127.0.0.1:8090/p/WarehouseManagementSystem1/dashboard`. So over SSH,
`ssh -L 8090:127.0.0.1:8090 <host>` is the one forward; VS Code's remote extensions forward 8090 by
themselves; and under WSL, a browser on Windows needs `localhost:8090` alone.

### What the pages show

**The Launcher page**, <http://127.0.0.1:8090/>, sets the run up and steers it; it is no
middleware instance and never appears in the knowledge graph. It lists every process with its
state (`starting`, `live`, `stopped`, `killed`, `failed`) and its recent output, and links the API
docs, activity page, dashboard and logs of each module and of the WMS. A module shows `live` once
its service has registered its three workflows: Reserve, Receive and its box transport. The row of
each module arms and disarms its two faults. Below, the page lists the boxes on the station with
the SPARQL query that finds them, which runs unchanged in the triple store's workbench. Its
topology section has three rows. In the first, the picker chooses a topology, and one grid below
it draws the chosen topology, read-only; **Empty grid** and **Upload** open an empty grid and a
topology file. **Edit** makes the same grid editable, with **Download** and **Close** in its place;
while it is edited, the picker, **Empty grid** and **Upload** replace the edit, and they and
**Close** ask "Discard your changes to the topology edited from …?" when it has changed. The last
row holds the run settings and one **Launch** button, which launches what the grid shows, clears
the dataset and restarts every process. While a topology that is not shipped runs, such as an
edit, the picker has an entry for it, `cross (edited), running now`. The panel **Running now**
shows the running topology, its strategies and its modules. **Clear** stops every process and
empties the dataset; **Stop all** stops every process.

![The Launcher page of a run of the cross](images/uc2-launcher.png)

**The WMS's dashboard**, the button *Open the WMS*, opens on its tab *WMS*. *Spawn a box* spawns one
box, from an origin to a destination picked on the dashboard or left to the WMS. *Spawn rate*
spawns boxes on its own, so many a minute, until it is set to 0; its line reads "Current rate:
60 boxes per minute", and "Current rate: 0, spawning is off" at 0. *Last spawns*, *Held boxes* and
*Transports* show what the WMS did and holds.

**The monitor**, the button *Open the monitor*, is a process with its own middleware instance that
only reads the knowledge graph: its own code writes nothing. It draws the modules as the
knowledge graph connects them, each box on the module that possesses it, and a disc on the box's
destination. A box is named by a UUID of its own, `Box-<UUID>`: the disc, and the box's square, show the first four characters of the UUID, and
the box keeps one colour, which comes from its UUID. Below are its tables of the boxes and of the
handovers it saw, and *Queries sent*, each SPARQL query it sends, exactly as the triple store
received it. Its view *Refusals* shows each write that the triple store refused, with the refusing
shape's message, the focus node, the path and the value, and the whole validation report behind
*report*. The switch *Follow the services' handover event streams* at the head of *Refusals*, off, shows
what a page sees that reads only the knowledge graph: the refusals go, because a refused write
leaves no trace in the knowledge graph. The page shows the switch in the mode the monitor runs
in. The Launcher starts the monitor with the switch off (*graph only*); the checkbox *Collect the SHACL refusals in one monitor* on the
Launcher page, ticked before a launch, starts it with the switch on (*graph + streams*).

![The monitor of a run of the cross](images/uc2-monitor.png)

**The pages of each module**, linked in its row of the Launcher page: its activity page shows each
step the module takes as it happens (a Reserve asked and answered, a box conveyed, a Receive, a
refusal); its dashboard has the module's tab, which draws it from above with the box it holds, its
routing decisions and the connections it read, and the middleware's tab *System*, whose box
*Refused writes* lists each write that the triple store refused.

**One hop, in the logs** of the terminal and of the activity page of each module. Spawn a box on
the WMS's dashboard and follow it from Module1 to Module3. The giver logs
`Module1: Module3's ReserveWorkflow accepted Box-<UUID>`, then
`Module1: conveyed Box-<UUID> to Module3`; the receiver logs
`Module3: ReserveWorkflow accepted Box-<UUID> from Module1` and
`Module3: ReceiveWorkflow accepted Box-<UUID> from Module1`. `Box-<UUID>` is the box's name, the
same in every line of its journey. Between the Reserve and the move, the giver commits the change
of possession; the box moves only once the triple store has admitted it. The monitor's *Handovers*
lists the hop.

### See the triple store refuse a write: the two faults

The paper's fault injection is two switches in the row of each module on the Launcher page. Both
are faults of the module's own logic, which the triple store refuses by SHACL validation against
the declared shapes; a module starts with both off. Tick *Collect the SHACL refusals in one
monitor* before the launch; with the monitor's switch *Follow the services' handover event
streams* off, the monitor shows no refusal.

1. **Fault scenario 1, `skip-reserve`:** the module hands a box on without asking the next
   module's Reserve. Launch the topology `merge-fault-demo` on the Launcher page, a merge of two
   streams that is made for this fault, and arm `skip-reserve` on Module5. Open the WMS and set
   *Spawn rate* to 30 boxes a minute. Module5 soon hands a box to Module6 while Module6 still holds
   the box before it: the triple store refuses that handover by
   `FlexConveyorModulePossessionShape`, "A FlexConveyorModule may possess at most one Box at a
   time.", and the monitor's *Refusals* shows it. Module5 logs a WARNING,
   `Module5: the triple store refused handing Box-<UUID> to Module6 [...]`, with the report, and
   its dashboard's tab *System* lists the write in *Refused writes*. Right after, the monitor still
   shows the box on Module5 and Module6's own box on Module6: nothing moved. Module5 then asks
   Module6's Reserve and waits its turn,
   `Module5: Module6 refused to reserve: occupied by [...]; asking again in 1 s`. It logs
   `Module5: conveyed [...] to Module6` only once Module6 has accepted and the triple store has
   admitted the next handover, and the box arrives. Set the rate back to 0.
2. **Fault scenario 2, `complete-without-releasing`:** the module, as the destination of a box,
   commits the box's transport `done` before it gives the box back. Arm it on Module4, the exit of
   `merge-fault-demo`, and spawn a box toward Module4. The triple store refuses the commit by
   `DoneTransportBoxReleasedShape`, "The Box of a done BoxTransportOperation must not be possessed
   by a FlexConveyorModule."; then the module gives the box back properly.

### See the triple store refuse a rule that SHACL Core cannot express

SHACL Core expresses every physical invariant of use case 2 above: a module possesses at most one
box, the box of a done transport is not possessed by a module, and the others. One cross-entity
constraint it cannot express, because no SHACL Core constraint compares the modules that a path
reaches with another value of the same node: a transport may only be created toward a module that
the station's connections reach from its origin. That constraint is a SHACL-SPARQL shape,
`:ReachableDestinationShape`, and the triple store refuses a write that breaks it at commit, with
the same exception and validation report as for a SHACL Core shape. A launch does not load it: its
target is a SPARQL query that the triple store evaluates over the whole dataset at every commit, so
it adds to the cost of every commit.

`jms-uc2 reachable-destination` shows it apart from a launch. It takes only `--dataset`, a name
that starts with `ogm-disposable-`, and clears that dataset at its start and its end; it starts no
module and no page. Run it, for example, as
`uv run jms-uc2 reachable-destination --dataset ogm-disposable-demo`. It prints four steps:

1. It seeds three modules with both shape files: Module1 is connected to Module2, and no connection
   reaches Module3.
2. It creates a transport from Module1 to Module2: `the store: admitted`.
3. It places a second box on Module2 with a running transport toward Module3: `the store:
   refused`, with the shape's message and the triple store's validation report as the triple store
   access module returns it. The report names `ReachableDestinationShape` alone.
4. It removes the connection from Module1 to Module2 while the first transport is open:
   `the store: refused`, although the write does not touch the transport. The printout says why:
   the shape's target is evaluated over the whole dataset at every commit.

It exits 0 when every step behaved as stated and 1 otherwise, naming the step. A refusal counts
only when its report names the reachable-destination shape.

### Read every change of a box's possession back from the triple store's history

A handover overwrites the box's link to its possessor, so the knowledge graph holds only the
present possessor. The triple store's history holds every change, each with the transaction that
made it and the time of that transaction. The trace below comes from the triple store's history,
not from the knowledge graph.

`jms-uc2 trace-box` runs use case 2 with four modules in a line, the topology `line`, sends one
box from Module1 to Module4, and reads every change of its possession back from the history. It
takes only `--dataset`, a name that starts with `ogm-disposable-`: it clears that dataset at its
start, and turns its history collection on for good. It starts no page.

**Create a disposable dataset** once, from `graphdb/jms-kapps-repo-config.ttl` in the clone with
only its ID changed, here to `ogm-disposable-trace`.

<!-- vendor-check: off -- the creation of a disposable dataset, through the local GraphDB's REST API, which says repository -->
- On the local triple store of `compose.yaml`, which answers on port 7200 unless
  `JMS_KAPPS_GRAPHDB_PORT` names another:

  ```bash
  sed 's/"jms-kapps"/"ogm-disposable-trace"/' graphdb/jms-kapps-repo-config.ttl > trace-config.ttl
  curl -X POST http://localhost:7200/rest/repositories -F config=@trace-config.ttl
  ```

  On Windows, copy the file, change `"jms-kapps"` in it to `"ogm-disposable-trace"` in an editor,
  and send it with `curl.exe` in place of `curl`.
- On a configured triple store, create a repository from the same file with its ID changed, in
  the triple store's Workbench, as for `jms-kapps` in [Your own triple store](#your-own-triple-store).
  The command clears the dataset, so set `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` for it on a triple
  store that is not on this machine.
<!-- vendor-check: on -->

Then run it:

```bash
uv run jms-uc2 trace-box --dataset ogm-disposable-trace
```

It prints four steps while the box travels, about half a minute in all: the seed with the history
collection on and the start of the four modules and the WMS, the spawn, the stop, and the read of
the history. Then it prints one row for each transaction that changed the box's possession, its
transport's status or the module its transport is assigned to (`cfc:executedOn`). From a run on
the local triple store:

```text
time (UTC)               time (server)                  box                                       possessor                   status   assigned module  transaction
-----------------------  -----------------------------  ----------------------------------------  --------------------------  -------  ---------------  -----------------
2026-10-24 11:05:30.484  2026-10-24 11:05:30.484+00:00  Box-8aa68034-418d-4efe-9be2-8abb70aad28f  Module1                     queued   Module1          20261024110530484
2026-10-24 11:05:30.872  2026-10-24 11:05:30.872+00:00  Box-8aa68034-418d-4efe-9be2-8abb70aad28f  -                           running  -                20261024110530872
2026-10-24 11:05:31.246  2026-10-24 11:05:31.246+00:00  Box-8aa68034-418d-4efe-9be2-8abb70aad28f  Module2                     -        Module2          20261024110531246
2026-10-24 11:05:32.862  2026-10-24 11:05:32.862+00:00  Box-8aa68034-418d-4efe-9be2-8abb70aad28f  Module3                     -        Module3          20261024110532862
2026-10-24 11:05:34.465  2026-10-24 11:05:34.465+00:00  Box-8aa68034-418d-4efe-9be2-8abb70aad28f  Module4                     -        Module4          20261024110534465
2026-10-24 11:05:36.029  2026-10-24 11:05:36.029+00:00  Box-8aa68034-418d-4efe-9be2-8abb70aad28f  WarehouseManagementSystem1  done     -                20261024110536029

possessors from the history: Module1, Module2, Module3, Module4, WarehouseManagementSystem1
possessors from the logs:    Module1, Module2, Module3, Module4, WarehouseManagementSystem1
the history and the logs agree; the trace is in trace-Box-8aa68034-418d-4efe-9be2-8abb70aad28f.json
```

- The first row is the spawn: one transaction creates the box on Module1, its transport `queued`,
  and assigns the transport to Module1. The second is Module1 starting the transport.
- Each handover between modules is one transaction: the box's possession and the transport's
  assignment move to the next module together.
- The last row is the give-back: one transaction makes the WMS the possessor and the transport
  `done`.
- **The two times** are the transaction's timestamp: in UTC, and in the triple store server's
  local time, with its offset from UTC. The transaction's ID is the time in UTC, to the
  millisecond. The local triple store runs in UTC, so its two columns show the same time.
- **The cross-check** compares the possessors in the order of their transactions with the
  handovers the participants logged: the WMS's `spawned ... onto Module1`, each module's
  `conveyed ... to`, and the WMS's `accepted ... back`. The command exits 0 when they agree and 1
  otherwise.

It writes the same rows and the cross-check as JSON, to `--out`, or to `trace-<box>.json` in the
current folder. A second run on the same dataset works: each box has a name of its own. A query
of the history for one point in time is in [Past possessions](reference/past-possessions.md).

### Stop it

Ctrl+C in the terminal of `./run-uc2`, or closing that terminal, stops every process of the run;
each one deregisters its service on the way out. The services and their workflows stay in the
knowledge graph, with no address and no endpoint, so that no service calls them: the query Q2
below shows it after a stop. The local triple store and InfluxDB keep running. The next
`./run-uc2`, or a launch from the Launcher page, clears the dataset and starts again.

### How the screenshots were taken

From the running pages of use case 2: the Launcher page and the monitor, each opened in a
headless browser through the Launcher's port once every process showed live, and saved whole.

## Where to read it in the code

The package of use case 2 is under `src/`, in the folder `uc2/`. Every write of a module and of
the WMS is a commit of its own middleware instance, which writes through the OGM. The middleware
itself writes the registration of a service, its heartbeat and its deregistration; the writes of
use case 2's own code are in three modules.

- **`uc2/transport.py`, the transport.** `TransportRecords.open` makes one `mw.create()`: the box,
  its transport, `queued` and assigned to the origin, and the box's first possession, in one
  commit. `start`, `complete`, the write of fault scenario 2, and `fail` are each a `mw.commit()`
  of the transport's status; the normal `done` joins the commit of the last handover. These
  writes appear in the WMS's *Transports* and in the monitor's table of the boxes.
- **`uc2/handshake.py`, the handover.** `Giver.hand_over` opens the middleware's `handover()`. Its
  entry commits the change of possession together with the transport's new assignment; the move of
  the box runs inside it, only once the triple store has admitted that commit. A refusal arrives
  here as the `ShaclValidationError` of the triple store access module, with the triple store's
  validation report, and is logged. These writes appear in the monitor's *Handovers*, and their
  refusals in its *Refusals* and in a module's *Refused writes*.
- **`uc2/process.py`, the start of a service.** `open_handover_event_stream` commits, once at the
  start, where the service's handover event stream is served, so that the monitor finds the stream
  through the knowledge graph. It appears in the monitor's mode *graph + streams*.

The reads are in `uc2/discovery.py`: each answer is fetched from the knowledge graph at the moment
it is asked, such as a peer's endpoint, a connection or what a module holds. Before any service
runs, the reset and the setup write directly: the clear of the dataset, the shared ontologies, the
seeds, the Launcher's Clear and the switch of the triple store's history. No service writes past
its middleware instance: the middleware also registers a capability together with the resource's
`cfc:hasCapability` link to it in one commit through the OGM. `tests/test_uc2_writes.py` reads
every module of `src/` and finds no other direct write.

## Query the knowledge graph

Two queries for the triple store's workbench, on the dataset of the run. The workbench is on the
triple store's own address, <http://localhost:7200> for the local one: choose the dataset
`jms-kapps` at the top right, then open *SPARQL*. **Q1** lists every module
of the running topology, as a Core resource and a FlexConveyor module, only where the ontology
states that a FlexConveyor module is a Core resource:

```sparql
PREFIX fc: <http://w3id.org/circularfactory/FlexConveyor#>
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?module WHERE {
  fc:FlexConveyorModule rdfs:subClassOf cfc:Resource .
  ?module a cfc:Resource , fc:FlexConveyorModule .
}
ORDER BY ?module
```

**Q2** lists the service of each module, its address and its workflows with their endpoints. The
endpoints are on ports that each process takes at its start, and change at every launch. A column
stays empty where the knowledge graph holds no value: after a stop, the address and the endpoints.

```sparql
PREFIX fc: <http://w3id.org/circularfactory/FlexConveyor#>
PREFIX svc: <http://w3id.org/circularfactory/Service#>
SELECT ?module ?address ?workflow ?endpoint WHERE {
  ?module a fc:FlexConveyorModule .
  ?service svc:isServiceOf ?module .
  OPTIONAL { ?service svc:hasAddress ?address }
  ?step svc:isWorkflowOf ?service ;
        a ?workflow .
  FILTER(STRSTARTS(STR(?workflow), STR(fc:)))
  OPTIONAL { ?step svc:hasEndpoint ?endpoint }
}
ORDER BY ?module ?workflow
```

## Use case 1: anomaly detection and learning in robotic unscrewing

### What use case 1 shows

Four services, each behind its own middleware instance, work on the unscrewing of a screw from an
angle grinder: the planner, the screwing resource, the anomaly detector and the learner. The
planner creates one unscrewing operation for one screw and waits for its end. The screwing
resource makes the unscrewing attempt on its robot, records the time series in InfluxDB and calls
the detector. The anomaly detector decides the outcome: it classifies the attempt and commits the outcome with
its provenance. The operation ends *done* when the screw is intact, and *failed* when the outcome
is any other, with a reason that names it. The
learner refines a detection threshold of each screw type from the attempts that ended well. No
service holds the address of another: they find each other in the knowledge graph, as the modules of
use case 2 do. The seed holds two angle grinders, `Grinder099` and `Grinder100`, with one screw
each: `Screw1`, whose attempt is the example recording of a good unscrewing, and `Screw2`, whose
attempt is the example recording of a missing screw.

A fifth process, the robot controller stand-in, takes the place of the robot's controller, the one
part of the robot cell that the demonstrator replaces. It is a device: it speaks only MQTT and holds no address in the knowledge graph. The
knowledge graph describes the robot `Robot1` and each of its twelve parameters: the broker, the port
and the topic, and the set topic of a parameter that a client may set. The screwing resource's
middleware instance builds its twenty connectors to the robot from that description: the broker, the
port and the topic all come from the graph. An attempt sets the seven values of the
screw type on the robot, starts it, and collects the samples that the robot publishes. [The robot
over MQTT](#the-robot-over-mqtt) shows where to read each step.

### What in use case 1 is example data or a placeholder

The thresholds of the screw types and the placement values are example data.
The learner applies a placeholder rule to one threshold: it sets the maximum dynamic torque 20 %
above the highest dynamic torque of the screw type's *ScrewIntact* attempts, and the first value
replaces whatever the type held. The stand-in publishes the samples of two example recordings,
to which the demo assumes the units of the ontology, as the readings of the robot; no robot makes
them. It publishes the same recording whatever setpoints it receives.

### Run use case 1

In the root of the clone, with the triple store and InfluxDB of [Set up](#set-up)
running and its variables set:

```bash
uv run jms-uc1 --dataset jms-kapps
```

It clears the dataset it runs on and seeds it, starts the four services and the robot controller
stand-in one after the other, the stand-in right after the screwing resource, prints one block of
addresses between blank lines, the page and the dashboard of each service, all through port 8091,
and runs until Ctrl+C. The block says that the stand-in speaks only MQTT. The MQTT broker listens
on `127.0.0.1`, port 18860, inside the screwing resource's process. It refuses to start while another service is live
on that dataset, or while another `jms-uc1` holds its page's port, 8091. Stop use case 2 first if
it runs on the same dataset. `uv run jms-uc1 --help` lists the options, and
[Use case 1](reference/use-case-1.md) describes the run by hand, on other computers.

**One port is enough, over SSH, WSL or VS Code.** Every page of the run goes through the page's
port, 8091: the page passes the pages of each service through under a path of its own,
`/p/<id>/`, such as `http://127.0.0.1:8091/p/Planner1/dashboard`, as the Launcher of use case 2
does on its port. So over SSH, `ssh -L 8091:127.0.0.1:8091 <host>` is the one forward; VS Code's
remote extensions forward 8091 by themselves; and under WSL, a browser on Windows needs
`localhost:8091` alone. The address that a service prints for itself is direct and changes at each
run, since every start gives each service a new port.

### InfluxDB

Use case 1 keeps its time series in InfluxDB. A run, and the tests that start `jms-uc1`, write
into the database `jms-kapps`, and the other tests of use case 1 into the database `Tests`,
whatever dataset they run on. The local InfluxDB holds at most five databases and refuses a
sixth. To clear the local InfluxDB,
and only it:

```bash
docker compose rm --stop --force influxdb
docker volume rm jms-kapps_influxdb-data
docker compose up -d --wait
```

### The robot over MQTT

The graph holds the description of the robot's parameters, and the screwing resource's middleware
instance builds its connectors from it, as the instances of the TransferUnit demo in
[Protocol mediation](#protocol-mediation-the-transferunits-demo-of-kapps_semantic_middleware) do.
In the triple store's workbench, choose the dataset of the run (`jms-kapps` for `uv run jms-uc1`)
and open *SPARQL*. This query reads the connection description of the axial force of the robot:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX inf: <http://www.sfb1574.kit.edu/ontologies/CrcInterfaces#>
SELECT ?broker ?port ?topic WHERE {
  uns:Robot1 uns:hasAxialForce ?force .
  ?force inf:hasMQTTBrokerIP ?broker ;
         inf:hasMQTTBrokerPort ?port ;
         inf:hasMQTTTopic ?topic .
}
```

The answer is one row: the broker `127.0.0.1`, the port `18860` and the topic
`Robot1/hasAxialForce`. The screwing resource's middleware instance starts the broker at that
address and subscribes to that topic. Each of the seven setpoints also has an
`inf:hasMQTTSetTopic`, such as `Robot1/hasRobotMaxDynamicTorque/set`.

**The connections.** While a run is live, open the screwing resource's dashboard from the page,
tab *System*, box *Connectors*. It lists the twenty connectors of the robot: twelve that read a
parameter (the three readings, the state of the attempt, the start command and the seven
setpoints) and eight that write one (the start command and the seven setpoints), each with its
state, its last sync and its last error.

**The setpoints arriving and the samples of an attempt.** Press Unscrew on `Screw1` and read the
terminal as in the list of
[one operation in the logs](#what-the-pages-of-use-case-1-show): the screwing resource sets the
seven values of the screw type on the robot and then starts it, and the stand-in logs each
setpoint, the start and the end of its example recording with the sample count. The samples of the attempt
are in InfluxDB, and the screwing resource's box *Attempts* names their frame.

The stand-in has a command line of its own, described in
[Use case 1](reference/use-case-1.md#the-robot-controller-stand-in).

### What the pages of use case 1 show

**The page**, <http://127.0.0.1:8091/>, is read-only. It asks for its state every second and redraws
in place. Its first section says what to do: press Unscrew on `Screw1`, then on
`Screw2`, and where each result shows. Its second section has a row for each of the five processes:
its state, its PID, its recent output on a click, and for a service the links to its API docs, its
activity page, its dashboard and that dashboard's Logs tab, each through the page's port. The
stand-in's row has no links, since it has no pages. The page never appears in the knowledge graph. Its section
*Operations* lists each unscrewing operation: its screw, the service that issued it, its status,
its outcome, the reason of a failed one, and the service that decided the outcome, which links to
that service's dashboard. The deciding service is the agent of the classification that generated
the values the operation used, the provenance chain that the anomaly detector commits; after both
presses it is the anomaly detector for both operations. Its section *Learned values* shows each
screw type's maximum dynamic torque at the start of the run and at the latest read; after
`Screw1`, the latest is the value the learner committed for `M4`. Each list comes from one SPARQL
query, which the page shows exactly as it was sent, with a button *Copy the query*: it declares
its own prefixes and runs unchanged in the triple store's workbench. The queries run in the
background every second from the seed on, whether a page is open or not; a read that fails keeps the last answer and says why. The first button opens the
planner's dashboard. The button *Sequence* opens the story of the scenario, step by
step with the seeded names (`Screw1`, `Screw2`, `Grinder099`, `Grinder100`): what each service reads
and commits, and what changes in the knowledge graph. It is the page [The UC1
sequence](reference/uc1-sequence.md), which the page serves itself.

![The page of a run of use case 1 after both presses: the two steps, a row for each of the five processes, the two operations, whose deciding service is the anomaly detector, and M4's learned value](images/uc1-page.png)

**The planner's dashboard** has the button *Unscrew*. Choose `Screw1` in its drop-down and press it:
the planner creates the unscrewing operation of that screw, and the terminal logs
`Planner: a press of Unscrew opens the unscrewing of Screw1 in Grinder099`. A screw that has an
operation leaves the drop-down; a press on it is refused. Press it for `Screw2` too.

**The dashboards of the screwing resource and of the detector** show, in boxes of their own, each
attempt and each classification as it happens: the operation, the screw and its screw type, the
seven values of the screw type that the attempt was given (the attempt sets each on the robot; the
stand-in echoes it and publishes the same example recording, since in a real cell the robot and
the screwdriver would use them), the outcome, and the ten values the decision
used. After the learner has refined a threshold, the next attempt on a screw of that type is given
the refined value. A screw type that lacks one of the seven values, holds it twice or holds no
finite number fails the operation before the attempt, with the missing value named. The
detector's outcome for `Screw1` is *ScrewIntact*, which ends its operation *done*. For `Screw2` it
is *MissingScrew*, which ends its operation *failed* with the reason `ANOMALY: MissingScrew`; the
screwing resource's box shows *failed, MissingScrew*. When the robot reports a failure, or sends no end within 60 s,
the operation ends *failed* with that reason, in the box and on the page. The planner's dashboard shows each screw's
status and outcome, and the reason of a failed one when the pointer rests on the ⓘ beside
*failed*.

**The learner's dashboard**, box *Learner*, shows its last run, each value it changed (screw type,
threshold, old value, new value, when) and the last thing that went wrong. The learner runs every
10 s. After the first operation ends *done* with *ScrewIntact*, it commits a new maximum dynamic
torque to the screw type `M4`.

**The terminal** shows the lines of each service behind its name. Whenever a service fetches
through the OGM, a line says which service, what it fetches, with which projection scope (in the
code, a *class scope*), and what for.

**One operation in the logs** of the terminal. Press Unscrew on `Screw1` and follow its operation
from the press to the planner's read of the outcome. `UnscrewingOperation_<hex>` is the operation's
name, the same in every line from the second on; `<frame>` is the frame of its curves in InfluxDB.
`…` stands for text cut from a line. `OPERATION_SCOPE`, `EFFECTIVE_VALUES_SCOPE` and the other
`…_SCOPE` names are the projection scopes (in the code, *class scopes*) that a read or write uses.

1. The planner opens the operation:
   `Planner: a press of Unscrew opens the unscrewing of Screw1 in Grinder099`.
2. The screwing resource gets the operation and reads it, with the seven values of the screw type:
   `Screwing resource receives UnscrewingOperation_<hex> with OPERATION_SCOPE = [[concernsScrew, hasScrewType, hasMinApproachAxialForce], …, [hasOperationStatus], [hasExecutionResult], [hasDetectionResult]]: …`.
3. It starts the attempt and sets the operation `running`:
   `ScrewingResource1: the attempt of UnscrewingOperation_<hex> on Screw1 is given the values of M4: MinApproachAxialForce 0.5, …`.
   It sets the seven values on the robot, one line each, then the start command:
   `ScrewingResource1: hasRobotMinApproachAxialForce is set on Robot1`, …,
   `ScrewingResource1: hasAttemptCommand is set on Robot1`, and `the robot is started on Screw1`.
   The stand-in's lines come behind its own name: `Setpoint hasRobotMinApproachAxialForce <- {"v": …}`
   for each of the seven, `Start: http://w3id.org/circularfactory/Unscrewing#Screw1 replays success`, and at the end
   `Done: example-d320118/success, … samples`.
   Once the samples are stored, `ScrewingResource1: UnscrewingOperation_<hex> is running, with its curves at influxdb:///jms-kapps/unscrewing?frame=<frame>&start=…&stop=…`.
4. The anomaly detector reads the operation, then the screw's ten values for the decision:
   `Anomaly detector reads UnscrewingOperation_<hex> with OPERATION_SCOPE = [[hasOperationStatus], [hasExecutionResult], [concernsScrew], [hasTimeSeriesReference]]: …` and
   `Anomaly detector reads Screw1 with EFFECTIVE_VALUES_SCOPE = [[hasMinApproachPosition], …]: …`.
5. It commits the classification and the outcome:
   `Anomaly detector writes UnscrewingOperation_<hex> with an empty ClassScope = []: …`, then
   `AnomalyDetector1: UnscrewingOperation_<hex> is done, with the outcome ScrewIntact`. For `Screw2`
   the line reads `is failed, with the outcome MissingScrew: …` instead.
6. The planner, notified of the end, reads the outcome:
   `Planner reads UnscrewingOperation_<hex> with OUTCOME_SCOPE = [[hasDetectionResult]]: it wants the outcome that the detector committed (hasDetectionResult)`.
   For a failed operation it reads the reason too, with `FAILURE_SCOPE`.

The screwing resource's `ScrewingResource1: the anomaly detector took UnscrewingOperation_<hex>`
comes when its call of the detector returns. It stands before or after the planner's read, since
the two services are separate processes.

### The uncertainty of a measured position

The anomaly detector can save each position it measures as a random variable, beside the plain
best estimate. The mode is off by default, and a plain run of `jms-uc1` writes no variable. Turn
it on with the checkbox *Save measurements as uncertainty quantifications* on the page, which
takes effect from the next classification, or from the start with `uv run jms-uc1
--save-measurements-as-uq`. With the mode on, the commit of each classification also writes the
position it measured: the position at contact, as the Screw's head position, and after a failure
outcome the end of the approach, on the operation. Each is a `uq:RandomVariable` whose literal, of
the datatype `uq:gmmLiteral`, holds that one measurement: the measured value in mm as the mean,
and the square of the robot's standard deviation as the variance.

Two queries for the triple store's workbench, on the dataset of the run. **Q5** reads the best
estimate of a Screw's head position, in mm:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
SELECT ?screw ?estimate WHERE { ?screw uns:hasMeasuredScrewHeadPosition ?estimate }
```

**Q6** reads the literal of that position with its datatype, which is `uq:gmmLiteral`:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX uq: <http://w3id.org/circularfactory/Uncertainty#>
SELECT ?screw ?literal (DATATYPE(?literal) AS ?datatype) WHERE {
  ?screw uns:hasScrewHeadPositionVariable ?variable .
  ?variable uq:hasDistribution ?literal
}
```

The standard deviation of a measured position is an assumption: 0.1 mm, the example value that the
seed gives the robot (`uns:hasPositionStandardDeviation`), in mm along the y axis of the robot
frame, as the positions are. A robot without it ends the operation *failed* with the reason, since
the detector cannot write the variable.

Querying the literals by probability is ProbSPARQL's, in the triple store (Wu et al. 2026,
[doi:10.1007/978-3-032-42029-9_19](https://doi.org/10.1007/978-3-032-42029-9_19)), and fusing
measurements is the job of a fusion service (Hoffmann et al. 2026,
[doi:10.5162/sensoren2026/D3-2.1](https://doi.org/10.5162/sensoren2026/D3-2.1)).

### What each part of use case 1 shows, and the test that proves it

Each part below is proved by a test that carries the marker `paper_claim`. Run the tests of this
use case with the tests of use case 2 by the marker, as in [Run the tests](#run-the-tests), or one
file alone: `uv run pytest tests/test_uc1_claims_live.py --dataset <name>`. That file starts
`jms-uc1` itself, on the dataset it is given, and stops it again: stop any other run of use case 1
first. It clears the dataset it is given, as the run of the tests does; its time series go into
the InfluxDB database `jms-kapps`, which the run creates if it is missing.

1. **The services coordinate only through the knowledge graph.** Every change of status, handover
   and result is in the knowledge graph; each service is started with no address of another; the
   planner hands each operation to the service that realizes the capability the operation
   implements, and the screwing resource finds the detector by its capability. *Run:*
   `uv run jms-uc1` and press Unscrew for both screws. *See it:* the page shows five rows, each
   with its state and, for a service, its links, and query Q3 below lists each operation with its issuer, its status and its
   outcome. *Test:*
   `tests/test_uc1_claims_live.py::test_the_participants_coordinate_through_the_graph`.
2. **The detector fetches the screw type's thresholds through the OGM at every attempt.** *Run:*
   press Unscrew for `Screw1`. *See it:* one line of the terminal for each attempt,
   `Anomaly detector reads Screw1 with EFFECTIVE_VALUES_SCOPE …`, and the detector's dashboard shows
   the values of the decision. *Test:*
   `tests/test_uc1_claims_live.py::test_the_detector_reads_the_screw_types_thresholds_through_the_mapper_at_every_attempt`.
3. **The learner commits new values from completed operations, and the detector uses them next.**
   *Run:* press Unscrew for `Screw1`, wait until the learner's box shows a change, then press
   Unscrew for `Screw2`. *See it:* the learner's box, and query Q4 below, which gives the maximum
   dynamic torque of `M4`; the decision on `Screw2` used the new value. *Test:*
   `tests/test_uc1_claims_live.py::test_the_learner_commits_a_value_from_completed_operations_and_the_detector_uses_it_next`.
4. **No service writes SPARQL or interprets RDF.** Every write of a service is a commit of its
   middleware instance; its SPARQL queries only read. *Run:* `uv run pytest tests/test_uc1_writes.py`;
   it reads the source of the four services and needs no triple store. *See it:* in the code, under
   `uc1/`, as in [Where to read it in the code](#where-to-read-it-in-the-code). *Test:*
   `tests/test_uc1_writes.py::test_no_service_of_use_case_1_writes_past_its_middleware`.
5. **The sensor channels are a replay of recorded series.** The robot's readings are the samples
   of one of two example recordings, `success` for `Screw1` and `missing_screw` for `Screw2`,
   which the stand-in publishes over MQTT. The screwing resource builds the time series of the
   attempt from them in InfluxDB, and the operation holds a reference to them. *Run:* press
   Unscrew. *See it:* the operation's `hasTimeSeriesReference` in query Q3, and the screwing
   resource's dashboard, which names the frame. *Test:*
   `tests/test_uc1_claims_live.py::test_the_sensor_channels_are_a_replay_of_recorded_series`.
6. **A measured position travels through the knowledge graph as a random variable.** With the
   mode of the section above on, each position the detector measures is committed as a random
   variable with a Gaussian-mixture literal beside its best estimate, and read back cleanly: each
   literal has the datatype `uq:gmmLiteral`, parses, and holds the best estimate as its mean.
   *Run:* `uv run jms-uc1 --save-measurements-as-uq` and press Unscrew for both screws. *See it:*
   the page's *Measured positions*, and queries Q5 and Q6 above. This test starts `jms-uc1` itself
   with the mode on, as the file above does with the default. *Test:*
   `tests/test_uc1_measured_positions_claim_live.py::test_each_measured_position_reads_back_as_a_gaussian_mixture_literal`.

Two queries for the triple store's workbench, on the dataset of the run. **Q3** lists each
operation with its screw, the service that issued it, its status, its outcome and the reference to
its time series:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX svc: <http://w3id.org/circularfactory/Service#>
SELECT ?operation ?screw ?issuer ?status ?outcome ?curves WHERE {
  ?operation a uns:UnscrewingOperation ;
             uns:concernsScrew ?screw .
  OPTIONAL { ?operation svc:issuedBy ?issuer }
  OPTIONAL { ?operation svc:hasOperationStatus ?status }
  OPTIONAL { ?operation uns:hasDetectionResult ?outcome }
  OPTIONAL { ?operation uns:hasTimeSeriesReference ?curves }
}
ORDER BY ?screw
```

**Q4** gives the maximum dynamic torque that the screw type `M4` holds. After the learner's first
change it differs from the seeded value:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
SELECT ?torque WHERE { uns:M4 uns:hasMaxDynamicTorque ?torque }
```

### See admission refuse a planned operation that the factory information state cannot carry out

One command shows it, on a dataset it clears and seeds as `jms-uc1` does:

```bash
uv run jms-uc1 infeasible-operation --dataset <name> --record refusal.nq
```

Besides the demo, the seed writes the Screw type `M3`, which holds none of the seven values an
attempt needs, the assembly type `AG2` that expects it, the Screw `Screw3` and the grinder
`Grinder101` that contains it. The command starts the five participants, waits until each is
ready, and presses Unscrew twice on the planner. Each press is the planning decision: the planner
issues an unscrewing operation for the Screw, and the triple store admits or refuses the planner's
commit against use case 1's shapes.

1. Unscrew on `Screw1`, whose Screw type `M4` holds the seven values. The store admits the
   operation, and it ends `done`.
2. Unscrew on `Screw3`. The store refuses the commit by `UnscrewingOperationScrewShape`: the
   operation is not written and no resource is notified.

The command prints the store's answer to each press, then stops the participants:

```text
1. Press Unscrew on Screw1, whose Screw type M4 holds the seven values the attempt needs
   the store: admitted; UnscrewingOperation_<hex> ended done, outcome ScrewIntact
2. Press Unscrew on Screw3, whose Screw type M3 holds none of the seven values
   the store: refused, by UnscrewingOperationScrewShape at UnscrewingOperation_<hex>: An unscrewing operation concerns exactly one Screw, whose Screw type holds each of the seven values the attempt needs (the six detection thresholds and the preparation turning angle), once each.
   the store's validation report:
     …
   the report is written to refusal.nq
Stopping the five participants...
```

It exits 0 when the first operation ended `done` and the second was refused by that shape, and 1
otherwise, naming the step that did not. `--record FILE` writes the validation report as the store
sent it. The planner's dashboard shows the refused Screw with the rule and its message, and the
planner's middleware lists the refused write in its box "Refused writes". The planner serves its
record of the presses as JSON at `/presses`, which the command reads.

The recorded report, `tests/reports/infeasible-operation.nq`, holds one validation result: its
focus node is the refused operation, its path `:concernsScrew`, its value `:Screw3`, its source
shape `:UnscrewingOperationScrewShape` and its message the one above. The shape is in use case 1's
shapes file, `integration_usecase_shapes.ttl`.

*Test:*
`tests/test_uc1_infeasible_operation_live.py::test_the_store_refuses_the_infeasible_operation_by_the_shape`.

### Stop use case 1

Ctrl+C in the terminal of `jms-uc1`: the first signal stops all five processes, and each of the
four services deregisters in the knowledge graph. The local triple store and InfluxDB keep running; the next run
clears the dataset and seeds it again.

### How the screenshot of use case 1 was taken

From the running page of use case 1, opened in a headless browser once all four services showed live with an address, and saved whole.

## Protocol mediation: the transferunits demo of kapps_semantic_middleware

### What it shows

Simulated conveyor controllers speak only MQTT. Each middleware instance reads the broker and the
topics of each parameter from the knowledge graph and builds its connectors from them. The payloads
arrive in the instance's data model as values of the deployed ontology. The demo ships inside
`kapps-semantic-middleware`: a factory of two TransferUnits, each a conveyor with two belts and two
light barriers, with one middleware instance for each unit and a control station that finds the
units in the knowledge graph. [Use case 1](#the-robot-over-mqtt) shows protocol mediation a second
time, on the robot of the screwing resource.

### Run it

The commands are tested on Linux. Start the local stack, which holds the dataset `kapps-demo` beside
`jms-kapps`, and point the commands at it, as the README's setup block does:

```bash
docker compose up -d --wait
export TRIPLESTORE_URL=http://localhost:7200 TRIPLESTORE_USERNAME=admin TRIPLESTORE_PASSWORD=root
```

Install the demo's packages with the extra `transferunits-demo`, which requires the middleware's
extra `examples`, and start the factory with two
units on the dataset `kapps-demo`:

```bash
uv sync --locked --extra transferunits-demo
uv run --no-sync kapps-transferunit-factory --units 2 --dataset kapps-demo
```

The factory prints `Factory running. Index page at http://127.0.0.1:8080/`. That page lists every
process of the factory with its address: a PLC and a middleware instance for each unit, the control
station and a watchdog. Ctrl+C in the terminal stops the factory, and each middleware instance
removes its address from the knowledge graph on the way out.

The demo's seeding clears the dataset it runs on. On a triple store of your own, create the dataset `kapps-demo` from `graphdb/kapps-demo-repo-config.ttl` in the clone, and
set `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` for the command, because the demo's seeding clears its
dataset.

### The description a connector is built from

In the triple store's workbench, choose the dataset `kapps-demo` and open *SPARQL* (the workbench
is on <http://localhost:7200> for the local store). This query reads the connection description of
the speed of the left belt of unit 1:

```sparql
PREFIX tu: <http://www.sfb1574.kit.edu/ontologies/TransferUnit#>
PREFIX tui: <http://www.sfb1574.kit.edu/ontologies/TransferUnitInstances#>
PREFIX inf: <http://www.sfb1574.kit.edu/ontologies/CrcInterfaces#>
SELECT ?broker ?port ?topic WHERE {
  tui:ConveyorBelt1_left tu:hasConveyorSpeed ?speed .
  ?speed inf:hasMQTTBrokerIP ?broker ;
         inf:hasMQTTBrokerPort ?port ;
         inf:hasMQTTTopic ?topic .
}
```

The answer is one row: the broker `127.0.0.1`, the port `18831` and the topic
`TransferUnit1/ConveyorBelt/left/speed`. The middleware instance of unit 1 starts the broker at that
address and subscribes to that topic.

This query lists the addresses of the services that are registered while the factory runs:

```sparql
PREFIX svc: <http://w3id.org/circularfactory/Service#>
SELECT ?service ?address WHERE { ?service svc:hasAddress ?address }
```

It answers with the middleware instances and the control station, and with no row after the stop.

### The value in the data model

Open the PLC of unit 1 from the index page. Its panel sets the speed of a belt. The belt ramps up
at 1 m/s each second, so a speed of 3 m/s takes about three seconds.

The middleware instance of unit 1 serves its data model over REST, on the address that the index page
shows for it. Open `<address>/docs` for the list of routes. The parameter route of the left belt's
speed is

```text
<address>/http_c__s__s_www_d_sfb1574_d_kit_d_edu_s_ontologies_s_TransferUnit_h_TransferUnit/http_c__s__s_www_d_sfb1574_d_kit_d_edu_s_ontologies_s_TransferUnitInstances_h_TransferUnit1/http_c__s__s_www_d_sfb1574_d_kit_d_edu_s_ontologies_s_TransferUnit_h_hasConveyorBelt/http_c__s__s_www_d_sfb1574_d_kit_d_edu_s_ontologies_s_TransferUnitInstances_h_ConveyorBelt1__left/http_c__s__s_www_d_sfb1574_d_kit_d_edu_s_ontologies_s_TransferUnit_h_hasConveyorSpeed
```

and answers with the parameter: its `hasValue`, `hasUnit` and `accessMode`. Set a belt speed on the
panel and read the route again a few seconds later: `hasValue` has moved to the new speed. The
control station shows the same values. Open it from the index page, expand the card of unit 1, and
turn on *show IRIs* to see each value beside its name in the knowledge graph.

The value in the answer belongs to the ontology's property `hasConveyorSpeed`. The device
publishes its reading on the topic, and the middleware instance puts it into the parameter node of
that property.

### What the demo covers

- Values travel inward, from the device through its connector into the data model.
- A belt is set on the machine's own panel.
- The served data model answers reads only. A write verb gets `405`.
- The live values are in the data model of each instance. The knowledge graph holds each
  connector's description, and the registered address and the heartbeat of each service.

The source of the demo is in the folder `demo/transferunits` of
[kapps-semantic-middleware at v0.2.6](https://github.com/circularfactory/kapps_semantic_middleware/tree/v0.2.6/demo/transferunits).

## Run the tests

With the variables of [Set up](#set-up) unset, the tests that need no triple store run:

```bash
uv run pytest
```

With them set, the tests of what the use cases show run against the triple store, by their marker:

```bash
uv run pytest -m paper_claim --dataset jms-kapps
```

The run clears the dataset that `--dataset` names, here `jms-kapps`, the dataset of the use cases:
stop any run of a use case first, since a test and a run on one dataset disturb each other. The
next `./run-uc2` seeds the dataset again. On another triple store, name a dataset that
nothing else uses; the tests have no default dataset. The run takes a few minutes, and every test passes. Without the variables, every test that
needs the triple store is skipped, and the run shows nothing about a knowledge graph.
[Tests](reference/tests.md) lists the markers and the other runs.

The OGM's own tests, of the types and maximum counts it reads from OWL, run in a checkout of
`kapps_ogm`: `uv run pytest -m "not live" tests/unit/test_class_restriction_maximum.py
tests/unit/test_single_valued_property.py tests/unit/test_owl_minimums_not_required.py`.
