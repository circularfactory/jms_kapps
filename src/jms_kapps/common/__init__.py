"""What both use cases use.

These things belong here, and nothing else:

- the bootstrap that puts an ontology into the triplestore,
- the setup of the triplestore itself,
- the one reader of a participant's service in the graph (`services.py`),
- the supervision of a launcher's processes (`supervision.py`),
- the start that every participant's process shares (`process.py`), with its API docs by relative
  paths (`api_docs.py`).

A module that only one use case uses belongs in that use case. This package is not a place for
code that has no other home. The harness that measures a run belongs to the evaluation of the
revised paper, in `jms_kapps.evaluation`, which the demonstration runs without. (Changed on
2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07: the evaluation lives in a folder of its
own. Until then the list ended with "the harness that measures a run.")
"""
