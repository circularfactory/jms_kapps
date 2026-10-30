# Use Case 1: Anomaly detection and learning in robotic unscrewing

## Overview

This use case is Section 5.1 of the paper. It shows these requirements of Table 3 of the paper:

| Requirement | Where to observe it |
|---|---|
| R1 | part 5 of [Requirement demonstrations](#requirement-demonstrations) |
| R2 | query Q3 |
| R3 | [Position uncertainty](#position-uncertainty) and part 6 |
| R4 | the IRIs in the answer of query Q3, ended operations among them, and `tests/test_no_deletion_of_individuals.py` |
| R5 | part 7 |
| R6 | part 2 and query Q3 |
| R7 | [Infeasible operation refusal](#infeasible-operation-refusal) |
| R8 | part 1 and [Operation log](#operation-log) |
| R11 | [Robot over MQTT](#robot-over-mqtt) |
| R12 | part 7 and query Q6 |
| R13 | part 3 and query Q4 |

Four services work on the unscrewing of a screw from an angle grinder. Each service has its own
middleware instance:

1. The planning service (the planner) commits one unscrewing operation for one screw, and waits for
   its end.
2. The screwing resource makes the unscrewing attempt on its robot. It writes the time series of
   the attempt to the time-series database, InfluxDB, and calls the classification workflow of the
   anomaly detection service (the anomaly detector).
3. The anomaly detection service classifies the attempt, and commits the detection result with its
   provenance. The same commit ends the operation: *done* when the screw is intact, and *failed*
   otherwise, with a reason that names the detection result.
4. The learning service (the learner) refines the maximum dynamic torque of each screw type from
   the operations that ended with an intact screw.

No service starts with the address of another service. The services find each other in the
knowledge graph.

The seed holds two angle grinders, `Grinder099` and `Grinder100`, with one screw each.

A fifth process, the robot controller stand-in, takes the place of the robot and its controller.

The knowledge graph describes the robot `Robot1` and each of its twelve MQTT parameters. For each
parameter, it gives the broker, the port and the topic. The middleware instance of the screwing
resource builds its connectors to the robot from that description. An attempt sets the seven
values of the screw type on the robot, starts the robot, and collects the samples that the
robot publishes. [Robot over MQTT](#robot-over-mqtt) shows where to read each step.

### Robot cell video

[`robot_unscrewing.mp4`](videos/robot_unscrewing.mp4) is the video of Section 5.1 of the paper.
It shows a robot of the Transformer Cell unscrewing a screw of an angle grinder.

## Run

Do the [setup](../README.md#setup) first. Then, in the root of the clone and in the same
terminal:

```shell
uv run jms-uc1
```

Stop use case 2 first if it runs on the same dataset.

## Pages and logs

### Run page

The page is <http://127.0.0.1:8091/>. It names the deciding service of each operation. The deciding
service is the agent of the classification that generated the values that the operation used. The
links of its table *Processes* open the dashboard of each service.

![The page of a run of use case 1 after both presses: the two steps, a row for each of the five processes, the two operations, whose deciding service is the anomaly detector, and M4's learned value](images/uc1-page.png)

### Planner dashboard

The button *Open the planner's dashboard* opens the dashboard of the planner.

Select `Screw1` in the drop-down of the button *Unscrew*, and push the button. A press commits one
unscrewing operation for the screw, and [Operation log](#operation-log) follows it from the press
to its end.
The box *Planner* lists each pressed screw with the status and the detection result of its operation.

### Screwing resource and detector dashboards

The detection result for `Screw1` is *ScrewIntact*, and its operation ends *done*. For `Screw2`,
the detection result is *MissingScrew*, and its operation ends *failed* with the reason
`ANOMALY: MissingScrew`. The boxes of the two dashboards show each attempt with the seven values of
the screw type that it got, and each classification with the ten values that the decision used.

### Learner dashboard

The learner runs every 10 s. Each run reads the time series of the new operations that ended *done*
with the detection result *ScrewIntact*, and derives from them the maximum dynamic torque of their
screw type. It commits a changed value to the screw type. After `Screw1`, the maximum dynamic torque
of `M4` goes from 1.5 to 0.84. The box *Learner* shows each change with its time, its screw type,
and the old and the new value. When it shows the change, push the button for `Screw2`: its attempt
and its classification get 0.84.

### Operation log

The terminal shows the lines of each service after its name. Each time that a service fetches
through the OGM, a line tells which service fetches, what it fetches, with which projection scope,
and why. Press Unscrew on `Screw1`, and follow its operation from the press to the read of the
detection result by the planner.

`UnscrewingOperation_<hex>` is the name of the operation, the same in each line from the second
line on. `<frame>` is its frame in InfluxDB. `…` stands for text cut from a line.
`OPERATION_SCOPE`, `EFFECTIVE_VALUES_SCOPE` and the other `…_SCOPE` names are the projection
scopes that a read or a write uses.

1. The planner opens the operation:
   `Planner: a press of Unscrew opens the unscrewing of Screw1 in Grinder099`.
2. The screwing resource gets the operation, and reads it with the seven values of the screw type:
   `Screwing resource receives UnscrewingOperation_<hex> with OPERATION_SCOPE = [[concernsScrew, hasScrewType, hasMinApproachAxialForce], …, [hasOperationStatus], [hasExecutionResult], [hasDetectionResult]]: …`.
3. It starts the attempt, and sets the operation `running`:
   `ScrewingResource1: the attempt of UnscrewingOperation_<hex> on Screw1 is given the values of M4: MinApproachAxialForce 0.5, …`.
   It sets the seven values on the robot, one line each, then the start command:
   `ScrewingResource1: hasRobotMinApproachAxialForce is set on Robot1`, …,
   `ScrewingResource1: hasAttemptCommand is set on Robot1`, and `the robot is started on Screw1`.
   The lines of the stand-in come after its own name: `Setpoint hasRobotMinApproachAxialForce <- {"v": …}`
   for each of the seven, `Start: http://w3id.org/circularfactory/Unscrewing#Screw1 replays success`, and at the end
   `Done: example-d320118/success, … samples`.
   When the samples are stored, `ScrewingResource1: UnscrewingOperation_<hex> is running, with its curves at influxdb:///jms-kapps/unscrewing?frame=<frame>&start=…&stop=…`.
4. The anomaly detector reads the operation, then the ten values of the screw for the decision:
   `Anomaly detector reads UnscrewingOperation_<hex> with OPERATION_SCOPE = [[hasOperationStatus], [hasExecutionResult], [concernsScrew], [hasTimeSeriesReference]]: …` and
   `Anomaly detector reads Screw1 with EFFECTIVE_VALUES_SCOPE = [[hasMinApproachPosition], …]: …`.
5. It commits the classification and the detection result:
   `Anomaly detector writes UnscrewingOperation_<hex> with an empty ClassScope = []: …`, then
   `AnomalyDetector1: UnscrewingOperation_<hex> is done, with the outcome ScrewIntact`. For `Screw2`,
   the line is `is failed, with the outcome MissingScrew: …`.
6. The planner gets the notification of the end, and reads the detection result:
   `Planner reads UnscrewingOperation_<hex> with OUTCOME_SCOPE = [[hasDetectionResult]]: it wants the outcome that the detector committed (hasDetectionResult)`.
   For a failed operation, it also reads the reason, with `FAILURE_SCOPE`.

The screwing resource logs `ScrewingResource1: the anomaly detector took UnscrewingOperation_<hex>`
when its call of the detector returns. This line can come before or after the read of the planner,
because the two services are different processes.

## Demonstrations

### Robot over MQTT

In the [workbench](setup.md#triple-store-workbench), select the dataset of
the run, `jms-kapps` for `uv run jms-uc1`, and open *SPARQL*. In the queries, `uns:` is the use
case's namespace, written `:` elsewhere. This query reads the connection
description of the axial force of the robot:

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

The answer is one row: the broker `127.0.0.1`, the port `18860` and the topic
`Robot1/hasAxialForce`.

**The connections.** While a run is live, open the dashboard of the screwing resource from the
page, tab *System*, box *Connectors*. It lists the connectors of the robot:

- Twelve connectors read a parameter: the three readings, the state of the attempt, the start
  command and the seven setpoints.
- Eight connectors write a parameter: the start command and the seven setpoints.

**The setpoints and the samples of an attempt.** Press Unscrew on `Screw1`, and read the terminal
as [Operation log](#operation-log) tells.

### Position uncertainty

The anomaly detection service can save each position that it measures as a random variable, next
to the plain best estimate. This mode is off by default, and a plain run of `jms-uc1` writes no
variable. Start the run with `uv run jms-uc1 --save-measurements-as-uq`.

With the mode on, the commit of each classification also writes the position that the detector
measured:

- The position at contact, as the head position of the screw.

Each position is a `uq:RandomVariable`. Its literal, of the datatype `uq:gmmLiteral`, holds that one
measurement.

Two queries for the workbench, on the dataset of the run. **Q1** reads the best estimate of the
head position of a screw, in mm:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
SELECT ?screw ?estimate WHERE { ?screw uns:hasMeasuredScrewHeadPosition ?estimate }
```

**Q2** reads the literal of that position with its datatype, which is `uq:gmmLiteral`:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX uq: <http://w3id.org/circularfactory/Uncertainty#>
SELECT ?screw ?literal (DATATYPE(?literal) AS ?datatype) WHERE {
  ?screw uns:hasScrewHeadPositionVariable ?variable .
  ?variable uq:hasDistribution ?literal
}
```

Admission checks the literal. `:RandomVariableDistributionShape` in
`src/jms_kapps/uc1/shapes/integration_usecase_shapes.ttl` refuses a random variable whose
distribution is not one `uq:gmmLiteral` that names the six fields `n_components`, `dimensions`,
`covariance_type`, `weights`, `means` and `covariances`.
`uv run pytest tests/test_uc1_uncertainty_live.py --dataset <name>` shows the triple store refuse it.

### Requirement demonstrations

Each part names the test that shows it. `uv run pytest <test> --dataset <name>` runs one test, and
[Tests](../README.md#tests) runs them all. The tests of `tests/test_uc1_claims_live.py` start
`jms-uc1` themselves on the dataset that they get, clear it, and stop again. Thus stop all other
runs of use case 1 first. Their time series go into the InfluxDB database `jms-kapps`, which the
run creates if it is missing.

1. **The services coordinate through the knowledge graph.** Each change of status and each
   detection result is committed to the knowledge graph. Each service starts with no address of
   another service: the operation of the planning service goes to the service whose workflow
   realizes the capability that the operation implements, and the screwing resource finds the
   anomaly detection service by its capability. *Run:* `uv run jms-uc1`, and press Unscrew for both
   screws. *See it:* query Q3 below lists each operation with its issuer, its status and its
   detection result. *Test:*
   `tests/test_uc1_claims_live.py::test_the_participants_coordinate_through_the_graph`.
2. **The anomaly detection service fetches the parameters of the screw type through the OGM at each
   attempt.** *Run:* press Unscrew for `Screw1`. *See it:* one line of the terminal for each
   attempt, `Anomaly detector reads Screw1 with EFFECTIVE_VALUES_SCOPE …`. *Test:*
   `tests/test_uc1_claims_live.py::test_the_detector_reads_the_screw_types_thresholds_through_the_mapper_at_every_attempt`.
3. **The learning service commits a refined maximum dynamic torque to the screw type, and the next
   attempt fetches it.** *Run:* press Unscrew for `Screw1`, and wait until the box of the learner
   shows a change. Then press Unscrew for `Screw2`. *See it:* query Q4 below gives the maximum
   dynamic torque of `M4`; the dashboard of the anomaly detection service shows that the decision on
   `Screw2` used it. *Test:*
   `tests/test_uc1_claims_live.py::test_the_learner_commits_a_value_from_completed_operations_and_the_detector_uses_it_next`.
4. **No service writes past its middleware instance.** Each write of a service is a commit of its
   middleware instance, and its SPARQL queries only read. *Run:*
   `uv run pytest tests/test_uc1_writes.py`. It reads the source of the four services, and needs no
   triple store. *See it:* in the code, as [Code](#code) tells. *Test:*
   `tests/test_uc1_writes.py::test_no_service_of_use_case_1_writes_past_its_middleware`.
5. **The time series of each attempt are kept in the time-series database and referenced from the
   operation.** *Run:* press Unscrew. *See it:* the `hasTimeSeriesReference` of the operation in
   query Q3, and the dashboard of the screwing resource, which names the frame. *Test:*
   `tests/test_uc1_claims_live.py::test_the_sensor_channels_are_a_replay_of_recorded_series`.
6. **A measured position goes through the knowledge graph as a random variable.** With the mode of
   [Position uncertainty](#position-uncertainty) on, the anomaly detection service commits each
   position that it measures as a random variable. The variable has a Gaussian-mixture literal, next
   to the best estimate. Each literal reads back cleanly: it has the datatype `uq:gmmLiteral`, it
   parses, and it holds the best estimate as its mean. *Run:*
   `uv run jms-uc1 --save-measurements-as-uq`, and press Unscrew for both screws. *See it:* the
   queries Q1 and Q2 above. *Test:*
   `tests/test_uc1_measured_positions_claim_live.py::test_each_measured_position_reads_back_as_a_gaussian_mixture_literal`.
7. **Each classification is a PROV-O activity with its agent and its end time, and admission
   refuses a parameter copy that names no classification.** The anomaly detector commits the
   classification with the parameter copy that it used. The shape `:ParameterCopyGeneratedByShape`
   requires each parameter copy to name the classification that generated it. *See it:* query Q6
   below. *Test:*
   `tests/test_uc1_run_live.py::test_a_press_runs_the_loop_end_to_end_and_the_learner_closes_it`
   and `tests/test_uc1_prov_live.py::test_a_snapshot_without_its_classification_is_refused_by_the_store`.

Four queries for the workbench, on the dataset of the run. **Q3** lists each operation with its
screw, the screw type of that screw, the screw type that the design of its grinder expects, the
service that issued it, its status, its detection result and the reference to its time series:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX svc: <http://w3id.org/circularfactory/Service#>
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
SELECT ?operation ?screw ?type ?expected ?issuer ?status ?detectionResult ?timeSeries WHERE {
  ?operation a uns:UnscrewingOperation ;
             uns:concernsScrew ?screw .
  OPTIONAL { ?screw uns:hasScrewType ?type }
  OPTIONAL { ?grinder cfc:containsWorkpiece ?screw ;
                      cfc:hasAssemblyType/uns:hasExpectedScrewType ?expected }
  OPTIONAL { ?operation svc:issuedBy ?issuer }
  OPTIONAL { ?operation svc:hasOperationStatus ?status }
  OPTIONAL { ?operation uns:hasDetectionResult ?detectionResult }
  OPTIONAL { ?operation uns:hasTimeSeriesReference ?timeSeries }
}
ORDER BY ?screw
```

**Q4** gives the maximum dynamic torque that the screw type `M4` holds. After the first change of
the learner, it is different from the seeded value:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
SELECT ?torque WHERE { uns:M4 uns:hasMaxDynamicTorque ?torque }
```

**Q5** gives, for each operation, the maximum dynamic torque of the parameter copy that its
classification used. The copy of an earlier classification keeps its value after the learner
changes the screw type:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
SELECT ?screw ?torque WHERE {
  ?operation uns:concernsScrew ?screw ;
             uns:classifiedWith ?copy .
  ?copy uns:hasMaxDynamicTorque ?torque .
}
ORDER BY ?screw
```

**Q6** lists each classification with its agent, its end time, its parameter copy and the
detection result of the operation that it decided:

```sparql
PREFIX uns: <http://w3id.org/circularfactory/Unscrewing#>
PREFIX prov: <http://www.w3.org/ns/prov#>
SELECT ?classification ?agent ?endedAt ?copy ?operation ?detectionResult WHERE {
  ?classification a prov:Activity ;
                  prov:wasAssociatedWith ?agent ;
                  prov:endedAtTime ?endedAt .
  ?copy prov:wasGeneratedBy ?classification .
  ?operation uns:classifiedWith ?copy ;
             uns:hasDetectionResult ?detectionResult .
}
ORDER BY ?endedAt
```

### Infeasible operation refusal

One command shows it, on a dataset that it clears and seeds as `jms-uc1` does:

```bash
uv run jms-uc1 infeasible-operation --dataset <name> --record refusal.nq
```

For this demonstration, the seed also writes these items:

- The screw type `M3`, which holds none of the seven values that an attempt needs.
- The assembly type `AG2`, which expects `M3`.
- The screw `Screw3`, and the grinder `Grinder101` that contains it.

The command starts the five processes of use case 1, waits until each is ready, and presses
Unscrew two times on the planning service. Then the triple store admits or refuses the commit of
the planner, against the shapes of use case 1.

1. Unscrew on `Screw1`, whose screw type `M4` holds the seven values. The store admits the
   operation, and it ends `done`.
2. Unscrew on `Screw3`. The store refuses the commit by `UnscrewingOperationScrewShape`. The
   operation is not written, and no resource gets a notification.

The command prints the answer of the store to each press, then stops the five processes:

```text
1. Press Unscrew on Screw1, whose Screw type M4 holds the seven values the attempt needs
   the store: admitted; UnscrewingOperation_<hex> ended done, outcome ScrewIntact
2. Press Unscrew on Screw3, whose Screw type M3 holds none of the seven values
   the store: refused, by UnscrewingOperationScrewShape at UnscrewingOperation_<hex>: An unscrewing operation concerns exactly one Screw, whose Screw type holds each of the seven values the attempt needs (the six detection thresholds and the preparation turning angle), once each.
   the store's validation report:
     …
   the report is written to refusal.nq
Stopping the five processes...
```

It exits 0 when the first operation ended `done` and that shape refused the second. Else it exits
1, and names the step that did not occur. `--record FILE` writes the validation report as the store
sent it.

The recorded report, `tests/reports/infeasible-operation.nq`, holds one validation result. Its
focus node is the refused operation, its path `:concernsScrew`, its value `:Screw3`, its source
shape `:UnscrewingOperationScrewShape`, and its message the message above. The shape is in the
shapes file of use case 1, `src/jms_kapps/uc1/shapes/integration_usecase_shapes.ttl`.

*Test:*
`tests/test_uc1_infeasible_operation_live.py::test_the_store_refuses_the_infeasible_operation_by_the_shape`.

## Code

The package of use case 1 is in `src/jms_kapps/uc1/`. Each write of its four services is
a commit of the middleware instance of the service. Its ontology is
[`integration_usecase.ttl`](../src/jms_kapps/uc1/ontology/integration_usecase.ttl), with
[`uncertainty.ttl`](../src/jms_kapps/uc1/ontology/uncertainty.ttl) beside it, and its shapes are
[`integration_usecase_shapes.ttl`](../src/jms_kapps/uc1/shapes/integration_usecase_shapes.ttl).

The two recordings that use case 1 replays in place of the robot are in
[`src/jms_kapps/uc1/data/unscrewing_timeseries/`](../src/jms_kapps/uc1/data/unscrewing_timeseries/README.md).
