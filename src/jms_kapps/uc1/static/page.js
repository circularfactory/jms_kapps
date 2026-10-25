// The page of use case 1. The server draws the page whole once; this script then asks for the
// state every second and changes what differs, in place, so the page never reloads. It builds every
// element with textContent, so a process's output stays text. Without JavaScript the page shows the
// state of its load.

const POLL_MS = 1000;

// Which processes have their output open, by resource IRI; kept across draws.
const openOutputs = new Set();

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === "text") node.textContent = value;
    else node.setAttribute(name, value);
  }
  node.append(...children);
  return node;
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function setAttribute(node, name, value) {
  if (node.getAttribute(name) !== value) node.setAttribute(name, value);
}

function rowOf(resource) {
  return [...document.querySelectorAll("#processes tbody tr:not(.output)[data-resource]")].find(
    (row) => row.dataset.resource === resource,
  );
}

function outputRowOf(resource) {
  return [...document.querySelectorAll("#processes tbody tr.output")].find(
    (row) => row.dataset.resource === resource,
  );
}

function buildRows(process) {
  const name = el("button", { type: "button", class: "link", text: process.name });
  const row = el("tr", { "data-resource": process.resource }, [
    el("td", {}, [el("span", { class: "dot" }), name]),
    el("td", { class: "state" }),
    el("td", { class: "mono pid" }),
    el("td", { class: "links" }),
  ]);
  const output = el("tr", { class: "output", "data-resource": process.resource, hidden: "" }, [
    el("td", { colspan: 4 }, [el("div", { class: "muted mono command" }), el("pre")]),
  ]);
  return { row, output };
}

function toggleOutput(resource) {
  const output = outputRowOf(resource);
  if (!output) return;
  if (openOutputs.has(resource)) {
    openOutputs.delete(resource);
    output.hidden = true;
  } else {
    openOutputs.add(resource);
    output.hidden = false;
  }
}

// One listener for every name, those the server drew and those drawn here.
document.querySelector("#processes tbody").addEventListener("click", (event) => {
  const name = event.target.closest("button.link");
  if (name) toggleOutput(name.closest("tr").dataset.resource);
});

function drawProcess(process) {
  let row = rowOf(process.resource);
  let output = outputRowOf(process.resource);
  if (!row) {
    const built = buildRows(process);
    document.querySelector("#processes tbody").append(built.row, built.output);
    row = built.row;
    output = built.output;
  }
  const unconfirmed = process.unconfirmed_for !== null && process.unconfirmed_for !== undefined;
  setAttribute(row, "class", process.state + (unconfirmed ? " unconfirmed" : ""));
  const state = row.querySelector(".state");
  setText(state, process.state);
  setAttribute(
    state,
    "title",
    unconfirmed ? `Last confirmed against the knowledge graph ${Math.round(process.unconfirmed_for)} s ago.` : "",
  );
  setText(row.querySelector(".pid"), String(process.pid));

  const links = row.querySelector(".links");
  const wanted = JSON.stringify(process.links);
  if (links.dataset.links !== wanted) {
    links.dataset.links = wanted;
    links.replaceChildren(
      ...process.links.map((link) =>
        el("a", { href: link.href, target: "_blank", rel: "noopener", text: link.label }),
      ),
    );
  }

  setText(output.querySelector(".command"), process.command);
  setText(
    output.querySelector("pre"),
    process.output.length ? process.output.join("\n") : "(no output yet)",
  );
  output.hidden = !openOutputs.has(process.resource);
}

// Every element that points at a process's dashboard by the process's name: shown with the link
// once the process has an address, and hidden before.
function drawDashboards(processes) {
  for (const node of document.querySelectorAll("[data-dashboard-of]")) {
    const process = processes.find((p) => p.name === node.dataset.dashboardOf);
    if (process && process.dashboard) {
      setAttribute(node, "href", process.dashboard);
      node.hidden = false;
    } else {
      node.removeAttribute("href");
      node.hidden = node.hasAttribute("data-hide-without-address");
    }
  }
}

// -- Operations, Learned values and Measured positions ---------------------------------------- //

// Each list shows the rows that the background read of its one SPARQL query last found, and that
// query's text, exactly as the page's server sent it. The page sends no query of its own: it
// shows what the state answers.

// The cells of one row of a list, each as text or as a node.
function drawRows(tbody, rows, cellsOf) {
  const wanted = JSON.stringify(rows);
  if (tbody.dataset.rows === wanted) return;
  tbody.dataset.rows = wanted;
  tbody.replaceChildren(
    ...rows.map((row) =>
      el(
        "tr",
        {},
        cellsOf(row).map(([value, cls]) =>
          el("td", cls ? { class: cls } : {}, [value instanceof Node ? value : String(value ?? "")]),
        ),
      ),
    ),
  );
}

function operationCells(operation) {
  const name = operation.decided_by_name;
  const decided =
    name !== null && operation.decided_by_dashboard !== null
      ? el("a", { href: operation.decided_by_dashboard, target: "_blank", rel: "noopener", text: name })
      : name;
  return [
    [operation.operation],
    [operation.screw],
    [operation.issuer_name],
    [operation.status],
    [operation.outcome],
    [operation.reason],
    [decided, "decided-by"],
  ];
}

function learnedCells(value) {
  return [[value.screw_type], [value.at_start], [value.now]];
}

// The line on the latest read of the list `name`, and its query with its copy button.
function drawList(name, list, one, many, cellsOf) {
  if (!list) return;
  drawRows(document.querySelector(`#${name} tbody`), list.rows, cellsOf);
  const read = list.read_at !== null;
  const when = read ? new Date(list.read_at).toLocaleTimeString() : null;
  const count = list.rows.length;
  let text = read ? `Read at ${when}: ${count} ${count === 1 ? one : many}.` : "";
  let cls = "muted";
  if (list.error !== null) {
    text = `The latest read failed: ${list.error}.` + (read ? ` The list is the one read at ${when}.` : "");
    cls = "message error";
  }
  const line = document.getElementById(`${name}-read`);
  setAttribute(line, "class", cls);
  setText(line, text);
  setText(document.getElementById(`${name}-query`), list.query || "");
  const button = document.querySelector(`button[data-copies="${name}-query"]`);
  if (button.disabled !== !list.query) button.disabled = !list.query;
}

// What the copy button says, until it says what became of a copy.
const COPY_QUERY = "Copy the query";

// Copies the query's text as the page shows it. Where the page is no secure context the browser
// offers no clipboard: then the text is selected, to copy as a selection is copied.
async function copyQuery(button) {
  const text = document.getElementById(button.dataset.copies);
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

for (const button of document.querySelectorAll("button[data-copies]")) {
  button.addEventListener("click", () => copyQuery(button));
}

function measuredCells(position) {
  return [
    [position.subject],
    [position.position],
    [position.estimate],
    [position.standard_deviation],
    [position.literal, "mono literal"],
  ];
}

// -- The switch "Save measurements as uncertainty quantifications" ------------------------------ //

// The switch shows and sets the anomaly detector's mode on the detector's own route, through the
// page's passthrough: the page's server holds no connection to the detector. It stays disabled
// while the detector has no address, and while a change is on its way.

const saveUq = document.getElementById("save-uq");

// Counts the changes of the switch, so that a read sent before a change does not undo it.
let changes = 0;
let changing = false;

async function readMode(route) {
  if (!route) {
    saveUq.removeAttribute("data-mode-route");
    saveUq.disabled = true;
    return;
  }
  saveUq.dataset.modeRoute = route;
  if (changing) return;
  const before = changes;
  try {
    const response = await fetch(route, { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const mode = await response.json();
    if (changing || changes !== before) return;
    saveUq.checked = mode.on;
    saveUq.disabled = false;
  } catch {
    if (!changing) saveUq.disabled = true;
  }
}

saveUq.addEventListener("change", async () => {
  const route = saveUq.dataset.modeRoute;
  const wanted = saveUq.checked;
  changes += 1;
  changing = true;
  saveUq.disabled = true;
  try {
    const response = await fetch(route, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: wanted }),
    });
    if (!response.ok) throw new Error(String(response.status));
    saveUq.checked = (await response.json()).on;
  } catch {
    saveUq.checked = !wanted;
  } finally {
    changing = false;
    // The detector may have lost its address meanwhile.
    saveUq.disabled = !saveUq.dataset.modeRoute;
  }
});

function drawAll(state) {
  const known = new Set(state.processes.map((p) => p.resource));
  for (const row of document.querySelectorAll("#processes tbody tr[data-resource]")) {
    if (!known.has(row.dataset.resource)) row.remove();
  }
  for (const process of state.processes) drawProcess(process);
  drawDashboards(state.processes);
  drawList("operations", state.operations, "operation", "operations", operationCells);
  drawList("learned-values", state.learned_values, "Screw type", "Screw types", learnedCells);
  drawList("measured-positions", state.measured_positions, "position", "positions", measuredCells);
  readMode(state.mode_route);
}

function answered(ok) {
  document.body.classList.toggle("unanswered", !ok);
  document.getElementById("status").hidden = ok;
}

async function poll() {
  try {
    const response = await fetch("api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    drawAll(await response.json());
    answered(true);
  } catch (error) {
    answered(false);
  }
}

poll();
setInterval(poll, POLL_MS);
