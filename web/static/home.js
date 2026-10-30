// Home page: the character gallery. Opening a card starts a chat with that character on /chat.
const $ = (id) => document.getElementById(id);
const POLL_MS = 8000;      // how often to check for profiles still being written
const POLL_LIMIT = 12;     // stop after about a minute and a half
let polls = 0;
let favOnly = false;             // search shows only favourited characters when on
let allCards = [];          // every solo card from the last load; search and tag filters work on these
let allRecs = [];
let relTypeFilter = "";
let relTrustFilter = "";
let relAffectionFilter = "";
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
  renderRelationshipFilters();
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
  for (const card of cards) grid.appendChild(renderCard(card, grid, { onFavourite: refreshAll }));
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
    const wrap = renderCard(card, grid, { onFavourite: refreshAll });
    wrap.querySelector(".home-text").appendChild(el("span", t("Shares tags: {tags}", { tags: r.tags.join(", ") }), "home-reason"));
    grid.appendChild(wrap);
  }
  $("homeRecs").hidden = grid.childElementCount === 0;
}

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

// A card must match every search word, carry every selected tag, and match the relationship filters.
function visibleCards() {
  const terms = norm($("homeSearch").value).trim().split(/\s+/).filter(Boolean);
  return allCards.filter((c) => {
    if (!matchesSearch(c, terms)) return false;
    if (![...selectedTags].every((t) => (c.tags || []).includes(t))) return false;
    if (favOnly && !c.favourite) return false;
    const rel = c.relationship || {};
    if (relTypeFilter && rel.type !== relTypeFilter) return false;
    if (relTrustFilter && rel.trust !== relTrustFilter) return false;
    if (relAffectionFilter && rel.affection !== relAffectionFilter) return false;
    return true;
  });
}

function applyFilters() {
  const cards = visibleCards();
  const pages = Math.max(1, Math.ceil(cards.length / PAGE_SIZE));
  if (page > pages) page = pages;
  const start = (page - 1) * PAGE_SIZE;
  renderCards(cards.slice(start, start + PAGE_SIZE));
  renderRecommended(allRecs, cards);
  const filtering = $("homeSearch").value.trim() !== "" || selectedTags.size > 0 || favOnly
    || !!relTypeFilter || !!relTrustFilter || !!relAffectionFilter;
  $("homeCount").textContent = filtering ? t("Showing {shown} of {total} characters", { shown: cards.length, total: allCards.length }) : "";
  $("homeEmpty").hidden = allCards.length > 0;
  $("homeNoMatch").hidden = !(allCards.length > 0 && cards.length === 0);
  renderPager(cards.length, pages, start);
}

// Shown only when the results need more than one page.
function renderPager(total, pages, start) {
  $("homePager").hidden = total <= PAGE_SIZE;
  $("pagePrev").disabled = page <= 1;
  $("pageNext").disabled = page >= pages;
  $("pageInfo").textContent = t("Page {page} of {pages}. Showing {from} to {to} of {total}.", {
    page: page, pages: pages, from: start + 1, to: Math.min(start + PAGE_SIZE, total), total: total,
  });
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
  const relActive = !!(relTypeFilter || relTrustFilter || relAffectionFilter);
  $("tagPanelNote").textContent = selectedTags.size
    ? t("{n} selected. A character must have all of them.", { n: selectedTags.size })
    : relActive ? t("Relationship filters applied.") : t("Pick tags or relationship filters to narrow the list.");
  $("tagClear").disabled = selectedTags.size === 0 && !relActive;
}

// Relationship filters: the type list follows the cards on this page; trust and affection use their categories.
function renderRelationshipFilters() {
  const counts = new Map();
  for (const c of allCards) {
    const type = c.relationship && c.relationship.type;
    if (type) counts.set(type, (counts.get(type) || 0) + 1);
  }
  if (relTypeFilter && !counts.has(relTypeFilter)) relTypeFilter = "";
  const sel = $("relTypeFilter");
  sel.textContent = "";
  const all = el("option", t("All types"), null);
  all.value = "";
  sel.appendChild(all);
  for (const type of [...counts.keys()].sort((a, b) => a.localeCompare(b))) {
    const opt = el("option", `${t(type)} \u00b7 ${counts.get(type)}`, null);
    opt.value = type;
    sel.appendChild(opt);
  }
  sel.value = relTypeFilter;
  $("relTrustFilter").value = relTrustFilter;
  $("relAffectionFilter").value = relAffectionFilter;
}

$("relTypeFilter").addEventListener("change", (e) => { relTypeFilter = e.target.value; page = 1; renderTagFilter(); applyFilters(); });
$("relTrustFilter").addEventListener("change", (e) => { relTrustFilter = e.target.value; page = 1; renderTagFilter(); applyFilters(); });
$("relAffectionFilter").addEventListener("change", (e) => { relAffectionFilter = e.target.value; page = 1; renderTagFilter(); applyFilters(); });

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
  renderRelationshipFilters();
  applyFilters();
}
$("tagFilterBtn").addEventListener("click", () => {
  const open = $("tagPanel").hidden;
  $("tagPanel").hidden = !open;
  $("tagFilterBtn").setAttribute("aria-expanded", String(open));
});
$("tagClear").addEventListener("click", () => {
  selectedTags.clear();
  relTypeFilter = "";
  relTrustFilter = "";
  relAffectionFilter = "";
  page = 1;
  renderTagFilter();
  renderRelationshipFilters();
  applyFilters();
});
$("pagePrev").addEventListener("click", () => { page -= 1; applyFilters(); $("homeGrid").scrollIntoView({ block: "start" }); });
$("pageNext").addEventListener("click", () => { page += 1; applyFilters(); $("homeGrid").scrollIntoView({ block: "start" }); });
