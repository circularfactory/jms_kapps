# The Launcher and the pages of a run

`jms-uc2` runs use case 2 by hand. With the connection variables of
[The triple store](triple-store.md) set, it runs on that store:

```bash
uv run jms-uc2                      # the cross of five modules
uv run jms-uc2 --topology grid      # a shipped one: cross, grid, merge-fault-demo, pair or tee
uv run jms-uc2 --topology my.json   # any other topology, a module list
uv run jms-uc2 --transit-time 1     # a module with no transit time of its own takes 1 s (default 3 s)
uv run jms-uc2 --retry-time 0.5     # a module asks an occupied module again after 0.5 s (default 1 s)
uv run jms-uc2 --collect-history    # the store records every change, to read past possessions
uv run jms-uc2 --deadlock-alarm-seconds 60  # a box that waits 60 s raises the deadlock alarm (default 300 s)
```

It **clears the dataset it runs against**: `jms-kapps`, unless `--dataset` names another. The
topologies and their editor are in [Topologies](topologies.md), the strategies in
[A box's journey](journey-and-faults.md#the-strategies), and past possessions in
[Past possessions](past-possessions.md).

## What `jms-uc2` starts

`jms-uc2` seeds the topology, starts one FlexConveyorModule process for each module, one
WarehouseManagementSystem process and one monitor process, and serves **the Launcher page on
<http://127.0.0.1:8090/>**. Each process is a *participant*: a program with its own middleware
instance, which registers a *service* in the graph, its address and its workflows, and writes a
*heartbeat* there, a time stamp it renews while it runs.

Every process prints its command line, so it can be run on its own, and its output goes to the
terminal with its name in front. The line is in the form of the platform's shell: on POSIX it runs
when pasted into a POSIX shell, and on Windows into cmd.exe or PowerShell, where a line whose
program path holds a space, and is quoted therefore, runs with `& ` in front of it. How a run
stops is in [Stopping](stopping.md).

Both pages, the Launcher's and the monitor's, are plain HTML, CSS and JavaScript in a `static/`
folder, and learn everything from a JSON API; no HTML is generated in Python. A second `jms-uc2`
refuses to run while the first is live in the same dataset, unless `--force` is given. On the same
machine a second `jms-uc2` is refused in any case, on Windows as on POSIX, because the first holds
port 8090; once the first has stopped, the next finds the port free.

## The Launcher page

The Launcher page lists every process with its state and its recent output. It links to the API
docs, the activity feed, the dashboard (the middleware's page of what that process holds) and the
logs (that page on its Logs tab) of each module and of the WMS, each through the Launcher's own
port. It arms each module's two faults, and stops one process or all of them. It shows the running
topology, and it launches any topology this package ships, which clears the dataset and restarts
every process. **A topology of your own is built in the page's topology editor**, or opened there
from a file, or goes in from a file at the command line, with `--topology`: see
[Topologies](topologies.md).

- **The checkbox collect history**, beside the topology picker, has the next launch turn on the
  store's history collection, so that the possessor a box had at any earlier time can be read: see
  [Past possessions](past-possessions.md). It starts at whether the launch that runs was launched
  with it, so on where `jms-uc2 --collect-history` started the page and off by default, and the
  page says "collects history" while that launch was launched with it.
- **The boxes on the station.** Under the processes, the page lists every box whose possessor is
  not the WMS, with the module that possesses it, and the box's transport and that transport's
  status, from one SPARQL query on the run's dataset. It shows that query's text exactly as the
  Launcher sent it, with a **Copy the query** button. The query is standard SPARQL 1.1: it declares
  its own prefixes, and names no graph and calls no function that only one store knows, so it runs
  unchanged on the same dataset in the store's workbench, or in any other SPARQL endpoint, and
  gives the same list: paste it into the workbench's SPARQL editor with the run's dataset chosen.
  The Launcher sends it in its background read, once a second while it serves the page, whether a
  page is open or not, and a poll of the page sends no query. A read that fails keeps the list read
  last, and the page says why. The monitor's table of boxes lists the same boxes, beside those the
  WMS possesses.
- **The activity pages.** A module's or the WMS's activity page shows, as they happen, the steps
  that process takes, from INFO up, beside the middleware's own entries: a Reserve asked and its
  answer, a box conveyed, a Receive, a refusal with its reason, a change of a transport's status.
  The middleware's own entries include its record of each write the store refused: its first line
  names the verb and the rules, such as `handover refused by FlexConveyorModulePossessionShape at
  Module3`, and the readable results and the whole report follow.
- **What it cannot confirm.** While the Launcher cannot read the graph, each state that rests on a
  heartbeat is shown as last read and marked with when it was last confirmed, and a banner says
  why. While the Launcher itself does not answer, the banner says so, and the page keeps what the
  Launcher said last.
- **A part it cannot draw.** The page draws each of its parts on its own from the Launcher's latest
  answer: the header with its links, the processes, the status banner, the boxes on the station,
  the fields of the next launch, and the topology with its table. Where it cannot draw one, it says
  so under the banner, with the part's name and why, and draws the others all the same; the message
  goes once that part draws again.
- **A launch that would clear another run.** A launch clears nothing while a service in the dataset
  is live that is not one of this Launcher's own killed or failed processes: another launcher's,
  or that of a module run on its own from its command line. It answers 409 (the HTTP code for a
  conflict), the page says which service is live, and this Launcher's processes stay stopped.

### The states of a process

- **`starting`** until its service is ready in the graph: it has an address, a fresh heartbeat and
  every workflow the process registers, a module's Reserve, Receive and BoxTransport workflows and
  the WMS's two spawn workflows; the monitor registers none.
- **`live`** while its service stays so, so a spawn sent the moment the WMS turns `live` finds its
  spawn workflow.
- **`failed`** when it exits without being asked, when its service stops being ready, as when its
  heartbeat goes *stale* (90 s old) and it is *offline*, or when it does not turn
  live within 60 s. The terminal then shows one line with its name and why, with its exit code if
  it exited.
- **`stopped`** when it exited on a stop, so it *deregistered* its service first: it removed its
  address from the graph.
- **`killed`** when it did not exit within the stop timeout of 10 s.

The Launcher finds an exit within a second without reading the graph, so a read of the graph that
hangs, for up to the client's timeout of 60 s, does not hide it. Which exits count as a stop, and
what a kill leaves in the graph, is in [Stopping](stopping.md).

### Clear

**Clear**, beside **Stop all**, stops every process and then empties the run's dataset, so that a
person starts again from an empty store without leaving the page. It asks first: "Clear stops
every process and empties dataset <dataset> on <store>. Go on?".

Every process is stopped first, then every graph of the dataset is cleared, one by one: the default
graph, the named graphs of the middleware's shared ontologies, then the shapes, which the store's
shapes registry removes, and every other named graph the store lists, as one a person loaded by
hand, or the one in which the middleware keeps the receipts of writes that a stopped process left,
`urn:x-kapps:write-receipts`. Never in one request for the whole dataset, which the store refuses
while it collects history (see [Past possessions](past-possessions.md)).

The page then says what the store holds: "<dataset> is empty: 0 statements", the statements
written, without what the reasoner infers. The processes stay listed, stopped, and no topology
shows as seeded; the next launch loads the ontologies, the shapes and the topology again. Clear
meets the guard of a launch: it clears nothing while a service in the dataset is live that is not
one of this Launcher's own, answers 409, and the page says which, and why. A graph or shapes that
the store does not clear stop the clear, and the page names them and the store's reason. Through
the Launcher's server it is `POST /api/clear`, which answers the dataset and its count of
statements.

## A module's dashboard

Each module's dashboard, which the Launcher page links in the module's row, has a tab named after
the module beside the middleware's own boxes, with three dashboard boxes. Each shows what the
module keeps in memory, so an open tab causes no load.

- **The module from above** draws it as a real FlexConveyor module looks: a green frame, the silver
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
  or when it started, as a classic adjacency matrix drawn to the width of the box: a row and a
  column for each module, in the order of their numbers. A row is the module a connection leaves,
  a column the module it leads to, and the cell says the side it leaves by, N, E, S or W, or •
  where the graph names none, with ↔ where the two modules connect both ways. The module's own row
  is marked. The matrix takes the whole width of the tab, as Routing decisions does, so that a
  station of many modules draws cells large enough to read; a small station's matrix grows to at
  most two and a half times its own size.

The module keeps each decision as it makes it, from the read of the connections that the decision
makes anyway. Its process makes one read of its own when it starts: the connections, for the
drawing and the matrix only, which route nothing, and the boxes the module possesses, so that a
module started again shows the box it holds. A module that is a box's origin shows the box only
once it has committed the box's transport `running`, a moment after the box arrived.

The WMS's dashboard is described with the spawn, in
[A box's journey](journey-and-faults.md#spawning-a-box).

## The monitor

The monitor is its own process behind its own middleware, which registers it as a plain resource
with a plain service, no classes of its own, and sends its heartbeat. The Launcher page links to
it. Its own code writes nothing.

In the background it reads the graph every second, or at the interval set on its page, from 0.5 s
to 10 s: the topology from the connections, each module live or offline from its service, each box
with its transport's status, origin and destination and the module that possesses it, and the WMS
by its class.

It has two modes, which a switch on its page sets: **graph + streams**, in which it starts, and
**graph only**.

- In **graph + streams** it finds the participants' handover event streams through the graph every
  5 s, and follows those of the live ones (each stream is SSE, server-sent events: a web page's
  feed that the server pushes), so its Refusals view shows each commit the store refused: when, the
  giving module, the box and the hop, then one row for each result of the store's validation
  report: the refusing shape's message, then the focus node, the path and the value, then the shape
  and the constraint, with the severity as a badge. Each name is short, with its whole IRI as its
  tooltip. The report itself, as the store sent it, is behind "report", and is shown open, with a
  note, when the monitor cannot read it. A refusal that arrives while the page is open is
  highlighted for a few seconds. Its view **Possible deadlock** shows one notice that names every
  module involved, each module that raised a deadlock alarm and the modules it asks, with each
  alarm's line, for the alarms whose box the graph still shows on that module; an alarm whose box
  has moved is dropped.
- In **graph only** it sees what a dashboard sees that sees only the knowledge graph: it follows no
  stream and sends no query for them. Its Refusals view says why it is empty: a refused write
  leaves no trace in the graph. It shows no deadlock notice, and says why: an alarm leaves no trace
  in the graph. Back in graph + streams it follows the streams again at once, and shows the
  refusals it kept and those the participants emitted meanwhile.

Every other view comes from its reads of the graph, in both modes.

- **Its drawing** gives each connection an arrowhead where it leads, and a double head where it
  leads both ways, from the connections alone. It draws each module from above, as a real
  FlexConveyor module looks, with no light barrier drawn. It tints the ground of a live module that
  holds a box, and puts a disc on the box's destination, which its transport names, with the first
  four characters of the box's UUID, in the box's colour, which comes from its UUID; its table of
  the boxes lists them by name. It draws no route: only the module that holds a box knows where the
  box goes next, and it decides that again at every hop.
- **Its Handovers view** lists, newest first, each handover it saw by its commit: a box whose
  possessor changed between two of its reads, or a new box on its origin, which the WMS handed it
  to. A box handed over twice between two reads, which a transit time under a second allows, shows
  as one handover. A box already on its origin at the monitor's first read gets no handover row,
  since that read has nothing to compare with.
- **Its panel Queries sent** shows each SPARQL query it sends, exactly as the store received it the
  last time it went: how often it goes now, when it went last, how long the store took to answer
  and how many rows came back, with a button that copies the text. A page poll reads only what the
  monitor holds, and sends no query.

**It is an interim surface.** The monitoring views move onto the middleware's own dashboard
registry, which the dashboards of the modules and the WMS already use, and this page goes when
they do.
