# Glossary

## Paper concepts and their instances

| Concept in the paper | Instance in the use cases |
|---|---|
| service | the service that a middleware instance hosts, wrapping one resource |
| planning service, screwing resource, anomaly detection service, learning service | `Planner1` (the planner), `ScrewingResource1`, `AnomalyDetector1` (the anomaly detector), `Learner1` (the learner), in use case 1 |
| parameters of a screw type | the seven values that a screw type holds and an attempt needs, in use case 1 |
| operation | a box's transport, in use case 2; an unscrewing operation, in use case 1 |
| fault injection | the two faults a person arms on a module: `skip-reserve` and `complete-without-releasing` |
| knowledge graph | the dataset of the triple store that a run uses, which holds the ontologies and the individuals |

## Terms

**Admission, refusal.** The triple store refuses a write that breaks a shape in whole, before
anything of it is stored, and returns a validation report that names the shape.

**Best estimate.** The plain number that a measured position holds beside its random variable: the
screw's `:hasMeasuredScrewHeadPosition`.

**Box.** The workpiece of use case 2.

**Capability.** What a resource can do (`cfc:hasCapability`). A workflow realizes one capability,
and an operation runs on the resource of its capability.

**Data object.** An instance of a typed class that the Object-Graph Mapper (OGM) derives at runtime from the deployed
ontology: a projection of the knowledge graph, which a service reads, changes and commits.

**Frame.** The time series of one unscrewing attempt of use case 1 in the time-series database,
InfluxDB.

**Gaussian-mixture literal.** The typed literal (`uq:gmmLiteral`) that holds a random variable's
distribution: a JSON object with the six fields `n_components`, `dimensions`, `covariance_type`,
`weights`, `means` and `covariances`.

**Heartbeat.** A time stamp that a service writes into the knowledge graph and renews while it
runs. A service with a fresh heartbeat counts as live.

**History.** The triple store's own record of every change of a dataset, each with the
transaction that made it and its time, collected once a launch turns it on.

**Inference.** The facts that the triple store derives from the ontologies and the individuals,
under its ruleset and the open-world assumption. Inference adds facts and refuses none.

**IRI.** The persistent name of an individual or a term in the knowledge graph, a web address such
as `http://w3id.org/circularfactory/FlexConveyorInstances#Module1`, written short with a prefix as
`fci:Module1`. The empty prefix `:` is the namespace of the use case's ontology.

**Middleware instance.** One instance of the semantic middleware. It hosts one or more services,
holds their data model, registers them in the knowledge graph and carries their writes to the
triple store.

**Operation.** The implementation of a task on specific resources (`cfc:Operation`). A
notification names an operation's IRI to the middleware instance of the service that must look at
it: in use case 2 the WMS notifies a transport's origin, and a module notifies the WMS when the
transport ends: the destination when the box is back, or the module where it failed. In use case 1
the planner notifies the screwing resource, and the commit that ends the operation notifies the
planner, its issuer.

**Participant.** The code's name for a process of a run, as in the names of the tests and in the
printed lines.

**Possessor.** The one party that possesses a box: a module while the box travels, and the WMS at
the end of its transport.

**Projection scope.** The properties of an individual that a service reads or writes through the
OGM, and no others. The code calls it a *class scope* (`ClassScope`).

**Random variable.** A `uq:RandomVariable`: the way the knowledge graph holds an uncertain value,
with one distribution (a Gaussian-mixture literal) and one value domain.

**Replay.** The recorded updates of the OGM of a run, replayed to the triple store: the row
"replay" of Figure 10.

**Service.** An addressable unit on the network that wraps one resource, decides from the factory
information state and commits its decisions. Its entry in the knowledge graph holds its address and
its workflows.

**SHACL shape.** A constraint in SHACL, the W3C's language for constraints on a graph: a physical
invariant, a required value or a datatype. The shapes of the use cases are SHACL Core.

**Topology.** The modules of a station and their connections, as a module list. The shipped ones
are in `src/jms_kapps/uc2/topologies/`.

**Transport.** The operation that carries one box, `:BoxTransportOperation`: its status says how
far the transport is, and it is assigned to one module at a time.

**Triple store access module.** The library through which the semantic middleware and the OGM
reach the triple store, `kapps_triplestore_interface`; short, the access module.

**Unified Knowledge Base.** The triple store together with the specialized databases: in use case
1, the triple store and the time-series database, InfluxDB.

**Workflow.** The part of a service that realizes one capability of its resource, called at the
address that the knowledge graph holds.

**WMS.** The WarehouseManagementSystem: the service that spawns boxes onto the station and takes
them back at their destination.
