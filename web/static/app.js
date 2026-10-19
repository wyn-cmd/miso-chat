// Chat page: character sidebar (desktop) or drawer (mobile), markdown messages, streaming replies,
// undo, usage counter, forced password change.
const $ = (id) => document.getElementById(id);
let me = null;
let current = null;   // character or group chat currently open
let busy = false;     // a turn is streaming: keep the character list and buttons locked

function setSidebar(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").hidden = !open;
  $("menuBtn").setAttribute("aria-expanded", String(open));
}

// mobile width uses the drawer; wider screens keep the sidebar beside the chat
function mobileNav() { return window.matchMedia("(max-width: 760px)").matches; }

function closeSidebar() { if (mobileNav()) setSidebar(false); }

// chat font is chosen per browser: sans-serif, Merriweather (serif) or monospace
const FONT_CHOICES = ["sans", "serif", "mono"];
// Chat text size, per browser (12px to 24px, default 15px). Only the message text and the input change;
// speaker names, notes and the thinking panel keep their own sizes.
const SIZE_MIN = 12, SIZE_MAX = 24, SIZE_DEFAULT = 15;
let chatSize = SIZE_DEFAULT;
function applySize(px) {
  const n = Math.min(SIZE_MAX, Math.max(SIZE_MIN, parseInt(px, 10) || SIZE_DEFAULT));
  chatSize = n;
  document.body.style.setProperty("--chat-size", n + "px");
  $("sizeLabel").textContent = n + "px";
  $("sizeDown").disabled = n <= SIZE_MIN;
  $("sizeUp").disabled = n >= SIZE_MAX;
  localStorage.setItem("rp_size", String(n));
  return n;
}

function applyFont(name) {
  const font = FONT_CHOICES.includes(name) ? name : "sans";
  document.body.classList.remove("font-sans", "font-serif", "font-mono");
  document.body.classList.add("font-" + font);
  $("fontSelect").value = font;
  localStorage.setItem("rp_font", font);
}

// Bubble colours: each character's colour from character.md. Shown as a badge around the name, or
// as the whole bubble (per browser). Text switches black or white by the colour's brightness.
let colorMap = {};        // name -> "#rrggbb"
let iconSet = new Set(); // characters that have an icon.png/jpg/webp on the server
const BUBBLE_CHOICES = ["badge", "bubble"];

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Text colour for a bubble of this colour: white on dark colours (black, navy, purple, crimson, brown),
// black on light ones (yellow, gold, silver, cyan). A luminance cut-off, not a pure contrast ratio, so
// mid-dark colours like purple still take white text, as wanted.
function readableText(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return "#e9eff9";
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.3 ? "#111010" : "#ffffff";
}

function applyBubbleStyle(mode) {
  const style = BUBBLE_CHOICES.includes(mode) ? mode : "badge";
  document.body.classList.remove("bubble-badge", "bubble-bubble");
  document.body.classList.add("bubble-" + style);
  $("bubbleSelect").value = style;
  localStorage.setItem("rp_bubble", style);
}

// sets the colour variables on a message box for the speaker, if the speaker has a colour
function colourLine(box, speaker) {
  const hex = colorMap[speaker];
  if (!hex) return;
  const text = readableText(hex);
  box.style.setProperty("--speaker", hex);
  box.style.setProperty("--speaker-text", text);
  // translucent chip for code inside a coloured bubble: darkens on light colours, lightens on dark ones
  box.style.setProperty("--chip", text === "#111010" ? "rgba(0,0,0,.14)" : "rgba(255,255,255,.16)");
  box.classList.add("coloured");
}


function setBusy(on) {
  busy = on;
  $("sendBtn").disabled = on;
  $("autoBtn").disabled = on;
  $("rollBtn").disabled = on;
  $("undoBtn").disabled = on;
  for (const b of document.querySelectorAll(".char-item")) b.disabled = on;
  for (const b of document.querySelectorAll(".turn-tools button")) b.disabled = on;
  $("memBtn").disabled = on;
}

function showPassword(note) {
  closeSidebar();
  document.body.classList.add("locked");
  $("chatSection").hidden = true;
  $("memSection").hidden = true;
  $("pwSection").hidden = false;
  $("pwNote").textContent = note || "";
  $("backBtn").hidden = !!(me && me.must_change);
}

function showChat() {
  document.body.classList.remove("locked");
  $("pwSection").hidden = true;
  $("chatSection").hidden = false;
}

function renderUsage() {
  if (!me || !me.usage) return;
  const u = me.usage;
  // two forms of the same line: the long one on desktop, a compact one for phones (CSS picks which shows)
  const fmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  const usage = $("usage");
  usage.textContent = "";
  const long = document.createElement("span");
  long.className = "u-long";
  long.textContent = `Requests today ${u.requests_today}/${u.request_cap} · tokens ${u.tokens_today.toLocaleString()}`;
  const short = document.createElement("span");
  short.className = "u-short";
  short.textContent = `${u.requests_today}/${u.request_cap} req · ${fmt.format(u.tokens_today)} tok`;
  usage.append(long, short);
}

function addLine(speaker, text, cls, time) {
  const box = el("div", null, "line " + (cls || ""));
  if (speaker) {
    const name = el("div", speaker, "speaker");
    box.appendChild(name);
    if (cls && cls.includes("theirs")) colourLine(box, speaker);
  }
  const body = el("div", null, "text");
  renderMarkdown(text, body);
  box.appendChild(body);
  if (time) addStamp(box, time);
  $("log").appendChild(box);
  $("log").scrollTop = $("log").scrollHeight;
  return box;
}

// Thinking panel: a collapsible block above a model's answer. Collapsed, it shows only the latest
// line of reasoning; open, it shows the whole thinking so far. It streams live either way.
function thinkingPanel(box) {
  let panel = box.querySelector(".think");
  if (panel) return panel;
  panel = el("details", null, "think");
  panel.open = false;
  const summary = el("summary", "Thinking", "think-summary");
  const preview = el("span", "", "think-preview");
  summary.appendChild(preview);
  const body = el("div", "", "think-body");
  panel.appendChild(summary);
  panel.appendChild(body);
  box.insertBefore(panel, box.querySelector(".text"));
  return panel;
}

function setThinking(box, text) {
  const panel = thinkingPanel(box);
  const body = panel.querySelector(".think-body");
  body.textContent = text;
  const lastLine = text.split("\n").map((l) => l.trim()).filter(Boolean).pop() || "";
  panel.querySelector(".think-preview").textContent = lastLine.length > 90 ? "… " + lastLine.slice(-88) : lastLine;
}

// Message timestamps: a small line at the bottom of each message, in the reader's local time.
function stampLabel(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return time;
  return d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + time;
}

function addStamp(box, iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return;
  const s = el("div", stampLabel(iso), "stamp");
  s.title = d.toLocaleString();
  box.appendChild(s);
}

// The open chat's icon and name above the chat window: the character's icon, or its initial if it has none.
function setChatHeader(name) {
  const title = $("chatTitle");
  if (title) title.textContent = name;
  const icon = $("chatIcon");
  if (!icon) return;   // an older cached page has no icon element; the chat must still load without it
  icon.textContent = "";
  icon.style.background = "";
  icon.className = "char-icon chat-icon" + (iconSet.has(name) ? "" : " placeholder");
  if (!name) return;
  if (iconSet.has(name)) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(name);
    icon.appendChild(img);
  } else {
    icon.textContent = name.charAt(0).toUpperCase();
    if (colorMap[name]) icon.style.background = colorMap[name];
  }
}

function renderHistory(messages) {
  $("log").textContent = "";
  if (!messages.length) {
    addLine("", "No messages yet. Say hello!", "note");
  } else {
    messages.forEach((m, i) => {
      const isEvent = m.speaker === "Narrator";   // random events are saved as user-side entries, shown as notes
      const box = addLine(m.speaker, m.text, isEvent ? "note event" : (m.role === "user" ? "mine" : "theirs"), m.time);
      box.dataset.idx = String(i);   // position in the saved chat, used to place the context marker
      addEditButton(box, i, m);
      if (m.role === "user" && !isEvent && me && me.persona) colourLine(box, me.persona.name);
      if (m.thinking) setThinking(box, m.thinking);
    });
  }
  placeTurnTools(messages);
  refreshContext();
}

// Editing a saved message: the text opens in a box with Save and Cancel. Saving changes the saved chat only; no model call.
function addEditButton(box, i, m) {
  const btn = el("button", "Edit", "ghost small edit-btn");
  btn.type = "button";
  btn.setAttribute("aria-label", "Edit this message");
  btn.addEventListener("click", () => startEdit(box, i, m));
  box.appendChild(btn);
}

function startEdit(box, i, m) {
  if (busy || !current) return;
  const who = current;
  const body = box.querySelector(".text");
  const area = el("textarea", null, "edit-box");
  area.value = m.text;
  area.rows = 4;
  area.maxLength = 4000;
  const bar = el("div", null, "edit-bar");
  const save = el("button", "Save", null);
  save.type = "button";
  const cancel = el("button", "Cancel", "ghost small");
  cancel.type = "button";
  bar.append(save, cancel);
  body.hidden = true;
  box.querySelector(".edit-btn").hidden = true;
  box.insertBefore(area, body.nextSibling);
  box.insertBefore(bar, area.nextSibling);
  area.focus();
  cancel.addEventListener("click", () => { if (who === current) loadHistory().catch(() => {}); });
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const data = await api("POST", "/api/edit", { character: who, index: i, text: area.value });
      if (who === current) renderHistory(data.messages);
    } catch (err) {
      save.disabled = false;
      $("chatErr").textContent = err.message;
    }
  });
}

// Settings sheet: this character's creativity, the rolling window, and export.
function loadCharSettings() {
  $("charSettings").hidden = !current;
  for (const id of ["tempSlider", "tempReset", "winSlider", "winReset", "exportMd", "exportTxt"]) $(id).disabled = !current;
  $("charMsg").textContent = "";
  if (!current) return;
  api("GET", "/api/character-settings?character=" + encodeURIComponent(current))
    .then((d) => applyCharSettings(d))
    .catch((err) => { $("charMsg").textContent = err.message; });
}

function applyCharSettings(d) {
  const t = d.temperature == null ? d.temperature_default : d.temperature;
  $("tempSlider").value = t;
  $("tempVal").textContent = Number(t).toFixed(2) + (d.temperature == null ? " (default)" : "");
  $("tempReset").disabled = d.temperature == null;
  const w = d.window == null ? d.window_default : d.window;
  $("winSlider").value = w;
  $("winVal").textContent = w + (d.window == null ? " (default)" : "");
  $("winReset").disabled = d.window == null;
  $("winNote").textContent = `Messages from the chat sent to the model with each reply. Default: ${d.window_defaults.solo} for 1:1 chats, ${d.window_defaults.group} for group chats.`;
}

async function saveCharSettings(patch) {
  if (!current) return;
  try {
    applyCharSettings(await api("POST", "/api/character-settings", { character: current, ...patch }));
    $("charMsg").textContent = "Saved. Applies from your next message.";
  } catch (err) {
    $("charMsg").textContent = err.message;
    loadCharSettings();
  }
}

async function saveWindow(value) {
  try {
    await api("POST", "/api/settings", { window: value });
    loadCharSettings();
    $("charMsg").textContent = "Saved. Applies from your next message.";
  } catch (err) {
    $("charMsg").textContent = err.message;
  }
}

async function exportChat(fmt) {
  if (!current) return;
  $("charMsg").textContent = "";
  try {
    const r = await fetch("/api/export?character=" + encodeURIComponent(current) + "&format=" + fmt, { credentials: "same-origin" });
    if (!r.ok) throw new Error("Export failed (" + r.status + ").");
    const blob = await r.blob();
    const named = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = named ? named[1] : "chat." + fmt;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (err) {
    $("charMsg").textContent = err.message;
  }
}

$("tempSlider").addEventListener("input", () => { $("tempVal").textContent = Number($("tempSlider").value).toFixed(2); });
$("tempSlider").addEventListener("change", () => saveCharSettings({ temperature: Number($("tempSlider").value) }));
$("tempReset").addEventListener("click", () => saveCharSettings({ temperature: null }));
$("winSlider").addEventListener("input", () => { $("winVal").textContent = $("winSlider").value; });
$("winSlider").addEventListener("change", () => saveWindow(Number($("winSlider").value)));
$("winReset").addEventListener("click", () => saveWindow(null));
$("exportMd").addEventListener("click", () => exportChat("md"));
$("exportTxt").addEventListener("click", () => exportChat("txt"));

// Regenerate and swipe. The last exchange's reply can be written again; saved alternatives are shown with ◀ ▶.
function removeTurnTools() {
  for (const n of document.querySelectorAll(".turn-tools")) n.remove();
}

function placeTurnTools(messages) {
  removeTurnTools();
  let last = -1;
  messages.forEach((m, i) => { if (m.role === "user" && m.speaker !== "Narrator") last = i; });
  if (last < 0 || !messages.slice(last + 1).some((m) => m.role !== "user")) return;
  const v = messages[last].variant || { index: 1, count: 1, limit: 30 };
  const bar = el("div", null, "turn-tools");
  const prev = el("button", "\u25C0", "ghost small");
  prev.type = "button";
  prev.setAttribute("aria-label", "Previous reply");
  prev.disabled = busy || v.index <= 1;
  prev.addEventListener("click", () => swipe(-1));
  const count = el("span", `${v.index} / ${v.count}`, "muted turn-count");
  const next = el("button", "\u25B6", "ghost small");
  next.type = "button";
  next.setAttribute("aria-label", "Next reply");
  next.disabled = busy || v.index >= v.count;
  next.addEventListener("click", () => swipe(1));
  const regen = el("button", "\u21BB Regenerate", "ghost small");
  regen.type = "button";
  const atLimit = v.count - 1 >= v.limit;
  regen.title = atLimit ? "This message has reached its regeneration limit."
    : "Write a new reply to your last message. The current reply is kept; swipe back to it any time.";
  regen.disabled = busy || atLimit;
  regen.addEventListener("click", regenerate);
  bar.append(prev, count, next, regen);
  $("log").appendChild(bar);
}

// Context marker: "context window ends here" sits above the oldest message the next prompt will include,
// with an estimate of that prompt beside it. The server supplies the window and the stored parts; the text
// still in the input box is added here as it is typed. Estimates are about 4 characters per token.
let ctxInfo = null;
async function refreshContext() {
  const name = current;
  if (!name) { ctxInfo = null; return; }
  try {
    const info = await api("GET", "/api/context?character=" + encodeURIComponent(name));
    if (name !== current) return;   // the user switched chats while this was loading
    ctxInfo = info;
    placeMarker();
  } catch (err) {
    ctxInfo = null;
  }
}

function nextPromptTokens() {
  if (!ctxInfo) return null;
  const typed = Math.ceil(($("msg").value || "").length / 4);
  return ctxInfo.system_tokens + ctxInfo.window_tokens + typed;
}

function updateEstimate() {
  const label = document.querySelector("#log .ctx-est");
  if (!label) return;
  const n = nextPromptTokens();
  label.textContent = n === null ? "" : "next prompt \u2248 " + n.toLocaleString() + " tokens";
}

function placeMarker() {
  document.querySelectorAll("#log .ctx-marker").forEach((m) => m.remove());
  if (!ctxInfo) return;
  const marker = el("div", null, "ctx-marker");
  marker.appendChild(el("span", "", "ctx-rule"));
  marker.appendChild(el("span", "context window ends here", "ctx-label"));
  marker.appendChild(el("span", "", "ctx-est"));
  marker.appendChild(el("span", "", "ctx-rule"));
  // above the oldest message the next prompt includes; a chat shorter than the window puts it at the top
  const target = document.querySelector('#log .line[data-idx="' + ctxInfo.window_start + '"]');
  $("log").insertBefore(marker, target || null);
  updateEstimate();
}


function iconFor(name) {
  // icon beside the name: the character's (or persona's) icon when it has one, otherwise a placeholder initial
  const icon = el("span", "", "char-icon" + (iconSet.has(name) ? "" : " placeholder"));
  if (iconSet.has(name)) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(name);
    img.loading = "lazy";
    icon.appendChild(img);
  } else {
    icon.textContent = name.charAt(0).toUpperCase();
  }
  return icon;
}

function editPencil(name) {
  const edit = el("span", "✎", "char-edit");
  edit.title = "Edit " + name;
  edit.setAttribute("role", "button");
  edit.addEventListener("click", (e) => { e.stopPropagation(); openEditor(name).catch((err) => { $("chatErr").textContent = err.message; }); });
  return edit;
}

function charButton(name) {
  const b = el("button", "", "char-item");
  b.type = "button";
  b.dataset.name = name;
  b.disabled = busy;
  if (colorMap[name]) b.style.setProperty("--dot", colorMap[name]);
  b.appendChild(iconFor(name));
  b.appendChild(el("span", name, "char-name"));
  if (me && me.is_admin) b.appendChild(editPencil(name));
  b.addEventListener("click", () => pickCharacter(name));
  return b;
}



function personaButton(p) {
  const b = el("button", "", "char-item persona-item");
  b.type = "button";
  b.dataset.persona = p.name;
  b.disabled = busy;
  if (p.color) b.style.setProperty("--dot", p.color);
  if (me && me.persona && me.persona.name === p.name) b.classList.add("active");
  b.appendChild(iconFor(p.name));
  b.appendChild(el("span", p.name, "char-name"));
  if (me && me.is_admin) b.appendChild(editPencil(p.name));
  b.addEventListener("click", () => choosePersona(p.name));
  return b;
}

function renderPersonas(personas) {
  const list = $("personaList");
  list.textContent = "";
  const none = el("button", "No persona", "char-item persona-item");
  none.type = "button";
  none.disabled = busy;
  if (!me || !me.persona) none.classList.add("active");
  none.addEventListener("click", () => choosePersona(""));
  list.appendChild(none);
  for (const p of personas) list.appendChild(personaButton(p));
  $("personaBox").hidden = personas.length === 0;
}

// Settings: thinking on or off, and which model answers. Both apply from the next turn.
function renderModels() {
  const sel = $("modelSelect");
  sel.textContent = "";
  sel.appendChild(el("option", "Automatic (Gemini fallback chain)", null));
  sel.options[0].value = "";
  for (const name of (me && me.models && me.models.extra) || []) {
    const o = el("option", name, null);
    o.value = name;
    sel.appendChild(o);
  }
  sel.value = (me && me.model) || "";
  $("thinkToggle").checked = !!(me && me.thinking);
}

async function saveSettings(patch) {
  try {
    const data = await api("POST", "/api/settings", patch);
    me.thinking = data.thinking;
    me.model = data.model;
    renderModels();
    $("settingsMsg").textContent = "Saved. Applies from your next message.";
  } catch (err) {
    $("settingsMsg").textContent = err.message;
    renderModels();
  }
}


async function choosePersona(name) {
  if (busy) return;
  try {
    const data = await api("POST", "/api/persona", { persona: name });
    me.persona = data.persona;
    renderPersonas(data.personas);
    // the cached chat sessions were dropped on the server; reopen the current chat
    if (current) await loadHistory();
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}


function markActive() {
  for (const b of document.querySelectorAll("#charList .char-item, #groupList .char-item")) {
    const on = b.dataset.name === current;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "true");
    else b.removeAttribute("aria-current");
  }
  setChatHeader(current || "");
}

async function loadCharacters() {
  const data = await api("GET", "/api/characters");
  colorMap = data.colors || {};
  iconSet = new Set(data.icons || []);
  renderPersonas(data.personas || []);
  const solo = $("charList");
  const groups = $("groupList");
  solo.textContent = "";
  groups.textContent = "";
  for (const name of data.solo) solo.appendChild(charButton(name));
  for (const g of data.groups) groups.appendChild(charButton(g.name));
  $("groupBox").hidden = data.groups.length === 0;
  const all = [...data.solo, ...data.groups.map((g) => g.name)];
  const saved = localStorage.getItem("rp_character");
  const start = all.includes(saved) ? saved : (data.solo[0] || null);
  if (start) await pickCharacter(start);
  else { current = null; markActive(); }
}

async function pickCharacter(name) {
  if (busy) return;
  current = name;
  localStorage.setItem("rp_character", name);
  markActive();
  closeSidebar();
  showMemory(false);
  $("chatErr").textContent = "";
  await loadHistory();
}

async function loadHistory() {
  const name = current;
  if (!name) return;
  const data = await api("GET", "/api/history?character=" + encodeURIComponent(name));
  if (name !== current) return;   // the user switched characters while this was loading
  renderHistory(data.messages);
}


// Smooth reply streaming. The server sends a reply in chunks, so each reply bubble reveals its text one character at a
// time, at the speed the text is arriving (measured over the last two seconds). It never falls more than about a second
// behind, and it catches up quickly once the reply is complete. Thinking panels and saved history are not affected.
function makeReveal(render) {
  const clock = () => performance.now();
  const st = { target: "", shown: 0, last: null, arrivals: [], rate: 120, raf: null, lastFrame: 0,
               finishing: false, resolveDone: null, cancelled: false };
  const paint = () => {
    if (st.cancelled) return;
    const text = st.target.slice(0, st.shown);
    if (text !== st.last) { st.last = text; render(text); }
  };
  const schedule = () => {
    if (st.raf || st.cancelled) return;
    // animation frames pause in hidden tabs, so a hidden page keeps going on a timer
    const visible = typeof requestAnimationFrame === "function" && document.visibilityState === "visible";
    st.raf = visible ? requestAnimationFrame(step) : setTimeout(() => step(clock()), 16);
  };
  function step(now) {
    st.raf = null;
    if (st.cancelled) return;
    const dt = st.lastFrame ? Math.min(0.1, (now - st.lastFrame) / 1000) : 0.016;
    st.lastFrame = now;
    const backlog = st.target.length - st.shown;
    if (backlog > 0) {
      let speed = Math.max(40, Math.min(600, st.rate));
      speed = Math.max(speed, backlog);                        // the backlog drains within about a second
      if (st.finishing) speed = Math.max(speed, backlog / 0.35); // the reply is complete: catch up
      st.shown = Math.min(st.target.length, st.shown + Math.max(1, Math.round(speed * dt)));
      paint();
    }
    if (st.shown < st.target.length) schedule();
    else if (st.resolveDone) { const done = st.resolveDone; st.resolveDone = null; done(); }
  }
  return {
    set(text) {
      if (st.cancelled) return;
      if (!text) { st.target = ""; st.shown = 0; st.arrivals = []; st.last = ""; render(""); return; }
      if (!text.startsWith(st.target.slice(0, st.shown))) st.shown = 0;   // a retry rewrote the text
      st.target = text;
      const now = clock();
      st.arrivals.push([now, text.length]);
      st.arrivals = st.arrivals.filter((a) => now - a[0] <= 2000);
      const first = st.arrivals[0], last = st.arrivals[st.arrivals.length - 1];
      if (st.arrivals.length >= 2 && last[0] - first[0] >= 250) st.rate = (last[1] - first[1]) / ((last[0] - first[0]) / 1000);
      schedule();
    },
    finish() {
      return new Promise((resolve) => {
        if (st.cancelled) { resolve(); return; }
        if (st.shown >= st.target.length) { paint(); resolve(); return; }
        st.finishing = true;
        st.resolveDone = resolve;
        setTimeout(() => {   // safety: never hold the page for more than 1.5 seconds
          if (st.resolveDone === resolve) { st.resolveDone = null; st.shown = st.target.length; paint(); resolve(); }
        }, 1500);
        schedule();
      });
    },
    cancel() {
      st.cancelled = true;
      if (st.resolveDone) { const done = st.resolveDone; st.resolveDone = null; done(); }
    },
  };
}

async function send(ev) {
  ev.preventDefault();
  if (busy || !current) return;
  const text = $("msg").value.trim();
  if (!text) return;
  const who = current;
  setBusy(true);
  $("chatErr").textContent = "";
  $("msg").value = "";
  const mine = me && me.persona ? me.persona.name : "You";
  const mineBox = addLine(mine, text, "mine", new Date().toISOString());
  if (me && me.persona) colourLine(mineBox, me.persona.name);
  await streamTurn("/api/chat", { character: who, message: text }, who);
}

// Stream one turn (a new message, or a regenerated reply) into the log, then reload the saved chat.
async function streamTurn(path, body, who) {
  setBusy(true);
  $("chatErr").textContent = "";
  removeTurnTools();

  // one streaming bubble per speaker; a reset (empty text) means a retry started, so the text clears
  const bubbles = {};
  let thinking = addLine("", "mrrp~ thinking…", "thinking");
  const dropThinking = () => { if (thinking) { thinking.remove(); thinking = null; } };
  const reveals = {};
  const ensureBubble = (speaker) => {
    if (bubbles[speaker]) return;
    bubbles[speaker] = addLine(speaker, "", "theirs streaming");
    const node = bubbles[speaker].querySelector(".text");
    reveals[speaker] = makeReveal((txt) => {
      renderMarkdown(txt || "mrrp~ thinking…", node);
      $("log").scrollTop = $("log").scrollHeight;
    });
  };
  const show = (speaker, t) => {
    dropThinking();
    ensureBubble(speaker);
    reveals[speaker].set(t);
  };
  const clearBubbles = () => {
    for (const k of Object.keys(reveals)) reveals[k].cancel();
    for (const k of Object.keys(bubbles)) bubbles[k].remove();
    dropThinking();
  };

  let flushing = Promise.resolve();
  try {
    await streamChat(body, {
      delta(p) { if (who === current) show(p.speaker, p.text); },
      thinking(p) {
        if (who !== current) return;
        dropThinking();
        ensureBubble(p.speaker);
        setThinking(bubbles[p.speaker], p.text);
        $("log").scrollTop = $("log").scrollHeight;
      },
      done(data) {
        flushing = Promise.all(Object.keys(reveals).map((k) => reveals[k].finish())).then(() => {
          clearBubbles();
          if (who !== current) return;
          for (const r of data.replies) {
            const box = addLine(r.speaker, r.text, "theirs", new Date().toISOString());
            if (r.thinking) setThinking(box, r.thinking);
          }
          if (data.selector_note) addLine("", data.selector_note, "note");
        });
      },
      command(c) {
        clearBubbles();
        if (who !== current) return;
        if (c.notice) addLine("", c.notice, "note");
        if (c.reload) loadHistory().catch(() => {});
      },
      usage(u) { me.usage = u; renderUsage(); },
      error(err) {
        clearBubbles();
        $("chatErr").textContent = err.message;
        if (err.code === "password_change_required") showPassword(err.message);
      },
    }, path);
  } catch (err) {
    clearBubbles();
    $("chatErr").textContent = err.message;
    if (err.code === "password_change_required") showPassword(err.message);
  } finally {
    await flushing.catch(() => {});
    if (who === current) await loadHistory().catch(() => {});   // reload so positions and the marker match the saved chat
    setBusy(false);
    $("msg").focus();
  }
}

// Regenerate: drop the old reply from the view (the server keeps it as an alternative) and stream a new one.
async function regenerate() {
  if (busy || !current) return;
  let anchor = null;
  for (const n of $("log").children) if (n.classList.contains("mine")) anchor = n;
  if (!anchor) { $("chatErr").textContent = "Nothing to regenerate yet."; return; }
  while (anchor.nextSibling) anchor.nextSibling.remove();
  await streamTurn("/api/regenerate", { character: current }, current);
}

// Swipe: show the previous (-1) or next (+1) saved reply to the last message.
async function swipe(direction) {
  if (busy || !current) return;
  const who = current;
  setBusy(true);
  $("chatErr").textContent = "";
  try {
    const data = await api("POST", "/api/swipe", { character: who, direction });
    if (who === current) renderHistory(data.messages);
  } catch (err) {
    if (who === current) $("chatErr").textContent = err.message;
  } finally {
    setBusy(false);
  }
}

async function undo() {
  if (busy || !current) return;
  $("chatErr").textContent = "";
  try {
    const data = await api("POST", "/api/undo", { character: current });
    renderHistory(data.messages);
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}

async function changePassword(ev) {
  ev.preventDefault();
  $("pwErr").textContent = "";
  try {
    await api("POST", "/api/password", { old: $("pwOld").value, new: $("pwNew").value });
    $("pwOld").value = "";
    $("pwNew").value = "";
    me = await api("GET", "/api/me");
    showChat();
    renderUsage();
    await loadCharacters();
  } catch (err) {
    $("pwErr").textContent = err.message;
  }
}

async function init() {
  try {
    me = await api("GET", "/api/me");
  } catch (err) {
    $("chatErr").textContent = err.message;
    return;
  }
  $("who").textContent = me.username;
  $("menuBtn").setAttribute("aria-expanded", String(!mobileNav()));
  if (me.is_admin) {
    $("adminLink").hidden = false;
    $("sandboxLink").hidden = false;
    $("editBox").hidden = false;
  }
  renderModels();
  if (me.must_change) {
    showPassword("Your account was made with a starting password. Set your own to continue.");
    return;
  }
  showChat();
  renderUsage();
  await loadCharacters();
}

$("chatForm").addEventListener("submit", send);

async function refreshMe() {
  try { me = await api("GET", "/api/me"); renderUsage(); } catch (err) { /* the usage bar keeps its last values */ }
}

// Auto-fill: the model drafts the user's next message into the box. The user reads it, edits it, then sends it.
async function autoFill() {
  if (busy || !current) return;
  const who = current;
  if ($("msg").value.trim()) { $("chatErr").textContent = "Clear the message box first, then Auto-fill."; return; }
  setBusy(true);
  $("chatErr").textContent = "";
  try {
    const data = await api("POST", "/api/autofill", { character: who });
    if (who === current && !$("msg").value.trim()) {
      $("msg").value = data.text;
      $("msg").dispatchEvent(new Event("input"));
      $("msg").focus();
    }
  } catch (err) {
    if (who === current) $("chatErr").textContent = err.message;
  } finally {
    setBusy(false);
    refreshMe();
  }
}

// Roll: the model adds a random event to the scene as a narrator note. The next reply takes it into account.
async function rollEvent() {
  if (busy || !current) return;
  const who = current;
  setBusy(true);
  $("chatErr").textContent = "";
  try {
    const data = await api("POST", "/api/roll", { character: who });
    if (who === current) addLine("Narrator", data.event.text, "note event", new Date().toISOString());
  } catch (err) {
    if (who === current) $("chatErr").textContent = err.message;
  } finally {
    setBusy(false);
    refreshMe();
  }
}

$("autoBtn").addEventListener("click", autoFill);
$("rollBtn").addEventListener("click", rollEvent);
$("msg").addEventListener("input", updateEstimate);

// Phones: the on-screen keyboard covers the lower part of the page. Fit the app to the area still visible
// (visualViewport), so the message box stays above the keyboard and the newest messages stay in view.
// Checked on every resize and scroll, and every 300ms as a backstop, since not every browser reports the
// keyboard through those events. Styles are only written when the visible area has changed.
let lastFit = "";
function fitToVisibleArea() {
  const vv = window.visualViewport;
  const body = document.body;
  const phone = window.matchMedia("(max-width: 760px)").matches;
  const key = phone && vv ? `${Math.round(vv.offsetTop)}x${Math.round(vv.height)}` : "off";
  if (key === lastFit) return;
  lastFit = key;
  if (key === "off") {
    body.classList.remove("fit-viewport");
    body.style.top = "";
    body.style.height = "";
    return;
  }
  const log = $("log");
  const wasAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  body.classList.add("fit-viewport");
  body.style.top = vv.offsetTop + "px";
  body.style.height = vv.height + "px";
  if (wasAtBottom) log.scrollTop = log.scrollHeight;
}
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", fitToVisibleArea);
  window.visualViewport.addEventListener("scroll", fitToVisibleArea);
}
window.addEventListener("resize", fitToVisibleArea);
setInterval(fitToVisibleArea, 300);
fitToVisibleArea();
$("msg").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chatForm").requestSubmit(); }
});
$("menuBtn").addEventListener("click", () => {
  if (mobileNav()) {
    setSidebar(!$("sidebar").classList.contains("open"));
  } else {
    const collapsed = document.querySelector(".shell").classList.toggle("collapsed");
    $("menuBtn").setAttribute("aria-expanded", String(!collapsed));
  }
});
$("scrim").addEventListener("click", closeSidebar);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSidebar(); });
$("fontSelect").addEventListener("change", (e) => applyFont(e.target.value));
$("bubbleSelect").addEventListener("change", (e) => applyBubbleStyle(e.target.value));
applyFont(localStorage.getItem("rp_font"));
$("sizeDown").addEventListener("click", () => applySize(chatSize - 1));
$("sizeUp").addEventListener("click", () => applySize(chatSize + 1));
applySize(localStorage.getItem("rp_size") || SIZE_DEFAULT);
applyBubbleStyle(localStorage.getItem("rp_bubble"));
$("thinkToggle").addEventListener("change", (e) => saveSettings({ thinking: e.target.checked }));
$("modelSelect").addEventListener("change", (e) => saveSettings({ model: e.target.value }));
$("editNewBtn").addEventListener("click", () => openEditor(null));
$("undoBtn").addEventListener("click", undo);
$("pwForm").addEventListener("submit", changePassword);
$("pwBtn").addEventListener("click", () => showPassword(""));
$("backBtn").addEventListener("click", () => { showChat(); renderUsage(); });
$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (e) { /* ignore */ }
  location.href = "/login";
});
// Memory: the character's memory bank and pinned facts, editable here. Saving takes effect on the next reply.
let memFor = null;
let memLoaded = { memory: "", pinned: "" };

function showMemory(on) {
  if (on && !$("settingsSheet").hidden) $("sheetClose").click();   // on phones, Memory is opened from the Settings sheet
  $("memSection").hidden = !on;
  $("chatSection").hidden = on;
}

async function openMemory() {
  if (busy || !current) return;
  const who = current;
  $("chatErr").textContent = "";
  try {
    const data = await api("GET", "/api/memory?character=" + encodeURIComponent(who));
    memFor = who;
    memLoaded = { memory: data.memory, pinned: data.pinned, summary: data.summary || "" };
    $("memText").value = data.memory;
    $("sumText").value = data.summary || "";
    $("pinText").value = data.pinned;
    $("memTitle").textContent = "Memory: " + who;
    $("memErr").textContent = "";
    $("memOk").textContent = "";
    showMemory(true);
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}

async function saveMemory() {
  if (!memFor) return;
  $("memErr").textContent = "";
  $("memOk").textContent = "";
  try {
    const data = await api("POST", "/api/memory", {
      character: memFor, memory: $("memText").value, pinned: $("pinText").value, summary: $("sumText").value,
    });
    memLoaded = { memory: data.memory, pinned: data.pinned, summary: data.summary };
    $("memOk").textContent = "Saved. The next reply uses this.";
  } catch (err) {
    $("memErr").textContent = err.message;
  }
}

async function refreshMemory() {
  if (!memFor || busy) return;
  if ($("memText").value !== memLoaded.memory || $("pinText").value !== memLoaded.pinned || $("sumText").value !== memLoaded.summary) {
    $("memErr").textContent = "Save your edits first, then refresh.";
    return;
  }
  $("memErr").textContent = "";
  $("memOk").textContent = "Refreshing from the chat…";
  try {
    const data = await api("POST", "/api/chat", { character: memFor, message: "/save" });
    const r = await api("GET", "/api/memory?character=" + encodeURIComponent(memFor));
    memLoaded = { memory: r.memory, pinned: r.pinned, summary: r.summary };
    $("memText").value = r.memory;
    $("sumText").value = r.summary || "";
    $("memOk").textContent = (data.command && data.command.notice) || "Refreshed.";
  } catch (err) {
    $("memErr").textContent = err.message;
    $("memOk").textContent = "";
  }
}

$("memBtn").addEventListener("click", openMemory);
$("memBack").addEventListener("click", () => showMemory(false));
$("memSave").addEventListener("click", saveMemory);
$("memRefresh").addEventListener("click", refreshMemory);

init();

// Highlight colour picker in the Settings sheet. Saved in this browser only.
(function accentPicker() {
  const pick = document.getElementById("accentPick");
  pick.value = applyAccent(savedAccent());
  pick.addEventListener("input", (e) => {
    const v = applyAccent(e.target.value);
    try { localStorage.setItem("rp_accent", v); } catch (err) { /* private mode: applies for this page only */ }
  });
  document.getElementById("accentReset").addEventListener("click", () => {
    try { localStorage.removeItem("rp_accent"); } catch (err) { /* ignore */ }
    pick.value = applyAccent(null);
  });
})();

// Phone settings sheet. On phones the chat bar's font, name badge and size controls, and the sidebar's model, thinking and
// character-creation controls move into a sheet opened from the Settings button beside the usage line. On desktop they go
// straight back to their places. The controls keep their listeners when they move.
(function settingsSheet() {
  const phone = window.matchMedia("(max-width: 760px)");
  const sheet = document.getElementById("settingsSheet");
  const btn = document.getElementById("settingsBtn");
  const closeBtn = document.getElementById("sheetClose");
  const parts = [["fontSelect", "font"], ["bubbleSelect", "badge"], ["sizeCtl", "size"],
                 ["modelSection", "model"], ["editBox", "characters"], ["personaBox", "persona"], ["memBtn", "memory"]].map(([id, slot]) => {
    const node = document.getElementById(id);
    const home = document.createComment("home:" + id);   // stays where the control belongs on desktop
    node.parentNode.insertBefore(home, node);
    return { node, home, slot };
  });

  function open() {
    sheet.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    loadCharSettings();
    closeBtn.focus();
  }
  function close(refocus) {
    if (sheet.hidden) return;
    sheet.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (refocus) btn.focus();
  }
  function place() {
    for (const p of parts) {
      if (phone.matches) {
        const slot = sheet.querySelector('[data-slot="' + p.slot + '"]');
        if (p.node.parentNode !== slot) slot.appendChild(p.node);
      } else if (p.home.nextSibling !== p.node) {
        p.home.parentNode.insertBefore(p.node, p.home.nextSibling);
      }
    }
    if (!phone.matches) close(false);
  }

  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", () => close(true));
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(true); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(true); });
  document.getElementById("editNewBtn").addEventListener("click", () => close(false));
  if (phone.addEventListener) phone.addEventListener("change", place); else phone.addListener(place);
  place();
})();

// Phone composer: one line high by default, growing to about two and a half lines as the text wraps, then scrolling.
// Desktop keeps its fixed box. The height is worked out again on input, after a send empties the box, and when the text
// size, font or screen size changes.
(function growComposer() {
  const phone = window.matchMedia("(max-width: 760px)");
  const box = document.getElementById("msg");
  const form = document.getElementById("chatForm");
  function fit() {
    if (!phone.matches) { box.style.height = ""; box.style.overflowY = ""; return; }
    const cs = getComputedStyle(box);
    const borders = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    const edge = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + borders;
    const line = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.35;
    box.style.height = "0px";
    const natural = box.value ? box.scrollHeight + borders : 0;   // empty: one line (the placeholder can wrap and is not text)
    const low = line + edge;
    const high = line * 2.5 + edge;
    box.style.height = Math.min(high, Math.max(low, natural)) + "px";
    box.style.overflowY = natural > high ? "auto" : "hidden";
  }
  box.addEventListener("input", fit);
  form.addEventListener("submit", () => setTimeout(fit, 0));   // send() empties the box
  document.getElementById("sizeCtl").addEventListener("click", fit);
  document.getElementById("fontSelect").addEventListener("change", () => setTimeout(fit, 0));
  window.addEventListener("resize", fit);
  if (phone.addEventListener) phone.addEventListener("change", fit); else phone.addListener(fit);
  fit();
})();

// Phone header and chat bar. The character's icon and name take the place of "Miso Chat" in the header, and Admin,
// Password and Log out move into the character drawer. While the password screen is up (body.locked) the account buttons
// stay in the header, because the drawer is hidden then. Desktop keeps everything where it was.
(function phoneHeader() {
  const phone = window.matchMedia("(max-width: 760px)");
  const headerChat = document.getElementById("headerChat");
  const accountSlot = document.getElementById("accountSlot");
  const section = document.getElementById("chatSection");
  const parts = [["chatIcon", "chat"], ["chatTitle", "chat"], ["undoBtn", "chat"], ["rollBtn", "chat"], ["settingsBtn", "chat"], ["adminLink", "account"], ["pwBtn", "account"], ["logoutBtn", "account"]]
    .map(([id, group]) => {
      const node = document.getElementById(id);
      const home = document.createComment("home:" + id);   // where the control belongs on desktop
      node.parentNode.insertBefore(home, node);
      return { node, home, group };
    });

  function place() {
    const locked = document.body.classList.contains("locked");
    for (const p of parts) {
      let dest = null;
      if (phone.matches && p.group === "chat") dest = headerChat;
      if (phone.matches && p.group === "account" && !locked) dest = accountSlot;
      if (dest) {
        if (p.node.parentNode !== dest) dest.appendChild(p.node);
      } else if (p.home.nextSibling !== p.node) {
        p.home.parentNode.insertBefore(p.node, p.home.nextSibling);
      }
    }
  }
  const gear = document.getElementById("settingsBtn");
  const placeGear = () => { if (phone.matches) headerChat.appendChild(gear); };
  const sync = () => document.body.classList.toggle("chat-open", !section.hidden);
  new MutationObserver(sync).observe(section, { attributes: true, attributeFilter: ["hidden"] });
  const placeAll = () => { place(); placeGear(); };
  new MutationObserver(placeAll).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  if (phone.addEventListener) phone.addEventListener("change", placeAll); else phone.addListener(placeAll);
  sync();
  placeAll();
})();
