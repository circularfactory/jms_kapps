# A box's journey, the strategies and the faults

A box travels from the WarehouseManagementSystem (WMS) through the station and back. Its *transport*
is the operation that carries it: one `:BoxTransportOperation` in the knowledge graph, whose status
says where the journey is (`queued`, `running`, `done` or `failed`), and which is assigned to one
module at a time. A *hop* is one step of the journey, from one module to its neighbour. A *handover*
is the change of a box's possessor, written as one commit. Every state transition is admitted by the
triple store before it is carried out.

## Spawning a box

The WMS is a service of its own, with its own middleware instance, two workflows, `spawn_box` and
`set_spawn_rate`, and a dashboard of its own. **To spawn a box**, press **Open the WMS** on the
Launcher page: its dashboard opens on the tab **WMS**. In **Spawn a box**, pick the origin from the
entry modules and the destination from the exit modules, or leave either to the WMS, which picks a
random free entry module as the box's origin and a random exit module as its destination, never
the origin.

**Roles are optional**: entry and exit are roles of a module, and one module may have both. Where
the topology names no entry module, the form offers every module as the origin and the WMS picks a
random free module; where it names no exit module, the form offers every module as the destination
and the WMS picks a random module other than the origin. The shipped cross names Modules 1 and 2 as entry
modules and Modules 4 and 5 as exit modules, so a box never starts or ends on Module 3; the grid
names its entry row and its exits.

A spawn that names a module without a role the topology names is refused, for example with
"Module4 is not an entry module", and one that names no module of the topology, where it names no
such role, with "Module9 is not a module". A box's origin and destination always differ. The WMS
picks only a destination that the origin reaches over the connections, each one way as listed, and
only an origin that reaches the named destination, or else some destination. The form keeps
offering every module a spawn may name, and a refusal says why: "Module4 cannot be reached from
Module1", "no exit module can be reached from Module1", "no free entry module can reach Module4"
or "no free entry module can reach an exit module" ("module" for a role the topology does not
name). On the shipped topologies every pick stays possible. Where a spawn leaves the WMS an end to
pick, its **WMS strategy** picks among these candidates, and the picks above are those of `real`,
the default; a request that names both ends is checked as above, and no strategy is asked (see
[The strategies](#the-strategies)).

**Spawn rate** sets how many boxes a minute the WMS spawns on its own, each left to it, until it
is set to 0; it shows the current rate. The WMS starts a spawn at each interval of the rate, also
while earlier spawns are still on their way. At most as many spawns are under way at once, of the
timer and of the form or a script alike, as the topology has modules that may be an origin, and
one beyond that is refused with "every origin has a spawn under way"; spawns under way at once
pick different origins.

Every spawn asked for ends in one record at INFO on the WMS's log, which the load tests count:
"spawned Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10 onto Module1, for Module25", or "spawn refused:
<reason>", with its origin and destination where they are known. The WMS names each box it spawns by
a new UUID, `Box-<UUID>`. A spawn counts as spawned once the triple store
admitted the create of its box, also when its origin then cannot be notified, which a warning and
the answer say.

The WMS's dashboard:

- **Last spawns** shows the answers to the 20 newest spawns, accepted or refused with the reason: a
  spawn while no entry module is free is refused with "no entry module is free" (on a topology that
  names no entry module, "no module is free"), and one whose origin refuses to reserve itself with
  the origin's reason, such as "Module1 refused to reserve: occupied by Box-<UUID>". One whose
  create the triple store refuses shows the triple store's reason, the shape it names and that
  shape's message, such as "the triple store refused creating Box-<UUID> on Module1, by
  FlexConveyorModulePossessionShape at Module1: A FlexConveyorModule may possess at most one Box at
  a time."
- **Held boxes** and **Transports** show the boxes the WMS holds and the transports it opened, with
  their last known status.

Each of these dashboard boxes shows what the WMS keeps in memory, from its own spawns and the
notifications that a transport ended. A spawn lasts until its box
is created on its origin and the origin is notified, a few round trips to the triple store.

A script spawns without the page, with the same JSON on the same routes:
`POST /workflows/spawn_box/execute` with `{"origin": ..., "destination": ...}`, either left out,
and `POST /workflows/set_spawn_rate/execute` with `{"boxes_per_minute": 6}`, at the WMS's own
address or through the Launcher, under `http://127.0.0.1:8090/p/WarehouseManagementSystem1/`.

## The journey, step by step

1. **The WMS spawns the box directly on the origin.** It asks the origin's Reserve, and writes
   nothing before the origin accepts: an origin that holds a box or has reserved itself for
   another refuses, and the spawn is refused with its reason, with no other module tried; an
   origin that cannot be reached refuses it too, with why ("Module1 is not reachable: ..."). A
   module that passes a box onto the origin at the same moment is refused as reserved when the WMS
   asked first, and waits its turn. The origin looks ahead, as every module does (step 3): it
   accepts only once its own next module has reserved itself for the box, so an origin never fills
   with a box that cannot move on, and a box that waits for the origin comes before the spawn,
   which is then refused with "Module1 refused to reserve: Box-<UUID> has waited longer".

   Once the origin has accepted, **one commit creates the box on the origin** with its
   `:BoxTransportOperation`, `queued` and assigned to the origin. If the triple store refuses it, as
   when a neighbour with `skip-reserve` armed has filled the origin since its answer, nothing is
   written, and **Last spawns** shows the triple store's reason. Then the WMS notifies the origin
   through its middleware instance: the origin's intake reads the transport and carries the box on,
   as a module does after a Receive. So a spawn is one call, one commit and one notification, and
   the WMS moves no box. An origin whose notification has not come within 30 s of its answer to
   Reserve frees its reservation and writes nothing: the WMS owns the station's entry.
2. **The origin's BoxTransportWorkflow**, which the notification starts, commits the transport
   `running` and carries the box on. The transport is assigned to one module at a time, the one
   that carries the box on now (`cfc:executedOn`, and that module's BoxTransportCapability).
3. **Each hop** goes to the first neighbour that the module's **module strategy** names, from the
   connections, which the module reads from the knowledge graph at every decision, and the second it
   names is the detour below. With `real`, the default, that is the next module on a shortest path
   to the destination, by hop count over the connections; a tie goes to the module with the lowest
   IRI, and no path runs straight back to the module the box came from. See [Each hop](#each-hop).
4. **The destination** gives the box back to the WMS in one commit with the transport `done`,
   moves it, and notifies the WMS, which checks the transport and logs that it accepted the box.
5. **A module that cannot go on**, because the next module refused to reserve with a refusal that
   does not pass, the triple store refused the handover, or no path leads to the destination,
   commits the transport `failed` with the reason and notifies the WMS. See [When a module cannot go
   on](#when-a-module-cannot-go-on).

## Each hop

The hop works like the real FlexConveyor: the giving module asks the receiving module's Reserve.
**Routing keeps boxes flowing:**

- **Look-ahead.** A receiving module accepts only if it is free and either it is the box's
  destination, or its own next module accepts a plain Reserve for the box at once; that inner ask
  looks no further ahead. Otherwise it refuses at once, as `blocked`, and keeps no reservation. The
  box then goes on to the module reserved ahead, which waits for it 30 s and two transit times. So
  a box moves only onto a module from which it can move on. A next module that cannot be reached is
  left out, and the module looks ahead to the next one its strategy names, the module that holds
  the box still excluded; with none left, it refuses "no live route on", and the asker leaves it
  out in turn. It leaves such a module out at every Reserve of the box while it cannot be reached,
  but warns about it once per box: again only once the box has left its waiting order, or that
  module has answered a look-ahead for the box since.
- **One detour.** When the receiving module refuses, the giving module asks one other neighbour
  first: not the module the box came from, and one from which the destination is reached without
  coming back, so the module the next one ties with. Only if that one refuses too does it keep the
  box, wait the retry time (1 s unless `--retry-time` sets another) and ask both again, with no
  bound, while the receiving module refuses as occupied, reserved, behind or blocked. Any other
  refusal then ends the transport, but that of a module that cannot be reached, or that refuses
  "no live route on" (see [When a module cannot go on](#when-a-module-cannot-go-on)). A detour
  that cannot be reached is left out for the box, and the module asks its strategy again: where it
  names another detour, the module asks the first and then that detour at once, without waiting.
- **The deadlock alarm.** A box that waits for a Reserve longer than the deadlock alarm time of its
  launch, counted from its module's first ask, raises the alarm once: one WARNING line in the
  module's output that starts with "possible deadlock:" and names the module, the box, the time
  waited and the modules it asks, and one event of the kind `deadlock_alarm` on the module's
  handover event stream, which the monitor shows as a "possible deadlock" notice. The box keeps
  waiting: nothing fails. The time is 300 s unless `jms-uc2 --deadlock-alarm-seconds` sets
  another, and the Launcher page's field **deadlock alarm (s)**, beside the transit time, starts at
  it and sets it for each launch; the page refuses a value that is not a positive number, before
  anything is cleared, and the server checks it again. The run core puts it on every module's
  command line, `--deadlock-alarm-seconds`.
- **Longest-waiting first.** A free module serves the box that asked it first, and refuses any
  other as `behind` ("Box-<UUID> has waited longer"). A box keeps its place, also when it was
  refused through a look-ahead, and the WMS's spawn waits behind every box; a box leaves the order
  when it arrives, moves elsewhere or its transport ends, and one that has not asked for twice the
  retry time and 10 s comes first no more until it asks again.

On the cross no box deadlocks, and on the grid boxes keep flowing; on other topologies the
deadlock alarm shows a box that waits too long.

Once the receiving module has accepted, the giving module commits the change of possession and the
transport's new assignment in one commit, which the triple store admits or refuses before anything
moves.
Only then does it convey the box, its own move, and call the receiving module's Receive, the
receiving module's light barrier, which never refuses. The receiving module answers, reads the box
with its transport, and its BoxTransportWorkflow carries the box on. So a hop is two calls between
the modules, Reserve and Receive, and one commit; no notification passes between them, and no
module exposes a Convey.

A box the receiving module reserved itself for that has not arrived within 30 s and the transit
time, or two transit times for a box reserved ahead, frees its reservation. When the box is its own
already, the giving module was lost after its commit, and the receiving module ends the transport
`failed` with a reason that names it ("Module1 is lost: ...").

## When a module cannot go on

**A module whose next module is not reachable** (the knowledge graph names no live workflow of it,
its process is gone, or a call of it fails or times out) leaves it out for the box, with one warning
that names it ("Module9: leaves Module15 out for Box-<UUID>: Module15 is not reachable: ..."), and
no traceback. It asks its module strategy again, with the connections to each module left out
removed and with no module the box came from, which is then a valid choice: on two-way connections,
the box may go back where it came from, and on by another path. A module left out stays out while
the box is there, and so does a next module that refuses "no live route on". A detour that cannot
be reached, or refuses "no live route on", is left out the same way, and the module asks its
strategy for another detour.

Only where no neighbour is left to ask does the module end the transport `failed`, with a reason
that names the destination and each module left out ("no live route to Module9: Module3 and
Module1 are not reachable"), keep the box, and notify the WMS, whose dashboard shows the reason; on
a one-way topology one crash can be enough. An origin with no live route on refuses the WMS's
Reserve with that reason, so the spawn is refused, and nothing is written. With `skip-reserve`
armed, a hop the triple store refused asks first and leaves no module out.

Only the module a transport is assigned to writes it. When a failure comes after the move, the box
is on the next module and the transport is assigned to it: the module that passed both on writes
nothing, and the next module ends the transport at the bound of its reservation, or at its stop. A
next module that crashed after the commit leaves the transport `running` until its next start, when
the semantic middleware's crash recovery ends it. The WMS ends the transport when its notification
cannot reach the origin, on which it created the box, and the origin leaves that transport alone. A
module that is lost after its commit, before its Receive, leaves the transport to the next module,
which ends it at the bound of its reservation.

## The strategies

Who chooses where a box goes is a *strategy*, chosen by its name when a run starts. There are two
kinds, both in `jms_kapps.uc2.strategies`:

- The **module strategy** names, for a box on a module, the neighbours the module asks, in order,
  at most two: the first, and the detour if the first declines; none where the box cannot go on.
  It is given the module, the box's destination, the module the box came from, the module reserved
  ahead for the box, and the connections as the module read them, each with its direction. A
  module asks it wherever it chooses a neighbour: when it carries a box on, and at its look-ahead,
  where it asks the first. A module keeps the first two neighbours of an answer, and drops one to
  which none of its connections leads, with one WARNING that names the strategy; where none is
  left, the box cannot go on, as where no path leads on. The routing decisions on its dashboard
  keep their columns: next and detour are the strategy's two, and ties are the other modules first
  on a shortest path.
- The **WMS strategy** picks the origin and the destination of a spawn among its candidates: each
  free origin the spawn allows, with the destinations it allows that the origin reaches, given the
  connections as the WMS read them and the WMS's chooser. A pair that is not a candidate refuses
  the spawn, "the strategy <name> chose Module1 to Module7, which is not a candidate", logged like
  every refusal.

**`real`**, "Real behaviour", is one strategy of each kind and the default of both: the behaviour
these pages describe. A synthetic strategy, one that makes traffic a benchmark can repeat, says so:
its label ends in "(synthetic)". A strategy only chooses; it never writes, reads the triple store or waits. The look-ahead, the bound of a reservation, the waiting order, the stop rules and
what the triple store admits stay the module's and the WMS's.

**Choosing one.** `jms-uc2 --module-strategy <name> --wms-strategy <name>`, both `real` by default,
runs the first launch with them, and the Launcher page's two pickers, **Module strategy** and **WMS
strategy**, among the run settings, start at them. Each picker lists the strategies of its kind
by label, and every launch from the page, of a shipped topology or of the editor's, runs with the
two chosen; the page shows the strategies in force beside the running topology's name. The
Launcher's server lists them in `GET /api/strategies`, as `{"module": [{"name": "real", "label":
"Real behaviour"}], "wms": [...]}`; both launches take `module_strategy` and `wms_strategy`, in the
body of `POST /api/launch` and as query parameters of `POST /api/topology/launch`, both `real` if
left out; and `/api/state` names the strategies in force. The run core, which the Launcher runs on,
puts `--module-strategy <name>` on the command line of every module process and `--wms-strategy
<name>` on the WMS's, and each process loads its strategy by that name at its start. A run without
the Launcher, as the measurement harness's, names the two to the run core as `module_strategy` and
`wms_strategy`, both `real` if left out.

**A name that none has** is refused with a reason that names it and the strategies of its kind,
such as "no module strategy 'nosuch'; the module strategies are real". `jms-uc2` exits with it
before anything is cleared; a launch from the page is refused with 422, and the run that is there
stays; a process started with it ends at its start, and the Launcher shows it `failed` with that
reason.

**Where strategies come from.** Two Python entry-point groups, `jms_kapps.uc2.module_strategies`
and `jms_kapps.uc2.wms_strategies`, declared in the manifest: each entry is named as the strategy
and loads one strategy object, and jms_kapps registers `real` in both. An entry whose module cannot
be imported, an object of the other kind, and a second strategy of one kind with a name already
found are each skipped with one WARNING that names it. The benchmark strategies live in the
`evaluation` folder, whose [README](../../src/jms_kapps/evaluation/README.md) says how one
registers; use case 1 and use case 2 import nothing from it, so they run without it,
and the Launcher then offers `real` alone.

## The faults

Each module has two switches, the paper's *fault injection*: two faults of a module's own logic,
each of which makes the module write what a shape forbids, so that the triple store refuses the
write. The Launcher page arms and disarms each, in the
module's row, and shows what the Launcher commanded; a module starts with both off.

- **`skip-reserve`.** The module hands a box on without asking the next module's Reserve. If that
  module holds a box, the triple store refuses the handover before anything moves. The module logs
  the report, emits the refusal on its handover event stream, then asks Reserve and waits its turn,
  so the box still arrives. If the next module is free, nothing collides: the module conveys the box
  and calls Receive, and the next module carries it on. The fault skips the look-ahead and the
  detour for the module's own hop too, so on a busy station the triple store refuses its hops
  regularly. The module then takes a box from a module without looking ahead, so its next module is
  reserved for nobody; the WMS's Reserve is looked ahead for all the same, so arm the fault on a
  module inside the station to see it collide: Module5 of the shipped `merge-fault-demo`, which is
  built for fault injection (see [The shipped topologies](topologies.md#the-shipped-topologies)).
- **`complete-without-releasing`.** The module, as a box's destination, commits the transport `done`
  before it gives the box back. The triple store refuses that while the module holds the box. The
  module logs the report, emits the refusal, then gives the box back with the last handover.

The refusing module's dashboard shows each refusal in the semantic middleware's box **Refused
writes**, in the tab **System**: its first line names the verb and the shapes, and it opens to the
results and the whole report. The WMS's dashboard has the same box for its own refused writes. The
monitor's Refusals view shows each refusal as rows a person reads (see [The
monitor](launcher.md#the-monitor)).

## Every rule refuses a write at commit

A box has no lifecycle state of its own: its transport's status says where the journey is, and its
possessor says where the box is. The rules that tie the two together,
`:RunningTransportBoxPossessedShape` among them, are SHACL Core shapes, so the triple store refuses
a write that breaks one when the write is committed.
`tests/test_uc2_shapes.py` proves each rule, including writes that never touch the box.

One rule is not SHACL Core: a transport's destination must be reachable from its origin over the
station's connections, which no Core constraint can state. It is a SHACL-SPARQL shape,
`:ReachableDestinationShape`, in a file of its own, `flexconveyor_usecase_sparql_shapes.ttl`. Its
SPARQL target selects the transports whose destination is not reached, and the triple store
evaluates that target over the whole dataset at every commit. So a launch and the measurement
harness do not load the file, which would add the price of one property path over every transport to
every commit. The shape tests and `jms-uc2 audit` load it. With it loaded the triple store refuses,
at commit, a transport created toward an unreachable module, a transport whose destination changes
to one, and the removal of a connection that the destination was reached by.

`jms-uc2 reachable-destination --dataset ogm-disposable-<id>` shows the rule firing, apart from a
launch: it seeds three modules, opens a transport, and prints the triple store's answer to a
transport toward a module that no connection reaches and to the removal of a connection, then clears
the dataset. The [user
guide](../user-guide.md#see-the-triple-store-refuse-a-rule-that-shacl-core-cannot-express) shows how to
read its output.

## Every capability belongs to exactly one resource

A capability cannot exist without a resource, and a resource can have several. So exactly one
resource links to each capability (`cfc:hasCapability`), and the shapes that both use cases seed
check it at every commit: `:CapabilityResourceShape`, SHACL Core, on the inverse of
`cfc:hasCapability`, with a minimum and a maximum of one. The triple store refuses a capability that
no resource links to, and one that two resources link to, also when the second link comes in a later
write. The shape targets `cfc:Capability` and each of the use case's capability classes, because the
semantic middleware types a capability by its use case's class only.

The semantic middleware registers each capability with its resource's link in one commit, and an
operation takes its capability from the resource it runs on. In use case 2 the
WarehouseManagementSystem's create reads the origin's BoxTransportCapability, and a module's
handover the receiving module's, each by the module and the capability class. Use case 1's planner
asks its middleware instance for an UnscrewingCapability, which resolves it to the capability of a
live screwing resource. So a run never breaks the rule, and an operation's resource is its
capability's resource by construction.

`jms-uc2 capability-resource --dataset ogm-disposable-<id>` shows the rule firing: it seeds the
dataset, makes three writes, a resource with two capabilities, a capability with no resource and
one that two resources claim, prints each with the triple store's answer, and clears the dataset.
`tests/test_capability_shape_live.py` proves the writes.
