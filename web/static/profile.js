// Account profile page: description, picture, background, the characters the account made, and the
// characters it has liked when it shares them. Fetched from /api/account/<username>.
const $ = (id) => document.getElementById(id);

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
  if (me.is_admin) $("adminLink").hidden = false;
  if (me.avatar_shape) applyAvatarShape(me.avatar_shape);

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
    $("profileArt").style.backgroundImage =
      'url("/api/account-background/' + encodeURIComponent(data.username) + "?v=" + data.background_version + '")';
  }

  const avatar = $("profileAvatar");
  avatar.textContent = "";
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

$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (err) { /* the session is gone either way */ }
  location.href = "/login";
});

init();
