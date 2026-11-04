// Memory Match - a built-in mini-game. Turn cards two at a time; find all eight pairs before you give up.
// Runs inside the sandboxed game frame and talks to the app only through the MisoGame bridge.

(function () {
  "use strict";

  const GLYPHS = ["●", "◆", "▲", "★", "✦", "◇", "○", "■"];
  const MAX_MOVES = 40;

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
    ".top .grow{flex:1;}" +
    ".line{margin:0 0 12px;padding:8px 12px;border-radius:12px;max-width:75%;font-size:14px;" +
    "background:rgba(120,140,180,.16);}" +
    ".grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;max-width:520px;}" +
    ".card{aspect-ratio:1;border-radius:12px;border:1px solid rgba(140,160,200,.35);background:transparent;" +
    "color:inherit;font-size:26px;cursor:pointer;transition:background .15s,transform .1s;}" +
    ".card:hover:not(:disabled){background:rgba(120,140,180,.16);}" +
    ".card.open{background:rgba(76,196,255,.22);border-color:#4cc4ff;}" +
    ".card.gone{opacity:.28;cursor:default;}" +
    ".card:disabled{cursor:default;}" +
    "button.pill{border:1px solid rgba(140,160,200,.45);background:transparent;color:inherit;border-radius:999px;" +
    "padding:7px 14px;cursor:pointer;font:inherit;font-size:13px;}" +
    "button.pill:hover{background:rgba(120,140,180,.16);}"
  );

  MisoGame.register({
    id: "memory-match",
    name: "Memory Match",
    size: { w: 548 },
    start(ctx) {
      const old = ctx.state;
      const st = old && !old.done && Array.isArray(old.deck) && old.deck.length === 16 ? old
        : { deck: shuffle(GLYPHS.concat(GLYPHS)), matched: new Array(16).fill(false), moves: 0, done: false };

      const open = [];   // indices showing but not yet matched
      let busy = false;
      let greeted = false;

      ctx.root.appendChild(buildTop());

      const grid = el("div", "grid");
      ctx.root.appendChild(grid);
      const cards = [];
      for (let i = 0; i < 16; i++) {
        const b = el("button", "card", "?");
        b.type = "button";
        b.addEventListener("click", () => flip(i));
        cards.push(b);
        grid.appendChild(b);
      }

      const bar = el("div");
      bar.style.marginTop = "14px";
      const moves = el("span", "sub", "");
      moves.style.marginRight = "12px";
      const giveUp = el("button", "pill", "Give up");
      giveUp.type = "button";
      giveUp.addEventListener("click", () => {
        if (st.done) return;
        st.done = true;
        save();
        ctx.finish("loss");
        sub.textContent = "You gave up. The open cards are " + left();
      });
      bar.appendChild(moves);
      bar.appendChild(giveUp);
      ctx.root.appendChild(bar);
      const sub = el("p", "sub");
      ctx.root.appendChild(sub);

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
        who.appendChild(el("div", "who", ctx.character.name || "Memory Match"));
        const mood = ctx.mood ? "Energy " + ctx.mood.energy + " - Affinity " + ctx.mood.affinity : "Memory Match";
        who.appendChild(el("div", "sub", mood));
        top.appendChild(who);
        top.appendChild(el("span", "grow"));
        return top;
      }

      function draw() {
        for (let i = 0; i < 16; i++) {
          const c = cards[i];
          const matched = st.matched[i];
          const shown = matched || open.indexOf(i) !== -1;
          c.textContent = shown ? st.deck[i] : "?";
          c.className = "card" + (shown ? " open" : "") + (matched ? " gone" : "");
          c.disabled = matched || st.done;
        }
        const left = 8 - st.matched.filter(Boolean).length;
        moves.textContent = st.moves + (st.moves === 1 ? " move" : " moves") + " · " + left + " pairs left";
        if (st.done) giveUp.disabled = true;
      }

      function left() {
        return 8 - st.matched.filter(Boolean).length;
      }

      function save() {
        ctx.save({ deck: st.deck, matched: st.matched, moves: st.moves, done: st.done });
      }

      function flip(i) {
        if (busy || st.done || st.matched[i] || open.indexOf(i) !== -1) return;
        open.push(i);
        draw();
        if (open.length < 2) return;
        busy = true;
        st.moves += 1;
        const a = open[0];
        const b = open[1];
        setTimeout(() => {
          if (st.deck[a] === st.deck[b]) {
            st.matched[a] = true;
            st.matched[b] = true;
            open.length = 0;
            busy = false;
            save();
            draw();
            if (!greeted && st.matched.filter(Boolean).length === 2) {
              greeted = true;
              const mood = ctx.mood ? " Their energy is " + ctx.mood.energy + " out of 100." : "";
              ctx.say("The player just found their first matching pair in a memory game with you." + mood)
                .then(showLine);
            }
            if (st.matched.every(Boolean)) {
              st.done = true;
              save();
              draw();
              sub.textContent = "Every pair found in " + st.moves + " moves.";
              ctx.finish("win");
              ctx.say("The player just cleared the whole memory game and won. Congratulate them in one short line.")
                .then(showLine);
            }
          } else {
            open.length = 0;
            busy = false;
            save();
            draw();
            if (st.moves >= MAX_MOVES) {
              st.done = true;
              save();
              draw();
              sub.textContent = "Out of moves.";
              ctx.finish("loss");
            }
          }
        }, 650);
      }

      function showLine(text) {
        if (typeof text !== "string" || !text) return;
        const p = el("p", "line", (ctx.character.name ? ctx.character.name + ": " : "") + text);
        ctx.root.insertBefore(p, ctx.root.children[1] || null);
      }

      draw();
      if (!st.moves) sub.textContent = "Find all eight pairs. You have " + MAX_MOVES + " moves.";
    },
  });
})();
