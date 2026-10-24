# The Launcher and the pages of a run

`jms-uc2` runs use case 2 by hand. With the connection variables of
[The triple store](triple-store.md) set, it runs on that triple store:

```bash
uv run jms-uc2                      # the cross of five modules
uv run jms-uc2 --topology grid      # a shipped one: cross, grid, line, merge-fault-demo, pair or tee
uv run jms-uc2 --topology my.json   # any other topology, a module list
uv run jms-uc2 --transit-time 1     # a module with no transit time of its own takes 1 s (default 3 s)
uv run jms-uc2 --retry-time 0.5     # a module asks an occupied module again after 0.5 s (default 1 s)
uv run jms-uc2 --collect-history    # the triple store records every change, for past possessions
uv run jms-uc2 --deadlock-alarm-seconds 60  # a box that waits 60 s raises the deadlock alarm (default 300 s)
```

It **clears the dataset it runs against**: `jms-kapps`, unless `--dataset` names another. The
topologies and their editor are in [Topologies](topologies.md), the strategies in
[A box's journey](journey-and-faults.md#the-strategies), and past possessions in
[Past possessions](past-possessions.md). The terms are defined in the
[glossary](glossary.md).

## What `jms-uc2` starts

`jms-uc2` seeds the topology, starts one FlexConveyorModule process for each module, one
WarehouseManagementSystem (WMS) process and one monitor process, and serves **the Launcher page on
<http://127.0.0.1:8090/>**. Each process is a service (in the code, a *participant*: one process
with its own middleware instance). Its middleware instance registers the service in the knowledge
graph, with its address and its workflows, and writes a *heartbeat* there, a time stamp that it
renews while the service runs.

Every process prints its command line, so that it can be run on its own, and its output goes to
the terminal with its name in front. The line is in the form of the platform's shell: on POSIX it
runs when pasted into a POSIX shell, and on Windows into cmd.exe or PowerShell, where a line whose
program path holds a space, and is therefore quoted, runs with `& ` in front of it. How a run stops
is in [Stopping](stopping.md).

Both pages, the Launcher's and the monitor's, take everything they show from a JSON API. A second
`jms-uc2` refuses to run while the first is live in the same dataset, unless `--force` is given. On
the same machine a second `jms-uc2` is refused in any case, on Windows as on POSIX, because the
first holds port 8090; once the first has stopped, the next finds the port free.

## The Launcher page

The Launcher page lists every process with its state and its recent output. It links to the API
docs, the activity feed, the dashboard (the middleware's page of what that process holds) and the
logs (that page on its Logs tab) of each module and of the WMS, each through the Launcher's own
port. It arms each module's two faults, and stops one process or all of them. Its topology section
shows what a launch will start, and launches it, which clears the dataset and restarts every
process; its panel **Running now** shows what runs. **A topology of one's own is built on the
section's grid in edit mode**, or opened there from a file, or given from a file at the command
line with `--topology`: see [Topologies](topologies.md).

### The topology section

The section has three rows.

- **Choose.** The picker, **Empty grid**, **Upload** and **Edit**. Below them one grid always
  draws the topology chosen in the picker, read-only, as the server lays it out, and draws it again
  at each change of the picker.
- **Edit mode.** **Edit** makes the same grid editable, on the chosen topology; **Empty grid**
  opens it on an empty grid and **Upload** on a topology file. In edit mode **Download** and
  **Close** show in place of **Edit**. The picker, **Empty grid** and **Upload** stay active and
  replace what the grid holds: the chosen topology, an empty grid, or the file. When the edit
  differs from what it opened, the browser asks first: "Discard your changes to the topology
  edited from <origin>?", such as `"pair"`, `an empty grid` or `the file "loop.json"`. Cancel keeps
  the edit and sets the picker back. **Close** asks the same, then the grid draws the chosen
  topology again. An edit that has not changed goes without a question. How a grid is edited is in
  [Topologies](topologies.md#the-topology-editor).
- **Run.** The run settings of the next launch (transit time, deadlock alarm, the two strategies,
  collect history, and Collect the SHACL refusals in one monitor) and one **Launch** button, which
  launches what the grid shows: the chosen topology outside edit mode, the edit in edit mode,
  which it launches only while the load check passes. Every launch asks first, since it clears the
  dataset, and a launch ends edit mode.

**The picker's entry for what runs.** While a topology runs that is not shipped (an edit, an
upload, or a file given with `--topology`), the picker has one more entry for it, by its name:
`pair (edited), running now`. It is selected after such a launch, the grid draws it, **Launch**
launches it again under the same name, and **Edit** continues from it. The entry goes when that
topology no longer runs, because a shipped one was launched or the dataset was cleared; the picker
then falls back to the shipped topology it was edited from, or to the first shipped one. During
an edit the edit stays as it is, and **Close** then shows that fallback.

**Running now** is a panel of its own below the section: the running line (`running: cross`), the
strategies in force, "collects history" where the launch collects it, and the table of the
running topology's modules, each with its label, its types and where it hands boxes to.

The longer explanations are in closed details blocks: **Build your own topology**, **The shipped topologies** and **The run
settings** in the topology section, **The two faults** in the processes section, and **The query
elsewhere** with the boxes on the station.

- **The checkbox collect history**, among the run settings, has the next launch turn on the
  triple store's history collection, so that the possessor a box had at any earlier time can be
  read: see [Past possessions](past-possessions.md). The checkbox starts at whether the launch that
  runs was launched with it, so it is on where `jms-uc2 --collect-history` started the page and off
  by default, and the page says "collects history" while that launch was launched with it.
- **The checkbox Collect the SHACL refusals in one monitor**, beside it, off by default, has the
  next launch start the monitor in graph + streams instead of graph only (see
  [The monitor](#the-monitor)). It starts at whether the launch that runs was launched with it.
- **The boxes on the station.** Under the processes, the page lists every box whose possessor is
  not the WMS, with the module that possesses it, and the box's transport and that transport's
  status, from one SPARQL query on the run's dataset. It shows that query's text exactly as the
  Launcher sent it, with a **Copy the query** button. The query is standard SPARQL 1.1: it declares
  its own prefixes, names no graph and calls no function that only one triple store knows, so it
  runs unchanged on the same dataset in the triple store's workbench, or in any other SPARQL
  endpoint, and gives the same list: paste it into the workbench's SPARQL editor with the run's
  dataset chosen. The Launcher sends it in its background read, once a second while it serves the
  page, whether a page is open or not. A read that fails keeps
  the list read last, and the page says why. The monitor's table of boxes lists the same boxes,
  beside those the WMS possesses.
- **The activity pages.** A module's or the WMS's activity page shows, as they happen, the steps
  that the service takes, from INFO up, beside the middleware's own entries: a Reserve asked and
  its answer, a box conveyed, a Receive, a refusal with its reason, a change of a transport's
  status. The middleware's own entries include one for each write that the triple store refused:
  its first line names the verb and the shapes, such as `handover refused by
  FlexConveyorModulePossessionShape at Module3`, and the readable results and the whole validation
  report follow.
- **What it cannot confirm.** While the Launcher cannot read the knowledge graph, each state that
  rests on a heartbeat is shown as last read and marked with when it was last confirmed, and a
  banner says why. While the Launcher itself does not answer, the banner says so, and the page
  keeps what the Launcher said last.
- **A part it cannot draw.** The page draws each of its parts on its own from the Launcher's latest
  answer: the header with its links, the processes, the status banner, the boxes on the station,
  the fields of the next launch, and the topology with its table. Where it cannot draw one, it says
  so under the banner, with the part's name and why, and draws the others all the same; the message
  goes once that part draws again.
- **A launch that would clear another run.** A launch clears nothing while a service in the dataset
  is live that is not one of this Launcher's own killed or failed processes: another launcher's, or
  that of a module run on its own from its command line. It answers 409 (the HTTP code for a
  conflict), the page says which service is live, and this Launcher's processes stay stopped.

### The states of a process

- **`starting`** until its service is ready in the knowledge graph: it has an address, a fresh
  heartbeat and every workflow the process registers, a module's Reserve, Receive and BoxTransport
  workflows and the WMS's two spawn workflows; the monitor registers none.
- **`live`** while its service stays ready, so that a spawn sent the moment the WMS turns `live`
  finds its spawn workflow.
- **`failed`** when it exits without being asked, when its service stops being ready, as when its
  heartbeat goes *stale* (90 s old) and the service is *offline*, or when it does not turn live
  within 60 s. The terminal then shows one line with its name and why, with its exit code if it
  exited, and the page shows the same why when a person points at the state, as it shows why a
  process was killed. For a process that still runs but whose service stops being ready, the
  Launcher reads that one service once more, live or not, and the line says which part of ready
  it misses:
  - "its last heartbeat is <n> s old": the heartbeat is older than 90 s, as after a kill or a
    hang;
  - "its service has no address": the service *deregistered*;
  - "its service has no <workflow class> any more": a workflow the process registers is gone,
    named by the local name of each class that is missing;
  - "its service is not in the knowledge graph": no service of its resource is found, as after a
    clear.

  If that read fails, or finds the service ready again, the line says only "its service is not
  ready any more". The process is `failed` all the same.
- **`stopped`** when it exited on a stop, so that it *deregistered* its service first: it removed
  the service's address from the knowledge graph.
- **`killed`** when it did not exit within the stop timeout of 10 s.

The Launcher finds an exit within a second without reading the knowledge graph, so that a read
that hangs, for up to the client's timeout of 60 s, does not hide it. Which exits count as a stop,
and what a kill leaves in the knowledge graph, is in [Stopping](stopping.md).

### Clear

**Clear**, beside **Stop all**, stops every process and then empties the run's dataset, so that a
person starts again from an empty dataset without leaving the page. It asks first: "Clear stops
every process and empties dataset <dataset> on <triple store URL>. Go on?".

Every process is stopped first, then every graph of the dataset is cleared, one by one: the default
graph, the named graphs of the middleware's shared ontologies, then the shapes, which the triple
store's shapes registry removes, and every other named graph that the triple store lists, such as
one a person loaded by hand, or the one in which the middleware keeps the receipts of writes that a
stopped process left, `urn:x-kapps:write-receipts`. It never sends one request for the whole
dataset, which the triple store refuses while it collects history (see
[Past possessions](past-possessions.md)).

The page then says what the dataset holds: "<dataset> is empty: 0 statements", counting the
statements written, without those that inference adds. The processes stay listed, stopped, and no
topology shows as seeded; the next launch loads the ontologies, the shapes and the topology again, as a demonstration
dataset's seed.
Clear meets the guard of a launch: it clears nothing while a service in the dataset is live that is
not one of this Launcher's own, answers 409, and the page says which service and why. A graph or
shapes that the triple store does not clear stop the clear, and the page names them and the triple
store's reason. Through the Launcher's server it is `POST /api/clear`, which answers the dataset
and its count of statements.

## A module's dashboard

Each module's dashboard, which the Launcher page links in the module's row, has a tab named after
the module beside the middleware's own boxes, with three dashboard boxes. Each shows what the
module keeps in memory.

- **The module from above** draws it as a FlexConveyor module looks: a green frame, the silver
  motor rollers, which lie east–west and carry a box north–south, and between them the dark strips
  of the belt conveyors, which carry a box east–west. North is up. The box the module holds lies
  on it, with its name: from the moment the module knows it possesses the box, by its start read, a
  confirmed arrival or, at the origin, the transport committed `running`, until the move of the box
  off it has ended. A transport merely assigned to the module puts no box there. On each side, an
  arrow out points to each module it hands boxes to that way, and an arrow in comes from each
  module that hands boxes to it from there. Below, it names the faults armed.
- **Routing decisions** lists the module's 20 newest decisions, newest first: when, the box, its
  destination, the next module chosen ("back to the WMS" on the destination, or that no path leads
  there), the modules it tied with, which lost to the lowest IRI or to the module reserved ahead,
  the Reserve refusals that followed (`occupied`, `reserved`, `behind`, `blocked`), the detour and
  what came of it ("Module8: taken", "Module8: refused as occupied ×2" or "Module8: not asked"),
  and how many times it waited and asked again.
- **Adjacency matrix** shows the connections the module read at its last decision that read them,
  or when it started, as an adjacency matrix drawn to the width of the box: a row and a column for
  each module, in the order of their numbers. A row is the module a connection leaves, a column the
  module it leads to, and the cell says the side it leaves by, N, E, S or W, or • where the
  knowledge graph names none, with ↔ where the two modules connect both ways. The module's own row
  is marked. The matrix takes the whole width of the tab, as Routing decisions does; a small station's matrix grows to at
  most two and a half times its own size.

The module keeps each decision as it makes it, from the read of the connections that the decision
makes anyway. Its process makes one read of its own when it starts: the connections, for the
drawing and the matrix only, and the boxes the module possesses, so that a
module started again shows the box it holds. A module that is a box's origin shows the box only
once it has committed the box's transport `running`, a moment after the box arrived.

The WMS's dashboard is described with the spawn, in
[A box's journey](journey-and-faults.md#spawning-a-box).

## The monitor

The monitor is a service of its own, with its own middleware instance, which registers it as a
plain resource with a plain service, no classes of its own, and sends its heartbeat. The Launcher
page links to it. Its own code writes nothing.

In the background it reads the knowledge graph every second, or at the interval set on its page,
from 0.5 s to 10 s: the topology from the connections, each module live or offline from its
service, each box with its transport's status, origin and destination and the module that possesses
it, and the WMS by its class.

It has two modes, which the switch "Follow the services' handover event streams" at the head of
its Refusals view sets: off is **graph only**, on is **graph + streams**. The Launcher starts it in
graph only unless the checkbox "Collect the SHACL refusals in one monitor" is ticked. The page shows
the switch in the mode the monitor runs in, also when it is opened directly.

- In **graph + streams** it finds the services' handover event streams through the knowledge graph
  every 5 s, and follows those of the live ones (each stream is SSE, server-sent events: a feed
  that the server pushes to a web page). Its Refusals view shows each commit that the triple store
  refused: when, the giving module, the box and the hop, then one row for each result of the
  validation report: the refusing shape's message, then the focus node, the path and the value,
  then the shape and the constraint, with the severity as a badge. Each name is short, with its
  whole IRI as its tooltip. The report itself, as the triple store sent it, is behind "report", and
  is shown open, with a note, when the monitor cannot read it. A refusal that arrives while the page
  is open is highlighted for a few seconds. Its view **Possible deadlock** shows one notice that
  names every module involved, each module that raised a deadlock alarm and the modules it asks,
  with each alarm's line, for the alarms whose box the knowledge graph still shows on that module;
  an alarm whose box has moved is dropped.
- In **graph only** it sees what a dashboard sees that reads only the knowledge graph: it follows
  no stream and sends no query for them. Its Refusals view says why it is empty: a refused write
  leaves no trace in the knowledge graph. It shows no deadlock notice, and says why: an alarm
  leaves no trace in the knowledge graph. Back in graph + streams it follows the streams again at
  once, and shows the refusals it kept and those the services emitted meanwhile.

Every other view comes from its reads of the knowledge graph, in both modes.

- **Its drawing** gives each connection an arrowhead where it leads, and a double head where it
  leads both ways, from the connections alone. It draws each module from above, as a FlexConveyor
  module looks. It tints the ground of a live module that holds a box,
  and puts a disc on the box's destination, which its transport names, with the first four
  characters of the box's UUID, in the box's colour, which comes from its UUID; its table of the
  boxes lists them by name. It draws no route: only the module that holds a box knows where the
  box goes next.
- **Its Handovers view** lists, newest first, each handover it saw by its commit: a box whose
  possessor changed between two of its reads, or a new box on its origin, which the WMS handed it
  to. A box handed over twice between two reads, which a transit time under a second allows, shows
  as one handover. A box already on its origin at the monitor's first read gets no handover row,
  since that read has nothing to compare with.
- **Its panel Queries sent** shows each SPARQL query it sends, exactly as the triple store received
  it the last time it went: how often it goes now, when it went last, how long the triple store
  took to answer and how many rows came back, with a button that copies the text. A page poll reads
  only what the monitor holds.
