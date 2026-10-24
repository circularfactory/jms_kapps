# What a UC2 module finds in the graph, and what the demo gives it from outside

The participants of use case 2 coordinate through the knowledge graph: a FlexConveyor module, the
WarehouseManagementSystem (WMS) and the monitor each read the facts they need from it, and the
modules hand a box over by committing to it. Not every fact comes from there. The demo's Launcher
starts the processes, and it gives each of them some facts from outside. This page lists both
kinds. The terms are defined in the [glossary](glossary.md).

## From the graph

- **A module's neighbours and its connections.** A module reads the connections of the station at
  every decision about a box, and decides on that answer. The copy it keeps in memory is for its
  dashboard only.
- **A peer's workflow endpoint.** Before every call to another module (for example a Reserve or a
  Receive) the caller reads the endpoint from the graph: the peer's live service with the
  newest heartbeat, so that a process killed before it deregistered, and then restarted, is not
  called at its old address. When the graph names no live workflow of the peer, the call fails with
  that reason.
- **The WarehouseManagementSystem.** A module finds it by its class when it first needs it, and
  then keeps it; no module has its IRI in its code.
- **Which modules exist, and their roles.** The WMS reads the modules, their types (among them the
  roles entry and exit) and their connections at every spawn, to offer and pick an origin and a
  destination that the origin reaches.
- **A module's own transit time, and the longest of any module.** A module reads both once, when it
  starts: its own is how long it conveys a box, and the longest of all enters the bound after which a
  reservation made for an expected box lapses.
- **For the monitor,** the WMS by its class, the modules, their connections, the boxes with their transports and the
  module that holds each, at the interval of its reads. In the mode "graph + streams" only, it also
  reads where each participant serves its handover event stream. Each module and the WMS commits
  that address to the graph when it starts.

## From outside, as a demonstration's commissioning or supervision

- **A module's own IRI, and the store.** A module is given its IRI and the dataset on its command
  line. The triple store's address and credentials are the environment's connection variables (see
  [The triple store](triple-store.md)). A process needs them to start at all; in a productive
  factory they come from its deployment.
- **The IRIs of the WMS and the monitor.** Both are fixed in the code, not given at the start.
  The demo has one WMS and one monitor; a module still
  finds the WMS by its class, not by that name.
- **The strategies and the timing parameters.** The Launcher puts the module strategy, the WMS
  strategy, the modules' retry time and the deadlock alarm time on each process's command line,
  and the monitor's mode. They are settings of the demonstration, chosen by a person at a launch;
  a module that knows no strategy by the name it is given does not start.
- **Starting and stopping.** The Launcher is the demo's process supervisor: it starts every
  process, watches that each is live, and stops them (see [Stopping](stopping.md)). In a
  productive factory a supervisor of the deployment does that.
- **The topology file.** The Launcher writes the topology into the graph once, before the run: the
  demonstration's commissioning of the station. No participant reads the file; each reads the
  station from the graph. See [Topologies](topologies.md).

## Four limits

1. **One machine.** Every participant binds to the loopback address (127.0.0.1), because the
   Launcher passes no host to a process. The address a participant registers is in the graph, but
   a peer on another machine could not reach it, so the discovery works on one machine only.
2. **The faults are held outside the graph.** The Launcher's two faults, skip-reserve and
   complete-without-releasing (see [A box's journey](journey-and-faults.md#the-faults)), are
   set over each module's HTTP endpoint. The Launcher holds what it set, and the module holds the
   switch in its memory; neither writes it to the graph. They exist to demonstrate the store's
   refusals.
3. **Refusals and alarms are not in the graph.** The monitor's refusals and the deadlock alarms
   come from the participants' handover event streams, because a refused commit and an alarm leave
   nothing in the graph. The monitor's default mode, "graph only", reads the graph and nothing
   else, and so shows neither. The modes are in [The monitor](launcher.md#the-monitor).
4. **A module checks only its transit time.** When a module starts, it checks one fact about
   its description in the graph, its transit time: with none, or with more than one, it does not
   start, and the Launcher shows it as failed with the reason (see
   [Topologies](topologies.md#the-transit-time)). It does not check the rest of the description,
   its types, connections and capabilities. A part that is missing shows up only when it is first
   needed, for example as "Module3 has no route to Module7" when a box is bound for a destination
   that its connections do not reach.
