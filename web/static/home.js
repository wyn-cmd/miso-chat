// Home page: the character gallery. Opening a card starts a chat with that character on /chat.
const $ = (id) => document.getElementById(id);
const POLL_MS = 8000;      // how often to check for profiles still being written
const POLL_LIMIT = 12;     // stop after about a minute and a half
let polls = 0;
let favOnly = false;             // search shows only favourited characters when on
let allCards = [];          // every solo card from the last load; search and tag filters work on these
let allRecs = [];
const selectedTags = new Set();
const PAGE_SIZE = 50;        // search results shown at a time; Next and Previous move through the rest
let page = 1;

async function init() {
  let me;
  try {
    me = await api("GET", "/api/me");
  } catch (err) {
    $("homeErr").textContent = err.message;
    return;
  }
  // A new account must set its password first, and that happens on the chat page.
  if (me.must_change) { location.href = "/chat"; return; }
  $("who").textContent = me.username;
  renderSideProfile(me);
  if (me.avatar_shape) applyAvatarShape(me.avatar_shape);
  if (me.is_admin) $("adminLink").hidden = false;
  await loadCards();
}

async function loadCards() {
  let data;
  try {
    data = await api("GET", "/api/characters");
  } catch (err) {
    $("homeErr").textContent = err.message;
    return;
  }
  $("homeErr").textContent = "";
  renderSidebar(data);
  allCards = data.cards || [];
  allRecs = data.recommended || [];
  renderTagFilter();
  applyFilters();
  // Profiles are written in the background; check back while any are still pending.
  if (allCards.some((c) => c.pending) && polls < POLL_LIMIT) {
    polls += 1;
    setTimeout(loadCards, POLL_MS);
  }
}

// The same character list as the chat page; a click opens that character's chat.
function renderSidebar(data) {
  const solo = $("charList");
  const groups = $("groupList");
  solo.textContent = "";
  groups.textContent = "";
  // Only characters this account has chatted with; the search page below lists every character.
  const favs = new Set(data.favourites || []);
  const icons = new Set(data.icons || []);
  for (const name of data.sidebar || []) solo.appendChild(sidebarButton(name, data.colors || {}, favs, icons));
  for (const g of data.groups || []) groups.appendChild(sidebarButton(g.name, data.colors || {}, null, icons));
  $("groupBox").hidden = !(data.groups || []).length;
  paintUnread();
}

// The character's picture when it has one (the same picture as its home card), otherwise its colour face.
function sidebarIcon(name, colors, icons) {
  const icon = el("span", "", "char-icon" + (icons.has(name) ? "" : " placeholder"));
  if (icons.has(name)) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(name);
    img.loading = "lazy";
    icon.appendChild(img);
  } else {
    icon.appendChild(faceIcon(colors[name]));
  }
  return icon;
}

function sidebarButton(name, colors, favs, icons) {
  const b = el("button", "", "char-item");
  b.type = "button";
  b.dataset.name = name;
  b.dataset.chatName = name;
  if (colors[name]) b.style.setProperty("--dot", colors[name]);
  b.appendChild(sidebarIcon(name, colors, icons || new Set()));
  b.appendChild(el("span", name, "char-name"));
  if (favs) b.appendChild(favStar(name, favs.has(name), async (on) => { await setFavourite(name, on); await refreshAll(); }));
  b.addEventListener("click", () => openChat(name));
  return b;
}

function setSidebar(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").hidden = !open;
  $("menuBtn").setAttribute("aria-expanded", String(open));
}

function renderCards(cards) {
  const grid = $("homeGrid");
  stopArt(grid);
  grid.textContent = "";
  for (const card of cards) grid.appendChild(renderCard(card, grid));
}

// Suggestions from this account's chats. Each one reuses its gallery card plus the shared tags.
function renderRecommended(recs, cards) {
  const byName = new Map(cards.map((c) => [c.name, c]));
  const grid = $("recGrid");
  stopArt(grid);
  grid.textContent = "";
  for (const r of recs) {
    const card = byName.get(r.name);
    if (!card) continue;
    const wrap = renderCard(card, grid);
    wrap.querySelector(".home-text").appendChild(el("span", "Shares tags: " + r.tags.join(", "), "home-reason"));
    grid.appendChild(wrap);
  }
  $("homeRecs").hidden = grid.childElementCount === 0;
}

// The creator handle. The built-in admin account has no profile page, so its handle stays plain text.
function creatorHandle(name, linkable) {
  const creator = name || "admin";
  if (linkable === false || creator.toLowerCase() === "admin") return el("span", "@" + creator, "home-creator");
  const a = el("a", "@" + creator, "home-creator creator-link");
  a.href = "/u/" + encodeURIComponent(creator);
  a.title = "View " + creator + "'s profile";
  a.addEventListener("click", (e) => e.stopPropagation());
  return a;
}

function renderCard(card, container) {
  const wrap = el("div", "", "home-card");
  wrap.dataset.chatName = card.name;   // where the missed check-in dot goes
  // A card with no pictures of its own carries a field of circles instead of the shared defaults.
  const artIds = card.backgrounds || [];
  if (artIds.length) {
    wrap.classList.add("has-art");
    buildArt(wrap, card.name, artIds, container);
  } else {
    wrap.classList.add("has-field");
    wrap.appendChild(circleField(2));
  }

  const open = el("div", "", "home-open");
  open.setAttribute("role", "button");
  open.setAttribute("tabindex", "0");
  open.setAttribute("aria-label", "Chat with " + card.name);
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
  if (card.popular) title.appendChild(el("span", "popular", "char-badge"));
  text.appendChild(title);
  if (card.has_profile) {
    text.appendChild(el("span", card.headline, "home-headline"));
  } else {
    text.appendChild(el("span", card.pending ? "Profile is being written..." : "No profile yet.", "home-headline muted"));
  }
  text.appendChild(el("span", "\u2605 " + (card.stars || 0).toLocaleString() + "  \u00b7  " + (card.messages || 0).toLocaleString() + " messages", "home-stats"));
  const tags = el("span", "", "home-tags");
  for (const t of card.tags || []) tags.appendChild(el("span", t, "home-tag"));
  text.appendChild(tags);
  open.appendChild(text);
  wrap.appendChild(open);

  const star = favStar(card.name, !!card.favourite, async (on) => { await setFavourite(card.name, on); await refreshAll(); });
  star.classList.add("home-fav");
  wrap.appendChild(star);

  const info = el("button", "i", "home-info");
  info.type = "button";
  info.title = "About " + card.name;
  info.setAttribute("aria-label", "About " + card.name);
  info.addEventListener("click", () => showInfo(card));
  wrap.appendChild(info);
  paintDot(wrap);

  return wrap;
}

function showInfo(card) {
  // The character's backgrounds sit behind the dialog, darker at the bottom so the text reads.
  const dlg = $("infoDialog");
  stopArt(dlg);
  dlg.querySelector(":scope > .art-stack")?.remove();
  const ids = (card.backgrounds || []).length ? card.backgrounds : lookDefaults(card.default_backgrounds);
  dlg.classList.toggle("has-art", ids.length > 0);
  if (ids.length) buildArt(dlg, card.name, ids, dlg);
  dlg.addEventListener("close", () => stopArt(dlg), { once: true });
  $("infoTitle").textContent = card.name;
  $("infoHeadline").textContent = card.headline || "";
  const infoCreator = $("infoCreator");
  infoCreator.textContent = "Created by ";
  infoCreator.appendChild(creatorHandle(card.creator, card.creator_profile));
  const tags = $("infoTags");
  tags.textContent = "";
  for (const t of card.tags || []) tags.appendChild(el("span", t, "home-tag"));
  $("infoBody").textContent = card.info || (card.pending ? "This character's profile is still being written." : "No profile yet. An admin can add one.");
  $("infoStart").onclick = () => openChat(card.name);
  $("infoDialog").showModal();
}

// ---- Background art. Each card or dialog shows one of its backgrounds at random, then fades to another
// after 5 to 8 seconds. Reduced motion keeps the first random pick still.
const ART_MIN_MS = 5000;
const ART_SPREAD_MS = 3000;
const artRuns = new Map();   // container -> list of stop functions
const REDUCE_MOTION = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

function otherId(ids, current) {
  const pool = ids.length > 1 ? ids.filter((x) => x !== current) : ids;
  return pool[Math.floor(Math.random() * pool.length)];
}

function stopArt(container) {
  for (const stop of artRuns.get(container) || []) stop();
  artRuns.delete(container);
  for (const v of container.querySelectorAll("video")) v.pause();
}

// One layer holds either a picture or a video; re-using the layer is what makes the fade between two
// pieces of art work for both. When the reader asked for reduced motion a video is shown as its poster
// frame instead: the still is the point, so the video itself is never downloaded.
function fillArtLayer(layer, name, id) {
  const video = isVideoArt(id);
  const still = video ? posterFor(id) : null;
  const playVideo = video && !motionReduced();
  layer.textContent = "";
  layer.style.backgroundImage = "";
  layer.classList.toggle("video", playVideo);
  if (playVideo) {
    layer.appendChild(artVideo(artUrl(name, id), artUrl(name, still)));
  } else {
    layer.style.backgroundImage = 'url("' + artUrl(name, still || id) + '")';
  }
}

// Two image layers fade into each other, with a shade on top. The stack sits behind the text.
function buildArt(host, name, ids, container) {
  const stack = el("span", "", "art-stack");
  stack.setAttribute("aria-hidden", "true");
  const layers = [el("span", "", "art-layer"), el("span", "", "art-layer")];
  stack.append(...layers, el("span", "", "art-shade"));
  host.prepend(stack);

  let current = ids[Math.floor(Math.random() * ids.length)];
  let front = 0;
  fillArtLayer(layers[0], name, current);
  layers[0].classList.add("on");
  setArtPlaying(layers[0], true);
  if (ids.length < 2 || REDUCE_MOTION) return;

  let timer = null;
  let stopped = false;
  const swap = (next) => {
    const back = layers[1 - front];
    fillArtLayer(back, name, next);
    back.classList.add("on");
    layers[front].classList.remove("on");
    setArtPlaying(layers[front], false);
    setArtPlaying(back, true);
    front = 1 - front;
    current = next;
    schedule();
  };
  const schedule = () => { timer = setTimeout(step, ART_MIN_MS + Math.random() * ART_SPREAD_MS); };
  const step = () => {
    timer = null;
    if (stopped || !stack.isConnected) return;
    if (document.hidden) { schedule(); return; }
    const next = otherId(ids, current);
    if (isVideoArt(next)) { swap(next); return; }   // a video warms itself up; there is no image to preload
    const pre = new Image();
    pre.onload = () => { if (!stopped) swap(next); };
    pre.onerror = schedule;
    pre.src = artUrl(name, next);
  };
  schedule();
  const stop = () => { stopped = true; clearTimeout(timer); };
  if (!artRuns.has(container)) artRuns.set(container, []);
  artRuns.get(container).push(stop);
}

function openChat(name) {
  location.href = "/chat?c=" + encodeURIComponent(name);
}

$("infoClose").addEventListener("click", () => $("infoDialog").close());
$("menuBtn").addEventListener("click", () => setSidebar(!$("sidebar").classList.contains("open")));
$("scrim").addEventListener("click", () => setSidebar(false));
$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (err) { /* the session is gone either way */ }
  location.href = "/login";
});

init();
refreshUnread();
setInterval(refreshUnread, 60000);

// Lower-case, accent-free text, so "pokemon" finds "Pokémon".
function norm(s) {
  return String(s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function matchesSearch(card, terms) {
  const hay = norm([card.name, card.creator, card.headline, card.info].join("\n"));
  return terms.every((t) => hay.includes(t));
}

// A card must match every search word and carry every selected tag.
function visibleCards() {
  const terms = norm($("homeSearch").value).trim().split(/\s+/).filter(Boolean);
  return allCards.filter((c) => matchesSearch(c, terms) && [...selectedTags].every((t) => (c.tags || []).includes(t))
    && (!favOnly || !!c.favourite));
}

function applyFilters() {
  const cards = visibleCards();
  const pages = Math.max(1, Math.ceil(cards.length / PAGE_SIZE));
  if (page > pages) page = pages;
  const start = (page - 1) * PAGE_SIZE;
  renderCards(cards.slice(start, start + PAGE_SIZE));
  renderRecommended(allRecs, cards);
  const filtering = $("homeSearch").value.trim() !== "" || selectedTags.size > 0 || favOnly;
  $("homeCount").textContent = filtering ? `Showing ${cards.length} of ${allCards.length} characters` : "";
  $("homeEmpty").hidden = allCards.length > 0;
  $("homeNoMatch").hidden = !(allCards.length > 0 && cards.length === 0);
  renderPager(cards.length, pages, start);
}

// Shown only when the results need more than one page.
function renderPager(total, pages, start) {
  $("homePager").hidden = total <= PAGE_SIZE;
  $("pagePrev").disabled = page <= 1;
  $("pageNext").disabled = page >= pages;
  $("pageInfo").textContent = `Page ${page} of ${pages}. Showing ${start + 1} to ${Math.min(start + PAGE_SIZE, total)} of ${total}.`;
}

// Tags with counts, most common first. Selected tags sit above the rest, each group still most common first.
function renderTagFilter() {
  const counts = new Map();
  for (const c of allCards) for (const t of c.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
  for (const t of [...selectedTags]) if (!counts.has(t)) selectedTags.delete(t);
  const order = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));
  const pinned = order.filter((t) => selectedTags.has(t));
  const rest = order.filter((t) => !selectedTags.has(t));
  const box = $("tagChips");
  box.textContent = "";
  for (const t of [...pinned, ...rest]) {
    const b = el("button", `${t} \u00b7 ${counts.get(t)}`, "tag-filter");
    b.type = "button";
    b.setAttribute("aria-pressed", String(selectedTags.has(t)));
    b.addEventListener("click", () => {
      if (selectedTags.has(t)) selectedTags.delete(t); else selectedTags.add(t);
      page = 1;
      renderTagFilter();
      applyFilters();
    });
    box.appendChild(b);
  }
  $("tagPanelNote").textContent = selectedTags.size
    ? `${selectedTags.size} selected. A character must have all of them.`
    : "Pick tags to narrow the list.";
  $("tagClear").disabled = selectedTags.size === 0;
}

$("homeSearch").addEventListener("input", () => { page = 1; applyFilters(); });
$("favOnly").addEventListener("click", () => {
  favOnly = !favOnly;
  $("favOnly").setAttribute("aria-pressed", String(favOnly));
  $("favOnly").classList.toggle("on", favOnly);
  page = 1;
  applyFilters();
});

// Re-read the characters after a favourite changes, so the sidebar, cards and search all agree.
async function refreshAll() {
  const data = await api("GET", "/api/characters");
  allCards = data.cards || [];
  renderSidebar(data);
  applyFilters();
}
$("tagFilterBtn").addEventListener("click", () => {
  const open = $("tagPanel").hidden;
  $("tagPanel").hidden = !open;
  $("tagFilterBtn").setAttribute("aria-expanded", String(open));
});
$("tagClear").addEventListener("click", () => {
  selectedTags.clear();
  page = 1;
  renderTagFilter();
  applyFilters();
});
$("pagePrev").addEventListener("click", () => { page -= 1; applyFilters(); $("homeGrid").scrollIntoView({ block: "start" }); });
$("pageNext").addEventListener("click", () => { page += 1; applyFilters(); $("homeGrid").scrollIntoView({ block: "start" }); });
