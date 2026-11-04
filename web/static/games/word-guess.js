// Word Guess - a built-in mini-game. Guess the hidden word one letter at a time; six misses allowed.
// Runs inside the sandboxed game frame and talks to the app only through the MisoGame bridge.

(function () {
  "use strict";

  const WORDS = ["amber", "bridge", "candle", "drift", "ember", "feather", "garden", "hollow", "island",
    "jasmine", "lantern", "meadow", "nebula", "orchard", "pebble", "quartz", "ripple", "shadow",
    "thunder", "velvet", "willow", "yonder", "zephyr", "compass", "harbor", "mirage"];
  const MAX_MISSES = 6;
  const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

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
    ".word{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0;font-size:30px;font-weight:600;letter-spacing:2px;}" +
    ".word span{min-width:26px;text-align:center;border-bottom:2px solid rgba(140,160,200,.5);}" +
    ".keys{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));gap:6px;max-width:560px;}" +
    ".keys button{padding:9px 0;border-radius:8px;border:1px solid rgba(140,160,200,.35);background:transparent;" +
    "color:inherit;font:inherit;font-weight:600;cursor:pointer;}" +
    ".keys button:hover:not(:disabled){background:rgba(120,140,180,.16);}" +
    ".keys button.hit{border-color:#4ade80;color:#4ade80;}" +
    ".keys button.miss{border-color:#f87171;color:#f87171;}" +
    ".keys button:disabled{cursor:default;opacity:.55;}"
  );

  MisoGame.register({
    id: "word-guess",
    name: "Word Guess",
    size: { w: 588 },
    start(ctx) {
      const old = ctx.state;
      const st = old && !old.done && typeof old.word === "string" && old.word
        ? old : { word: WORDS[Math.floor(Math.random() * WORDS.length)], guessed: [], misses: 0, done: false };

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
      who.appendChild(el("div", "who", ctx.character.name || "Word Guess"));
      const status = el("div", "sub", "");
      who.appendChild(status);
      top.appendChild(who);
      ctx.root.appendChild(top);

      const word = el("div", "word");
      ctx.root.appendChild(word);

      const keys = el("div", "keys");
      ctx.root.appendChild(keys);
      const buttons = {};
      for (const letter of LETTERS) {
        const b = el("button", null, letter);
        b.type = "button";
        b.addEventListener("click", () => guess(letter.toLowerCase()));
        buttons[letter] = b;
        keys.appendChild(b);
      }

      function guessed() {
        return new Set(st.guessed);
      }

      function draw() {
        word.textContent = "";
        const have = guessed();
        let done = true;
        for (const ch of st.word) {
          const shown = have.has(ch);
          if (!shown) done = false;
          word.appendChild(el("span", null, shown ? ch : ""));
        }
        for (const letter of LETTERS) {
          const b = buttons[letter];
          const lc = letter.toLowerCase();
          b.disabled = st.done || have.has(lc);
          b.className = have.has(lc) ? (st.word.indexOf(lc) !== -1 ? "hit" : "miss") : "";
        }
        status.textContent = st.done ? "Game over." : "Misses " + st.misses + " of " + MAX_MISSES + " · guess a letter";
        return done;
      }

      function save() {
        ctx.save({ word: st.word, guessed: st.guessed, misses: st.misses, done: st.done });
      }

      function finishIfOver() {
        const have = guessed();
        const solved = st.word.split("").every((ch) => have.has(ch));
        if (solved && !st.done) {
          st.done = true;
          save();
          draw();
          status.textContent = "You found it in " + (st.guessed.length) + " guesses.";
          ctx.finish("win");
          ctx.say("The player just solved the hidden word in a word game and won. Say one short line in character.")
            .then(showLine);
          return true;
        }
        if (st.misses >= MAX_MISSES && !st.done) {
          st.done = true;
          save();
          draw();
          status.textContent = "Out of misses. The word was " + st.word + ".";
          ctx.finish("loss");
          return true;
        }
        return false;
      }

      function guess(ch) {
        if (st.done || guessed().has(ch)) return;
        st.guessed.push(ch);
        const hit = st.word.indexOf(ch) !== -1;
        if (!hit) st.misses += 1;
        save();
        draw();
        if (finishIfOver()) return;
        if (hit && !greeted) {
          greeted = true;
          ctx.say("The player just guessed a correct letter in a word game. Say a short, in-character line about it.")
            .then(showLine);
        }
      }

      function showLine(text) {
        if (typeof text !== "string" || !text) return;
        const p = el("p", "line", (ctx.character.name ? ctx.character.name + ": " : "") + text);
        ctx.root.insertBefore(p, ctx.root.children[1] || null);
      }

      draw();
    },
  });
})();
