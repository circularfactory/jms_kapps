"""What both use cases use.

These things belong here, and nothing else:

- the demo seed that puts the ontologies and shapes into the triplestore,
- the setup of the triplestore itself,
- the one reader of a participant's service in the graph (`services.py`),
- the supervision of a launcher's processes (`supervision.py`),
- the start that every participant's process shares (`process.py`), with its API docs by relative
  paths (`api_docs.py`),
- the one port of a run's page, through which every page of the run passes (`passthrough.py`).

A module that only one use case uses belongs in that use case. This package is not a place for
code that has no other home. The harness that measures a run belongs to the evaluation of the
revised paper, in `jms_kapps.evaluation`, which the demonstration runs without: the evaluation lives
in a folder of its own.
"""
