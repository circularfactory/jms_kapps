# Glossary

The words that the README, the reviewer guide and these pages use, each in a few lines.

## The paper's words and the demo's

| The paper says | The demo shows |
|---|---|
| service | a participant's process, with its own middleware instance |
| operation | a box's transport, in use case 2; an unscrewing operation, in use case 1 |
| fault injection | the two faults a person arms on a module: `skip-reserve` and `complete-without-releasing` |
| the OGM | the object-graph mapper, which turns the middleware's Python objects into triples and back |
| the knowledge graph | the graph: the dataset of the triple store that a run uses |

## Terms

**Box.** The workpiece of use case 2. It travels from the WMS through the station and back.

**Dataset.** The triple store's unit: one set of graphs that a run clears and seeds, and that
every participant of the run reads and writes. `--dataset` names it; `jms-kapps` by default.

**Deregister.** A participant removes its service's address from the graph when it stops, so that
no other participant calls it. A kill or a crash skips it: see [Stopping](stopping.md).

**Fault.** One of the two switches of a module, which make it write what the shapes forbid, so
that the store refuses the write: see [The faults](journey-and-faults.md#the-faults).

**First iteration.** The paper's own artifact, from which the assets are copied and whose
topology format the demo keeps: see [The assets](assets.md).

**Frame.** The raw curves of one unscrewing attempt of use case 1, kept in InfluxDB.

**Handover.** One box passing from one party to the next: the receiving module consents, the giving
module commits the change of possession, then the box moves, then it arrives.

**Heartbeat.** A time stamp that a participant writes into the graph and renews while it runs. A
service with a fresh heartbeat counts as live.

**Hop.** One step of a box's journey, from one module to its neighbour: two calls, Reserve and
Receive, and one commit.

**IRI.** The name of a thing in the graph, a web address such as
`http://w3id.org/circularfactory/FlexConveyorInstances#Module1`, written short with a prefix as
`fci:Module1`.

**Launcher.** The process of `jms-uc2` that starts the participants of a run and serves the page
to watch and steer them, on <http://127.0.0.1:8090/>: see [The Launcher](launcher.md).

**Live, offline.** A participant is live while its service has an address and a heartbeat younger
than 90 s. It is offline while its service is not live: it deregistered, or its heartbeat is
stale, as a kill or a crash leaves it.

**Module.** One FlexConveyor module of use case 2, run as its own process: it reserves, conveys and
receives boxes.

**Monitor.** The participant that reads the graph and draws the station. Its own code writes
nothing: see [The monitor](launcher.md#the-monitor).

**Participant.** A program with its own middleware instance, which registers a service in the
graph: each module, the WMS and the monitor in use case 2; the screwing resource, the anomaly
detector, the learner and the planner in use case 1.

**Possessor.** The one participant that possesses a box: a module while the box travels, and the
WMS at the end of its journey. A module possesses at most one box.

**Service.** A participant's entry in the graph: its address, its workflows and its heartbeat.

**SHACL shape.** A rule that the triple store checks at every commit, written in SHACL, the W3C's
language for such rules. A write that breaks a shape is refused before it is stored, and the store
answers with a validation report that names the shape.

**SPARQL.** The query language of the triple store.

**SSE.** Server-sent events: a stream that a web server pushes to its reader. Each participant of
use case 2 serves its handover events as one.

**Stale.** A heartbeat 90 s old or older. Only a heartbeat is stale: a participant whose heartbeat
is stale is offline.

**Strategy.** Who chooses where a box goes: the module strategy picks a box's next module, the WMS
strategy picks a spawn's ends. `real` is the default of both: see
[The strategies](journey-and-faults.md#the-strategies).

**Topology.** The modules of a station and their connections, as a module list: see
[Topologies](topologies.md).

**Transport.** The operation that carries one box, `:BoxTransportOperation`: its status says where
the journey is, and it is assigned to one module at a time.

**WMS.** The WarehouseManagementSystem: the participant that spawns boxes onto the station and
takes them back at their destination.
