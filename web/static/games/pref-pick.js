// miso-game: {"id": "pref-pick", "name": "Preference Pick", "description": "Guess which three preference cards the character would pick from a lineup of ten.", "version": "1.0.0"}
// Preference Pick - an importable mini-game. Ten cards are laid out, three of them the character's
// own picks, and six guesses are enough to find them. Runs inside the sandboxed game frame and talks
// to the app only through the MisoGame bridge.

(function () {
  "use strict";

  const TRIES = 6;
  const LINEUP = 10;
  const ENERGY = 5;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function shuffle(list) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }

  function addStyles(css) {
    const s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }

  addStyles(
    ".top{display:flex;align-items:center;gap:10px;margin-bottom:12px;}" +
    ".top .pic{width:38px;height:38px;border-radius:50%;object-fit:cover;flex:none;display:inline-flex;" +
    "align-items:center;justify-content:center;font-weight:700;color:#fff;background:#4cc4ff;}" +
    ".top .who{font-weight:600;}" +
    ".top .sub{font-size:12px;opacity:.7;}" +
    ".line{margin:0 0 12px;padding:8px 12px;border-radius:12px;max-width:75%;font-size:14px;" +
    "background:rgba(120,140,180,.16);}" +
    ".grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;max-width:520px;}" +
    ".card{border-radius:12px;border:1px solid rgba(140,160,200,.35);background:transparent;" +
    "color:inherit;font:inherit;font-size:14px;padding:12px 10px;cursor:pointer;text-align:left;" +
    "transition:background .15s;}" +
    ".card:hover:not(:disabled){background:rgba(120,140,180,.16);}" +
    ".card.right{background:rgba(76,196,255,.22);border-color:#4cc4ff;}" +
    ".card.wrong{opacity:.55;}" +
    ".card.missed{border-color:#4cc4ff;border-style:dashed;}" +
    ".card:disabled{cursor:default;}" +
    ".progress{margin:12px 0 0;font-size:13px;opacity:.8;}"
  );

  // The deck travels with the game as one of its own files (prefcards.json), so importing the zip
  // brings the cards too. The bridge hands files over as data URLs; this reads the JSON back out.
  function readDeck(ctx) {
    const raw = typeof ctx.asset === "function" ? ctx.asset("prefcards.json") : null;
    if (typeof raw !== "string" || !raw) return null;
    const comma = raw.indexOf(",");
    if (comma < 0 || raw.slice(0, comma).indexOf(";base64") < 0) return null;
    let text;
    try {
      const bin = atob(raw.slice(comma + 1));
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      text = new TextDecoder("utf-8").decode(bytes);
    } catch (err) {
      return null;
    }
    let data;
    try { data = JSON.parse(text); } catch (err) { return null; }
    const rows = data && Array.isArray(data.cards) ? data.cards : null;
    if (!rows) return null;
    const deck = [];
    const seen = {};
    for (const row of rows) {
      if (!row || typeof row.id !== "string" || !row.id || seen[row.id]) continue;
      seen[row.id] = true;
      deck.push({ id: row.id, label: String(row.label || row.id),
                  description: String(row.description || "") });
    }
    return deck.length ? deck : null;
  }

  MisoGame.register({
    id: "pref-pick",
    name: "Preference Pick",
    size: { w: 548 },
    start(ctx) {
      const deck = readDeck(ctx);
      if (!deck || deck.length < 4) {
        ctx.root.appendChild(el("p", "line",
          "The card deck did not load. It should be one of this game's files, called prefcards.json; " +
          "import the game's zip again or add that file from the game's Files list."));
        return;
      }
      const byId = {};
      for (const c of deck) byId[c.id] = { id: c.id, label: String(c.label || c.id), description: String(c.description || "") };

      let st = savedState(ctx.state, byId);
      let busy = false;

      ctx.root.appendChild(buildTop());
      const grid = el("div", "grid");
      ctx.root.appendChild(grid);
      const progress = el("p", "progress", "");
      ctx.root.appendChild(progress);
      const sub = el("p", "sub", "");
      ctx.root.appendChild(sub);

      const buttons = [];
      for (let i = 0; i < LINEUP; i++) {
        const b = el("button", "card", "");
        b.type = "button";
        b.hidden = true;
        b.addEventListener("click", () => pick(i));
        buttons.push(b);
        grid.appendChild(b);
      }

      function buildTop() {
        const top = el("div", "top");
        const icon = ctx.character.icon;
        if (icon) {
          const img = el("img", "pic");
          img.alt = "";
          img.src = icon;
          top.appendChild(img);
        } else {
          const face = el("span", "pic", String((ctx.character.name || "?").charAt(0)).toUpperCase());
          if (/^#[0-9a-f]{6}$/i.test(ctx.character.color || "")) face.style.background = ctx.character.color;
          top.appendChild(face);
        }
        const who = el("div");
        who.appendChild(el("div", "who", ctx.character.name || "Preference Pick"));
        const mood = ctx.mood && typeof ctx.mood.energy === "number"
          ? ENERGY + " energy · Energy " + ctx.mood.energy
          : ENERGY + " energy";
        who.appendChild(el("div", "sub", mood));
        top.appendChild(who);
        return top;
      }

      function savedState(old, cards) {
        if (!old || old.done) return null;
        if (!Array.isArray(old.prefs) || old.prefs.length !== 3) return null;
        if (!Array.isArray(old.lineup) || !old.lineup.length || old.lineup.length > LINEUP) return null;
        if (!Array.isArray(old.picks)) return null;
        for (const id of old.prefs.concat(old.lineup, old.picks)) {
          if (typeof id !== "string" || !cards[id]) return null;
        }
        return { prefs: old.prefs.slice(), lineup: old.lineup.slice(), picks: old.picks.slice(),
                 right: Number(old.right) || 0, wrong: Number(old.wrong) || 0, done: false };
      }

      function present() {
        return st.lineup.filter((id) => st.prefs.indexOf(id) !== -1).length;
      }

      function triesLeft() {
        return Math.max(0, TRIES - st.picks.length);
      }

      function save() {
        st.done = st.done || false;
        ctx.save({ prefs: st.prefs, lineup: st.lineup, picks: st.picks,
                   right: st.right, wrong: st.wrong, done: st.done });
      }

      function draw() {
        for (let i = 0; i < LINEUP; i++) {
          const b = buttons[i];
          const id = st ? st.lineup[i] : null;
          if (!id) { b.hidden = true; continue; }
          b.hidden = false;
          const card = byId[id];
          const picked = st.picks.indexOf(id) !== -1;
          const mine = st.prefs.indexOf(id) !== -1;
          let mark = "";
          let cls = "card";
          if (picked) {
            if (mine) { cls += " right"; mark = " ✔"; }
            else { cls += " wrong"; mark = " ✘"; }
          } else if (st.done && mine) {
            cls += " missed";
          }
          b.className = cls;
          b.textContent = card.label + mark;
          b.disabled = st.done || picked;
        }
        if (!st) { progress.textContent = ""; return; }
        progress.textContent = st.right + " of " + present() + " found · " +
          triesLeft() + (triesLeft() === 1 ? " try left" : " tries left");
        if (st.done) progress.textContent += " · the round is over";
      }

      function pickLine(card, mine, ended) {
        let s = "The player just picked the card \"" + card.label + "\" in a preference game with you " +
          (mine ? "and found one of the three you would pick." : "and it is not one of the three you would pick.");
        if (card.description) s += " The card stands for: " + card.description;
        s += " So far " + st.right + " of " + present() + " of your picks are found and " +
          st.picks.length + " of " + TRIES + " guesses are used.";
        if (ended === "win") s += " They found every pick that was on the table and won the round.";
        else if (ended === "draw") s += " The guesses ran out, but they had found some of your picks.";
        else if (ended === "loss") s += " The guesses ran out and they found none of your picks.";
        return s;
      }

      function showLine(text) {
        if (typeof text !== "string" || !text) return;
        const p = el("p", "line", (ctx.character.name ? ctx.character.name + ": " : "") + text);
        ctx.root.insertBefore(p, grid);
      }

      function finish(outcome) {
        st.done = true;
        save();
        draw();
        ctx.finish(outcome, { prefs: st.prefs, lineup: st.lineup, picks: st.picks,
                              right: st.right, wrong: st.wrong, done: true },
                  { right: st.right, wrong: st.wrong });
        const missed = st.prefs.filter((id) => st.lineup.indexOf(id) !== -1 && st.picks.indexOf(id) === -1);
        const off = st.prefs.filter((id) => st.lineup.indexOf(id) === -1);
        let tail = "";
        if (missed.length) tail += " The picks they never found: " +
          missed.map((id) => byId[id].label).join(", ") + ".";
        if (off.length) tail += " Not on the table at all: " +
          off.map((id) => byId[id].label).join(", ") + ".";
        sub.textContent = tail.trim();
      }

      function pick(i) {
        if (!st || busy || st.done) return;
        const id = st.lineup[i];
        if (!id || st.picks.indexOf(id) !== -1) return;
        busy = true;
        const card = byId[id];
        const mine = st.prefs.indexOf(id) !== -1;
        st.picks.push(id);
        if (mine) st.right += 1;
        else st.wrong += 1;
        save();
        let ended = null;
        if (st.right >= present()) ended = "win";
        else if (st.picks.length >= TRIES) ended = st.right > 0 ? "draw" : "loss";
        if (ended) finish(ended);
        else draw();
        busy = false;
        ctx.say(pickLine(card, mine, ended)).then(showLine);
      }

      function buildLineup(prefs) {
        const size = Math.min(LINEUP, deck.length);
        const chosen = prefs.filter(() => Math.random() < 0.5);
        if (!chosen.length) chosen.push(prefs[Math.floor(Math.random() * prefs.length)]);
        const rest = shuffle(deck.map((c) => c.id).filter((id) => prefs.indexOf(id) === -1));
        const out = chosen.slice();
        for (let i = 0; out.length < size && i < rest.length; i++) out.push(rest[i]);
        return shuffle(out);
      }

      function failed(text) {
        sub.textContent = text;
        progress.textContent = "";
      }

      function begin() {
        sub.textContent = "Choosing three cards…";
        Promise.resolve(ctx.preferences()).then((ids) => {
          if (!Array.isArray(ids) || ids.length !== 3) {
            failed("The character did not choose three cards. Try again in a moment.");
            return;
          }
          const clean = [];
          for (const id of ids) if (byId[id] && clean.indexOf(id) === -1) clean.push(id);
          if (clean.length !== 3) {
            failed("The character chose a card that is not in the deck. Try again in a moment.");
            return;
          }
          st = { prefs: clean, lineup: buildLineup(clean), picks: [], right: 0, wrong: 0, done: false };
          save();
          sub.textContent = "";
          draw();
        }, () => failed("The character could not choose three cards. Try again in a moment."));
      }

      if (st) draw();
      else { draw(); begin(); }
    },
  });
})();
