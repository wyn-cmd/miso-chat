// Chat page: character sidebar (desktop) or drawer (mobile), markdown messages, streaming replies,
// undo, usage counter, forced password change.
const $ = (id) => document.getElementById(id);
let me = null;
let current = null;   // character or group chat currently open
const creatorMap = new Map();   // solo character -> who made it, and whether that account has a page
let favSet = new Set();            // characters this account has starred
let popularSet = new Set();        // top 10% of characters by user-sent messages
let editableSet = new Set();   // characters this account may change (shows the sidebar pencil)
let busy = false;     // a turn is streaming: keep the character list and buttons locked
let picking = false;   // Select mode is on: every line shows a pick box
const picked = new Set();   // saved-chat positions picked for deletion
let novelNames = {};           // novel ID -> its name, for the sidebar and the chat header
let worldCache = { worlds: [], default: null };   // the account's worlds and default, as the server last sent them

function setSidebar(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").hidden = !open;
  $("menuBtn").setAttribute("aria-expanded", String(open));
}

// mobile width uses the drawer; wider screens keep the sidebar beside the chat
function mobileNav() { return window.matchMedia("(max-width: 760px)").matches; }

function closeSidebar() { if (mobileNav()) setSidebar(false); }

// chat font is chosen per browser: sans-serif, Merriweather (serif) or monospace
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

// The chat and interface font, shared with every page through applyFontChoice in theme-init.js.
function applyFont(name) {
  const font = window.applyFontChoice(name);
  $("fontSelect").value = font;
  localStorage.setItem("rp_font", font);
}

// Bubble colours: each character's colour from character.md. Shown as a badge around the name, or
// as the whole bubble (per browser). Text switches black or white by the colour's brightness.
let colorMap = {};        // name -> "#rrggbb"
let iconSet = new Set(); // characters that have an icon.png/jpg/webp on the server
let bgIds = {};   // solo character -> ids of its backgrounds on the server
let musicByName = new Map();   // solo character -> its theme music tracks (see theme_music.js)
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
  $("selectBtn").disabled = on;
  $("pickDelete").disabled = on || picked.size === 0;
  for (const b of document.querySelectorAll(".char-item")) b.disabled = on;
  for (const b of document.querySelectorAll(".turn-tools button")) b.disabled = on;
  $("memBtn").disabled = on;
}

function showPassword(note) {
  closeSidebar();
  document.body.classList.add("locked");
  $("chatSection").hidden = true;
  $("memSection").hidden = true;
  $("bibleSection").hidden = true;
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
  // Memory is an admin switch; hide its button and its Story & memory entry when it is off.
  $("memBtn").hidden = !!(me && me.features && me.features.memory === false);
  $("memStoryLink").hidden = $("memBtn").hidden;
  if (!me || !me.usage) return;
  const u = me.usage;
  // two forms of the same line: the long one on desktop, a compact one for phones (CSS picks which shows)
  const fmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  const usage = $("usage");
  usage.textContent = "";
  const long = document.createElement("span");
  long.className = "u-long";
  long.textContent = tr("Requests today {used}/{cap} · tokens {tokens}", { used: u.requests_today, cap: u.request_cap, tokens: u.tokens_today.toLocaleString() });
  const short = document.createElement("span");
  short.className = "u-short";
  short.textContent = tr("{used}/{cap} req · {tokens} tok", { used: u.requests_today, cap: u.request_cap, tokens: fmt.format(u.tokens_today) });
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
    $("tokenText").textContent = tr("{left} left", { left: fmt.format(left) });
    meter.title = tr("{window} token budget: {left} of {limit} tokens left, {reset}", { window: tr(title), left: left.toLocaleString(), limit: w.limit.toLocaleString(), reset: w.reset });
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
    ? tr("The {window} token budget is used up. New messages are blocked until it resets: {reset}.", { window: tr(title).toLowerCase(), reset: w.reset })
    : tr("You have {percent}% of your {window} token budget left ({left} tokens), {reset}.", { percent: Math.round(100 - windowShare(w) * 100), window: tr(title).toLowerCase(), left: left.toLocaleString(), reset: w.reset });
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
  head.append(el("span", tr(title), "muted"), el("span", tr("{percent}% used", { percent: shown.toFixed(1) }), "usage-window-pct"));
  const track = el("div", null, "bar-track");
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", tr("{window} token usage", { window: tr(title) }));
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", String(Math.round(shown)));
  const fill = el("div", null, "bar-fill");
  fill.style.width = shown.toFixed(1) + "%";
  track.append(fill);
  box.append(head, track,
    el("p", tr("{used} of {limit} tokens used", { used: fmtN(w.used), limit: fmtN(w.limit) })),
    el("p", tr("{left} tokens left · {reset}.", { left: fmtN(left), reset: w.reset }), "muted"));
  if (w.used >= w.limit) box.append(el("p", tr("The {window} budget is used up. New messages are blocked until it resets.", { window: tr(title).toLowerCase() }), "error-msg"));
  return box;
}

// On a phone the next prompt's estimate is shown here, not in the chat, where it crowds the messages (style.css hides
// it at the same width). The chat has to be open for there to be an estimate.
const PHONE_QUERY = "(max-width: 760px)";
function renderNextPrompt(body) {
  if (!window.matchMedia(PHONE_QUERY).matches) return;
  const box = el("div", null, "usage-next");
  box.append(el("p", tr("Next prompt"), "usage-window-head"));
  const n = nextPromptTokens();
  if (n === null) {
    box.append(el("p", tr("Open a chat to see the estimate for its next prompt."), "muted"));
  } else {
    const fresh = n.total - n.cached;
    box.append(el("p", tr("≈ {n} tokens", { n: n.total.toLocaleString() })));
    box.append(el("p", tr("≈ {cached} cached · ≈ {fresh} new", { cached: n.cached.toLocaleString(), fresh: fresh.toLocaleString() }), "muted"));
    const saved = promptSaved();
    if (saved) box.append(el("p", tr("≈ {saved} saved by quenching or clipping", { saved: saved.toLocaleString() }), "muted"));
  }
  box.append(el("p", tr("Estimates, about 4 characters a token."), "muted"));
  body.append(box);
}

function renderTokenUsage(usage) {
  const body = $("tokenUsageBody");
  body.textContent = "";
  renderNextPrompt(body);
  if (!usage) {
    body.append(el("p", tr("Usage is not available right now."), "muted"));
    return;
  }
  const tv = usage.token_budgets || {};
  const windows = TOKEN_WINDOWS.filter(([kind]) => tv[kind]);
  if (!windows.length) {
    body.append(el("p", tr("This account has no token limits."), "muted"));
    return;
  }
  body.append(el("p", tv.tier ? tv.tier[0].toUpperCase() + tv.tier.slice(1) + " tier" : "Custom limits", "muted"));
  for (const [kind, title] of windows) body.append(usageWindow(title, tv[kind]));
}

async function openTokenUsage() {
  renderTokenUsage(me && me.usage);
  $("tokenUsageDlg").showModal();
  if (current && window.matchMedia(PHONE_QUERY).matches) {
    refreshContext().then(() => renderTokenUsage(me && me.usage)).catch(() => {});
  }
  try {
    me = await api("GET", "/api/me");
    renderUsage();
    renderTokenUsage(me.usage);
  } catch (err) { /* keep the figures already shown */ }
}
$("tokenUsageBtn").addEventListener("click", openTokenUsage);
$("tokenUsageClose").addEventListener("click", () => $("tokenUsageDlg").close());
$("balanceBtn").addEventListener("click", () => { location.href = "/balance"; });

// Follow new text only while the reader is at the bottom, so scrolling up to read history is not undone.
function nearBottom(log) {
  return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
}

function addLine(speaker, text, cls, time) {
  const box = el("div", null, "line " + (cls || ""));
  if (speaker) {
    const name = el("div", speaker, "speaker");
    box.appendChild(name);
    if (cls && cls.includes("theirs")) {
      colourLine(box, speaker);
      // A character's name opens the story bible for this chat.
      name.classList.add("bible-link");
      name.title = tr("Open the story bible");
      name.addEventListener("click", openStoryBible);
    }
  }
  const body = el("div", null, "text");
  renderMarkdown(text, body);
  box.appendChild(body);
  if (speaker && cls && cls.includes("theirs") && window.ttsMessageButton) {
    const speak = window.ttsMessageButton(box, speaker);
    if (speak) box.appendChild(speak);
  }
  if (time) addStamp(box, time);
  const log = $("log");
  // A message the reader just sent always comes into view; other lines follow only when already at the bottom.
  const follow = nearBottom(log) || (cls || "").includes("mine");
  log.appendChild(box);
  if (follow) log.scrollTop = log.scrollHeight;
  return box;
}

// Thinking panel: a collapsible block above a model's answer. Collapsed, it shows only the latest
// line of reasoning; open, it shows the whole thinking so far. It streams live either way.
function thinkingPanel(box) {
  let panel = box.querySelector(".think");
  if (panel) return panel;
  panel = el("details", null, "think");
  panel.open = false;
  const summary = el("summary", tr("Thinking"), "think-summary");
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
  picked.clear();   // positions change with every render, so a pick list never carries over
  if (!messages.length) {
    loadScenarioDraft(current);   // an opening for a new chat, if the account asked for one
  } else {
    messages.forEach((m, i) => {
      const isEvent = m.speaker === "Narrator";   // random events are saved as user-side entries, shown as notes
      const box = addLine(m.speaker, m.text, isEvent ? "note event" : (m.role === "user" ? "mine" : "theirs"), m.time);
      if (m.scheduled) box.prepend(el("div", tr("Check-in"), "checkin-tag"));   // sent on the character's schedule
      box.dataset.idx = String(i);   // position in the saved chat, used to place the context marker
      addEditButton(box, i, m);
      addBranchButton(box, i);
      if (m.role === "user" && !isEvent && me && me.persona) colourLine(box, me.persona.name);
      if (m.thinking) setThinking(box, m.thinking);
    });
  }
  placeTurnTools(messages);
  applyPickBoxes();
  updatePickBar();
  refreshContext();
}

// A new chat can offer one of the account's scenarios as its opening. It is shown in the window with a Start button
// and is never sent on its own. Pressing Start sends the text as the reader's first message; Not now hides it.
let scenarioDraftToken = 0;
async function loadScenarioDraft(name) {
  const token = ++scenarioDraftToken;
  if (!name) return;
  let data;
  try {
    data = await api("GET", "/api/scenario-draft?character=" + encodeURIComponent(name));
  } catch (err) {
    data = {};
  }
  const log = $("log");
  // The reader switched chats, a newer load started, or the chat got its first message while this was in flight.
  if (token !== scenarioDraftToken || name !== current || log.children.length) return;
  if (!data.scenario) {
    addLine("", "No messages yet. Say hello!", "note");
    return;
  }
  const box = el("div", null, "line scenario");
  box.appendChild(el("div", tr("Starting scene: {name}", { name: data.scenario.name }), "speaker"));
  const body = el("div", null, "text");
  renderMarkdown(data.scenario.text, body);
  box.appendChild(body);
  const bar = el("div", null, "scenario-actions");
  const start = el("button", tr("Start"), "small");
  start.type = "button";
  start.title = tr("Send this opening message");
  const skip = el("button", tr("Not now"), "ghost small");
  skip.type = "button";
  skip.title = tr("Hide this opening and write your own message");
  bar.append(start, skip);
  box.appendChild(bar);
  const follow = nearBottom(log);
  log.appendChild(box);
  if (follow) log.scrollTop = log.scrollHeight;
  start.addEventListener("click", () => {
    if (busy || name !== current) return;
    box.remove();
    sendMessage(data.scenario.text);
  });
  skip.addEventListener("click", () => { box.remove(); $("msg").focus(); });
}

function addBranchButton(box, i) {
  const btn = el("button", tr("Branch"), "ghost small branch-btn");
  btn.type = "button";
  btn.title = tr("Start a new branch from this message. The current chat stays as it is.");
  btn.setAttribute("aria-label", tr("Branch from this message"));
  btn.addEventListener("click", () => branchFrom(i));
  box.appendChild(btn);
}

async function branchFrom(index) {
  const name = prompt(tr("Name for the new branch (optional)"), "");
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
  pick.replaceChildren(new Option(tr("Main chat"), "main"));
  for (const item of data.items) pick.append(new Option(item.name, item.id));
  pick.value = data.active;
  pick.hidden = data.items.length === 0;   // the picker appears once a branch exists
  $("branchDel").hidden = data.active === "main";
  // Admins always see the Tree button, since they can open any account's tree from it.
  $("branchTree").hidden = !(me && me.is_admin) && data.items.length === 0;
}

// Editing a saved message: the text opens in a box with Save and Cancel. Saving changes the saved chat only; no model call.
function addEditButton(box, i, m) {
  const btn = el("button", tr("Edit"), "ghost small edit-btn");
  btn.type = "button";
  btn.setAttribute("aria-label", tr("Edit this message"));
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
  const save = el("button", tr("Save"), null);
  save.type = "button";
  const cancel = el("button", tr("Cancel"), "ghost small");
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
function renderCreatorRow() {
  // The creator of the open solo character, with a link to that account's page. Groups and novels have none.
  const row = $("charCreatorRow");
  const box = $("charCreator");
  if (!row || !box) return;
  const info = current ? creatorMap.get(current) : null;
  row.hidden = !info;
  box.textContent = "";
  if (!info) return;
  const name = info.creator || "admin";
  if (!info.linkable || name.toLowerCase() === "admin") {
    box.appendChild(el("span", "@" + name, "muted"));
    return;
  }
  const link = el("a", "@" + name, "creator-link");
  link.href = "/u/" + encodeURIComponent(name);
  link.title = tr("View {name}'s profile", { name: name });
  box.appendChild(link);
}

function loadCharSettings() {
  renderCreatorRow();
  for (const n of document.querySelectorAll(".char-only")) n.hidden = !current;
  for (const n of document.querySelectorAll(".needs-char")) n.hidden = !!current;
  $("palaceRow").hidden = !current;
  for (const id of ["tempSlider", "tempReset", "winSlider", "winReset", "exportMd", "exportTxt", "exportJson", "exportEpub", "exportPdf", "pinSave", "pinSettingText", "ciEnabled", "ciEvery", "ciSave", "ciAt", "ciStart", "ciEnd", "dreamEnabled", "dreamEverySelect", "dreamSave", "dreamStart", "dreamEnd", "dreamBubblesBtn", "offEnabled", "offQuick", "offWrite"]) $(id).disabled = !current;
  $("charMsg").textContent = "";
  refreshStateCards();
  if (!current) return;
  loadOffQuick();
  api("GET", "/api/character-settings?character=" + encodeURIComponent(current))
    .then((d) => applyCharSettings(d))
    .catch((err) => { $("charMsg").textContent = err.message; });
}

function applyCharSettings(d) {
  const t = d.temperature == null ? d.temperature_default : d.temperature;
  $("tempSlider").value = t;
  $("tempVal").textContent = Number(t).toFixed(2) + (d.temperature == null ? " " + tr("(default)") : "");
  $("tempReset").disabled = d.temperature == null;
  const w = d.window == null ? d.window_default : d.window;
  $("winSlider").value = w;
  $("winVal").textContent = w + (d.window == null ? " " + tr("(default)") : "");
  $("winReset").disabled = d.window == null;
  $("winNote").textContent = tr("Messages from the chat sent to the model with each reply, at least this many: the window moves in steps of 10, so it can run up to 9 messages over. Default: {solo} for 1:1 chats, {group} for group chats.", { solo: d.window_defaults.solo, group: d.window_defaults.group });
}

async function saveCharSettings(patch) {
  if (!current) return;
  try {
    applyCharSettings(await api("POST", "/api/character-settings", { character: current, ...patch }));
    $("charMsg").textContent = tr("Saved. Applies from your next message.");
  } catch (err) {
    $("charMsg").textContent = err.message;
    loadCharSettings();
  }
}

async function saveWindow(value) {
  try {
    await api("POST", "/api/settings", { window: value });
    loadCharSettings();
    $("charMsg").textContent = tr("Saved. Applies from your next message.");
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
$("replySlider").addEventListener("input", () => { $("replyVal").textContent = tr("{n} tokens", { n: $("replySlider").value }); });
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
  prev.setAttribute("aria-label", tr("Previous reply"));
  prev.disabled = busy || v.index <= 1;
  prev.addEventListener("click", () => swipe(-1));
  const count = el("span", `${v.index} / ${v.count}`, "muted turn-count");
  const next = el("button", "\u25B6", "ghost small");
  next.type = "button";
  next.setAttribute("aria-label", tr("Next reply"));
  next.disabled = busy || v.index >= v.count;
  next.addEventListener("click", () => swipe(1));
  const regen = el("button", "\u21BB Regenerate", "ghost small");
  regen.type = "button";
  const atLimit = v.count - 1 >= v.limit;
  regen.title = atLimit ? tr("This message has reached its regeneration limit.")
    : tr("Write a new reply to your last message. The current reply is kept; swipe back to it any time.");
  regen.disabled = busy || atLimit;
  regen.addEventListener("click", regenerate);
  // Delete the last message, drawn as a trash can. Undo is the button that takes the whole turn away.
  const del = el("button", null, "ghost small del-btn");
  del.type = "button";
  del.title = tr("Delete the last message. Undo removes the whole last turn instead.");
  del.setAttribute("aria-label", tr("Delete the last message"));
  del.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/></svg>';
  del.disabled = busy;
  del.addEventListener("click", () => deleteLastMessage(messages.length - 1));
  bar.append(prev, count, next, regen, del);
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

// The next prompt's size, and how much of it the provider can reuse from its cache: the system text and the window
// texts sent last time, up to the first change. The text still in the input box is all new.
function nextPromptTokens() {
  if (!ctxInfo) return null;
  const typed = Math.ceil(($("msg").value || "").length / 4);
  const total = ctxInfo.system_tokens + ctxInfo.window_tokens + typed;
  return { total, cached: Math.min(ctxInfo.cached_tokens || 0, total) };
}

// Quenching and clipping savings for the next prompt, when either is on.
function promptSaved() {
  if (!ctxInfo) return 0;
  return (ctxInfo.quench ? (ctxInfo.quench_saved || 0) : 0) + (ctxInfo.clip ? (ctxInfo.clip_saved || 0) : 0);
}

function updateEstimate() {
  const label = document.querySelector("#log .ctx-est");
  if (!label) return;
  const n = nextPromptTokens();
  if (n === null) {
    label.textContent = "";
    return;
  }
  const fresh = n.total - n.cached;
  // One figure per line: the whole prompt, the part the provider can reuse, and the part that is new.
  const lines = ["prompt \u2248 " + n.total.toLocaleString() + " tokens",
                 "\u2248 " + n.cached.toLocaleString() + " cached",
                 "\u2248 " + fresh.toLocaleString() + " new"];
  const saved = promptSaved();
  if (saved) lines.push(saved.toLocaleString() + " saved");
  label.textContent = lines.join("\n");
  label.title = tr("Estimates, about 4 characters a token. Reused from cache: the part of this prompt the provider can reuse, which holds only while the system text and the older messages stay the same as the last reply's.");
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
  edit.title = tr("Edit {name}", { name: name });
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
  edit.title = tr("Edit {name}", { name: p.name });
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
  const none = el("button", tr("No persona"), "char-item persona-item");
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
  $("personaFormTitle").textContent = p ? tr("Edit persona") : tr("New persona");
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
  if (!confirm(tr("Delete this persona? Your chats stay, but it is no longer used."))) return;
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
// The reasoning levels the account's tier allows for the saved model. The row shows only with thinking on.
function renderReasoning() {
  const info = (me && me.reasoning) || {};
  const levels = Array.isArray(info.levels) && info.levels.length ? info.levels : ["low"];
  const sel = $("reasoningSelect");
  sel.textContent = "";
  for (const level of levels) {
    const label = level.charAt(0).toUpperCase() + level.slice(1);
    sel.appendChild(el("option", tr(label), null));
    sel.options[sel.options.length - 1].value = level;
  }
  sel.value = levels.includes(info.level) ? info.level : levels[0];
  sel.disabled = levels.length < 2;
  $("reasoningRow").hidden = !(me && me.thinking);
}

function renderModels() {
  const sel = $("modelSelect");
  sel.textContent = "";
  sel.appendChild(el("option", tr("Automatic (Gemini fallback chain)"), null));
  sel.options[0].value = "";
  for (const name of (me && me.models && me.models.extra) || []) {
    const o = el("option", name, null);
    o.value = name;
    sel.appendChild(o);
  }
  const saved = (me && me.model) || "";
  if (saved && ![...sel.options].some((o) => o.value === saved)) {
    // The saved choice is kept even when the current tier no longer lists it, so the picker does not reset.
    const o = el("option", saved, null);
    o.value = saved;
    sel.appendChild(o);
  }
  sel.value = saved;
  $("thinkToggle").checked = !!(me && me.thinking);
  renderReasoning();
  if (me && me.avatar_shape) applyAvatarShape(me.avatar_shape);
  $("avatarShape").value = (me && me.avatar_shape) || "squircle";
  if (me) setLook(me.bg_shade || 0, me.bubble_opacity || 72);
  renderReplyLength();
  renderAccountPrefs();
}

// ---- Account profile settings. The block shows only when the server sends a profile: the built-in
// admin account has no page, so there it stays hidden.
function renderProfileSettings(resetBio = true) {
  const box = $("profileSettings");
  if (!box) return;
  renderSideProfile(me);
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
  if (p.has_background) {
    // A video background previews as its still frame; the poster is the only thing an <img> can show.
    background.src = p.background_video
      ? "/api/account-background-poster/" + encodeURIComponent(me.username) + "?v=" + (p.poster_version || 0)
      : "/api/account-background/" + encodeURIComponent(me.username) + "?v=" + (p.background_version || 0);
  }
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
    $("profileMsg").textContent = tr("Description saved.");
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
      ? tr("Liked characters are visible on your profile.")
      : tr("Liked characters are private.");
  } catch (err) {
    $("profileMsg").textContent = err.message;
    renderProfileSettings(false);
  }
}

async function uploadProfileImage(kind, file) {
  if (!file) return;
  $("profileMsg").textContent = tr("Uploading...");
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
    $("profileMsg").textContent = kind === "avatar" ? tr("Profile picture saved.") : tr("Profile background saved.");
  } catch (err) {
    $("profileMsg").textContent = err.message;
  }
}

async function removeProfileImage(kind) {
  const label = kind === "avatar" ? tr("profile picture") : tr("profile background");
  if (!confirm(tr("Remove your {what}?", { what: label }))) return;
  $("profileMsg").textContent = "";
  try {
    me.profile = await api("DELETE", "/api/account/" + kind);
    renderProfileSettings(false);
    $("profileMsg").textContent = tr("Removed.");
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
  $("replyVal").textContent = v == null ? tr("(no target)") : tr("{n} tokens", { n: v });
  $("replyReset").disabled = v == null;
  $("replyNote").textContent = tr("An aim for how long each reply should be, in tokens, not counting reasoning. The model is guided, not cut off. The server admin sets the range: {lo} to {hi} tokens.", { lo: lo, hi: hi });
}

// Background brightness and bubble opacity: dragging previews at once, release saves to the account.
function setLook(shade, bubble) {
  const s = Math.max(-100, Math.min(100, Math.round(shade) || 0));
  const b = Math.max(30, Math.min(100, Math.round(bubble) || 72));
  applyLook(s, b);
  $("shadeSlider").value = s;
  $("shadeVal").textContent = s === 0 ? tr("Default") : s < 0 ? tr("Darker {n}", { n: -s }) : tr("Lighter {n}", { n: s });
  $("bubbleSlider").value = b;
  $("bubbleVal").textContent = b + "%";
}

async function saveLook(key, value) {
  try {
    const data = await api("POST", "/api/settings", { [key]: value });
    me[key] = data[key];
    $("settingsMsg").textContent = tr("Saved.");
  } catch (err) {
    $("settingsMsg").textContent = err.message;
  }
}

async function saveAvatarShape(shape) {
  try {
    const data = await api("POST", "/api/settings", { avatar_shape: shape });
    me.avatar_shape = data.avatar_shape;
    applyAvatarShape(me.avatar_shape);
    $("settingsMsg").textContent = tr("Saved.");
  } catch (err) {
    $("settingsMsg").textContent = err.message;
    $("avatarShape").value = me.avatar_shape || "squircle";
  }
}

// Near-lossless token quenching: this account's own switch, under Account. It applies from the next reply, and the
// chat's context marker then reports how much of that reply's window was trimmed.
function renderAccountPrefs() {
  const on = !!(me && me.quench);
  $("quenchToggle").checked = on;
  $("quenchNote").textContent = on
    ? tr("On. Older messages are trimmed for each reply, within the fidelity floor.")
    : tr("Off. Nothing is trimmed from the messages the model gets.");
  const clip = !!(me && me.clip);
  $("clipToggle").checked = clip;
  $("clipNote").textContent = clip
    ? tr("On. Messages older than the newest 8 or more are cut to about 550 characters; the summary keeps what is cut.")
    : tr("Off. Older messages are sent in full.");
}

async function saveClip(on) {
  try {
    const data = await api("POST", "/api/settings", { clip: on });
    if (me) me.clip = !!data.clip;
    renderAccountPrefs();
    refreshContext();
  } catch (err) {
    $("clipNote").textContent = err.message;
    renderAccountPrefs();
  }
}
$("clipToggle").addEventListener("change", (e) => saveClip(e.target.checked));

async function saveQuench(on) {
  try {
    const data = await api("POST", "/api/settings", { quench: on });
    if (me) me.quench = !!data.quench;
    renderAccountPrefs();
    if (on) $("quenchNote").textContent = tr("On. Applies from your next reply.");
    refreshContext();
  } catch (err) {
    $("quenchNote").textContent = err.message;
    renderAccountPrefs();
  }
}
$("quenchToggle").addEventListener("change", (e) => saveQuench(e.target.checked));

async function saveReplyLength(value) {
  try {
    const data = await api("POST", "/api/settings", { reply_length: value });
    me.reply_length = data.reply_length;
    renderReplyLength();
    $("settingsMsg").textContent = tr("Saved. Applies from your next message.");
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
    if (data.reasoning) me.reasoning = data.reasoning;
    renderModels();
    $("settingsMsg").textContent = tr("Saved. Applies from your next message.");
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
  setChatHeader(displayName(current || ""));
  renderCreatorRow();
}

async function loadCharacters(choose = true) {
  const data = await api("GET", "/api/characters");
  colorMap = data.colors || {};
  iconSet = new Set(data.icons || []);
  bgIds = data.bg_ids || {};
  musicByName = new Map((data.cards || []).map((card) => [card.name, card.music || []]));
  defaultArt = data.default_backgrounds || [];
  editableSet = new Set(data.editable || []);
  favSet = new Set(data.favourites || []);
  popularSet = new Set(data.popular || []);
  creatorMap.clear();
  for (const card of data.cards || []) {
    creatorMap.set(card.name, { creator: card.creator || "admin", linkable: card.creator_profile !== false });
  }
  soloNames = data.solo || [];   // set before the lists are drawn: the star check reads it
  renderPersonas(data.personas || []);
  const solo = $("charList");
  const groups = $("groupList");
  solo.textContent = "";
  groups.textContent = "";
  // Only characters this account has chatted with. Every character still counts for links and groups below.
  for (const name of data.sidebar || []) solo.appendChild(charButton(name));
  if (!(data.sidebar || []).length) {
    solo.appendChild(el("p", tr("Characters you chat with appear here. Pick one from the home page to start."), "muted"));
  }
  for (const g of data.groups) groups.appendChild(g.private ? privateGroupRow(g) : charButton(g.name));
  $("groupBox").hidden = false;   // always shown, so any account can start its own group
  renderNovels(data.novels || []);
  worldCache = { worlds: data.worlds || [], default: data.world_default || null };
  paintUnread();
  soloNames = data.solo || [];
  groupNames = new Set((data.groups || []).map((g) => g.name));
  novelIds = new Set((data.novels || []).map((n) => n.id));
  const all = [...data.solo, ...data.groups.map((g) => g.name), ...(data.novels || []).map((n) => n.id)];
  // A card on the home page sends the name as ?c=; that wins over the last character used.
  const asked = new URLSearchParams(location.search).get("c");
  const saved = localStorage.getItem("rp_character");
  const start = all.includes(asked) ? asked : (all.includes(saved) ? saved : ((data.sidebar || [])[0] || null));
  if (!choose) { markActive(); return; }   // refresh only: keep the open chat
  if (start) await pickCharacter(start);
  else { current = null; if (window.ttsSetCharacter) window.ttsSetCharacter("", true); markActive(); $("chatSection").hidden = true; }   // nothing to open yet: no empty chat window
  if (start) await applySearchJump(start);
}

// A search result opens /chat with the branch and message that matched. The chat is already drawn
// from its active branch, so switch first when the hit lives on another one, then scroll to the line.
async function applySearchJump(name) {
  const params = new URLSearchParams(location.search);
  const branch = params.get("branch");
  const msg = params.get("msg");
  if (!branch && msg === null) return;
  if (branch) {
    try {
      const list = await api("GET", "/api/branches?character=" + encodeURIComponent(name));
      if (list.active !== branch) {
        const data = await api("POST", "/api/branch/switch", { character: name, id: branch });
        if (name === current) applyBranchState(name, data);
      }
    } catch (err) {
      $("chatErr").textContent = err.message;
    }
  }
  if (msg !== null && /^\d+$/.test(msg)) {
    const line = document.querySelector('#log .line[data-idx="' + msg + '"]');
    if (line) {
      line.scrollIntoView({ block: "center", behavior: "smooth" });
      line.classList.add("search-hit");
      setTimeout(() => line.classList.remove("search-hit"), 4000);
    }
  }
  params.delete("branch");
  params.delete("msg");
  const clean = location.pathname + (params.toString() ? "?" + params.toString() : "");
  history.replaceState(null, "", clean);
}

// A character's background sits behind its chat, and the whole page takes it. A picture goes through the
// --chat-art variable; a video needs a real element, so it plays in the fixed layer beside the shell. When
// the reader asked for reduced motion a video is shown as its poster frame, and never starts.
function applyChatArt(name) {
  const section = $("chatSection");
  if (!section) return;
  const ids = name ? (bgIds[name] || []) : [];
  // A chat with no backgrounds of its own (solo, group or novel) shows the animated circles, tinted by its mood and affection.
  if (name && !ids.length) {
    clearChatArt(section);
    applyAmbientMood(name);
    return;
  }
  // No character at all takes a shared default.
  const pool = ids.length ? ids : lookDefaults(defaultArt);
  clearChatArt(section);
  if (!pool.length) return;
  // One piece is chosen at random each time the page or character opens.
  const chosenId = pool[Math.floor(Math.random() * pool.length)];
  const chosen = artUrl(name, chosenId);
  const still = posterFor(chosenId);
  section.classList.add("has-art");
  document.body.classList.add("page-art");
  if (isVideoArt(chosenId) && !motionReduced()) {
    const host = $("pageArtVideo");
    const video = $("pageArtVideoEl");
    if (host && video) {
      if (still) video.poster = artUrl(name, still);
      else video.removeAttribute("poster");
      // The WebM first, the H.264 MP4 second, so a phone that cannot play VP9 still gets the video.
      setArtSources(video, chosen, artFallbackUrl(name, chosenId));
      host.hidden = false;
      setArtPlaying(host, true);
      return;
    }
  }
  const art = 'url("' + (still ? artUrl(name, still) : chosen) + '")';
  section.style.setProperty("--chat-art", art);
  document.body.style.setProperty("--chat-art", art);
}

// Reads the character's energy (mood) and affection (attitude), and tints the circles by them. A feature that is
// switched off, or a character that cannot be read, leaves the plain colours; so does a reply that arrives after
// the reader has moved on to another character.
async function applyAmbientMood(name) {
  let warm = 0, green = 0;
  try {
    const [m, a] = await Promise.all([
      api("GET", "/api/character-state?character=" + encodeURIComponent(name)),
      api("GET", "/api/character-attitude?character=" + encodeURIComponent(name)),
    ]);
    if (m.enabled) green = Math.min(1, Math.max(0, m.energy / 100));
    if (a.enabled) warm = Math.min(1, Math.max(0, a.affection / 100));
  } catch (err) {
    return;
  }
  if (current !== name) return;
  setAmbientMood(warm, green);
}

// Takes the background away: the picture variables, the classes and the video element's source.
function clearChatArt(section) {
  setAmbientMood(0, 0);
  section.style.removeProperty("--chat-art");
  document.body.style.removeProperty("--chat-art");
  section.classList.remove("has-art");
  document.body.classList.remove("page-art");
  const host = $("pageArtVideo");
  const video = $("pageArtVideoEl");
  if (video) {
    video.pause();
    for (const s of [...video.querySelectorAll("source")]) s.remove();
    video.removeAttribute("src");
    video.removeAttribute("poster");
    video.load();
  }
  if (host) host.hidden = true;
}

async function pickCharacter(name) {
  if (busy) return;
  current = name;
  if (window.ttsSetCharacter) window.ttsSetCharacter(name, soloNames.includes(name));
  if (picking) setPicking(false);   // a pick list belongs to the chat it was made in
  markRead(name);   // opening the chat marks its missed check-ins as read
  localStorage.setItem("rp_character", name);
  markActive();
  applyChatArt(name);
  // A character's theme music plays while its chat is open. A group, a novel or a character with no tracks is silent.
  themeMusic.set("char:" + name, musicByName.get(name) || null);
  closeSidebar();
  showMemory(false);
  showBible(false);   // a bible open for the last chat is closed when another character opens
  $("chatErr").textContent = "";
  $("log").classList.toggle("novel", isNovelName(name));   // a novel's replies are drawn as prose
  refreshStory().catch(() => {});
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
  // The reveal advances on every frame, but the markdown rebuild is heavy, so it runs a few times a second.
  const PAINT_MS = 33;
  const st = { target: "", shown: 0, last: null, arrivals: [], rate: 120, raf: null, lastFrame: 0,
               lastPaint: 0, finishing: false, resolveDone: null, cancelled: false };
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
      if (now - st.lastPaint >= PAINT_MS || st.shown >= st.target.length) {
        st.lastPaint = now;
        paint();
      }
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
  $("msg").value = "";
  await sendMessage(text);
}

// Send one message as the reader. Shared by the composer and by a scenario's Start button.
async function sendMessage(text) {
  text = String(text || "").trim();
  if (busy || !current || !text) return;
  const who = current;
  setBusy(true);
  $("chatErr").textContent = "";
  const mine = me && me.persona ? me.persona.name : "You";
  const mineBox = addLine(mine, text, "mine", new Date().toISOString());
  if (me && me.persona) colourLine(mineBox, me.persona.name);
  await streamTurn("/api/chat", { character: who, message: text }, who);
  // The sidebar ranks solo characters by recent activity, so refresh only when this chat's place there can change.
  if (who === current && soloNames.includes(who)) {
    const first = $("charList").querySelector(".char-item");
    if (!first || first.dataset.name !== who) loadCharacters(false).catch(() => {});
  }
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
      const log = $("log");
      const follow = nearBottom(log);
      renderMarkdown(txt || "mrrp~ thinking…", node);
      if (follow) log.scrollTop = log.scrollHeight;
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
  let commandNotice = null;
  let commandReload = null;
  try {
    await streamChat(body, {
      delta(p) { if (who === current) show(p.speaker, p.text); },
      thinking(p) {
        if (who !== current) return;
        dropThinking();
        ensureBubble(p.speaker);
        const log = $("log");
        const follow = nearBottom(log);
        setThinking(bubbles[p.speaker], p.text);
        if (follow) log.scrollTop = log.scrollHeight;
      },
      done(data) {
        flushing = Promise.all(Object.keys(reveals).map((k) => reveals[k].finish())).then(() => {
          clearBubbles();
          if (who !== current) return;
          for (const r of data.replies) {
            const box = addLine(r.speaker, r.text, "theirs", new Date().toISOString());
            if (r.thinking) setThinking(box, r.thinking);
            if (window.ttsAutoSpeak) window.ttsAutoSpeak(box, r.speaker);
          }
          if (data.selector_note) addLine("", data.selector_note, "note");
          refreshStateCards();   // energy and the relationship change with each reply
        });
      },
      command(c) {
        clearBubbles();
        if (who !== current) return;
        commandNotice = c.notice || "";
        if (c.reload) commandReload = loadHistory().catch(() => {});
        else if (commandNotice) addLine("", commandNotice, "note");
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
    if (who === current) {
      if (commandReload) {
        await commandReload.catch(() => {});
        if (commandNotice) addLine("", commandNotice, "note");
      } else if (commandNotice === null) {
        await loadHistory().catch(() => {});   // reload so positions and the marker match the saved chat
      }
    }
    setBusy(false);
    $("msg").focus();
  }
}

// Regenerate: drop the old reply from the view (the server keeps it as an alternative) and stream a new one.
async function regenerate() {
  if (busy || !current) return;
  let anchor = null;
  for (const n of $("log").children) if (n.classList.contains("mine")) anchor = n;
  if (!anchor) { $("chatErr").textContent = tr("Nothing to regenerate yet."); return; }
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

// Deleting saved messages. Undo takes the last turn away; these take any messages the user names. The chat is this
// account's own copy, and the server makes no model call, so nothing is spent.
async function deleteMessages(indexes) {
  if (busy || !current || !indexes.length) return;
  const who = current;
  setBusy(true);
  $("chatErr").textContent = "";
  try {
    const data = await api("POST", "/api/delete", { character: who, indexes });
    if (who !== current) return;
    renderHistory(data.messages);
    $("chatErr").textContent = trn("{n} message deleted.", "{n} messages deleted.", data.removed);
  } catch (err) {
    if (who === current) $("chatErr").textContent = err.message;
  } finally {
    setBusy(false);
  }
}

// The trash beside Regenerate takes the last message only, which is the reply those tools act on. Undo still takes
// the whole last turn.
async function deleteLastMessage(index) {
  if (busy || !current || index < 0) return;
  if (!confirm(tr("Delete the last message? This cannot be undone."))) return;
  await deleteMessages([index]);
}

// Select mode: a pick box on every saved message, and a bar with the count and the delete button.
function setPicking(on) {
  picking = on;
  picked.clear();
  $("selectBtn").setAttribute("aria-pressed", String(on));
  $("selectBtn").classList.toggle("active", on);
  $("pickBar").hidden = !on;
  $("log").classList.toggle("picking", on);
  applyPickBoxes();
  updatePickBar();
}

function applyPickBoxes() {
  for (const line of document.querySelectorAll("#log .line")) {
    if (line.dataset.idx === undefined) continue;   // the "no messages yet" note is not a saved message
    let box = line.querySelector(".pick-box");
    if (!picking) {
      if (box) box.remove();
      line.classList.remove("picked");
      continue;
    }
    if (!box) {
      box = el("input", null, "pick-box");
      box.type = "checkbox";
      box.setAttribute("aria-label", tr("Pick this message"));
      box.addEventListener("change", () => setPicked(line.dataset.idx, box.checked));
      line.prepend(box);
    }
    box.checked = picked.has(line.dataset.idx);
    line.classList.toggle("picked", box.checked);
  }
}

function setPicked(idx, on) {
  if (idx === undefined) return;
  if (on) picked.add(String(idx)); else picked.delete(String(idx));
  const line = document.querySelector('#log .line[data-idx="' + idx + '"]');
  if (line) line.classList.toggle("picked", !!on);
  updatePickBar();
}

function updatePickBar() {
  const n = picked.size;
  $("pickCount").textContent = n ? tr("{n} picked", { n: n }) : tr("Pick messages to delete");
  $("pickDelete").disabled = busy || n === 0;
  $("pickDelete").textContent = n ? tr("Delete {n}", { n: n }) : tr("Delete picked");
}

// Clicking anywhere on a line picks it, except on a button, a link or the box itself (which fires change).
$("log").addEventListener("click", (e) => {
  if (!picking) return;
  const line = e.target.closest(".line");
  if (!line) return;
  if (e.target.closest("button, a, textarea, input")) return;
  const box = line.querySelector(".pick-box");
  if (!box) return;
  box.checked = !box.checked;
  setPicked(line.dataset.idx, box.checked);
});

$("selectBtn").addEventListener("click", () => setPicking(!picking));
$("pickCancel").addEventListener("click", () => setPicking(false));
$("pickNone").addEventListener("click", () => { picked.clear(); applyPickBoxes(); updatePickBar(); });
$("pickAll").addEventListener("click", () => {
  for (const line of document.querySelectorAll("#log .line")) if (line.dataset.idx !== undefined) picked.add(line.dataset.idx);
  applyPickBoxes();
  updatePickBar();
});
$("pickDelete").addEventListener("click", () => {
  const indexes = [...picked].map(Number).filter(Number.isInteger);
  if (!indexes.length) return;
  const what = indexes.length === 1 ? tr("this message") : tr("these {n} messages", { n: indexes.length });
  if (!confirm(tr("Delete {what} from this chat? This cannot be undone.", { what: what }))) return;
  setPicking(false);
  deleteMessages(indexes);
});

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
  renderSideProfile(me);
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
  openRequestedEditor();
}

// /chat?edit=<name> opens that character's editor. The character wizard's done screen links here. The address is
// tidied at once, so a refresh does not open the editor again. editor.js may not have run yet, so it waits for load.
function openRequestedEditor() {
  const name = new URLSearchParams(location.search).get("edit");
  if (!name) return;
  history.replaceState(null, "", "/chat");
  const open = () => openEditor(name).catch((err) => { $("chatErr").textContent = err.message; });
  if (typeof openEditor === "function") open();
  else window.addEventListener("load", open, { once: true });
}

$("chatForm").addEventListener("submit", send);

async function refreshMe() {
  try { me = await api("GET", "/api/me"); renderUsage(); } catch (err) { /* the usage bar keeps its last values */ }
}

// Auto-fill: the model drafts the user's next message into the box. The user reads it, edits it, then sends it.
async function autoFill() {
  if (busy || !current) return;
  const who = current;
  if ($("msg").value.trim()) { $("chatErr").textContent = tr("Clear the message box first, then Auto-fill."); return; }
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
  const wasAtBottom = nearBottom(log);
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
// The keyboard only opens while the message box has focus, so the 300ms backstop runs only then.
let fitTimer = null;
$("msg").addEventListener("focus", () => { if (!fitTimer) fitTimer = setInterval(fitToVisibleArea, 300); });
$("msg").addEventListener("blur", () => { clearInterval(fitTimer); fitTimer = null; fitToVisibleArea(); });
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
$("reasoningSelect").addEventListener("change", (e) => saveSettings({ reasoning: e.target.value }));
$("avatarShape").addEventListener("change", (e) => saveAvatarShape(e.target.value));
$("modelSelect").addEventListener("change", (e) => saveSettings({ model: e.target.value }));
$("editNewBtn").addEventListener("click", () => { location.href = "/create/character"; });
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
  if (!confirm(tr("Delete this branch? Its messages and memory are removed. The main chat is kept."))) return;
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

$("pinSave").addEventListener("click", async () => {
  if (!pinFor || busy) return;
  $("pinMsg").textContent = "";
  try {
    // Only the pinned part changes; the memory bank and summary are sent back as they were read.
    const data = await api("POST", "/api/memory", { character: pinFor, memory: pinLoaded.memory, pinned: $("pinSettingText").value });
    pinLoaded = data;
    $("pinMsg").textContent = tr("Saved. The next reply includes these facts.");
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
    $("memTitle").textContent = tr("Memory: {name}", { name: who });
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
    $("memOk").textContent = tr("Saved. The next reply uses this.");
  } catch (err) {
    $("memErr").textContent = err.message;
  }
}

async function refreshMemory() {
  if (!memFor || busy) return;
  if ($("memText").value !== memLoaded.memory || $("pinText").value !== memLoaded.pinned || $("sumText").value !== memLoaded.summary) {
    $("memErr").textContent = tr("Save your edits first, then refresh.");
    return;
  }
  $("memErr").textContent = "";
  $("memOk").textContent = tr("Refreshing from the chat...");
  try {
    const data = await api("POST", "/api/chat", { character: memFor, message: "/save" });
    const r = await api("GET", "/api/memory?character=" + encodeURIComponent(memFor));
    memLoaded = { memory: r.memory, pinned: r.pinned, summary: r.summary };
    $("memText").value = r.memory;
    $("sumText").value = r.summary || "";
    $("memOk").textContent = (data.command && data.command.notice) || tr("Refreshed.");
  } catch (err) {
    $("memErr").textContent = err.message;
    $("memOk").textContent = "";
  }
}

$("memBtn").addEventListener("click", openMemory);
$("memBack").addEventListener("click", () => showMemory(false));
$("memSave").addEventListener("click", saveMemory);
$("memRefresh").addEventListener("click", refreshMemory);

// The story bible: a wiki page of this chat, built from every memory plus the first and last 30 messages.
// Characters met, places visited, items acquired, open questions and key events, with an overview on top.
// With the account switch on it is rebuilt after every dream; otherwise the reader rebuilds it by hand.
let bibleFor = null;

const BIBLE_HEADINGS = {
  characters_met: "Characters met",
  places_visited: "Places visited",
  items_acquired: "Items acquired",
  open_questions: "Open questions",
  key_events: "Key events",
};

function showBible(on) {
  $("bibleSection").hidden = !on;
  $("chatSection").hidden = on;
}

function renderBible(data) {
  $("bibleAuto").checked = !!data.on;
  showBibleNote(data);
  $("bibleErr").textContent = "";
  const body = $("bibleBody");
  body.textContent = "";
  const bible = data.bible;
  if (!bible) {
    $("bibleMeta").textContent = "";
    body.appendChild(el("p", tr("Nothing written yet. Regenerate to build the bible from everything remembered so far."), "muted"));
    return;
  }
  const when = bible.when ? MISO_I18N.date(bible.when) : "";
  $("bibleMeta").textContent = when ? tr("Written {when} for {n} messages", { when: when, n: bible.history }) : "";
  const groups = [];
  if (bible.overview) groups.push([tr("Overview"), el("p", bible.overview, "bible-overview")]);
  for (const [key, title] of Object.entries(BIBLE_HEADINGS)) {
    const items = bible[key] || [];
    if (items.length) {
      const list = el("ul", null, "bible-list");
      for (const item of items) list.appendChild(el("li", item));
      groups.push([tr(title), list]);
    }
  }
  if (!groups.length) {
    body.appendChild(el("p", tr("The last generation found nothing worth listing yet."), "muted"));
    return;
  }
  for (const [title, content] of groups) {
    const part = el("div", null, "bible-part");
    part.append(el("h2", title, "bible-head"), content);
    body.appendChild(part);
  }
}

function showBibleNote(data) {
  if (data.on && data.dream_on) {
    $("bibleAutoNote").textContent = tr("On. The bible is rebuilt after every dream of this character, from what it tidied plus everything else remembered.");
  } else if (data.on) {
    $("bibleAutoNote").textContent = tr("On. No dreams are scheduled for this character, so rebuild the bible by hand when you want it fresh.");
  } else {
    $("bibleAutoNote").textContent = tr("Autobuild after dreams is off. Rebuild the bible by hand here whenever you like.");
  }
}

async function openStoryBible() {
  if (busy || !current) return;
  const who = current;
  $("chatErr").textContent = "";
  $("bibleErr").textContent = "";
  try {
    const data = await api("GET", "/api/story-bible?character=" + encodeURIComponent(who));
    bibleFor = who;
    renderBible(data);
    showBible(true);
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}

$("bibleBack").addEventListener("click", () => showBible(false));

$("bibleRegen").addEventListener("click", async () => {
  if (!bibleFor || busy) return;
  $("bibleErr").textContent = "";
  $("bibleMsg").textContent = tr("Reading every memory plus the first and last 30 messages...");
  $("bibleRegen").disabled = true;
  try {
    renderBible(await api("POST", "/api/story-bible/regenerate", { character: bibleFor }));
    $("bibleMsg").textContent = tr("Written.");
  } catch (err) {
    $("bibleErr").textContent = err.message;
    $("bibleMsg").textContent = "";
  } finally {
    $("bibleRegen").disabled = false;
  }
});

$("bibleAuto").addEventListener("change", async (e) => {
  if (!bibleFor) return;
  try {
    await api("POST", "/api/settings", { story_bible: e.target.checked });
    renderBible(await api("GET", "/api/story-bible?character=" + encodeURIComponent(bibleFor)));
    $("bibleMsg").textContent = tr("Saved.");
  } catch (err) {
    $("bibleErr").textContent = err.message;
    e.target.checked = !e.target.checked;
  }
});

// The character's icon and name above the chat open the story bible, like their name on a message.
$("chatIcon").title = tr("Open the story bible");
$("chatTitle").title = tr("Open the story bible");
$("chatIcon").addEventListener("click", openStoryBible);
$("chatTitle").addEventListener("click", openStoryBible);
$("bibleDocBtn").addEventListener("click", openDocImport);
$("docBtnMem").addEventListener("click", openDocImport);

// Document import: a backstory, novel excerpt or setting document is read once and what it may be worth
// remembering is proposed. Memory Palace entries and memory-bank facts are shown for review; nothing
// enters memory until it is picked here.
let docFor = null;
let docMined = null;

function openDocImport() {
  if (!current) return;
  docFor = current;
  docMined = null;
  $("docFile").value = "";
  $("docImportBtn").disabled = true;
  $("docResults").textContent = "";
  $("docMsg").textContent = "";
  $("docSaveMsg").textContent = "";
  $("docSave").disabled = true;
  if (!$("docImportDlg").open) $("docImportDlg").showModal();
}

$("docFile").addEventListener("change", () => {
  $("docImportBtn").disabled = !$("docFile").files.length;
});

async function docMine() {
  const file = $("docFile").files[0];
  if (!file || busy) return;
  $("docMsg").textContent = tr("Reading...");
  $("docImportBtn").disabled = true;
  $("docResults").textContent = "";
  $("docSave").disabled = true;
  try {
    const r = await fetch("/api/memory/import-doc?character=" + encodeURIComponent(docFor), {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream",
                 "x-filename": encodeURIComponent(file.name) },
      credentials: "same-origin",
      body: await file.arrayBuffer(),
    });
    if (!r.ok) {
      let data = {};
      try { data = await r.json(); } catch (e) { /* no body */ }
      throw apiError(r.status, data);
    }
    docMined = await r.json();
    renderDocCandidates();
    $("docMsg").textContent = tr("Pick what to keep, then save.");
  } catch (err) {
    $("docMsg").textContent = err.message;
  } finally {
    $("docImportBtn").disabled = false;
  }
}

function renderDocCandidates() {
  const body = $("docResults");
  body.textContent = "";
  const mined = docMined || {};
  const entries = Array.isArray(mined.palace) ? mined.palace : [];
  const facts = Array.isArray(mined.facts) ? mined.facts : [];
  if (!entries.length && !facts.length) {
    body.appendChild(el("p", tr("Nothing worth keeping was found in this document."), "muted"));
    return;
  }
  if (entries.length) {
    const box = el("div", null, "doc-group");
    box.appendChild(el("h3", tr("Mind Palace entries"), "doc-head"));
    entries.forEach((entry, i) => {
      const row = el("label", null, "doc-candidate");
      const pick = el("input");
      pick.type = "checkbox"; pick.checked = true; pick.dataset.kind = "palace"; pick.dataset.index = String(i);
      row.appendChild(pick);
      row.appendChild(el("span", entry.title || tr("Untitled"), "doc-title"));
      if (entry.keys && entry.keys.length) row.appendChild(el("span", "(" + entry.keys.join(", ") + ")", "muted"));
      row.appendChild(el("span", " " + (entry.content || ""), "doc-text"));
      box.appendChild(row);
    });
    body.appendChild(box);
  }
  if (facts.length) {
    const box = el("div", null, "doc-group");
    box.appendChild(el("h3", tr("Memory facts"), "doc-head"));
    facts.forEach((fact, i) => {
      const row = el("label", null, "doc-candidate");
      const pick = el("input");
      pick.type = "checkbox"; pick.checked = true; pick.dataset.kind = "facts"; pick.dataset.index = String(i);
      row.appendChild(pick);
      row.appendChild(el("span", "[" + fact.section + "]", "doc-section"));
      row.appendChild(el("span", " " + (fact.text || ""), "doc-text"));
      box.appendChild(row);
    });
    body.appendChild(box);
  }
  $("docSave").disabled = false;
}

$("docImportBtn").addEventListener("click", docMine);

$("docSave").addEventListener("click", async () => {
  if (!docMined || busy) return;
  const entries = [], facts = [];
  for (const pick of $("docResults").querySelectorAll("input[data-kind]")) {
    if (!pick.checked) continue;
    const item = (docMined[pick.dataset.kind] || [])[Number(pick.dataset.index)];
    if (!item) continue;
    if (pick.dataset.kind === "palace") entries.push(item); else facts.push(item);
  }
  if (!entries.length && !facts.length) {
    $("docSaveMsg").textContent = tr("Nothing picked.");
    return;
  }
  $("docSaveMsg").textContent = tr("Saving...");
  try {
    const data = await api("POST", "/api/memory/import-doc/apply", { character: docFor, entries: entries, facts: facts });
    // the chat sessions were dropped on the server, so the next reply reads the new memory
    $("docSaveMsg").textContent = tr("Saved: {p} palace entries and {f} facts.", { p: data.palace_added, f: data.facts_added });
    $("docSave").disabled = true;
  } catch (err) {
    $("docSaveMsg").textContent = err.message;
  }
});

$("docCancel").addEventListener("click", () => $("docImportDlg").close());

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
    $("ciEvery").value = String(data.every_days || 1);
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
  const every = Number(data.every_days) || 1;
  if (!data.enabled) {
    $("ciNote").textContent = tr("Off. {name} will not message you on its own.", { name: ciFor || tr("This character") });
  } else if (data.next) {
    $("ciNote").textContent = tr("Today's check-in is set for {next} ({zone}). It is skipped if the server is not running then.", { next: data.next, zone: zone });
  } else if (every === 1) {
    $("ciNote").textContent = tr("On. Once a day, in {zone}. It is skipped if the server is not running at the chosen time.", { zone: zone });
  } else {
    $("ciNote").textContent = tr("On. Once every {n} days, in {zone}. It is skipped if the server is not running at the chosen time.", { n: every, zone: zone });
  }
}

$("checkinCard").addEventListener("click", openCheckin);

// Autonomous dreaming: the quiet hours for this character, saved with the browser's time zone.
let dreamFor = null;
let dreamItems = [];

function showDreamLast(data) {
  $("dreamServerNote").hidden = data.server_on;
  const every = Math.max(Number(data.every_days) || 1, Number(data.floor_days) || 1);
  $("dreamEverySelect").value = String(data.every_days || 1);
  $("dreamEvery").textContent = every === 1 ? tr("A dream can run each night.") : tr("A dream runs at most once every {n} days.", { n: every });
  const last = data.last;
  if (!last) {
    dreamItems = [];
    $("dreamBubblesBtn").disabled = true;
    $("dreamLast").textContent = tr("No dream yet.");
    return;
  }
  dreamItems = Array.isArray(last.items) && last.items.length
    ? last.items
    : (last.note ? [{ kind: "NOTE", text: last.note }] : []);
  $("dreamBubblesBtn").disabled = !dreamItems.length;
  const when = last.when ? MISO_I18N.date(last.when) : "";
  const events = last.events ? tr(", {events} events recorded, {evolved} changes noted", { events: last.events, evolved: last.evolved || 0 }) : "";
  $("dreamLast").textContent = tr("Last dream {when}: {note} ({added} added, {updated} updated, {removed} removed, {dropped} palace entries dropped{events}).", {
    when: when,
    note: last.note || tr("nothing needed tidying"),
    added: last.added,
    updated: last.updated,
    removed: last.removed,
    dropped: last.dropped,
    events: events,
  });
}

// The last dream's work as bubbles: one per memory change, palace drop, event, change of outlook or note. A bubble
// shows a short label and no more than a line of text; clicking it opens the full line.
const DREAM_KIND_LABELS = {
  ADD: "Learned",
  UPDATE: "Corrected",
  REMOVE: "Forgot",
  LINK: "Linked",
  DROP: "Dropped",
  EVENT: "Happened",
  EVOLVE: "Changed",
  NOTE: "Noted",
};

function dreamKindLabel(kind) {
  return tr(DREAM_KIND_LABELS[String(kind || "").toUpperCase()] || "Noted");
}

function dreamLine(text) {
  return String(text || "").replace(/^\[[^\]]+\]\s*/, "").replace(/\s+/g, " ").trim();
}

function dreamShort(text) {
  const line = dreamLine(text);
  return line.length > 18 ? line.slice(0, 18) + "…" : line;
}

function scatterDreamBubbles() {
  const field = $("dreamBubbleField");
  const bubbles = Array.from(field.querySelectorAll(".dream-bubble"));
  if (!bubbles.length) return;
  const fw = field.clientWidth, fh = field.clientHeight;
  const maxW = Math.max(...bubbles.map((b) => b.offsetWidth), 1);
  const cols = Math.max(1, Math.floor(fw / (maxW + 8)));
  const rows = Math.max(1, Math.ceil(bubbles.length / cols));
  const cw = fw / cols, ch = fh / rows;
  bubbles.forEach((b, i) => {
    const bw = b.offsetWidth, bh = b.offsetHeight;
    const x = (i % cols) * cw + Math.max(0, cw - bw - 4) * Math.random();
    const y = Math.floor(i / cols) * ch + Math.max(0, ch - bh - 4) * Math.random();
    b.style.left = Math.max(3, Math.min(fw - bw - 3, x)) + "px";
    b.style.top = Math.max(3, Math.min(fh - bh - 3, y)) + "px";
  });
}

function openDreamBubbles() {
  if (!dreamItems.length) return;
  const field = $("dreamBubbleField");
  field.textContent = "";
  for (const item of dreamItems) {
    const kind = String(item.kind || "NOTE").toUpperCase();
    const text = String(item.text || "");
    const bubble = el("button", null, "dream-bubble");
    bubble.type = "button";
    bubble.dataset.kind = kind;
    bubble.dataset.text = text;
    bubble.append(el("span", dreamKindLabel(kind), "dream-bubble-kind"), el("span", dreamShort(text), "dream-bubble-text"));
    bubble.setAttribute("aria-label", dreamKindLabel(kind) + ": " + dreamLine(text));
    field.appendChild(bubble);
  }
  if (!$("dreamBubblesDlg").open) $("dreamBubblesDlg").showModal();
  requestAnimationFrame(scatterDreamBubbles);
}

function showDreamItem(kind, text) {
  $("dreamItemKind").textContent = dreamKindLabel(kind);
  $("dreamItemText").textContent = dreamLine(text);
  if (!$("dreamItemDlg").open) $("dreamItemDlg").showModal();
}

let dreamDrag = null;

$("dreamBubbleField").addEventListener("pointerdown", (e) => {
  const bubble = e.target.closest(".dream-bubble");
  if (!bubble) return;
  const rect = bubble.getBoundingClientRect();
  dreamDrag = { bubble, id: e.pointerId, dx: e.clientX - rect.left, dy: e.clientY - rect.top, x: e.clientX, y: e.clientY, moved: false };
  bubble.setPointerCapture(e.pointerId);
  bubble.classList.add("dragging");
  e.preventDefault();
});

$("dreamBubbleField").addEventListener("pointermove", (e) => {
  if (!dreamDrag || e.pointerId !== dreamDrag.id) return;
  const field = $("dreamBubbleField");
  const fr = field.getBoundingClientRect(), br = dreamDrag.bubble.getBoundingClientRect();
  let x = e.clientX - fr.left - dreamDrag.dx;
  let y = e.clientY - fr.top - dreamDrag.dy;
  x = Math.max(3, Math.min(fr.width - br.width - 3, x));
  y = Math.max(3, Math.min(fr.height - br.height - 3, y));
  dreamDrag.bubble.style.left = x + "px";
  dreamDrag.bubble.style.top = y + "px";
  if (Math.abs(e.clientX - dreamDrag.x) > 4 || Math.abs(e.clientY - dreamDrag.y) > 4) dreamDrag.moved = true;
});

function endDreamDrag(open) {
  if (!dreamDrag) return;
  const d = dreamDrag;
  dreamDrag = null;
  d.bubble.classList.remove("dragging");
  if (open && !d.moved) showDreamItem(d.bubble.dataset.kind, d.bubble.dataset.text);
}

$("dreamBubbleField").addEventListener("pointerup", (e) => {
  if (dreamDrag && e.pointerId === dreamDrag.id) endDreamDrag(true);
});
$("dreamBubbleField").addEventListener("pointercancel", () => endDreamDrag(false));
$("dreamBubbleField").addEventListener("keydown", (e) => {
  const bubble = e.target.closest(".dream-bubble");
  if (!bubble || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault();
  showDreamItem(bubble.dataset.kind, bubble.dataset.text);
});

async function openDreams() {
  if (busy || !current) return;
  const who = current;
  $("dreamMsg").textContent = "";
  dreamItems = [];
  $("dreamBubblesBtn").disabled = true;
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

// One bar built on the fly, for the lists that have no fixed ids: a label, a note, and a fill from 0 to 1.
function metricRow(label, share, note) {
  const box = el("div", null, "metric");
  const head = el("div", null, "metric-head");
  head.append(el("span", label, null), el("span", note || "", "muted"));
  const track = el("div", null, "bar-track");
  const fill = el("div", null, "bar-fill");
  fill.style.width = Math.max(0, Math.min(100, (Number(share) || 0) * 100)) + "%";
  track.appendChild(fill);
  box.append(head, track);
  return box;
}

// Learned memory retention: the memories the character wrote for itself, strongest first, with the score that keeps
// them. Read only; the admin switch decides whether the scores are kept at all.
async function refreshRetention() {
  const box = $("retentionBox");
  if (!current) {
    box.hidden = true;
    return;
  }
  const who = current;
  try {
    const data = await api("GET", "/api/memory-policy?character=" + encodeURIComponent(who));
    if (current !== who) return;
    box.hidden = !data.enabled;
    if (!data.enabled) return;
    $("retentionNote").textContent = data.items.length
      ? tr("{kept} held, {atRisk} fading, turn {turn}", { kept: data.kept, atRisk: data.at_risk, turn: data.turn })
      : tr("Nothing scored yet (turn {turn})", { turn: data.turn });
    const list = $("retentionList");
    list.textContent = "";
    for (const item of data.items.slice(0, 10)) {
      const note = item.uses
        ? `used ${item.uses}x, +${item.wins} / \u2212${item.losses}`
        : "never recalled";
      list.appendChild(metricRow(item.title, item.effectiveness, note));
    }
  } catch (err) {
    box.hidden = true;
  }
}

// One state bar: the fill width, the value beside the name, and an amber warning when the figure is low.
function setMetric(name, value, band, warn) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  const fill = $(name + "Fill");
  fill.style.width = pct + "%";
  $(name + "Val").textContent = tr("{value} of 100", { value: value }) + (band ? " (" + tr(band) + ")" : "");
  const box = fill.closest(".metric");
  if (box) box.classList.toggle("low", !!warn);
}

// An admin's number field for a state value. A field the admin is dragging is left alone, so a refresh does not
// pull the slider back mid-drag.
function setAdminField(id, value) {
  const input = $(id);
  if (document.activeElement !== input) input.value = value;
  $(id + "Val").textContent = input.value;
}

function showMood(m) {
  $("moodNote").textContent = m.emotion.charAt(0).toUpperCase() + m.emotion.slice(1);
  setMetric("moodEnergy", m.energy, m.energy_band, m.energy_band === "low");
  setMetric("moodAffinity", m.affinity, m.affinity_band, false);
  $("moodTone").textContent = m.tone;
  // Only an admin gets the editors, and the server refuses the save for everyone else as well.
  $("moodAdmin").hidden = !(me && me.is_admin);
  if (me && me.is_admin) {
    setAdminField("moodEnergyInput", m.energy);
    setAdminField("moodAffinityInput", m.affinity);
  }
}

function showAttitude(a) {
  $("attBox").hidden = !a.enabled;
  $("attitudeServerNote").hidden = a.enabled;
  const rel = a.relationship;
  $("attitudeType").textContent = rel
    ? tr("{type} · {trust} · {affection}", {
        type: tr(rel.type), trust: tr(rel.trust), affection: tr(rel.affection),
      })
    : "";
  $("attitudeLine").textContent = tr("Level: {level}.", { level: tr(a.level) });
  $("attitudeNote").textContent = tr("{level}, affection {affection}, trust {trust}", { level: tr(a.level), affection: a.affection, trust: a.trust });
  setMetric("attAffection", a.affection, null, false);
  setMetric("attTrust", a.trust, null, a.trust < 25);
  $("attitudeTone").textContent = a.tone;
  $("attAdmin").hidden = !(me && me.is_admin);
  if (me && me.is_admin) {
    setAdminField("attAffectionInput", a.affection);
    setAdminField("attTrustInput", a.trust);
  }
}

// The mood values sit in the Character panel, and the relationship keeps its own card. Both are for characters
// only; a group has neither.
async function refreshStateCards() {
  // The mood block and the relationship block start hidden in the page and show only when the server says the
  // feature is on, so a switched-off feature shows nothing. The Relationship card holds both and shows when
  // either is on. A group, or no open character, hides everything.
  refreshRetention();
  const solo = !!current && soloNames.includes(current);
  if (!solo) {
    $("moodBox").hidden = true;
    $("attBox").hidden = true;
    $("attitudeCard").hidden = true;
    return;
  }
  const who = current;
  let moodOn = false, attOn = false;
  try {
    const m = await api("GET", "/api/character-state?character=" + encodeURIComponent(who));
    if (current !== who) return;
    showMood(m);
    moodOn = !!m.enabled;
  } catch (err) {
    // On a failed load the block stays hidden.
  }
  $("moodBox").hidden = !moodOn;
  try {
    const a = await api("GET", "/api/character-attitude?character=" + encodeURIComponent(who));
    if (current !== who) return;
    showAttitude(a);
    attOn = !!a.enabled;
  } catch (err) {
    // On a failed load the block stays hidden.
  }
  $("attBox").hidden = !attOn;
  $("attitudeCard").hidden = !(moodOn || attOn);
  if (!attOn) $("attitudeNote").textContent = tr("Mood, affection and trust");
}

// The relationship with the user: affection, trust and the level. Read only, except for admins.
async function openAttitude() {
  if (busy || !current) return;
  $("attitudeMsg").textContent = "";
  try {
    const a = await api("GET", "/api/character-attitude?character=" + encodeURIComponent(current));
    showAttitude(a);
  } catch (err) {
    $("attitudeMsg").textContent = err.message;
  }
}

// An admin setting the scores by hand. The server checks the admin flag again, so a non-admin call is refused.
async function saveAttitudeAdmin() {
  if (!current || !(me && me.is_admin)) return;
  $("attMsg").textContent = "";
  try {
    const a = await api("POST", "/api/character-attitude", {
      character: current,
      affection: Number($("attAffectionInput").value),
      trust: Number($("attTrustInput").value),
    });
    showAttitude(a);
    $("attMsg").textContent = tr("Saved.");
  } catch (err) {
    $("attMsg").textContent = err.message;
  }
}

// An admin setting the mood by hand. An empty emotion lets the numbers decide it.
async function saveMoodAdmin() {
  if (!current || !(me && me.is_admin)) return;
  $("moodMsg").textContent = "";
  try {
    const m = await api("POST", "/api/character-state", {
      character: current,
      energy: Number($("moodEnergyInput").value),
      affinity: Number($("moodAffinityInput").value),
      emotion: $("moodEmotion").value || null,
    });
    showMood(m);
    $("moodEmotion").value = "";
    $("moodMsg").textContent = tr("Saved.");
  } catch (err) {
    $("moodMsg").textContent = err.message;
  }
}

$("attitudeCard").addEventListener("click", openAttitude);
$("moodAdminSave").addEventListener("click", saveMoodAdmin);
$("attAdminSave").addEventListener("click", saveAttitudeAdmin);
for (const id of ["moodEnergyInput", "moodAffinityInput", "attAffectionInput", "attTrustInput"]) {
  $(id).addEventListener("input", () => { $(id + "Val").textContent = $(id).value; });
}
$("dreamSave").addEventListener("click", async () => {
  if (!dreamFor || busy) return;
  $("dreamMsg").textContent = "";
  try {
    const data = await api("POST", "/api/dreams", {
      character: dreamFor,
      enabled: $("dreamEnabled").checked,
      every_days: Number($("dreamEverySelect").value),
      start: $("dreamStart").value,
      end: $("dreamEnd").value,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
    showDreamLast(data);
    $("dreamMsg").textContent = tr("Saved.");
  } catch (err) {
    $("dreamMsg").textContent = err.message;
  }
});
$("dreamBubblesBtn").addEventListener("click", openDreamBubbles);
$("dreamBubblesClose").addEventListener("click", () => $("dreamBubblesDlg").close());
$("dreamItemClose").addEventListener("click", () => $("dreamItemDlg").close());
$("ciSave").addEventListener("click", async () => {
  if (!ciFor || busy) return;
  $("ciMsg").textContent = "";
  const mode = document.querySelector('input[name="ciMode"]:checked').value;
  try {
    const data = await api("POST", "/api/checkins", {
      character: ciFor,
      enabled: $("ciEnabled").checked,
      every_days: Number($("ciEvery").value),
      mode,
      at: $("ciAt").value,
      start: $("ciStart").value,
      end: $("ciEnd").value,
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    });
    showCheckinNote(data);
    $("ciMsg").textContent = tr("Saved.");
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

// The look itself: Moonlit, Soft Shadows, Dusk or Hyacinth, the 1.4.1 look. Kept per browser, like the light/dark theme.
(function uiThemePicker() {
  const pick = document.getElementById("uiThemePick");
  if (!pick) return;
  pick.value = uiThemeName();
  pick.addEventListener("change", (e) => {
    applyUi(e.target.value);
    const accent = document.getElementById("accentPick");
    if (accent && !savedAccent()) accent.value = accentDefault();
  });
})();

// Settings sheet. Category cards open one category at a time. On phones the chat bar's font, name
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

  // Show one category, or the grid of cards when name is null. back is the category the Back button returns to.
  let backTo = null;
  function show(name, back) {
    backTo = name ? (back || null) : null;
    grid.hidden = !!name;
    for (const p of panels) p.hidden = p.dataset.panel !== name;
    backBtn.hidden = !name;
    const card = name && sheet.querySelector('.set-card[data-panel="' + name + '"] .set-name');
    const panel = name && panels.find((p) => p.dataset.panel === name);
    title.textContent = card ? tr(card.textContent) : tr((panel && panel.dataset.title) || "Settings");
  }
  function openPanel(name, back) {
    show(name, back);
    if (name === "worlds") return refreshWorldCache().catch(() => {});
    if (name === "memory") { openPinned(); refreshRetention(); }
    if (name === "dreams") openDreams();
    if (name === "offscreen") loadOffscreen();
    if (name === "voice") {
      if (window.ttsInitPanel) window.ttsInitPanel();
      if (window.themeMusicInitPanel) window.themeMusicInitPanel();
    }
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
    renderAccountPrefs();
    if (window.ttsInitPanel) window.ttsInitPanel();
    if (window.themeMusicInitPanel) window.themeMusicInitPanel();
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
  backBtn.addEventListener("click", () => { if (backTo) show(backTo); else show(null); });
  grid.addEventListener("click", (e) => {
    const card = e.target.closest(".set-card");
    if (card) { openPanel(card.dataset.panel); backBtn.focus(); }
  });
  sheet.addEventListener("click", (e) => {
    const story = e.target.closest("[data-story]");
    if (story) { openPanel(story.dataset.story, story.dataset.back || "story"); backBtn.focus(); }
  });
  sheet.addEventListener("click", (e) => { if (e.target === sheet) close(true); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || sheet.hidden || document.querySelector("dialog[open]")) return;
    if (grid.hidden) { if (backTo) show(backTo); else show(null); } else close(true);   // Escape steps back from a category before closing
  });
  document.getElementById("editNewBtn").addEventListener("click", () => close(false));
  if (phone.addEventListener) phone.addEventListener("change", place); else phone.addListener(place);
  place();
  // /chat?settings=account opens the Account panel directly (used by the profile page's Edit link).
  const wanted = new URLSearchParams(location.search).get("settings");
  if (wanted && /^[a-z]+$/.test(wanted) && sheet.querySelector('.set-panel[data-panel="' + wanted + '"]')) {
    open();
    const ready = openPanel(wanted);
    if (wanted === "worlds") {
      const worldId = new URLSearchParams(location.search).get("world");
      Promise.resolve(ready).then(() => {
        if (worldId && worldCache.worlds.some((w) => w.id === worldId)) openWorld(worldId);
      }).catch(() => {});
    }
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
  // Roll stays in the chat bar beside Tree, Undo and Select at every width, so the four keep one size.
  const parts = [["chatIcon", "chat"], ["chatTitle", "chat"], ["settingsBtn", "chat"], ["adminLink", "account"], ["pwBtn", "account"], ["logoutBtn", "account"]]
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
    // Undo keeps its own place in the chat bar at every width, so the run reads Tree, Roll, Undo, Select.
    if (undoHome.nextSibling !== undo) undoHome.parentNode.insertBefore(undo, undoHome.nextSibling);
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
  // placeAll only reads the "locked" class, so it runs when that class changes and not on every class change.
  let wasLocked = document.body.classList.contains("locked");
  new MutationObserver(() => {
    const locked = document.body.classList.contains("locked");
    if (locked !== wasLocked) { wasLocked = locked; placeAll(); }
  }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  if (phone.addEventListener) phone.addEventListener("change", placeAll); else phone.addListener(placeAll);
  sync();
  placeAll();
})();

// ---- Private group chats: an account's own groups of solo characters, with a note for each member.
let soloNames = [];
let groupNames = new Set();
let novelIds = new Set();
// ---- Branch tree. Everyone sees their own; admins can also open any account's tree, read-only.
async function openTree() {
  const admin = !!(me && me.is_admin);
  $("treeAccountRow").hidden = !admin;
  if (admin) {
    const sel = $("treeAccount");
    const users = await api("GET", "/api/admin/users");
    sel.replaceChildren(new Option(tr("My own chats"), ""));
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
    $("treeNote").textContent = tr("Open a chat first.");
    return;
  }
  try {
    const data = other
      ? await api("GET", "/api/admin/branches/tree?uid=" + encodeURIComponent(sel.value) + "&character=" + encodeURIComponent(current))
      : await api("GET", "/api/branches/tree?character=" + encodeURIComponent(current));
    $("treeNote").textContent = other
      ? tr("Read-only: {name}'s branches for this chat. Nothing here can be changed.", { name: data.account })
      : tr("Choose a branch to open it. The one you are in is marked open.");
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

$("branchTree").addEventListener("click", openTree);
$("treeAccount").addEventListener("change", loadTree);
$("treeClose").addEventListener("click", () => $("treeDlg").close());

// ---- Novels and worlds. A novel is a narrator's story for a cast of characters, in a world. A world is the setting:
// its places, magic, rules, civilisations and other details. Both belong to the account alone.
function isNovelName(name) {
  return typeof name === "string" && name.startsWith("nvl-");
}

function displayName(name) {
  return novelNames[name] || name;
}

function novelButton(n) {
  const b = el("button", "", "char-item");
  b.type = "button";
  b.dataset.name = n.id;
  b.dataset.chatName = n.id;   // where the missed check-in dot goes
  b.disabled = busy;
  b.appendChild(el("span", n.name, "char-name"));
  b.addEventListener("click", () => pickCharacter(n.id));
  return b;
}

// A private group's row in the sidebar: the chat, and an edit link to its page.
function privateGroupRow(g) {
  const row = el("div", "", "group-row");
  row.appendChild(charButton(g.name));
  const edit = el("button", tr("Edit"), "ghost small group-edit");
  edit.type = "button";
  edit.title = tr("Change this group's characters and notes");
  edit.setAttribute("aria-label", tr("Edit group {name}", { name: g.name }));
  edit.addEventListener("click", () => { location.href = "/create/group?id=" + encodeURIComponent(g.id); });
  row.appendChild(edit);
  return row;
}

function novelRow(n) {
  const row = el("div", "", "group-row");
  row.appendChild(novelButton(n));
  const edit = el("button", tr("Edit"), "ghost small group-edit");
  edit.type = "button";
  edit.title = tr("Change this novel's name, cast or world");
  edit.setAttribute("aria-label", tr("Edit novel {name}", { name: n.name }));
  edit.addEventListener("click", () => { location.href = "/create/novel?id=" + encodeURIComponent(n.id); });
  row.appendChild(edit);
  return row;
}

// Draws the novel list in the sidebar and remembers each novel's name for the header.
function renderNovels(list) {
  novelNames = {};
  for (const n of list) novelNames[n.id] = n.name;
  const box = $("novelList");
  box.textContent = "";
  for (const n of list) box.appendChild(novelRow(n));
  if (!list.length) box.appendChild(el("p", tr("No novels yet. A narrator tells a novel with the characters you pick."), "muted"));
}

// The world choices for a novel or chat: the account default (""), no world ("none"), or one of the account's worlds.
function fillWorldSelect(sel, selected) {
  sel.textContent = "";
  sel.appendChild(new Option("Account default", ""));
  sel.appendChild(new Option("No world", "none"));
  for (const w of worldCache.worlds) sel.appendChild(new Option(w.name, w.id));
  sel.value = selected || "";
  if (sel.value !== (selected || "")) sel.value = "";   // a world that is gone falls back to the default
}

async function refreshWorldCache() {
  worldCache = await api("GET", "/api/worlds");
  renderWorldEditor();
}

// ---- Worlds (settings panel)
const WORLD_SECTIONS = ["locations", "magic", "rules", "civilisations", "factions", "technology", "other"];
let worldEditing = null;   // the world ID being edited; null while a new one is made

function renderWorldEditor() {
  const pick = $("worldPick");
  pick.textContent = "";
  pick.appendChild(new Option(tr("New world"), ""));
  for (const w of worldCache.worlds) pick.appendChild(new Option(w.name, w.id));
  pick.value = worldEditing || "";
  const def = $("worldDefault");
  def.textContent = "";
  def.appendChild(new Option("None", ""));
  for (const w of worldCache.worlds) def.appendChild(new Option(w.name, w.id));
  def.value = worldCache.default || "";
}

function openWorld(id) {
  worldEditing = id || null;
  const item = id ? worldCache.worlds.find((w) => w.id === id) : null;
  $("worldName").value = item ? item.name : "";
  $("worldSummary").value = item ? item.summary : "";
  for (const key of WORLD_SECTIONS) $("world_" + key).value = item ? (item.sections[key] || []).join("\n") : "";
  $("worldDelete").hidden = !item;
  $("worldEditor").hidden = false;
  $("worldMsg").textContent = "";
  renderWorldEditor();
  updateWorldCounters();
}

// The token limits, the same numbers as web/worlds.py (ENTRY_TOKENS, SECTION_TOKENS). The server refuses an over-limit
// save; this only shows the count while the user types.
const WORLD_ENTRY_TOKENS = 300;
const WORLD_SECTION_TOKENS = { locations: 1500, magic: 1500, rules: 1500, civilisations: 1500, factions: 1500, technology: 1500, other: 1500 };
const WORLD_TITLES = { locations: "Locations", magic: "Magic systems", rules: "Rules", civilisations: "Civilisations", factions: "Factions", technology: "Technology", other: "Other details" };
const WORLD_SUMMARY_TOKENS = 250;

// The same estimate as worlds.estimate_tokens: about four characters a token, counted in characters, not UTF-16 units.
function worldTokens(text) {
  return Math.ceil(Array.from(text).length / 4);
}

// Each line is one entry, stored the way web/worlds.py normalise_entry stores it: whitespace squeezed, the [NEW] tag and
// a leading bullet dropped. A number is kept.
function worldEntry(s) {
  return s.split(/\s+/).filter(Boolean).join(" ").replace(/\s*\[new\]/gi, "").replace(/^[-*•]\s+/, "").trim();
}

function worldEntries(text) {
  return text.split("\n").map(worldEntry).filter(Boolean);
}

// The summary is one entry, so it shows its count under its own box.
function updateSummaryCounter() {
  const list = $("worldSummary_entries");
  const cost = worldTokens(worldEntries($("worldSummary").value).join(" "));
  list.textContent = "";
  const li = document.createElement("li");
  li.classList.toggle("over", cost > WORLD_SUMMARY_TOKENS);
  const label = document.createElement("span");
  label.textContent = tr("Summary");
  const count = document.createElement("span");
  count.textContent = `${cost} / ${WORLD_SUMMARY_TOKENS}`;
  li.append(label, count);
  list.appendChild(li);
}

// Each entry shows its own count under its box, and the section totals sit at the foot beside the save button.
function updateWorldCounters() {
  updateSummaryCounter();
  const totals = $("worldTotals");
  totals.textContent = "";
  for (const key of WORLD_SECTIONS) {
    const entries = worldEntries($("world_" + key).value);
    const list = $("world_" + key + "_entries");
    list.textContent = "";
    list.hidden = entries.length === 0;
    let total = 0;
    entries.forEach((text, i) => {
      const cost = worldTokens(text);
      total += cost;
      const li = document.createElement("li");
      li.classList.toggle("over", cost > WORLD_ENTRY_TOKENS);
      const label = document.createElement("span");
      const head = Array.from(text).slice(0, 36).join("");
      label.textContent = `${i + 1}. ${head}${Array.from(text).length > 36 ? "..." : ""}`;
      const count = document.createElement("span");
      count.textContent = `${cost} / ${WORLD_ENTRY_TOKENS}`;
      li.append(label, count);
      list.appendChild(li);
    });
    const limit = WORLD_SECTION_TOKENS[key];
    const part = document.createElement("span");
    part.classList.toggle("over", total > limit);
    part.textContent = tr("{section} {total} / {limit}", { section: tr(WORLD_TITLES[key]), total: total, limit: limit });
    totals.appendChild(part);
  }
}

for (const key of WORLD_SECTIONS) $("world_" + key).addEventListener("input", updateWorldCounters);
$("worldSummary").addEventListener("input", updateSummaryCounter);

async function saveWorld() {
  $("worldMsg").textContent = "";
  const sections = {};
  for (const key of WORLD_SECTIONS) {
    sections[key] = $("world_" + key).value.split("\n").map((s) => s.trim()).filter(Boolean);
  }
  const body = { name: $("worldName").value, summary: $("worldSummary").value, sections };
  try {
    const saved = worldEditing
      ? await api("PUT", "/api/worlds/" + encodeURIComponent(worldEditing), body)
      : await api("POST", "/api/worlds", body);
    await refreshWorldCache();
    openWorld(saved.id);
    $("worldMsg").textContent = tr("Saved.");
    if (current) await refreshStory();
  } catch (err) {
    $("worldMsg").textContent = err.message;
  }
}

async function deleteWorld() {
  if (!worldEditing) return;
  if (!confirm(tr("Delete this world? Novels that used it fall back to the account default."))) return;
  try {
    await api("DELETE", "/api/worlds/" + encodeURIComponent(worldEditing));
    worldEditing = null;
    $("worldEditor").hidden = true;
    await refreshWorldCache();
    if (current) await refreshStory();
  } catch (err) {
    $("worldMsg").textContent = err.message;
  }
}

async function saveDefaultWorld() {
  try {
    const data = await api("POST", "/api/worlds/default", { world: $("worldDefault").value || null });
    worldCache.default = data.default;
    $("worldDefaultMsg").textContent = tr("Saved.");
    if (current) await refreshStory();
  } catch (err) {
    $("worldDefaultMsg").textContent = err.message;
  }
}

// ---- Scenarios (settings panel): the account's opening messages, and how a new chat gets one.
let scenarioCache = { scenarios: [], mode: "off", fixed: null };
let scenarioEditing = null;   // the scenario ID being edited; null while a new one is made

async function refreshScenarioCache() {
  scenarioCache = await api("GET", "/api/scenarios");
  renderScenarioEditor();
}

function renderScenarioEditor() {
  const pick = $("scenarioPick");
  pick.replaceChildren(new Option(tr("New scenario"), ""));
  for (const s of scenarioCache.scenarios) pick.appendChild(new Option(s.name, s.id));
  pick.value = scenarioEditing || "";
  $("scenarioDelete").hidden = !scenarioEditing;

  $("scenarioMode").value = scenarioCache.mode;
  const fixed = $("scenarioFixed");
  fixed.replaceChildren();
  for (const s of scenarioCache.scenarios) fixed.appendChild(new Option(s.name, s.id));
  if (scenarioCache.fixed) fixed.value = scenarioCache.fixed;
  $("scenarioFixedRow").hidden = scenarioCache.mode !== "fixed";
}

function openScenario(id) {
  scenarioEditing = id || null;
  const item = id ? scenarioCache.scenarios.find((s) => s.id === id) : null;
  $("scenarioName").value = item ? item.name : "";
  $("scenarioText").value = item ? item.text : "";
  $("scenarioEditor").hidden = false;
  $("scenarioMsg").textContent = "";
  renderScenarioEditor();
}

async function saveScenario() {
  $("scenarioMsg").textContent = "";
  const body = { name: $("scenarioName").value, text: $("scenarioText").value };
  try {
    const saved = scenarioEditing
      ? await api("PUT", "/api/scenarios/" + encodeURIComponent(scenarioEditing), body)
      : await api("POST", "/api/scenarios", body);
    await refreshScenarioCache();
    openScenario(saved.id);
    $("scenarioMsg").textContent = tr("Saved.");
  } catch (err) {
    $("scenarioMsg").textContent = err.message;
  }
}

async function deleteScenario() {
  if (!scenarioEditing) return;
  if (!confirm(tr("Delete this scenario? A new chat that used it will stop offering an opening."))) return;
  try {
    await api("DELETE", "/api/scenarios/" + encodeURIComponent(scenarioEditing));
    scenarioEditing = null;
    $("scenarioEditor").hidden = true;
    await refreshScenarioCache();
  } catch (err) {
    $("scenarioMsg").textContent = err.message;
  }
}

async function saveScenarioMode() {
  $("scenarioModeMsg").textContent = "";
  const mode = $("scenarioMode").value;
  const fixed = mode === "fixed" ? ($("scenarioFixed").value || null) : null;
  if (mode === "fixed" && !fixed) {
    $("scenarioModeMsg").textContent = tr("Pick a scenario for new chats.");
    return;
  }
  try {
    const data = await api("POST", "/api/scenarios/mode", { mode, fixed });
    scenarioCache.mode = data.mode;
    scenarioCache.fixed = data.fixed;
    renderScenarioEditor();
    $("scenarioModeMsg").textContent = tr("Saved.");
  } catch (err) {
    $("scenarioModeMsg").textContent = err.message;
  }
}

// ---- Story panel: the world a chat uses, and its narrative tensions (Character settings).
async function refreshStory() {
  const name = current;
  const isOther = !!name && (groupNames.has(name) || novelIds.has(name));
  $("storyBox").hidden = !name;
  $("offQuickRow").hidden = isOther;
  $("offStoryLink").hidden = isOther;
  if (!name) return;
  await refreshWorldCache();
  const data = await api("GET", "/api/tensions?character=" + encodeURIComponent(name));
  if (name !== current) return;   // the user switched chats while this was loading
  fillWorldSelect($("storyWorld"), data.world || "");
  $("tensionMode").value = data.mode;
  renderTensions(data.items);
}

function renderTensions(items) {
  const box = $("tensionList");
  box.textContent = "";
  const open = items.filter((t) => t.status === "open");
  const done = items.filter((t) => t.status === "resolved").slice(-5).reverse();
  if (!open.length) box.appendChild(el("p", tr("No open threads right now."), "muted"));
  for (const t of open) box.appendChild(tensionRow(t));
  if (done.length) {
    box.appendChild(el("p", tr("Recently resolved"), "muted"));
    for (const t of done) {
      const text = t.how ? t.text + " Resolved: " + t.how : t.text;
      box.appendChild(el("p", text, "tension-done muted"));
    }
  }
}

function tensionRow(t) {
  const row = el("div", "", "tension-row");
  row.appendChild(el("div", t.kind + (t.who ? " (" + t.who + ")" : ""), "tension-kind"));
  row.appendChild(el("p", t.text, "tension-text"));
  const how = document.createElement("input");
  how.type = "text";
  how.maxLength = 240;
  how.placeholder = tr("How it resolved (optional)");
  how.setAttribute("aria-label", tr("How it resolved"));
  const close = el("button", tr("Resolve"), "ghost small");
  close.type = "button";
  close.addEventListener("click", () => tensionAction("/api/tensions/resolve", { id: t.id, how: how.value }));
  const drop = el("button", tr("Remove"), "ghost small");
  drop.type = "button";
  drop.addEventListener("click", () => tensionAction("/api/tensions/drop", { id: t.id }));
  row.append(how, close, drop);
  return row;
}

async function tensionAction(url, extra) {
  try {
    const data = await api("POST", url, { character: current, ...extra });
    renderTensions(data.items);
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
}

// ---- Off-screen life: what a character does between chats, written by a daily director.
let offServerOn = false;

// The quick switch in the Character panel reads the same per-chat row as the Off-screen life panel.
async function loadOffQuick() {
  if (!current || groupNames.has(current) || novelIds.has(current)) { $("offQuick").checked = false; return; }
  const name = current;
  try {
    const data = await api("GET", "/api/offscreen?character=" + encodeURIComponent(name));
    if (name !== current) return;   // the user switched chats while this was loading
    offServerOn = !!data.server_on;
    $("offQuick").checked = !!data.enabled;
    $("offEnabled").checked = !!data.enabled;
    offSyncButtons();
  } catch { /* the Off-screen life panel shows the error when it is opened */ }
}

function offSyncButtons() {
  $("offWrite").disabled = !offServerOn || !$("offEnabled").checked;
}

async function loadOffscreen() {
  const name = current;
  $("offMsg").textContent = "";
  if (!name) return;
  if (groupNames.has(name) || novelIds.has(name)) { $("offEnabled").checked = false; $("offWrite").disabled = true; $("offMsg").textContent = tr("Only characters have an off-screen life."); return; }
  try {
    const data = await api("GET", "/api/offscreen?character=" + encodeURIComponent(name));
    if (name !== current) return;   // the user switched chats while this was loading
    offServerOn = !!data.server_on;
    $("offEnabled").checked = !!data.enabled;
    $("offQuick").checked = !!data.enabled;
    $("offServerNote").hidden = offServerOn;
    offSyncButtons();
    renderOffscreen(data);
  } catch (err) {
    $("offMsg").textContent = err.message;
  }
}

function renderOffscreen(data) {
  const frag = $("offFragments");
  const twist = $("offTwists");
  frag.textContent = "";
  twist.textContent = "";
  const moments = data.fragments || [];
  const twists = data.twists || [];
  frag.appendChild(el("p", tr("Recent moments"), "muted"));
  if (!moments.length) frag.appendChild(el("p", tr("Nothing yet."), "muted"));
  for (const item of moments.slice().reverse()) {
    frag.appendChild(el("p", (item.day ? item.day + "  " : "") + item.text, "muted"));
  }
  twist.appendChild(el("p", tr("Twist cards"), "muted"));
  if (!twists.length) twist.appendChild(el("p", tr("Nothing yet."), "muted"));
  for (const item of twists.slice().reverse()) {
    twist.appendChild(el("p", (item.day ? item.day + "  " : "") + item.text, "muted"));
  }
}

document.querySelector('.set-card[data-panel="scenarios"]').addEventListener("click", () => {
  refreshScenarioCache().catch(() => {});   // the Scenarios panel always shows the account's current library
});
$("newScenarioBtn").addEventListener("click", () => openScenario(null));
$("scenarioPick").addEventListener("change", (e) => openScenario(e.target.value || null));
$("scenarioSave").addEventListener("click", saveScenario);
$("scenarioDelete").addEventListener("click", deleteScenario);
$("scenarioMode").addEventListener("change", (e) => { $("scenarioFixedRow").hidden = e.target.value !== "fixed"; });
$("scenarioModeSave").addEventListener("click", saveScenarioMode);
$("newWorldBtn").addEventListener("click", () => openWorld(null));
$("worldPick").addEventListener("change", (e) => openWorld(e.target.value || null));
$("worldSave").addEventListener("click", saveWorld);
$("worldDelete").addEventListener("click", deleteWorld);
$("worldDefaultSave").addEventListener("click", saveDefaultWorld);
$("storyWorld").addEventListener("change", async (e) => {
  try {
    await api("POST", "/api/chat-world", { character: current, world: e.target.value });
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
});
$("tensionMode").addEventListener("change", async (e) => {
  try {
    await api("POST", "/api/tensions/mode", { character: current, mode: e.target.value });
  } catch (err) {
    $("chatErr").textContent = err.message;
  }
});

$("offEnabled").addEventListener("change", async (e) => {
  $("offMsg").textContent = "";
  try {
    const data = await api("POST", "/api/offscreen", { character: current, enabled: e.target.checked });
    offServerOn = !!data.server_on;
    $("offQuick").checked = !!data.enabled;
    $("offServerNote").hidden = offServerOn;
    offSyncButtons();
    $("offMsg").textContent = tr("Saved.");
  } catch (err) {
    $("offMsg").textContent = err.message;
    e.target.checked = !e.target.checked;
  }
});

$("offQuick").addEventListener("change", async (e) => {
  $("charMsg").textContent = "";
  if (!current || groupNames.has(current) || novelIds.has(current)) return;
  try {
    const data = await api("POST", "/api/offscreen", { character: current, enabled: e.target.checked });
    offServerOn = !!data.server_on;
    $("offEnabled").checked = !!data.enabled;
    $("offServerNote").hidden = offServerOn;
    offSyncButtons();
    $("charMsg").textContent = tr("Saved.");
  } catch (err) {
    $("charMsg").textContent = err.message;
    e.target.checked = !e.target.checked;
  }
});

$("offWrite").addEventListener("click", async () => {
  $("offMsg").textContent = tr("Writing...");
  $("offWrite").disabled = true;
  try {
    const data = await api("POST", "/api/offscreen", { character: current, enabled: $("offEnabled").checked, write: true });
    renderOffscreen(data);
    $("offMsg").textContent = tr("Written.");
  } catch (err) {
    $("offMsg").textContent = err.message;
  } finally {
    offSyncButtons();
  }
});
