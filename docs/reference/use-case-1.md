# Use case 1: the run, InfluxDB and the learner

Use case 1, anomaly detection and learning in robotic unscrewing, is vertical integration. Four
services work on unscrewing operations. The planner opens one operation per press. The screwing
resource makes one unscrewing attempt for each operation, on its robot over MQTT, and records the
curves. The anomaly
detector classifies the attempt and ends the operation: `done` when the outcome is ScrewIntact,
`failed` for every other outcome, with an execution result that names it (`ANOMALY: MissingScrew`).
The learner refines the detection thresholds of each Screw type.
The services coordinate through the knowledge graph, and they find each other there. The
notifications and the call of the detector go over REST, to addresses that the knowledge graph
names. The robot's controller speaks only MQTT: the knowledge graph describes the robot's
parameters, and the screwing resource's middleware instance builds its connectors from that
description. In the demonstrator, the [robot controller stand-in](#the-robot-controller-stand-in)
takes the controller's place. The curves are kept in InfluxDB.

[The UC1 sequence](uc1-sequence.md) tells one run step by step, with the seeded names: the design,
the decision to unscrew, the attempt, the classification and the learning.

What in use case 1 is example data or a placeholder is stated once, in the
[user guide](../user-guide.md#what-in-use-case-1-is-example-data-or-a-placeholder).

## Run use case 1

`jms-uc1` runs use case 1. It **clears the dataset it runs against**: `jms-kapps`, unless
`--dataset` names another. It refuses to run while another service is live there, unless
`--force` is given. A second `jms-uc1` on the same machine is refused while the first holds the
page's port, 8091 (`--page-port` names another).

In order, it binds the page's port, checks the guard, seeds, and starts five processes under one
supervisor: the anomaly detector, the screwing resource, the robot controller stand-in, the
learner and the planner. A service starts after the services it calls, the stand-in right after
the screwing resource, whose process hosts the MQTT broker, and the planner last, because it reads
the seeded grinders at its start. It waits until each is ready, prints the block of addresses, and
runs until Ctrl+C. The first stop signal stops all five, and each of the four services
deregisters; a second one does not cut the stop short.

Its page is on <http://127.0.0.1:8091/>, read-only. It asks for its state every second and redraws
in place, as the Launcher of use case 2 does. Without JavaScript it shows the
state at load. The state comes from memory, the supervisor's and that of the page's background
read. The section "What to do" gives two steps:
press Unscrew on Screw1, then on Screw2, each with the dashboards where the result shows. The
section "Processes" has a row for each of the five processes: its name, its state, its PID, its
recent output on a click, and for a service the links to its API docs, its activity page, its
dashboard and the Logs tab of that dashboard. The stand-in is a device with no service in the
knowledge graph, so its row has no links and says that it speaks only MQTT.

Every link goes through the page's own port, under `/p/<id>/`, where `<id>` is the name of the
service's resource (`Planner1`, `Learner1`, `AnomalyDetector1`, `ScrewingResource1`): the page
passes each request on to the service and its answer back as it comes, a stream included, with the
passthrough that the Launcher of use case 2 uses. So one forwarded port, 8091, reaches every page
of the run, over SSH as `ssh -L 8091:127.0.0.1:8091 <host>`. The service's own address, which
changes at each start, still answers too.

The section "Operations" lists each unscrewing operation: its Screw, the service that issued it
(`svc:issuedBy`), its status, its outcome, the reason of a failed one, and the service that decided
the outcome. The deciding service is the agent of the classification that generated the values
the operation used: the provenance chain that the anomaly detector commits with each decision
(`uns:classifiedWith` to the parameter copy, `prov:wasGeneratedBy` to the classification,
`prov:wasAssociatedWith` to its agent). Its cell links to that service's dashboard. An operation
that was never classified has no outcome and no deciding service; its reason says why. The section
"Learned values" shows each Screw type's maximum dynamic torque, the threshold the learner refines:
its value at the run's start, which the first read after the seed found, and its value at the
latest read. After Screw1, the latest is the one the learner committed for M4.

Each of the two lists comes from one SPARQL query, which the page shows exactly as it was sent,
with a button "Copy the query", as the Launcher of use case 2 shows its query of the boxes. Each
query declares its own prefixes, names no graph, and asks only for statements a participant writes
itself, so it runs unchanged in the triple store's workbench. The queries run in a background read
of the dataset, every second from the seed on, whether a page is open or not, and the page shows
what the last read answered. A read that fails keeps the last answer and says why.

A button opens the
planner's dashboard. A button "Sequence" opens the page `/sequence`, a copy of
[The UC1 sequence](uc1-sequence.md) that the page serves itself, so that it works from an
installed copy. Every link opens in a new tab.

After the start, the terminal prints one block between blank lines: the address of the page and
the dashboard of each service, each through the page's port, and a line that says the stand-in
speaks only MQTT.

Unscrew is pressed by hand only, on the planner's dashboard. The planner finds the seeded grinders
in the knowledge graph with one SPARQL query. The drop-down offers every Screw of those grinders
that has no operation yet. A Screw that has an operation leaves the drop-down, and a press on it is
refused. A new run seeds again.

The seed holds two angle grinders, `Grinder099` and `Grinder100`, one Screw each. Their assembly
type `AG1` expects the Screw type `M4`: the design. Each Screw's own Screw type is read from the
design and written to the Screw. Which example recording an attempt gets depends on the Screw:
`Screw1` gets `success` and gives ScrewIntact, so its operation ends `done`; `Screw2` gets
`missing_screw` and gives MissingScrew, so its operation ends `failed`, with the outcome, the
parameter copy and the reason `ANOMALY: MissingScrew`.
The mapping is in the seed module, and `jms-uc1` passes it to the stand-in as one `--recording`
for each Screw. The seed also writes the robot `Robot1`,
which the screwing resource names with `:hasRobot`, and a node for each of its twelve parameters.

The terminal log shows each service's lines behind its name. Each press prints one line:
`Planner: a press of Unscrew opens the unscrewing of Screw1 in Grinder099`, or that it is refused.
Every use of a projection scope (in the code, a *class scope*) prints one line: who, what, the
scope's name and its property chains, and what it wants. For example: `Screwing resource receives
UnscrewingOperation_… with OPERATION_SCOPE = [[concernsScrew, hasScrewType, hasMinApproachAxialForce],
…, [hasOperationStatus], [hasExecutionResult], [hasDetectionResult]]: …`. The intake reads the seven values of the Screw type with the operation,
in one fetch, and the attempt is given them: it sets each on the robot, and the stand-in echoes
it and publishes the same example recording, since in the real cell the robot and the screwdriver
would use them. When the Screw type holds one of the seven zero times,
more than once, or not as a finite number, no attempt is made: the screwing resource ends the
operation `failed`, with why (`no attempt was made: the screw type M4 holds no value of
MaxDynamicTorque`). Each attempt prints one line with the values it was
given: `Screwing resource: the attempt of UnscrewingOperation_… on Screw1 is given the values of M4:
MinApproachAxialForce 0.5, …`. The learner names the new ScrewIntact attempts, the value, and what the Screw type held before.

The terminal shows the robot's side of an attempt too. The screwing resource prints one line for
each value it sets on the robot: `ScrewingResource1: hasRobotMinApproachAxialForce is set on
Robot1`, …, then `hasAttemptCommand is set on Robot1` and `the robot is started on Screw1`. The
stand-in prints `Setpoint hasRobotMinApproachAxialForce <- {"v": …}` for each of the seven,
`Start: http://w3id.org/circularfactory/Unscrewing#Screw1 replays success`, and at the end
`Done: example-d320118/success, … samples`.

The curves are in InfluxDB, one frame per attempt; see [InfluxDB](#influxdb). The learner refines
the thresholds of each Screw type; see [The learner](#the-learner).

## The robot controller stand-in

The robot of the screwing resource, `:Robot1`, has a controller that speaks only MQTT. The
stand-in takes the controller's place in the demonstrator: on each start command it publishes an
example recording. It is configured like a device, with its broker and the example recording of
each Screw.

**The description in the graph.** The seed writes a node for each of the robot's twelve
parameters: the three readings (`:hasRobotPosition`, `:hasAxialForce`, `:hasUnscrewingTorque`),
the state of the attempt (`:hasAttemptState`), the start command (`:hasAttemptCommand`) and one
setpoint for each of the seven values of the Screw type. Each node holds its broker
(`inf:hasMQTTBrokerIP`, `inf:hasMQTTBrokerPort`), its topic `Robot1/<parameter>`
(`inf:hasMQTTTopic`), the key `v` of the value in the payload and its access mode. A parameter
that a client may set also holds its set topic, `Robot1/<parameter>/set`
(`inf:hasMQTTSetTopic`). The broker is `127.0.0.1`, port 18860. The screwing resource's middleware
instance builds a connector for each read (twelve) and each write (eight) from these nodes, and
starts the broker at that address inside its process. This query shows the description of one
parameter:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX inf: <http://www.sfb1574.kit.edu/ontologies/CrcInterfaces#>
SELECT ?broker ?port ?topic WHERE {
  uns:Robot1 uns:hasAxialForce ?force .
  ?force inf:hasMQTTBrokerIP ?broker ;
         inf:hasMQTTBrokerPort ?port ;
         inf:hasMQTTTopic ?topic .
}
```

It answers `127.0.0.1`, `18860` and `Robot1/hasAxialForce`. The twenty connectors are listed on the
screwing resource's dashboard, tab "System", box "Connectors".

**Run it by hand.** The stand-in runs on the computer of the screwing resource, since the broker is
inside that process and listens on `127.0.0.1`. It connects again with a growing pause until the
broker is there, so the order of the two starts does not matter:

```bash
uv run python -m jms_kapps.uc1.robot_controller --broker-host 127.0.0.1 --broker-port 18860 \
    --recording http://w3id.org/circularfactory/Unscrewing#Screw1=success \
    --recording http://w3id.org/circularfactory/Unscrewing#Screw2=missing_screw
```

`--recording <Screw IRI>=<recording>` is given once for each Screw it can unscrew; the recordings
are `success` and `missing_screw`. `--speed <factor>` makes the publishing of the example recording run that many times faster
than recorded (default 1.0). `jms-uc1 seed` prints the same command line.

**The MQTT steps of an attempt.** Every payload is a JSON object with the value at `v`, and no
message is retained.

1. After it connects, and after each end of an attempt, the stand-in publishes `idle` on
   `Robot1/hasAttemptState`.
2. The screwing resource publishes the seven values of the Screw type on the set topics of the
   setpoints. The stand-in publishes each one again on the setpoint's own topic, and the
   screwing resource's data model shows it.
3. The screwing resource publishes the Screw's IRI on `Robot1/hasAttemptCommand/set`. The stand-in
   publishes `running`.
4. It publishes each sample of the Screw's recording on the topics of the three readings, as
   `{"t": <ns since epoch>, "v": <value>, "phase": <phase or null>}`, in the example recording's own timing
   divided by the speed factor. `t` is the sample's time in the recording stamped at the start of
   the attempt.
5. It publishes `{"v": "done", "samples": {<reading>: <count>, …}, "source": <the example
   recording's tag>}`, then `idle`. The screwing resource builds the frame from the samples once each reading
   holds the counted number, and stores it in InfluxDB.

A start for a Screw without a recording ends in `{"v": "failed", "reason": …}`, then `idle`; so
does a start while an attempt runs, and the running attempt goes on. When the robot reports
`failed`, sends a message that is not a sample, or sends no end within 60 s, the screwing
resource ends the operation `failed` with the reason, and the reason shows on the page and in the
box "Attempts".

## Run use case 1 by hand

`uv run jms-uc1 seed` seeds the dataset once. It clears the dataset it seeds, guarded as the run
is, so it refuses while another service is live there, unless `--force`. It starts nothing, and
prints the command line of each service and of the stand-in. Each process then runs from its
printed line, in its own terminal, in any order. Each service reads its part from the knowledge
graph and seeds nothing. The planner
offers the Screws of the grinders it finds; a grinder seeded later appears after its next press or
restart.

**On another computer**: every service needs the same triple store. Set the connection variables on
each. The screwing resource, the detector and the learner need the same InfluxDB. Set
`INFLUXDB_URL` on each of them, or each uses its own empty local one. A service registers
`http://<--host>:<port>`. On another computer, start it with `--host <that computer's own
address>` and let its port through. `0.0.0.0` does not work: the middleware instance would
register it as the address. The port is chosen free at start. The stand-in runs on the computer
of the screwing resource, since the MQTT broker is inside that process (see
[the stand-in](#the-robot-controller-stand-in)).

**One process per resource**: a second process for the same resource registers a second service.
Whichever the knowledge graph names first is used, as in use case 2. Stop each with Ctrl+C; each
deregisters its service.

## InfluxDB

Use case 1 keeps the raw curves of each unscrewing attempt, its *frame*, in InfluxDB 3. Together
with the triple store, InfluxDB forms use case 1's Unified Knowledge Base. The knowledge graph
holds the attempt's operation and one reference to its frame. A run uses the
InfluxDB that the environment names, as it uses the triple store. The local InfluxDB is only the
fallback, when no `INFLUXDB_URL` is set. The command in
[The local fallback](triple-store.md#the-local-fallback) starts it with the local triple store,
InfluxDB 3 Core 3.11.5 on <http://localhost:8181>, and the commands there stop and remove both.

The local InfluxDB answers on this machine only, without a token. A configured server is
named with two variables, which are set as the triple store's variables are on each system:

```bash
export INFLUXDB_URL=https://your-influxdb.example
export INFLUXDB_TOKEN=...
```

A writer names the database, and it defaults to `jms-kapps`, the name of the dataset. The writer creates the database and its table before its first write.

- **The layout.** A frame is one row for each sample in the table `unscrewing`: the time, one float
  column for each signal (`robot_position`, `axial_force`, `unscrewing_torque`), and three tags.
  `frame` is the frame's id. `phase` is Approach, Form-fit, Preparation or Disassembly, and a
  sample without a phase has no `phase` tag. `source` is only on a replayed frame, and
  names the recording.
- **The reference** is `influxdb:///<database>/unscrewing?frame=<id>&start=<time>&stop=<time>`,
  with the times of the frame's first and last sample. It names no server: it is read from the
  InfluxDB that the reader is connected to.
- **The two recordings in `uc1/data/`** are example data: two example recordings of an unscrewing
  attempt, to try the anomaly detector against. They come from the paper's first
  artifact; the note in their folder says so. The replay assumes the ontology's units for them
  (newton, newton metre, millimetre, second). Their phases come from labels that a person wrote in their `comment`
  column. The frame replay, `jms_kapps.uc1.replay`, maps each label to a phase, or to none, and
  gives each frame it makes from them the source `example-d320118/<name>`.

## The learner

The learner is a service of its own, which wraps the resource `:Learner1` and has its own
middleware instance, run as `python -m jms_kapps.uc1.learner_process --dataset <name>
--learn-seconds <s>`. Every `--learn-seconds` (default 10) it reads the
unscrewing operations that are `done` with the outcome ScrewIntact since its last run, reads the
curves of each through the operation's own reference, and commits the new maximum dynamic torque of
each Screw type in one commit. It writes to the Screw type only, never to a Screw, and never a
placement value. The anomaly detector reads a Screw's effective values afresh at each attempt, so
its next decision uses the new value.

**Its rule.** The maximum dynamic torque of a Screw type is set 20 % above the highest dynamic
torque that the type's ScrewIntact attempts recorded in their Disassembly phase. The rule learns
from the decision tree's own outcomes. With the recording `success.csv`, whose highest dynamic
torque is 0.7, the rule changes `:M4`'s seeded 1.5 to 0.84.

**The first value replaces, then a value only rises.** The first value that the rule derives for a
Screw type replaces whatever the type held, a seeded value too, higher or lower. So a run shows one
learned change. After that the value only rises, because the rule takes the highest torque.

**Its dashboard.** The learner's process serves its middleware instance's state dashboard at
`/dashboard`, whose address it prints when it starts: the boxes of the instance in the tab
"System", and the box "Learner" in the tab "Learner". That box shows the last run and when the next
comes, how many attempts the learner learned from and how many of them were new in the last run,
each value it changed (Screw type, threshold, old → new, when), and the last thing that went wrong
(when, at which Operation's attempt, why). It renders the learner's memory only. A failed run or a passed-over attempt shows there,
and is still logged too.

## Measured positions

The anomaly detector can save each position it measures as a random variable, beside the plain
best estimate. The mode, "Save measurements as uncertainty quantifications", is off by default.
Turn it on with `--save-measurements-as-uq` on `jms-uc1` or on the detector's process, or with the
checkbox on the page, which reads and sets it on the detector's route `/save-measurements-as-uq`
through the page's port. A change takes effect at the next classification. What the user guide
shows of it is in [Use case 1](../user-guide.md#the-uncertainty-of-a-measured-position).

**What the detector writes.** In the one commit of a classification:

| Mode | Outcome | Written |
|---|---|---|
| off | any | what the commit wrote before: the classification, the parameter copy, the outcome, and for a failure outcome `:hasMeasuredApproachEndPosition` |
| on | a failure outcome (`MissingScrew` in the example run) | those, and a random variable linked by `:hasApproachEndPositionVariable` from the operation |
| on | any outcome that reached contact, every one except `MissingScrew` (`ScrewIntact` in the example run) | those, and the Screw's `:hasMeasuredScrewHeadPosition` with a random variable linked by `:hasScrewHeadPositionVariable` |

The literal of a variable holds one measurement: one component in one dimension, the measured
value in mm as the mean, and the square of the robot's `:hasPositionStandardDeviation` (0.1 mm in
the seed) as the variance. A robot without its standard deviation ends the
operation `failed` with the reason.

**The vocabulary.** The uncertainty module `uq`
(`http://w3id.org/circularfactory/Uncertainty#`), loaded beside the ontology at every launch:
`uq:RandomVariable`, `uq:Domain`, `uq:gmmLiteral`, `uq:double`, `uq:nonNegativeDouble`,
`uq:hasDistribution`, `uq:hasDomain` and `uq:representedBy`. The use case's own:
`:hasApproachEndPositionVariable`, `:hasMeasuredScrewHeadPosition`,
`:hasScrewHeadPositionVariable`, `:hasPositionStandardDeviation`, and the existing
`:hasMeasuredApproachEndPosition`, which is the best estimate of the approach end position.
`jms_kapps.uc1.uncertainty` writes a literal, reads it back as mean and variance, and checks that a
lexical form is well formed.

**The shapes.** Deployed beside the existing ones: `:RandomVariableShape` with
`:RandomVariableDistributionShape` (exactly one `uq:hasDistribution`, a `uq:gmmLiteral` whose
lexical form names the six fields in their order) and `:RandomVariableDomainShape` (exactly one
`uq:hasDomain`); `:ScrewMeasuredPositionShape` with `:ScrewMeasuredHeadPositionShape` and
`:ScrewHeadPositionVariableShape` (at most one of each on a Screw);
`:UnscrewingOperationVariableShape` with `:ApproachEndPositionVariableShape` (at most one on an
operation). They check the form of a literal.

## The screwing resource's and the detector's dashboards

Each service serves its middleware instance's state dashboard at `/dashboard`, whose address
`jms-uc1` prints and its page links to. The screwing resource's and the detector's open on a tab of
their own. Each box renders what its service keeps in memory as it works; the memory starts empty at each start of the process.

**The screwing resource**, tab "Screwing resource", box "Attempts": each attempt, newest first:
when it began, the operation, the Screw and its Screw type, the frame that holds its time series,
the seven values of the Screw type that the attempt was given (in the order of the box's note;
the pointer shows their names), the address of the detector it called, and the operation's status after the detector's answer with
the outcome the detector answered, or why the operation failed. When the detector was not reached,
the status is the `failed` that the screwing resource committed itself, or the end the operation
had already reached, with the outcome and reason the detector left (read once), as the planner's box
shows them. It keeps the newest 50.

**The anomaly detector**, tab "Anomaly detector", two boxes:

- "Classifications": each attempt it classified, newest first: the operation, the Screw and its
  Screw type, the Detection result, the exit rule of the decision tree that decided it, how it
  ended the operation (`done`, or `failed` with its `ANOMALY` reason), and the ten effective values,
  each with whether the decision tree asked it on its way to the exit. An attempt it could not
  classify shows why. Each also shows whether it saved measurements as uncertainty quantifications,
  and each measured position it saved so, with its standard deviation. A classification shows once
  its commit has reached the triple store. It keeps the newest 20.
- "Screw type values": what each Screw type held at the detector's last read of it, the values it
  read for its last classification of a Screw of that type. A value that the learner commits shows
  there at the detector's next read, and that classification uses it: the seven type values come
  from the Screw type only, and a Screw holds none of them. The resolution refuses a Screw that is
  given one: a type value written onto a Screw past the mapper is not read and not used.
