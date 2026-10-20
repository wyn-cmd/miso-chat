// Home page: the character gallery. Opening a card starts a chat with that character on /chat.
const $ = (id) => document.getElementById(id);
const POLL_MS = 8000;      // how often to check for profiles still being written
const POLL_LIMIT = 12;     // stop after about a minute and a half
let polls = 0;
let allCards = [];          // every solo card from the last load; search and tag filters work on these
let allRecs = [];
const selectedTags = new Set();

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
  for (const name of data.solo || []) solo.appendChild(sidebarButton(name, data.colors || {}));
  for (const g of data.groups || []) groups.appendChild(sidebarButton(g.name, data.colors || {}));
  $("groupBox").hidden = !(data.groups || []).length;
}

function sidebarButton(name, colors) {
  const b = el("button", "", "char-item");
  b.type = "button";
  b.dataset.name = name;
  if (colors[name]) b.style.setProperty("--dot", colors[name]);
  b.appendChild(el("span", name, "char-name"));
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
  grid.textContent = "";
  for (const card of cards) grid.appendChild(renderCard(card));
}

// Suggestions from this account's chats. Each one reuses its gallery card plus the shared tags.
function renderRecommended(recs, cards) {
  const byName = new Map(cards.map((c) => [c.name, c]));
  const grid = $("recGrid");
  grid.textContent = "";
  for (const r of recs) {
    const card = byName.get(r.name);
    if (!card) continue;
    const wrap = renderCard(card);
    wrap.querySelector(".home-text").appendChild(el("span", "Shares tags: " + r.tags.join(", "), "home-reason"));
    grid.appendChild(wrap);
  }
  $("homeRecs").hidden = grid.childElementCount === 0;
}

function renderCard(card) {
  const wrap = el("div", "", "home-card");
  if (card.has_background) {
    wrap.classList.add("has-art");
    wrap.style.setProperty("--card-art", 'url("/api/background/' + encodeURIComponent(card.name) + '")');
  }

  const open = el("button", "", "home-open");
  open.type = "button";
  open.setAttribute("aria-label", "Chat with " + card.name);
  open.addEventListener("click", () => openChat(card.name));

  const avatar = el("span", "", "home-avatar");
  avatar.style.setProperty("--avatar-ring", card.color || "");
  if (card.has_icon) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(card.name);
    avatar.appendChild(img);
  } else {
    avatar.textContent = card.name.charAt(0).toUpperCase() || "?";
  }
  open.appendChild(avatar);

  const text = el("span", "", "home-text");
  const title = el("span", "", "home-title");
  title.appendChild(el("span", card.name, "home-name"));
  title.appendChild(el("span", "@" + (card.creator || "admin"), "home-creator"));
  text.appendChild(title);
  if (card.has_profile) {
    text.appendChild(el("span", card.headline, "home-headline"));
  } else {
    text.appendChild(el("span", card.pending ? "Profile is being written..." : "No profile yet.", "home-headline muted"));
  }
  const tags = el("span", "", "home-tags");
  for (const t of card.tags || []) tags.appendChild(el("span", t, "home-tag"));
  text.appendChild(tags);
  open.appendChild(text);
  wrap.appendChild(open);

  const info = el("button", "i", "home-info");
  info.type = "button";
  info.title = "About " + card.name;
  info.setAttribute("aria-label", "About " + card.name);
  info.addEventListener("click", () => showInfo(card));
  wrap.appendChild(info);

  return wrap;
}

function showInfo(card) {
  $("infoTitle").textContent = card.name;
  $("infoHeadline").textContent = card.headline || "";
  $("infoCreator").textContent = "Created by " + (card.creator || "admin");
  const tags = $("infoTags");
  tags.textContent = "";
  for (const t of card.tags || []) tags.appendChild(el("span", t, "home-tag"));
  $("infoBody").textContent = card.info || (card.pending ? "This character's profile is still being written." : "No profile yet. An admin can add one.");
  $("infoStart").onclick = () => openChat(card.name);
  $("infoDialog").showModal();
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
  return allCards.filter((c) => matchesSearch(c, terms) && [...selectedTags].every((t) => (c.tags || []).includes(t)));
}

function applyFilters() {
  const cards = visibleCards();
  renderCards(cards);
  renderRecommended(allRecs, cards);
  const filtering = $("homeSearch").value.trim() !== "" || selectedTags.size > 0;
  $("homeCount").textContent = filtering ? `Showing ${cards.length} of ${allCards.length} characters` : "";
  $("homeEmpty").hidden = allCards.length > 0;
  $("homeNoMatch").hidden = !(allCards.length > 0 && cards.length === 0);
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

$("homeSearch").addEventListener("input", applyFilters);
$("tagFilterBtn").addEventListener("click", () => {
  const open = $("tagPanel").hidden;
  $("tagPanel").hidden = !open;
  $("tagFilterBtn").setAttribute("aria-expanded", String(open));
});
$("tagClear").addEventListener("click", () => {
  selectedTags.clear();
  renderTagFilter();
  applyFilters();
});

