"""The two KAPPS use cases of the JMS paper.

`uc1` is the vertical integration use case: a screwing resource, an anomaly detector and a
learner, coordinated through the knowledge graph. `uc2` is the decentral FlexConveyor use case:
one process for each conveyor module, which hand boxes over to each other. `common` holds what
both of them use.

All Python here is built against `kapps_semantic_middleware`. The ontology, the shapes and the
time-series frames come from the artifact that the paper describes. The Python of that artifact
does not: it is written against a different middleware, and it is not carried forward.
"""

__all__ = ["__version__"]

__version__ = "0.0.1"
