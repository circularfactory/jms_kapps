# Changelog

Notable changes to `jms-kapps`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

`release-mechanism prepare` turns the heading of the changes not released yet into the dated entry
for the version being released, and stops the release if there is no such heading. Write the entry
as the work lands, not at release time. Name that heading nowhere else in this file: the release
mechanism insists on finding exactly one.

## 0.0.1 — 2026-10-16

First public release: the two KAPPS use cases of the JMS paper, built on
[`kapps-semantic-middleware`](https://pypi.org/project/kapps-semantic-middleware/),
[`kapps-ogm`](https://pypi.org/project/kapps-ogm/) and
[`kapps-triplestore-interface`](https://pypi.org/project/kapps-triplestore-interface/), with the
measurement harness of the paper's evaluation.

No git history is carried. This repository takes one commit per release, so the credit that
history would have carried lives in `LICENSE`, `pyproject.toml`, `README.md` and here instead.

The two recordings of use case 1 under `src/jms_kapps/uc1/data/unscrewing_timeseries/` are legacy
mock data, carried unchanged from the paper's first artifact; the note beside them says so.

### Added

- Use case 2, the decentral FlexConveyor: one process for each module of a topology, the
  WarehouseManagementSystem and a monitor, behind the Launcher page (`jms-uc2`), with the vertical
  slice (`jms-uc2 slice`), the topology editor, the module and WMS strategies, the two faults, and
  the reading of past possessions from the store's history. `./run-uc2` runs it in one command.
- Use case 1, vertical integration: the planner, the screwing resource, the anomaly detector and
  the learner as participants, the decision tree, the frames in InfluxDB, and the provenance of the
  detector's decision. `jms-uc1` runs it in one command, behind its page; `jms-uc1 seed` seeds
  once for a run by hand.
- The measurement harness of the paper's evaluation (`jms-eval`, `jms-eval-uc2`, `jms-eval-sweep`,
  `jms-eval-table`, `jms-eval-report`, `jms-eval-baseline`).
- The reviewer guide, the reference pages under `docs/reference/` and their glossary. The README
  holds the current text only; what a version changes is recorded here.
- The reviewer guide says what a reader sees and how to use it, in the paper's words, and where to
  read the graph interaction in the code. It has no table of the paper's claims: what the run means
  is the paper's to say.
