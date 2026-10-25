# The UC1 sequence

This page tells the scenario of use case 1 as a story, with the names that the seed writes. Two angle grinders, `Grinder099` and
`Grinder100`, hold one screw each, `Screw1` and `Screw2`. Their assembly type `AG1` expects the screw
type `:M4`. A person presses Unscrew on each screw. `Screw1` is intact and its operation ends `done`;
`Screw2` has no screw in it and its operation ends `failed`, with its reason.

Four services take part, each wrapping one resource and hosted by a middleware instance of its own:
the planner (`:Planner1`), the screwing resource (`:ScrewingResource1`), the anomaly detector
(`:AnomalyDetector1`) and the learner (`:Learner1`). They share one factory information state, the
knowledge graph in the triple store. Each of them writes it only by a commit, and the store admits
or refuses the commit against its shapes. Three shapes are deployed: the parameter copy's shape,
the capability shape and the unscrewing operation shape. A refused commit stores nothing.

A fifth process takes part, a device: the robot controller stand-in, the
controller of the screwing resource's robot `:Robot1`. It speaks only MQTT and holds no address in
the graph.

An unscrewing operation has a status: `queued`, `running`, `done` or `failed`. A notification
carries only the operation's IRI: the service that gets it reads the operation from the graph.
Names with a colon are in the use case's namespace, and `cfc:`, `svc:` and `prov:` are the Core
ontology, the service ontology and PROV-O.

## 1. The design

**Acts:** no service. The seed of `jms-uc1` writes the graph before any service starts, through the
object-graph mapper, and clears the dataset first. The seed loads the ontologies and shapes for the
demonstration; a productive knowledge graph already holds them.

**Reads:** for each grinder, one projection scope with two property chains: the screws it contains
(`cfc:containsWorkpiece`), and the screw type that its assembly type expects (`cfc:hasAssemblyType`,
then `:hasExpectedScrewType`).

**Commits:** the seed writes `:M4`'s seven values, the assembly type `AG1`, the two screws with their
three placement values, and the two grinders. Then it gives each screw the screw type that its
grinder's assembly type expects. No operation exists yet, so there is no status.

**In the knowledge graph:**

- Before: the ontology and the shapes only. `:M4` is declared and holds no value.
- After: `:M4` holds the seven values of the screw type: the minimum approach axial force 0.5, the
  minimum counter-torque 0.5, the minimum preload axial force 0.1, the minimum loosening axial force
  1.0, the maximum loose torque 3.0, the maximum dynamic torque 1.5 and the preparation turning angle
  30.
- After: `:AG1` is a `cfc:AssemblyType` with `:hasExpectedScrewType :M4`. This is the design. It
  is also a product type by Core's range of `cfc:hasProductType`, for a reasoner that applies
  `rdfs:range`; the demo store's ruleset (rdfsplus-optimized) does not materialize range conclusions.
- After: `:Screw1` and `:Screw2` hold the three placement values (the approach depth limit 0.25, the
  screw-head band from -0.5 to 0.5), and each holds `:hasScrewType :M4`, the record of that screw,
  taken from the design.
- After: `:Grinder099` contains `:Screw1` and `:Grinder100` contains `:Screw2`, and both have
  `cfc:hasProductType :AG1` and `cfc:hasAssemblyType :AG1`.

## 2. The decision to unscrew

**Acts:** the planner, wrapping `:Planner1`. A person presses Unscrew on the planner's
dashboard, and the planner opens the operation for that press.

**Reads:** the grinders, by one SPARQL query, which lists them and nothing else. Then two projection
scopes: for each grinder, one chain, the screws it contains (`cfc:containsWorkpiece`), and for each
screw, one chain, `:hasOperation`. A screw that holds an operation is not offered.

**Commits:** one commit. It is the middleware's issue of an operation: it finds a live service
that realizes the unscrewing capability, writes the operation with its status `queued` and the
screw's explicit back-link in the same commit, so the store admits all of it or none of it, and
then notifies that service. The store admits the operation only when its screw's screw type holds
each of the seven values the attempt needs (`:UnscrewingOperationScrewShape`); `:M4` holds them.

**In the knowledge graph:**

- Before: `:Screw1` has no operation, so the drop-down offers it.
- After: a new `:UnscrewingOperation` exists with `cfc:implementsCapability` the unscrewing
  capability of `:ScrewingResource1`, `svc:hasOperationStatus "queued"`, `:concernsScrew :Screw1`,
  `:concernsWorkpiece :Grinder099` and `svc:issuedBy :Planner1`.
- After: `:Screw1 :hasOperation` that operation. The ontology declares it the inverse of `:concernsScrew`,
  so a store that infers inverses holds it anyway; the same commit writes it explicitly.
  `:Screw1` leaves the drop-down, and a second press on it is refused.

The planner then waits for the operation to end.

## 3. The attempt

**Acts:** the screwing resource, wrapping `:ScrewingResource1`, on the notification of the planner.

**Reads:** the operation, with one projection scope whose chains are the screw it names
(`:concernsScrew`), that screw's screw type with each of its seven values (`:concernsScrew`, `:hasScrewType`,
then the value), and the operation's status, result and outcome. The attempt is given the seven
values. A screw holds none of them: only its screw type does, and the detector's resolution of the values refuses a
screw that is given one. When the screw type holds one of them zero times, more than once or not as a finite number,
no attempt is made: the screwing resource commits the operation from `queued` to `failed` with a
reason that begins `no attempt was made`, and the planner is notified of the end.

**Over MQTT:** the graph describes `:Robot1`, which `:ScrewingResource1` names with `:hasRobot`,
and each of its twelve parameters: the broker, the port and the topic, and the set topic of a
parameter that a client may set. At its start, the screwing resource's middleware instance builds
its twenty connectors from that description. The attempt then goes in five steps, all of which
happen on the MQTT broker:

1. It writes the seven values of the screw type to the robot's setpoints. A write connector
   publishes each on its set topic, `Robot1/<parameter>/set`, and the stand-in publishes it again on
   the parameter's own topic.
2. It writes the screw's IRI to the start command. The stand-in publishes `running` on the state
   topic.
3. The stand-in publishes each sample of the screw's example recording on the three reading
   topics (the robot position, the axial force and the unscrewing torque), with its time, its value
   and its phase.
4. It publishes `done`, with the number of samples of each reading and the example recording's tag, then
   `idle`.
5. The read connectors hand every message to the attempt, which builds the frame once each reading
   holds the counted number of samples.

**Commits:** one commit around the attempt. Its entry sets the status `running`. The frame that the
attempt built goes to InfluxDB. The clean exit writes one more
value, the operation's reference to the frame, and no status. After the commit the operation is
still `running`: the anomaly detector ends it. Then the screwing resource calls the detector's
classify workflow with the operation's IRI. The detector is found in the graph by its classify
capability, and the call goes over REST.

**In the knowledge graph:**

- Before: the operation is `queued`, with no reference.
- After: the operation is `running` and holds `:hasTimeSeriesReference`, the address of its frame in
  InfluxDB (`influxdb:///jms-kapps/unscrewing?frame=…`).

For `Screw1` the stand-in publishes the example recording `success`, and for `Screw2`
`missing_screw`. If the attempt fails, because the robot reports a failure, sends a message that is
not a sample, or sends no end within 60 s, the operation is committed `failed` with the reason and
the detector is not called. If the
detector is not reached, the screwing resource commits `failed` itself, with a reason that begins
`the detector was not reached`.

## 4. The classification

**Acts:** the anomaly detector, wrapping `:AnomalyDetector1`, on the call of the screwing resource.

**Reads:** the operation, with a projection scope of four chains: its status, its result, the screw
it names and its reference. Then the effective values of that screw, with one projection scope:
the screw's three placement values, and, through `:hasScrewType`, the seven values of its screw
type. It reads the frame from InfluxDB through the operation's reference. It reads the values afresh
at each attempt. If the operation is no longer `running`, it leaves it as it is.

**Commits:** one commit. It creates the classification, a `prov:Activity` associated with
`:AnomalyDetector1`, and the parameter copy, a record of the ten values of the decision that
names the classification (`prov:wasGeneratedBy`). The store refuses a parameter copy that names
no classification. The commit also writes the operation's outcome and its link to the parameter copy, and
ends the operation. The decision tree of the paper's anomaly detection decides the outcome. After
the store admits the commit, the middleware notifies the planner, which issued the operation.

**In the knowledge graph:**

- Before: the operation is `running`, with its reference and no outcome.
- After, in both endings: a `prov:Activity` `:Classification_…` with `prov:wasAssociatedWith
  :AnomalyDetector1` and `prov:endedAtTime`; a `:ParameterCopy` holding the ten values the
  decision used, with `prov:wasGeneratedBy` that classification; and on the operation `:hasDetectionResult`
  and `:classifiedWith` that parameter copy.

**Ending A, `Screw1`:** the recording `success` passes every node of the tree.

- The outcome is `:ScrewIntact`, and the status is `done`. The operation holds no end position.

**Ending B, `Screw2`:** the recording `missing_screw` passes the approach's depth limit before the
axial force reaches its minimum.

- The outcome is `:MissingScrew`, and the status is `failed`, with the reason
  `svc:hasExecutionResult "ANOMALY: MissingScrew"`.
- The operation also holds `:hasMeasuredApproachEndPosition 0.0`, the position of the last sample of
  its approach. Only an operation with a failure outcome holds it.

**With the mode "Save measurements as uncertainty quantifications" on** (off by default; the
checkbox on the page, or `--save-measurements-as-uq` at the start), the detector also reads the
standard deviation of the robot that measured the attempt, and the same commit writes, beside each
measured position, a random variable (`uq:RandomVariable`) whose literal holds that one
measurement: the measured value as the mean and the square of the standard deviation as the
variance, with the datatype `uq:gmmLiteral`.

- `Screw1` (ending A): the position at contact is `:Screw1 :hasMeasuredScrewHeadPosition`, the
  best estimate, and `:Screw1 :hasScrewHeadPositionVariable` is its variable.
- `Screw2` (ending B): the approach ended before contact, so there is no head position. The
  operation's `:hasMeasuredApproachEndPosition` is the best estimate, and its
  `:hasApproachEndPositionVariable` is the variable.
- A robot without its standard deviation ends the operation `failed` with the reason, and nothing
  of the classification is written.

The page's *Measured positions* list shows these rows. With the mode off, none is written.

The planner reads the status, and the outcome and the reason of a failure, and records them for its
dashboard. If the detector cannot classify the attempt, nothing of the classification is written,
and the operation is committed `failed` with a reason that begins `the anomaly detector could not
classify the attempt`.

## 5. The learning

**Acts:** the learner, wrapping `:Learner1`. It runs every ten seconds
by default.

**Reads:** one SPARQL query for the operations that are `done` with the outcome `:ScrewIntact` and
that it has not learned from, each with its screw type, its reference and the maximum dynamic torque
that the type holds. Then the curves of each attempt from InfluxDB, through its reference.

**Commits:** one commit that updates the maximum dynamic torque of each screw type whose value
changed. It writes to the screw type only, never to a screw, and it changes no operation: each stays
`done` or `failed`, as the detector left it. The placeholder rule sets the value 20 %
above the highest dynamic torque of the disassembly phase over the screw type's intact attempts. The
first value it derives replaces whatever the type holds. After that, the value only rises.

**In the knowledge graph:**

- Before: `:M4 :hasMaxDynamicTorque 1.5`.
- After: `:M4 :hasMaxDynamicTorque 0.84`. The recording `success` has a highest dynamic torque of
  0.7, and 0.7 × 1.2 is 0.84.
- `Screw2`'s operation is `failed`, so the learner does not learn from it. If it were pressed alone,
  the learner would commit nothing.

The anomaly detector reads the screw type's values afresh at each attempt, so the next attempt on a
screw of `:M4` is decided with 0.84, and its parameter copy records it. The demo offers each
screw once, so within one run no further attempt reads the new value: the learner's dashboard shows
the change, and a new run seeds again and restores 1.5.

## What the demo simplifies

The demo's robot controller stand-in publishes two example recordings in place of the robot. The
attempt sets the screw type's values on the robot, and the stand-in echoes each and ignores it,
since in the real cell the robot and the screwdriver would use them. The example
values of the screw type and of the placement are fitted to the two example recordings, so that
`success` is classified `ScrewIntact` and `missing_screw` `MissingScrew`.
The learner's rule is a placeholder: its 20 % margin is an arbitrary number, and it learns from the
decision tree's own outcomes, which are not a confirmed screw condition. The outcome individuals
indicate screw conditions, and the conditions require a disassembly category. The ontology models
both, and no service reads or acts on them.
