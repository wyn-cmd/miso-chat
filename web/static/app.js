// Chat page: character sidebar (desktop) or drawer (mobile), markdown messages, streaming replies,
// undo, usage counter, forced password change.
const $ = (id) => document.getElementById(id);
let me = null;
let current = null;   // character or group chat currently open
let favSet = new Set();            // characters this account has starred
let popularSet = new Set();        // top 10% of characters by user-sent messages
let editableSet = new Set();   // characters this account may change (shows the sidebar pencil)
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
let bgIds = {};   // solo character -> ids of its backgrounds on the server
let defaultArt = [];   // shared default backgrounds, used when a character has none
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

const TOKEN_WINDOWS = [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"]];

function windowShare(w) {
  return w.limit > 0 ? w.used / w.limit : 1;
}

function renderUsage() {
  // Memory is an admin switch; hide its button when it is off.
  $("memBtn").hidden = !!(me && me.features && me.features.memory === false);
  $("pinCard").hidden = $("memBtn").hidden;   // pinned facts are part of the memory bank
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
  // token bar shows the window closest to its limit, the one that will block first; hidden for admins with no limits
  const meter = $("tokenBar");
  const tv = u.token_budgets;
  let tight = null;
  for (const [kind, title] of TOKEN_WINDOWS) {
    const w = tv && tv[kind];
    if (w && (!tight || windowShare(w) > windowShare(tight.w))) tight = { kind, title, w };
  }
  meter.hidden = !tight;
  meter.classList.toggle("warn", !!tight && windowShare(tight.w) >= BUDGET_WARN_SHARE);
  if (tight) {
    // the bar shows what is left: it shrinks as tokens are used
    const { title, w } = tight;
    const left = Math.max(0, w.limit - w.used);
    const pct = Math.max(0, Math.min(100, 100 - windowShare(w) * 100));
    $("tokenFill").style.width = pct.toFixed(1) + "%";
    $("tokenText").textContent = `${fmt.format(left)} left`;
    meter.title = `${title} token budget: ${left.toLocaleString()} of ${w.limit.toLocaleString()} tokens left, ${w.reset}`;
  }
  renderBudgetAlert(tight);
}

// A window at this share of its budget raises the warning; the page keeps the warning closed until the window drops back under it.
const BUDGET_WARN_SHARE = 0.8;
let budgetAlertClosed = null;   // the window kind the viewer closed the warning for

function renderBudgetAlert(tight) {
  const box = $("budgetAlert");
  if (!tight || windowShare(tight.w) < BUDGET_WARN_SHARE) budgetAlertClosed = null;
  if (!tight || windowShare(tight.w) < BUDGET_WARN_SHARE || budgetAlertClosed === tight.kind) {
    box.hidden = true;
    return;
  }
  const { kind, title, w } = tight;
  const used = windowShare(w) >= 1;
  const left = Math.max(0, w.limit - w.used);
  $("budgetAlertText").textContent = used
    ? `The ${title.toLowerCase()} token budget is used up. New messages are blocked until it resets: ${w.reset}.`
    : `You have ${Math.round(100 - windowShare(w) * 100)}% of your ${title.toLowerCase()} token budget left (${left.toLocaleString("en")} tokens), ${w.reset}.`;
  box.classList.toggle("over", used);
  box.dataset.kind = kind;
  box.hidden = false;
}

$("budgetAlertClose").addEventListener("click", () => {
  budgetAlertClosed = $("budgetAlert").dataset.kind || null;
  $("budgetAlert").hidden = true;
});

function usageWindow(title, w) {
  const fmtN = (n) => n.toLocaleString("en");
  const shown = Math.min(windowShare(w) * 100, 100);
  const left = Math.max(0, w.limit - w.used);
  const box = el("div", null, "usage-window");
  const head = el("div", null, "usage-window-head");
  head.append(el("span", title, "muted"), el("span", `${shown.toFixed(1)}% used`, "usage-window-pct"));
  const track = el("div", null, "bar-track");
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", `${title} token usage`);
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", String(Math.round(shown)));
  const fill = el("div", null, "bar-fill");
  fill.style.width = shown.toFixed(1) + "%";
  track.append(fill);
  box.append(head, track,
    el("p", `${fmtN(w.used)} of ${fmtN(w.limit)} tokens used`),
    el("p", `${fmtN(left)} tokens left · ${w.reset}.`, "muted"));
  if (w.used >= w.limit) box.append(el("p", `The ${title.toLowerCase()} budget is used up. New messages are blocked until it resets.`, "error-msg"));
  return box;
}

function renderTokenUsage(usage) {
  const body = $("tokenUsageBody");
  body.textContent = "";
  if (!usage) {
    body.append(el("p", "Usage is not available right now.", "muted"));
    return;
  }
  const tv = usage.token_budgets || {};
  const windows = TOKEN_WINDOWS.filter(([kind]) => tv[kind]);
  if (!windows.length) {
    body.append(el("p", "This account has no token limits.", "muted"));
    return;
  }
  body.append(el("p", tv.tier ? tv.tier[0].toUpperCase() + tv.tier.slice(1) + " tier" : "Custom limits", "muted"));
  for (const [kind, title] of windows) body.append(usageWindow(title, tv[kind]));
}

async function openTokenUsage() {
  renderTokenUsage(me && me.usage);
  $("tokenUsageDlg").showModal();
  try {
    me = await api("GET", "/api/me");
    renderUsage();
    renderTokenUsage(me.usage);
  } catch (err) { /* keep the figures already shown */ }
}
$("tokenUsageBtn").addEventListener("click", openTokenUsage);
$("tokenUsageClose").addEventListener("click", () => $("tokenUsageDlg").close());

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
    icon.appendChild(faceIcon(colorMap[name]));
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
      if (m.scheduled) box.prepend(el("div", "Check-in", "checkin-tag"));   // sent on the character's schedule
      box.dataset.idx = String(i);   // position in the saved chat, used to place the context marker
      addEditButton(box, i, m);
      addBranchButton(box, i);
      if (m.role === "user" && !isEvent && me && me.persona) colourLine(box, me.persona.name);
      if (m.thinking) setThinking(box, m.thinking);
    });
  }
  placeTurnTools(messages);
  refreshContext();
}

// Branching: copy the chat up to this message into a new branch and open it. The chat being branched from is left as it was.
function addBranchButton(box, i) {
  const btn = el("button", "Branch", "ghost small branch-btn");
  btn.type = "button";
  btn.title = "Start a new branch from this message. The current chat stays as it is.";
  btn.setAttribute("aria-label", "Branch from this message");
  btn.addEventListener("click", () => branchFrom(i));
  box.appendChild(btn);
}

async function branchFrom(index) {
  const name = prompt("Name for the new branch (optional)", "");
  if (name === null) return;
  const chat = current;
  $("chatErr").textContent = "";
  try {
    const data = await api("POST", "/api/branch", { character: chat, index, name: name.trim() });
    applyBranchState(chat, data);
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}

function applyBranchState(name, data) {
  if (name !== current) return;   // the user moved to another chat while this was saving
  renderHistory(data.messages);
  renderBranches(data);
}

function renderBranches(data) {
  const pick = $("branchPick");
  pick.replaceChildren(new Option("Main chat", "main"));
  for (const item of data.items) pick.append(new Option(item.name, item.id));
  pick.value = data.active;
  pick.hidden = data.items.length === 0;   // the picker appears once a branch exists
  $("branchDel").hidden = data.active === "main";
  // Admins always see the Tree button, since they can open any account's tree from it.
  $("branchTree").hidden = !(me && me.is_admin) && data.items.length === 0;
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

// Settings sheet: the per-character rows show or hide with a note when no character is open.
function loadCharSettings() {
  for (const n of document.querySelectorAll(".char-only")) n.hidden = !current;
  for (const n of document.querySelectorAll(".needs-char")) n.hidden = !!current;
  $("palaceRow").hidden = !(current && soloNames.includes(current));
  for (const id of ["tempSlider", "tempReset", "winSlider", "winReset", "exportMd", "exportTxt", "exportJson", "exportEpub", "exportPdf", "pinSave", "pinSettingText", "ciEnabled", "ciSave", "ciAt", "ciStart", "ciEnd", "dreamEnabled", "dreamSave", "dreamStart", "dreamEnd"]) $(id).disabled = !current;
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
$("replySlider").addEventListener("input", () => { $("replyVal").textContent = `${$("replySlider").value} tokens`; });
$("replySlider").addEventListener("change", () => saveReplyLength(Number($("replySlider").value)));
$("replyReset").addEventListener("click", () => saveReplyLength(null));
$("exportMd").addEventListener("click", () => exportChat("md"));
$("exportTxt").addEventListener("click", () => exportChat("txt"));
$("exportJson").addEventListener("click", () => exportChat("json"));
$("exportEpub").addEventListener("click", () => exportChat("epub"));
$("exportPdf").addEventListener("click", () => exportChat("pdf"));

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
    icon.appendChild(faceIcon(colorMap[name]));
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
  b.dataset.chatName = name;   // where the missed check-in dot goes
  b.disabled = busy;
  if (colorMap[name]) b.style.setProperty("--dot", colorMap[name]);
  b.appendChild(iconFor(name));
  b.appendChild(el("span", name, "char-name"));
  if (editableSet.has(name)) b.appendChild(editPencil(name));
  if (soloNames.includes(name)) {
    b.appendChild(favStar(name, favSet.has(name), async (on) => {
      try {
        await setFavourite(name, on);
        await loadCharacters(false);
      } catch (err) {
        $("chatErr").textContent = err.message;
      }
    }));
  }
  if (popularSet.has(name)) b.appendChild(el("span", "popular", "char-badge"));
  b.addEventListener("click", () => pickCharacter(name));
  return b;
}



// Picture for an account's own persona: its uploaded picture, or the colour face placeholder.
const personaIconVersion = {};   // persona id -> time of its last upload, so a new picture shows at once
function ownPersonaIcon(p) {
  const icon = el("span", "", "char-icon" + (p.icon ? "" : " placeholder"));
  if (p.icon) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/persona-icon/" + encodeURIComponent(p.id) + "?v=" + (personaIconVersion[p.id] || 0);
    img.loading = "lazy";
    icon.appendChild(img);
  } else {
    icon.appendChild(faceIcon(p.color));
  }
  return icon;
}

function ownPersonaEdit(p) {
  const edit = el("span", "✎", "char-edit");
  edit.title = "Edit " + p.name;
  edit.setAttribute("role", "button");
  edit.addEventListener("click", (e) => { e.stopPropagation(); openPersonaForm(p); });
  return edit;
}

function personaButton(p) {
  const b = el("button", "", "char-item persona-item");
  b.type = "button";
  b.dataset.persona = p.id;
  b.disabled = busy;
  if (p.color) b.style.setProperty("--dot", p.color);
  if (me && me.persona && me.persona.id === p.id) b.classList.add("active");
  b.appendChild(p.own ? ownPersonaIcon(p) : iconFor(p.name));
  b.appendChild(el("span", p.name, "char-name"));
  if (p.own) b.appendChild(ownPersonaEdit(p));
  else if (me && me.is_admin) b.appendChild(editPencil(p.name));
  b.addEventListener("click", () => choosePersona(p.own ? p.id : p.name));
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
  $("personaBox").hidden = false;   // every account can make its own persona, so the box always shows
}

let personaEditing = null;   // the account's own persona being edited; null when making a new one

function openPersonaForm(p) {
  personaEditing = p || null;
  $("personaFormTitle").textContent = p ? "Edit persona" : "New persona";
  $("pfName").value = p ? p.name : "";
  $("pfColour").value = p && p.color ? p.color : "#8b9bb4";
  $("pfDesc").value = p ? (p.description || "") : "";
  $("pfPic").value = "";
  $("pfDelete").hidden = !p;
  $("pfMsg").textContent = "";
  $("personaForm").hidden = false;
  $("pfName").focus();
}

function closePersonaForm() {
  personaEditing = null;
  $("personaForm").hidden = true;
}

// The picture is sent as the raw file, like the character icon upload.
async function uploadPersonaPicture(pid, file) {
  const r = await fetch("/api/personas/" + encodeURIComponent(pid) + "/icon", {
    method: "POST",
    headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream" },
    credentials: "same-origin",
    body: await file.arrayBuffer(),
  });
  if (!r.ok) {
    let data = {};
    try { data = await r.json(); } catch (e) { /* no body */ }
    throw apiError(r.status, data);
  }
  personaIconVersion[pid] = Date.now();
}

$("newPersonaBtn").addEventListener("click", () => openPersonaForm(null));
$("pfCancel").addEventListener("click", closePersonaForm);

$("personaForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("pfMsg").textContent = "";
  const body = { name: $("pfName").value, colour: $("pfColour").value, description: $("pfDesc").value };
  const file = $("pfPic").files[0];
  try {
    let data;
    let pid = personaEditing ? personaEditing.id : null;
    if (pid) data = await api("POST", "/api/personas/" + encodeURIComponent(pid), body);
    else { data = await api("POST", "/api/personas", body); pid = data.id; }
    if (file) {
      await uploadPersonaPicture(pid, file);
      for (const p of data.personas) if (p.id === pid) p.icon = true;
    }
    const activeChanged = me.persona && me.persona.id === pid;
    me.persona = data.persona;
    renderPersonas(data.personas);
    closePersonaForm();
    if (current && activeChanged) await loadHistory();
  } catch (err) {
    $("pfMsg").textContent = err.message;
  }
});

$("pfDelete").addEventListener("click", async () => {
  if (!personaEditing) return;
  if (!confirm("Delete this persona? Your chats stay, but it is no longer used.")) return;
  const pid = personaEditing.id;
  const wasActive = me.persona && me.persona.id === pid;
  try {
    const data = await api("POST", "/api/personas/" + encodeURIComponent(pid) + "/delete", {});
    me.persona = data.persona;
    renderPersonas(data.personas);
    closePersonaForm();
    if (current && wasActive) await loadHistory();
  } catch (err) {
    $("pfMsg").textContent = err.message;
  }
});

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
  if (me && me.avatar_shape) applyAvatarShape(me.avatar_shape);
  $("avatarShape").value = (me && me.avatar_shape) || "squircle";
  if (me) setLook(me.bg_shade || 0, me.bubble_opacity || 72);
  renderReplyLength();
}

// ---- Account profile settings. Admin accounts have no profile, so the block stays hidden.
function renderProfileSettings(resetBio = true) {
  const box = $("profileSettings");
  if (!box) return;
  const p = me && me.profile;
  box.hidden = !p;
  if (!p) return;
  $("profileLink").href = "/u/" + encodeURIComponent(me.username);
  if (resetBio && document.activeElement !== $("profileBio")) $("profileBio").value = p.description || "";
  updateProfileBioCount();
  const avatar = $("profileAvatarPreview");
  avatar.hidden = !p.has_avatar;
  if (p.has_avatar) avatar.src = "/api/account-avatar/" + encodeURIComponent(me.username) + "?v=" + (p.avatar_version || 0);
  $("profilePicRemove").hidden = !p.has_avatar;
  const background = $("profileBgPreview");
  background.hidden = !p.has_background;
  if (p.has_background) background.src = "/api/account-background/" + encodeURIComponent(me.username) + "?v=" + (p.background_version || 0);
  $("profileBgRemove").hidden = !p.has_background;
  $("profileLikes").checked = !!p.likes_visible;
}

function updateProfileBioCount() {
  $("profileBioCount").textContent = $("profileBio").value.length + " / 1000";
}

async function saveProfileDescription() {
  $("profileMsg").textContent = "";
  try {
    me.profile = await api("POST", "/api/account/profile", { description: $("profileBio").value });
    renderProfileSettings();
    $("profileMsg").textContent = "Description saved.";
  } catch (err) {
    $("profileMsg").textContent = err.message;
  }
}

async function saveLikesVisibility() {
  $("profileMsg").textContent = "";
  try {
    me.profile = await api("POST", "/api/account/profile", { likes_visible: $("profileLikes").checked });
    renderProfileSettings(false);
    $("profileMsg").textContent = me.profile.likes_visible
      ? "Liked characters are visible on your profile."
      : "Liked characters are private.";
  } catch (err) {
    $("profileMsg").textContent = err.message;
    renderProfileSettings(false);
  }
}

async function uploadProfileImage(kind, file) {
  if (!file) return;
  $("profileMsg").textContent = "Uploading…";
  try {
    const r = await fetch("/api/account/" + kind, {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream" },
      credentials: "same-origin",
      body: await file.arrayBuffer(),
    });
    if (!r.ok) {
      let data = {};
      try { data = await r.json(); } catch (e) { /* no body */ }
      throw apiError(r.status, data);
    }
    me.profile = await r.json();
    renderProfileSettings(false);
    $("profileMsg").textContent = kind === "avatar" ? "Profile picture saved." : "Profile background saved.";
  } catch (err) {
    $("profileMsg").textContent = err.message;
  }
}

async function removeProfileImage(kind) {
  const label = kind === "avatar" ? "profile picture" : "profile background";
  if (!confirm("Remove your " + label + "?")) return;
  $("profileMsg").textContent = "";
  try {
    me.profile = await api("DELETE", "/api/account/" + kind);
    renderProfileSettings(false);
    $("profileMsg").textContent = "Removed.";
  } catch (err) {
    $("profileMsg").textContent = err.message;
  }
}

$("profileBio").addEventListener("input", updateProfileBioCount);
$("profileBioSave").addEventListener("click", saveProfileDescription);
$("profileLikes").addEventListener("change", saveLikesVisibility);
$("profilePic").addEventListener("change", (e) => { const file = e.target.files[0]; e.target.value = ""; uploadProfileImage("avatar", file); });
$("profileBg").addEventListener("change", (e) => { const file = e.target.files[0]; e.target.value = ""; uploadProfileImage("background", file); });
$("profilePicRemove").addEventListener("click", () => removeProfileImage("avatar"));
$("profileBgRemove").addEventListener("click", () => removeProfileImage("background"));

// The reply length target. The server keeps it inside the admin range; null means no target.
function renderReplyLength() {
  const [lo, hi] = (me && me.reply_length_bounds) || [50, 1000];
  const s = $("replySlider");
  const v = me ? me.reply_length : null;
  s.min = lo;
  s.max = hi;
  s.value = v == null ? lo : v;
  $("replyVal").textContent = v == null ? "(no target)" : `${v} tokens`;
  $("replyReset").disabled = v == null;
  $("replyNote").textContent = `An aim for how long each reply should be, in tokens, not counting reasoning. The model is guided, not cut off. The server admin sets the range: ${lo} to ${hi} tokens.`;
}

// Background brightness and bubble opacity: dragging previews at once, release saves to the account.
function setLook(shade, bubble) {
  const s = Math.max(-100, Math.min(100, Math.round(shade) || 0));
  const b = Math.max(30, Math.min(100, Math.round(bubble) || 72));
  applyLook(s, b);
  $("shadeSlider").value = s;
  $("shadeVal").textContent = s === 0 ? "Default" : s < 0 ? "Darker " + -s : "Lighter " + s;
  $("bubbleSlider").value = b;
  $("bubbleVal").textContent = b + "%";
}

async function saveLook(key, value) {
  try {
    const data = await api("POST", "/api/settings", { [key]: value });
    me[key] = data[key];
    $("settingsMsg").textContent = "Saved.";
  } catch (err) {
    $("settingsMsg").textContent = err.message;
  }
}

async function saveAvatarShape(shape) {
  try {
    const data = await api("POST", "/api/settings", { avatar_shape: shape });
    me.avatar_shape = data.avatar_shape;
    applyAvatarShape(me.avatar_shape);
    $("settingsMsg").textContent = "Saved.";
  } catch (err) {
    $("settingsMsg").textContent = err.message;
    $("avatarShape").value = me.avatar_shape || "squircle";
  }
}

async function saveReplyLength(value) {
  try {
    const data = await api("POST", "/api/settings", { reply_length: value });
    me.reply_length = data.reply_length;
    renderReplyLength();
    $("settingsMsg").textContent = "Saved. Applies from your next message.";
  } catch (err) {
    $("settingsMsg").textContent = err.message;
    renderReplyLength();
  }
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

async function loadCharacters(choose = true) {
  const data = await api("GET", "/api/characters");
  colorMap = data.colors || {};
  iconSet = new Set(data.icons || []);
  bgIds = data.bg_ids || {};
  defaultArt = data.default_backgrounds || [];
  editableSet = new Set(data.editable || []);
  favSet = new Set(data.favourites || []);
  popularSet = new Set(data.popular || []);
  soloNames = data.solo || [];   // set before the lists are drawn: the star check reads it
  renderPersonas(data.personas || []);
  const solo = $("charList");
  const groups = $("groupList");
  solo.textContent = "";
  groups.textContent = "";
  // Only characters this account has chatted with. Every character still counts for links and groups below.
  for (const name of data.sidebar || []) solo.appendChild(charButton(name));
  if (!(data.sidebar || []).length) {
    solo.appendChild(el("p", "Characters you chat with appear here. Pick one from the home page to start.", "muted"));
  }
  for (const g of data.groups) groups.appendChild(g.private ? privateGroupRow(g) : charButton(g.name));
  $("groupBox").hidden = false;   // always shown, so any account can start its own group
  paintUnread();
  soloNames = data.solo || [];
  const all = [...data.solo, ...data.groups.map((g) => g.name)];
  // A card on the home page sends the name as ?c=; that wins over the last character used.
  const asked = new URLSearchParams(location.search).get("c");
  const saved = localStorage.getItem("rp_character");
  const start = all.includes(asked) ? asked : (all.includes(saved) ? saved : ((data.sidebar || [])[0] || null));
  if (!choose) { markActive(); return; }   // refresh only: keep the open chat
  if (start) await pickCharacter(start);
  else { current = null; markActive(); $("chatSection").hidden = true; }   // nothing to open yet: no empty chat window
}

// A character's background sits behind its chat, and the whole page takes it.
function applyChatArt(name) {
  // The whole page takes the art too, so the bars beside the chat column show the same picture.
  const section = $("chatSection");
  if (!section) return;
  const ids = name ? (bgIds[name] || []) : [];
  // A character without backgrounds of its own, or no character at all, takes a shared default.
  const pool = ids.length
    ? ids.map((b) => "/api/background/" + encodeURIComponent(name) + "?b=" + encodeURIComponent(b))
    : defaultArt;
  if (pool.length) {
    // One picture is chosen at random each time the page or character opens.
    const url = pool[Math.floor(Math.random() * pool.length)];
    const art = 'url("' + url + '")';
    section.style.setProperty("--chat-art", art);
    document.body.style.setProperty("--chat-art", art);
    section.classList.add("has-art");
    document.body.classList.add("page-art");
  } else {
    section.style.removeProperty("--chat-art");
    document.body.style.removeProperty("--chat-art");
    section.classList.remove("has-art");
    document.body.classList.remove("page-art");
  }
}

async function pickCharacter(name) {
  if (busy) return;
  current = name;
  markRead(name);   // opening the chat marks its missed check-ins as read
  localStorage.setItem("rp_character", name);
  markActive();
  applyChatArt(name);
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
  try {
    const list = await api("GET", "/api/branches?character=" + encodeURIComponent(name));
    if (name === current) renderBranches(list);
  } catch (err) {
    $("branchPick").hidden = true;
  }
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
  // A first message adds this character to the sidebar.
  loadCharacters(false).catch(() => {});
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
    renderModels();
    renderProfileSettings();
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
  $("editBox").hidden = false;   // any account can create characters; the pencil shows only on the ones it may change
  if (me.is_admin) {
    $("adminLink").hidden = false;
    $("sandboxLink").hidden = false;
    $("setAdminCard").hidden = false;
  }
  renderModels();
  renderProfileSettings();
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

// The dice face changes on every click. One click in a million shows Miso instead.
const DIE_PIPS = {
  1: [[12, 12]],
  2: [[8, 8], [16, 16]],
  3: [[8, 8], [12, 12], [16, 16]],
  4: [[8, 8], [16, 8], [8, 16], [16, 16]],
  5: [[8, 8], [16, 8], [12, 12], [8, 16], [16, 16]],
  6: [[8, 8], [16, 8], [8, 12], [16, 12], [8, 16], [16, 16]],
};
const MISO_DIE = `<svg x="3" y="3" width="18" height="18" viewBox="0 0 512 512" aria-hidden="true"><defs><linearGradient id="dieMisoGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4cc4ff"/><stop offset="1" stop-color="#9b8cff"/></linearGradient></defs><rect width="512" height="512" rx="112" fill="url(#dieMisoGrad)"/><ellipse cx="136" cy="150" rx="58" ry="58" fill="#04121f"/><ellipse cx="376" cy="150" rx="58" ry="58" fill="#04121f"/><ellipse cx="256" cy="296" rx="150" ry="128" fill="#f4f8ff"/><ellipse cx="196" cy="282" rx="40" ry="34" fill="#04121f"/><ellipse cx="316" cy="282" rx="40" ry="34" fill="#04121f"/><circle cx="206" cy="274" r="9" fill="#f4f8ff"/><circle cx="326" cy="274" r="9" fill="#f4f8ff"/><circle cx="160" cy="336" r="20" fill="#9b8cff" fill-opacity="0.55"/><circle cx="352" cy="336" r="20" fill="#9b8cff" fill-opacity="0.55"/><ellipse cx="256" cy="334" rx="22" ry="15" fill="#04121f"/></svg>`;

// Uniform random integer in [0, n) from the browser's crypto source, without modulo bias.
function randomBelow(n) {
  const limit = Math.floor(4294967296 / n) * n;
  const buf = new Uint32Array(1);
  do { crypto.getRandomValues(buf); } while (buf[0] >= limit);
  return buf[0] % n;
}

function rollDie() {
  const face = $("autoBtn").querySelector(".die-face");
  if (randomBelow(1000000) === 0) {
    face.dataset.face = "miso";
    face.innerHTML = MISO_DIE;
    return;
  }
  const current = Number(face.dataset.face) || 0;
  let next;
  do { next = 1 + randomBelow(6); } while (next === current);   // always a different face
  face.dataset.face = String(next);
  face.innerHTML = DIE_PIPS[next].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="1.7" fill="currentColor"/>`).join("");
}

$("autoBtn").addEventListener("click", () => { rollDie(); autoFill(); });
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
$("sideBack").addEventListener("click", () => {
  if (mobileNav()) { closeSidebar(); return; }
  const collapsed = document.querySelector(".shell").classList.toggle("collapsed");
  $("menuBtn").setAttribute("aria-expanded", String(!collapsed));
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
$("shadeSlider").addEventListener("input", () => setLook(Number($("shadeSlider").value), Number($("bubbleSlider").value)));
$("shadeSlider").addEventListener("change", () => saveLook("bg_shade", Number($("shadeSlider").value)));
$("bubbleSlider").addEventListener("input", () => setLook(Number($("shadeSlider").value), Number($("bubbleSlider").value)));
$("bubbleSlider").addEventListener("change", () => saveLook("bubble_opacity", Number($("bubbleSlider").value)));
$("shadeReset").addEventListener("click", () => { setLook(0, Number($("bubbleSlider").value)); saveLook("bg_shade", 0); });
$("bubbleReset").addEventListener("click", () => { setLook(Number($("shadeSlider").value), 72); saveLook("bubble_opacity", 72); });
applyFont(localStorage.getItem("rp_font"));
$("sizeDown").addEventListener("click", () => applySize(chatSize - 1));
$("sizeUp").addEventListener("click", () => applySize(chatSize + 1));
applySize(localStorage.getItem("rp_size") || SIZE_DEFAULT);
applyBubbleStyle(localStorage.getItem("rp_bubble"));
$("thinkToggle").addEventListener("change", (e) => saveSettings({ thinking: e.target.checked }));
$("avatarShape").addEventListener("change", (e) => saveAvatarShape(e.target.value));
$("modelSelect").addEventListener("change", (e) => saveSettings({ model: e.target.value }));
$("editNewBtn").addEventListener("click", () => openEditor(null));
$("undoBtn").addEventListener("click", undo);
$("branchPick").addEventListener("change", async () => {
  const chat = current;
  $("chatErr").textContent = "";
  try {
    applyBranchState(chat, await api("POST", "/api/branch/switch", { character: chat, id: $("branchPick").value }));
  } catch (err) {
    $("chatErr").textContent = err.message;
    loadHistory();
  }
});
$("branchDel").addEventListener("click", async () => {
  const chat = current;
  if (!confirm("Delete this branch? Its messages and memory are removed. The main chat is kept.")) return;
  $("chatErr").textContent = "";
  try {
    applyBranchState(chat, await api("POST", "/api/branch/delete", { character: chat, id: $("branchPick").value }));
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
});
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

// Pinned facts from the settings sheet: the pinned box of the Memory panel, saved on its own.
let pinFor = null;
let pinLoaded = { memory: "" };

async function openPinned() {
  if (busy || !current) return;
  const who = current;
  $("pinMsg").textContent = "";
  try {
    const data = await api("GET", "/api/memory?character=" + encodeURIComponent(who));
    pinFor = who;
    pinLoaded = data;
    $("pinSettingText").value = data.pinned;
  } catch (err) {
    $("pinMsg").textContent = err.message;
  }
}

$("pinCard").addEventListener("click", openPinned);
$("pinSave").addEventListener("click", async () => {
  if (!pinFor || busy) return;
  $("pinMsg").textContent = "";
  try {
    // Only the pinned part changes; the memory bank and summary are sent back as they were read.
    const data = await api("POST", "/api/memory", { character: pinFor, memory: pinLoaded.memory, pinned: $("pinSettingText").value });
    pinLoaded = data;
    $("pinMsg").textContent = "Saved. The next reply includes these facts.";
  } catch (err) {
    $("pinMsg").textContent = err.message;
  }
});

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

// Scheduled check-ins: a character's own schedule, set in Settings, Check-in. Off by default.
// The page shows the time in the reader's own time zone, which is the one saved with the schedule.
let ciFor = null;   // the character whose check-in settings are open

async function openCheckin() {
  if (busy || !current) return;
  const who = current;
  $("ciMsg").textContent = "";
  try {
    const data = await api("GET", "/api/checkins?character=" + encodeURIComponent(who));
    ciFor = who;
    $("ciEnabled").checked = data.enabled;
    document.querySelector('input[name="ciMode"][value="' + data.mode + '"]').checked = true;
    $("ciAt").value = data.at;
    $("ciStart").value = data.start;
    $("ciEnd").value = data.end;
    showCheckinNote(data);
  } catch (err) {
    $("ciMsg").textContent = err.message;
  }
}

function showCheckinNote(data) {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!data.enabled) {
    $("ciNote").textContent = `Off. ${ciFor || "This character"} will not message you on its own.`;
  } else if (data.next) {
    $("ciNote").textContent = `Today's check-in is set for ${data.next} (${zone}). It is skipped if the server is not running then.`;
  } else {
    $("ciNote").textContent = `On. Once a day, in ${zone}. It is skipped if the server is not running at the chosen time.`;
  }
}

$("checkinCard").addEventListener("click", openCheckin);

// Autonomous dreaming: the quiet hours for this character, saved with the browser's time zone.
let dreamFor = null;

function showDreamLast(data) {
  $("dreamServerNote").hidden = data.server_on;
  $("dreamEvery").textContent = data.every_days === 1 ? "A dream can run each night." : `A dream runs at most once every ${data.every_days} days.`;
  const last = data.last;
  if (!last) {
    $("dreamLast").textContent = "No dream yet.";
    return;
  }
  const when = last.when ? new Date(last.when).toLocaleDateString() : "";
  $("dreamLast").textContent = `Last dream ${when}: ${last.note || "nothing needed tidying"} (${last.added} added, ${last.updated} updated, ${last.removed} removed, ${last.dropped} palace entries dropped).`;
}

async function openDreams() {
  if (busy || !current) return;
  const who = current;
  $("dreamMsg").textContent = "";
  try {
    const data = await api("GET", "/api/dreams?character=" + encodeURIComponent(who));
    dreamFor = who;
    $("dreamEnabled").checked = data.enabled;
    $("dreamStart").value = data.start;
    $("dreamEnd").value = data.end;
    showDreamLast(data);
  } catch (err) {
    $("dreamMsg").textContent = err.message;
  }
}

$("dreamCard").addEventListener("click", openDreams);
$("dreamSave").addEventListener("click", async () => {
  if (!dreamFor || busy) return;
  $("dreamMsg").textContent = "";
  try {
    const data = await api("POST", "/api/dreams", {
      character: dreamFor,
      enabled: $("dreamEnabled").checked,
      start: $("dreamStart").value,
      end: $("dreamEnd").value,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
    showDreamLast(data);
    $("dreamMsg").textContent = "Saved.";
  } catch (err) {
    $("dreamMsg").textContent = err.message;
  }
});
$("ciSave").addEventListener("click", async () => {
  if (!ciFor || busy) return;
  $("ciMsg").textContent = "";
  const mode = document.querySelector('input[name="ciMode"]:checked').value;
  try {
    const data = await api("POST", "/api/checkins", {
      character: ciFor,
      enabled: $("ciEnabled").checked,
      mode,
      at: $("ciAt").value,
      start: $("ciStart").value,
      end: $("ciEnd").value,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
    showCheckinNote(data);
    $("ciMsg").textContent = "Saved.";
  } catch (err) {
    $("ciMsg").textContent = err.message;
  }
});

init();
refreshUnread();
setInterval(refreshUnread, 60000);

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

// Settings sheet. Ten cards (nine categories and Admin) open one category at a time. On phones the chat bar's font, name
// badge and size controls, and the sidebar's model, persona, character tools and memory move into the sheet. On desktop
// they move in only while the sheet is open and go back to their places when it closes.
(function settingsSheet() {
  const phone = window.matchMedia("(max-width: 760px)");
  const sheet = document.getElementById("settingsSheet");
  const btn = document.getElementById("settingsBtn");
  const closeBtn = document.getElementById("sheetClose");
  const backBtn = document.getElementById("setBack");
  const title = document.getElementById("sheetTitle");
  const grid = document.getElementById("setGrid");
  const panels = [...sheet.querySelectorAll(".set-panel")];
  // Font, text size and Memory live only in the settings sheet, so they leave the chat bar on every layout.
  for (const [id, slot] of [["fontSelect", "font"], ["sizeCtl", "size"], ["memBtn", "memory"]]) {
    sheet.querySelector('[data-slot="' + slot + '"]').appendChild(document.getElementById(id));
  }
  const parts = [["bubbleSelect", "badge"],
                 ["modelSection", "model"], ["editBox", "characters"], ["personaBox", "persona"]].map(([id, slot]) => {
    const node = document.getElementById(id);
    const home = document.createComment("home:" + id);   // stays where the control belongs on desktop
    node.parentNode.insertBefore(home, node);
    return { node, home, slot };
  });

  // Show one category, or the grid of cards when name is null.
  function show(name) {
    grid.hidden = !!name;
    for (const p of panels) p.hidden = p.dataset.panel !== name;
    backBtn.hidden = !name;
    const card = name && sheet.querySelector('.set-card[data-panel="' + name + '"] .set-name');
    title.textContent = card ? card.textContent : "Settings";
  }
  function place() {
    const inSheet = phone.matches || !sheet.hidden;
    for (const p of parts) {
      if (inSheet) {
        const slot = sheet.querySelector('[data-slot="' + p.slot + '"]');
        if (p.node.parentNode !== slot) slot.appendChild(p.node);
      } else if (p.home.nextSibling !== p.node) {
        p.home.parentNode.insertBefore(p.node, p.home.nextSibling);
      }
    }
  }
  function open() {
    sheet.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    show(null);
    place();
    loadCharSettings();
    renderProfileSettings();
    closeBtn.focus();
  }
  function close(refocus) {
    if (sheet.hidden) return;
    sheet.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    place();
    if (refocus) btn.focus();
  }

  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", () => close(true));
  backBtn.addEventListener("click", () => { show(null); });
  grid.addEventListener("click", (e) => {
    const card = e.target.closest(".set-card");
    if (card) { show(card.dataset.panel); backBtn.focus(); }
  });
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(true); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || sheet.hidden) return;
    if (grid.hidden) show(null); else close(true);   // Escape steps back from a category before closing
  });
  document.getElementById("editNewBtn").addEventListener("click", () => close(false));
  if (phone.addEventListener) phone.addEventListener("change", place); else phone.addListener(place);
  place();
  // /chat?settings=account opens the Account panel directly (used by the profile page's Edit link).
  const wanted = new URLSearchParams(location.search).get("settings");
  if (wanted && /^[a-z]+$/.test(wanted) && sheet.querySelector('.set-card[data-panel="' + wanted + '"]')) {
    open();
    show(wanted);
  }
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
  const parts = [["chatIcon", "chat"], ["chatTitle", "chat"], ["rollBtn", "chat"], ["settingsBtn", "chat"], ["adminLink", "account"], ["pwBtn", "account"], ["logoutBtn", "account"]]
    .map(([id, group]) => {
      const node = document.getElementById(id);
      const home = document.createComment("home:" + id);   // where the control belongs on desktop
      node.parentNode.insertBefore(home, node);
      return { node, home, group };
    });

  // On phones the Undo button sits beside the Tree button; on desktop it returns to its own place.
  const undo = document.getElementById("undoBtn");
  const undoHome = document.createComment("home:undoBtn");
  undo.parentNode.insertBefore(undoHome, undo);
  const branchTree = document.getElementById("branchTree");
  // The Tree button stays in the top chat bar on both layouts. The token bar moves: on phones it sits in the bar
  // (after the usage counter), on desktop it sits right under the prompt box.
  const treeHome = document.createComment("home:branchTree");
  branchTree.parentNode.insertBefore(treeHome, branchTree);
  const tokenBar = document.getElementById("tokenBar");
  // The requests counter and the token bar share a row under the prompt on desktop; on phones they sit in the top bar.
  const usage = document.getElementById("usage");
  const usageHome = document.createComment("home:usage");
  usage.parentNode.insertBefore(usageHome, usage);
  const deskMeters = document.getElementById("deskMeters");
  function placeTree() {
    if (branchTree.previousSibling !== treeHome) treeHome.parentNode.insertBefore(branchTree, treeHome.nextSibling);
    if (phone.matches) {
      if (usage.previousSibling !== usageHome) usageHome.parentNode.insertBefore(usage, usageHome.nextSibling);
      if (tokenBar.previousElementSibling !== usage) usage.insertAdjacentElement("afterend", tokenBar);
    } else if (usage.parentNode !== deskMeters || tokenBar.parentNode !== deskMeters) {
      deskMeters.append(usage, tokenBar);
    }
  }
  function placeUndo() {
    if (phone.matches) {
      if (undo.previousElementSibling !== branchTree) branchTree.parentNode.insertBefore(undo, branchTree.nextSibling);
    } else if (undoHome.nextSibling !== undo) {
      undoHome.parentNode.insertBefore(undo, undoHome.nextSibling);
    }
  }
  function place() {
    placeTree();
    placeUndo();
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

// ---- Private group chats: an account's own groups of solo characters, with a note for each member.
let soloNames = [];
let groupEditing = null;       // id of the group being edited; null when a new one is being made
let groupEditingName = "";
let groupLimits = { min_members: 2, max_members: 8, note_max: 500 };

function privateGroupRow(g) {
  const row = el("div", "", "group-row");
  row.appendChild(charButton(g.name));
  const edit = el("button", "Edit", "ghost small group-edit");
  edit.type = "button";
  edit.title = "Change this group's characters and notes";
  edit.setAttribute("aria-label", "Edit group " + g.name);
  edit.addEventListener("click", () => openGroupDialog(g.id));
  row.appendChild(edit);
  return row;
}

async function refreshGroups() {
  const data = await api("GET", "/api/characters");
  soloNames = data.solo || [];
  colorMap = data.colors || {};
  const groups = $("groupList");
  groups.textContent = "";
  for (const g of data.groups) groups.appendChild(g.private ? privateGroupRow(g) : charButton(g.name));
  markActive();
  return data.groups.map((g) => g.name);
}

async function openGroupDialog(gid) {
  $("groupMsg").textContent = "";
  const data = await api("GET", "/api/groups");
  groupLimits = { min_members: data.min_members, max_members: data.max_members, note_max: data.note_max };
  const item = gid ? data.groups.find((g) => g.id === gid) : null;
  if (gid && !item) {
    $("chatErr").textContent = "That group no longer exists.";
    return;
  }
  groupEditing = item ? item.id : null;
  groupEditingName = item ? item.name : "";
  $("groupDlgTitle").textContent = item ? "Edit group chat" : "New group chat";
  $("groupPickLegend").textContent = `Characters (${groupLimits.min_members} to ${groupLimits.max_members})`;
  $("groupName").value = item ? item.name : "";
  $("groupDelete").hidden = !item;
  const chosen = new Set(item ? item.members.map((m) => m.name) : []);
  const known = item ? item.notes : {};
  const pick = $("groupPick");
  pick.replaceChildren();
  for (const name of soloNames) {
    const label = el("label", "", "group-check");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = name;
    box.checked = chosen.has(name);
    box.addEventListener("change", () => renderGroupNotes(known));
    label.append(box, document.createTextNode(" " + name));
    pick.appendChild(label);
  }
  renderGroupNotes(known);
  // A group's background can be set once the group exists; a new group gets one after it is saved.
  $("groupBgBox").hidden = !item;
  $("groupBgMsg").textContent = "";
  $("groupBgFile").value = "";
  $("groupBgRemove").hidden = !(item && item.has_background);
  $("groupIconBox").hidden = !item;
  $("groupIconMsg").textContent = "";
  $("groupIconFile").value = "";
  $("groupIconRemove").hidden = !(item && item.has_icon);
  $("groupDlg").showModal();
  $("groupName").focus();
}

// Sends a private group's picture or background, or removes it, then reloads the lists so the chat shows it.
// kind is "icon" (the sidebar picture) or "background" (behind the chat).
async function changeGroupArt(kind, method, file) {
  if (!groupEditing) return;
  const icon = kind === "icon";
  const msg = $(icon ? "groupIconMsg" : "groupBgMsg");
  const remove = $(icon ? "groupIconRemove" : "groupBgRemove");
  const input = $(icon ? "groupIconFile" : "groupBgFile");
  msg.textContent = method === "DELETE" ? "Removing…" : "Uploading…";
  try {
    const r = await fetch("/api/groups/" + encodeURIComponent(groupEditing) + "/" + kind, {
      method,
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream" },
      credentials: "same-origin",
      body: method === "POST" ? await file.arrayBuffer() : undefined,
    });
    if (!r.ok) {
      let data = {};
      try { data = await r.json(); } catch (e) { /* no body */ }
      throw apiError(r.status, data);
    }
    const data = await r.json();
    const has = icon ? data.has_icon : data.has_background;
    remove.hidden = !has;
    msg.textContent = has ? (icon ? "Picture saved." : "Background saved.") : (icon ? "Picture removed." : "Background removed.");
    input.value = "";
    await loadCharacters(false);
    applyChatArt(current);
  } catch (err) {
    msg.textContent = err.message;
  }
}
$("groupBgFile").addEventListener("change", () => {
  const file = $("groupBgFile").files[0];
  if (file) changeGroupArt("background", "POST", file);
});
$("groupBgRemove").addEventListener("click", () => changeGroupArt("background", "DELETE"));
$("groupIconFile").addEventListener("change", () => {
  const file = $("groupIconFile").files[0];
  if (file) changeGroupArt("icon", "POST", file);
});
$("groupIconRemove").addEventListener("click", () => changeGroupArt("icon", "DELETE"));

// Rebuilds one note box per ticked character. Text already typed is kept when a tick changes.
function renderGroupNotes(known) {
  const typed = {};
  for (const ta of $("groupNotes").querySelectorAll("textarea")) typed[ta.dataset.name] = ta.value;
  const chosen = [...$("groupPick").querySelectorAll("input:checked")].map((b) => b.value);
  const box = $("groupNotes");
  box.replaceChildren();
  $("groupNotesBox").hidden = chosen.length === 0;
  for (const name of chosen) {
    const wrap = el("label", "", "group-note");
    wrap.appendChild(el("span", name, "group-note-name"));
    const ta = document.createElement("textarea");
    ta.dataset.name = name;
    ta.maxLength = groupLimits.note_max;
    ta.rows = 2;
    ta.value = typed[name] !== undefined ? typed[name] : (known[name] || "");
    wrap.appendChild(ta);
    box.appendChild(wrap);
  }
}

async function saveGroup() {
  $("groupMsg").textContent = "";
  const members = [...$("groupPick").querySelectorAll("input:checked")].map((b) => b.value);
  const notes = {};
  for (const ta of $("groupNotes").querySelectorAll("textarea")) {
    const text = ta.value.trim();
    if (text) notes[ta.dataset.name] = text;
  }
  const body = { name: $("groupName").value, members, notes };
  const wasOpen = groupEditing !== null && current === groupEditingName;
  try {
    const saved = groupEditing
      ? await api("PUT", "/api/groups/" + encodeURIComponent(groupEditing), body)
      : await api("POST", "/api/groups", body);
    $("groupDlg").close();
    const names = await refreshGroups();
    if (wasOpen && !busy) await pickCharacter(saved.name);
    else if (current && !names.includes(current) && !soloNames.includes(current)) await loadCharacters();
  } catch (err) {
    $("groupMsg").textContent = err.message;
  }
}

async function deleteGroup() {
  if (!groupEditing) return;
  if (!confirm("Delete this group chat? Its messages, memory and notes are removed. The characters are kept.")) return;
  try {
    await api("DELETE", "/api/groups/" + encodeURIComponent(groupEditing));
    $("groupDlg").close();
    const names = await refreshGroups();
    if (current && !names.includes(current) && !soloNames.includes(current)) await loadCharacters();
  } catch (err) {
    $("groupMsg").textContent = err.message;
  }
}

// ---- Branch tree. Everyone sees their own; admins can also open any account's tree, read-only.
async function openTree() {
  const admin = !!(me && me.is_admin);
  $("treeAccountRow").hidden = !admin;
  if (admin) {
    const sel = $("treeAccount");
    const users = await api("GET", "/api/admin/users");
    sel.replaceChildren(new Option("My own chats", ""));
    for (const u of users.users) sel.append(new Option(u.username, String(u.id)));
    sel.value = "";
  }
  $("treeDlg").showModal();
  await loadTree();
}

async function loadTree() {
  const sel = $("treeAccount");
  const other = !!(me && me.is_admin && sel.value !== "");
  $("treeBody").replaceChildren();
  $("treeNote").textContent = "";
  if (!current) {
    $("treeNote").textContent = "Open a chat first.";
    return;
  }
  try {
    const data = other
      ? await api("GET", "/api/admin/branches/tree?uid=" + encodeURIComponent(sel.value) + "&character=" + encodeURIComponent(current))
      : await api("GET", "/api/branches/tree?character=" + encodeURIComponent(current));
    $("treeNote").textContent = other
      ? `Read-only: ${data.account}'s branches for this chat. Nothing here can be changed.`
      : "Choose a branch to open it. The one you are in is marked open.";
    renderTree(data, other);
  } catch (err) {
    $("treeNote").textContent = err.message;
  }
}

function renderTree(data, readOnly) {
  const kids = new Map();
  for (const n of data.nodes) {
    const key = n.parent === null ? "" : n.parent;
    if (!kids.has(key)) kids.set(key, []);
    kids.get(key).push(n);
  }
  const build = (key) => {
    const list = el("ul", "", "tree-list");
    for (const n of kids.get(key) || []) {
      let text = n.name;
      if (n.fork_index !== null && n.fork_index !== undefined) text += ` (from message ${n.fork_index + 1})`;
      if (n.id === data.active) text += " - open";
      const node = readOnly ? el("div", text, "tree-node") : el("button", text, "tree-node");
      if (!readOnly) {
        node.type = "button";
        node.addEventListener("click", () => switchFromTree(n.id));
      }
      if (n.id === data.active) node.classList.add("open");
      const li = el("li");
      li.appendChild(node);
      const sub = build(n.id);
      if (sub.childElementCount) li.appendChild(sub);
      list.appendChild(li);
    }
    return list;
  };
  const body = $("treeBody");
  body.replaceChildren(build(""));
  body.classList.add("tree");
}

async function switchFromTree(id) {
  $("treeDlg").close();
  if (busy || !current) return;
  try {
    applyBranchState(current, await api("POST", "/api/branch/switch", { character: current, id }));
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}

$("newGroupBtn").addEventListener("click", () => openGroupDialog(null));
$("groupSave").addEventListener("click", saveGroup);
$("groupCancel").addEventListener("click", () => $("groupDlg").close());
$("groupDelete").addEventListener("click", deleteGroup);
$("branchTree").addEventListener("click", openTree);
$("treeAccount").addEventListener("change", loadTree);
$("treeClose").addEventListener("click", () => $("treeDlg").close());
