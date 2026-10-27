// Account profile page: description, picture, background, the characters the account made, and the
// characters it has liked when it shares them. Fetched from /api/account/<username>. The sidebar holds
// the viewer's own characters, the same list as the home page, so a chat is always one click away.
const $ = (id) => document.getElementById(id);
let colors = {};
let iconNames = new Set();

function mobileNav() { return window.matchMedia("(max-width: 760px)").matches; }

function setSidebar(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").hidden = !open;
  $("menuBtn").setAttribute("aria-expanded", String(open));
}

function sidebarIcon(name) {
  const box = el("span", "", "char-icon" + (iconNames.has(name) ? "" : " placeholder"));
  if (iconNames.has(name)) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(name);
    img.loading = "lazy";
    box.appendChild(img);
  } else {
    box.appendChild(faceIcon(colors[name]));
  }
  return box;
}

function sidebarButton(name) {
  const b = el("button", "", "char-item");
  b.type = "button";
  b.dataset.name = name;
  b.dataset.chatName = name;   // where the missed check-in dot goes
  if (colors[name]) b.style.setProperty("--dot", colors[name]);
  b.appendChild(sidebarIcon(name));
  b.appendChild(el("span", name, "char-name"));
  b.addEventListener("click", () => { location.href = "/chat?c=" + encodeURIComponent(name); });
  return b;
}

// The viewer's own characters and groups, drawn the way the home page draws them.
async function loadSidebar() {
  let data;
  try {
    data = await api("GET", "/api/characters");
  } catch (err) {
    $("sidebar").hidden = true;   // the page still works without the list
    $("menuBtn").hidden = true;
    return;
  }
  colors = data.colors || {};
  iconNames = new Set(data.icons || []);
  const list = $("charList");
  list.textContent = "";
  for (const name of data.sidebar || []) list.appendChild(sidebarButton(name));
  if (!(data.sidebar || []).length) {
    list.appendChild(el("p", "Characters you chat with appear here.", "muted"));
  }
  const groups = $("groupList");
  groups.textContent = "";
  for (const g of data.groups || []) groups.appendChild(sidebarButton(g.name));
  $("groupBox").hidden = !(data.groups || []).length;
  paintUnread();
}

function usernameFromPath() {
  const parts = location.pathname.split("/").filter(Boolean);
  try {
    return decodeURIComponent(parts[parts.length - 1] || "");
  } catch (err) {
    return "";
  }
}

async function init() {
  let me;
  try {
    me = await api("GET", "/api/me");
  } catch (err) {
    $("loading").hidden = true;
    $("profileErr").textContent = err.message;
    return;
  }
  if (me.must_change) { location.href = "/chat"; return; }
  $("who").textContent = me.username;
  renderSideProfile(me);
  if (me.is_admin) $("adminLink").hidden = false;
  if (me.avatar_shape) applyAvatarShape(me.avatar_shape);
  loadSidebar();

  const username = usernameFromPath();
  try {
    const data = await api("GET", "/api/account/" + encodeURIComponent(username));
    render(data);
  } catch (err) {
    $("loading").hidden = true;
    $("profileErr").textContent = err.message;
  }
}

function render(data) {
  $("loading").hidden = true;
  document.title = "@" + data.username + " - Miso Chat";

  const hero = $("profileCard");
  hero.hidden = false;
  if (data.has_background) {
    hero.classList.add("has-art");
    const base = "/api/account-background/" + encodeURIComponent(data.username) + "?v=" + data.background_version;
    const poster = "/api/account-background-poster/" + encodeURIComponent(data.username) + "?v=" + data.poster_version;
    const video = $("profileArtVideo");
    if (data.background_video && video && !motionReduced()) {
      // A video background loops behind the hero. Muted, or the browser refuses to start it.
      video.poster = poster;
      video.src = base;
      video.hidden = false;
      setArtPlaying(hero, true);
      hero.classList.add("has-video");
    } else {
      // A still background, or a video shown as its first frame when reduced motion was asked for.
      $("profileArt").style.backgroundImage =
        'url("' + (data.background_video ? poster : base) + '")';
    }
  }

  const avatar = $("profileAvatar");
  avatar.textContent = "";
  // The monogram is a case of its own: with no picture the box needs a tile, or a bright ring around an
  // empty box reads as a broken image. The class says so, since CSS cannot see whether an <img> arrived.
  hero.classList.toggle("no-avatar", !data.has_avatar);
  if (data.has_avatar) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/account-avatar/" + encodeURIComponent(data.username) + "?v=" + data.avatar_version;
    avatar.appendChild(img);
  } else {
    avatar.appendChild(el("span", (data.username.charAt(0) || "?").toUpperCase(), "profile-initial"));
  }

  $("profileName").textContent = "@" + data.username;
  const made = data.created_at ? new Date(data.created_at) : null;
  const count = data.characters.length;
  const meta = [];
  if (made && !Number.isNaN(made.getTime())) {
    meta.push("Member since " + made.toLocaleDateString([], { month: "long", year: "numeric" }));
  }
  meta.push(count + (count === 1 ? " character" : " characters"));
  $("profileMeta").textContent = meta.join(" \u00b7 ");
  $("profileBio").textContent = data.description || "No description yet.";
  $("profileBio").classList.toggle("muted", !data.description);
  $("profileEdit").hidden = !data.is_self;

  renderCharacters(data.characters, data.is_self);
  renderLiked(data);
}

function renderCharacters(cards, isSelf) {
  $("charactersSection").hidden = false;
  $("charactersTitle").textContent = isSelf ? "Your characters" : "Characters";
  const grid = $("characterGrid");
  grid.textContent = "";
  const empty = $("charactersEmpty");
  empty.hidden = cards.length > 0;
  empty.textContent = isSelf ? "You have not made any characters yet." : "This account has not made any characters yet.";
  for (const card of cards) grid.appendChild(characterCard(card));
}

function renderLiked(data) {
  $("likesSection").hidden = false;
  const grid = $("likesGrid");
  grid.textContent = "";
  if (data.liked === null) {
    $("likesNote").textContent = "This account keeps its liked characters private.";
    return;
  }
  if (!data.liked.length) {
    $("likesNote").textContent = data.is_self ? "You have not liked any characters yet." : "No liked characters yet.";
    return;
  }
  $("likesNote").textContent = data.is_self
    ? "Only you can see these unless you turn on sharing in Settings, Account."
    : "Characters this account has liked.";
  for (const card of data.liked) grid.appendChild(characterCard(card));
}

// A compact character card. Opening it starts or resumes that chat.
function characterCard(card) {
  const link = el("a", "", "profile-char");
  link.href = "/chat?c=" + encodeURIComponent(card.name);

  const avatar = el("span", "", "profile-char-avatar");
  avatar.style.setProperty("--avatar-ring", card.color || "");
  if (card.has_icon) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(card.name);
    img.loading = "lazy";
    avatar.appendChild(img);
  } else {
    avatar.appendChild(faceIcon(card.color));
  }
  link.appendChild(avatar);

  const body = el("span", "", "profile-char-body");
  body.appendChild(el("span", card.name, "profile-char-name"));
  if (card.has_profile) body.appendChild(el("span", card.headline, "profile-char-line"));
  else body.appendChild(el("span", card.pending ? "Profile is being written..." : "No profile yet.", "profile-char-line muted"));
  if ((card.tags || []).length) {
    const tags = el("span", "", "home-tags");
    for (const tag of card.tags) tags.appendChild(el("span", tag, "home-tag"));
    body.appendChild(tags);
  }
  link.appendChild(body);
  return link;
}

$("menuBtn").addEventListener("click", () => setSidebar(!$("sidebar").classList.contains("open")));
$("scrim").addEventListener("click", () => setSidebar(false));
$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (err) { /* the session is gone either way */ }
  location.href = "/login";
});

init();
refreshUnread();
