// Shared helpers for every page. Model output is only ever written with textContent.

// Theme and highlight colour. Stored per browser in localStorage ("rp_theme", "rp_accent").
// The light/dark tokens live in style.css and tokens.css; theme-init.js sets the attribute before first paint.
// "rp_ui" picks which look the app wears: "moon" (Moonlit, the default, a sheet of its own over redesign.css),
// "folio" (Soft Shadows), "dusk", or "classic" (Hyacinth, the 1.4.1 look). The stored value stays "classic" even
// though the theme is named Hyacinth, so choices already saved in a browser keep working. Classic switches the
// redesign stylesheet off, so the plain style.css underneath shows through. The other looks keep redesign.css and
// add their own sheet over it, with data-look set to the look's name. A saved look that no longer exists (Spore)
// falls back to Moonlit.
const UI_KEY = "rp_ui";
const UI_CLASSIC = "classic";
const UI_MOON = "moon";
const UI_FOLIO = "folio";
const UI_DUSK = "dusk";
// The sheet each look adds over redesign.css. Hyacinth switches redesign.css off instead.
const LOOK_SHEETS = { moon: "/static/moon.css", folio: "/static/folio.css", dusk: "/static/dusk.css" };
const ACCENT_DEFAULT_DUSK = "#a0b0f4";
const ACCENT_DEFAULT_FOLIO = "#1b6e66";
const ACCENT_DEFAULT_CLASSIC = "#4cc4ff";
const ACCENT_DEFAULT_MOON = "#8fd3cc";
const THEME_KEY = "rp_theme";
const SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; } catch (e) { return "dark"; }
}

// Moonlit is the default: a browser with no saved choice, or an unknown one, gets it.
// Default character backgrounds come in one set per look (files named <look>-N.jpg). Keep the set for the
// look in use now; if a look has no set, fall back to the full list.
function lookDefaults(list) {
  const look = uiThemeName();
  const mine = (list || []).filter((u) => u.includes("/defaults/" + look + "-"));
  return mine.length ? mine : (list || []);
}

function uiThemeName() {
  try {
    const saved = localStorage.getItem(UI_KEY);
    return saved === UI_CLASSIC || saved in LOOK_SHEETS ? saved : UI_MOON;
  } catch (e) { return UI_MOON; }
}

function accentDefault() {
  const look = uiThemeName();
  if (look === UI_CLASSIC) return ACCENT_DEFAULT_CLASSIC;
  if (look === UI_FOLIO) return ACCENT_DEFAULT_FOLIO;
  if (look === UI_DUSK) return ACCENT_DEFAULT_DUSK;
  return ACCENT_DEFAULT_MOON;
}

// The paper colour of the active look, for the browser's own chrome around the page.
function metaThemeColor() {
  if (uiThemeName() !== UI_CLASSIC) {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--meta-theme").trim();
    if (v) return v;
  }
  return document.documentElement.dataset.theme === "light" ? "#f6f8fc" : "#020307";
}

function applyMetaTheme() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = metaThemeColor();
}

// Switch looks. The stylesheet stays in the page and is disabled, so nothing reloads and no state is lost.
// The attribute is data-look, not data-ui: data-ui is the marker design tools use for a cropped interface fragment,
// and setting it on <html> makes an inspector read the whole page as a fragment.
function applyUi(name) {
  const look = name === UI_CLASSIC || name in LOOK_SHEETS ? name : UI_MOON;
  document.documentElement.dataset.look = look;
  const link = document.getElementById("uiTheme");
  if (link) link.disabled = look === UI_CLASSIC;
  // A look's sheet is added the first time it is chosen, after redesign.css, so it wins the cascade; then it is switched.
  let sheet = document.getElementById("lookSheet");
  const href = LOOK_SHEETS[look];
  if (href && !sheet) {
    sheet = document.createElement("link");
    sheet.id = "lookSheet";
    sheet.rel = "stylesheet";
    document.head.appendChild(sheet);
  }
  if (sheet) {
    if (href) sheet.href = href;
    sheet.disabled = !href;
  }
  try { localStorage.setItem(UI_KEY, look); } catch (e) { /* private mode */ }
  applyMetaTheme();
  const pick = document.getElementById("accentPick");
  if (pick && !savedAccent()) pick.value = accentDefault();
}

// Soft circles drifting in a field. Each circle is drawn with its own tone, size, path, pace and start point, so no
// two fields move alike. The page field is fixed behind everything; a card's field sits inside the card. Only the page
// field takes a character's tint (setAmbientMood), so its colours and pace move with the character's mood and affection.
function randBetween(a, b) { return a + Math.random() * (b - a); }

function setCirclePace(c, speed) {
  const pace = Number(c.dataset.pace);
  c.style.setProperty("--cd", (pace * speed).toFixed(1) + "s");
  c.style.setProperty("--cs", (-Number(c.dataset.start) * pace * speed).toFixed(1) + "s");
}

function circleField(count, extra) {
  const field = el("span", "", "ambient-field" + (extra ? " " + extra : ""));
  field.setAttribute("aria-hidden", "true");
  const sizes = extra ? [20, 39] : [34, 68];   // in cqmax: a share of the larger side of the field
  for (let i = 0; i < count; i++) {
    const c = el("span", "", "ambient-circle");
    c.style.setProperty("--tone", "var(--tone-" + ((i % 3) + 1) + ")");
    c.style.setProperty("--a", Math.round(randBetween(14, 30)) + "%");
    c.style.setProperty("--s", randBetween(sizes[0], sizes[1]).toFixed(1) + "cqmax");
    for (let k = 0; k < 4; k++) {
      c.style.setProperty("--x" + k, randBetween(-10, 110).toFixed(1) + "cqw");
      c.style.setProperty("--y" + k, randBetween(-10, 110).toFixed(1) + "cqh");
    }
    c.dataset.pace = 70 + 1 + Math.floor(Math.random() * 10);   // the 70 second baseline plus a random 1 to 10
    c.dataset.start = Math.random().toFixed(4);                  // where in the cycle it starts, as a fraction
    setCirclePace(c, 1);
    field.appendChild(c);
  }
  return field;
}

// The tint and speed of the page field: affection warms its colours toward amber (up to 8 percent), energy leans them
// toward green (up to 6 percent), and both speed the drift by up to 15 percent. At zero it is the look's own.
let pageField = null;
function setAmbientMood(warm, green) {
  const s = document.documentElement.style;
  s.setProperty("--ambient-warm", (warm * 8).toFixed(1) + "%");
  s.setProperty("--ambient-green", (green * 6).toFixed(1) + "%");
  const speed = 1 - 0.15 * (warm + green) / 2;
  if (pageField) for (const c of pageField.querySelectorAll(".ambient-circle")) setCirclePace(c, speed);
}

function mountPageField() {
  if (pageField || !document.body) return;
  pageField = circleField(3, "ambient-page");
  document.body.prepend(pageField);
}
if (document.body) mountPageField(); else document.addEventListener("DOMContentLoaded", mountPageField);
setAmbientMood(0, 0);

function applyAccent(hex) {
  const s = document.documentElement.style;
  const v = /^#[0-9a-f]{6}$/i.test(hex || "") ? hex.toLowerCase() : null;
  if (!v) {
    // No custom colour: drop the overrides so the look's own accent (coral or cyan) applies.
    for (const n of ["--accent", "--accent-2", "--on-accent", "--accent-soft", "--accent-glow"]) s.removeProperty(n);
    return accentDefault();
  }
  const r = parseInt(v.slice(1, 3), 16), g = parseInt(v.slice(3, 5), 16), b = parseInt(v.slice(5, 7), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;   // light accent gets dark text, and the reverse
  s.setProperty("--accent", v);
  s.setProperty("--on-accent", lum > 0.6 ? "#04121f" : "#ffffff");
  s.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, 0.14)`);
  s.setProperty("--accent-glow", `rgba(${r}, ${g}, ${b}, 0.38)`);
  // The gradient's second stop: half the chosen colour, half the Hyacinth violet, so buttons keep the two-tone look.
  const mix = [[r, 0x9b], [g, 0x8c], [b, 0xff]].map(([c, t]) => Math.round((c + t) / 2));
  s.setProperty("--accent-2", "#" + mix.map((n) => n.toString(16).padStart(2, "0")).join(""));
  return v;
}

function savedAccent() {
  try { return localStorage.getItem("rp_accent"); } catch (e) { return null; }
}

function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  applyMetaTheme();
  for (const b of document.querySelectorAll("#themeBtn")) {
    const label = t === "light" ? "Switch to dark theme" : "Switch to light theme";
    b.setAttribute("aria-label", label);
    b.title = label;
    b.innerHTML = t === "light" ? MOON_ICON : SUN_ICON;   // static markup, never model output
  }
}

// Icon shape is per account, but it is also cached so theme-init.js can set it before the first paint.
const AVATAR_KEY = "rp_avatar";
function applyAvatarShape(shape) {
  const circle = shape === "circle";
  document.documentElement.dataset.avatar = circle ? "circle" : "squircle";
  try { localStorage.setItem(AVATAR_KEY, circle ? "circle" : "squircle"); } catch (e) { /* storage blocked */ }
}

// The account's own picture at the top of the sidebar, beside the Characters heading. It opens the
// profile page for the signed-in account. The server sends a profile only for accounts that have a
// page, so the built-in admin account leaves this link hidden.
function renderSideProfile(me) {
  const link = document.getElementById("sideProfile");
  if (!link) return;
  const profile = me && me.profile ? me.profile : null;
  link.hidden = !profile;
  if (!profile) return;
  link.href = "/u/" + encodeURIComponent(me.username);
  link.textContent = "";
  if (profile.has_avatar) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/account-avatar/" + encodeURIComponent(me.username) + "?v=" + (profile.avatar_version || 0);
    link.appendChild(img);
  } else {
    const initial = document.createElement("span");
    initial.textContent = (me.username.charAt(0) || "?").toUpperCase();
    link.appendChild(initial);
  }
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* private mode: applies for this page only */ }
  applyTheme(next);
}

function wireThemeButtons() {
  for (const b of document.querySelectorAll("#themeBtn")) b.addEventListener("click", toggleTheme);
  applyUi(uiThemeName());
  applyTheme(savedTheme());
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wireThemeButtons);
else wireThemeButtons();
applyAccent(savedAccent());

function apiError(status, data) {
  const d = data && data.detail;
  const err = new Error(typeof d === "string" ? d : (d && d.message) || "Request failed");
  err.code = d && d.code;
  err.status = status;
  return err;
}

function sendToLogin() {
  if (location.pathname !== "/login") location.href = "/login";
}

async function api(method, url, body) {
  const opts = { method, headers: { "X-Requested-With": "roleplay" }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty body */ }
  if (r.status === 401) sendToLogin();
  if (!r.ok) throw apiError(r.status, data);
  return data;
}

// POST /api/chat (or /api/regenerate) answers with server-sent events. handlers: delta, done, usage, error.
async function streamChat(body, handlers, path = "/api/chat") {
  const r = await fetch(path, {
    method: "POST",
    headers: { "X-Requested-With": "roleplay", "Content-Type": "application/json", "Accept": "text/event-stream" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let data = {};
    try { data = await r.json(); } catch (e) { /* empty body */ }
    if (r.status === 401) sendToLogin();
    throw apiError(r.status, data);
  }
  if ((r.headers.get("Content-Type") || "").includes("application/json")) {
    // slash commands answer with plain JSON, not a stream
    const data = await r.json();
    if (handlers.command) handlers.command(data.command);
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "error") {
        finished = true;
        const err = new Error(payload.message);
        err.code = payload.code;
        if (handlers.error) handlers.error(err);
      } else if (event === "done") {
        finished = true;
        if (handlers.done) handlers.done(payload);
      } else if (handlers[event]) {
        handlers[event](payload);
      }
    }
  }
  if (!finished && handlers.error) {
    handlers.error(new Error("The connection dropped. Your message may still have been saved; reloading the chat will show it."));
  }
}

// Look settings: page-art brightness and bubble opacity. Cached so the first paint already uses them.
const LOOK_KEY = "rp_look";
function applyLook(shade, bubble) {
  const s = document.documentElement.style;
  const v = Math.max(-100, Math.min(100, Math.round(Number(shade)) || 0));
  const b = Math.max(30, Math.min(100, Math.round(Number(bubble)) || 72));
  // Negative darkens with black, positive lightens with white.
  s.setProperty("--shade-overlay", v < 0 ? `rgba(0, 0, 0, ${(-v / 100 * 0.85).toFixed(3)})` : `rgba(255, 255, 255, ${(v / 100 * 0.6).toFixed(3)})`);
  s.setProperty("--bubble-alpha", b + "%");
  try { localStorage.setItem(LOOK_KEY, JSON.stringify({ shade: v, bubble: b })); } catch (e) { /* storage blocked */ }
}
(function restoreLook() {
  try {
    const saved = JSON.parse(localStorage.getItem(LOOK_KEY) || "null");
    if (saved) applyLook(saved.shade, saved.bubble);
  } catch (e) { /* storage blocked or corrupt */ }
})();

// Placeholder for a character with no picture: its colour, with a simple face in a readable ink.
function faceIcon(hex) {
  const span = document.createElement("span");
  span.className = "face";
  span.setAttribute("aria-hidden", "true");
  const ok = /^#[0-9a-f]{6}$/i.test(hex || "");
  let ink = "#0b1020";
  if (ok) {
    span.style.background = hex.toLowerCase();
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    ink = (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? "#1a1420" : "#ffffff";
  }
  span.innerHTML = `<svg viewBox="0 0 64 64" focusable="false"><circle cx="23" cy="27" r="4.5" fill="${ink}"/><circle cx="41" cy="27" r="4.5" fill="${ink}"/><path d="M21 40 Q32 50 43 40" fill="none" stroke="${ink}" stroke-width="4" stroke-linecap="round"/></svg>`;
  return span;
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

// Favourite star: a span (not a button) so it can sit inside the sidebar's character buttons.
// onToggle(newValue) does the save and returns when it is done.
function favStar(name, on, onToggle) {
  const s = el("span", on ? "\u2605" : "\u2606", "fav-star" + (on ? " on" : ""));
  s.setAttribute("role", "button");
  s.setAttribute("tabindex", "0");
  s.setAttribute("aria-pressed", String(!!on));
  s.setAttribute("aria-label", (on ? "Unfavourite " : "Favourite ") + name);
  s.title = on ? "Remove from favourites" : "Add to favourites";
  const go = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (s.dataset.busy) return;
    s.dataset.busy = "1";
    try { await onToggle(!on); } finally { delete s.dataset.busy; }
  };
  s.addEventListener("click", go);
  s.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") go(e); });
  return s;
}

async function setFavourite(name, on) {
  return api("POST", "/api/favourites", { name, on });
}

// Missed check-ins: a red dot with the count on each character that has some waiting. Any element with
// data-chat-name (a sidebar button or a home card) gets the dot; opening the chat clears it.
let unreadMap = {};
async function refreshUnread() {
  try {
    const data = await api("GET", "/api/checkins/unread");
    unreadMap = data.unread || {};
  } catch (err) {
    // keep the dots already shown
  }
  paintUnread();
}
function paintUnread() {
  for (const node of document.querySelectorAll("[data-chat-name]")) paintDot(node);
}
// The dot on one element, set from the last refresh; called when an element is drawn as well as on refresh.
function paintDot(node) {
  const n = unreadMap[node.dataset.chatName] || 0;
  let dot = node.querySelector(":scope > .unread-dot");
  if (!n) {
    if (dot) dot.remove();
    return;
  }
  if (!dot) {
    dot = el("span", "", "unread-dot");
    node.appendChild(dot);
  }
  dot.textContent = n > 9 ? "9+" : String(n);
  dot.title = `${n} missed check-in${n === 1 ? "" : "s"}`;
  dot.setAttribute("aria-label", dot.title);
}
async function markRead(name) {
  if (!unreadMap[name]) return;
  delete unreadMap[name];
  paintUnread();
  try {
    await api("POST", "/api/checkins/read", { character: name });
  } catch (err) {
    refreshUnread();
  }
}

// ---- Background art, shared by the gallery, the chat page and the editor --------------------------
// A background id ending in a video extension is played as a video; every other id is a picture, and a
// shared default arrives already as a path (so it starts with a slash). Video backgrounds are stored as
// WebM, with the first frame beside them as <id>.poster.jpg.
function isVideoArt(id) {
  return /\.(webm|mp4)$/i.test(String(id || ""));
}

function artUrl(name, id) {
  if (String(id).charAt(0) === "/") return String(id);
  return "/api/background/" + encodeURIComponent(name) + "?b=" + encodeURIComponent(id);
}

// The still frame of a video background, or null when the id is not a video.
function posterFor(id) {
  if (!isVideoArt(id)) return null;
  return String(id).replace(/\.(webm|mp4)$/i, ".poster.jpg");
}

function motionReduced() {
  return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

// A silent looping video for use as a background. It is muted, because browsers refuse to autoplay
// anything else. Its poster is the first frame, so a paused card shows the picture instead of a black box.
function artVideo(url, poster) {
  const v = document.createElement("video");
  v.className = "art-video";
  v.muted = true;
  v.loop = true;
  v.playsInline = true;
  v.preload = "metadata";
  if (poster) v.poster = poster;
  v.setAttribute("muted", "");
  v.setAttribute("playsinline", "");
  v.setAttribute("aria-hidden", "true");
  v.src = url;
  if (artVideoEdge) artVideoEdge.observe(v);
  return v;
}

// A background video plays only while it is wanted, on screen and the tab is showing. A grid of cards
// otherwise decodes every video at once, and an open tab keeps decoding behind other windows.
// v.__want is set by setArtPlaying; v.__inView is set by the observer, and an element it has not reported
// on yet (the chat and profile videos, which sit in the page) counts as in view.
const artVideoEdge = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
  for (const entry of entries) {
    const v = entry.target;
    if (!v.isConnected) { artVideoEdge.unobserve(v); continue; }
    v.__inView = entry.isIntersecting;
    syncArtVideo(v);
  }
}) : null;

function syncArtVideo(v) {
  const on = !!v.__want && v.__inView !== false && !document.hidden && !motionReduced();
  if (on) {
    const p = v.play();
    if (p && p.catch) p.catch(() => {});
  } else {
    v.pause();
  }
}

// Play or pause a background video inside a layer. A refusal (autoplay policy, unloaded source) is not
// worth reporting: the poster frame stays, which is the same thing a still background would show.
function setArtPlaying(host, playing) {
  const v = host && host.querySelector("video");
  if (!v) return;
  v.__want = !!playing;
  syncArtVideo(v);
}

// Switching tabs or minimising the window pauses every wanted video; coming back resumes the ones on screen.
document.addEventListener("visibilitychange", () => {
  for (const v of document.querySelectorAll("video")) {
    if (v.__want) syncArtVideo(v);
  }
});
