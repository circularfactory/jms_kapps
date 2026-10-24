# The assets, and where they come from

The ontologies, use case 2's SHACL shapes and the time-series recordings come from the paper's
first artifact, the *first iteration* of these use cases: the `paper` branch of
[CPPS_Circular_factory_usecases_JMS](https://github.com/EHoffm/CPPS_Circular_factory_usecases_JMS),
at commit `d32011805be9199291eec0731acab241e5bd74f1` of 2026-05-27. They were copied without a
change of a single byte, and only the file names changed: the name of the source file for use case
2 contains a spelling error, which the copy corrects, and the `_JMS` part of each name is removed.

| This repository | The source artifact |
|---|---|
| `src/jms_kapps/uc1/ontology/integration_usecase.ttl` | `turtle/integration_usecase_JMS.ttl` |
| `src/jms_kapps/uc1/shapes/integration_usecase_shapes.ttl` | none: written for these use cases; its shapes require the provenance of each parameter copy, and exactly one resource for each capability |
| `src/jms_kapps/uc1/data/unscrewing_timeseries/success.csv` | `Usecase_Vertical_integration/unscrewing_timeseries/success.csv` |
| `src/jms_kapps/uc1/data/unscrewing_timeseries/missing_screw.csv` | `Usecase_Vertical_integration/unscrewing_timeseries/missing_screw.csv` |
| `src/jms_kapps/uc2/ontology/flexconveyor_usecase.ttl` | `turtle/fleyconveyor_usecase_JMS.ttl` |
| `src/jms_kapps/uc2/shapes/flexconveyor_usecase_shapes.ttl` | `turtle/flexconveyor_usecase_JMS_SHACL.ttl` |
| `src/jms_kapps/uc2/topologies/cross.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_preview_cross.json` |
| `src/jms_kapps/uc2/topologies/pair.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_preview_minimal.json` |
| `src/jms_kapps/uc2/topologies/tee.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_preview.json` |
| `src/jms_kapps/uc2/topologies/grid.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_30.json` |

## What has changed since

**The two files of use case 2 have been changed** to run on the semantic middleware: the ontology is
aligned with the Circular Factory Core Ontology (`cfc:`) and the semantic middleware's service
ontology, possession is the Core Ontology's, a box has no lifecycle state, and the rules that
depended on that state are SHACL Core shapes keyed on the box's transport. The head of each file
lists what changed.

**One shapes file has no source.** `src/jms_kapps/uc2/shapes/flexconveyor_usecase_sparql_shapes.ttl`
is new: it holds use case 2's one cross-entity constraint that SHACL Core cannot state, a
SHACL-SPARQL shape, which is loaded on request.

**The ontology of use case 1 has been changed too**, to the values that the decision tree in Figure
2 of the anomaly-detection paper of use case 1 needs: a Screw type and a Screw, ten single-valued
properties in place of five (the Detection thresholds, the placement values and the preparation
turning angle), a parameter copy, named outcomes in place of the free-text success status, and an
unscrewing operation that is a `cfc:Operation`. The angle grinder is a `cfc:Assembly` and a
`cfc:Product`. Its assembly type names the expected Screw type through `:hasExpectedScrewType`. A Screw is a
`cfc:Component` and the Screw type a `cfc:ComponentType`, as Core 0.9.0 defines them. The
operation links to its workpiece through `:concernsWorkpiece`, and the Screw links back to its operation
through `:hasOperation`. The head of the file lists what changed. The two recordings of use case 1
are byte-identical.

**The middleware's operation-status shape is loaded beside them.** The semantic middleware ships
one shape for the service ontology: every operation has exactly one status, one of `queued`,
`running`, `done` and `failed`. The seed of a demonstration or a test loads that shape into the
dataset, the same way it loads the middleware's service ontology. It joins the shapes document of
each seed, use case 1's and use case 2's alike, and the evaluation's prefill run is seeded the
same way, so the store refuses, at commit, an operation without a status, with two, or with
another value. A dataset seeded without shapes (the evaluation's SHACL-off twin) gets none. A
productive circular factory already holds these ontologies and shapes in its knowledge graph; the
files shipped here seed the demonstrations and tests, and document the vocabulary. Use case 2 has
its own status shape.

**The four topologies are converted**: each is a module list in the source's own
format, with a label on each module. `cross` keeps the source's five modules, connections and roles:
two entry and two exit modules. The other three keep the
source's shapes, but each link runs one way. See
[The shipped topologies](topologies.md#the-shipped-topologies), which also describes the fifth,
`merge-fault-demo`, which is not from the artifact.

## The Python

The Python of the source artifact is written against `semantic_service` and against version 0.2.6
of `semantic_middleware`, which the semantic middleware (`kapps-semantic-middleware`) replaces. So
the Python of both use cases is new. The RDF and the recordings are copied from the paper's.
