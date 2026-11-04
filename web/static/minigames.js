// Mini-games: the dice button opens a picker, a chosen game runs inside a sandboxed frame, and this file
// is the parent half of the bridge - character context in, saves, assets and spoken lines out.
// Progress and finished results are both worth retrying: the server confirms a result by the id the
// page made, so a lost response is asked for again instead of running the round twice.

(function () {
  "use strict";
  let frame = null;      // the live game frame, or null when the list is showing
  let session = null;    // {sid, game, name, character, icon, assets, size, saveSeq, sentSeq} while a game runs
  let lastState = null;  // the last state the game posted, sent back with the result
  let saveTimer = 0;     // debounce handle for the game's save messages
  let saveQueue = Promise.resolve();   // saves go out one at a time, newest last
  let sessionNum = 0;    // which visit a session snapshot came from, so saves stay ordered per game window
  let stats = { mood: null, attitude: null };   // the character's mood and attitude as last fetched
  const sizes = new Map();   // the window each game asked for, so a second visit opens at the right size
  const assetCache = new Map();   // game id -> {file name: data URL}, read once per game per page
  const PENDING_KEY = "miso.minigame.pending";
  let pending = [];      // finished results the server has not acknowledged yet
  let pendingTimer = 0;
  let pendingBusy = false;
  const LIST_SIZE = { w: 820, h: 600 };
  const GAME_SIZE = { w: 600, h: 620 };
  const RETRY_DELAYS = [1000, 3000, 9000];   // pauses between tries for a save or a result
  const PENDING_MAX = 5;                      // in-session tries before a result waits for the next visit

  function dlg() { return $("gameDlg"); }

  // The dialog is centred by its own margins. These two numbers are its whole size, and the game content
  // fills what is left under the header. A window wider or taller than the screen is pulled in to fit.
  function applyWindowSize(w, h) {
    const d = dlg();
    w = Math.round(Number(w));
    h = Math.round(Number(h));
    if (!(w > 0 && h > 0)) return;
    const maxW = Math.max(280, window.innerWidth - 24);
    const maxH = Math.max(320, window.innerHeight - 24);
    d.style.setProperty("--game-w", Math.min(w, maxW) + "px");
    d.style.setProperty("--game-h", Math.min(h, maxH) + "px");
  }

  function chromeHeight() {
    const d = dlg();
    const head = d.querySelector(".game-head");
    const note = $("gameNote");
    const footer = $("gameFooter");
    let n = (head ? head.offsetHeight : 0) + 2;   // the dialog's two 1px borders
    if (note && !note.hidden) n += note.offsetHeight;
    if (footer && !footer.hidden) n += footer.offsetHeight;
    return n;
  }

  // session.size is the width the game asked for and the height it measured; the window adds the
  // header, note and footer that happen to be showing.
  function layoutGame() {
    if (!session || !session.size) return;
    applyWindowSize(session.size.w + 2, session.size.h + chromeHeight());
  }

  function setNote(text) {
    const n = $("gameNote");
    n.textContent = text || "";
    n.hidden = !text;
    layoutGame();
  }

  // Games are for one-to-one chats only; groups and novels have no single character to play with.
  function soloOnly() {
    return !!current && !groupNames.has(current) && !novelIds.has(current);
  }

  function enc(name) { return encodeURIComponent(name); }

  function paintHeaderIcon() {
    const src = $("chatIcon");
    const out = $("gameIcon");
    out.hidden = false;
    out.className = "char-icon" + (src.className.includes("placeholder") ? " placeholder" : "");
    out.style.background = src.style.background;
    out.textContent = "";
    for (const node of src.childNodes) out.appendChild(node.cloneNode(true));
  }

  function renderStats() {
    const bits = [];
    if (stats.mood) {
      bits.push(tr("Energy {n}", { n: stats.mood.energy }));
      bits.push(tr("Affinity {n}", { n: stats.mood.affinity }));
    }
    if (stats.attitude) {
      bits.push(tr("Affection {n}", { n: stats.attitude.affection }));
      const level = String(stats.attitude.level || "");
      if (level) bits.push(level.charAt(0).toUpperCase() + level.slice(1));
    }
    $("gameStats").textContent = bits.length ? bits.join(" · ") : tr("Mood and attitude are off for this character.");
  }

  async function fetchStats(who) {
    try {
      const [m, a] = await Promise.all([
        api("GET", "/api/character-state?character=" + enc(who)).catch(() => null),
        api("GET", "/api/character-attitude?character=" + enc(who)).catch(() => null),
      ]);
      stats = { mood: m && m.enabled ? m : null, attitude: a && a.enabled ? a : null };
    } catch (err) {
      stats = { mood: null, attitude: null };
    }
    renderStats();
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve) => {
      const fr = new FileReader();
      fr.onload = () => resolve(typeof fr.result === "string" ? fr.result : null);
      fr.onerror = () => resolve(null);
      fr.readAsDataURL(blob);
    });
  }

  async function iconDataUrl(name) {
    if (!iconSet.has(name)) return null;
    try {
      const r = await fetch("/api/icon/" + enc(name), { credentials: "same-origin" });
      if (!r.ok) return null;
      return await blobToDataUrl(await r.blob());
    } catch (err) {
      return null;
    }
  }

  // The frame cannot carry a session cookie, so the page reads the game's files itself and hands
  // them over as data URLs. One map per game, fetched the first time that game opens.
  async function loadAssets(gid) {
    if (assetCache.has(gid)) return assetCache.get(gid);
    const map = {};
    try {
      const data = await api("GET", "/api/minigames/assets/" + enc(gid));
      const rows = (data.assets || []).slice(0, 24);
      const got = await Promise.all(rows.map(async (row) => {
        try {
          const r = await fetch("/api/minigames/asset/" + enc(gid) + "/" + enc(row.name),
            { credentials: "same-origin" });
          if (!r.ok) return null;
          const url = await blobToDataUrl(await r.blob());
          return url ? [row.name, url] : null;
        } catch (err) {
          return null;
        }
      }));
      for (const pair of got) if (pair) map[pair[0]] = pair[1];
    } catch (err) { /* a game with no files of its own still runs */ }
    if (assetCache.size > 8) assetCache.clear();
    assetCache.set(gid, map);
    return map;
  }

  // A JSON POST that reports the refusal as an error with its status, without the redirect the
  // shared api() does on a signed-out page: a queued result must not drag a closing tab to /login.
  async function postJson(url, body, opts) {
    const r = await fetch(url, Object.assign({
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "roleplay" },
      body: JSON.stringify(body),
    }, opts || {}));
    let data = {};
    try { data = await r.json(); } catch (e) { /* empty body */ }
    if (!r.ok) {
      const err = new Error((data && data.detail) || tr("Request failed"));
      err.status = r.status;
      throw err;
    }
    return data;
  }

  // A lost POST would lose a save or a round, so a dropped connection or a server hiccup is tried
  // again with a pause. A straight refusal from the server is the answer: no point asking twice.
  async function postWithRetry(url, body, opts) {
    let err = null;
    for (let i = 0; i <= RETRY_DELAYS.length; i++) {
      if (i) await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS[i - 1]));
      try {
        return await postJson(url, body, opts);
      } catch (e) {
        err = e;
        if (e && e.status && e.status < 500) throw e;
      }
    }
    throw err;
  }

  function newResultId() {
    try {
      if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    } catch (err) { /* an older browser: the time and a random tail are unique enough here */ }
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function card(g, saved) {
    // A div rather than a button: the app's button styling is a pill with a filled label, and a card is not that.
    const b = el("div", null, "game-card");
    b.setAttribute("role", "button");
    b.tabIndex = 0;
    b.appendChild(el("p", g.name, "g-name"));
    if (g.description) b.appendChild(el("p", g.description, "g-desc"));
    const meta = [g.source === "imported" ? tr("Imported") : tr("Built in")];
    if (g.author) meta.push(g.author);
    if (saved) meta.push(tr("Progress saved"));
    b.appendChild(el("p", meta.join(" · "), "g-meta"));
    b.addEventListener("click", () => playGame(g));
    b.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); playGame(g); }
    });
    return b;
  }

  async function refreshList() {
    teardown();
    $("gameList").hidden = false;
    $("gameStage").hidden = true;
    $("gameFooter").hidden = true;
    $("gameBack").hidden = true;
    setNote("");
    applyWindowSize(LIST_SIZE.w, LIST_SIZE.h);
    $("gameTitle").textContent = tr("Games");
    if (current) paintHeaderIcon();
    const list = $("gameList");
    list.textContent = "";
    if (!current) {
      $("gameStats").textContent = "";
      list.appendChild(el("p", tr("Open a character to play."), "muted"));
      return;
    }
    if (!soloOnly()) {
      $("gameStats").textContent = "";
      list.appendChild(el("p", tr("Mini-games are for a one-to-one chat. Open a character to play."), "muted"));
      return;
    }
    const who = current;
    const [, data] = await Promise.all([fetchStats(who), api("GET", "/api/minigames").catch((err) => ({ error: err }))]);
    if (current !== who) return;   // the open chat changed while loading
    if (data.error) { list.appendChild(el("p", data.error.message, "muted")); return; }
    if (!data.enabled) { list.appendChild(el("p", tr("Mini-games are turned off on this server."), "muted")); return; }
    const games = (data.games || []).filter((g) => g.on);
    if (!games.length) { list.appendChild(el("p", tr("No games are switched on right now."), "muted")); return; }
    let saves = {};
    try {
      saves = (await api("GET", "/api/minigames/saves?character=" + enc(who))).saves || {};
    } catch (err) { /* the list still works without the saved markers */ }
    if (current !== who) return;
    for (const g of games) list.appendChild(card(g, saves[g.id]));
  }

  async function playGame(g) {
    if (!current) return;
    const who = current;
    teardown();
    setNote("");
    $("gameFooter").hidden = true;
    $("gameAgain").hidden = true;
    $("gameRewards").textContent = "";
    $("gameList").hidden = true;
    $("gameStage").hidden = false;
    $("gameBack").hidden = false;
    $("gameTitle").textContent = g.name;
    paintHeaderIcon();
    const [saveRes, icon, m, a, assets] = await Promise.all([
      api("GET", "/api/minigames/save?character=" + enc(who) + "&game=" + enc(g.id)).catch(() => null),
      iconDataUrl(who),
      api("GET", "/api/character-state?character=" + enc(who)).catch(() => null),
      api("GET", "/api/character-attitude?character=" + enc(who)).catch(() => null),
      loadAssets(g.id),
    ]);
    if (current !== who) { teardown(); return; }   // the chat changed while loading
    stats = { mood: m && m.enabled ? m : null, attitude: a && a.enabled ? a : null };
    renderStats();
    session = { sid: ++sessionNum, game: g.id, name: g.name, character: who, icon, assets,
                size: sizes.get(g.id) || GAME_SIZE, saveSeq: 0, sentSeq: 0 };
    layoutGame();
    lastState = saveRes ? saveRes.state : null;
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("title", g.name);
    f.setAttribute("referrerpolicy", "no-referrer");
    f.srcdoc = "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>" +
      "<script src=\"/static/games/miso-bridge.js\"></" + "script>" +
      "<script src=\"/api/minigames/file/" + enc(g.id) + "\"></" + "script>" +
      "</body></html>";
    frame = f;
    $("gameStage").textContent = "";
    $("gameStage").appendChild(f);
  }

  function sendContext() {
    if (!frame || !session) return;
    const prof = profileMap.get(session.character) || null;
    frame.contentWindow.postMessage({
      src: "miso-app",
      type: "context",
      character: {
        name: session.character,
        icon: session.icon || null,
        color: colorMap[session.character] || "",
        description: prof ? (prof.info || prof.headline || "") : "",
      },
      mood: stats.mood,
      attitude: stats.attitude,
      state: lastState,
      assets: session.assets || {},
      theme: document.documentElement.dataset.theme === "light" ? "light" : "dark",
    }, "*");
  }

  // Progress saves: debounced while the game runs, sent one at a time so two POSTs never race over
  // the same account record, and coalesced so only the newest state goes out. A snapshot remembers
  // which game window it came from, so closing one game never lets it skip behind a freshly opened one.
  function saveSnapshot() {
    if (!session) return null;
    return { sid: session.sid, character: session.character, game: session.game,
             state: lastState, seq: session.saveSeq };
  }

  function queueSave(snap) {
    saveQueue = saveQueue.then(() => sendSave(snap)).catch(() => {});
  }

  async function sendSave(snap) {
    const same = session && session.sid === snap.sid;
    if (same && (session.saveSeq !== snap.seq || session.sentSeq === snap.seq)) return;   // stale or already there
    try {
      await postWithRetry("/api/minigames/save",
        { character: snap.character, game: snap.game, state: snap.state });
      if (session && session.sid === snap.sid) session.sentSeq = snap.seq;
    } catch (err) {
      // The state is kept: the next flush, a closing dialog or a closing page sends it again.
    }
  }

  function flushSave() {
    clearTimeout(saveTimer);
    saveTimer = 0;
    const snap = saveSnapshot();
    if (snap) queueSave(snap);
  }

  function onSave(state) {
    if (!session) return;
    lastState = state === undefined ? null : state;
    session.saveSeq += 1;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, 1000);
  }

  async function onSay(id, situation) {
    if (!session) return;
    const f = frame;
    try {
      const r = await api("POST", "/api/minigames/say",
        { character: session.character, game: session.game, situation: String(situation || "") });
      if (f) f.contentWindow.postMessage({ src: "miso-app", type: "say-result", id, text: r.text }, "*");
      // The server already stored the line in the chat; show it when it arrives.
      if (session && session.character === current) addLine(session.character, r.text, "theirs", new Date().toISOString());
    } catch (err) {
      if (f) f.contentWindow.postMessage({ src: "miso-app", type: "say-result", id, text: null }, "*");
      // A hit rate limit just means the character stays quiet for a moment; not worth a scare.
      if (err.status !== 429) setNote(err.message || tr("The character could not answer."));
    }
  }

  // A card game asks which three cards the character would pick. The server keeps that answer per
  // character, so opening the same game again costs no model call and shows the same three cards.
  async function onPrefs(id) {
    if (!session) return;
    const f = frame;
    try {
      const r = await api("POST", "/api/minigames/preferences",
        { character: session.character, game: session.game });
      const ids = Array.isArray(r.ids) ? r.ids : null;
      if (f && f.contentWindow) f.contentWindow.postMessage({ src: "miso-app", type: "prefs-result", id, ids }, "*");
    } catch (err) {
      if (f && f.contentWindow) f.contentWindow.postMessage({ src: "miso-app", type: "prefs-result", id, ids: null }, "*");
      // A hit rate limit means the cards stay unread for a moment; the frame says so itself.
      if (err.status !== 429) setNote(err.message || tr("The character could not choose three cards."));
    }
  }

  // ---- results: one record per finished round until the server acknowledges it ----

  function loadPending() {
    try {
      const list = JSON.parse(localStorage.getItem(PENDING_KEY) || "[]");
      if (!Array.isArray(list)) return [];
      const cutoff = Date.now() - 24 * 3600 * 1000;
      return list.filter((p) => p && p.body && typeof p.body.resultId === "string" && (p.ts || 0) >= cutoff);
    } catch (err) {
      return [];
    }
  }

  function savePending() {
    // The try count stays in memory on purpose: a fresh visit gets a fresh set of tries.
    try {
      localStorage.setItem(PENDING_KEY, JSON.stringify(
        pending.slice(-4).map((p) => ({ body: p.body, ts: p.ts }))));
    } catch (err) { /* private browsing: the queue then lives for this page only */ }
  }

  function rememberResult(body) {
    pending = pending.filter((p) => p.body.resultId !== body.resultId);
    pending.push({ body, ts: Date.now(), tries: 0 });
    savePending();
  }

  function forgetResult(resultId) {
    pending = pending.filter((p) => p.body.resultId !== resultId);
    savePending();
  }

  function schedulePending(delay) {
    clearTimeout(pendingTimer);
    pendingTimer = setTimeout(deliverPending, delay);
  }

  function paintResult(body, r) {
    if (session) {
      if (r.mood) stats.mood = r.mood;
      if (r.attitude) stats.attitude = r.attitude;
      renderStats();
      $("gameRewards").textContent = (r.rewards || []).length
        ? r.rewards.map((x) => (x.delta >= 0 ? "+" : "") + x.delta + " " + tr(x.key) + " → " + x.now).join(" · ")
        : tr("Mood and attitude are off, so this game gave no rewards.");
    }
    // A replayed result is one the server already wrote into the chat: rewards again, line not twice.
    if (!r.replayed && body.character === current) {
      addLine("Narrator", r.note, "note event", new Date().toISOString());
    }
    refreshMe();
  }

  function nextPending() {
    return pending.find((p) => !p.stopped) || null;
  }

  function morePending() {
    return pending.some((p) => !p.stopped);
  }

  // One round went through, so the server is answering again: anything that gave up for this visit
  // is worth one more try, its try count already carried over from the last go.
  function resumePending() {
    if (!pending.length) return;
    for (const p of pending) p.stopped = false;
    schedulePending(500);
  }

  // The oldest live result, sent until the server takes it. A refusal that will never become an
  // answer drops the round; a limit or a quiet server keeps it for later, this visit or the next.
  async function deliverPending() {
    clearTimeout(pendingTimer);
    pendingTimer = 0;
    if (pendingBusy) return;
    const item = nextPending();
    if (!item) return;
    pendingBusy = true;
    try {
      const r = await postWithRetry("/api/minigames/play", item.body);
      forgetResult(item.body.resultId);
      paintResult(item.body, r);
      pendingBusy = false;
      resumePending();
    } catch (err) {
      pendingBusy = false;
      const status = err && err.status;
      if (status === 400 || status === 403) {
        forgetResult(item.body.resultId);
        if (session) setNote(err.message || tr("The result could not be saved."));
        if (morePending()) schedulePending(300);
        return;
      }
      item.tries = (item.tries || 0) + 1;
      if (item.tries >= PENDING_MAX) {
        // Out of tries for this visit. The state already holds the score; the record waits in
        // storage for the next time the app opens, where the try count starts again.
        item.stopped = true;
        if (session) setNote(tr("The result is kept and will be sent again next time the app opens."));
        if (morePending()) schedulePending(300);
        return;
      }
      schedulePending(status === 429 ? 20000 : status === 401 ? 60000 : Math.min(30000, 2000 * item.tries));
      if (session) setNote(err.message || tr("The result could not be saved yet. Trying again in a moment."));
    }
  }

  async function onResult(outcome, finalState, score) {
    if (!session) return;
    if (finalState !== undefined) {
      // The game ended on this state, whether or not it called save first.
      lastState = finalState;
      session.saveSeq += 1;
    }
    // The result carries the final state itself, so the debounced save is not sent as well.
    clearTimeout(saveTimer);
    saveTimer = 0;
    const body = {
      character: session.character, game: session.game, outcome,
      state: lastState, resultId: newResultId(),
    };
    // A scored game also reports its counts. The server checks them against the game's own rule.
    if (score && typeof score === "object" && !Array.isArray(score)) body.score = score;
    $("gameAgain").hidden = false;
    $("gameFooter").hidden = false;
    layoutGame();
    $("gameRewards").textContent = tr("Saving the result…");
    rememberResult(body);
    try {
      const r = await postWithRetry("/api/minigames/play", body);
      forgetResult(body.resultId);
      paintResult(body, r);
      resumePending();
    } catch (err) {
      $("gameRewards").textContent = "";
      setNote(err.message || tr("The result could not be saved yet. Trying again in a moment."));
      schedulePending(err && err.status === 429 ? 20000 : 10000);
    }
  }

  function onSize(width, height) {
    if (!session) return;
    const w = Math.round(Number(width));
    const h = Math.round(Number(height));
    const next = { w: w >= 240 ? w : session.size.w, h: h >= 120 ? h : session.size.h };
    if (next.w === session.size.w && next.h === session.size.h) return;
    session.size = next;
    sizes.set(session.game, session.size);
    layoutGame();
  }

  function onMessage(ev) {
    if (!frame || ev.source !== frame.contentWindow) return;
    const data = ev.data;
    if (!data || data.src !== "miso-game") return;
    if (data.type === "loaded") sendContext();
    else if (data.type === "size") onSize(data.width, data.height);
    else if (data.type === "save") onSave(data.state);
    else if (data.type === "say") onSay(data.id, data.situation);
    else if (data.type === "prefs") onPrefs(data.id);
    else if (data.type === "result") onResult(data.outcome, data.state, data.score);
    else if (data.type === "error") setNote(String(data.message || tr("The game stopped with an error.")));
  }

  function teardown() {
    clearTimeout(saveTimer);
    saveTimer = 0;
    if (session && session.saveSeq !== session.sentSeq) queueSave(saveSnapshot());   // closing still saves
    if (frame) { frame.remove(); frame = null; }
    session = null;
    lastState = null;
    const stage = $("gameStage");
    if (stage) stage.textContent = "";
  }

  async function openGames() {
    if (!current) return;
    const d = dlg();
    if (!d.open) d.showModal();
    await refreshList();
  }

  function closeGames() {
    const d = dlg();
    if (d.open) d.close();   // the close event tears the frame down
  }

  $("gamesBtn").addEventListener("click", openGames);
  $("gameClose").addEventListener("click", closeGames);
  $("gameDone").addEventListener("click", closeGames);
  $("gameBack").addEventListener("click", () => refreshList());
  $("gameAgain").addEventListener("click", () => {
    if (session) playGame({ id: session.game, name: session.name });
  });
  dlg().addEventListener("close", teardown);
  window.addEventListener("resize", () => {
    if (!dlg().open) return;
    if (session) layoutGame();
    else applyWindowSize(LIST_SIZE.w, LIST_SIZE.h);
  });
  window.addEventListener("message", onMessage);

  // A closing page must not lose the last second of progress or a round it never saw answered:
  // both queues get one fire-and-forget chance as the page goes.
  window.addEventListener("pagehide", () => {
    if (session && session.saveSeq !== session.sentSeq) {
      const snap = saveSnapshot();
      if (snap) {
        postJson("/api/minigames/save",
          { character: snap.character, game: snap.game, state: snap.state }, { keepalive: true })
          .catch(() => {});
      }
    }
    for (const item of pending.slice(0, 4)) {
      postJson("/api/minigames/play", item.body, { keepalive: true })
        .then(() => forgetResult(item.body.resultId))
        .catch(() => {});
    }
  });

  // Results left over from an earlier visit are sent again shortly after the app opens. The server
  // answers a round it already has with the reply it gave then, so nothing runs twice.
  pending = loadPending();
  if (pending.length) schedulePending(2000);
})();
