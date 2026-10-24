"""Use case 1: vertical integration.

A screwing resource records the curves of each unscrewing attempt. An anomaly detector reads them
and classifies the attempt. A learner refines the Detection thresholds of each Screw type from
the results. A planner opens each operation. The four of them find each other in the knowledge
graph; the planner's dispatch and the resource's call of the detector go over REST, to addresses
that the graph names.

`seed.py` writes what a run finds before any participant starts: the demo grinder, the Screw in it, the model it is, and the example thresholds of the Screw type that model is built with, fitted to the two example recordings the paper's artifact ships.

`ontology/integration_usecase.ttl` declares the classes of this use case: the Screw type and the
Screw, the ten properties of their effective values, the parameter copy and the unscrewing
outcomes. `vocabulary.py` names the terms that the Python uses. `effective_values.py` resolves
the ten values of one Screw, seven from its Screw type and three placement values of its own, which the detector
decides with. `frame.py` holds a frame, the raw curves of one attempt, and `timeseries.py` writes frames
to InfluxDB and reads them back through the reference an operation holds. `replay.py` makes a
frame from a recording in place of the robot. `data/unscrewing_timeseries/` holds the two
recordings of the paper's artifact, one of a successful unscrewing attempt and one of a missing
screw. Both are example data: example recordings of an unscrewing attempt, to try the anomaly
detector against, not training data.

Two parts of the domain logic are built. `decision_tree` is the decision tree that classifies one
unscrewing attempt, as a pure function. `learner` is the learner, which derives the maximum dynamic
torque of each Screw type from the completed operations with a placeholder rule, and
`learner_process` runs it as a participant of its own, every `--learn-seconds`, with a dashboard
whose box "Learner" (`learner_dashboard`) shows what the learner keeps in `learner_memory`.

`screwing_resource.py` is the first of the four participants: its middleware instance takes each
unscrewing operation the planner dispatches, makes one attempt, keeps the attempt's frame in
InfluxDB, and leaves the operation `running` with its reference. Then it calls the detector's
classify workflow, which `classify_call.py` finds in the graph by the detector's Classify
capability. Before an attempt, it ends the operation `failed`, with why, when its Screw type
holds no usable value of the seven type values (the minimum approach axial force, the minimum
counter torque, the minimum preload axial force, the preparation turning angle, the maximum loose
torque, the minimum loosening axial force and the maximum dynamic torque), and the attempt is
given those seven values otherwise. When it cannot reach the detector, it ends the operation
`failed`, with why. Its
dashboard's box "Attempts" (`screwing_resource_dashboard`) shows each attempt from what it keeps in
`screwing_resource_memory`.

`anomaly_detector.py` is the second: its middleware instance serves that classify workflow. It
reads the Screw's effective values and the attempt's frame, and runs the decision tree. In one
commit it records the outcome and a parameter copy of the ten values, and ends the operation
`done` for the outcome ScrewIntact and `failed` for every other outcome, with an execution result
that names it. An attempt that it cannot classify ends the operation `failed`, with why. It ends an
operation only while the operation is `running`. Its dashboard's boxes "Classifications" and "Screw
type values" (`anomaly_detector_dashboard`) show each classification and what each Screw type held
at its last read, from what it keeps in `anomaly_detector_memory`.

`planner.py` is the participant that opens the work: one press, one Screw, one unscrewing
operation, as use case 2's warehouse management system spawns one box per press. It issues the
operation with the middleware's issuing call, naming the Screw and the workpiece it sits in; one
press is one commit, which carries the operation, its `queued` status and the Screw's explicit
back-link (the call's update of an existing instance). It then waits for that operation to end; a
dispatch that was refused, an operation that failed and a wait that timed out are each recorded
and none of them raises. `planner_reads`
holds the two things it reads from the graph — the Screws it offers, with their products, read
once at start, and an operation's outcome — `planner_memory` what it knows of its presses, and
`planner_process` runs it as a participant of its own, with a dashboard whose box "Planner"
(`planner_dashboard`) offers the Screws and shows what became of each press.
"""
