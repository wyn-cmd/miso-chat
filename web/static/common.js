// Shared helpers for every page. Model output is only ever written with textContent.

// Theme and highlight colour. Stored per browser in localStorage ("rp_theme", "rp_accent").
// The light/dark tokens live in style.css; theme-init.js sets the attribute before first paint.
const ACCENT_DEFAULT = "#4cc4ff";      // portfolio cyan; used only as the picker's starting value
const THEME_KEY = "rp_theme";
const SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; } catch (e) { return "dark"; }
}

function applyAccent(hex) {
  const s = document.documentElement.style;
  const v = /^#[0-9a-f]{6}$/i.test(hex || "") ? hex.toLowerCase() : null;
  if (!v) {
    // No custom colour: drop the overrides so the theme's own accent (cyan dark, blue light) applies.
    for (const n of ["--accent", "--accent-2", "--on-accent", "--accent-soft", "--accent-glow"]) s.removeProperty(n);
    return ACCENT_DEFAULT;
  }
  const r = parseInt(v.slice(1, 3), 16), g = parseInt(v.slice(3, 5), 16), b = parseInt(v.slice(5, 7), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;   // light accent gets dark text, and the reverse
  s.setProperty("--accent", v);
  s.setProperty("--on-accent", lum > 0.6 ? "#04121f" : "#ffffff");
  s.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, 0.14)`);
  s.setProperty("--accent-glow", `rgba(${r}, ${g}, ${b}, 0.38)`);
  // The gradient's second stop: half the chosen colour, half the portfolio violet, so buttons keep the two-tone look.
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
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = t === "light" ? "#f6f8fc" : "#020307";
  for (const b of document.querySelectorAll("#themeBtn")) {
    const label = t === "light" ? "Switch to dark theme" : "Switch to light theme";
    b.setAttribute("aria-label", label);
    b.title = label;
    b.innerHTML = t === "light" ? MOON_ICON : SUN_ICON;   // static markup, never model output
  }
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* private mode: applies for this page only */ }
  applyTheme(next);
}

function wireThemeButtons() {
  for (const b of document.querySelectorAll("#themeBtn")) b.addEventListener("click", toggleTheme);
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

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
