# Past possessions: the triple store's history

<!-- vendor-check: off -- the possession-history exception: the store's history plugin -->
A handover overwrites the box's link to its possessor's possession state, so the knowledge graph
holds only the present possessor: the history of possessions is kept by the triple store's own
history mechanism, not in the knowledge graph. GraphDB's history plugin ("data history and
versioning") records every transactional change at triple granularity, with the time of its
transaction, and answers a query for the data as it was at one point in time. It is off in a new
dataset, and use case 2 runs without it.

## Launch with the option

**Launch with the option to read past possessions**: `jms-uc2 --collect-history`, or the Launcher
page's checkbox **collect history**, among the run settings. It is off by default.
`jms-uc2 --collect-history` makes the first launch with it, and the checkbox starts at whether the
launch that runs was launched with it: on where `jms-uc2 --collect-history` started the page, and,
after a launch from the page, as that launch had it.

A launch with it has its reset turn on the triple store's history collection, with no filter, as
the reset's last step, after it loaded the ontology; from then on the triple store records every
change of the dataset. A launch without it never turns the collection on, and never off either: a
dataset in which an earlier launch turned it on goes on collecting, until a person turns it off by
hand (below). The page shows "collects history" beside the running topology's name while the
launch that runs was launched with the option. The Launcher's server takes the option as
`collect_history`, in the body of `POST /api/launch` and as a query parameter of
`POST /api/topology/launch`, off if it is left out, and `/api/state` names it too: whether the
launch that runs was launched with it, or `null` before the first launch.

With the collection on, GraphDB refuses only a clear of the
whole dataset, which the reset never sends: its clears of the default graph and of each graph it
loads by name work (measured on GraphDB 10.8.5), and so does its setting of the shapes, which
replaces them in one request. Nor does the Launcher's **Clear** send one: it clears each graph of
the dataset on its own. **The collection is never switched off around a clear**: the clear would
then not be recorded, and the cleared data would disappear from every earlier point in time.

## Read a past possession

Use a query for one point in time, in GraphDB's Workbench on the run's dataset. Who possessed the
box `Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10` at 14:30:00 on 2026-10-10? A box the WMS spawned is
named by its UUID, as its row on the monitor shows it.

```sparql
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
PREFIX fci: <http://w3id.org/circularfactory/FlexConveyorInstances#>
SELECT ?possessor
FROM <http://www.ontotext.com/at/20261010143000>
WHERE {
  fci:Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10 cfc:hasPossessedWorkpiece ?state .
  ?possessor cfc:hasPossessor ?state .
}
```

- **The point in time** is the graph after `FROM`: `http://www.ontotext.com/at/` followed by the
  time as `yyyyMMddHHmmss`, which may stop early (`20261010` is midnight). It is the **server's
  local time**, which `SELECT (NOW() AS ?now) WHERE {}` answers, with the server's offset from UTC.
  The transaction IRIs of the history, with which the plugin names each change, are in **UTC**: a
  time read from one goes into the `FROM` converted to the server's local time.
- **The query names its subject**, here the box, so that it returns the data of that time. The
  second pattern needs no point in time, because a resource's link to its own possession state
  never changes.
- `jms_kapps.uc2.history` writes this query with full IRIs (`possessor_query`), and its live test
  reads a handover back with it.

## Read every change of one box: the change list

The plugin's **change list** gives every change of one subject's predicate, each with its
transaction and its timestamp: one row for each triple a transaction inserted or deleted. Every
change of the box's possession, from the spawn to the give-back:

```sparql
PREFIX cfc: <http://w3id.org/circularfactory/Core#>
PREFIX fci: <http://w3id.org/circularfactory/FlexConveyorInstances#>
PREFIX hist: <http://www.ontotext.com/at/>
SELECT ?transaction ?timestamp ?object ?insert WHERE {
  [] hist:listWithFilter (?from ?to fci:Box-5b0e1c52-9d37-4c8e-a1f4-2f6c3a7d9e10
                          cfc:hasPossessedWorkpiece ?filtered ?context) ;
     hist:transactionId ?transaction ;
     hist:timestamp ?timestamp ;
     hist:graph ?graph ;
     hist:subject ?subject ;
     hist:predicate ?predicate ;
     hist:object ?object ;
     hist:insert ?insert .
}
```

- `?object` is the possession state of the box's possessor, `?insert` says whether the
  transaction set it or removed it, and `?timestamp` carries the server's offset from UTC.
- A point in time is given to the second, so a query for one can miss a possession shorter than a
  second; the change list holds each change.
- `jms_kapps.uc2.history` writes this query (`change_list_query`) and reads it (`changes`).
  [`jms-uc2 trace-box`](../user-guide.md#read-every-change-of-a-boxs-possession-back-from-the-triple-stores-history)
  reads the change lists of a box's possession, its transport's status and its transport's
  assignment, and prints them, one row for each transaction.

## What it costs, and how to trim it

**What it costs**, as measured on GraphDB 10.8.5: about 204 history rows for each steady reset of
use case 2, 2 rows for each triple a run writes, and rows for the statements that inference adds
too. The history grows with every launch and every box, and nothing trims it.

**Trim the history by hand**, by time, by size or by period, with one of the plugin's updates, in
the Workbench on the run's dataset. Nothing in this package trims it automatically.

```sparql
PREFIX hist: <http://www.ontotext.com/at/>
INSERT DATA { [] hist:trimBefore "2026-10-01T00:00:00" }
```

drops the history before that time, the server's local time; `hist:trimToSize 100000` in its place
keeps the newest 100000 rows, and `hist:trimToPeriod "P7D"` keeps the last 7 days.
`[] hist:clearHistory []` deletes the whole history and keeps the data. To stop collecting, send
`INSERT DATA { [] <http://www.ontotext.com/at/enabled> false }`: the history collected so far
stays readable, but a clear while the collection is off is not recorded.

## The one vendor feature

The history plugin is GraphDB's own, outside the contract of the triple store access module, and
these queries run on GraphDB alone. The reset turns it on directly, because the access module has
no call for it: it is the one triple store feature that the use cases use outside the access
module. In the source, `jms_kapps.uc2.history` alone names it, and a test checks that. The plugin's
own transaction IDs are used: they work on GraphDB 10.8.5, and 10.8.6
fixed major issues of the plugin with transaction IDs.
<!-- vendor-check: on -->
