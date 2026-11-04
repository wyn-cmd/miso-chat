// miso-game: {"id": "high-low", "name": "High or Low", "description": "Guess whether the next card is higher or lower.", "author": "you", "version": "1.0.0"}
// High or Low - a small example game for Miso Chat.
//
// The first line above is required: it declares the game without the server ever running the file.
// Everything below runs inside a sandboxed frame with no cookies, no storage and no network access of
// its own. MisoGame.register hands you a ctx object, and that is your whole world:
//
//   ctx.root        an empty element to draw in
//   ctx.character   {name, icon, color, description}
//   ctx.mood        {energy, affinity, emotion, ...} or null when mood is off
//   ctx.attitude    {affection, level, trust, ...} or null when attitude is off
//   ctx.state       progress saved last time, or null on a first run
//   ctx.theme       "light" or "dark"
//   ctx.assets      files the admin attached to this game: {"card.png": "data:image/png;base64,..."}
//   ctx.asset(name) one of those files as a string, or null when there is no file by that name
//   ctx.save(obj)   store progress (plain JSON, under 8 KB, four levels deep at most)
//   ctx.say(text)   ask the character to say one short line about a moment; resolves to a string or null
//   ctx.finish(x)   end the game with "win", "loss" or "draw"; call it once
//                   (a second argument stores that state with the result, saved or not)
//
// A game with pictures, sounds or clips of its own has them attached as files by the admin; the
// page reads them and offers the set in ctx.assets, so the frame itself still makes no requests.
// Any image or video element can use one directly, e.g. img.src = ctx.asset("card.png") or
// img.src = ctx.assets["table.png"].
//
// Give the game a width in its register call, e.g. size: {w: 560}; the frame measures how tall the
// content is and sizes the window to it, so the game does not need to guess a height.
//
// Keep it self-contained: plain JavaScript, no libraries, no network calls. Inline event handlers are
// blocked by the page's security policy, so use addEventListener.

(function () {
  "use strict";

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  MisoGame.register({
    id: "high-low",
    name: "High or Low",
    start(ctx) {
      // Progress from an earlier visit. A finished game starts fresh.
      const old = ctx.state;
      const st = old && !old.done ? old : { round: 0, score: 0, card: 1 + Math.floor(Math.random() * 13), done: false };

      // A small header with whoever you are playing with.
      const head = el("p", null, "Playing with " + (ctx.character.name || "a friend"));
      head.style.margin = "0 0 10px";
      head.style.opacity = ".75";
      ctx.root.appendChild(head);

      const card = el("p", null, "");
      card.style.fontSize = "54px";
      card.style.margin = "10px 0";
      ctx.root.appendChild(card);

      const actions = el("p");
      const higher = el("button", null, "Higher");
      const lower = el("button", null, "Lower");
      higher.type = "button";
      lower.type = "button";
      actions.appendChild(higher);
      actions.appendChild(lower);
      ctx.root.appendChild(actions);

      const line = el("p", null, "");
      line.style.marginTop = "12px";
      line.style.opacity = ".8";
      ctx.root.appendChild(line);

      function paint() {
        card.textContent = "Card " + st.round + ": value " + st.card;
        higher.disabled = lower.disabled = st.done;
      }

      function ask(higherGuess) {
        if (st.done) return;
        const next = 1 + Math.floor(Math.random() * 13);
        st.round += 1;
        const right = higherGuess ? next > st.card : next < st.card;
        if (right) st.score += 1;
        line.textContent = "It came up " + next + (right ? " - correct." : " - wrong. Score " + st.score + ".");
        st.card = next;
        ctx.save({ round: st.round, score: st.score, card: st.card, done: st.done });
        if (st.round >= 5) {
          st.done = true;
          ctx.save({ round: st.round, score: st.score, card: st.card, done: true });
          if (st.score >= 3) {
            line.textContent += " You win the round of five with " + st.score + ".";
            ctx.finish("win");
          } else if (st.score <= 2) {
            line.textContent += " The round of five goes to " + (ctx.character.name || "them") + ".";
            ctx.finish("loss");
          }
          ctx.say("The player just finished a round of high or low card guesses with a score of " + st.score + " out of 5. Say one short line in character.")
            .then((text) => { if (text) line.textContent += " " + text; });
        }
        paint();
      }

      higher.addEventListener("click", () => ask(true));
      lower.addEventListener("click", () => ask(false));
      paint();
    },
  });
})();
