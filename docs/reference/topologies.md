# Topologies and the editor

A *topology* is the station of use case 2: its FlexConveyor modules, and the connections between
them. `jms-uc2 --topology` names one, and the Launcher page launches one or builds one in its
editor.

```bash
uv run jms-uc2 --topology grid      # a shipped one: cross, grid, line, merge-fault-demo, pair or tee
uv run jms-uc2 --topology my.json   # any other topology, a module list (below)
```

## The format: a module list

**A topology is the first iteration's module list**: a JSON list of modules, as the first
iteration wrote them in `Usecase_FlexConveyor_decentral/src/Visualizer/` on the `paper` branch of
its repository. The *first iteration* is the paper's own artifact, from which these use cases are
rewritten (see [The assets](assets.md)). The module list is the one format in which topologies are
exchanged between machines that run use case 2, and the format of the five shipped ones.

Each module has its `id`, its IRI in `fci:` (an *IRI* is a persistent name in the knowledge
graph), and its properties under their IRIs, each IRI encoded as the Object–Graph
Mapper (OGM) encodes a field name, each with a list of values:

- its types under `rdf:type`: `fc:FlexConveyorModule`, and the roles `fc:EntryModule` and
  `fc:ExitModule`, which are optional. A module that lists no type is a FlexConveyorModule.
  **Roles are optional for the topology too**: one that names no entry module lets a box start on
  any module, and one that names no exit module lets a box end on any module but its origin. One
  module may have both roles.
- an optional label under `rdfs:label`. Without one, the pages show the module's local name.
- an optional transit time under `fc:hasTransitTime`: a list of one number, the seconds the module
  takes to convey a box, 0 or more. 0 means that the module conveys at once, with no wait. A module
  without one takes the transit time of its launch (below). A value that is no number, is negative,
  or is more than one value is refused, naming the module. The key is the one the Launcher's
  `GET /api/topology/format` names as `transit_time`.
- its connections under `fc:hasConnection`, each with the module it leads to (`fc:connectsTo`),
  its direction (`fc:hasDirection`: `fc:North`, `fc:East`, `fc:South` or `fc:West`) and its port
  (`fc:onPort`, 0 where none is given).

Every value is in a list, even where there is one, and a key that is none of these is refused.

`src/jms_kapps/uc2/topologies/pair.json` is the smallest example. The first iteration's files load
as they are. The seed writes every type listed into the knowledge graph, and every connection runs
one way, as it is listed. The seed gives every module its handover abilities
(`hasHandoverAbility`), which the files do not carry. A file in any other format is refused with the format it needs.

## The load check

**A topology is refused when it is loaded**, by `jms-uc2 --topology` and by a launch from the
Launcher page, before anything is cleared:

- **where it has no module**, with the reason "a topology needs at least one module". A run on it
  would have the WMS and the monitor and no box to spawn. A topology of one module loads.
- **where it has a pure two-way corridor**: a module whose only two connections both run both
  ways. The refusal names the module and its two neighbours, for example "Module2 is a pure two-way
  corridor: its only two connections, to Module1 and to Module3, both run both ways. [...]". Two
  boxes that meet there head-on, one each way, could wait for each other forever, with no
  neighbour to step aside to.

Any other topology loads, two-way connections and cycles too: a module takes a box only where the
box can move on at once, and a box whose next module declines tries one detour first (see
[Each hop](journey-and-faults.md#each-hop)). Where boxes on a cycle could still wait for each
other, the deadlock alarm makes the wait visible; it fails nothing. The shipped tee, grid and
merge-fault-demo are one-way.

## The transit time

Each move is simulated: it takes the module's own transit time. **A module's transit time is in
the knowledge graph** (`fc:hasTransitTime`, a decimal, in seconds): the seed writes exactly one on
every module of a launch, the module's own from the topology file or else the launch's. The
module's process reads it once, when it starts, together with the longest one of any module, T,
which bounds a wait for a box (30 s and T, or 30 s and 2 T for a box reserved ahead).

The Launcher page's field **transit time (s)**, among the run settings, is the launch's value:
its default is that of `jms-uc2 --transit-time` (3 s), the server refuses a negative value or a
text, and 0 is allowed. A module whose individual in the knowledge graph holds no transit time, or
more than one, does not start: its process ends with the reason, and the Launcher shows it
`failed` with that reason. The module's own dashboard box shows its transit time.

## The topology editor

The Launcher page's topology section has one grid, which draws the topology chosen in the picker,
read-only. **Edit** makes that grid editable, in edit mode, on the chosen topology, a shipped one
or the one that runs; **Empty grid** opens it on an empty grid, and **Upload** on a topology file
(below). In edit mode the grid has an empty row and column around the modules. How edit mode
asks before it discards an edit is in [The topology section](launcher.md#the-topology-section).

- **A click on an empty cell adds a module** there, named in order (`Module6`, labelled
  `Module 6`, after `Module5`), and selects it.
- **A click on a module selects it**, and a second click releases it. The selected module is made
  an entry, an exit, both or neither with its two checkboxes, and its **Remove** takes it out,
  with every connection to it.
- **Its transit time field** gives the module its own transit time in seconds (0 allowed, no wait;
  empty leaves the launch's value, the Launcher's field), as `fc:hasTransitTime` in the module
  list. A value that is no number or is negative is refused in the field, and by the load check.
- **A click on the gap between two neighbouring modules cycles their connection**: none, one way,
  the other way, both ways.

On every change the editor sends what it holds, a module list, to the server's load check, and
shows the result and its reason under the grid. **Launch** launches it only while that check
passes, and asks first, as every launch does, since it clears the dataset. The server checks it
again, and the page then says `running: cross (edited)`, by the topology the editor started from,
or `running: empty grid (edited)`. The launch ends edit mode, and the picker selects its entry for
the running topology, `cross (edited), running now`, which the grid draws and **Edit** opens
again. The editor writes only what the
format names: a new module is a FlexConveyorModule with a label, and a module keeps every type it
was read with. It changes no label; a connection it adds, or takes out and adds again, is on port
0.

**Where each module is drawn.** The editor opens a topology with each module on the cell the
server's layout gives it, the rule the monitor draws by. From there each module stays on its cell,
and a module added stays on the cell clicked: the format carries no cells, and the rule puts a
module that is not connected to the first one on a row below the others. For a topology whose
modules are all connected the rule gives back the editor's cells, since every connection leads to
the neighbouring cell in its direction; the monitor draws a part that is not connected on a row
below. A topology with a connection to a module that is not the neighbour in its direction does
not open in the editor, since the grid cannot show it.

## Topology files

A topology file is a module list, the format above, which the shipped topologies and `--topology`
use too, so that a topology built on one machine runs on another.

- **Download**, in edit mode, saves what the grid holds as one, named after the topology it
  started from: `pair.json`, `empty grid.json`, or the name of the file it was opened from.
- **Upload**, beside **Empty grid**, opens one in edit mode: a shipped one, one the first
  iteration wrote, one downloaded, or one made by hand.

The Launcher's server reads the file, as `--topology` reads one, through `POST /api/topology/file`,
which answers its modules and a cell for each; the page reads nothing of the file but its name. A
file that holds no module list, or one that cannot be seeded, is refused in the page with the
reason, and so is one with a connection to a module that the layout does not put next to it; the
editor keeps what it held.

A topology that fails the load check opens, with the check's reason under the grid, and **Launch**
stays off until it passes: **Empty grid**, and a file with an empty module list, open as an
empty grid that fails with "a topology needs at least one module" until a module is added. A
running topology opened from a file is called by the file's name without `.json`:
`running: my-loop (edited)`. A file keeps every type, label and port the editor holds. It carries
no cells, so it opens where the layout puts its modules: a topology whose modules are all connected
comes back as the same grid, and a part that is not connected to the first module comes back laid
out whole on the rows below the others, from the left edge, which need not be where it was.

The Launcher's server also takes a topology sent whole, in the format of the shipped ones:
`POST /api/topology/check` answers whether it passes the load check and why not, and
`POST /api/topology/launch` checks it again and launches it only if it passes, under the same
guards as any launch.

## The shipped topologies

- `cross` is five modules, the first iteration's, with their connections. Module 1 and Module 2
  enter, Module 4 and Module 5 exit, and Module 3, in the middle, has no role, so that every box
  starts on Module 1 or 2, passes Module 3 and ends on Module 4 or 5. It is the default of
  `jms-uc2`.
- `pair` is two modules, one above the other: Module 1 enters, Module 2 exits.
- `tee` is four modules, three in a column and one to the East of the middle one. Module 1, at the
  top, enters, and the fork below it exits on Module 3 and on Module 4.
- `grid` is thirty modules on a grid of six columns and five rows. It enters on its top row and
  exits on its bottom row. Every column runs down. The top row passes boxes only downwards, so that
  no box arrives on an entry module from a neighbour, and the rows below run West and East by
  turns, starting West on row 2.
- `line` is four modules one after another, each connected East to the next: Module 1 enters and
  Module 4 exits, so that every box passes Module 2 and Module 3. `jms-uc2 trace-box` runs on it.
- `merge-fault-demo` is for fault injection: a merge of two streams, where `skip-reserve` on
  Module5 collides with the box that waits on Module6. Module1 and Module2 enter, Module4 exits,
  and Module3, Module5 and Module6 have no role. Module1 connects South to Module5, Module5 South
  to Module6, Module6 East to Module4; Module2 South to Module3, Module3 South to Module4. Arm
  `skip-reserve` on Module5 and spawn boxes from Module1 and from Module2 toward Module4.

`pair`, `tee` and `grid` have the shapes of the first iteration's samples, each link running one
way, from the entry modules toward the exit modules. The first iteration's samples and seed made
every link two-way, and on those two-way samples two boxes could each wait for the other's module
forever. Loading refuses the two-way grid, whose corner modules are pure
two-way corridors, and loads the two-way tee and pair, which have none.
