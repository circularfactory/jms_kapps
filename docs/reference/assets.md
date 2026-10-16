# The assets, and where they come from

The ontology, the SHACL shapes and the time-series recordings are the ones the paper describes.
They come from the paper's own artifact, the *first iteration* of these use cases: the `paper`
branch of
[CPPS_Circular_factory_usecases_JMS](https://github.com/EHoffm/CPPS_Circular_factory_usecases_JMS),
at commit `d32011805be9199291eec0731acab241e5bd74f1` of 2026-05-27. They were copied without a
change of a single byte, and only the file names changed: the name of the source file for use case
2 contains a spelling error, which the copy corrects, and the `_JMS` part of each name is removed,
because the name of the repository already says it.

| This repository | The source artifact |
|---|---|
| `src/jms_kapps/uc1/ontology/integration_usecase.ttl` | `turtle/integration_usecase_JMS.ttl` |
| `src/jms_kapps/uc1/data/unscrewing_timeseries/success.csv` | `Usecase_Vertical_integration/unscrewing_timeseries/success.csv` |
| `src/jms_kapps/uc1/data/unscrewing_timeseries/missing_screw.csv` | `Usecase_Vertical_integration/unscrewing_timeseries/missing_screw.csv` |
| `src/jms_kapps/uc2/ontology/flexconveyor_usecase.ttl` | `turtle/fleyconveyor_usecase_JMS.ttl` |
| `src/jms_kapps/uc2/shapes/flexconveyor_usecase_shapes.ttl` | `turtle/flexconveyor_usecase_JMS_SHACL.ttl` |
| `src/jms_kapps/uc2/topologies/cross.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_preview_cross.json` |
| `src/jms_kapps/uc2/topologies/pair.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_preview_minimal.json` |
| `src/jms_kapps/uc2/topologies/tee.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_preview.json` |
| `src/jms_kapps/uc2/topologies/grid.json` | `Usecase_FlexConveyor_decentral/src/Visualizer/modules_30.json` |

## What has changed since

**The two UC2 files have been changed** to run on the middleware: the ontology is aligned with
Core and the middleware's service vocabulary, possession is Core's, a box has no lifecycle state,
and the rules that depended on that state are SHACL Core shapes keyed on the box's transport. The
head of each file lists what changed.

**The UC1 ontology has been changed too**, to the values that the decision tree in Figure 2 of the
UC1 anomaly-detection paper needs: a Screw type and a Screw, ten single-valued properties in place
of five (the Detection thresholds, the placement values and the preparation turning angle), a
decision snapshot, named outcomes in place of the free-text success status, and an unscrewing
operation that is a Core Operation. The angle grinder is a Core Assembly and a Core Product. Its
product type names the expected Screw type through `:hasExpectedScrewType`. The operation links to
its workpiece through `:hasWorkpiece`, and the Screw links back to its operation through
`:hasOperation`. The head of the file lists what changed. The two UC1 recordings are still
byte-identical.

**The four topologies are converted, not copied**: each is a module list in the source's own
format, with a label on each module. `cross` keeps the source's five modules and connections, and
names no roles where the source names two entry and two exit modules. The other three keep the
source's shapes, but each link runs one way. See
[The shipped topologies](topologies.md#the-shipped-topologies), which also describes the fifth,
`merge-fault-demo`, which is not from the artifact.

## The Python is not carried forward

The Python of the source artifact is written against `semantic_service` and against version 0.2.6
of `semantic_middleware`. Both are frozen. Every function they provide has a replacement in
`kapps-semantic-middleware`, so the Python of both use cases is written again from the start. The
recorded RDF and the recorded measurements are a different matter: to type them again is to risk a
difference from the paper, so they are copied.
