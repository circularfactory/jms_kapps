# Tests

```bash
uv run pytest                                   # without connection variables: the offline tests
uv run pytest --dataset <name>                  # with them set: also the tests on that dataset
uv run pytest -m paper_claim --dataset <name>   # the tests that show what the paper shows
uv run pytest -m "not live"                     # the offline tests only, whatever is set
```

The marker `paper_claim` groups the tests that show what the paper shows; the
[user guide](../user-guide.md) runs them by the marker.

## The markers

- **`graphdb`**: the tests need the connection variables of [The triple store](triple-store.md),
  and are skipped without them. **They clear the dataset they run against**, which the run names
  with `uv run pytest --dataset <name>`, for example `--dataset Tests`. There is no default. A run with the connection variables set that selects
  such a test and names no dataset stops before any test runs, with a message that says so;
  `-m "not graphdb"` selects none and needs no dataset. A run without those variables is still
  green with no dataset named, and says so only in its skip count. If every `graphdb` test is
  skipped, the live tier did not run and nothing about the knowledge graph was shown.
- **`influxdb`**: the tests need an InfluxDB, and are skipped when none answers. They write their
  frames into one database, `Tests`, whatever dataset they run on. The local InfluxDB holds at
  most five databases and refuses a sixth. Each test reads only its own frame, by its id and time window.
- **`live`**: every `graphdb` and `influxdb` test carries it too, so that `-m "not live"` selects
  the offline tier.
- **`posix`**: the tests need what only POSIX has, such as SIGHUP, a signal to a process group, a
  stop that a process handles itself as SIGTERM, or a POSIX shell, and are skipped on Windows; each
  marker says why. **`windows`**: the tests need what only Windows has, a CTRL_BREAK, cmd.exe or
  PowerShell, and are skipped elsewhere. Each choice that differs between the two platforms is made
  in one module per platform, and the tests of the Windows module's choices run on POSIX too.
- **`browser`**: the tests run the JavaScript of the Launcher page and of the monitor page in a
  headless browser, through pytest-playwright (below).

## What runs without a triple store

The tests check the imports, the pins, the console scripts, the assets, and that no frozen module
is named anywhere in the repository. A deprecated name of the triple store access module or of the
semantic middleware fails the test that uses it, and a test checks that the package names no triple
store vendor and calls the triple store's unit a dataset. A test checks that the README and these
pages carry no dated notes, and that their links lead somewhere.

The UC2 handshake, the topologies, the process supervision, the run core, the WMS, a module's
memory and its dashboard boxes, and the Launcher's and the monitor's APIs are tested without a
network or a triple store. The query of the Launcher's list of the boxes on the station also runs,
without a triple store, on rdflib's SPARQL engine.

A test reads the source and finds no write outside the write path through the middleware, apart
from the seed, and no import of the middleware's private `registration` module. It counts a SPARQL
update as a write, and counts the reset, the Launcher's Clear and the switch of the triple store's
history as setup, as the seed is.

The UC1 ontology, the effective values of a Screw, the decision tree, the learner's rule and its
dashboard box are tested without a triple store too. `tests/topologies/` holds the first
iteration's cross, copied unchanged, and, for each shipped topology, the modules and connections
that its seed is expected to write; the topology tests compare with both. The values that the UC1
tests seed are in `tests/_uc1_example.py`, with the source of each; the
[user guide](../user-guide.md) states what in use case 1 is example data.

The strategies themselves, their discovery from a fake set of entry points, two test strategies
that the tests alone register, run on the grid without a triple store, the Launcher's two pickers,
and the command lines the run core builds with the two strategies a caller names are tested without
a triple store; a test reads the source and finds no import of the `evaluation` folder in use case 1
or use case 2.

## The live tests of use case 2

The live tests of a box's journey serve the WMS and the modules as middleware instances in the
test's own process. They move boxes with a transit time of 0, except the tests of the two faults on
the cross, whose moves take 1 s.

- `tests/test_uc2_running_system.py` runs the real `jms-uc2` on the cross, spawns boxes over HTTP
  as a person does from the WMS's dashboard, arms both faults over HTTP as a person does from the
  Launcher page, reads both refusals from the monitor, and stops the run with Ctrl+C. It takes
  about a minute, and needs the Launcher's port 8090 free. Its run of the strategies launches the
  grid from the Launcher, as the page does, with `real` for both strategies and every transit time
  0, sets the spawn rate to 30 boxes a minute for 2 minutes, then to 0, and finds, within 5
  minutes, every spawned box arrived or its transport ended `failed` with a reason, every process
  on the dataset the run names with its strategy on its command line, and the state naming `real`
  for both.
- `tests/test_uc2_monitor_modes_live.py` runs the real `jms-uc2` on the cross too, and opens the
  monitor's page through the Launcher in a headless browser: it checks that the monitor starts in
  graph only, turns the switch at Refusals on (graph + streams), then off (graph only), with a refusal shown and a
  box on its way, sets the interval of the knowledge graph read, switches back, and saves a
  screenshot of the page in each mode in its temporary directory, whose path it prints.

### On a disposable dataset only

These tests refuse any dataset whose name does not start with `ogm-disposable-`, `Tests` included,
with a skip that says why. Such a dataset is created for them from
`graphdb/jms-kapps-repo-config.ttl` with only its ID changed, and deleted afterwards.

- **The history collection**, `tests/test_uc2_history_live.py`. A reset with the option turns the
  dataset's history collection on, and the tests need it off when they start and leave it on, so
  each run needs a dataset created afresh. They show that a reset turns the collection on only with
  the option and never turns it off, and read a box handed from one module to another back in the
  first module's possession, with the query of [Past possessions](past-possessions.md). The option
  itself, the switch the reset sends after it loaded the ontology, the checkbox and the launch
  fields are tested without a triple store.
- **The Launcher's Clear**, `tests/test_uc2_launcher_clear_live.py`, since Clear empties every
  graph, not only what a launch seeds. It starts `jms-uc2 --collect-history` on the cross, loads a
  named graph of its own, presses Clear through the page's API, reads 0 statements and no named
  graph from the triple store, and launches the cross again; it needs the Launcher's port 8090
  free. The order of the stop and the clear, each graph cleared on its own over a stand-in of the
  triple store that holds a named graph no run made, the guard, a graph the triple store refuses to
  clear, and the page's question and answer in a headless browser are tested without a triple
  store.

## The live test of the store's conclusions

`tests/test_store_conclusions_live.py` loads a small ontology of its own (one property with a
domain and a range, one subclass axiom, one inverse pair) and asserts that the store materializes
the subclass conclusion and the inverse conclusion. It prints whether the store also typed an
individual from the range and from the domain, and asserts nothing about either. It clears the
dataset it runs against.

## The browser tests

The tests marked `browser` check what a person sees and clicks on the two pages. No test reaches a
triple store.

The Launcher page is served by the Launcher's own routes, over a stand-in of the knowledge graph
that calls every process live, children that only sleep, and fault switches that record what they
are sent. The editor's tests take a stand-in that calls no process live, so that a launch from the
page goes on, and read what the editor holds from each module list it sends to the load check; they
save the files its Download writes and choose the files its Upload opens. The tests of the topology
section take the same stand-in: they read the grid that draws the chosen topology, accept or dismiss
the discard question, and launch an edit to find the picker's entry for it. The tests of the page's
list of the boxes on the station take a Launcher that launches nothing, whose background read sends
the list's query to a stand-in of the triple store that answers fixed rows and keeps each query text
it receives, and compare what the page shows and copies with what the stand-in received. The tests
of a launch from a page that stays open take a stand-in that calls the first topology's processes
live and then none, as when they have deregistered, so that the new processes stay `starting`; one
of them changes the Launcher's answers on their way to the page, so that a `starting` process cannot
be drawn.

The monitor page is served by the monitor's routes. For the heads of its wires, its state route is
a fake that answers one state; for its switch, the interval of its knowledge graph read, "Queries
sent" and each box's destination, its routes stand over a real memory that reads fakes of the
knowledge graph and the streams.

They run in Playwright's Chromium, which `uv run playwright install chromium` downloads, or else in
the Google Chrome installed on the machine, and they are skipped where neither is installed.
pytest-playwright's options choose another browser: `--browser`, `--browser-channel`, `--headed`.

## The live tests of use case 1

The live tests of the screwing resource serve it and a fake anomaly detector as middleware
instances in the test's own process, and issue each unscrewing operation with
`mw.issue_operation()`, as the planner does. The fake detector realizes the detector's Classify
capability, keeps each call, answers with a fixed detection result and writes nothing. Some tests
serve no detector, or make the fake refuse the call, answer too late, or end the operation itself,
to show when the screwing resource ends an operation `failed`.

The live tests of the anomaly detector serve the real one beside the screwing resource, the same
way. These are what they show:

- An issued attempt ends `done`, with its outcome and its parameter copy.
- A threshold that changes between two attempts is used on the next.
- A Screw without placement values, and a frame that InfluxDB lacks, end the
  operation `failed` with why, and nothing else is written.
- An operation that is not `running` is left as it is. So is one that the screwing resource ended
  while the detector was still classifying.

The detector's commits, its refusals and its guard are also tested without a triple store. They run
on the middleware's own commit, over a stand-in of the Object–Graph Mapper (OGM).
