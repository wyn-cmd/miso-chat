// Dice Duel - a built-in mini-game. Three rolls against the character; most round wins takes the duel.
// Runs inside the sandboxed game frame and talks to the app only through the MisoGame bridge.

(function () {
  "use strict";

  const ROUNDS = 3;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
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
    ".board{display:flex;gap:22px;align-items:center;margin:16px 0;flex-wrap:wrap;}" +
    ".side{text-align:center;}" +
    ".side .name{font-size:13px;opacity:.75;margin-bottom:6px;}" +
    ".die{width:86px;height:86px;border-radius:16px;border:2px solid rgba(140,160,200,.4);display:flex;" +
    "align-items:center;justify-content:center;font-size:40px;font-weight:700;}" +
    ".versus{font-size:13px;opacity:.6;}" +
    ".tally{font-size:14px;opacity:.8;}" +
    "button.pill{border:1px solid rgba(140,160,200,.45);background:transparent;color:inherit;border-radius:999px;" +
    "padding:9px 20px;cursor:pointer;font:inherit;font-size:14px;}" +
    "button.pill:hover:not(:disabled){background:rgba(120,140,180,.16);}" +
    "button.pill:disabled{opacity:.5;cursor:default;}"
  );

  MisoGame.register({
    id: "dice-duel",
    name: "Dice Duel",
    size: { w: 400 },
    start(ctx) {
      const old = ctx.state;
      const st = old && !old.done && Array.isArray(old.mine) ? old
        : { round: 0, mine: [], theirs: [], done: false };

      let busy = false;
      let greeted = false;

      const top = el("div", "top");
      if (ctx.character.icon) {
        const img = el("img", "pic");
        img.alt = "";
        img.src = ctx.character.icon;
        top.appendChild(img);
      } else {
        const face = el("span", "pic", String((ctx.character.name || "?").charAt(0)).toUpperCase());
        if (/^#[0-9a-f]{6}$/i.test(ctx.character.color || "")) face.style.background = ctx.character.color;
        top.appendChild(face);
      }
      const who = el("div");
      who.appendChild(el("div", "who", ctx.character.name || "Dice Duel"));
      top.appendChild(who);
      ctx.root.appendChild(top);

      const board = el("div", "board");
      const youDie = el("div", "die", "?");
      const theirDie = el("div", "die", "?");
      const youSide = el("div", "side");
      youSide.appendChild(el("div", "name", "You"));
      youSide.appendChild(youDie);
      const theirSide = el("div", "side");
      theirSide.appendChild(el("div", "name", ctx.character.name || "Them"));
      theirSide.appendChild(theirDie);
      board.appendChild(youSide);
      board.appendChild(el("div", "versus", "vs"));
      board.appendChild(theirSide);
      ctx.root.appendChild(board);

      const tally = el("p", "tally", "");
      ctx.root.appendChild(tally);

      const rollBtn = el("button", "pill", "Roll");
      rollBtn.type = "button";
      rollBtn.addEventListener("click", roll);
      ctx.root.appendChild(rollBtn);

      function points(list) {
        let n = 0;
        list.forEach((v) => { n += v; });
        return n;
      }

      function draw() {
        youDie.textContent = st.mine.length ? String(st.mine[st.mine.length - 1]) : "?";
        theirDie.textContent = st.theirs.length ? String(st.theirs[st.theirs.length - 1]) : "?";
        let mineWins = 0;
        let theirWins = 0;
        for (let i = 0; i < st.mine.length; i++) {
          if (st.mine[i] > st.theirs[i]) mineWins++;
          else if (st.mine[i] < st.theirs[i]) theirWins++;
        }
        tally.textContent = "Round " + Math.min(st.round + 1, ROUNDS) + " of " + ROUNDS +
          " · you " + mineWins + " - " + theirWins + " " + (ctx.character.name || "them");
        rollBtn.disabled = st.done || busy;
        if (st.done) rollBtn.textContent = "Duel over";
      }

      function save() {
        ctx.save({ round: st.round, mine: st.mine, theirs: st.theirs, done: st.done });
      }

      function roll() {
        if (busy || st.done) return;
        busy = true;
        draw();
        let ticks = 0;
        const timer = setInterval(() => {
          youDie.textContent = String(1 + Math.floor(Math.random() * 6));
          theirDie.textContent = String(1 + Math.floor(Math.random() * 6));
          if (++ticks >= 8) {
            clearInterval(timer);
            settle();
          }
        }, 90);
      }

      function settle() {
        st.mine.push(1 + Math.floor(Math.random() * 6));
        st.theirs.push(1 + Math.floor(Math.random() * 6));
        st.round += 1;
        busy = false;
        save();
        draw();
        if (!greeted) {
          greeted = true;
          ctx.say("You just rolled dice against the player in a three-round dice duel. Say one short line in character.")
            .then(showLine);
        }
        if (st.round >= ROUNDS) {
          st.done = true;
          save();
          draw();
          const mine = points(st.mine);
          const theirs = points(st.theirs);
          if (mine > theirs) {
            tally.textContent = "You win the duel " + mine + " to " + theirs + ".";
            ctx.finish("win");
            ctx.say("You just lost a dice duel to the player. Say one short, in-character line about losing.")
              .then(showLine);
          } else if (mine < theirs) {
            tally.textContent = "The duel goes to " + (ctx.character.name || "them") + ", " + theirs + " to " + mine + ".";
            ctx.finish("loss");
            ctx.say("You just won a dice duel against the player. Say one short, in-character line about winning.")
              .then(showLine);
          } else {
            tally.textContent = "The duel is a draw at " + mine + " points each.";
            ctx.finish("draw");
            ctx.say("Your dice duel with the player ended in a draw. Say one short, in-character line.")
              .then(showLine);
          }
        }
      }

      function showLine(text) {
        if (typeof text !== "string" || !text) return;
        const p = el("p", "line", (ctx.character.name ? ctx.character.name + ": " : "") + text);
        ctx.root.insertBefore(p, ctx.root.children[1] || null);
      }

      draw();
      if (st.done) {
        const mine = points(st.mine);
        const theirs = points(st.theirs);
        tally.textContent = "Duel over, " + mine + " to " + theirs + ". Start a new one to roll again.";
      }
    },
  });
})();
