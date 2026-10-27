// The maker page: a novel or a group chat is made here, the way a character is made on its own page. The kind comes
// from the address (/create/novel or /create/group). ?id= opens an existing one for editing. Every value goes into
// fields with .value or textContent, never innerHTML.
const $ = (id) => document.getElementById(id);
const KIND = location.pathname.includes("/novel") ? "novel" : "group";
const EDIT_ID = new URLSearchParams(location.search).get("id") || "";
const LIMITS = { novel: { min: 1, max: 8 }, group: { min: 2, max: 8 } };
const NOUN = { novel: "novel", group: "group chat" };
const WORLD_SECTIONS = ["locations", "magic", "rules", "civilisations", "factions", "technology", "other"];
let noteMax = 500;
let knownNotes = {};     // a group's saved notes, kept while the cast changes
let iconNames = new Set();   // characters with a picture of their own
let bgIds = {};          // character -> its background ids
let colorMap = {};       // character -> its colour
let worlds = [];         // the account's worlds, as the world list shows them

function fail(message) {
  $("loading").hidden = true;
  $("pageErr").textContent = message;
}

// The chosen characters, as display names. The server turns them into folder names.
function chosen() {
  return [...$("castPick").querySelectorAll("input:checked")].map((b) => b.value);
}

// Groups take a note per chosen character. Notes typed so far are kept when the list changes.
function renderNotes(known) {
  const typed = {};
  for (const ta of $("notes").querySelectorAll("textarea")) typed[ta.dataset.name] = ta.value;
  const box = $("notes");
  box.replaceChildren();
  const picked = chosen();
  $("notesBox").hidden = picked.length === 0;
  for (const name of picked) {
    const wrap = el("label", "", "group-note");
    wrap.appendChild(el("span", name, "group-note-name"));
    const ta = document.createElement("textarea");
    ta.dataset.name = name;
    ta.maxLength = noteMax;
    ta.rows = 2;
    ta.value = typed[name] !== undefined ? typed[name] : (known[name] || "");
    wrap.appendChild(ta);
    box.appendChild(wrap);
  }
}

// A character's picture, or its initial on its colour when it has none.
function castAvatar(name) {
  if (iconNames.has(name)) {
    const img = document.createElement("img");
    img.alt = "";
    img.className = "cast-avatar";
    img.src = "/api/icon/" + encodeURIComponent(name);
    return img;
  }
  const initial = el("span", name.trim().charAt(0).toUpperCase() || "?", "cast-avatar cast-initial");
  if (colorMap[name]) initial.style.background = colorMap[name];
  return initial;
}

// The cast as cards: each character's picture beside its name, on its own background picture. A card is chosen by
// clicking it; the check box inside it keeps the keyboard and the form working.
function drawCast(solo, selected, notes) {
  knownNotes = notes;
  const pick = $("castPick");
  pick.replaceChildren();
  $("castLegend").textContent = tr("Characters ({min} to {max})", { min: LIMITS[KIND].min, max: LIMITS[KIND].max });
  for (const name of solo) {
    const card = el("label", "", "cast-card");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = name;
    box.checked = selected.includes(name);
    box.className = "visually-hidden";
    const bg = (bgIds[name] || [])[0];
    if (bg) card.style.backgroundImage = `url("/api/background/${encodeURIComponent(name)}?b=${encodeURIComponent(bg)}")`;
    const row = el("span", "", "cast-row");
    row.appendChild(castAvatar(name));
    row.appendChild(el("span", name, "cast-name"));
    card.append(box, row);
    card.classList.toggle("on", box.checked);
    box.addEventListener("change", () => {
      card.classList.toggle("on", box.checked);
      if (KIND === "group") renderNotes(knownNotes);
    });
    pick.appendChild(card);
  }
  if (KIND === "group") renderNotes(notes);
}

function drawWorld(list, selected) {
  const sel = $("makerWorld");
  sel.replaceChildren();
  sel.appendChild(new Option(tr("Account default"), ""));
  sel.appendChild(new Option(tr("No world"), "none"));
  for (const w of list) sel.appendChild(new Option(w.name, w.id));
  sel.value = selected || "";
  if (sel.value !== (selected || "")) sel.value = "";
}

// A world made right here, without leaving the page. It is added to the choices and picked.
function openNewWorld() {
  $("worldNew").hidden = false;
  $("worldNewBtn").hidden = true;
  $("newWorldName").focus();
}

function closeNewWorld() {
  $("worldNew").hidden = true;
  $("worldNewBtn").hidden = false;
  $("newWorldMsg").textContent = "";
}

async function createWorld() {
  $("newWorldMsg").textContent = "";
  const sections = {};
  for (const key of WORLD_SECTIONS) {
    sections[key] = $("newWorld_" + key).value.split("\n").map((s) => s.trim()).filter(Boolean);
  }
  try {
    const item = await api("POST", "/api/worlds", {
      name: $("newWorldName").value,
      summary: $("newWorldSummary").value,
      sections,
    });
    worlds.push({ id: item.id, name: item.name });
    drawWorld(worlds, item.id);
    $("newWorldName").value = "";
    $("newWorldSummary").value = "";
    for (const key of WORLD_SECTIONS) $("newWorld_" + key).value = "";
    closeNewWorld();
  } catch (err) {
    $("newWorldMsg").textContent = err.message;
  }
}

// A group's picture or background, sent or removed, then shown as saved.
async function changeArt(kind, method, file) {
  const icon = kind === "icon";
  const msg = $(icon ? "iconMsg" : "bgMsg");
  const remove = $(icon ? "iconRemove" : "bgRemove");
  const input = $(icon ? "iconFile" : "bgFile");
  msg.textContent = method === "DELETE" ? tr("Removing...") : tr("Uploading...");
  try {
    const r = await fetch("/api/groups/" + encodeURIComponent(EDIT_ID) + "/" + kind, {
      method,
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream" },
      credentials: "same-origin",
      body: method === "POST" ? await file.arrayBuffer() : undefined,
    });
    if (!r.ok) {
      let data = {};
      try { data = await r.json(); } catch (e) { /* no body */ }
      throw apiError(r.status, data);
    }
    const data = await r.json();
    const has = icon ? data.has_icon : data.has_background;
    remove.hidden = !has;
    msg.textContent = has ? (icon ? tr("Picture saved.") : tr("Background saved.")) : (icon ? tr("Picture removed.") : tr("Background removed."));
    input.value = "";
  } catch (err) {
    msg.textContent = err.message;
  }
}

async function save(event) {
  event.preventDefault();
  $("makerMsg").textContent = "";
  const name = $("makerName").value;
  const members = chosen();
  try {
    let saved;
    if (KIND === "novel") {
      const body = { name, members, world: $("makerWorld").value || null };
      saved = EDIT_ID
        ? await api("PUT", "/api/novels/" + encodeURIComponent(EDIT_ID), body)
        : await api("POST", "/api/novels", body);
    } else {
      const notes = {};
      for (const ta of $("notes").querySelectorAll("textarea")) {
        const text = ta.value.trim();
        if (text) notes[ta.dataset.name] = text;
      }
      const body = { name, members, notes };
      saved = EDIT_ID
        ? await api("PUT", "/api/groups/" + encodeURIComponent(EDIT_ID), body)
        : await api("POST", "/api/groups", body);
    }
    location.href = "/chat?c=" + encodeURIComponent(saved.id);
  } catch (err) {
    $("makerMsg").textContent = err.message;
  }
}

async function remove() {
  if (!EDIT_ID) return;
  if (!confirm(tr("Delete this {noun}? Its messages, memory and notes are removed. The characters and worlds are kept.", { noun: tr(NOUN[KIND]) }))) return;
  const path = KIND === "novel" ? "/api/novels/" : "/api/groups/";
  try {
    await api("DELETE", path + encodeURIComponent(EDIT_ID));
    location.href = "/chat";
  } catch (err) {
    $("makerMsg").textContent = err.message;
  }
}

async function init() {
  let me;
  try {
    me = await api("GET", "/api/me");
  } catch (err) {
    fail(err.message);
    return;
  }
  if (me.must_change) { location.href = "/chat"; return; }
  $("who").textContent = me.username;
  if (me.is_admin) $("adminLink").hidden = false;

  const chars = await api("GET", "/api/characters");
  const solo = chars.solo || [];
  iconNames = new Set(chars.icons || []);
  bgIds = chars.bg_ids || {};
  colorMap = chars.colors || {};
  worlds = chars.worlds || [];

  let item = null;
  if (KIND === "novel") {
    const data = await api("GET", "/api/novels");
    item = EDIT_ID ? data.novels.find((n) => n.id === EDIT_ID) : null;
    if (EDIT_ID && !item) return fail(tr("That novel no longer exists."));
    drawWorld(worlds, item ? (item.world || "") : "");
    $("worldBox").hidden = false;
  } else {
    const data = await api("GET", "/api/groups");
    noteMax = data.note_max;
    item = EDIT_ID ? data.groups.find((g) => g.id === EDIT_ID) : null;
    if (EDIT_ID && !item) return fail(tr("That group no longer exists."));
    // A group's picture and background are kept on the group, so they can be set only once it exists.
    $("artBox").hidden = !item;
    if (item) {
      $("iconRemove").hidden = !item.has_icon;
      $("bgRemove").hidden = !item.has_background;
    }
  }

  const selected = item ? item.members.map((m) => m.name) : [];
  const notes = item && item.notes ? item.notes : {};
  const title = item ? tr("Edit {noun}", { noun: tr(NOUN[KIND]) }) : tr("New {noun}", { noun: tr(NOUN[KIND]) });
  document.title = title + " - Miso Chat";
  $("makerTitle").textContent = title;
  $("makerIntro").textContent = KIND === "novel"
    ? tr("A narrator tells the story with the characters you pick, in the world you choose. The novel has its own chat, memory and dreams.")
    : tr("A group chat is a set of characters that answer you together. Each one can have a note only you see.");
  $("makerName").value = item ? item.name : "";
  drawCast(solo, selected, notes);
  $("makerDelete").hidden = !item;
  $("makerForm").addEventListener("submit", save);
  $("makerDelete").addEventListener("click", remove);
  $("worldNewBtn").addEventListener("click", openNewWorld);
  $("newWorldCancel").addEventListener("click", closeNewWorld);
  $("newWorldSave").addEventListener("click", createWorld);
  $("iconFile").addEventListener("change", () => {
    const file = $("iconFile").files[0];
    if (file) changeArt("icon", "POST", file);
  });
  $("iconRemove").addEventListener("click", () => changeArt("icon", "DELETE"));
  $("bgFile").addEventListener("change", () => {
    const file = $("bgFile").files[0];
    if (file) changeArt("background", "POST", file);
  });
  $("bgRemove").addEventListener("click", () => changeArt("background", "DELETE"));
  $("loading").hidden = true;
  $("makerSection").hidden = false;
  $("makerName").focus();
}

init().catch((err) => fail(err.message));
