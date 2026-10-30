// The monitor's page. It draws what `/api/state` answers. It sends a PUT only when a person
// switches the monitor's mode or sets the interval of its graph read, and every other request is a
// GET. The monitor answers from memory, so a poll of this page loads neither the graph nor the
// streams.
"use strict";

const DIRECTIONS = ["North", "East", "South", "West"];
const CELL = 150; // one grid cell of the drawing, in SVG units
const MODULE = 104; // a module's square
const FRAME = 5; // the stroke of its green frame, drawn inside the square; style.css gives it the same width
const ROLLERS = 6; // the rollers in a module, each ROLLER_HEIGHT high, with a belt between each two
const ROLLER_HEIGHT = 9;
const BELT_HEIGHT = 5;
const ROLLER_END = 5; // the darker cap at each end of a roller
const ROLLER_INSET = 7; // the free ground between the frame and the rollers
const SVG = "http://www.w3.org/2000/svg";

let timer = null;

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function svg(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function centre(module) {
  const [x, y] = module.place;
  return [x * CELL + CELL / 2, y * CELL + CELL / 2];
}

// The arrowheads of the wires, by class: how long and how wide each is. A wire stops short of a
// square by WIRE_GAP, so that its head stands in the gap between two modules, clear of both.
const HEADS = { wire: [11, 9] };
const WIRE_GAP = 3;

// One marker for each kind of head. It is drawn from the end of its line onwards, so the line
// stops where the head begins and no stroke shows past its point.
function arrowheads() {
  const defs = svg("defs");
  defs.append(rollerShading());
  for (const [kind, [length, width]] of Object.entries(HEADS)) {
    const marker = svg("marker", {
      id: `${kind}-head`, class: `${kind}-head`, viewBox: "0 0 10 10", refX: 0, refY: 5,
      markerUnits: "userSpaceOnUse", markerWidth: length, markerHeight: width,
      preserveAspectRatio: "none", orient: "auto-start-reverse",
    });
    marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z" }));
    defs.append(marker);
  }
  return defs;
}

// How a motor roller looks from above: a silver cylinder, light along its top, dark along its
// foot. It lies east–west, so the shading runs north to south.
function rollerShading() {
  const gradient = svg("linearGradient", { id: "roller-shading", x1: 0, y1: 0, x2: 0, y2: 1 });
  for (const [offset, colour] of [[0, "#fbfbfb"], [0.45, "#cfcfcf"], [0.75, "#8a8a8a"], [1, "#d6d6d6"]]) {
    gradient.append(svg("stop", { offset, "stop-color": colour }));
  }
  return gradient;
}

// The look of a module from above, as the photo of a real FlexConveyor module shows it. The
// motor rollers are silver cylinders that lie east–west side by side and carry a box
// north–south; between each two runs a dark belt conveyor that carries a box east–west, with its
// light pads. No light barrier is drawn. `cx`, `cy` is the module's centre.
function moduleInterior(cx, cy) {
  const interior = svg("g", { class: "interior" });
  const step = ROLLER_HEIGHT + BELT_HEIGHT;
  const top = cy - (ROLLERS * ROLLER_HEIGHT + (ROLLERS - 1) * BELT_HEIGHT) / 2;
  const half = MODULE / 2 - FRAME - ROLLER_INSET; // half a roller's length
  for (let row = 0; row < ROLLERS; row++) {
    const y = top + row * step;
    interior.append(svg("rect", { class: "roller", x: cx - half, y, width: 2 * half, height: ROLLER_HEIGHT, rx: 2, fill: "url(#roller-shading)" }));
    for (const x of [cx - half, cx + half - ROLLER_END]) {
      interior.append(svg("rect", { class: "roller-end", x, y, width: ROLLER_END, height: ROLLER_HEIGHT, rx: 1.5 }));
    }
    if (row === ROLLERS - 1) continue; // a belt lies between two rollers, none below the last
    const beltY = y + ROLLER_HEIGHT;
    interior.append(svg("rect", { class: "belt", x: cx - half + ROLLER_END, y: beltY, width: 2 * half - 2 * ROLLER_END, height: BELT_HEIGHT }));
    // The pads sit in turn further west and further east, as on a real module.
    for (const pad of row % 2 === 0 ? [-26, 8] : [-14, 20]) {
      interior.append(svg("rect", { class: "belt-pad", x: cx + pad, y: beltY, width: 16, height: BELT_HEIGHT }));
    }
  }
  return interior;
}

// A line of `kind` from the edge of the square of `from` to that of `to`, with a head at either
// end that `heads` names, each pointing out of the line.
function arrow(kind, from, to, heads) {
  const [x1, y1] = centre(from);
  const [x2, y2] = centre(to);
  const length = Math.hypot(x2 - x1, y2 - y1);
  const [ux, uy] = [(x2 - x1) / length, (y2 - y1) / length];
  // From a square's centre to its edge, along the line, and then the gap.
  const off = MODULE / 2 / Math.max(Math.abs(ux), Math.abs(uy)) + WIRE_GAP;
  const [head] = HEADS[kind];
  const start = off + (heads.start ? head : 0);
  const end = length - off - (heads.end ? head : 0);
  const line = svg("line", {
    class: kind, x1: x1 + ux * start, y1: y1 + uy * start, x2: x1 + ux * end, y2: y1 + uy * end,
  });
  if (heads.start) line.setAttribute("marker-start", `url(#${kind}-head)`);
  if (heads.end) line.setAttribute("marker-end", `url(#${kind}-head)`);
  return line;
}

// Each wire with an arrowhead where each of its connections leads, and a double head where they
// lead both ways: the connections as the graph holds them, and nothing the monitor computed.
function drawWires(drawing, wires, byId) {
  for (const wire of wires) {
    const [a, b] = wire.between.map((id) => byId[id]);
    if (!a || !b) continue;
    const group = svg("g", { class: "connection" });
    const leads = wire.leads_to.map((id) => byId[id].label).join(" and ");
    group.append(svg("title", {}, `${a.label} – ${b.label}: leads to ${leads}.`));
    group.append(arrow("wire", a, b, { start: wire.leads_to.includes(a.id), end: wire.leads_to.includes(b.id) }));
    drawing.append(group);
  }
}

// One colour for each box, from its UUID, so that a box keeps its colour from poll to poll: its
// square's outline and the disc on its destination have it. None of them is red, which means
// failed, green, which means live, or the orange of a box. A box is named by a UUID, `Box-<UUID>`.
const BOX_COLOURS = ["#1565c0", "#6a1b9a", "#00838f", "#ad1457", "#5d4037", "#37474f"];
// Where the discs of the boxes bound for a module stand, from its centre: in a row along its foot,
// the first box's on the right, so that up to four of them never overlap.
const DISC_PLACES = [[34, 40], [10, 40], [-14, 40], [-38, 40]];

// A box's name without "Box": its UUID, or the number of a box that a seed named by one.
function boxId(id) {
  return (id || "").replace(/^Box-?/, "");
}

// What a box's square and the disc on its destination say: the first four characters of its UUID.
function boxLabel(id) {
  return boxId(id).slice(0, 4);
}

// The colour of a box: a hash of its UUID, taken modulo the colours. No number is read from it.
function boxColour(id) {
  let hash = 0;
  for (const character of boxId(id)) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return BOX_COLOURS[hash % BOX_COLOURS.length];
}

// The boxes that a module of the drawing holds, by their names.
function onAModule(modules) {
  return new Set(modules.flatMap((module) => module.boxes));
}

// A disc with each box's label on its destination, for each box that a module holds: the
// destination its transport names, as the graph holds it. The monitor computes no path to it.
function drawDestinations(drawing, boxes, modules, byId) {
  const held = onAModule(modules);
  const bound = {};
  for (const box of boxes) {
    const destination = byId[box.destination_id];
    if (!destination || !held.has(box.id)) continue;
    const before = bound[destination.id] || 0;
    bound[destination.id] = before + 1;
    const [dx, dy] = DISC_PLACES[before % DISC_PLACES.length];
    const [cx, cy] = centre(destination);
    const disc = svg("g", { class: "destination" });
    disc.append(svg("title", {}, `${box.id} goes to ${destination.label}, its transport's destination`));
    disc.append(svg("circle", { cx: cx + dx, cy: cy + dy, r: 11, fill: boxColour(box.id) }));
    disc.append(svg("text", { x: cx + dx, y: cy + dy + 4, "text-anchor": "middle" }, boxLabel(box.id)));
    drawing.append(disc);
  }
}

function drawTopology(modules, boxes, wires) {
  const drawing = document.getElementById("topology");
  const columns = Math.max(1, ...modules.map((m) => m.place[0] + 1));
  const rows = Math.max(1, ...modules.map((m) => m.place[1] + 1));
  drawing.setAttribute("viewBox", `0 0 ${columns * CELL} ${rows * CELL}`);
  // At most one and a half times its own size, so that a topology of one or two columns, such as
  // the pair, is not drawn as wide as the page.
  drawing.style.maxWidth = `${columns * CELL * 1.5}px`;
  drawing.replaceChildren(arrowheads());

  const byId = Object.fromEntries(modules.map((m) => [m.id, m]));
  drawWires(drawing, wires, byId);

  // Each module's square now, and its name later, over the discs on it.
  const names = [];
  for (const module of modules) {
    const [cx, cy] = centre(module);
    const state = module.live ? "live" : "offline";
    // An offline module keeps its dashed grey, whatever it holds: style.css fills only a live one.
    const classes = `module ${state}${module.holds_box ? " holds" : ""}`;
    const title = `${module.label}: ${state}${module.holds_box ? ", holds a box" : ""}`;
    const square = svg("g", { class: classes });
    square.append(svg("title", {}, title));
    // The frame's stroke lies inside the square, so that a wire's gap is measured from its edge.
    square.append(svg("rect", { class: "frame", x: cx - MODULE / 2 + FRAME / 2, y: cy - MODULE / 2 + FRAME / 2, width: MODULE - FRAME, height: MODULE - FRAME }));
    square.append(moduleInterior(cx, cy));
    drawing.append(square);
    const name = svg("g", { class: "name" });
    name.append(svg("title", {}, title));
    name.append(svg("text", { x: cx, y: cy - MODULE / 2 + 18, "text-anchor": "middle" }, module.label));
    const kind = { EntryModule: "entry", ExitModule: "exit" }[module.type];
    if (kind) name.append(svg("text", { class: "kind", x: cx, y: cy - MODULE / 2 + 32, "text-anchor": "middle" }, kind));
    names.push(name);
  }

  drawDestinations(drawing, boxes, modules, byId);
  drawing.append(...names);

  for (const module of modules) {
    const [cx, cy] = centre(module);
    module.boxes.forEach((box, i) => {
      const bx = cx - 30 + (i % 2) * 32;
      const by = cy - 4 + Math.floor(i / 2) * 26;
      const boxGroup = svg("g", { class: "box" });
      // Outlined in the colour of the disc on its destination.
      boxGroup.append(svg("rect", { x: bx, y: by, width: 28, height: 22, rx: 3, style: `stroke: ${boxColour(box)}` }));
      boxGroup.append(svg("text", { x: bx + 14, y: by + 15, "text-anchor": "middle" }, boxLabel(box)));
      boxGroup.append(svg("title", {}, box));
      drawing.append(boxGroup);
    });
  }
}

function drawModules(modules) {
  const body = document.querySelector("#modules tbody");
  body.replaceChildren(...modules.map((module) => {
    const to = Object.fromEntries(module.connections.map((c) => [c.direction, c.to_label]));
    return el("tr", {}, [
      el("td", { text: module.label }),
      el("td", { class: "muted", text: module.type }),
      el("td", { class: module.live ? "live" : "offline", text: module.live ? "live" : "offline" }),
      el("td", { class: "mono", text: module.boxes.join(", ") || "—" }),
      ...DIRECTIONS.map((d) => el("td", { class: "mono", text: to[d] || "—" })),
    ]);
  }));
}

// A box's destination, with the colour of its disc in the drawing where a module holds the box.
function destinationCell(box, held) {
  if (!box.destination) return el("td", { class: "mono", text: "—" });
  return el("td", { class: "mono" }, [
    held.has(box.id) ? el("span", { class: "swatch", style: `background: ${boxColour(box.id)}` }) : null,
    box.destination,
  ]);
}

function drawBoxes(boxes, modules) {
  document.getElementById("box-count").textContent = boxes.length;
  const body = document.querySelector("#boxes tbody");
  if (boxes.length === 0) {
    body.replaceChildren(el("tr", {}, el("td", { colspan: 5, class: "muted", text: "No box in the knowledge graph." })));
    return;
  }
  const held = onAModule(modules);
  body.replaceChildren(...boxes.map((box) => el("tr", {}, [
    el("td", { class: "mono", text: box.id }),
    el("td", { text: box.status || "—" }),
    el("td", { class: "mono", text: box.holder || "nobody" }),
    el("td", { class: "mono", text: box.origin || "—" }),
    destinationCell(box, held),
  ])));
}

function drawHandovers(handovers) {
  const body = document.querySelector("#handovers tbody");
  if (handovers.length === 0) {
    body.replaceChildren(el("tr", {}, el("td", { colspan: 3, class: "muted", text: "No handover seen yet." })));
    return;
  }
  body.replaceChildren(...handovers.map((handover) => el("tr", { class: "handover" }, [
    el("td", { class: "mono", text: new Date(handover.seen).toLocaleTimeString(), title: handover.seen }),
    el("td", { class: "mono", text: handover.box }),
    el("td", { class: "mono", text: `${handover.from} → ${handover.to}` }),
  ])));
}

// The refusals drawn, by id. A refusal never changes once the monitor keeps it, so each is drawn
// once and kept: an open "report" stays open across polls.
const drawnRefusals = new Map();
let refusalsDrawnOnce = false;
// Every refusal this page has shown, by id: one that comes back when the streams are followed
// again, after "graph only", is not new, and is not highlighted.
const shownRefusals = new Set();

// The monitor's two modes, as `/api/state` and `/api/mode` name them. The switch at Refusals is on
// in GRAPH_AND_STREAMS.
const GRAPH_ONLY = "graph only";
const GRAPH_AND_STREAMS = "graph + streams";

// Why the Refusals view is empty in "graph only".
const NO_REFUSALS_IN_GRAPH_ONLY =
  "The monitor follows no handover event stream, and a refused write leaves no trace in the " +
  "knowledge graph: the triple store refused it, so nothing of it was written. " +
  "Only the refusing service's handover event stream reports it, so turn on " +
  '"Follow the services\' handover event streams" to see the refusals.';

// Why the notice of a possible deadlock is not shown in "graph only".
const NO_DEADLOCK_IN_GRAPH_ONLY =
  "The monitor follows no handover event stream, and a deadlock alarm leaves no trace in the " +
  "knowledge graph: only the module that raised it reports it, on its handover event stream, " +
  'so turn on "Follow the services\' handover event streams" to see a possible deadlock.';

// A list of names as a person reads it: "Module1", "Module1 and Module2", "Module1, Module2 and
// Module3".
function listed(names) {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

// The one notice of a possible deadlock: every module involved, and each alarm with its reason; or
// why there is none.
function drawDeadlock(notice, mode) {
  const shown = document.getElementById("deadlock");
  if (mode === GRAPH_ONLY) {
    shown.replaceChildren(el("p", { class: "message info", text: NO_DEADLOCK_IN_GRAPH_ONLY }));
    return;
  }
  if (!notice) {
    shown.replaceChildren(el("p", { class: "muted", text: "No deadlock alarm." }));
    return;
  }
  shown.replaceChildren(
    el("p", { class: "message error notice", text: `Possible deadlock: ${listed(notice.modules)}` }),
    el("ul", { class: "alarms" }, notice.alarms.map((alarm) => el("li", { text: alarm.reason, title: alarm.time })))
  );
}

// The badge of each SHACL severity, by its whole IRI: a short name is only ever read.
const SH = "http://www.w3.org/ns/shacl#";
const SEVERITIES = {
  [`${SH}Violation`]: { text: "Violation", class: "violation" },
  [`${SH}Warning`]: { text: "Warning", class: "warning" },
  [`${SH}Info`]: { text: "Info", class: "info" },
};

function field(label, value, title, valueClass = "mono") {
  return el("span", { class: "field" }, [
    el("span", { class: "label", text: label }),
    el("span", { class: valueClass, text: value, ...(title ? { title } : {}) }),
  ]);
}

// One term of a result: its short name, with the whole IRI as its tooltip, or a dash.
function term(label, value) {
  return value ? field(label, value.short, value.full) : field(label, "—", null, "muted");
}

function badge(severity) {
  if (!severity) return el("span", { class: "badge", text: "Result" });
  const known = SEVERITIES[severity.full];
  return el("span", {
    class: `badge ${known ? known.class : ""}`,
    text: known ? known.text : severity.short,
    title: severity.full,
  });
}

function resultRow(result) {
  return el("li", { class: "result" }, [
    badge(result.severity),
    el("div", {}, [
      result.message
        ? el("p", { class: "result-message", text: result.message })
        : el("p", { class: "result-message muted", text: "The result gives no message." }),
      el("p", { class: "terms" }, [term("focus", result.focus), term("path", result.path), term("value", result.value)]),
      el("p", { class: "terms" }, [term("shape", result.shape), term("constraint", result.constraint)]),
    ]),
  ]);
}

function refusalCard(refusal) {
  const hop = refusal.receiver ? `${refusal.giver} → ${refusal.receiver}` : "—";
  return el("article", { class: "refusal", "data-id": refusal.id }, [
    el("div", { class: "refusal-head" }, [
      field("when", new Date(refusal.time).toLocaleTimeString(), refusal.time),
      field("giver", refusal.giver),
      field("box", refusal.box),
      field("hop", hop),
      refusal.reason ? el("span", { class: "muted", text: refusal.reason }) : null,
    ]),
    refusal.readable_results.length
      ? el("ul", { class: "results" }, refusal.readable_results.map(resultRow))
      : null,
    refusal.report_note ? el("p", { class: "message error", text: refusal.report_note }) : null,
    refusal.report
      ? el("details", refusal.report_note ? { open: "" } : {}, [
          el("summary", { text: "report" }),
          el("pre", { class: "mono report", text: refusal.report }),
        ])
      : null,
  ]);
}

function drawRefusals(refusals, mode) {
  const list = document.getElementById("refusals");
  const wanted = new Set(refusals.map((r) => r.id));
  for (const id of drawnRefusals.keys()) if (!wanted.has(id)) drawnRefusals.delete(id);
  const cards = refusals.map((refusal) => {
    let card = drawnRefusals.get(refusal.id);
    if (!card) {
      card = refusalCard(refusal);
      // A refusal that arrives while the page is open is highlighted for as long as style.css
      // fades it; those on the first draw were there before.
      if (refusalsDrawnOnce && !shownRefusals.has(refusal.id)) {
        card.classList.add("fresh");
        card.addEventListener("animationend", () => card.classList.remove("fresh"), { once: true });
      }
      drawnRefusals.set(refusal.id, card);
      shownRefusals.add(refusal.id);
    }
    return card;
  });
  refusalsDrawnOnce = true;
  if (cards.length === 0) {
    list.replaceChildren(mode === GRAPH_ONLY
      ? el("p", { class: "message info", text: NO_REFUSALS_IN_GRAPH_ONLY })
      : el("p", { class: "muted", text: "No refusal yet." }));
    return;
  }
  // Move only what is out of place, so that no card is taken out of the page and drawn again.
  [...list.children].filter((child) => !cards.includes(child)).forEach((child) => child.remove());
  cards.forEach((card, i) => {
    if (list.children[i] !== card) list.insertBefore(card, list.children[i] || null);
  });
}

// -- How the monitor reads: the streams switch at Refusals and the interval of its graph read -- //

// How many changes a person made are on their way: meanwhile a poll does not put back what the
// monitor answered before them.
let changing = 0;

function drawReading(reading) {
  const interval = document.getElementById("graph-seconds");
  interval.min = reading.shortest_graph_seconds;
  interval.max = reading.longest_graph_seconds;
  if (changing) return;
  // Disabled until the monitor has said which mode it runs in.
  const streamsSwitch = document.getElementById("follow-streams");
  streamsSwitch.checked = reading.mode === GRAPH_AND_STREAMS;
  streamsSwitch.disabled = false;
  // Not while a person edits the field.
  if (document.activeElement !== interval) interval.value = reading.graph_seconds;
}

// Why the monitor refused a change, as it says it: its reason, or each of its validation errors.
function refusedBecause(answer, status) {
  const detail = answer && answer.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((error) => error.msg).join("; ");
  return `the monitor answered HTTP ${status}`;
}

// Send a change of how the monitor reads, show why if it is refused, and draw what holds then.
async function change(path, body, messageId) {
  const message = document.getElementById(messageId);
  changing += 1;
  try {
    const response = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const answer = await response.json().catch(() => null);
      throw new Error(refusedBecause(answer, response.status));
    }
    message.hidden = true;
  } catch (e) {
    message.textContent = `The monitor did not take the change: ${e.message}`;
    message.hidden = false;
  } finally {
    changing -= 1;
  }
  await refresh();
}

function applyInterval() {
  const interval = document.getElementById("graph-seconds");
  if (Number.isNaN(interval.valueAsNumber)) {
    const message = document.getElementById("reading-message");
    message.textContent = `Give the interval in seconds, from ${interval.min} s to ${interval.max} s.`;
    message.hidden = false;
    return;
  }
  change("api/graph-read-interval", { seconds: interval.valueAsNumber }, "reading-message");
}

// -- The queries the monitor sends ------------------------------------------------------------ //

// What each read of the monitor reads, for a person; the read's own name stands beside it.
const READS = {
  modules: "The modules and their connections",
  live_resources: "The live services: those with an address and a fresh heartbeat",
  boxes: "Every box, with its transport and who possesses it",
  wms: "The WarehouseManagementSystem, by its class",
  streams: "Where each service serves its handover event stream",
};

// The card of each query drawn, by its read: a card is kept from poll to poll, so that a text a
// person selects stays selected while it does not change.
const queryCards = new Map();

function duration(seconds) {
  const milliseconds = seconds * 1000;
  return milliseconds < 1 ? "under 1 ms" : `${Math.round(milliseconds)} ms`;
}

// Copy the text of a query. Where the page is no secure context the browser offers no clipboard:
// then the text is selected and copied as a person copies a selection.
async function copyQuery(text, button) {
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
  setTimeout(() => { button.textContent = "Copy"; }, 2000);
}

function queryCard(read) {
  const copy = el("button", { type: "button", class: "copy", text: "Copy" });
  const text = el("pre", { class: "mono query-text" });
  copy.addEventListener("click", () => copyQuery(text, copy));
  return el("article", { class: "query", "data-read": read }, [
    el("div", { class: "query-head" }, [
      el("b", { text: READS[read] || read }),
      el("span", { class: "mono muted", text: read }),
      el("span", { class: "every" }),
      el("span", { class: "last muted" }),
      copy,
    ]),
    text,
  ]);
}

function drawQueries(queries) {
  const list = document.getElementById("queries");
  if (queries.length === 0) {
    queryCards.clear();
    list.replaceChildren(el("p", { class: "muted", text: "No query sent yet." }));
    return;
  }
  const cards = queries.map((query) => {
    let card = queryCards.get(query.read);
    if (!card) {
      card = queryCard(query.read);
      queryCards.set(query.read, card);
    }
    // The monitor sends every query in both modes but that for the streams, which "graph only"
    // does not send.
    card.querySelector(".every").textContent =
      query.every_seconds === null ? "not sent in graph only" : `every ${query.every_seconds} s`;
    const last = card.querySelector(".last");
    const when = `last sent ${new Date(query.sent).toLocaleTimeString()}`;
    last.textContent = query.error === null
      ? `${when}: ${duration(query.duration_seconds)}, ${query.rows} ${query.rows === 1 ? "row" : "rows"}`
      : `${when}: failed after ${duration(query.duration_seconds)}: ${query.error}`;
    last.classList.toggle("failed", query.error !== null);
    last.title = query.sent;
    const text = card.querySelector("pre");
    if (text.textContent !== query.text) text.textContent = query.text;
    return card;
  });
  [...list.children].filter((child) => !cards.includes(child)).forEach((child) => child.remove());
  cards.forEach((card, i) => {
    if (list.children[i] !== card) list.insertBefore(card, list.children[i] || null);
  });
}

async function refresh() {
  const error = document.getElementById("error");
  try {
    const response = await fetch("api/state");
    if (!response.ok) throw new Error(`the monitor answered HTTP ${response.status}`);
    const state = await response.json();
    drawTopology(state.modules, state.boxes, state.wires);
    drawModules(state.modules);
    drawBoxes(state.boxes, state.modules);
    drawHandovers(state.handovers);
    drawReading(state.reading);
    drawRefusals(state.refusals, state.reading.mode);
    drawDeadlock(state.possible_deadlock, state.reading.mode);
    drawQueries(state.queries);
    document.getElementById("on-modules").textContent =
      state.modules.reduce((sum, m) => sum + m.boxes.length, 0);
    document.getElementById("updated").textContent = `read ${new Date().toLocaleTimeString()}`;
    if (state.error) {
      error.textContent = `Could not read the knowledge graph: ${state.error}`;
      error.hidden = false;
    } else {
      error.hidden = true;
    }
  } catch (e) {
    error.textContent = `Could not reach the monitor: ${e.message}`;
    error.hidden = false;
  }
}

function schedule() {
  clearInterval(timer);
  if (document.getElementById("live").checked) {
    timer = setInterval(refresh, Number(document.getElementById("rate").value));
  }
}

document.getElementById("live").addEventListener("change", schedule);
document.getElementById("rate").addEventListener("change", schedule);
document.getElementById("refresh").addEventListener("click", refresh);
const follow = document.getElementById("follow-streams");
follow.addEventListener("change", () =>
  change("api/mode", { mode: follow.checked ? GRAPH_AND_STREAMS : GRAPH_ONLY }, "mode-message"));
document.getElementById("graph-seconds").addEventListener("change", applyInterval);
refresh();
schedule();
