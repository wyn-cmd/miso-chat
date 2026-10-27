// Shared character cards: the gallery card and its info dialog, used by the home page and by account
// profiles. The page supplies the click target and the refresh after a favourite change.
const CARD_ART_MIN_MS = 5000;
const CARD_ART_SPREAD_MS = 3000;
const cardArtRuns = new Map();
const CARD_REDUCE_MOTION = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

function cardById(id) { return document.getElementById(id); }

function otherId(ids, current) {
  const pool = ids.length > 1 ? ids.filter((x) => x !== current) : ids;
  return pool[Math.floor(Math.random() * pool.length)];
}

function stopArt(container) {
  for (const stop of cardArtRuns.get(container) || []) stop();
  cardArtRuns.delete(container);
  for (const v of container.querySelectorAll("video")) v.pause();
}

function fillArtLayer(layer, name, id, meta, loop) {
  const info = meta[id] || {};
  const video = isVideoArt(id);
  const still = video ? posterFor(id) : null;
  const playVideo = video && !motionReduced();
  layer.textContent = "";
  layer.style.backgroundImage = "";
  layer.classList.toggle("video", playVideo);
  if (playVideo) {
    const el = artVideo(artUrl(name, id), still ? artUrl(name, still) : null, artFallbackUrl(name, id), loop);
    layer.appendChild(el);
    return { kind: "video", el: el };
  }
  if (!video && info.kind === "gif") {
    const el = new Image();
    el.className = "art-gif";
    el.alt = "";
    el.src = artUrl(name, id);
    layer.appendChild(el);
    return { kind: "gif", el: el, ms: info.duration_ms || 0 };
  }
  layer.style.backgroundImage = 'url("' + artUrl(name, still || id) + '")';
  return { kind: "still" };
}

function buildArt(host, name, ids, container, meta) {
  meta = meta || {};
  const stack = el("span", "", "art-stack");
  stack.setAttribute("aria-hidden", "true");
  const layers = [el("span", "", "art-layer"), el("span", "", "art-layer")];
  stack.append(...layers, el("span", "", "art-shade"));
  host.prepend(stack);

  const cycle = ids.length > 1 && !CARD_REDUCE_MOTION;
  let current = ids[Math.floor(Math.random() * ids.length)];
  let front = 0;
  let timer = null;
  let stopped = false;
  let cancelFinish = null;

  function cancelPending() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (cancelFinish) { const f = cancelFinish; cancelFinish = null; f(); }
  }

  function watch(filled) {
    cancelFinish = null;
    if (filled.kind === "video") {
      const v = filled.el;
      const done = () => { if (!stopped) advance(); };
      v.addEventListener("ended", done, { once: true });
      cancelFinish = () => { v.removeEventListener("ended", done); clearTimeout(timer); };
      timer = setTimeout(() => {
        timer = null;
        if (!stopped && (v.paused || v.currentTime === 0)) advance();
      }, CARD_ART_MIN_MS);
      return;
    }
    if (filled.kind === "gif" && filled.ms > 0) {
      timer = setTimeout(advance, filled.ms);
      return;
    }
    timer = setTimeout(advance, CARD_ART_MIN_MS + Math.random() * CARD_ART_SPREAD_MS);
  }

  function advance() {
    if (stopped || !stack.isConnected) return;
    if (document.hidden) { timer = setTimeout(advance, 1000); return; }
    cancelPending();
    const next = otherId(ids, current);
    if (isVideoArt(next) || (meta[next] || {}).kind === "gif") { swap(next); return; }
    const pre = new Image();
    pre.onload = () => { if (!stopped) swap(next); };
    pre.onerror = () => { if (!stopped) swap(next); };
    pre.src = artUrl(name, next);
  }

  function swap(next) {
    if (stopped) return;
    cancelPending();
    const back = layers[1 - front];
    const filled = fillArtLayer(back, name, next, meta, !cycle);
    back.classList.add("on");
    layers[front].classList.remove("on");
    setArtPlaying(layers[front], false);
    setArtPlaying(back, true);
    front = 1 - front;
    current = next;
    watch(filled);
  }

  const stop = () => { stopped = true; cancelPending(); };
  if (!cardArtRuns.has(container)) cardArtRuns.set(container, []);
  cardArtRuns.get(container).push(stop);

  const filled = fillArtLayer(layers[0], name, current, meta, !cycle);
  layers[0].classList.add("on");
  setArtPlaying(layers[0], true);
  if (cycle) watch(filled);
}

function creatorHandle(name, linkable) {
  const creator = name || "admin";
  if (linkable === false || creator.toLowerCase() === "admin") return el("span", "@" + creator, "home-creator");
  const a = el("a", "@" + creator, "home-creator creator-link");
  a.href = "/u/" + encodeURIComponent(creator);
  a.title = t("View {name}'s profile", { name: creator });
  a.addEventListener("click", (e) => e.stopPropagation());
  return a;
}

// opts.onFavourite runs after the save, so the page can reload its own list.
function renderCard(card, container, opts) {
  opts = opts || {};
  const afterFav = opts.onFavourite || (() => {});
  const wrap = el("div", "", "home-card");
  wrap.dataset.chatName = card.name;
  const artIds = card.backgrounds || [];
  if (artIds.length) {
    wrap.classList.add("has-art");
    buildArt(wrap, card.name, artIds, container, card.background_meta);
  } else {
    wrap.classList.add("has-field");
    wrap.appendChild(circleField(2));
  }

  const open = el("div", "", "home-open");
  open.setAttribute("role", "button");
  open.setAttribute("tabindex", "0");
  open.setAttribute("aria-label", t("Chat with {name}", { name: card.name }));
  open.addEventListener("click", () => openChat(card.name));
  open.addEventListener("keydown", (e) => {
    if (e.target !== open) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openChat(card.name); }
  });

  const avatar = el("span", "", "home-avatar");
  avatar.style.setProperty("--avatar-ring", card.color || "");
  if (card.has_icon) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(card.name);
    avatar.appendChild(img);
  } else {
    avatar.appendChild(faceIcon(card.color));
  }
  open.appendChild(avatar);

  const text = el("span", "", "home-text");
  const title = el("span", "", "home-title");
  title.appendChild(el("span", card.name, "home-name"));
  title.appendChild(creatorHandle(card.creator, card.creator_profile));
  if (card.popular) title.appendChild(el("span", t("popular"), "char-badge"));
  text.appendChild(title);
  if (card.has_profile) {
    text.appendChild(el("span", card.headline, "home-headline"));
  } else {
    text.appendChild(el("span", card.pending ? t("Profile is being written...") : t("No profile yet."), "home-headline muted"));
  }
  const messages = tn("{n} message", "{n} messages", card.messages || 0);
  text.appendChild(el("span", t("\u2605 {stars}  \u00b7  {messages}", { stars: card.stars || 0, messages: messages }), "home-stats"));
  const tags = el("span", "", "home-tags");
  for (const tag of card.tags || []) tags.appendChild(el("span", tag, "home-tag"));
  text.appendChild(tags);
  open.appendChild(text);
  wrap.appendChild(open);

  const star = favStar(card.name, !!card.favourite, async (on) => { await setFavourite(card.name, on); await afterFav(on); });
  star.classList.add("home-fav");
  wrap.appendChild(star);

  const info = el("button", "i", "home-info");
  info.type = "button";
  info.title = t("About {name}", { name: card.name });
  info.setAttribute("aria-label", t("About {name}", { name: card.name }));
  info.addEventListener("click", () => showInfo(card));
  wrap.appendChild(info);
  paintDot(wrap);

  return wrap;
}

function showInfo(card) {
  const dlg = cardById("infoDialog");
  if (!dlg) return;
  stopArt(dlg);
  dlg.querySelector(":scope > .art-stack")?.remove();
  const ids = (card.backgrounds || []).length ? card.backgrounds : lookDefaults(card.default_backgrounds);
  dlg.classList.toggle("has-art", ids.length > 0);
  if (ids.length) buildArt(dlg, card.name, ids, dlg, card.background_meta);
  dlg.addEventListener("close", () => stopArt(dlg), { once: true });
  cardById("infoTitle").textContent = card.name;
  cardById("infoHeadline").textContent = card.headline || "";
  const infoCreator = cardById("infoCreator");
  infoCreator.textContent = t("Created by") + " ";
  infoCreator.appendChild(creatorHandle(card.creator, card.creator_profile));
  const tags = cardById("infoTags");
  tags.textContent = "";
  for (const tag of card.tags || []) tags.appendChild(el("span", tag, "home-tag"));
  cardById("infoBody").textContent = card.info || (card.pending ? t("This character's profile is still being written.") : t("No profile yet. An admin can add one."));
  cardById("infoStart").onclick = () => openChat(card.name);
  dlg.showModal();
}

function openChat(name) {
  location.href = "/chat?c=" + encodeURIComponent(name);
}

const infoCloseBtn = cardById("infoClose");
if (infoCloseBtn) infoCloseBtn.addEventListener("click", () => cardById("infoDialog").close());
