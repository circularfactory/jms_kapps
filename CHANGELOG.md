# Changelog

Notable changes to `jms-kapps`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

`release-mechanism prepare` turns the heading of the changes not released yet into the dated entry
for the version being released, and stops the release if there is no such heading. Write the entry
as the work lands, not at release time. Name that heading nowhere else in this file: the release
mechanism insists on finding exactly one.

## 0.1.0 — 2026-10-30

### Added

- The paper's commit time run is in the repository, packed: its raw data in
  `commit_time/results/raw_data/`, one tar.xz per used repetition and store (14 repetitions of
  two stores; repetition 15 is excluded, as its `parameters.json` records), with its
  `parameters.json`, its spec under the name the run wrote, `MANIFEST.json` and a README; and in
  `commit_time/results/reports/` what the scripts derive from those archives: per store the
  index and the results page, the figure script's output and `REPRODUCE.md`, the steps to unpack
  the archives, redraw the figure and repeat a run. `commit_time/pack.py`
  (`jms_kapps.commit_time.packing`) packs a finished run so. It reads only the run's folder,
  leaves out the repetitions that `parameters.json` excludes, refuses a run whose records name
  more than one jms_kapps commit, or none, replaces each string the release's hygiene checks ban
  with a placeholder and lists each replacement, copies a file that is not text unchanged, and
  ships a report over 50 MB as tar.xz. The experiment script packs its run so at its end.
- A recorded refusal of use case 2, `docs/recorded-refusal.md`: one handover onto an
  occupied module, without a reservation, refused by the triple store at
  `:FlexConveyorModulePossessionShape` in a real run of the write path, with the commit that was
  attempted, the validation report verbatim as the store sent it and as the module received it,
  the state after the refusal and the recording's provenance. The page prints the report as Turtle
  in the form of the paper's Listing 1. The report is `tests/reports/skip-reserve.nq`, which
  replaces the earlier recording, with a Turtle copy beside it. `scripts/record_uc2_refusal.py`,
  which ships, makes the recording in one run on the dataset it is given, and a live test makes
  it again and compares the report with the committed one as RDF graphs. Recorded on
  kapps-semantic-middleware 0.2.6, kapps-ogm 0.3.3 and kapps-triplestore-interface 2.2.3, with
  the `http://` namespaces.
- The video of the robot cell that the paper's Section 5.1 promises,
  `docs/videos/robot_unscrewing.mp4`, linked from the landing page of use case 1.
- The replay of the store-only baseline (`writes_only=True` of
  `jms_kapps.commit_time.baseline`'s `read_recorded_run` and `run_baseline`, which the
  experiment script runs) replays the recorded writes of a UC2 run alone, with no recorded read
  and no login: the "replay" rows of the commit time figure. The harness
  verifies each checked write's receipt right after the write's own answer, unmeasured, and a
  receipt's removal waits for it, so an update answered after its recorded query's offset no
  longer reads as "not applied". Every replayed write keeps its recorded send time, span ids,
  category, recorded outcome and applied state, whether it overtook the participant's previous
  write, and whether its outcome is the recorded one; a replay with a mismatch is invalid and the
  table leaves it out. The record's `replay.mode` says "writes only" or "all requests"; a replay
  without the option is otherwise unchanged.

- The round-robin WMS strategy `roundrobin`, "Round robin (synthetic)", in
  `jms_kapps.commit_time.round_robin`. The WarehouseManagementSystem takes the entry modules in
  turn, the first after the last, and sends each box to the exit straight below its origin, so
  that every entry module spawns at a fixed rate and every box takes the same lane. A turn whose
  entry module is not free is refused as no candidate, and the slot is lost, never given to
  another module. `jms-uc2 --wms-strategy`, the Launcher's picker and the harness's configuration
  find it by name.

- The commit time experiment, one command: `commit_time/experiment.py` runs use case 2 on the
  reference implementation with SHACL on and off on the remote store and then on the local
  GraphDB, replays each SHACL-on run right after it, leaves one folder per store (the index of
  runs `arms.json` and the results page `results.md` and `results.json`), then makes the figures
  and packs the run. It builds its two configurations itself. Every parameter is in its
  `FULL_RUN` block: the round-robin WMS strategy at 30 boxes/min, heartbeats on, 14 repetitions
  whose order alternates (odd ones SHACL on first, even ones SHACL off first) and a 300 s window.
  `--debug` takes the small numbers of `DEBUG_RUN`: the cross at 6 boxes a minute over a 30 s
  window, with both heartbeat levels. `--stores` and
  `--repetitions` override them. A run whose window has a refused spawn, or whose record
  counts no spawns, is named in the results page and makes the exit code 1, as a replay with an
  outcome mismatch does; the script goes on. On a store that is not on loopback it starts only with
  `TRIPLESTORE_ALLOW_DESTRUCTIVE=1` set on the command. The harness gains
  `jms_kapps.commit_time.results` (the index and the page) and `spec.replay_fields`, the fields
  of a replay's record that name the spec.

- The commit time paper figure. `commit_time/figure.py` writes `commit-time-paper.pdf` and `.png`
  (the PNG at 600 dpi), to the paper's figure spec: one figure at heartbeats on, 78 × 70 mm with
  every glyph at 7 pt, a row for SHACL on, SHACL off and "replay" of the local store and of the
  remote store. Each row label gives the row's n on a second line,
  counted from the commits its box draws; the panel letters stand at the top left of each
  panel, the axis titles are "time per commit (ms)" and "mean time per commit (ms)". Panel (a), with
  `time_per_commit.csv`, draws box plots on a log axis, the mean as a diamond, whiskers at the 1st
  and the 99th percentile, a circle for every commit of 1 s or more (none for the commits beyond
  the whiskers under 1 s) and a dashed line at 3 s labelled "3 s". Panel (b), with
  `time_per_commit_by_phase.csv`, shows where a commit's time goes, the mean of four parts stacked, with each
  bar's mean in whole ms; Δ, the mean whole commit of SHACL on minus that of SHACL off, is not in
  the figure. The legend shows the symbols of panel (a), the whiskers named "1st–99th percentile"
  (the circle only if a row has one), then the four parts row by row. The figure's numbers for the
  caption and the text (n per row, Δ with its bootstrap interval, the
  class phases, the heartbeat share and the count and share of commits of 1 s or more per row) go
  into `figures.json` under `numbers`. A test reads the written PDF for its page size and its
  smallest glyph (pdfplumber, development group only).
- The README says how to cite: the paper, as submitted to the Journal of Manufacturing Systems,
  `jms-kapps` as the paper's supplementary material (doi:10.5281/zenodo.23772468), and the
  reference implementation of the KAPPS architecture, its three libraries
  (doi:10.5281/zenodo.23772750). `CITATION.cff` carries the same, with the paper as the preferred
  citation.

### Changed

- jms_kapps requires kapps-semantic-middleware 0.3.0, kapps-ogm 0.4.0 and
  kapps-triplestore-interface 2.3.0 or later within their minor versions; the lock moves to them.
- The docs are six pages: the README, `docs/setup.md`, `docs/use-case-1.md`,
  `docs/use-case-2.md`, `docs/glossary.md` and `commit_time/README.md`, beside the recorded
  refusal. Each page states only what a reader needs to find, run or check what the paper states.
  The user guide and the pages under `docs/reference/` are gone.
  A table at the top of the README links each item of the paper to the page or the library that
  holds it. The developers' README of the measurement harness does
  not ship. `CITATION.cff` gives the paper's DOI in its preferred citation.
  Every name marked interim in an earlier entry is final.
- `jms-uc2 --help`: `--topology` takes a module list, and `--transit-time` gives its default, 3.
  `commit_time/figure.py --help` describes the time of a commit in the reference implementation
  against the replay. The refusal of a topology names its format, and says that use case 2 does
  not read the short topology document.
- The development status in `pyproject.toml` is `4 - Beta`.

- The measurement of commit time keeps only what the final run and its figure need. The figure
  script writes the paper figure with its two tables (`time_per_commit.csv`, `time_per_commit_by_phase.csv`),
  the per-commit tables and the manifest; the CDF and box figures, the percentiles and the phase
  table are gone. The harness loses the sweep, its rounds, heavy parts and precision rule, the
  ramp and the ramp comparison, the approval files and the `approval` module, the arm latency, the
  run report and table, the grid generator, the `southward` strategies, the earlier iterations'
  specs, configurations and topologies, and the commands `jms-eval`,
  `jms-eval-uc2`, `jms-eval-sweep`, `jms-eval-table`, `jms-eval-report`, `jms-eval-baseline`
  and `jms-live-store`.
- The measurement of commit time takes the paper's words, "the time of a commit", and is named
  for what it is, a measurement of the implementation on use case 2: the
  folder `evaluation/section_7/` is `commit_time/`, the package `jms_kapps.evaluation` is
  `jms_kapps.commit_time`, the extra `evaluation` is `commit-time`
  (`uv sync --extra commit-time`), and the `roundrobin` entry point loads
  `jms_kapps.commit_time.round_robin`. A store folder's results page is `results.md` and
  `results.json`, written by `jms_kapps.commit_time.results`. A run's dataset
  is named `commit-time-<run id>` (was `eval-<run id>`). The experiment writes its spec as
  `commit_time.toml`, series `commit-time`, and a new prefill names its generator
  `jms_kapps.commit_time.prefill`; records written before keep their names and are read as they
  are. No alias of an old name remains, and a test keeps the old names out of the shipped tree.
- The commit time measurement calls its second store "the remote store". The paper figure labels
  that row group "remote"; the CSVs' `store` column keeps the folder names `kit` and `local`, and
  `figures.json` says "remote" in the `group` of each row. The prose of the measurement's
  READMEs, docstrings, comments and glossary entries says "the remote store" where it said "the
  server", "the KIT store" or "KIT". The drain counts the transports that the
  WarehouseManagementSystem's log names.
- The README sets up once, with one block for Linux and macOS and one for Windows, whose every
  line runs as pasted. Then `uv run jms-uc1` and `uv run jms-uc2` start the use cases on every
  operating system.
- The local GraphDB of `compose.yaml` is 10.8.5, the remote store's version, on a fresh volume,
  `graphdb-data-10-8-5`; the volume of 10.8.1, `graphdb-data`, is left as it was. Its heap stays
  2g unless `JMS_KAPPS_GRAPHDB_HEAP` names another (the commit time experiment starts it with 12g).
- The documentation site publishes the README and the pages under `docs/`, and no generated
  module pages. A link between two of these files is a link between their
  pages on the site; an image or the video loads from the public repository, and a link to any
  other file points at it in the public repository, at the release's tag.

### Removed

- Use case 1's sequence page: the button *Sequence* and the route `/sequence` of use case 1's page,
  and the page's file and its copy in `docs/reference/`.
- The pytest marker `paper_claim` and its development test. The tests that show what the paper
  claims run with `uv run pytest --dataset <name>`, and no test names a numbered claim.

- The SHACL-SPARQL rule of use case 2, the reachable destination, with its shapes file
  `flexconveyor_usecase_sparql_shapes.ttl`, the commands `jms-uc2 audit`,
  `jms-uc2 reachable-destination` and `jms-uc2 capability-resource`, their guide sections, README
  rows and tests, and the `sparql_shapes` option of the seed. The capability shape stays in both
  use cases' shapes files, and the WarehouseManagementSystem's own check that a destination is
  reachable stays.
- The guide's section on the transferunits demo of the semantic middleware, its README row, the
  extra `transferunits-demo` and its lock entries, and the dataset `kapps-demo` of the local stack:
  its repository configuration, the compose file's creation of it and its health check. The
  local stack creates `jms-kapps` only.
- The shipped topologies `pair` and `tee`, with every page and test that names them. The shipped
  topologies are `cross`, `grid`, `line` and `merge-fault-demo`.
- `./run-uc2`, and its tests: the README's setup starts the local triple store and sets its
  variables, and `uv run jms-uc2` runs use case 2.
- The page on the assets. The seed's load of the middleware's operation-status shape is stated
  with the dataset.

### Fixed

- `jms-uc1` counts the robot controller stand-in live only once it has connected to the broker,
  so it says "Use case 1 runs" only when a press of Unscrew reaches the robot.
- A participant counts as live only once a capability links to each of its workflows. A press of
  Unscrew right after the readiness gate, as `jms-uc1 infeasible-operation` makes, could come
  before that link, and the press was refused because no service could be resolved.

- The UC2 monitor lists the WMS's handover of a new box to its origin also when no read finds the
  box there. The origin commits its handover to the next module at once, with no move before it,
  so on a store that answers in a few milliseconds the box stays on its origin for much less than
  one read, and the first read finds it on the next module. Such a box now shows the WMS's
  handover to its origin and one handover from its origin to where the read found it, as two
  handovers between two reads show as one; before, it showed neither. A spawn in the GUI from
  Module 2 shows "WarehouseManagementSystem1 → Module2" again.
- Use case 1 runs on Windows: the robot stand-in starts its MQTT client on a Selector event loop,
  chosen for that start alone, because aiomqtt needs `add_reader` and `add_writer`, which the
  Proactor loop of a Windows process by default does not have. On Linux and macOS it keeps
  asyncio's default loop.

## 0.0.4 — 2026-10-26

### Added

- The per-commit table and the §7 figures (interim names). `jms_kapps.evaluation.commit_table`
  reads recorded run folders into one row per commit of the object-graph mapper (store, heartbeat
  level, arm, latency, admitted, window, kind, checked write, the ten phases from the spans, and the
  latency of its replayed update in a writes-only replay, paired by the commit's span id) and one
  row per replayed write. The new folder `evaluation/section_7` holds `figure.py`, which writes the
  two tables, the CDF and the box plot of the commit latency per heartbeat level (writes only, KAPPS
  with SHACL off, KAPPS with SHACL on; a panel per store), the percentile CSV with bootstrap
  intervals over repetitions, the phase table and a manifest, and prints why each plot its data
  lacks was skipped. `stats.difference_of_means` gives the SHACL difference's interval.

### Changed

- jms_kapps requires kapps-ogm 0.3.3 and kapps-triplestore-interface 2.2.3 or later within their
  minor versions; the lock moves to them.

## 0.0.3 — 2026-10-25

### Added

- The measurement harness's record links every store request to its commit and describes every
  commit (interim names). A store request in the measures gains `span_id`,
  `parent_id`, `commit`, `create` and `status_code`; a commit gains `span_id`, `kind` (`heartbeat`,
  `status: <statuses>`, `operation + handover`, `operation`, `registration: <what>` (endpoint,
  workflow, capability, event stream, service, address), `other update` or `no write`, by the
  predicates its first checked write removes or sets), `update_outcome`, `update_refusal`,
  `update_applied` and `checked_writes`. The report's steps and the dead-heartbeat point classify a
  heartbeat update written with either spelling of the predicate (the old one
  and `hasLastHeartbeat`) through one helper, `store_requests.names_heartbeat`. The report summary's
  format rises to 6, because its classification changed: a summary of the format before is made
  again from its record and spans. A record written before has none of the new fields and is read
  as before.
- Use case 1's knowledge graph can hold a measured position as a random variable with a Gaussian
  mixture in one typed literal, beside its best estimate. Every launch loads the uncertainty
  module (`uq`, `http://w3id.org/circularfactory/Uncertainty#`, according to Wu et al. 2026) with
  `uq:RandomVariable`, `uq:Domain`, `uq:gmmLiteral`, `uq:double`, `uq:nonNegativeDouble`,
  `uq:hasDistribution`, `uq:hasDomain` and `uq:representedBy`, and registers its prefix. Use case
  1's ontology gains `:hasApproachEndPositionVariable`, `:hasMeasuredScrewHeadPosition`,
  `:hasScrewHeadPositionVariable` and `:hasPositionStandardDeviation`; the seed gives `:Robot1` the
  standard deviation 0.1 mm, an example value. `jms_kapps.uc1.uncertainty` writes the literal of
  one measurement, reads it back as mean and variance, and checks that a lexical form is well
  formed. Admission refuses a random variable without exactly one distribution, whose lexical form
  names the six fields in their order, or without exactly one value domain; a second best estimate
  or a second variable on a Screw; and a second variable on an unscrewing operation.

- The anomaly detector can save measurements as uncertainty quantifications: a mode, off by
  default, that `--save-measurements-as-uq` turns on from the start, on the detector's process and
  on `jms-uc1`. With it on, the commit of a classification also writes a random variable whose
  literal holds the one measurement (mean the measured value, variance the square of the robot's
  `:hasPositionStandardDeviation`): after a failure outcome for the end of the approach, linked from
  the operation; after every outcome that reached contact for the position at contact, the Screw's
  `:hasMeasuredScrewHeadPosition`, linked from the Screw. A new measurement of a head position
  replaces the literal and the best estimate; nothing is fused. A robot without its standard
  deviation ends the operation `failed`, as an attempt that cannot be classified. With it off, the
  detector writes what 0.0.2 writes. The dashboard's "Classifications" box shows each
  classification's mode and the positions it saved, with their standard deviations.

- Use case 1's page has the switch "Save measurements as uncertainty quantifications", with the
  credit beside it to the two publications that the representation follows (ISWC 2026 and
  Sensoren und Messsysteme 2026) at their DOIs, and the list "Measured positions": each position the detector saved, with its
  best estimate, its standard deviation and its literal, from one plain SPARQL query with its copy
  button. The switch reads and sets the detector's mode on the detector's route
  `/save-measurements-as-uq` through the page's port, from the next classification; "What to do"
  gains an optional third step that turns it on.

- The user guide has the section "The uncertainty of a measured position": the switch, two short
  queries for the triple store's workbench (the best estimate and the literal with its datatype),
  the assumed standard deviation of 0.1 mm, and one sentence that querying the literals is
  ProbSPARQL's in the store and fusion is a fusion service's job, with both DOIs. The glossary,
  the use case 1 reference page and the UC1 sequence page say the same in their own places.

- A run of the measurement harness can turn the participants' heartbeats off, for a measurement
  with the heartbeats on and off: `jms-eval-uc2 <configuration>
  --heartbeat-interval off --live-heartbeat-age 3600` starts no heartbeat loop in any participant
  (the registration's one heartbeat stays) and judges every service live up to 3600 s, in the
  participants and in the run's own supervisor. Two environment variables of the common code
  carry the settings to the participants, `JMS_KAPPS_HEARTBEAT_INTERVAL_SECONDS` (read by a UC2
  module and the WarehouseManagementSystem as they build their middleware) and
  `JMS_KAPPS_LIVE_HEARTBEAT_AGE_SECONDS` (read by the services reader at its import); unset, every
  process runs as before, the Launcher and `./run-uc2` included. A run sets them for its
  participants only and sets back the harness's own after it; its record keeps the run's values
  under `run_settings.heartbeat_interval_seconds` (null for off) and
  `run_settings.live_heartbeat_age_seconds`. The heartbeat level is no key of the configuration.
  Interim names.

## 0.0.2 — 2026-10-24

### Added

- `jms-uc1 infeasible-operation` shows admission refuse a planned operation that the factory
  information state cannot carry out. It clears and seeds the dataset as `jms-uc1` does, adds a
  Screw whose Screw type `M3` holds none of the seven values an attempt needs, starts the five
  participants, presses Unscrew on `Screw1` (admitted, ends `done`) and on that Screw (refused by
  `:UnscrewingOperationScrewShape`), prints the store's answer with the shape's message and the
  validation report, and stops the participants. It exits 0 when both behaved as stated;
  `--record FILE` writes the report. Use case 1's shapes hold the new shape, so every launch loads
  it: an unscrewing operation concerns exactly one Screw, whose Screw type holds each of the seven
  values once, as a double. The planner records a refused operation with the rules, their messages
  and the report, shows them on its dashboard, and serves its record of the presses as JSON at
  `/presses`.

- The user guide, the use case 1 reference page and the UC1 sequence page describe use case 1 as it
  runs over MQTT: five processes, with the robot controller stand-in as the only replacement of the
  robot's controller; the robot's parameters in the knowledge graph, with the query that reads the
  broker, the port and the topic of one of them; the screwing resource's dashboard box
  "Connectors", which lists its twenty connections; the setpoints and the samples of an attempt in
  the terminal; and the command line of the stand-in, to run it by hand. The user guide's section
  on the TransferUnit demo points to use case 1 as the second place where protocol mediation shows.

- Use case 1's screwing resource drives its robot over MQTT through connectors that its middleware
  instance builds from the knowledge graph: the robot's twelve parameters, described in the graph
  by the seed, give twenty connectors (twelve read, eight write), through use case 1's own binding
  descriptor in a connector registry of the screwing resource's own, and the screwing resource's
  code names no broker, port or topic. The attempt of `jms-uc1` writes the seven values of the Screw
  type and the start command as persistence writes that name the parameter, which go out through
  the write connectors and show in the data model, collects every sample the robot publishes, and
  builds the frame; a robot that reports a failure or does not answer within 60 s ends the operation
  `failed`, with the reason on the page and in the Attempts box. The run of `jms-uc1` starts five
  processes: the robot controller stand-in, a device with no service in the graph, comes right
  after the screwing resource, which hosts the broker; the supervisor holds it live while it runs,
  the page shows its row without links, and the block of addresses says that it speaks only MQTT.

- `jms-uc2 trace-box --dataset <name>` reads every change of a box's possession back from the
  triple store's history. On a dataset whose name starts with `ogm-disposable-`, whose history
  collection it turns on, it runs use case 2 on four modules in a line, spawns one box from
  Module1 to Module4, and prints one row for each transaction that changed the box's possession,
  its transport's status or its assignment, with the time in UTC and in the server's local time.
  It writes the rows as JSON (`--out`, `trace-<box>.json` by default) and exits 0 when the
  possessors agree with the handovers the participants logged.
  [Possession history](docs/use-case-2.md#possession-history) shows how to create such a dataset
  and run it.

- The shipped topology `line`: four modules one after another, Module1 the entry and Module4 the
  exit, each connected East to the next. `jms-uc2 --topology line` and the Launcher's picker offer
  it.

- The local stack creates a second dataset, `kapps-demo`, beside `jms-kapps`, with the same
  configuration. Its init container checks each dataset on its own, so a volume that holds only
  `jms-kapps` gets `kapps-demo` on the next `docker compose up -d --wait`, and the health check
  waits for both. The configuration is `graphdb/kapps-demo-repo-config.ttl`.

- The optional extra `transferunits-demo` installs the packages of the TransferUnit factory demo that
  ships inside `kapps-semantic-middleware`, among them the in-process MQTT broker `amqtt`. `uv.lock`
  records them. `amqtt` asks for `websockets` 15, so the lock holds 15.0.1 in place of 17.1.

- The user guide has the section "Protocol mediation: the transferunits demo of
  kapps_semantic_middleware": the commands that start the factory with two units from a fresh clone,
  the SPARQL query that reads the broker and the topic of a belt's speed from the knowledge graph,
  and the route and the station board where its value shows in the data model. The README links to
  it.

- The page of `jms-uc1` passes every page of a run through its own port, 8091, under `/p/<id>/`,
  as use case 2's Launcher does on 8090, with the same passthrough. Its links and the block of
  addresses that `jms-uc1` prints go there, so over SSH, WSL or VS Code one forwarded port is
  enough. The passthrough moved from `jms_kapps.uc2.launcher.passthrough` to
  `jms_kapps.common.passthrough`.

- The page of `jms-uc1` lists the run's unscrewing operations (Screw, issuer, status, outcome,
  reason, and the deciding service, which links to its dashboard) and each Screw type's maximum
  dynamic torque at the start and at the latest read. Each list comes from one SPARQL query, which
  the page shows exactly as it was sent, with a copy button. The queries run in a background
  read every second, so a poll of the page sends none.

- The monitor's page has the switch "Follow the services' handover event streams" at the head of
  Refusals, in place of the radio buttons "graph + streams | graph only" in its first section,
  which now holds only the interval of the reads. On means "graph + streams", off "graph only".
  Possible deadlock says that it follows the same switch. The page shows the switch in the mode
  the monitor process runs in, also when it is opened directly and not through the Launcher.

- The page of `jms-uc1` redraws in place: it asks for its state every second and no longer reloads
  itself. It says what to do in two steps (press Unscrew on Screw1, then on Screw2) and names the
  dashboard where each result shows. Each of the four processes has a row, as in the Launcher of
  use case 2: name, state, PID, recent output on a click, and links to its API docs, its activity
  page, its dashboard and the dashboard's Logs tab. The page takes the look of the use case 2
  pages. After the start, the terminal prints one block of addresses between blank lines: the page
  and the dashboard of each service. The planner and the learner now have an activity page.

- The variable `JMS_KAPPS_GRAPHDB_PORT` names the port of this machine on which the local triple
  store of `compose.yaml` answers, 7200 without it. `docker compose up -d --wait` publishes the
  store there, and `./run-uc2` without `TRIPLESTORE_URL` starts it there and runs on it, for a
  machine on which 7200 is taken.
- The user guide opens with one sentence and the paper's reference. Its setup section links to the
  README's setup block instead of repeating it, and adds a triple store of your own, the settings,
  a taken port and the common errors, each with its message as printed. The InfluxDB paragraph
  moved to the use-case-1 part.

- After the first launch of `jms-uc2` and after each launch from the Launcher page, the terminal
  prints one block of addresses between blank lines: the Launcher page, the WMS dashboard on its
  tab WMS, the monitor, and the form of a module's pages, each through the Launcher's port 8090.
  A process's own address changes at each launch; these do not. The block takes its paths from the
  table of the page's links. The address line of each process stays, for a process run by hand.

- The documentation says that a launch seeds a demonstration dataset with the shipped ontologies and
  shapes, and that a productive circular factory already holds them in its knowledge graph; the
  seed is no longer called the ontology bootstrap.
- The seeds load the middleware's operation-status shape for the demonstrations: every operation
  has exactly one status, one of `queued`, `running`, `done` and `failed`. It joins every shapes
  document that the common store reset sends, so a launch of use case 1 or use case 2 and the
  evaluation's prefill run with it active; an empty shapes document still gets it, and the
  SHACL-off twin, which sends no shapes, none. A productive knowledge graph already holds it. Use
  case 2 keeps its own status shape. `python -m jms_kapps.evaluation.arm_latency` reports the
  median and the 95th percentile of the commit latency of two folders of run records, as the
  comparison with and without the shape reads them.

- The reference page "The UC1 sequence", which tells use case 1's scenario in five steps with the
  seeded names (`Screw1`, `Screw2`, `Grinder099`, `Grinder100`): the design, the decision to
  unscrew, the attempt, the classification and the learning. UC1's page serves it at `/sequence`,
  from a copy in the package, and links it with a button "Sequence".
- jms_kapps requires kapps-semantic-middleware 0.2.6, kapps-ogm 0.3.2 and
  kapps-triplestore-interface 2.2.2 or later within their minor versions; the lock moves to them.
- Use case 2's reachable-destination rule in SHACL-SPARQL, `:ReachableDestinationShape`: the store
  refuses, at commit, a transport created toward a module that the station's connections do not
  reach from its origin, a destination changed to such a module, and the removal of a connection
  that the destination was reached by. It is in `flexconveyor_usecase_sparql_shapes.ttl`, loaded on
  request (`shapes_document(sparql_shapes=True)`, and `sparql_shapes` of `reset_uc2`,
  `seed_topology` and `seed_two_modules`, off by default). The shape tests and the audit load it. A launch and the
  measurement harness do not, because the store evaluates its target over every transport at every
  commit. The Core shapes file holds SHACL Core only.
- Every capability belongs to exactly one resource: `:CapabilityShape` and its property shape
  `:CapabilityResourceShape`, SHACL Core, in the shapes that use case 1 and use case 2 seed. The
  store refuses, at commit, a capability that no resource links to with `cfc:hasCapability`, and
  one that two resources link to. The middleware registers a capability with its resource's link
  in one commit, so a run never breaks the rule; an operation's resource is its capability's
  resource by construction.
- `jms-uc2 capability-resource --dataset ogm-disposable-<id>` shows that rule firing: on the dataset,
  which it clears, it makes three writes and prints each with the store's answer.
- `jms-uc2 reachable-destination --dataset ogm-disposable-<id>` shows the reachable-destination rule firing,
  apart from a launch: on the dataset, which it clears, it seeds three modules, opens a transport,
  and prints the store's answer, with the shape's message and the validation report, to a
  transport toward a module that no connection reaches and to the removal of a connection. It exits
  0 when every step behaved as stated. `seed_three_modules` seeds the third
  module for it.
- `jms-uc2 audit [--dataset NAME]` validates the whole of a dataset against both shape files,
  through the triple-store interface's `validate`, and prints the store's report. It exits 0 when
  the dataset conforms. It reads only.

- Use case 2, the decentral FlexConveyor: one process for each module of a topology, the
  WarehouseManagementSystem and a monitor, behind the Launcher page (`jms-uc2`), with the
  topology editor, the module and WMS strategies, the two faults, and the reading of past
  possessions from the store's history. `./run-uc2` runs it in one command.
- Use case 1's views: the screwing resource's dashboard shows its attempts, and the anomaly
  detector's shows its classifications, with the outcome, the rule of the tree's exit and the ten
  values, and what each Screw type held at its last read. Each renders what its
  participant keeps in memory, and reads no store.
- The user guide's section on use case 1: what it shows, how to run and stop it, what its pages
  show, the five things it shows with the test that proves each, and what in it is example data or a placeholder. The
  screenshot script takes the page of `jms-uc1` with `--uc1`. The guide shows that screenshot,
  `docs/images/uc1-page.png`.
- The user guide, the reference pages under `docs/reference/` and their glossary. The README
  holds the current text only; what a version changes is recorded here.
- The user guide says what a reader sees and how to use it, in the paper's words, and where to
  read the graph interaction in the code. It has no table of the paper's claims: what the run means
  is the paper's to say.
- A static test (`tests/test_no_deletion_of_individuals.py`) proves that nothing in the use cases
  calls the OGM's `delete` or its private deletion entry point.

### Changed

- The Launcher page's topology section has three rows and a panel. The picker, **Empty grid**,
  **Upload** and **Edit** come first, and one grid below them always draws the chosen topology,
  read-only. **Edit** makes that grid editable, with **Download** and **Close** in its place. The
  run settings follow, with one **Launch** button that launches what the grid shows; it replaces
  "Launch this topology" and the editor's "Launch this". In edit mode the picker, **Empty grid**
  and **Upload** replace the edit, and they and **Close** first ask "Discard your changes to the
  topology edited from …?" when the edit has changed. While a topology that is not shipped runs,
  the picker has one entry for it, such as "pair (edited), running now". The panel **Running
  now** holds the running line, the strategies, the history note and the module table. Each
  section's intro is at most two sentences, with the longer explanations in closed details blocks,
  and the checkbox's label is now "Collect the SHACL refusals in one monitor".
- The planner's line "The planner takes no Operation of another resource, and does nothing with …"
  is logged at DEBUG instead of INFO.
- The glossary entry "Inference" and the dataset section of the triple-store page say what the demo
  store materializes: subclass conclusions and the back-link of an `owl:inverseOf`, which the use
  cases rely on. They give one dated observation, that the KIT triple store did not type AG1 a
  `cfc:ProductType` from the range of `cfc:hasProductType` on 2026-10-23, and say that no read
  relies on a type from a domain or a range. A new `graphdb` test checks the subclass and inverse
  conclusions on a small ontology of its own, and prints what the store does with a range and a
  domain.
- The dashboard boxes of use case 1 and use case 2 no longer carry the middleware's note that
  identifiers are shortened: they show each IRI by its local name, in full on hover, and the note
  said more than they do. In the tables of the WMS dashboard, the IRI cells (box, transport,
  origin and destination) wrap when their value is longer than the column, so a whole transport
  is visible without scrolling sideways.

- Use case 2's two pages, the Launcher and the monitor, get one minimal, modern look in SFB 1574's
  colours: a white background, the logo's teal, petrol blue and indigo as primary colours, red only
  for signals and errors, the SFB 1574 logo in each header, and layouts that fit a phone. What the
  pages show and do is unchanged, and so are the colours that carry meaning (the process states and
  the monitor's module drawing). Each page serves its own copy of the logo. The headings read
  "Use case 2 Launcher" and "Use case 2 Monitor".

- UC1's ontology, its shapes and the seeded UC1 instances use the namespace `http://w3id.org/circularfactory/Unscrewing#` (ontology IRI `http://w3id.org/circularfactory/Unscrewing`), beside Core and Service, in place of the `sfb1574.kit.edu` demonstration namespace; a clean sweep with no alias. Its prefix label is `uns:` in the dataset prefixes, the user guide and the triple-store reference (interim name). A dataset seeded before this change holds the old namespace and the old `jms` prefix; a launch's reset reloads the UC1 graphs.
- UC1's ontology aligns its screw and design terms with Core 0.9.0: `:ScrewType` is a `cfc:ComponentType`, `:Screw` a `cfc:Component` (in place of `cfc:Workpiece`), `:hasScrewType` a sub-property of `cfc:hasComponentType`, and `:hasExpectedScrewType` sits on `cfc:AssemblyType` (was `cfc:ProductType`). The seed writes AG1 as an assembly type, each grinder names it with `cfc:hasAssemblyType`, and the design is read through that link. Six use-case terms are renamed, a clean sweep with no alias: `:DecisionSnapshot` to `:ParameterCopy`, `:usedValues` to `:classifiedWith`, `:hasScrew` to `:concernsScrew`, `:hasWorkpiece` to `:concernsWorkpiece`, `:hasOutcome` to `:hasDetectionResult` and `:UnscrewingOutcome` to `:DetectionResult`, with the shapes `:ParameterCopyShape` and `:ParameterCopyGeneratedByShape` (interim names). The middleware's `svc:hasOutcome` is a different property and keeps its name.
- One press of UC1's planner is one commit instead of two: the Screw's explicit back-link
  (`:hasOperation`) is written with the issuing call's update of an existing instance, in the
  operation's own commit, so the operation, its `queued` status and the back-link are admitted or
  refused together. The second commit and its warning path are gone. The ontology's inverse
  declaration stays. The planner's description, UC1's package description and the page "The UC1
  sequence" say so.

- The shipped `cross` topology names the roles of the first iteration's cross again: Modules 1
  and 2 are entry modules, Modules 4 and 5 exit modules, and Module 3, in the middle, has none.
  A run of the cross spawns a box on Module 1 or 2 and ends it on Module 4 or 5, so that no box
  starts or ends on the one crossing point of the layout. The connections do not change, nor do
  the other shipped topologies. Loading, the topology editor and a user's own topology files
  accept any roles, as before.

- The README holds the one setup block of use case 2 and says that `./run-uc2` launches the
  shipped topology `cross`, five modules. The table of the paper's concepts and their instances in
  the use cases moved from the README and the user guide to the glossary, which they link to.

- The two recordings of use case 1 are called example data everywhere they are named: two example
  recordings of an unscrewing attempt, to try the anomaly detector against, not training data. No
  shipped text, comment, docstring, public name or data tag calls them or the seed's values "mock"
  or "legacy". The replay's units are stated as the demo's assumption (newton, newton metre,
  millimetre, second). `mock_frame`, `MOCK_RECORDINGS` and `MOCK_SOURCE` of `jms_kapps.uc1.replay`
  are `example_frame`, `EXAMPLE_RECORDINGS` and `EXAMPLE_SOURCE`, and a replayed frame's `source`
  tag is `example-d320118/<name>`. A test over the shipped tree keeps the words out. The
  recordings are byte-identical.
- The operation's status property is `svc:hasOperationStatus` everywhere in jms_kapps, as in the
  middleware 0.2.3 (renamed on 2026-10-22 from `svc:operationStatus`, with no alias): use case 1,
  use case 2, the evaluation's queries and record readers, the shapes, seeds, tests and docs.
  The release before this one, version 0.0.1, reads the run records made before this change.
- Eight more service-ontology properties are named as predicates everywhere in jms_kapps, as in
  the middleware 0.2.4, with no alias: `svc:precondition`, `svc:outcome`, `svc:address`,
  `svc:endpoint`, `svc:lastHeartbeat`, `svc:executionTimestamp`, `svc:executionResult` and
  `svc:failureState` are now `svc:hasPrecondition`, `svc:hasOutcome`, `svc:hasAddress`,
  `svc:hasEndpoint`, `svc:hasLastHeartbeat`, `svc:hasExecutionTimestamp`,
  `svc:hasExecutionResult` and `svc:hasFailureState`. Use case 1, use case 2, the evaluation, the
  seeds, the tests and the docs follow. Stored data that carries an old name is not read: reload
  the shared graphs (`load_shared_ontologies(db, reload=True)`) and re-seed before you run on this
  version; the seeds do it.
- Every ontology namespace uses `http://`, as the Core ontology does: the Core, Service and MES
  namespaces, use case 1's namespace and the recorded store reports. A graph stored under the old
  `https://` names does not clash with the new one, since the IRIs differ; drop it from a
  long-lived dataset. Two tests refuse the old name and an `https://` ontology namespace.
- The use case 1 screwing resource's attempt is given the seven values of its Screw type: the
  intake reads them with the operation, in one fetch (`OPERATION_SCOPE` reaches from the operation
  through the Screw and its Screw type to the type's values), and the attempt takes them as a
  second argument, so a custom `attempt` is called with `(screw, values)`. The replay ignores them.
  The attempt's log line and the "Attempts" box of the screwing resource's dashboard show them (a
  new column "Type values", interim name). After the learner refines a threshold, the next attempt
  on a Screw of that type is given the refined value. When the Screw type holds one of the seven
  values zero times, more than once, or not as a finite number, the screwing resource makes no
  attempt: it ends the operation `failed`, and its `svc:hasExecutionResult` begins with "no attempt
  was made:" and names each missing or unusable value.

- Use case 1's anomaly detector ends an unscrewing operation `done` only for the outcome
  ScrewIntact. Every other outcome, MissingScrew included, ends it `failed`, in the same one commit
  as the classification, the decision snapshot, the outcome and the measured end of the approach,
  and the operation's execution result names the outcome after the interim prefix `ANOMALY: `
  (`ANOMALY: MissingScrew`). The planner is notified of that `failed` as of a `done`, reads the
  outcome on every end, and its dashboard shows the outcome and the reason of a failed press. The
  detector's answer carries the `failed`, the outcome and the reason, and its box
  "Classifications" shows how each classification ended the operation. Before, every classified
  attempt ended `done`.

- A use case 1 Screw takes its seven type values (the six detection thresholds and the preparation
  turning angle) from its Screw type only; the Screw-level override is gone. The ontology narrows
  the domain of those seven properties to the Screw type and the decision snapshot, and the
  effective-values resolution refuses a Screw that is given one, with a readable reason. The
  placement values stay on the Screw. No seed or demo wrote an override.
- The service classes of the use case 1 and use case 2 ontologies say in their comments that a
  service wraps a resource and is hosted by its own middleware instance.
- `jms-uc2 capability-owner` is renamed `jms-uc2 capability-resource`, after the shape it shows; the
  old name is gone.
- The pages of both use cases use the terms of the accompanying article.
- The failure reasons that the use cases commit say "the knowledge graph".
- Documentation: terminology and wording aligned with the accompanying article
- The Launcher starts the monitor in "graph only", where it started it in "graph + streams": the
  monitor then reads the knowledge graph and nothing else, as a dashboard that sees only the graph
  does. A new checkbox on the Launcher page, "Collect the SHACL refusals in one monitor", off by
  default, starts it in "graph + streams" instead, so that its Refusals view fills at the two
  fault scenarios; the choice is the launch field `collect_refusals`, kept in the Launcher's state
  and shown on the page after a reload. The monitor's own mode switch works in both cases. The
  monitor process takes the mode as `--mode`, and a monitor started by hand starts in "graph
  only" too. The field and the option carry interim names until the first release after the
  paper revision.
- The reviewer guide is the user guide, `docs/user-guide.md`, under that name in the README, the
  glossary, the tests reference and the release files; the pages say "a user" or "you". The tests
  reference lists no claim number and no claim set, and the search of the docs-history test for a
  claim's number and a claims table covers the README and the reference pages too.
- Use case 2's module passes the possession extension's handover, bound to its middleware
  (`possession.handover(middleware, ...)`), where it passed the middleware's own `handover`, which
  the middleware keeps for one release as a deprecated alias. The tests that count or drive a
  handover use the new call, and the offline tier runs with the alias's `DeprecationWarning` as an
  error.
- The documentation now states that use case 1 has four participants and calls its detector over
  REST, that the vertical slice's clean handover ends its transport `failed` by design, that use
  case 1's shape file is new in 0.0.1, and which store image the local fallback uses against which
  version the store-specific caveats were measured on.
- The shipped tree carries no dated note, ticket number or person's name: the evaluation package's
  README and the last docstrings that still did lose them, and each rule keeps its reason as a plain
  sentence. Where a removed note was the only place that said why, the reason is now written beside
  its rule. The history test of the documentation also reads the evaluation README, and finds a
  ticket reference that a line break splits. No behaviour changed.

### Removed

- `jms-uc2 slice`, the vertical slice that played two modules, one box and three acts, and its
  module `jms_kapps.uc2.vertical_slice`. `jms-uc2` is the one way to run use case 2, and its
  monitor arms the faults. The refusals the slice showed stay proven by the tests of the live
  refusal of `skip-reserve` onto an occupied module and of the shapes. The name first shipped in
  0.0.1 and goes without an alias or a deprecation.

### Fixed

- The use case 2 Launcher's modules import with every Pydantic 2 release that the package allows.
  With a newer Pydantic than the lock's, the import used to stop with a `NameError` on
  `ModelPrivateAttr`.
- The use case 2 Launcher no longer prints a Pydantic serializer warning for each link of each live
  process every time its page asks for the state, which filled the terminal of a run. The JSON of
  the state answer and the API docs are the same as before. The offline tests turn such a
  warning into a failure.

- Use case 2: a module whose next module declines and whose detour cannot be reached asks its
  module strategy again for another detour, with the modules left out excluded, and asks it at
  once, so a box goes to a free third neighbour instead of waiting for its next module; only where
  no other detour is left does it wait the retry time, as before. A module's look-ahead still leaves
  a next module that cannot be reached out at every Reserve of a box, but warns about it once per
  box ("leaves Module15 out for ..."), not once per retry time: again only once the box has left
  its waiting order, or that module has answered a look-ahead for it since.

- The use case 1 screwing resource's box "Attempts" shows the true end of an operation whose call
  of the anomaly detector timed out or failed after the detector had ended it: the status, the
  outcome (for example MissingScrew) and the reason (`ANOMALY: MissingScrew`) of the operation, as
  the planner's box shows them, read once with its status. It showed the status with the reason
  "the detector was not reached" and no outcome. A call that fails while the operation is still
  running still ends it failed, not reached.
- A Ctrl+C, SIGTERM or SIGHUP that reaches a process of use case 1 or 2 while it still starts,
  after it entered its start and before its server takes the signals, now stops it as it would
  once serving: the start ends there, the process exits with code 0 and prints no traceback, so
  `jms-uc2` and the Launcher show it as stopped, not failed. It had registered nothing yet, so it
  has nothing to deregister. A signal during the imports, before the start, still ends it by the
  signal. The test of a Ctrl+C to the process group while pages are open waits for each process's
  own announcement and runs on CI again.
- A prefill transaction that the store refuses again stops the seed with `PrefillNotLoaded`, whose
  message names the prefill file, the number of the transaction and the store's report; the
  interface's refusal is its cause. Since the triplestore interface 2.2.1 raises on a refused
  write, the loader had stopped catching it. Any other interface error reaches the caller unchanged.

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
