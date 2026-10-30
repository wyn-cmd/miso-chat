// Account profile page: description, picture, background, the characters the account made, and the
// characters it has liked when it shares them. Fetched from /api/account/<username>. Character cards use
// the same renderer as the home gallery. The edit button opens a dialog on this page; it no longer
// sends the reader to the chat settings.
const $ = (id) => document.getElementById(id);
const tr = (key, vars) => window.t(key, vars);
const trn = (one, other, n, vars) => window.tn(one, other, n, vars);
let colors = {};
let iconNames = new Set();
let me = null;
let account = null;

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
  b.dataset.chatName = name;
  if (colors[name]) b.style.setProperty("--dot", colors[name]);
  b.appendChild(sidebarIcon(name));
  b.appendChild(el("span", name, "char-name"));
  b.addEventListener("click", () => { location.href = "/chat?c=" + encodeURIComponent(name); });
  return b;
}

async function loadSidebar() {
  let data;
  try {
    data = await api("GET", "/api/characters");
  } catch (err) {
    $("sidebar").hidden = true;
    $("menuBtn").hidden = true;
    return;
  }
  colors = data.colors || {};
  iconNames = new Set(data.icons || []);
  const list = $("charList");
  list.textContent = "";
  for (const name of data.sidebar || []) list.appendChild(sidebarButton(name));
  if (!(data.sidebar || []).length) {
    list.appendChild(el("p", tr("Characters you chat with appear here."), "muted"));
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
  await loadAccount();
}

async function loadAccount() {
  const username = usernameFromPath();
  try {
    const data = await api("GET", "/api/account/" + encodeURIComponent(username));
    account = data;
    if (data.is_self) {
      me.profile = Object.assign({}, me.profile, {
        description: data.description,
        likes_visible: data.likes_visible,
        has_avatar: data.has_avatar,
        has_background: data.has_background,
        background_video: data.background_video,
        avatar_version: data.avatar_version,
        background_version: data.background_version,
        poster_version: data.poster_version,
      });
    }
    render(data);
  } catch (err) {
    $("loading").hidden = true;
    $("profileErr").textContent = err.message;
  }
}

function resetHeroArt(hero) {
  const video = $("profileArtVideo");
  if (video) {
    video.pause();
    for (const source of [...video.querySelectorAll("source")]) source.remove();
    video.removeAttribute("src");
    video.removeAttribute("poster");
    video.hidden = true;
    video.load();
  }
  $("profileArt").style.backgroundImage = "";
  hero.classList.remove("has-art", "has-video");
}

function render(data) {
  $("loading").hidden = true;
  $("profileErr").textContent = "";
  document.title = "@" + data.username + " - Miso Chat";
  // The account's theme music plays while its page is open, for whoever is viewing it.
  themeMusic.setPage("acct:" + data.username, data.music || []);

  const hero = $("profileCard");
  hero.hidden = false;
  resetHeroArt(hero);
  if (data.has_background) {
    hero.classList.add("has-art");
    const base = "/api/account-background/" + encodeURIComponent(data.username) + "?v=" + data.background_version;
    const poster = "/api/account-background-poster/" + encodeURIComponent(data.username) + "?v=" + data.poster_version;
    const video = $("profileArtVideo");
    if (data.background_video && video && !motionReduced()) {
      video.poster = poster;
      setArtSources(video, base, base + "&fmt=mp4");
      video.hidden = false;
      setArtPlaying(hero, true);
      hero.classList.add("has-video");
    } else {
      $("profileArt").style.backgroundImage =
        'url("' + (data.background_video ? poster : base) + '")';
    }
  }

  const avatar = $("profileAvatar");
  avatar.textContent = "";
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
    meta.push(tr("Member since {date}", { date: MISO_I18N.date(made, { month: "long", year: "numeric" }) }));
  }
  meta.push(trn("{n} character", "{n} characters", count));
  $("profileMeta").textContent = meta.join(" \u00b7 ");
  $("profileBio").textContent = data.description || tr("No description yet.");
  $("profileBio").classList.toggle("muted", !data.description);
  $("profileEdit").hidden = !data.is_self;

  renderCharacters(data.characters, data.is_self);
  renderLiked(data);
  if ($("profileEditDialog").open) renderEditForm();
}

function renderCharacters(cards, isSelf) {
  $("charactersSection").hidden = false;
  $("charactersTitle").textContent = isSelf ? tr("Your characters") : tr("Characters");
  const grid = $("characterGrid");
  stopArt(grid);
  grid.textContent = "";
  const empty = $("charactersEmpty");
  empty.hidden = cards.length > 0;
  empty.textContent = isSelf ? tr("You have not made any characters yet.") : tr("This account has not made any characters yet.");
  for (const card of cards) grid.appendChild(renderCard(card, grid, { onFavourite: refreshAccount }));
}

function renderLiked(data) {
  $("likesSection").hidden = false;
  const grid = $("likesGrid");
  stopArt(grid);
  grid.textContent = "";
  if (data.liked === null) {
    $("likesNote").textContent = tr("This account keeps its liked characters private.");
    return;
  }
  if (!data.liked.length) {
    $("likesNote").textContent = data.is_self ? tr("You have not liked any characters yet.") : tr("No liked characters yet.");
    return;
  }
  $("likesNote").textContent = data.is_self
    ? (data.likes_visible
      ? tr("Liked characters are visible on your profile.")
      : tr("Only you can see these unless you turn on sharing in Settings, Account."))
    : tr("Characters this account has liked.");
  for (const card of data.liked) grid.appendChild(renderCard(card, grid, { onFavourite: refreshAccount }));
}

async function refreshAccount() {
  try { await loadAccount(); } catch (err) { /* the page keeps what it has */ }
}

// ---- Profile editor. This dialog belongs to the profile page; it does not open the chat settings.
function openProfileEditor() {
  if (!me || !me.profile) return;
  renderEditForm();
  const dlg = $("profileEditDialog");
  if (!dlg.open) dlg.showModal();
  $("peBio").focus();
}

function renderEditForm() {
  const p = me && me.profile;
  if (!p) return;
  const bio = $("peBio");
  if (document.activeElement !== bio) bio.value = p.description || "";
  updateEditCount();
  const avatar = $("peAvatar");
  avatar.hidden = !p.has_avatar;
  if (p.has_avatar) avatar.src = "/api/account-avatar/" + encodeURIComponent(me.username) + "?v=" + (p.avatar_version || 0);
  $("pePicRemove").hidden = !p.has_avatar;
  const background = $("peBg");
  background.hidden = !p.has_background;
  if (p.has_background) {
    background.src = p.background_video
      ? "/api/account-background-poster/" + encodeURIComponent(me.username) + "?v=" + (p.poster_version || 0)
      : "/api/account-background/" + encodeURIComponent(me.username) + "?v=" + (p.background_version || 0);
  }
  $("peBgRemove").hidden = !p.has_background;
  $("peLikes").checked = !!p.likes_visible;
  renderAccountMusic(account ? account.music || [] : []);
}

// ---- Theme music on this account's page: up to three tracks. The owner adds, orders and removes them here.
const ACCOUNT_MUSIC_MAX = 3;
const ACCOUNT_MUSIC_UPLOAD_MAX = 60 * 1024 * 1024;

function renderAccountMusic(items) {
  themeMusic.renderList($("peMusicList"), items, { onMove: moveAccountMusic, onRemove: removeAccountMusic });
  $("peMusicNote").textContent = tr("{n} of {max} tracks.", { n: items.length, max: ACCOUNT_MUSIC_MAX });
  $("peMusicFile").disabled = items.length >= ACCOUNT_MUSIC_MAX;
  $("peMusicBox").hidden = !(account && account.is_self);
}

// Reloads the account and redraws the page and the track list after a change to the tracks.
async function afterAccountMusicChange() {
  await refreshAccount();
  renderAccountMusic(account ? account.music || [] : []);
}

async function uploadAccountMusic(file) {
  if (!file) return;
  $("peMusicErr").textContent = "";
  if (file.size > ACCOUNT_MUSIC_UPLOAD_MAX) {
    $("peMusicErr").textContent = tr("A track can be up to 60 MB.");
    return;
  }
  try {
    await themeMusic.upload("/api/account/music", file, file.name.replace(/\.[^.]+$/, ""));
  } catch (err) {
    $("peMusicErr").textContent = err.message;
  }
  await afterAccountMusicChange();
}

async function moveAccountMusic(id, direction) {
  const ids = (account.music || []).map((t) => t.id);
  const at = ids.indexOf(id);
  const to = at + direction;
  if (to < 0 || to >= ids.length) return;
  [ids[at], ids[to]] = [ids[to], ids[at]];
  $("peMusicErr").textContent = "";
  try {
    await api("PUT", "/api/account/music/order", { ids: ids });
  } catch (err) {
    $("peMusicErr").textContent = err.message;
  }
  await afterAccountMusicChange();
}

async function removeAccountMusic(id) {
  $("peMusicErr").textContent = "";
  try {
    await api("DELETE", "/api/account/music/" + encodeURIComponent(id));
  } catch (err) {
    $("peMusicErr").textContent = err.message;
  }
  await afterAccountMusicChange();
}

function updateEditCount() {
  $("peCount").textContent = $("peBio").value.length + " / 1000";
}

async function saveEditDescription() {
  $("peMsg").textContent = "";
  try {
    me.profile = await api("POST", "/api/account/profile", { description: $("peBio").value });
    renderEditForm();
    $("peMsg").textContent = tr("Description saved.");
    await refreshAccount();
  } catch (err) {
    $("peMsg").textContent = err.message;
  }
}

async function saveEditLikes() {
  $("peMsg").textContent = "";
  try {
    me.profile = await api("POST", "/api/account/profile", { likes_visible: $("peLikes").checked });
    renderEditForm();
    $("peMsg").textContent = me.profile.likes_visible
      ? tr("Liked characters are visible on your profile.")
      : tr("Liked characters are private.");
    await refreshAccount();
  } catch (err) {
    $("peMsg").textContent = err.message;
    renderEditForm();
  }
}

async function uploadEditImage(kind, file) {
  if (!file) return;
  $("peMsg").textContent = tr("Uploading...");
  try {
    const r = await fetch("/api/account/" + kind, {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream" },
      credentials: "same-origin",
      body: await file.arrayBuffer(),
    });
    if (!r.ok) {
      let data = {};
      try { data = await r.json(); } catch (e) { /* no body */ }
      throw apiError(r.status, data);
    }
    me.profile = await r.json();
    renderEditForm();
    $("peMsg").textContent = kind === "avatar" ? tr("Profile picture saved.") : tr("Profile background saved.");
    await refreshAccount();
  } catch (err) {
    $("peMsg").textContent = err.message;
  }
}

async function removeEditImage(kind) {
  const what = kind === "avatar" ? tr("profile picture") : tr("profile background");
  if (!confirm(tr("Remove your {what}?", { what: what }))) return;
  $("peMsg").textContent = "";
  try {
    me.profile = await api("DELETE", "/api/account/" + kind);
    renderEditForm();
    $("peMsg").textContent = tr("Removed.");
    await refreshAccount();
  } catch (err) {
    $("peMsg").textContent = err.message;
  }
}

$("menuBtn").addEventListener("click", () => setSidebar(!$("sidebar").classList.contains("open")));
$("scrim").addEventListener("click", () => setSidebar(false));
$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (err) { /* the session is gone either way */ }
  location.href = "/login";
});

$("profileEdit").addEventListener("click", openProfileEditor);
$("peClose").addEventListener("click", () => $("profileEditDialog").close());
$("peBio").addEventListener("input", updateEditCount);
$("peSave").addEventListener("click", saveEditDescription);
$("peLikes").addEventListener("change", saveEditLikes);
$("pePic").addEventListener("change", (e) => { const file = e.target.files[0]; e.target.value = ""; uploadEditImage("avatar", file); });
$("peBgPick").addEventListener("change", (e) => { const file = e.target.files[0]; e.target.value = ""; uploadEditImage("background", file); });
$("pePicRemove").addEventListener("click", () => removeEditImage("avatar"));
$("peBgRemove").addEventListener("click", () => removeEditImage("background"));
$("peMusicFile").addEventListener("change", (e) => { const file = e.target.files[0]; e.target.value = ""; uploadAccountMusic(file); });

init();
refreshUnread();
