# Use case 1: the run, InfluxDB and the learner

Use case 1 is vertical integration. Four participants work an unscrewing operation. The planner
opens one operation per press. The screwing resource makes one unscrewing attempt for each
operation and records its curves. The anomaly detector classifies the attempt. The learner refines
the detection thresholds of each Screw type. They coordinate through the knowledge graph, and they
find each other there. The notifications and the call of the detector go over REST, to addresses
that the graph names. The curves go through InfluxDB.

## Run use case 1

`jms-uc1` runs use case 1. It **clears the dataset it runs against**: `jms-kapps`, unless
`--dataset` names another. It refuses to run while another service is live there, unless
`--force` is given. A second `jms-uc1` on the same machine is refused while the first holds the
page's port, 8091 (`--page-port` names another).

In order, it binds the page's port, checks the guard, seeds, and starts four processes under one
supervisor: the anomaly detector, the screwing resource, the learner and the planner. A
participant starts after the participants it calls, and the planner last, because it reads the
seeded grinders at its start. It waits until each is ready, prints each dashboard's address, and
runs until Ctrl+C. The first stop signal stops all four, and each deregisters its service; a
second one does not cut the stop short.

Its page is on <http://127.0.0.1:8091/>, read-only, and reloads every 2 s. Each card shows one
participant: its state, its resource (`Planner1`, for example), its middleware (the class and
the address of its service), and a button to its dashboard. A button opens the planner's
dashboard. Every dashboard opens in a new tab.

Unscrew is pressed by hand only, on the planner's dashboard. The planner finds the seeded grinders
in the graph, with one SPARQL query, because the mapper cannot list a class. The drop-down offers
every Screw of those grinders that has no operation yet. A Screw that has an operation leaves the
drop-down, and a press on it is refused. A new run seeds again.

The demo holds two angle grinders, `Grinder099` and `Grinder100`, one Screw each. Their product
type `AG1` expects the Screw type `M4` — the design. Each Screw's own Screw type is read from the
design and written to the Screw. Which recording an attempt replays depends on the Screw:
`Screw1` replays `success` and gives ScrewIntact, `Screw2` replays `missing_screw` and gives
MissingScrew. The mapping is in the seed module, not in the graph, because a recording is the
demo's stand-in for the robot.

The terminal log shows each participant's lines behind its name. Each press prints one line:
`Planner: a press of Unscrew opens the unscrewing of Screw1 in Grinder099`, or that it is
refused. Every use of a class scope prints one line: who, what, the scope's name and its property
chains, and what it wants. For example: `Screwing resource receives UnscrewingOperation_… with
OPERATION_SCOPE = [[hasScrew], [operationStatus]]: …`. The learner names the new ScrewIntact
attempts, the value, and what the Screw type held before.

Every value is mock: the thresholds and the placement values, and the learner's rule is a
placeholder. The curves live in InfluxDB, one frame per attempt; see [InfluxDB](#influxdb). The
learner refines the thresholds of each Screw type; see [The learner](#the-learner).

## Run use case 1 by hand

`uv run jms-uc1 seed` seeds the dataset once. It clears the dataset it seeds, guarded as the run
is, so it refuses while another service is live there, unless `--force`. It starts nothing, and
prints the command line of each participant. Each participant then runs from its printed line, in
its own terminal, in any order. Each reads its part from the graph and seeds nothing. The planner
offers the Screws of the grinders it finds; a grinder seeded later appears after its next press
or restart.

**On another computer**: every participant needs the same triple store. Set the connection
variables on each. The screwing resource, the detector and the learner need the same InfluxDB.
Set `INFLUXDB_URL` on each of them, or each uses its own empty local one. A participant registers
`http://<--host>:<port>`. On another computer, start it with `--host <that computer's own
address>` and let its port through. `0.0.0.0` does not work: the middleware would register it as
the address. The port is chosen free at start.

**One process per resource**: a second process for the same resource registers a second service.
Whichever answers in the graph first is used, as in use case 2. Stop each with Ctrl+C; each
deregisters its service.

## InfluxDB

Use case 1 keeps the raw curves of each unscrewing attempt, its *frame*, in InfluxDB 3. The graph
holds the attempt's operation and one reference to its frame, and not the curves. A run uses the
InfluxDB that the environment names, as it uses the triple store. The local InfluxDB is only the
fallback, when no `INFLUXDB_URL` is set, and needs no account. The command in
[The local fallback](triple-store.md#the-local-fallback) starts it with the local store, InfluxDB 3
Core 3.11.5 on <http://localhost:8181>, and the commands there stop and remove both.

The local InfluxDB asks for no token, and answers only on this machine. A configured server is
named with two variables, which are set as the store's variables are on each system:

```bash
export INFLUXDB_URL=https://your-influxdb.example
export INFLUXDB_TOKEN=...
```

The database never comes from the environment. A writer names it, and it defaults to `jms-kapps`,
the name of the dataset. The writer creates the database and its table before its first write.

- **The layout.** A frame is one row for each sample in the table `unscrewing`: the time, one float
  column for each signal (`robot_position`, `axial_force`, `unscrewing_torque`), and three tags.
  `frame` is the frame's id. `phase` is Approach, Form-fit, Preparation or Disassembly, and a
  sample whose phase is not known has no `phase` tag. `source` is only on a replayed frame, and
  names the recording.
- **The reference** is `influxdb:///<database>/unscrewing?frame=<id>&start=<time>&stop=<time>`,
  with the times of the frame's first and last sample. It names no server: it is read from the
  InfluxDB that the reader is connected to.
- **The two recordings in `uc1/data/` are legacy mock data** from the paper's artifact. Their units
  are not known, and their phases come from labels that a person wrote in their `comment` column.
  The frame replay, `jms_kapps.uc1.replay`, maps each label to a phase, or to none, and gives each
  frame it makes from them the source `legacy-mock-d320118/<name>`.

## The learner

The learner is a participant of its own: the resource `:Learner1`, behind its own middleware
instance, run as `python -m jms_kapps.uc1.learner_process --dataset <name> --learn-seconds <s>`.
No notification starts it. Every `--learn-seconds` (default 10) it reads the unscrewing operations
that are `done` with the outcome ScrewIntact since its last run, reads the curves of each through
the operation's own reference, and commits the new maximum dynamic torque of each Screw type in one
`mw.commit()`. It writes to the Screw type only, never to a Screw, and never a placement value. The
anomaly detector reads a Screw's effective values afresh at each attempt, so its next decision uses
the new value.

**Its rule is a placeholder, not a regression.** The maximum dynamic torque of a Screw type is set
20 % above the highest dynamic torque that the type's ScrewIntact attempts recorded in their
Disassembly phase. The 20 % is an arbitrary number. The rule learns from the decision tree's own
outcomes, which are not confirmed Screw conditions, so a wrong threshold can reinforce itself. A
real regression, the other thresholds and a confirmed Screw condition are not built. With the
legacy mock recording `success.csv`, whose highest dynamic torque is 0.7, the rule changes `:M4`'s
mock 1.5 to 0.84; both numbers are mock.

**The first value replaces, then a value only rises.** The first value that the placeholder rule
derives for a Screw type replaces whatever the type held, a seeded mock value too, higher or lower.
So the demo shows one learned change. After that the value only rises, because the rule takes the
highest torque. The rule learns from ScrewIntact outcomes, which all lie below the border to
ThreadFault, so a rule that could lower the value again would move that border down run after run.

**Its dashboard.** The learner's process serves the middleware's state dashboard at `/dashboard`,
whose address it prints when it starts: the boxes of the instance in the tab "System", and the box
"Learner" in the tab "Learner". That box shows the last run and when the next comes, how many
attempts the learner learned from and how many of them were new in the last run, each value it
changed (Screw type, threshold, old → new, when), and the last thing that went wrong (when, at which
Operation's attempt, why). It renders the learner's memory only, so an open dashboard reads no
store. A failed run or a passed-over attempt shows there, and is still logged too.
