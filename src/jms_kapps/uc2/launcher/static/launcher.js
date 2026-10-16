// The Launcher page. Everything it shows comes from the backend's JSON API; it renders no HTML
// it was sent, and it builds every element with textContent, so a process's output stays text.
"use strict";

const POLL_MS = 1000;
// A poll not answered by then has failed, and the page says so rather than wait on it.
const POLL_TIMEOUT_MS = 5000;

// The directions a module may connect in, as the backend names them. Read once, at the start.
let directions = [];

// Which processes have their output open, by resource IRI; kept across re-renders.
const openOutputs = new Set();

// What each of a module's faults makes it do, for a person who hovers over its switch. The
// switches themselves are the ones the backend lists for the module.
const FAULT_TITLES = {
  "skip-reserve": "Hand boxes on without asking the receiving module's Reserve first",
  "complete-without-releasing":
    "As a box's destination, commit its transport done before giving the box back",
};

// What the Launcher last answered and when, and why the latest poll failed, if it did.
let lastState = null;
let answeredAt = null;
let pollError = null;
let polling = false;

// -- Small helpers --------------------------------------------------------------------------- //

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

// Every path the page asks for, and every link it shows, is relative: the page works at whatever
// address it was opened, and each process's page opens through the Launcher's own port, under
// `p/<id>/`, which the backend's links name. A file goes as its bytes, and any other body as JSON.
async function call(method, path, body, signal) {
  const asBytes = body instanceof Blob;
  const response = await fetch(path, {
    method,
    signal,
    headers:
      body === undefined ? {} : { "Content-Type": asBytes ? "application/octet-stream" : "application/json" },
    body: body === undefined || asBytes ? body : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  // The backend refuses a command with one string. The page sends as JSON only bodies it builds
  // itself, and a file a person chose goes to a route that takes its bytes as they are, so the
  // page never meets FastAPI's list of the fields a body got wrong.
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

// The outcome of the latest command the page sent: a launch, a stop, or a fault switch.
function say(text, kind = "info") {
  const message = document.getElementById("command-message");
  message.hidden = !text;
  message.className = `message ${kind}`;
  message.textContent = text;
}

// -- Processes ------------------------------------------------------------------------------- //

// What the processes table was last built for: which processes, in which order, and whose output
// is open. A poll that changes none of it fills the rows already there, so that a link, a switch
// or a button stays the element it was while a person clicks it: a click whose press and release
// fall on either side of a rebuild is lost.
let builtFor = null;
// The rows built for each process, by resource: its own, and its output's while that is open, each
// with its cells by name.
const builtRows = new Map();

// What each cell was last drawn from, so that it is drawn again only when that changes.
const drawnFrom = new WeakMap();

// -- The parts of the page ------------------------------------------------------------------- //

// Each part of the page that the Launcher's state is drawn into, by the name the page calls it by
// when it cannot draw it. Each is drawn on its own: an error while drawing one part leaves the
// others drawn from the same state, and the page shows that error, under the status banner,
// until the part draws again. So a person sees in the page which part is not current, and why,
// rather than a page that has stopped drawing with the reason in the console only. (Added on
// 2026-10-10, jms_kapps 6, decided by Etienne: "Each part of the page (the processes, the status,
// the topology) is drawn on its own. An error while drawing one part does not stop the others,
// and the page reports such an error readably.")
const STATUS = "the status banner";
const PARTS = [
  ["the header", renderHeader],
  ["the processes", (state) => renderProcesses(state.processes)],
  [STATUS, renderStatus],
  ["the boxes on the station", (state) => renderBoxes(state.boxes)],
  ["the fields of the next launch", startLaunchFields],
  ["the topology", renderTopology],
];

// Why each part could not be drawn, by its name, while it cannot.
const drawErrors = new Map();

function renderState(state) {
  lastState = state;
  for (const [part, draw] of PARTS) drawPart(part, () => draw(state));
  renderDrawErrors();
}

// Draws one part with `draw`, and keeps why it failed, if it did, for `renderDrawErrors`.
function drawPart(part, draw) {
  try {
    draw();
    drawErrors.delete(part);
  } catch (error) {
    drawErrors.set(part, error instanceof Error ? error.message : String(error));
    console.error(`The page could not draw ${part}:`, error);
  }
}

function renderDrawErrors() {
  const shown = document.getElementById("draw-errors");
  const lines = [...drawErrors].map(
    ([part, why]) => `The page could not draw ${part} from the Launcher's latest answer: ${why}`
  );
  shown.hidden = lines.length === 0;
  setProperty(shown, "textContent", lines.join("\n"));
}

// Where the Launcher runs, and the links to the monitor and to the WMS's dashboard.
function renderHeader(state) {
  document.getElementById("where").textContent =
    `dataset ${state.dataset}` + (state.store_url ? ` on ${state.store_url}` : "");

  const monitorLink = document.getElementById("monitor-link");
  monitorLink.hidden = !state.monitor;
  if (state.monitor) monitorLink.href = state.monitor;

  // The WMS's dashboard, where a person spawns boxes.
  const wmsLink = document.getElementById("wms-link");
  wmsLink.hidden = !state.wms;
  if (state.wms) wmsLink.href = state.wms;
}

function renderProcesses(processes) {
  const layout = JSON.stringify(processes.map((p) => [p.resource, openOutputs.has(p.resource)]));
  if (layout !== builtFor) {
    builtRows.clear();
    const body = document.querySelector("#processes tbody");
    body.replaceChildren();
    if (processes.length === 0) {
      body.append(el("tr", {}, el("td", { colspan: 6, class: "muted", text: "Nothing runs. Launch a topology below." })));
    }
    for (const process of processes) {
      const rows = { process: processRow(process.resource), output: null };
      body.append(rows.process.row);
      if (openOutputs.has(process.resource)) body.append((rows.output = outputRow()).row);
      builtRows.set(process.resource, rows);
    }
    // Only once every row is built: a build cut short is built again at the next draw.
    builtFor = layout;
  }
  for (const process of processes) {
    const rows = builtRows.get(process.resource);
    fillProcessRow(rows.process, process);
    if (rows.output) fillOutputRow(rows.output, process);
  }
}

// What the page cannot vouch for: first the Launcher's own silence, then the states the Launcher
// could not confirm against the graph. Nothing, while both are current.
function renderStatus() {
  let text = "";
  if (pollError !== null) {
    text = answeredAt
      ? `The Launcher has not answered since ${answeredAt.toLocaleTimeString()} (${pollError}). ` +
        "The page shows what it said then."
      : `The Launcher does not answer (${pollError}).`;
  } else if (lastState) {
    const count = lastState.processes.filter(isUnconfirmed).length;
    if (count > 0) {
      const why = lastState.heartbeat_error
        ? `it cannot read the graph: ${lastState.heartbeat_error}`
        : "no read of the graph has succeeded lately";
      text =
        `The Launcher cannot confirm ${count === 1 ? "one state" : `${count} states`}, because ${why}. ` +
        "Each is shown as last read, with when it was last confirmed.";
    }
  }
  const status = document.getElementById("status");
  status.hidden = !text;
  status.textContent = text;
  document.body.classList.toggle("unanswered", pollError !== null);
}

function isUnconfirmed(process) {
  return typeof process.unconfirmed_for === "number";
}

// Sets `node[key]` to `value`, unless it holds that already.
function setProperty(node, key, value) {
  if (node[key] !== value) node[key] = value;
}

// Draws `cell`'s children with `draw`, unless they were drawn from the same `basis` already. A
// `draw` that throws leaves the cell to be drawn again next time.
function drawWhenChanged(cell, basis, draw) {
  const key = JSON.stringify(basis);
  if (drawnFrom.get(cell) === key) return;
  cell.replaceChildren(...[].concat(draw()).filter(Boolean));
  drawnFrom.set(cell, key);
}

// The row of the process `resource`, with its cells by name and empty: `fillProcessRow` fills them.
function processRow(resource) {
  const cells = {
    name: el("button", {
      type: "button",
      class: "link",
      onclick: () => {
        if (openOutputs.has(resource)) openOutputs.delete(resource);
        else openOutputs.add(resource);
        renderState(lastState);
      },
    }),
    state: el("td"),
    pid: el("td", { class: "mono" }),
    links: el("td", { class: "links" }),
    faults: el("td", { class: "faults" }),
    stop: el("td"),
  };
  // The columns of the table, in their order.
  cells.row = el("tr", {}, [
    el("td", {}, cells.name),
    cells.state,
    cells.pid,
    cells.links,
    cells.faults,
    cells.stop,
  ]);
  return cells;
}

// Shows `process` in its row. Each element stays the one it was while what it shows stays the
// same; a fault switch stays while the module has it, and only turns on or off.
function fillProcessRow(cells, process) {
  const unconfirmed = isUnconfirmed(process);
  setProperty(cells.row, "className", unconfirmed ? `${process.state} unconfirmed` : process.state);

  setProperty(cells.name, "title", process.command);
  setProperty(cells.name, "textContent", process.name);

  setProperty(cells.state, "title", process.state_note || "");
  const confirmedAgo = unconfirmed ? Math.round(process.unconfirmed_for) : null;
  drawWhenChanged(cells.state, [process.state, confirmedAgo], () => [
    el("span", { class: "dot" }),
    process.state,
    unconfirmed ? el("span", { class: "muted", text: ` · last confirmed ${confirmedAgo} s ago` }) : null,
  ]);

  setProperty(cells.pid, "textContent", String(process.pid));

  const links = process.links.map((link) => [link.href, link.label]);
  drawWhenChanged(cells.links, links, () =>
    links.length
      ? links.map(([href, label]) => el("a", { href, target: "_blank", text: label }))
      : el("span", { class: "muted", text: "—" })
  );

  // A module's faults, one switch each, as the Launcher commanded them. The others have none.
  const faults = Object.entries(process.faults || {});
  drawWhenChanged(cells.faults, [faults.map(([fault]) => fault), process.fault_switchable], () =>
    faults.map(([fault]) => faultSwitch(process, fault))
  );
  faults.forEach(([, armed], i) => {
    const label = cells.faults.children[i];
    setProperty(label, "className", armed ? "switch armed" : "switch");
    setProperty(label.querySelector("input"), "checked", armed);
  });

  // The backend says whether a stop still has a process to ask. One that has ended, stopped or
  // crashed alike, gets no button, rather than one that does nothing.
  drawWhenChanged(cells.stop, process.stoppable, () =>
    process.stoppable
      ? el("button", { type: "button", text: "stop", onclick: () => stopOne(process.resource) })
      : null
  );
}

// The switch of the fault `name` of the module `process`, off: `fillProcessRow` turns it on.
function faultSwitch(process, name) {
  return el("label", { class: "switch", title: FAULT_TITLES[name] }, [
    el("input", {
      type: "checkbox",
      disabled: !process.fault_switchable,
      onchange: (event) => setFault(process.resource, name, event.target.checked),
    }),
    ` ${name}`,
  ]);
}

// The row of a process's output, with its parts by name and empty: `fillOutputRow` fills it.
function outputRow() {
  const parts = { command: el("div", { class: "muted mono" }), output: el("pre") };
  parts.row = el("tr", { class: "output" }, el("td", { colspan: 6 }, [parts.command, parts.output]));
  return parts;
}

function fillOutputRow(parts, process) {
  setProperty(parts.command, "textContent", process.command);
  setProperty(parts.output, "textContent", process.output.length ? process.output.join("\n") : "(no output yet)");
}

async function stopOne(resource) {
  try {
    renderState(await call("POST", "api/processes/stop", { resource }));
  } catch (error) {
    say(error.message, "error");
  }
}

async function stopAll() {
  try {
    renderState(await call("POST", "api/processes/stop-all"));
  } catch (error) {
    say(error.message, "error");
  }
}

async function setFault(module, name, armed) {
  try {
    renderState(await call("POST", "api/modules/faults", { module, name, armed }));
  } catch (error) {
    say(error.message, "error");
    // The Launcher keeps what it held, so the switch shows that again.
    if (lastState) renderState(lastState);
  }
}

// -- The boxes on the station ------------------------------------------------------------------ //

// Every box whose possessor is not the WMS, with the module that possesses it, as the Launcher's
// background read of its one SPARQL query last found them, and that query's text, exactly as the
// Launcher sent it, for a person to copy and run on the same dataset. The page sends no query of
// its own: it shows what the state answers. A Launcher that lists no boxes answers none, and the
// section stays hidden. (Added on 2026-10-10, jms_kapps 23, decided by Etienne on 2026-10-05.)

function renderBoxes(boxes) {
  const section = document.getElementById("station");
  section.hidden = !boxes;
  if (!boxes) return;

  const read = boxes.read_at !== null;
  drawWhenChanged(document.querySelector("#boxes tbody"), [boxes.boxes, read], () =>
    boxes.boxes.length
      ? boxes.boxes.map((box) =>
          el("tr", {}, [
            el("td", { class: "mono", text: box.box }),
            el("td", { class: "mono", text: box.module }),
            el("td", { class: "mono", text: box.transport || "—" }),
            el("td", { text: box.status || "—" }),
          ])
        )
      : el("tr", {}, el("td", { colspan: 4, class: "muted", text: read ? "No box is on the station." : "Not read yet." }))
  );

  // When the list was read, and, where the latest read failed, why, in the page where a person
  // reads the list: the list is then the one read last.
  const when = read ? new Date(boxes.read_at).toLocaleTimeString() : null;
  const count = boxes.boxes.length;
  let text = read ? `Read at ${when}: ${count} ${count === 1 ? "box" : "boxes"}.` : "";
  if (boxes.error !== null) {
    text = `The latest read failed: ${boxes.error}.` + (read ? ` The list is the one read at ${when}.` : "");
  }
  const said = document.getElementById("boxes-read");
  setProperty(said, "className", boxes.error !== null ? "message error" : "muted");
  setProperty(said, "textContent", text);

  // Set only when it changes, so that a text a person selects stays selected.
  setProperty(document.getElementById("boxes-query"), "textContent", boxes.query || "");
  setProperty(document.getElementById("copy-query"), "disabled", !boxes.query);
}

// What the copy button says, until it says what became of a copy.
const COPY_QUERY = "Copy the query";

// Copies the query's text as the page shows it. Where the page is no secure context the browser
// offers no clipboard: then the text is selected, for a person to copy as they copy a selection.
async function copyQuery() {
  const text = document.getElementById("boxes-query");
  const button = document.getElementById("copy-query");
  let copied;
  try {
    await navigator.clipboard.writeText(text.textContent);
    copied = true;
  } catch {
    const range = document.createRange();
    range.selectNodeContents(text);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    copied = document.execCommand("copy");
  }
  button.textContent = copied ? "Copied" : "Selected: press Ctrl+C";
  setTimeout(() => { button.textContent = COPY_QUERY; }, 2000);
}

// -- The topology -------------------------------------------------------------------------- //

// The picker's launch names one of the shipped topologies; the editor's sends the whole topology
// it holds, a module list, to `api/topology/launch`, which checks it again before it clears
// anything. A topology file of one's own opens in the editor, and the server reads it (see
// "Topology files" below), or it goes in at the command line, `jms-uc2 --topology <path>`. The
// state shows the running topology in the page's own terms: each module by its local name, its
// label if it has one, its types, and its connections, and its name as the server gives it, which
// for a topology edited on the page names the one its editor started from, such as
// `cross (edited)`.

// The fields of the next launch start, each once, at what the launch in force was launched with:
// after that, a person's own values stay.
function startLaunchFields(state) {
  // The transit time field starts at `jms-uc2 --transit-time`.
  const field = document.getElementById("transit-time");
  if (field.value === "") field.value = state.default_transit_time;
  startStrategyPickers(state);
  startHistoryCheckbox(state);
  startDeadlockAlarmField(state);
}

// The running topology: its name, its strategies and whether it collects history, and its table.
// Every row is built before anything is shown, so that the name and the table are of one topology.
function renderTopology(state) {
  const modules = state.topology ? state.topology.modules : [];
  const rows = modules.map((module) => {
    const connections = module.connections || [];
    return el("tr", {}, [
      el("td", { class: "mono", text: module.id }),
      // A module without a label is called by its local name, as on every page.
      el("td", module.label ? { text: module.label } : { class: "muted", text: module.id }),
      el("td", { text: module.types.join(", ") }),
      ...directions.map((direction) => {
        const connection = connections.find((c) => c.direction === direction);
        return el("td", { class: "muted", text: connection ? connection.to : "—" });
      }),
    ]);
  });
  const strategies =
    state.module_strategy && state.wms_strategy
      ? `Module strategy: ${strategyLabel("module", state.module_strategy)} · ` +
        `WMS strategy: ${strategyLabel("wms", state.wms_strategy)}`
      : "";

  document.getElementById("running").textContent = state.topology_name
    ? `running: ${state.topology_name}`
    : "";
  document.getElementById("strategies").textContent = strategies;
  document.getElementById("history").textContent = state.collect_history ? "collects history" : "";
  document.querySelector("#modules tbody").replaceChildren(
    ...(rows.length
      ? rows
      : [el("tr", {}, el("td", { colspan: 3 + directions.length, class: "muted", text: "Nothing is seeded." }))])
  );
}

// Shows the shipped topology `name` in the picker, if it is one.
function pick(name) {
  const picker = document.getElementById("shipped");
  if ([...picker.options].some((option) => option.value === name)) picker.value = name;
}

// Whether a launch is under way: neither launch button may start another one then.
let launching = false;

// The one confirmation of every launch, which clears the dataset, and the launch itself:
// `what` says to a person what is launched, and `body` goes to `path`. Whether it launched.
async function launchAfterAsking(what, path, body) {
  const where = document.getElementById("where").textContent;
  if (!confirm(`Launching ${what} clears ${where} and restarts every process. Go on?`)) return false;
  launching = true;
  renderLaunchButtons();
  say(`Launching ${what}: stopping every process, seeding, starting\u2026`);
  try {
    renderState(await call("POST", path, body));
    say("Launched. The processes turn live as their heartbeats arrive.", "ok");
    return true;
  } catch (error) {
    say(`Not launched: ${error.message}`, "error");
    return false;
  } finally {
    launching = false;
    renderLaunchButtons();
  }
}

function renderLaunchButtons() {
  // No launch while a clear is under way, nor a clear while a launch is (see "Clear").
  document.getElementById("launch").disabled = launching || clearing;
  // The editor's launch only while the server's latest check of what it holds passes.
  document.getElementById("launch-edited").disabled =
    launching || clearing || !(editor.check && editor.check.passes);
  document.getElementById("clear").disabled = launching || clearing;
}

// The transit time field's value as a number of seconds, or null, with the reason on the page,
// where it is no number or is negative. The server checks it again.
function transitTime() {
  const field = document.getElementById("transit-time");
  const text = field.value.trim();
  const seconds = text === "" ? NaN : Number(text);
  if (!Number.isFinite(seconds) || seconds < 0) {
    say(`Not launched: the transit time must be a number of seconds, 0 or more, not "${text}".`, "error");
    field.focus();
    return null;
  }
  return seconds;
}

async function launch() {
  const name = document.getElementById("shipped").value;
  const seconds = transitTime();
  if (seconds === null) return;
  const alarm = deadlockAlarmSeconds();
  if (alarm === null) return;
  await launchAfterAsking(`"${name}"`, "api/launch", {
    name,
    transit_time: seconds,
    ...chosenStrategies(),
    collect_history: collectHistory(),
    deadlock_alarm_seconds: alarm,
  });
}

// -- The deadlock alarm time ------------------------------------------------------------------- //

// How long a box a module holds may wait for a Reserve before the module raises the deadlock
// alarm for it: the field "deadlock alarm (s)", which each launch sends as
// `deadlock_alarm_seconds`. (Added on 2026-10-11, jms_kapps 77, decided by Etienne.)

// Whether the field has started at its first value: after that, a person's value stays, an empty
// field too.
let deadlockAlarmStarted = false;

// The field starts, once, at `jms-uc2 --deadlock-alarm-seconds`, 300 s by default.
function startDeadlockAlarmField(state) {
  if (deadlockAlarmStarted) return;
  deadlockAlarmStarted = true;
  document.getElementById("deadlock-alarm").value = state.default_deadlock_alarm_seconds;
}

// The field's value as a number of seconds, or null, with the reason on the page, where it is no
// number or is not above 0. The server checks it again.
function deadlockAlarmSeconds() {
  const field = document.getElementById("deadlock-alarm");
  const text = field.value.trim();
  const seconds = text === "" ? NaN : Number(text);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    say(`Not launched: the deadlock alarm time must be a number of seconds above 0, not "${text}".`, "error");
    field.focus();
    return null;
  }
  return seconds;
}

// -- The strategies ---------------------------------------------------------------------------- //

// Who chooses where a box goes: the strategy of the modules and that of the WMS, each chosen by its
// name at a launch, `real` unless a person picks another. The two pickers list the strategies the
// Launcher found, by label (`api/strategies`); each launch sends the names chosen, and the state
// names the strategies in force, which the page shows beside the running topology's name. (Added
// on 2026-10-10, jms_kapps 52, decided by Etienne on 2026-10-07.)

// The strategies a launch may name, by kind: {module: [{name, label}], wms: [...]}. Read once, at
// the start.
let strategies = { module: [], wms: [] };

// The picker of each kind.
const STRATEGY_PICKERS = { module: "module-strategy", wms: "wms-strategy" };

// The name every launch takes unless it names another: the demonstration's behaviour.
const REAL = "real";

// Whether the pickers have started at their first values: after that, a person's choice stays.
let pickersStarted = false;

// The label of the strategy of `kind` named `name`, or the name where the Launcher lists none.
function strategyLabel(kind, name) {
  const strategy = strategies[kind].find((each) => each.name === name);
  return strategy ? strategy.label : name;
}

function fillStrategyPickers() {
  for (const [kind, id] of Object.entries(STRATEGY_PICKERS)) {
    document
      .getElementById(id)
      .replaceChildren(...strategies[kind].map(({ name, label }) => el("option", { value: name, text: label })));
  }
}

// The pickers start, once, at the strategies in force, the ones `jms-uc2 --module-strategy` and
// `--wms-strategy` launched with, or at `real` while none are in force.
function startStrategyPickers(state) {
  if (pickersStarted) return;
  pickersStarted = true;
  const inForce = { module: state.module_strategy, wms: state.wms_strategy };
  for (const [kind, id] of Object.entries(STRATEGY_PICKERS)) {
    const picker = document.getElementById(id);
    const name = inForce[kind] || REAL;
    if ([...picker.options].some((option) => option.value === name)) picker.value = name;
  }
}

// The names the pickers hold, as a launch sends them.
function chosenStrategies() {
  return {
    module_strategy: document.getElementById(STRATEGY_PICKERS.module).value,
    wms_strategy: document.getElementById(STRATEGY_PICKERS.wms).value,
  };
}

// -- The history ------------------------------------------------------------------------------ //

// Whether a launch turns on the triple store's history collection, so that past possessions can be
// read: the checkbox "collect history", which each launch sends as `collect_history`. A launch
// without it leaves the collection as it was, so the page says only that a launch collects, never
// that the store does not. (Added on 2026-10-10, jms_kapps 22, with the names Etienne decided on
// 2026-10-07.)

// Whether the checkbox has started at its first value: after that, a person's choice stays.
let historyStarted = false;

// The checkbox starts, once, at whether the launch in force collects history, the one
// `jms-uc2 --collect-history` launched, or off while none is in force.
function startHistoryCheckbox(state) {
  if (historyStarted) return;
  historyStarted = true;
  document.getElementById("collect-history").checked = state.collect_history === true;
}

// Whether the checkbox is on, as a launch sends it.
function collectHistory() {
  return document.getElementById("collect-history").checked;
}

// -- The topology editor --------------------------------------------------------------------- //

// The editor holds one topology while a person edits it: its modules, in the terms the state
// shows the running topology in, and the cell of each on a grid. It opens a topology only as the
// server lays it out (`api/topology/layout`, the one layout rule, which the monitor draws by),
// it writes the module list, the one topology format, with the names the server gives
// (`api/topology/format`), and the server's load check decides what may launch: the editor
// carries no reader of the format, no layout rule and no load check of its own.
// `editedModuleList()` is what it holds, and `openEditor()` takes a topology laid out.
//
// From there each module stays on its cell, and a module added stays on the cell clicked: the
// format carries no cells, and the layout rule puts a module that is not connected to the first
// one on a row below the others, so laying out again on every change would move a module away
// from where a person just put it. The rule gives back the editor's cells, up to a shift, for a
// topology whose modules are all connected, since every connection leads to the neighbouring cell
// in its direction; the monitor draws a part that is not connected on a row below.

// The names a module list is written with. Read once, at the start.
let moduleListNames = null;

// One step in each direction on the grid of the server's layout: x grows to the East, y to the
// South.
const STEPS = { North: [0, -1], East: [1, 0], South: [0, 1], West: [-1, 0] };
const OPPOSITE = { North: "South", East: "West", South: "North", West: "East" };

// The roles a module may have, in the order a module list gives them, and what the page calls them.
const ROLES = { EntryModule: "entry", ExitModule: "exit" };
// The class of every module, which a module list names for a module that has no role.
const MODULE_CLASS = "FlexConveyorModule";

// What the editor calls a topology that it started from no topology.
const EMPTY_GRID = "empty grid";

const editor = {
  // The topology the editor started from: a shipped one's name, EMPTY_GRID, or the name of an
  // uploaded file without `.json`. The server calls the running topology by it.
  base: null,
  // That topology, for a person to read: `"pair"`, `an empty grid`, `the file "loop.json"`.
  startedFrom: null,
  // Each module as {id, label, types, connections}, in the module list's order. `types` is
  // empty for a module that has no type but FlexConveyorModule and was not read with it listed.
  modules: [],
  // The cell of each module, by id, as [x, y]: the modules' leftmost column and top row are 0.
  cells: new Map(),
  // The id of the module selected, or null.
  selected: null,
  // How many checks were sent: only the latest one's answer is shown.
  checks: 0,
  // The latest check's answer, {passes, reason} or {error}, and null while it is under way.
  check: null,
};

// What the editor holds, as a module list.
function editedModuleList() {
  const names = moduleListNames;
  return editor.modules.map((module) => ({
    id: names.modules + module.id,
    [names.type]: (module.types.length ? module.types : [MODULE_CLASS]).map((type) => names.classes + type),
    ...(module.label ? { [names.label]: [module.label] } : {}),
    // A transit time the field holds as text is no number: sent as it is, the load check refuses it.
    ...(module.transit_seconds != null ? { [names.transit_time]: [module.transit_seconds] } : {}),
    [names.connection]: module.connections.map((connection) => ({
      [names.connects_to]: [names.modules + connection.to],
      [names.direction]: [names.classes + connection.direction],
      [names.port]: [connection.port],
    })),
  }));
}

// Opens the editor on `layout`, a topology as `api/topology/layout` answers it, started from
// `base`, which `startedFrom` names for a person to read. Throws if a connection does not lead to the
// neighbouring cell in its direction, since the grid can show no other, and the editor then holds
// what it held before.
function openEditor(base, layout, startedFrom = `"${base}"`) {
  const cells = new Map(Object.entries(layout.places));
  for (const module of layout.modules) {
    const [x, y] = cells.get(module.id);
    for (const connection of module.connections) {
      const [dx, dy] = STEPS[connection.direction];
      const neighbour = cells.get(connection.to);
      if (!neighbour || neighbour[0] !== x + dx || neighbour[1] !== y + dy) {
        throw new Error(
          `${module.id} connects ${connection.direction} to ${connection.to}, which the layout ` +
            "does not put next to it in that direction, so the grid cannot show it."
        );
      }
    }
  }
  editor.base = base;
  editor.startedFrom = startedFrom;
  editor.modules = layout.modules.map((module) => ({
    id: module.id,
    label: module.label,
    // The module's own transit time as read: a number of seconds, or null where it has none. The
    // selected module's transit time field changes it (see `parseTransitTime`).
    transit_seconds: module.transit_seconds,
    // As listed: the editor changes a module's roles only, and writes back what it read.
    types: [...module.types],
    connections: module.connections.map((connection) => ({ ...connection })),
  }));
  editor.cells = cells;
  editor.selected = null;
  document.getElementById("editor").hidden = false;
  changed();
}

async function editShipped() {
  const name = document.getElementById("shipped").value;
  try {
    const moduleList = await call("GET", `api/topologies/${encodeURIComponent(name)}`);
    openEditor(name, await call("POST", "api/topology/layout", moduleList));
  } catch (error) {
    say(`Cannot edit "${name}": ${error.message}`, "error");
  }
}

function editEmptyGrid() {
  openEditor(EMPTY_GRID, { modules: [], places: {} }, "an empty grid");
}

function closeEditor() {
  document.getElementById("editor").hidden = true;
  editor.checks += 1; // an answer still under way is shown nowhere
  editor.check = null;
  editor.selected = null;
  renderLaunchButtons();
}

function moduleById(id) {
  return editor.modules.find((module) => module.id === id) || null;
}

function moduleName(module) {
  return module.label || module.id;
}

function connects(module, direction, to) {
  return module.connections.some((c) => c.direction === direction && c.to === to.id);
}

// Lets `module` hand boxes to `to`, its neighbour in `direction`, or not.
function connect(module, direction, to, on) {
  const index = module.connections.findIndex((c) => c.direction === direction);
  if (on && index < 0) module.connections.push({ direction, to: to.id, port: 0 });
  if (!on && index >= 0) module.connections.splice(index, 1);
}

// Each change: the modules' leftmost column and top row are 0 again, the grid is drawn, and the
// server checks what the editor now holds.
function changed() {
  const cells = [...editor.cells.values()];
  if (cells.length) {
    const left = Math.min(...cells.map(([x]) => x));
    const top = Math.min(...cells.map(([, y]) => y));
    for (const [id, [x, y]] of editor.cells) editor.cells.set(id, [x - left, y - top]);
  }
  renderEditor();
  checkEdited();
}

// A module on the empty cell `x`, `y`, named in order: the next number after the highest one a
// module is named with. It is selected, so that its roles are a click away.
function addModule(x, y) {
  const numbers = editor.modules
    .map((module) => /^Module(\d+)$/.exec(module.id))
    .filter(Boolean)
    .map((match) => Number(match[1]));
  const number = Math.max(0, ...numbers) + 1;
  const id = `Module${number}`;
  editor.modules.push({ id, label: `Module ${number}`, types: [], connections: [] });
  editor.cells.set(id, [x, y]);
  editor.selected = id;
  changed();
}

// Takes the module out, with every connection to it.
function removeModule(id) {
  editor.modules = editor.modules.filter((module) => module.id !== id);
  for (const module of editor.modules) {
    module.connections = module.connections.filter((connection) => connection.to !== id);
  }
  editor.cells.delete(id);
  editor.selected = null;
  changed();
}

// Selects the module, or lets it go if it is selected. The topology stays as it is.
function select(id) {
  editor.selected = editor.selected === id ? null : id;
  renderEditor();
}

function setRole(id, role, on) {
  const module = moduleById(id);
  const others = module.types.filter((type) => !(type in ROLES));
  const roles = Object.keys(ROLES).filter((each) => (each === role ? on : module.types.includes(each)));
  module.types = [...others, ...roles];
  changed();
}

// The ways between two neighbouring modules `a` and `b`, which lies in `direction` from `a`, East
// or South: whether `a` hands boxes to `b`, and whether `b` hands them back.
function ways(a, b, direction) {
  return { there: connects(a, direction, b), back: connects(b, OPPOSITE[direction], a) };
}

// What a click on an edge makes of its ways: none, one way, the other way, both ways, none again.
const NEXT_WAYS = {
  none: { there: true, back: false },
  there: { there: false, back: true },
  back: { there: true, back: true },
  both: { there: false, back: false },
};

function waysName({ there, back }) {
  return there && back ? "both" : there ? "there" : back ? "back" : "none";
}

function cycleConnection(aId, bId, direction) {
  const a = moduleById(aId);
  const b = moduleById(bId);
  const next = NEXT_WAYS[waysName(ways(a, b, direction))];
  connect(a, direction, b, next.there);
  connect(b, OPPOSITE[direction], a, next.back);
  changed();
}

// The tracks of `count` cells on one axis of the grid, with a gap between each two for an edge.
function tracks(count) {
  return count > 1 ? `repeat(${count - 1}, var(--cell) var(--gap)) var(--cell)` : "var(--cell)";
}

// Draws the grid: every module on its cell, an empty row and column around them to add one on,
// and an edge between each two neighbouring modules. Then the selected module's controls.
function renderEditor() {
  document.getElementById("editor-base").textContent = `Edited from ${editor.startedFrom}`;
  const moduleOn = new Map();
  for (const module of editor.modules) moduleOn.set(editor.cells.get(module.id).join(","), module);
  const cells = [...editor.cells.values()];
  const right = Math.max(0, ...cells.map(([x]) => x)) + 1;
  const bottom = Math.max(0, ...cells.map(([, y]) => y)) + 1;
  const grid = document.getElementById("editor-grid");
  grid.style.gridTemplateColumns = tracks(right + 2);
  grid.style.gridTemplateRows = tracks(bottom + 2);
  const children = [];
  for (let y = -1; y <= bottom; y += 1) {
    for (let x = -1; x <= right; x += 1) {
      const column = 2 * (x + 1) + 1;
      const row = 2 * (y + 1) + 1;
      const module = moduleOn.get(`${x},${y}`);
      children.push(placed(module ? moduleCell(module, x, y) : emptyCell(x, y), column, row));
      if (!module) continue;
      const east = moduleOn.get(`${x + 1},${y}`);
      if (east) children.push(placed(edgeButton(module, east, "East"), column + 1, row));
      const south = moduleOn.get(`${x},${y + 1}`);
      if (south) children.push(placed(edgeButton(module, south, "South"), column, row + 1));
    }
  }
  grid.replaceChildren(...children);
  renderSelected();
}

function placed(node, column, row) {
  node.style.gridColumn = String(column);
  node.style.gridRow = String(row);
  return node;
}

function emptyCell(x, y) {
  return el("button", {
    type: "button",
    class: "cell empty",
    "data-cell": `${x},${y}`,
    "aria-label": "Add a module here",
    title: "Add a module here",
    text: "+",
    onclick: () => addModule(x, y),
  });
}

function moduleCell(module, x, y) {
  const selected = editor.selected === module.id;
  const roles = Object.entries(ROLES)
    .filter(([role]) => module.types.includes(role))
    .map(([, name]) => name);
  return el(
    "button",
    {
      type: "button",
      class: selected ? "cell module selected" : "cell module",
      "data-cell": `${x},${y}`,
      "data-module": module.id,
      "aria-pressed": String(selected),
      title: module.id,
      onclick: () => select(module.id),
    },
    [el("span", { class: "name", text: moduleName(module) }), el("span", { class: "roles", text: roles.join(" · ") })]
  );
}

// What an edge shows of its ways, by the direction of its second module from its first.
const GLYPHS = {
  East: { none: "·", there: "→", back: "←", both: "⇄" },
  South: { none: "·", there: "↓", back: "↑", both: "⇅" },
};

// The edge between `a` and `b`, its neighbour in `direction`, East or South.
function edgeButton(a, b, direction) {
  const name = waysName(ways(a, b, direction));
  const says = {
    none: "none",
    there: `${moduleName(a)} to ${moduleName(b)}`,
    back: `${moduleName(b)} to ${moduleName(a)}`,
    both: "both ways",
  }[name];
  return el("button", {
    type: "button",
    class: `edge ${direction === "East" ? "across" : "down"} ${name}`,
    "data-edge": `${a.id} ${b.id}`,
    "aria-label": `Connection between ${moduleName(a)} and ${moduleName(b)}: ${says}`,
    title: "Click to cycle: none, one way, the other way, both ways",
    text: GLYPHS[direction][name],
    onclick: () => cycleConnection(a.id, b.id, direction),
  });
}

// A transit time field's text as the module holds it: null for an empty field (the module takes
// the launch's transit time), the number of seconds for a number of 0 or more, else the text
// itself, which `editedModuleList` sends on so that the server's load check refuses it too.
const SECONDS_PATTERN = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;
function parseTransitTime(text) {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const seconds = SECONDS_PATTERN.test(trimmed) ? Number(trimmed) : NaN;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : trimmed;
}

// Why a module's transit time is refused, or "" where it is fine.
function transitTimeProblem(value) {
  return typeof value === "string"
    ? `The transit time must be a number of seconds, 0 or more, not "${value}".`
    : "";
}

// The selected module's transit time field and, after it, the reason it is refused, if it is.
// A change goes to the load check without drawing the panel again, so that the field keeps the
// focus while a person types.
function transitTimeField(module) {
  const problem = el("span", {
    class: "field-problem",
    role: "alert",
  });
  const input = el("input", {
    type: "text",
    inputmode: "decimal",
    size: "4",
    "aria-label": "Transit time in seconds",
    value: module.transit_seconds == null ? "" : String(module.transit_seconds),
    oninput: () => {
      module.transit_seconds = parseTransitTime(input.value);
      showProblem();
      checkEdited();
    },
  });
  function showProblem() {
    problem.textContent = transitTimeProblem(module.transit_seconds);
    input.setAttribute("aria-invalid", String(problem.textContent !== ""));
  }
  showProblem();
  const label = el(
    "label",
    { class: "switch", title: "How long this module takes to convey a box, in seconds. Empty: the launch's transit time. 0: no wait." },
    ["transit time (s) ", input]
  );
  return [label, problem];
}

function renderSelected() {
  const panel = document.getElementById("editor-selected");
  const module = moduleById(editor.selected);
  if (!module) {
    panel.replaceChildren(el("span", { class: "muted", text: "Select a module to make it an entry or an exit, to give it a transit time, or to remove it." }));
    return;
  }
  panel.replaceChildren(
    el("strong", { text: moduleName(module) }),
    ...Object.entries(ROLES).map(([role, name]) =>
      el("label", { class: "switch" }, [
        el("input", {
          type: "checkbox",
          checked: module.types.includes(role),
          onchange: (event) => setRole(module.id, role, event.target.checked),
        }),
        ` ${name}`,
      ])
    ),
    ...transitTimeField(module),
    el("button", { type: "button", text: "Remove", onclick: () => removeModule(module.id) })
  );
}

// Sends what the editor holds to the server's load check, and shows its answer under the grid,
// unless a later change has sent a check of its own by then.
async function checkEdited() {
  const sent = (editor.checks += 1);
  editor.check = null;
  renderCheck();
  let answer;
  try {
    answer = await call("POST", "api/topology/check", editedModuleList());
  } catch (error) {
    answer = { error: error.message };
  }
  if (sent !== editor.checks) return;
  editor.check = answer;
  renderCheck();
}

function renderCheck() {
  const check = editor.check;
  const result = document.getElementById("editor-check");
  if (check === null) {
    result.className = "message info";
    result.textContent = "The load check runs…";
  } else if (check.error !== undefined) {
    result.className = "message error";
    result.textContent = `The load check did not answer: ${check.error}`;
  } else if (check.passes) {
    result.className = "message ok";
    result.textContent = "The load check passes.";
  } else {
    result.className = "message error";
    result.textContent = `The load check refuses this topology: ${check.reason}`;
  }
  renderLaunchButtons();
}

// Launches what the editor holds, naming the topology it started from, which the server calls the
// running topology by. The picker then shows that topology, if it is a shipped one.
async function launchEdited() {
  const seconds = transitTime();
  if (seconds === null) return;
  const alarm = deadlockAlarmSeconds();
  if (alarm === null) return;
  const query = new URLSearchParams({
    base: editor.base,
    transit_time: seconds,
    ...chosenStrategies(),
    collect_history: collectHistory(),
    deadlock_alarm_seconds: alarm,
  });
  const what = `the topology edited from ${editor.startedFrom}`;
  if (await launchAfterAsking(what, `api/topology/launch?${query}`, editedModuleList())) pick(editor.base);
}

// -- Topology files -------------------------------------------------------------------------- //

// A topology file is a module list, the one topology format, which the shipped topologies and
// `jms-uc2 --topology <path>` have too: Download saves what the editor holds as one, and Upload
// opens one in the editor. The format carries no cells, so a file opens as the server lays it out.

// Saves what the editor holds as a topology file, named after the topology the editor started
// from, so that the file opens again as that same topology: `pair.json` for one edited from
// "pair".
function downloadEdited() {
  const text = `${JSON.stringify(editedModuleList(), null, 2)}\n`;
  const href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  el("a", { href, download: `${editor.base}.json` }).click();
  // Once the browser has taken the file.
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

// Opens the editor on the topology file `file`, a shipped one or one made by hand. The server
// reads it (`api/topology/file`), as the command line reads one, and lays it out; the page reads
// nothing of it but its name, which without `.json` names the topology the editor starts from. A
// file the server refuses, or one the grid cannot show, leaves the editor as it was, and the page
// says why. A topology that fails the load check opens, so that a person edits it until it passes.
async function uploadFile(file) {
  try {
    const layout = await call("POST", "api/topology/file", file);
    openEditor(file.name.replace(/\.json$/i, ""), layout, `the file "${file.name}"`);
    say("");
  } catch (error) {
    say(`Cannot open "${file.name}": ${error.message}`, "error");
  }
}

// The file a person chose, once: the chooser is emptied, so that choosing the same file again
// opens it again.
function uploadChosen(event) {
  const [file] = event.target.files;
  event.target.value = "";
  if (file) uploadFile(file);
}

// -- Clear ----------------------------------------------------------------------------------- //

// Clear stops every process, then empties the run's dataset graph by graph, and the page says
// what the store holds then. It asks first, since nothing brings the data back, and the server
// clears nothing while another launcher's process is live there, which the page then says. The
// next launch loads the ontologies, the shapes and the topology again. (Added on 2026-10-10,
// jms_kapps 5, decided by Etienne on 2026-10-05, who asked for a clear button that kills every
// process and clears the whole of the store's unit, the dataset.)

// Whether a clear is under way: no launch and no second clear may start then.
let clearing = false;

async function clearAfterAsking() {
  if (lastState === null) return; // nothing is known yet of the dataset it would empty
  const { dataset, store_url } = lastState;
  const where = `dataset ${dataset}` + (store_url ? ` on ${store_url}` : "");
  if (!confirm(`Clear stops every process and empties ${where}. Go on?`)) return;
  clearing = true;
  renderLaunchButtons();
  say(`Clearing: stopping every process, then emptying ${dataset} graph by graph\u2026`);
  try {
    const cleared = await call("POST", "api/clear");
    const count = cleared.statements;
    const statements = `${count} ${count === 1 ? "statement" : "statements"}`;
    if (count === 0) say(`${cleared.dataset} is empty: ${statements}`, "ok");
    else say(`${cleared.dataset} is not empty: ${statements}`, "error");
  } catch (error) {
    say(`Not cleared: ${error.message}`, "error");
  } finally {
    clearing = false;
    renderLaunchButtons();
  }
  // The processes stopped, and nothing is seeded any more: shown at once, not at the next poll.
  poll();
}

// -- Start ----------------------------------------------------------------------------------- //

// One poll at a time, so that a Launcher slow to answer is not sent more polls to queue up.
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await call("GET", "api/state", undefined, AbortSignal.timeout(POLL_TIMEOUT_MS));
    answeredAt = new Date();
    pollError = null;
    renderState(state);
  } catch (error) {
    // The Launcher is gone or stuck. The page keeps what it last showed, says so, and the next
    // poll tries again.
    pollError =
      error.name === "TimeoutError" ? `no answer within ${POLL_TIMEOUT_MS / 1000} s` : error.message;
    drawPart(STATUS, renderStatus);
    renderDrawErrors();
  } finally {
    polling = false;
  }
}

async function start() {
  const names = await call("GET", "api/topologies");
  document.getElementById("shipped").replaceChildren(...names.map((name) => el("option", { value: name, text: name })));
  directions = await call("GET", "api/directions");
  moduleListNames = await call("GET", "api/topology/format");
  strategies = await call("GET", "api/strategies");
  fillStrategyPickers();
  document.querySelector("#modules thead tr").append(...directions.map((direction) => el("th", { text: direction })));
  document.getElementById("stop-all").addEventListener("click", stopAll);
  document.getElementById("clear").addEventListener("click", clearAfterAsking);
  document.getElementById("copy-query").addEventListener("click", copyQuery);
  document.getElementById("launch").addEventListener("click", launch);
  document.getElementById("edit").addEventListener("click", editShipped);
  document.getElementById("edit-empty").addEventListener("click", editEmptyGrid);
  const chooser = document.getElementById("upload-file");
  chooser.addEventListener("change", uploadChosen);
  document.getElementById("upload").addEventListener("click", () => chooser.click());
  document.getElementById("download").addEventListener("click", downloadEdited);
  document.getElementById("launch-edited").addEventListener("click", launchEdited);
  document.getElementById("close-editor").addEventListener("click", closeEditor);
  await poll();
  setInterval(poll, POLL_MS);
}

start();
