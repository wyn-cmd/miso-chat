// Character editor (admin): create and edit character cards, colours, context and icons.
// Every value goes into fields with .value or textContent, never innerHTML.
let editing = null;      // name of the character being edited, or null for a new one
let loadedColour = ""; // colour as it was loaded (may be a name such as 'Crimson')

function showEditor(show) {
  $("editorSection").hidden = !show;
  $("chatSection").hidden = show;
  $("memSection").hidden = true;
  $("pwSection").hidden = true;
}

function syncColour(from) {
  const hex = from === "text" ? $("edColourText").value.trim() : $("edColour").value;
  const ok = /^#[0-9a-fA-F]{6}$/.test(hex);
  if (from === "text" && ok) $("edColour").value = hex.toLowerCase();
  if (from === "picker") $("edColourText").value = hex.toLowerCase();
  if (from === "text") $("edColourText").classList.toggle("invalid", !ok);
}

async function openEditor(name) {
  editing = name;
  $("edErr").textContent = "";
  $("edOk").textContent = "";
  $("edIconErr").textContent = "";
  $("edIconSave").disabled = true;
  $("edImportBox").hidden = !!name;   // importing only creates new characters
  $("edBgBox").hidden = !name;
  $("edExportBox").hidden = !name;
  $("edDraftBox").hidden = !!name;   // drafting only fills the new-character form
  $("edPalaceBox").hidden = !name;   // the Mind Palace is saved beside the character, so it needs one
  if (name) loadPalace(name);
  else {
    palaceEntries = [];
    renderPalace();
  }
  $("edBgErr").textContent = "";
  $("edBgSave").disabled = true;
  $("edExportErr").textContent = "";
  if (name) {
    $("editorTitle").textContent = "Edit " + name;
    const data = await api("GET", "/api/admin/characters/" + encodeURIComponent(name));
    $("edName").value = data.name;
    $("edName").readOnly = true;
    $("edCard").value = data.card;
    $("edContext").value = data.context;
    loadedColour = data.colour || "";
    $("edColourText").value = loadedColour;
    $("edColour").value = /^#[0-9a-fA-F]{6}$/.test(data.colour || "") ? data.colour.toLowerCase() : "#808080";
    $("edSandbox").checked = !!data.sandbox;
    setProfileFields(data.profile);
    setIconPreview(name, data.has_icon);
    setBgPreview(name);
  } else {
    $("editorTitle").textContent = "New character";
    $("edName").value = "";
    $("edName").readOnly = false;
    $("edCard").value = "";
    $("edContext").value = "";
    $("edColour").value = "#808080";
    $("edColourText").value = "#808080";
    $("edSandbox").checked = false;
    setProfileFields(null);
    setIconPreview(null, false);
  }
  $("edColourText").classList.remove("invalid");
  showEditor(true);
}

function setIconPreview(name, hasIcon) {
  const box = $("edIconPreview");
  box.textContent = "";
  box.classList.toggle("placeholder", !hasIcon);
  if (hasIcon && name) {
    const img = document.createElement("img");
    img.alt = "";
    img.src = "/api/icon/" + encodeURIComponent(name) + "?v=" + Date.now();
    box.appendChild(img);
  } else {
    box.textContent = ($("edName").value || "?").trim().charAt(0).toUpperCase() || "?";
  }
}

async function loadExample() {
  const data = await api("GET", "/api/admin/characters/example");
  $("edCard").value = data.card;
  const m = /^Colour:\s*(#[0-9a-fA-F]{6})/m.exec(data.card);
  if (m) {
    $("edColourText").value = m[1];
    $("edColour").value = m[1];
  }
  $("edErr").textContent = "";
}

async function saveCharacter(ev) {
  ev.preventDefault();
  $("edErr").textContent = "";
  $("edOk").textContent = "";
  const typed = $("edColourText").value.trim();
  if (editing && typed !== loadedColour && !/^#[0-9a-fA-F]{6}$/.test(typed)) {
    $("edErr").textContent = "Colour must be a hex code such as #c0392b.";
    return;
  }
  const body = {
    card: $("edCard").value,
    context: $("edContext").value,
    colour: typed || $("edColour").value,
    sandbox: $("edSandbox").checked,
    profile: profileFromForm(),
  };
  try {
    let saved;
    if (editing) {
      saved = await api("PUT", "/api/admin/characters/" + encodeURIComponent(editing), body);
    } else {
      body.name = $("edName").value.trim();
      saved = await api("POST", "/api/admin/characters", body);
      editing = saved.name;
    }
    $("edName").readOnly = true;
    setProfileFields(saved.profile);
    $("edOk").textContent = "Saved " + saved.name + ". Chats pick up the change on their next message.";
    $("edColourText").value = saved.colour || $("edColourText").value;
    await loadCharacters();
  } catch (err) {
    $("edErr").textContent = err.message;
  }
}

// Tags are chips. Enter or a comma adds one; Backspace on an empty box removes the last. Stored lower case,
// so "Dragon" and "dragon" gather the same characters. Limits match TAG_MAX and TAGS_MAX in web/profiles.py.
let edTags = [];
const TAG_MAX_LEN = 24;
const TAGS_MAX_COUNT = 8;

function setTags(list) {
  edTags = (list || []).slice();
  renderTags();
}

function renderTags() {
  const box = $("edTagBox");
  const input = $("edTagText");
  box.querySelectorAll(".tag-chip").forEach((c) => c.remove());
  for (const tag of edTags) {
    const chip = el("span", "", "tag-chip");
    chip.appendChild(el("span", tag));
    const x = el("button", "\u00d7", "tag-x");
    x.type = "button";
    x.setAttribute("aria-label", "Remove tag " + tag);
    x.addEventListener("click", () => {
      edTags = edTags.filter((t) => t !== tag);
      renderTags();
      input.focus();
    });
    chip.appendChild(x);
    box.insertBefore(chip, input);
  }
}

// Same rule as fold_accents in web/profiles.py: accents go from Latin letters only.
function foldAccents(text) {
  let out = "";
  let latin = false;
  for (const c of text.normalize("NFD")) {
    if (/\p{M}/u.test(c)) {
      if (latin) continue;
    } else {
      latin = /^\p{Script=Latin}/u.test(c);
    }
    out += c;
  }
  return out.normalize("NFC");
}

// Returns false when the tag was refused; the text stays in the box so it can be shortened.
function addTag(raw) {
  const tag = foldAccents(String(raw || "").trim().replace(/\s+/g, " ").toLowerCase());
  if (!tag) { $("edTagText").value = ""; return true; }
  if (tag.length > TAG_MAX_LEN) {
    $("edProfileErr").textContent = "Tags are " + TAG_MAX_LEN + " characters or fewer.";
    return false;
  }
  if (!edTags.includes(tag)) {
    if (edTags.length >= TAGS_MAX_COUNT) {
      $("edProfileErr").textContent = "Use up to " + TAGS_MAX_COUNT + " tags.";
      return false;
    }
    edTags.push(tag);
    renderTags();
  }
  $("edTagText").value = "";
  return true;
}

function flushTagText() {
  addTag($("edTagText").value);
}

// Suggestions are every tag in use, most used first. Reloaded each time the box gets focus.
async function loadTagSuggestions() {
  try {
    const data = await api("GET", "/api/admin/tags");
    const list = $("edTagSuggest");
    list.textContent = "";
    for (const t of data.tags) {
      const option = document.createElement("option");
      option.value = t.name;
      option.label = t.name + " (" + t.count + ")";
      list.appendChild(option);
    }
  } catch (err) {
    // Suggestions are optional; typing a tag still works.
  }
}

$("edTagText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    addTag(e.target.value);
  } else if (e.key === "Backspace" && !e.target.value && edTags.length) {
    edTags.pop();
    renderTags();
  }
});
$("edTagText").addEventListener("blur", flushTagText);
$("edTagText").addEventListener("focus", loadTagSuggestions);
$("edTagBox").addEventListener("click", (e) => {
  if (e.target === $("edTagBox")) $("edTagText").focus();
});

// The profile card: headline, summary and tags. Shown on the home page; never sent to the roleplay prompt.
function setProfileFields(profile) {
  loadTagSuggestions();   // ready before the admin clicks into the tag box
  $("edHeadline").value = profile ? profile.headline : "";
  $("edInfo").value = profile ? profile.info : "";
  setTags(profile ? profile.tags : []);
  $("edProfileNote").textContent = profile && profile.auto ? "Written by the AI. Edit it and save to make it yours." : "";
  $("edProfileErr").textContent = "";
}

function profileFromForm() {
  flushTagText();
  return { headline: $("edHeadline").value, info: $("edInfo").value, tags: edTags.slice() };
}

// Drafts the profile from the card in the box. Nothing is saved until the admin saves the character.
async function autoFillProfile() {
  $("edProfileErr").textContent = "";
  const card = $("edCard").value;
  if (!card.trim()) { $("edProfileErr").textContent = "Write the character card first."; return; }
  const filled = $("edHeadline").value.trim() || $("edInfo").value.trim() || edTags.length;
  if (filled && !window.confirm("Replace the headline, summary and tags with new ones from the AI?")) return;
  $("edProfileAuto").disabled = true;
  $("edProfileNote").textContent = "Writing...";
  try {
    const data = await api("POST", "/api/admin/characters/draft", { card });
    $("edHeadline").value = data.headline;
    $("edInfo").value = data.info;
    setTags(data.tags || []);
    $("edProfileNote").textContent = "Drafted by the AI. Check it, then save the character.";
  } catch (err) {
    $("edProfileErr").textContent = err.message;
    $("edProfileNote").textContent = "";
  } finally {
    $("edProfileAuto").disabled = false;
  }
}

async function uploadIcon() {
  const file = $("edIconFile").files[0];
  if (!file || !editing) return;
  $("edIconErr").textContent = "";
  try {
    const r = await fetch("/api/admin/characters/" + encodeURIComponent(editing) + "/icon", {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": file.type || "application/octet-stream" },
      credentials: "same-origin",
      body: file,
    });
    if (!r.ok) {
      let detail = "Upload failed.";
      try { detail = (await r.json()).detail || detail; } catch (e) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : "Upload failed.");
    }
    $("edIconFile").value = "";
    $("edIconSave").disabled = true;
    setIconPreview(editing, true);
    await loadCharacters();
  } catch (err) {
    $("edIconErr").textContent = err.message;
  }
}

function setBgPreview(name) {
  const box = $("edBgPreview");
  box.textContent = "";
  box.classList.add("placeholder");
  const img = document.createElement("img");
  img.alt = "";
  img.onload = () => box.classList.remove("placeholder");
  img.onerror = () => { box.textContent = "No background yet"; };
  img.src = "/api/background/" + encodeURIComponent(name) + "?v=" + Date.now();
  box.appendChild(img);
}

// ---- Draft from a file: fills the new-character form for review. Nothing is saved until Create.
async function draftFromFile() {
  const file = $("edDraftFile").files[0];
  if (!file) {
    $("edDraftMsg").textContent = "Choose a file first.";
    return;
  }
  $("edDraftMsg").textContent = "Reading the file and drafting. This can take a minute.";
  $("edDraftBtn").disabled = true;
  try {
    const r = await fetch("/api/admin/character-draft", {
      method: "POST",
      headers: {
        "X-Requested-With": "roleplay",
        "Content-Type": file.type || "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name),
      },
      credentials: "same-origin",
      body: file,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "The draft failed.");
    const hasText = $("edCard").value.trim() || $("edHeadline").value.trim() || $("edInfo").value.trim();
    if (hasText && !confirm("Replace the card, headline, summary and tags with the draft? A name you typed is kept.")) {
      $("edDraftMsg").textContent = "Kept your text. Nothing was changed.";
      return;
    }
    if (data.name && !$("edName").value.trim()) $("edName").value = data.name;
    $("edCard").value = data.character;
    $("edHeadline").value = data.headline;
    $("edInfo").value = data.info;
    setTags(data.tags);
    $("edDraftMsg").textContent = ["Draft ready. Check it, then Create."].concat(data.notes || []).join(" ");
  } catch (err) {
    $("edDraftMsg").textContent = err.message;
  } finally {
    $("edDraftBtn").disabled = false;
  }
}

// ---- Mind Palace: entries the server adds to the prompt when one of their keys appears in the chat.
let palaceEntries = [];

function renderPalace() {
  const list = $("edPalaceList");
  list.replaceChildren();
  palaceEntries.forEach((entry, index) => {
    const wrap = document.createElement("div");
    wrap.className = "palace-entry";
    const title = document.createElement("input");
    title.placeholder = "Title (optional)";
    title.value = entry.title;
    title.dataset.field = "title";
    title.setAttribute("aria-label", "Entry title");
    const keys = document.createElement("input");
    keys.placeholder = "Keys, comma separated";
    keys.value = entry.keys.join(", ");
    keys.dataset.field = "keys";
    keys.setAttribute("aria-label", "Keys that trigger this entry");
    const content = document.createElement("textarea");
    content.rows = 3;
    content.value = entry.content;
    content.dataset.field = "content";
    content.setAttribute("aria-label", "Entry content");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      readPalaceForm();
      palaceEntries.splice(index, 1);
      renderPalace();
    });
    wrap.append(title, keys, content, remove);
    list.append(wrap);
  });
}

function readPalaceForm() {
  // Keeps what is typed when a row is removed or the list is saved.
  palaceEntries = [...$("edPalaceList").children].map((wrap) => {
    const field = (name) => wrap.querySelector(`[data-field="${name}"]`).value;
    return {
      title: field("title").trim(),
      keys: field("keys").split(",").map((k) => k.trim()).filter(Boolean),
      content: field("content"),
    };
  });
}

async function loadPalace(name) {
  $("edPalaceMsg").textContent = "";
  try {
    const data = await api("GET", "/api/admin/characters/" + encodeURIComponent(name) + "/palace");
    palaceEntries = data.entries;
  } catch (err) {
    palaceEntries = [];
    $("edPalaceMsg").textContent = err.message;
  }
  renderPalace();
}

function addPalaceEntry() {
  readPalaceForm();
  palaceEntries.push({ title: "", keys: [], content: "" });
  renderPalace();
}

async function savePalace() {
  if (!editing) return;
  readPalaceForm();
  $("edPalaceMsg").textContent = "";
  try {
    const data = await api("POST", "/api/admin/characters/" + encodeURIComponent(editing) + "/palace", { entries: palaceEntries });
    palaceEntries = data.entries;
    renderPalace();
    $("edPalaceMsg").textContent = "Saved.";
  } catch (err) {
    $("edPalaceMsg").textContent = err.message;
  }
}

async function uploadBackground() {
  const file = $("edBgFile").files[0];
  if (!file || !editing) return;
  $("edBgErr").textContent = "";
  try {
    const r = await fetch("/api/admin/characters/" + encodeURIComponent(editing) + "/background", {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": file.type || "application/octet-stream" },
      credentials: "same-origin",
      body: file,
    });
    if (!r.ok) {
      let detail = "Upload failed.";
      try { detail = (await r.json()).detail || detail; } catch (e) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : "Upload failed.");
    }
    $("edBgFile").value = "";
    $("edBgSave").disabled = true;
    setBgPreview(editing);
    await loadCharacters();
  } catch (err) {
    $("edBgErr").textContent = err.message;
  }
}

// Export downloads the zip through fetch, so a failure shows here instead of as a blank page.
async function exportCharacter() {
  if (!editing) return;
  $("edExportErr").textContent = "";
  const hist = $("edExportHistory").checked ? "1" : "0";
  try {
    const r = await fetch("/api/admin/characters/" + encodeURIComponent(editing) + "/export?history=" + hist, { credentials: "same-origin" });
    if (!r.ok) {
      let detail = "Export failed.";
      try { detail = (await r.json()).detail || detail; } catch (e) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : "Export failed.");
    }
    const m = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = m ? m[1] : "character.zip";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (err) {
    $("edExportErr").textContent = err.message;
  }
}

async function importCharacter() {
  const file = $("impFile").files[0];
  if (!file) return;
  $("impMsg").textContent = "Importing...";
  try {
    const r = await fetch("/api/admin/characters/import", {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/zip" },
      credentials: "same-origin",
      body: file,
    });
    if (!r.ok) {
      let detail = "Import failed.";
      try { detail = (await r.json()).detail || detail; } catch (e) { /* not json */ }
      throw new Error(typeof detail === "string" ? detail : "Import failed.");
    }
    const out = await r.json();
    $("impFile").value = "";
    $("impBtn").disabled = true;
    $("impMsg").textContent = "Imported " + out.name + (out.history_restored ? ", with your history." : ".");
    await loadCharacters();
  } catch (err) {
    $("impMsg").textContent = err.message;
  }
}

$("editorForm").addEventListener("submit", saveCharacter);
$("editorBack").addEventListener("click", () => { showEditor(false); showChat(); renderUsage(); });
$("edExample").addEventListener("click", () => loadExample().catch((e) => { $("edErr").textContent = e.message; }));
$("edColourText").addEventListener("input", () => syncColour("text"));
$("edColour").addEventListener("input", () => syncColour("picker"));
$("edIconFile").addEventListener("change", () => {
  const f = $("edIconFile").files[0];
  const tooBig = !!f && f.size > 10 * 1024 * 1024;
  $("edIconErr").textContent = tooBig ? "Icon uploads can be up to 10 MB." : "";
  $("edIconSave").disabled = !f || !editing || tooBig;
});
$("edIconSave").addEventListener("click", uploadIcon);
$("edDraftBtn").addEventListener("click", draftFromFile);
$("edPalaceAdd").addEventListener("click", addPalaceEntry);
$("edPalaceSave").addEventListener("click", savePalace);
$("edBgFile").addEventListener("change", () => {
  const f = $("edBgFile").files[0];
  const tooBig = !!f && f.size > 10 * 1024 * 1024;
  $("edBgErr").textContent = tooBig ? "Background uploads can be up to 10 MB." : "";
  $("edBgSave").disabled = !f || !editing || tooBig;
});
$("edBgSave").addEventListener("click", uploadBackground);
$("edExport").addEventListener("click", exportCharacter);
$("impFile").addEventListener("change", () => {
  const f = $("impFile").files[0];
  const tooBig = !!f && f.size > 60 * 1024 * 1024;
  $("impMsg").textContent = tooBig ? "Archives can be up to 60 MB." : "";
  $("impBtn").disabled = !f || tooBig;
});
$("impBtn").addEventListener("click", importCharacter);
$("edProfileAuto").addEventListener("click", autoFillProfile);
