// miso-bridge.js
// Loaded inside the game iframe, before the game's own script. The frame runs sandboxed with an opaque
// origin (no cookies, no storage, no API access), so this file is the game's whole window on the app:
// it defines MisoGame, receives the character context from the parent page and sends saves, model
// requests and results back over postMessage.

(function () {
  "use strict";
  const registered = [];
  let context = null;
  let started = false;
  let finished = false;
  let saySeq = 0;
  const sayWaiters = new Map();
  let prefSeq = 0;
  const prefWaiters = new Map();

  function post(msg) {
    if (window.parent === window) return;
    msg.src = "miso-game";
    window.parent.postMessage(msg, "*");   // the sandbox hides the parent's origin, so "*" is required
  }

  window.MisoGame = {
    // One game file calls this once: {id, name, size, start(ctx)}. start runs once the parent has sent
    // the character context. size is the width the game wants, e.g. {w: 560}; its height is measured
    // from what it draws. The app adds its own header and keeps the window inside the screen.
    register(def) {
      if (!def || typeof def.start !== "function") throw new Error("MisoGame.register needs start(ctx)");
      registered.push(def);
      const w = Math.round(Number((def.size || {}).w));
      if (w >= 240) post({ type: "size", width: Math.min(w, 2400) });
      maybeStart();
    },
  };

  // The height the game actually uses, measured from its drawn content, so the frame can end where the
  // game does. The root stretches to the frame, so its own height says nothing; its last child does.
  function measureHeight(root) {
    const top = root.getBoundingClientRect().top;
    let bottom = 0;
    for (const child of root.children) {
      const r = child.getBoundingClientRect();
      if (r.height) bottom = Math.max(bottom, r.bottom - top);
    }
    return Math.ceil(bottom + 14);   // plus the root's bottom padding
  }

  function watchSize(root) {
    let queued = false;
    const send = () => {
      queued = false;
      const h = measureHeight(root);
      if (h > 0) post({ type: "size", height: h });
    };
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(() => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(send);
      }).observe(root);
    }
    requestAnimationFrame(send);
  }

  function maybeStart() {
    if (started || !context || !registered.length) return;
    started = true;
    const def = registered[0];
    document.body.textContent = "";
    const root = document.createElement("div");
    root.className = "game-root";
    document.body.appendChild(root);
    let saved = null;   // the last state the game saved, sent along with the result
    const assets = context.assets || {};
    const ctx = {
      root,
      character: context.character || {},
      mood: context.mood || null,
      attitude: context.attitude || null,
      state: context.state || null,
      theme: context.theme === "light" ? "light" : "dark",
      // Files the admin attached to this game, as data URLs: {"card.png": "data:image/png;base64,..."}.
      assets,
      asset(name) {
        return assets[name] || null;
      },
      save(state) {
        saved = state === undefined ? null : state;
        post({ type: "save", state: saved });
      },
      say(situation) {
        const id = ++saySeq;
        return new Promise((resolve) => {
          const timer = setTimeout(() => { sayWaiters.delete(id); resolve(null); }, 60000);
          sayWaiters.set(id, (text) => { clearTimeout(timer); sayWaiters.delete(id); resolve(text); });
          post({ type: "say", id, situation: String(situation || "").slice(0, 1200) });
        });
      },
      // A card game asks the app for the three cards this character would pick; the app asks the
      // model once per character and keeps the answer. Resolves to three ids, or null if it failed.
      preferences() {
        const id = ++prefSeq;
        return new Promise((resolve) => {
          const timer = setTimeout(() => { prefWaiters.delete(id); resolve(null); }, 60000);
          prefWaiters.set(id, (ids) => { clearTimeout(timer); prefWaiters.delete(id); resolve(ids); });
          post({ type: "prefs", id });
        });
      },
      // finish(outcome) ends the round; pass the last state as the second argument to have it
      // stored with the result even if the game never called save after the final move. A game that
      // keeps a score passes its counts as the third argument, and the server pays out by them.
      finish(outcome, finalState, score) {
        if (finished) return;
        if (outcome !== "win" && outcome !== "loss" && outcome !== "draw") return;
        if (finalState !== undefined) saved = finalState;
        finished = true;
        post({ type: "result", outcome, state: saved, score: score === undefined ? null : score });
      },
    };
    try {
      def.start(ctx);
    } catch (err) {
      post({ type: "error", message: String((err && err.message) || err).slice(0, 300) });
    }
    watchSize(root);
  }

  window.addEventListener("message", (ev) => {
    if (ev.source !== window.parent) return;
    const data = ev.data;
    if (!data || data.src !== "miso-app") return;
    if (data.type === "context") {
      context = data;
      document.documentElement.dataset.theme = data.theme === "light" ? "light" : "dark";
      maybeStart();
    } else if (data.type === "say-result") {
      const resolve = sayWaiters.get(data.id);
      if (resolve) resolve(typeof data.text === "string" ? data.text : null);
    } else if (data.type === "prefs-result") {
      const resolve = prefWaiters.get(data.id);
      if (resolve) resolve(Array.isArray(data.ids) ? data.ids : null);
    }
  });

  // A clean canvas to start from; each game dresses its own root.
  const style = document.createElement("style");
  style.textContent =
    "html,body{margin:0;padding:0;height:100%;}" +
    "body{font:15px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;background:#10151f;color:#e9eff9;}" +
    "html[data-theme='light'] body{background:#f6f8fc;color:#0d1728;}" +
    ".game-root{box-sizing:border-box;min-height:100%;padding:14px;}";
  document.head.appendChild(style);

  post({ type: "loaded" });
})();
