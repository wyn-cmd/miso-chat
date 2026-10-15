// Shared helpers for every page. Model output is only ever written with textContent.

// Highlight colour: drives the accent used for the chat outline, buttons, links and settings/admin text.
// Stored per browser in localStorage ("rp_accent"). Every page loads this file, so every page picks it up.
const ACCENT_DEFAULT = "#e8836a";
function applyAccent(hex) {
  const v = /^#[0-9a-f]{6}$/i.test(hex || "") ? hex.toLowerCase() : ACCENT_DEFAULT;
  const r = parseInt(v.slice(1, 3), 16), g = parseInt(v.slice(3, 5), 16), b = parseInt(v.slice(5, 7), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;   // light accent gets dark text, and the reverse
  const s = document.documentElement.style;
  s.setProperty("--accent", v);
  s.setProperty("--on-accent", lum > 0.6 ? "#14110f" : "#fff8f3");
  s.setProperty("--accent-soft", `rgba(${r}, ${g}, ${b}, 0.16)`);
  s.setProperty("--accent-glow", `rgba(${r}, ${g}, ${b}, 0.4)`);
  // Filled buttons: a shade off the page background (#14110f), nudged 4% toward the highlight colour.
  const bgRgb = [0x14, 0x11, 0x0f], hiRgb = [r, g, b];
  const btn = bgRgb.map((c, i) => Math.round(c + (hiRgb[i] - c) * 0.04));
  s.setProperty("--btn-bg", "#" + btn.map((n) => n.toString(16).padStart(2, "0")).join(""));
  return v;
}
function savedAccent() {
  try { return localStorage.getItem("rp_accent"); } catch (e) { return null; }
}
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

// POST /api/chat answers with server-sent events. handlers: delta, done, usage, error.
async function streamChat(body, handlers) {
  const r = await fetch("/api/chat", {
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
