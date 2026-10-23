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
  $("edBgBox").hidden = false;   // a new character can take a background too; it is sent once created
  $("edExportBox").hidden = !name;
  $("edDraftBox").hidden = !!name;   // drafting only fills the new-character form
  $("edPalaceBox").hidden = !name;   // the Mind Palace is saved beside the character, so it needs one
  if (name) {
    loadPalace(name, "");
    fillPalaceWho();
  } else {
    palaceEntries = [];
    renderPalace();
  }
  $("edBgErr").textContent = "";
  $("edBgSave").disabled = true;
  pendingBgs = [];
  pendingIcon = null;
  pendingPalace = [];
  $("edImportMsg").textContent = "";
  $("edBgNote").textContent = "";
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
    setBgPreview(name, data.backgrounds || []);
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
    resetBgPreview();
  }
  $("edColourText").classList.remove("invalid");
  $("edSandbox").closest("label").hidden = !(me && me.is_admin);   // sandboxes are an admin feature
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
    box.appendChild(faceIcon(editorColour()));
  }
}

// The colour typed or picked for the character; the face placeholder uses it.
function editorColour() {
  const t = ($("edColourText").value || "").trim();
  return /^#[0-9a-f]{6}$/i.test(t) ? t : "";
}

$("edColour").addEventListener("input", () => {
  $("edColourText").value = $("edColour").value;
  if ($("edIconPreview").classList.contains("placeholder")) setIconPreview(editing, false);
});
$("edColourText").addEventListener("input", () => {
  if ($("edIconPreview").classList.contains("placeholder")) setIconPreview(editing, false);
});

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
      // An imported card's picture and lorebook are saved now that the character folder exists.
      if (pendingIcon) {
        try {
          const r = await fetch("/api/admin/characters/" + encodeURIComponent(saved.name) + "/icon", {
            method: "POST",
            headers: { "X-Requested-With": "roleplay", "Content-Type": "application/octet-stream" },
            credentials: "same-origin",
            body: await pendingIcon.arrayBuffer(),
          });
          if (!r.ok) throw new Error("the picture was not saved");
        } catch (err) {
          $("edErr").textContent = "Created " + saved.name + ", but " + err.message + ".";
        }
        pendingIcon = null;
        $("edImportFile").value = "";
      }
      if (pendingPalace.length) {
        palaceEntries = pendingPalace;
        pendingPalace = [];
        renderPalace();
        try {
          await savePalace();
        } catch (err) {
          $("edErr").textContent = "Created " + saved.name + ", but the lorebook was not saved: " + err.message;
        }
      }
      if (pendingBgs.length) {
        // The folder exists now, so the queued backgrounds are sent in the order chosen.
        let failed = "";
        for (const file of pendingBgs) {
          try {
            await sendBackground(saved.name, file);
          } catch (err) {
            failed = err.message;
            break;
          }
        }
        if (failed) $("edErr").textContent = "Created " + saved.name + ", but a background did not upload: " + failed;
        pendingBgs = [];
        $("edBgFile").value = "";
        try {
          const data = await api("GET", "/api/admin/characters/" + encodeURIComponent(saved.name));
          setBgPreview(saved.name, data.backgrounds || []);
        } catch (err) {
          $("edErr").textContent = err.message;
        }
      }
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

// ---- Backgrounds: several per character. A new character queues its files until it exists.
const BG_MAX = 10;
let bgItems = [];      // saved backgrounds: { id }
let pendingBgs = [];   // files queued on the new-character form

function setBgPreview(name, ids) {
  bgItems = (ids || []).map((id) => ({ id }));
  renderBgList();
}

function resetBgPreview() {
  bgItems = [];
  renderBgList();
}

function renderBgList() {
  const box = $("edBgList");
  box.textContent = "";
  const items = [...bgItems, ...pendingBgs.map((file) => ({ file }))];
  if (!items.length) box.appendChild(el("p", "No backgrounds yet.", "muted"));
  for (const item of items) {
    const thumb = el("div", "", "bg-thumb");
    const img = document.createElement("img");
    img.alt = "";
    img.src = item.file ? URL.createObjectURL(item.file) : "/api/background/" + encodeURIComponent(editing) + "?b=" + encodeURIComponent(item.id);
    const rm = el("button", "Remove", "ghost small");
    rm.type = "button";
    rm.setAttribute("aria-label", "Remove this background");
    rm.addEventListener("click", () => removeBackground(item));
    thumb.append(img, rm);
    box.appendChild(thumb);
  }
  $("edBgNote").textContent = items.length + " of " + BG_MAX + " backgrounds.";
}

async function removeBackground(item) {
  if (item.file) {
    pendingBgs = pendingBgs.filter((f) => f !== item.file);
    renderBgList();
    return;
  }
  $("edBgErr").textContent = "";
  try {
    await api("DELETE", "/api/admin/characters/" + encodeURIComponent(editing) + "/backgrounds/" + encodeURIComponent(item.id));
    const data = await api("GET", "/api/admin/characters/" + encodeURIComponent(editing));
    setBgPreview(editing, data.backgrounds || []);
    await loadCharacters();
  } catch (err) {
    $("edBgErr").textContent = err.message;
  }
}

// ---- Import a character card (SillyTavern JSON or PNG). The picture and lorebook wait for Create.
let pendingIcon = null;    // the imported PNG, saved as the character's picture once it exists
let pendingPalace = [];    // the imported lorebook, saved as Mind Palace entries once it exists

async function importCard() {
  const file = $("edImportFile").files[0];
  if (!file) {
    $("edImportMsg").textContent = "Choose a .json or .png card first.";
    return;
  }
  $("edImportMsg").textContent = "Reading the card.";
  $("edImportBtn").disabled = true;
  try {
    const r = await fetch("/api/admin/character-import", {
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
    if (!r.ok) throw new Error(data.detail || "The card could not be imported.");
    const hasText = $("edCard").value.trim() || $("edHeadline").value.trim() || $("edInfo").value.trim();
    if (hasText && !confirm("Replace the card, context, headline, summary and tags with the imported card? A name you typed is kept.")) {
      $("edImportMsg").textContent = "Kept your text. Nothing was changed.";
      return;
    }
    if (data.name && !$("edName").value.trim()) $("edName").value = data.name;
    $("edCard").value = data.character;
    $("edContext").value = data.context;
    $("edHeadline").value = data.headline;
    $("edInfo").value = data.info;
    setTags(data.tags);
    pendingIcon = data.icon ? file : null;
    pendingPalace = data.palace || [];
    const parts = ["Card imported. Check it, then Create."];
    if (pendingIcon) parts.push("The PNG will be the picture.");
    if (pendingPalace.length) parts.push(`${pendingPalace.length} lorebook entries will be saved to the Mind Palace.`);
    $("edImportMsg").textContent = parts.concat(data.notes || []).join(" ");
  } catch (err) {
    $("edImportMsg").textContent = err.message;
  } finally {
    $("edImportBtn").disabled = false;
  }
}

$("edImportBtn").addEventListener("click", importCard);

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

const PALACE_LOGIC = [
  ["and_any", "Secondary: any one"],
  ["and_all", "Secondary: all"],
  ["not_any", "Secondary: none"],
  ["not_all", "Secondary: not all"],
];

function palaceField(tag, attrs) {
  const el = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => { if (k === "text") el.textContent = v; else el.setAttribute(k, v); });
  return el;
}

function palaceLabel(text, control) {
  const label = document.createElement("label");
  label.className = "palace-field";
  label.append(palaceField("span", { text }), control);
  return label;
}

function renderPalaceList(list, entries, readOnly) {
  list.replaceChildren();
  entries.forEach((entry, index) => {
    const wrap = document.createElement("div");
    wrap.className = "palace-entry";
    // The id and the automatic flag are not shown as fields, but they must survive an edit.
    wrap.dataset.id = entry.id || "";
    wrap.dataset.auto = entry.auto ? "1" : "";
    if (entry.auto) wrap.appendChild(palaceField("p", { class: "muted palace-auto", text: "Added automatically" }));
    const title = palaceField("input", { placeholder: "Title (optional)", "aria-label": "Entry title", "data-field": "title" });
    title.value = entry.title;
    const keys = palaceField("input", { placeholder: "Keys, comma separated", "aria-label": "Keys that trigger this entry", "data-field": "keys" });
    keys.value = entry.keys.join(", ");
    const secondary = palaceField("input", { placeholder: "Secondary keys (optional)", "aria-label": "Secondary keys", "data-field": "secondary" });
    secondary.value = entry.secondary.join(", ");
    const logic = palaceField("select", { "aria-label": "Secondary key logic", "data-field": "logic" });
    PALACE_LOGIC.forEach(([value, text]) => {
      const opt = palaceField("option", { value, text });
      logic.append(opt);
    });
    logic.value = entry.logic;
    const constant = palaceField("input", { type: "checkbox", "data-field": "constant" });
    constant.checked = entry.constant;
    const enabled = palaceField("input", { type: "checkbox", "data-field": "enabled" });
    enabled.checked = entry.enabled;
    const whole = palaceField("input", { type: "checkbox", "data-field": "whole" });
    whole.checked = entry.whole;
    const depth = palaceField("input", { type: "number", min: "1", max: "30", step: "1", "aria-label": "Scan depth", "data-field": "depth" });
    depth.value = entry.depth;
    const order = palaceField("input", { type: "number", min: "0", max: "1000", step: "1", "aria-label": "Insertion order", "data-field": "order" });
    order.value = entry.order;
    const content = palaceField("textarea", { rows: "3", "aria-label": "Entry content", "data-field": "content" });
    content.value = entry.content;
    const remove = palaceField("button", { type: "button", class: "ghost", text: "Remove" });
    remove.addEventListener("click", () => {
      const live = readPalaceList(list);
      live.splice(index, 1);
      renderPalaceList(list, live, readOnly);
    });
    const options = palaceField("div", { class: "palace-options" });
    options.append(
      palaceLabel("Secondary keys", secondary),
      palaceLabel("Logic", logic),
      palaceLabel("Scan depth (messages)", depth),
      palaceLabel("Order (higher = kept first)", order),
      palaceLabel("Always on", constant),
      palaceLabel("Enabled", enabled),
      palaceLabel("Whole words only", whole),
    );
    if (readOnly) {
      wrap.querySelectorAll("input, select, textarea").forEach((f) => { f.disabled = true; });
      remove.hidden = true;
    }
    wrap.append(title, keys, options, content, remove);
    list.append(wrap);
  });
}

function renderPalace(readOnly = false) {
  renderPalaceList($("edPalaceList"), palaceEntries, readOnly);
}

function readPalaceList(list) {
  // Reads what is typed in a list of entries, so nothing is lost when rows change.
  return [...list.children].map((wrap) => {
    const field = (name) => wrap.querySelector(`[data-field="${name}"]`);
    const list = (name) => field(name).value.split(",").map((k) => k.trim()).filter(Boolean);
    return {
      id: wrap.dataset.id || "",
      auto: wrap.dataset.auto === "1",
      title: field("title").value.trim(),
      keys: list("keys"),
      secondary: list("secondary"),
      logic: field("logic").value,
      content: field("content").value,
      constant: field("constant").checked,
      enabled: field("enabled").checked,
      whole: field("whole").checked,
      depth: Number(field("depth").value) || 6,
      order: Number(field("order").value),
    };
  });
}

function readPalaceForm() {
  // Keeps what is typed when a row is removed or the list is saved.
  palaceEntries = readPalaceList($("edPalaceList"));
}

async function loadPalace(name, uid) {
  // uid picks one account's palace to look at (admins only, read-only). An empty uid is the starting palace.
  $("edPalaceMsg").textContent = "";
  const viewing = !!uid;
  const query = viewing ? "?user=" + encodeURIComponent(uid) : "";
  try {
    const data = await api("GET", "/api/admin/characters/" + encodeURIComponent(name) + "/palace" + query);
    palaceEntries = data.entries;
    $("edPalaceRecursive").checked = !!data.recursive;
    $("edPalaceBudget").value = data.budget;
  } catch (err) {
    palaceEntries = [];
    $("edPalaceMsg").textContent = err.message;
  }
  for (const id of ["edPalaceRecursive", "edPalaceBudget"]) $(id).disabled = viewing;
  $("edPalaceAdd").hidden = viewing;
  $("edPalaceSave").hidden = viewing;
  renderPalace(viewing);
}

async function fillPalaceWho() {
  // Admins can look at a non-admin account's palace for this character. The select lists those accounts.
  const sel = $("edPalaceWho");
  $("edPalaceWhoRow").hidden = !(me && me.is_admin);
  sel.replaceChildren(palaceField("option", { value: "", text: "Starting palace (editable)" }));
  if (!me || !me.is_admin) return;
  try {
    const data = await api("GET", "/api/admin/users");
    for (const u of data.users) {
      if (!u.is_admin) sel.append(palaceField("option", { value: String(u.id), text: "Account " + u.username + " (read-only)" }));
    }
  } catch (err) {
    $("edPalaceMsg").textContent = err.message;
  }
}

function addPalaceEntry() {
  readPalaceForm();
  palaceEntries.push({ title: "", keys: [], secondary: [], logic: "and_any", content: "", constant: false,
                       enabled: true, whole: true, depth: 6, order: 100 });
  renderPalace();
}

async function savePalace() {
  if (!editing) return;
  readPalaceForm();
  $("edPalaceMsg").textContent = "";
  try {
    const data = await api("POST", "/api/admin/characters/" + encodeURIComponent(editing) + "/palace",
      { entries: palaceEntries, recursive: $("edPalaceRecursive").checked, budget: Number($("edPalaceBudget").value) });
    palaceEntries = data.entries;
    $("edPalaceRecursive").checked = !!data.recursive;
    $("edPalaceBudget").value = data.budget;
    renderPalace();
    $("edPalaceMsg").textContent = "Saved.";
  } catch (err) {
    $("edPalaceMsg").textContent = err.message;
  }
}

async function sendBackground(name, file) {
  // Adds one background for a character. Throws with the server's message if it is refused.
  const r = await fetch("/api/admin/characters/" + encodeURIComponent(name) + "/backgrounds", {
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
}

async function uploadBackground() {
  const files = Array.from($("edBgFile").files || []);
  if (!files.length || !editing) return;
  $("edBgErr").textContent = "";
  let failed = "";
  for (const file of files) {
    try {
      await sendBackground(editing, file);
    } catch (err) {
      failed = err.message;
      break;
    }
  }
  $("edBgFile").value = "";
  $("edBgSave").disabled = true;
  if (failed) $("edBgErr").textContent = failed;
  try {
    const data = await api("GET", "/api/admin/characters/" + encodeURIComponent(editing));
    setBgPreview(editing, data.backgrounds || []);
  } catch (err) {
    $("edBgErr").textContent = err.message;
  }
  await loadCharacters();
}

// A background chosen on the new-character form waits until the character exists.
const NEW_BG_MAX = 10 * 1024 * 1024;


function queueNewBackground(files) {
  // New characters hold their backgrounds until Create; the list shows what is queued.
  const tooBig = files.some((f) => f.size > NEW_BG_MAX);
  const room = BG_MAX - pendingBgs.length;
  pendingBgs = pendingBgs.concat(tooBig ? [] : files.slice(0, Math.max(0, room)));
  $("edBgErr").textContent = tooBig ? "Background uploads can be up to 10 MB." : files.length > room ? "Only " + Math.max(0, room) + " more fit (up to 10)." : "";
  $("edBgFile").value = "";
  renderBgList();
}

function onBgFilesChosen() {
  const files = Array.from($("edBgFile").files || []);
  if (!files.length) return;
  if (!editing) {
    queueNewBackground(files);
    return;
  }
  const tooBig = files.some((f) => f.size > NEW_BG_MAX);
  const over = bgItems.length + files.length > BG_MAX;
  $("edBgErr").textContent = tooBig ? "Background uploads can be up to 10 MB." : over ? "A character can have up to 10 backgrounds. Remove one first." : "";
  $("edBgSave").disabled = tooBig || over;
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
$("edBgSave").addEventListener("click", uploadBackground);
$("edBgFile").addEventListener("change", onBgFilesChosen);
$("edPalaceWho").addEventListener("change", () => { if (editing) loadPalace(editing, $("edPalaceWho").value); });

// ---- Settings: the account's own Mind Palace for the open character. Each account has its own copy.
async function openMyPalace() {
  if (!current || !soloNames.includes(current)) return;
  $("palMsg").textContent = "";
  try {
    const data = await api("GET", "/api/character-palace?character=" + encodeURIComponent(current));
    $("palRecursive").checked = !!data.recursive;
    $("palBudget").value = data.budget;
    $("palEpisodes").checked = data.episodes !== false;
    setAutoFields(data.auto);
    renderPalaceList($("palList"), data.entries, false);
  } catch (err) {
    $("chatErr").textContent = err.message;
    return;
  }
  $("palaceDlg").showModal();
}

// The automatic-entry controls in the Mind Palace dialog.
function setAutoFields(auto) {
  $("palAutoOn").checked = !!(auto && auto.enabled);
  $("palAutoEvery").value = auto ? auto.every : 3;
  $("palAutoLimit").value = auto ? auto.limit : 20;
}

function readAutoFields() {
  return { enabled: $("palAutoOn").checked, every: Number($("palAutoEvery").value), limit: Number($("palAutoLimit").value) };
}

async function saveMyPalace() {
  if (!current) return;
  $("palMsg").textContent = "";
  try {
    const data = await api("POST", "/api/character-palace?character=" + encodeURIComponent(current), {
      entries: readPalaceList($("palList")),
      recursive: $("palRecursive").checked,
      budget: Number($("palBudget").value),
      auto: readAutoFields(),
      episodes: $("palEpisodes").checked,
    });
    setAutoFields(data.auto);
    renderPalaceList($("palList"), data.entries, false);
    $("palMsg").textContent = "Saved. Applies from your next message.";
  } catch (err) {
    $("palMsg").textContent = err.message;
  }
}

function addMyPalaceEntry() {
  const entries = readPalaceList($("palList"));
  entries.push({ title: "", keys: [], secondary: [], logic: "and_any", content: "", constant: false,
                 enabled: true, whole: true, depth: 6, order: 100 });
  renderPalaceList($("palList"), entries, false);
}

$("palaceOpen").addEventListener("click", openMyPalace);
$("palAdd").addEventListener("click", addMyPalaceEntry);
$("palSave").addEventListener("click", saveMyPalace);
$("palClose").addEventListener("click", () => $("palaceDlg").close());
$("edExport").addEventListener("click", exportCharacter);
$("impFile").addEventListener("change", () => {
  const f = $("impFile").files[0];
  const tooBig = !!f && f.size > 60 * 1024 * 1024;
  $("impMsg").textContent = tooBig ? "Archives can be up to 60 MB." : "";
  $("impBtn").disabled = !f || tooBig;
});
$("impBtn").addEventListener("click", importCharacter);
$("edProfileAuto").addEventListener("click", autoFillProfile);
