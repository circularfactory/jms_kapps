# Glossary

The terms of the accompanying article, each in a few lines. Where the code names a thing
differently, the entry gives the code name.

## The paper's concepts and their instances in the use cases

| Concept in the paper | Instance in the use cases |
|---|---|
| service | the service that a participant's middleware instance hosts, wrapping one resource |
| operation | a box's transport, in use case 2; an unscrewing operation, in use case 1 |
| fault injection | the two faults a person arms on a module: `skip-reserve` and `complete-without-releasing` |
| the OGM | the Object–Graph Mapper, which turns a service's data objects into triples and back |
| the knowledge graph | the dataset of the triple store that a run uses, which holds the ontologies and the individuals |

## Terms

**Admission, refusal.** The triple store decides at every commit, by SHACL validation against the
declared shapes, whether a write is admitted. A write that breaks a shape is refused in whole,
before anything of it is stored, and the triple store returns a validation report that names the
shape: the refusal.

**Best estimate.** The plain number that a measured position holds beside its random variable: the
Screw's `:hasMeasuredScrewHeadPosition`, or the operation's `:hasMeasuredApproachEndPosition`. It is
the mean of the variable's literal, in mm.

**Box.** The workpiece of use case 2. It travels from the WMS through the station and back.

**Capability.** What a resource can do, linked to exactly one resource (`cfc:hasCapability`). A
workflow realises one capability, and an operation runs on the resource of its capability.

**Data object.** An instance of a typed class that the OGM derives at runtime from the deployed
ontology: a projection of the knowledge graph, which a service reads, changes and commits.

**Dataset.** The triple store's unit of data: one set of named graphs that a run clears and seeds,
and that every service of the run reads and writes. `--dataset` names it; `jms-kapps` by default.
The triple store's own name for it is given in [The local
fallback](triple-store.md#the-local-fallback).

**Deregister.** A service removes its address from the knowledge graph when it stops, so that no
other service calls it. A kill or a crash skips it: see [Stopping](stopping.md).

**Fault.** One of the two switches of a module, which make it write what the shapes forbid, so
that the triple store refuses the write: see [The faults](journey-and-faults.md#the-faults).

**First iteration.** The paper's first artifact of these use cases, from which the assets are copied
and whose topology format the use cases keep: see [The assets](assets.md).

**Frame.** The raw curves of one unscrewing attempt of use case 1, kept in InfluxDB.

**Gaussian-mixture literal.** The typed literal (`uq:gmmLiteral`) that holds a random variable's
distribution: a JSON object with the six fields `n_components`, `dimensions`, `covariance_type`,
`weights`, `means` and `covariances`. The anomaly detector writes one component in one dimension.

**Handover.** One box passing from one party to the next: the receiving module consents, the giving
module commits the change of possession, then the box moves, then it arrives.

**Heartbeat.** A time stamp that a service writes into the knowledge graph and renews while it
runs. A service with a fresh heartbeat counts as live.

**History.** The triple store's own record of every change of a dataset, each with the
transaction that made it and its time, collected once a launch turns it on. A box's past
possessors are read from it, not from the knowledge graph: by a query for one point in time, or
from its change list, which `jms-uc2 trace-box` reads. See [Past possessions](past-possessions.md).

**Hop.** One step of a box's journey, from one module to its neighbour: two calls, Reserve and
Receive, and one commit.

**Infeasible operation.** A planned unscrewing operation of use case 1 that the factory
information state cannot carry out: its Screw's Screw type lacks a value the attempt needs. The
triple store refuses the planner's commit of it by `:UnscrewingOperationScrewShape`.
`jms-uc1 infeasible-operation` shows the refusal.

**Inference.** The facts that the triple store derives from the ontologies and the individuals,
under its ruleset and the open-world assumption. Inference adds facts and refuses none. The demo
store materializes subclass conclusions and the back-link of an `owl:inverseOf`, and the use cases
rely on both. On the demo store, the domain or range of a property types no individual: the KIT
triple store did not type AG1 a `cfc:ProductType` from the range of `cfc:hasProductType`. No read of the use cases relies on a type from a domain or a range. Each class that a read asks for by type is written explicitly when the individual is created, or is reached
through a subclass.

**IRI.** The persistent name of an individual or a term in the knowledge graph, a web address such
as `http://w3id.org/circularfactory/FlexConveyorInstances#Module1`, written short with a prefix as
`fci:Module1`.

**Launcher.** The process of `jms-uc2` that starts the services of a run and serves the page to
watch and steer them, on <http://127.0.0.1:8090/>: see [The Launcher](launcher.md).

**Live, offline.** A service is live while it has an address and a heartbeat younger than 90 s. It
is offline while it is not live: it deregistered, or its heartbeat is stale, as a kill or a crash
leaves it.

**Middleware instance.** One instance of the semantic middleware. It hosts a service, holds its
data model, registers it in the knowledge graph and carries its writes to the triple store.

**Module.** One FlexConveyor module of use case 2, run as its own process: it reserves, conveys and
receives boxes.

**Monitor.** The service that reads the knowledge graph and draws the station. Its own code writes
nothing: see [The monitor](launcher.md#the-monitor).

**Participant.** The code's name for one process with its own middleware instance, which hosts one
service: each module, the WMS and the monitor in use case 2; the screwing resource, the anomaly
detector, the learner and the planner in use case 1.

**Operation.** The implementation of a task on specific resources (`cfc:Operation`): a box's
transport in use case 2, an unscrewing operation in use case 1. A notification names an operation's
IRI to the middleware instance of the service that must look at it: in use case 2 the WMS notifies
a transport's origin, and a module notifies the WMS when the transport ends: the destination when
the box is back, or the module where it failed. A hop between two modules notifies nobody. In use case 1 the planner notifies the screwing resource, and the commit that
ends the operation notifies the planner, its issuer.

**Possessor.** The one party that possesses a box: a module while the box travels, and the WMS at
the end of its journey. A module possesses at most one box.

**Projection scope.** The properties of an individual that a service reads or writes through the
OGM, and no others. The code calls it a *class scope* (`ClassScope`).

**Random variable.** A `uq:RandomVariable`: the way the knowledge graph holds an uncertain value,
with one distribution (a Gaussian-mixture literal) and one value domain. In use case 1 it holds a
measured position.

**Service.** An addressable unit on the network that wraps one resource, decides from the factory
information state and commits its decisions. Its entry in the knowledge graph holds its address,
its workflows and its heartbeat.

**SHACL shape.** The encoding of a physical invariant in SHACL, the W3C's language for constraints
on a graph. The triple store validates every commit against the declared shapes. A shape
constrains one entity (single-entity) or the relation of several (cross-entity). The shapes of the
use cases are SHACL Core, except use case 2's one cross-entity constraint that SHACL Core cannot
state, which is SHACL-SPARQL.

**SPARQL.** The query language of the triple store.

**SSE.** Server-sent events: a stream that a web server pushes to its reader. Each service of use
case 2 serves its handover events as one.

**Stale.** A heartbeat 90 s old or older. A service whose heartbeat is stale is offline.

**Standard deviation of a measured position.** The standard deviation with which the robot takes its
position readings, `:hasPositionStandardDeviation` on the robot: 0.1 mm in the seed, an example
value. The variance of a measured position's literal is its square.

**Strategy.** Who chooses where a box goes: the module strategy picks a box's next module, the WMS
strategy picks a spawn's ends. `real` is the default of both: see
[The strategies](journey-and-faults.md#the-strategies).

**Topology.** The modules of a station and their connections, as a module list: see
[Topologies](topologies.md).

**Transport.** The operation that carries one box, `:BoxTransportOperation`: its status says where
the journey is, and it is assigned to one module at a time.

**Triple store access module.** The library through which the semantic middleware and the OGM
reach the triple store, `kapps_triplestore_interface`; short, the access module.

**Uncertainty quantification.** A value held with its uncertainty, here as a random variable with a
Gaussian-mixture literal. The anomaly detector of use case 1 saves its measured positions this way
when its mode "Save measurements as uncertainty quantifications" is on. See [Measured
positions](use-case-1.md#measured-positions).

**Unified Knowledge Base.** The triple store together with the non-RDF databases: in use case 1,
the triple store and InfluxDB.

**User guide.** The one path from a clean machine to a running use case, and what each page shows:
[the user guide](../user-guide.md).

**Workflow.** The part of a service that realises one capability of its resource, called over
REST at the address that the service registers.

**WMS.** The WarehouseManagementSystem: the service that spawns boxes onto the station and takes
them back at their destination.
