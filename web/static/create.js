// The character wizard, on /create/character. A new character is made one part at a time: each step is a section of
// create.html, and the steps slide in as the user moves through them. Creating saves the character, then its picture,
// lorebook and backgrounds, with the same requests the editor makes. Changes to a character that already exists stay
// in the editor (editor.js). Every value goes into fields with .value or textContent, never innerHTML.
const $ = (id) => document.getElementById(id);

// Same limits as editor.js, web/profiles.py and the server's background checks.
const TAG_MAX_LEN = 24;
const TAGS_MAX_COUNT = 8;
const EXAMPLE_LIMIT = 12;
const BG_MAX = 10;
const ICON_MAX = 10 * 1024 * 1024;
const BG_IMAGE_MAX = 100 * 1024 * 1024;
const BG_VIDEO_MAX = 50 * 1024 * 1024;
const PALACE_BUDGET = 3000;
const ZIP_MAX = 60 * 1024 * 1024;

// Relationship speed. The wizard shows the category for the chosen scale, never the raw rate, as the editor does.
const REL = {
  trust: { range: "wzTrust", number: "wzTrustNum", fallback: 3,
    bands: [[2, "Cautious"], [4, "Open"], [5, "Trusting"], [6, "Heart of gold"]] },
  affection: { range: "wzAffection", number: "wzAffectionNum", fallback: 2,
    bands: [[2, "Fond"], [4, "Attached"], [5, "Devoted"], [6, "Soulmate"]] },
};

let me = null;           // the signed-in account
let stepIndex = 0;       // the step on screen
let furthest = 0;        // the furthest step reached; the pills up to it can be pressed
let busy = false;        // true while the character is being created
let tags = [];           // the profile tags as added
let iconFile = null;     // the picture: chosen here, or the PNG of an imported card
let iconUrl = "";        // the object URL shown for iconFile
let bgFiles = [];        // backgrounds, held until the character exists
let bgUrls = new Map();  // the object URL shown for each queued background
let palaceEntries = [];  // the lorebook of an imported card, saved once the character exists

// Drafts. The wizard keeps what is typed in this browser, per account, so a reload picks it up. Pictures and backgrounds
// are files and are not kept. dirty is true only between a change and its save; the page warns on leaving only then.
const DRAFT_PREFIX = "miso.createCharacterDraft.";
const DRAFT_DELAY = 500;
let dirty = false;
let settled = false;     // the character is created, or the draft was discarded: nothing to keep or warn about
let leaving = false;     // set when leaving on purpose (Cancel, Back to chat, Log out), after the draft is saved
let quiet = true;        // true while the page is set up or a draft is put back, so that is not a change
let draftTimer = null;

function fail(message) {
  $("loading").hidden = true;
  $("pageErr").textContent = message;
}

function sections() {
  return [...document.querySelectorAll(".wz-step")];
}

function indexOfStep(id) {
  return sections().findIndex((section) => section.dataset.step === id);
}

// Sends a file as the request body, as the editor does. Throws with the server's message when it refuses.
async function sendFile(url, file, options = {}) {
  const headers = { "X-Requested-With": "roleplay", "Content-Type": options.type || file.type || "application/octet-stream" };
  if (options.filename) headers["X-Filename"] = encodeURIComponent(file.name);
  const r = await fetch(url, { method: "POST", headers, credentials: "same-origin", body: file });
  let data = {};
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : options.fallback || tr("Upload failed."));
  return data;
}

// ---- Steps

// Shows one step. A step reached going forward slides in from the right, and one reached going back from the left.
function goTo(index) {
  const all = sections();
  const next = Math.max(0, Math.min(all.length - 1, index));
  if (next === stepIndex && !all[next].hidden) return;
  const forward = next >= stepIndex;
  stepIndex = next;
  furthest = Math.max(furthest, next);
  all.forEach((section, i) => {
    section.hidden = i !== next;
    section.classList.remove("enter-next", "enter-back");
  });
  const shown = all[next];
  shown.classList.add(forward ? "enter-next" : "enter-back");
  $("wzErr").textContent = "";
  $("wzBar").style.width = (all.length > 1 ? (next / (all.length - 1)) * 100 : 100) + "%";
  $("wzCount").textContent = tr("Step {n} of {total}", { n: next + 1, total: all.length });
  $("wzBack").hidden = next === 0;
  const last = shown.dataset.step === "review";
  $("wzNext").textContent = tr(last ? "Create character" : "Next");
  if (last) renderSummary();
  paintPills();
  const form = $("wzForm");
  if (form.getBoundingClientRect().top < 0) form.scrollIntoView({ block: "start", behavior: motionReduced() ? "auto" : "smooth" });
  const target = shown.querySelector("input:not([type=file]), textarea, select, button");
  if (target) target.focus({ preventScroll: true });
}

function buildPills() {
  const list = $("wzSteps");
  sections().forEach((section, i) => {
    const btn = el("button", (i + 1) + ". " + section.dataset.short, "wz-pill");
    btn.type = "button";
    btn.dataset.index = String(i);
    btn.addEventListener("click", () => goTo(i));
    const item = el("li");
    item.appendChild(btn);
    list.appendChild(item);
  });
}

// A step can be reopened once it has been reached; the step on screen is marked current.
function paintPills() {
  for (const btn of document.querySelectorAll(".wz-pill")) {
    const i = Number(btn.dataset.index);
    btn.disabled = busy || i > furthest;
    btn.classList.toggle("done", i < stepIndex);
    btn.classList.toggle("current", i === stepIndex);
    if (i === stepIndex) btn.setAttribute("aria-current", "step");
    else btn.removeAttribute("aria-current");
  }
}

function setBusy(on) {
  busy = on;
  $("wzNext").disabled = on;
  $("wzBack").disabled = on;
  paintPills();
}

// The reason the step on screen cannot be left yet, as { field, text }; null when it can. The text may be empty when
// the field has already said what is wrong.
function checkStep(index) {
  const id = sections()[index].dataset.step;
  if (id === "name") {
    if (!$("wzName").value.trim()) return { field: "wzName", text: tr("Give the character a name.") };
    const typed = $("wzColourText").value.trim();
    if (typed && !/^#[0-9a-fA-F]{6}$/.test(typed)) return { field: "wzColourText", text: tr("Colour must be a hex code such as #c0392b.") };
  }
  if (id === "card" && !$("wzCard").value.trim()) return { field: "wzCard", text: tr("Write the character card first.") };
  if (id === "profile" && !flushTagText()) return { field: "wzTagText", text: "" };
  if (id === "examples") {
    for (const row of $("wzExamplesList").children) {
      const user = row.querySelector(".example-user").value.trim();
      const reply = row.querySelector(".example-reply").value.trim();
      if (Boolean(user) !== Boolean(reply)) {
        return { field: null, text: tr("Each exchange needs both your message and the character's reply. Fill in the empty half or remove the row.") };
      }
    }
  }
  return null;
}

function showProblem(problem) {
  $("wzErr").textContent = problem.text || "";
  if (problem.field) $(problem.field).focus();
}

async function onSubmit(ev) {
  ev.preventDefault();
  if (busy) return;
  const problem = checkStep(stepIndex);
  if (problem) {
    showProblem(problem);
    return;
  }
  if (sections()[stepIndex].dataset.step === "review") {
    await create();
    return;
  }
  goTo(stepIndex + 1);
}

// ---- Start: the three ways in

function chooseRoute(route) {
  for (const btn of document.querySelectorAll(".wz-route")) btn.setAttribute("aria-pressed", String(btn.dataset.route === route));
  $("wzDraftPanel").hidden = route !== "draft";
  $("wzImportPanel").hidden = route !== "import";
  $("wzErr").textContent = "";
}

// Replacing the card and profile with a file's contents asks first, unless nothing has been written yet.
function canReplaceText(question) {
  const written = $("wzCard").value.trim() || $("wzHeadline").value.trim() || $("wzInfo").value.trim();
  return !written || window.confirm(question);
}

// Fills the card, profile and examples from a draft or an imported card. A name typed already is kept.
function fillCard(data) {
  if (data.name && !$("wzName").value.trim()) $("wzName").value = data.name;
  $("wzCard").value = data.character || "";
  if (typeof data.context === "string") $("wzContext").value = data.context;
  if (data.examples) renderExamples(data.examples);
  $("wzHeadline").value = data.headline || "";
  $("wzInfo").value = data.info || "";
  setTags(data.tags || []);
}

async function draftFromFile() {
  const file = $("wzDraftFile").files[0];
  if (!file) {
    $("wzDraftMsg").textContent = tr("Choose a file first.");
    return;
  }
  $("wzDraftMsg").textContent = tr("Reading the file and drafting. This can take a minute.");
  $("wzDraftBtn").disabled = true;
  try {
    const data = await sendFile("/api/admin/character-draft", file, { filename: true, fallback: tr("The draft failed.") });
    if (!canReplaceText(tr("Replace the card, headline, summary and tags with the draft? A name you typed is kept."))) {
      $("wzDraftMsg").textContent = tr("Kept your text. Nothing was changed.");
      return;
    }
    fillCard(data);
    $("wzDraftMsg").textContent = [tr("Draft ready. Check it on the next steps.")].concat(data.notes || []).join(" ");
  } catch (err) {
    $("wzDraftMsg").textContent = err.message;
  } finally {
    $("wzDraftBtn").disabled = false;
  }
}

async function importCard() {
  const file = $("wzImportFile").files[0];
  if (!file) {
    $("wzImportMsg").textContent = tr("Choose a .json or .png card first.");
    return;
  }
  $("wzImportMsg").textContent = tr("Reading the card.");
  $("wzImportBtn").disabled = true;
  try {
    const data = await sendFile("/api/admin/character-import", file, { filename: true, fallback: tr("The card could not be imported.") });
    if (!canReplaceText(tr("Replace the card, context, headline, summary and tags with the imported card? A name you typed is kept."))) {
      $("wzImportMsg").textContent = tr("Kept your text. Nothing was changed.");
      return;
    }
    fillCard(data);
    if (data.icon) setIcon(file);
    palaceEntries = data.palace || [];
    touch();
    const parts = [tr("Card imported. Check it on the next steps.")];
    if (data.icon) parts.push(tr("The PNG will be the picture."));
    if (palaceEntries.length) parts.push(tr("{n} lorebook entries will be saved to the Mind Palace.", { n: palaceEntries.length }));
    $("wzImportMsg").textContent = parts.concat(data.notes || []).join(" ");
  } catch (err) {
    $("wzImportMsg").textContent = err.message;
  } finally {
    $("wzImportBtn").disabled = false;
  }
}

// A zip is created at once by the server, so it skips the steps and goes straight to the done screen.
async function importZip() {
  const file = $("wzZipFile").files[0];
  if (!file) return;
  $("wzZipMsg").textContent = tr("Importing...");
  $("wzZipBtn").disabled = true;
  try {
    const out = await sendFile("/api/admin/characters/import", file, { type: "application/zip", fallback: tr("Import failed.") });
    showDone(tr("Imported {name}.", { name: out.name }), [out.history_restored ? tr("The chat history came with the character.") : ""], out.name);
  } catch (err) {
    $("wzZipMsg").textContent = err.message;
    $("wzZipBtn").disabled = false;
  }
}

// ---- Card and profile

async function loadExample() {
  if ($("wzCard").value.trim() && !window.confirm(tr("Replace the card with the example?"))) return;
  const data = await api("GET", "/api/admin/characters/example");
  $("wzCard").value = data.card;
  if (data.examples) renderExamples(data.examples);
  const m = /^Colour:\s*(#[0-9a-fA-F]{6})/m.exec(data.card);
  if (m) {
    $("wzColourText").value = m[1];
    $("wzColour").value = m[1];
    refreshPlaceholder();
  }
  touch();
}

// Drafts the headline, summary and tags from the card. Nothing is saved until Create.
async function autoFill() {
  $("wzProfileErr").textContent = "";
  const card = $("wzCard").value;
  if (!card.trim()) {
    $("wzProfileErr").textContent = tr("Write the character card first.");
    return;
  }
  const filled = $("wzHeadline").value.trim() || $("wzInfo").value.trim() || tags.length;
  if (filled && !window.confirm(tr("Replace the headline, summary and tags with new ones from the AI?"))) return;
  $("wzAuto").disabled = true;
  $("wzAutoNote").textContent = tr("Writing...");
  try {
    const data = await api("POST", "/api/admin/characters/draft", { card });
    $("wzHeadline").value = data.headline;
    $("wzInfo").value = data.info;
    setTags(data.tags || []);
    $("wzAutoNote").textContent = tr("Drafted by the AI. Check it, then continue.");
  } catch (err) {
    $("wzProfileErr").textContent = err.message;
    $("wzAutoNote").textContent = "";
  } finally {
    $("wzAuto").disabled = false;
  }
}

// Tags are chips. Enter or a comma adds one; Backspace on an empty box removes the last. Stored lower case, so
// "Dragon" and "dragon" gather the same characters.
function setTags(list) {
  tags = list.slice();
  renderTags();
}

function renderTags() {
  touch();
  const box = $("wzTagBox");
  const input = $("wzTagText");
  box.querySelectorAll(".tag-chip").forEach((chip) => chip.remove());
  for (const tag of tags) {
    const chip = el("span", "", "tag-chip");
    chip.appendChild(el("span", tag));
    const x = el("button", "×", "tag-x");
    x.type = "button";
    x.setAttribute("aria-label", tr("Remove tag {tag}", { tag: tag }));
    x.addEventListener("click", () => {
      tags = tags.filter((t) => t !== tag);
      renderTags();
      input.focus();
    });
    chip.appendChild(x);
    box.insertBefore(chip, input);
  }
}

// Returns false when the tag was refused; the text stays in the box so it can be shortened.
function addTag(raw) {
  const tag = foldAccents(String(raw || "").trim().replace(/\s+/g, " ").toLowerCase());
  if (!tag) {
    $("wzTagText").value = "";
    return true;
  }
  if (tag.length > TAG_MAX_LEN) {
    $("wzProfileErr").textContent = tr("Tags are {n} characters or fewer.", { n: TAG_MAX_LEN });
    return false;
  }
  if (!tags.includes(tag)) {
    if (tags.length >= TAGS_MAX_COUNT) {
      $("wzProfileErr").textContent = tr("Use up to {n} tags.", { n: TAGS_MAX_COUNT });
      return false;
    }
    tags.push(tag);
    renderTags();
  }
  $("wzProfileErr").textContent = "";
  $("wzTagText").value = "";
  return true;
}

function flushTagText() {
  return addTag($("wzTagText").value);
}

async function loadTagSuggestions() {
  try {
    const data = await api("GET", "/api/admin/tags");
    const list = $("wzTagSuggest");
    list.replaceChildren();
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

// ---- Example messages: one row per exchange, sent as examples once the character exists.

function renderExamples(pairs) {
  $("wzExamplesList").replaceChildren();
  $("wzExamplesErr").textContent = "";
  const list = pairs && pairs.length ? pairs : [null];   // one empty row, so the step reads as something to fill in
  for (const pair of list) addExampleRow(pair);
  updateExampleCount();
}

function addExampleRow(pair) {
  const list = $("wzExamplesList");
  if (list.children.length >= EXAMPLE_LIMIT) {
    $("wzExamplesErr").textContent = tr("Up to {n} exchanges. Remove one to add another.", { n: EXAMPLE_LIMIT });
    return null;
  }
  const row = el("div", "", "example-row");
  const head = el("div", "", "example-row-head");
  const remove = el("button", tr("Remove"), "ghost small");
  remove.type = "button";
  remove.addEventListener("click", () => {
    row.remove();
    renumberExamples();
  });
  head.append(el("span", "", "example-row-title"), remove);
  const you = el("textarea", "", "example-user");
  you.rows = 2;
  you.maxLength = 1000;
  you.placeholder = tr("What you say to the character");
  you.setAttribute("aria-label", tr("Your message in this example"));
  const them = el("textarea", "", "example-reply");
  them.rows = 3;
  them.maxLength = 2000;
  them.placeholder = tr("How the character answers");
  them.setAttribute("aria-label", tr("The character's reply in this example"));
  if (pair) {
    you.value = pair.user || "";
    them.value = pair.character || "";
  }
  row.append(head, el("span", tr("You"), "example-label"), you, el("span", tr("Character"), "example-label"), them);
  list.appendChild(row);
  renumberExamples();
  return row;
}

function renumberExamples() {
  touch();
  [...$("wzExamplesList").children].forEach((row, i) => {
    row.querySelector(".example-row-title").textContent = tr("Exchange {n}", { n: i + 1 });
  });
  updateExampleCount();
}

function updateExampleCount() {
  const rows = $("wzExamplesList").children.length;
  $("wzExampleCount").textContent = rows ? tr("{n} of {max}", { n: rows, max: EXAMPLE_LIMIT }) : tr("None yet");
  $("wzExampleAdd").disabled = rows >= EXAMPLE_LIMIT;
}

// A row with one half filled in is refused by the step check, so a row here is always whole or blank.
function readExamples() {
  const out = [];
  for (const row of $("wzExamplesList").children) {
    const user = row.querySelector(".example-user").value.trim();
    const reply = row.querySelector(".example-reply").value.trim();
    if (user || reply) out.push({ user, character: reply });
  }
  return out;
}

// ---- Relationship speed

function relValue(key) {
  const field = REL[key];
  let n = Number($(field.range).value);
  if (!Number.isFinite(n)) n = field.fallback;
  return Math.max(1, Math.min(6, n));
}

function setRel(key, value) {
  const field = REL[key];
  const n = Math.max(1, Math.min(6, Number(value) || field.fallback));
  const text = n.toFixed(2);
  $(field.range).value = text;
  $(field.number).value = text;
}

function category(scale, bands) {
  const n = Math.max(1, Math.min(6, Number(scale) || 2));
  for (const [top, label] of bands) if (n <= top) return label;
  return bands[bands.length - 1][1];
}

function updateSpeedPreview() {
  const trust = category(relValue("trust"), REL.trust.bands);
  const affection = category(relValue("affection"), REL.affection.bands);
  $("wzSpeedPreview").textContent = tr("Trust: {trust} · Affection: {affection}", { trust: tr(trust), affection: tr(affection) });
}

// ---- Name and colour

function colourValue() {
  return $("wzColourText").value.trim() || $("wzColour").value;
}

// The colour the placeholder face uses. Only a full hex code counts.
function faceColour() {
  const typed = $("wzColourText").value.trim();
  return /^#[0-9a-f]{6}$/i.test(typed) ? typed : "";
}

// ---- Picture and backgrounds

function setIcon(file) {
  touch();
  if (iconUrl) {
    URL.revokeObjectURL(iconUrl);
    iconUrl = "";
  }
  iconFile = file;
  const box = $("wzIconPreview");
  box.replaceChildren();
  box.classList.toggle("placeholder", !file);
  if (file) {
    iconUrl = URL.createObjectURL(file);
    const img = document.createElement("img");
    img.alt = "";
    img.src = iconUrl;
    box.appendChild(img);
  } else {
    box.appendChild(faceIcon(faceColour()));
  }
  $("wzIconClear").hidden = !file;
}

// The placeholder face takes the colour, so it is drawn again when the colour changes.
function refreshPlaceholder() {
  if (!iconFile) setIcon(null);
}

function backgroundTooBig(file) {
  return file.size > (String(file.type || "").startsWith("video/") ? BG_VIDEO_MAX : BG_IMAGE_MAX);
}

function queueBackgrounds(files) {
  const room = BG_MAX - bgFiles.length;
  const tooBig = files.some(backgroundTooBig);
  $("wzBgErr").textContent = tooBig ? tr("A background video can be up to 50 MB, and an animated picture up to 100 MB.")
    : files.length > room ? tr("Only {n} more fit (up to 10).", { n: Math.max(0, room) }) : "";
  if (!tooBig) bgFiles = bgFiles.concat(files.slice(0, Math.max(0, room)));
  $("wzBgFile").value = "";
  renderBackgrounds();
}

function bgUrl(file) {
  if (!bgUrls.has(file)) bgUrls.set(file, URL.createObjectURL(file));
  return bgUrls.get(file);
}

function removeBackground(file) {
  bgFiles = bgFiles.filter((f) => f !== file);
  if (bgUrls.has(file)) {
    URL.revokeObjectURL(bgUrls.get(file));
    bgUrls.delete(file);
  }
  renderBackgrounds();
}

function renderBackgrounds() {
  touch();
  const box = $("wzBgList");
  box.replaceChildren();
  if (!bgFiles.length) box.appendChild(el("p", tr("No backgrounds yet."), "muted"));
  for (const file of bgFiles) {
    const thumb = el("div", "", "bg-thumb");
    let media;
    if (/^video\//.test(file.type)) {
      media = document.createElement("video");
      media.muted = true;
      media.loop = true;
      media.autoplay = true;
      media.playsInline = true;
      media.setAttribute("muted", "");
      media.setAttribute("playsinline", "");
      media.setAttribute("aria-hidden", "true");
      media.src = bgUrl(file);
    } else {
      media = document.createElement("img");
      media.alt = "";
      media.src = bgUrl(file);
    }
    const rm = el("button", tr("Remove"), "ghost small");
    rm.type = "button";
    rm.setAttribute("aria-label", tr("Remove this background"));
    rm.addEventListener("click", () => removeBackground(file));
    thumb.append(media, rm);
    box.appendChild(thumb);
  }
  $("wzBgNote").textContent = tr("{n} of {max} backgrounds.", { n: bgFiles.length, max: BG_MAX });
}

// ---- Review

// Each part as the review shows it. A row with no step has no Change button (the lorebook comes from the import).
function renderSummary() {
  const list = $("wzSummary");
  list.replaceChildren();
  const name = $("wzName").value.trim();
  const card = $("wzCard").value.trim();
  const headline = $("wzHeadline").value.trim();
  const rows = [
    ["name", tr("Name"), name || tr("Not named yet")],
    ["name", tr("Colour"), colourValue()],
  ];
  if (me && me.is_admin) rows.push(["name", tr("Sandbox"), $("wzSandbox").checked ? tr("On") : tr("Off")]);
  rows.push(
    ["card", tr("Card"), card ? tr("{n} characters", { n: card.length }) : tr("Not written yet")],
    ["profile", tr("Headline"), headline || tr("None")],
    ["profile", tr("Tags"), tags.length ? tags.join(", ") : tr("None")],
    ["examples", tr("Examples"), readExamples().length ? tr("{n} exchanges", { n: readExamples().length }) : tr("None")],
    ["context", tr("Context"), $("wzContext").value.trim() ? tr("Written") : tr("None")],
    ["context", tr("Author's note"), $("wzAuthorNote").value.trim() ? tr("Written") : tr("None")],
    ["speed", tr("Relationship speed"), $("wzSpeedPreview").textContent],
    ["pictures", tr("Picture"), iconFile ? tr("Chosen") : tr("Placeholder")],
    ["pictures", tr("Backgrounds"), tr("{n} of {max} backgrounds.", { n: bgFiles.length, max: BG_MAX })],
  );
  if (palaceEntries.length) rows.push([null, tr("Lorebook"), tr("{n} entries for the Mind Palace", { n: palaceEntries.length })]);
  for (const [step, label, value] of rows) {
    const item = el("li", "", "wz-sum");
    item.append(el("span", label, "wz-sum-label"), el("span", value, "wz-sum-value"));
    if (step) {
      const change = el("button", tr("Change"), "ghost small");
      change.type = "button";
      change.setAttribute("aria-label", tr("Change {part}", { part: label }));
      change.addEventListener("click", () => goTo(indexOfStep(step)));
      item.appendChild(change);
    }
    list.appendChild(item);
  }
}

// ---- Create

// An imported lorebook entry in the shape the Mind Palace saves, with the same fields the editor's entry rows keep.
function palaceEntry(entry) {
  return {
    id: entry.id || "",
    auto: !!entry.auto,
    title: entry.title || "",
    keys: entry.keys || [],
    secondary: entry.secondary || [],
    logic: entry.logic || "and_any",
    content: entry.content || "",
    constant: !!entry.constant,
    enabled: entry.enabled !== false,
    whole: entry.whole !== false,
    depth: Number(entry.depth) || 6,
    order: Number.isFinite(Number(entry.order)) ? Number(entry.order) : 100,
  };
}

async function create() {
  setBusy(true);
  $("wzErr").textContent = "";
  let saved;
  try {
    saved = await api("POST", "/api/admin/characters", {
      name: $("wzName").value.trim(),
      card: $("wzCard").value,
      context: $("wzContext").value,
      author_note: $("wzAuthorNote").value,
      relationship: { trust: relValue("trust"), affection: relValue("affection") },
      colour: colourValue(),
      sandbox: $("wzSandbox").checked,
      profile: profileFromForm(),
      examples: readExamples(),
    });
  } catch (err) {
    $("wzErr").textContent = err.message;
    setBusy(false);
    return;
  }
  // The character exists from here on. A part that fails is reported on the done screen, not retried.
  const base = "/api/admin/characters/" + encodeURIComponent(saved.name);
  const notes = [];
  if (iconFile) {
    try {
      await sendFile(base + "/icon", iconFile, { fallback: tr("the picture was not saved") });
    } catch (err) {
      notes.push(tr("The picture was not saved: {error}", { error: err.message }));
    }
  }
  if (palaceEntries.length) {
    try {
      await api("POST", base + "/palace", { entries: palaceEntries.map(palaceEntry), recursive: false, budget: PALACE_BUDGET });
    } catch (err) {
      notes.push(tr("The lorebook was not saved: {error}", { error: err.message }));
    }
  }
  for (const file of bgFiles) {
    try {
      await sendFile(base + "/backgrounds", file, { fallback: tr("a background did not upload") });
    } catch (err) {
      notes.push(tr("A background did not upload: {error}", { error: err.message }));
      break;
    }
  }
  showDone(tr("Created {name}.", { name: saved.name }), notes, saved.name);
}

function profileFromForm() {
  flushTagText();
  return { headline: $("wzHeadline").value, info: $("wzInfo").value, tags: tags.slice() };
}

function showDone(title, notes, name) {
  settled = true;
  clearDraft();
  $("wzHead").hidden = true;
  $("wzForm").hidden = true;
  $("wzDoneTitle").textContent = title;
  // The editor opens on the chat page, so the link goes there and asks for this character.
  const edit = $("wzDoneEdit");
  edit.href = "/chat?edit=" + encodeURIComponent(name);
  edit.textContent = tr("Edit {name}", { name: name });
  edit.hidden = false;
  const list = $("wzDoneNotes");
  list.replaceChildren();
  for (const note of notes) if (note) list.appendChild(el("li", note));
  list.hidden = !list.children.length;
  $("wzDone").hidden = false;
  setBusy(false);
}

// ---- Drafts: kept in this browser until the character is created, so a reload loses nothing.

function draftKey() {
  return DRAFT_PREFIX + (me ? me.username : "");
}

function textOf(value) {
  return typeof value === "string" ? value : "";
}

function collectDraft() {
  return {
    v: 1,
    step: stepIndex,
    furthest,
    name: $("wzName").value,
    colour: $("wzColourText").value,
    sandbox: $("wzSandbox").checked,
    card: $("wzCard").value,
    headline: $("wzHeadline").value,
    info: $("wzInfo").value,
    tags: tags.slice(),
    examples: readExamples(),
    context: $("wzContext").value,
    authorNote: $("wzAuthorNote").value,
    trust: relValue("trust"),
    affection: relValue("affection"),
    palace: palaceEntries,
    hadPicture: !!iconFile,
    backgrounds: bgFiles.length,
  };
}

// Whether anything typed here would be kept in a draft.
function hasText() {
  const d = collectDraft();
  return Boolean(d.name.trim() || d.card.trim() || d.headline.trim() || d.info.trim() || d.context.trim()
    || d.authorNote.trim() || d.tags.length || d.examples.length || d.palace.length);
}

// Saves the draft in this browser. Returns false when the browser refuses storage, so the page keeps its warning.
function saveDraft() {
  if (settled) return true;
  try {
    if (hasText()) localStorage.setItem(draftKey(), JSON.stringify(collectDraft()));
    else localStorage.removeItem(draftKey());
    dirty = false;
    return true;
  } catch (err) {
    return false;
  }
}

function clearDraft() {
  dirty = false;
  clearTimeout(draftTimer);
  try {
    localStorage.removeItem(draftKey());
  } catch (err) {
    // storage refused: there is nothing saved to clear
  }
}

// Called on every change. The draft is saved a moment after the last change.
function touch() {
  if (quiet || settled) return;
  dirty = true;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, DRAFT_DELAY);
}

// Puts back a draft saved earlier, if there is one, and opens the wizard at the step it was on. Returns true if it did.
function restoreDraft() {
  let draft = null;
  try {
    draft = JSON.parse(localStorage.getItem(draftKey()) || "null");
  } catch (err) {
    draft = null;   // storage refused or damaged: start blank
  }
  if (!draft || typeof draft !== "object" || draft.v !== 1) return false;
  $("wzName").value = textOf(draft.name);
  const colour = textOf(draft.colour).trim();
  $("wzColourText").value = colour;
  if (/^#[0-9a-fA-F]{6}$/.test(colour)) $("wzColour").value = colour.toLowerCase();
  $("wzSandbox").checked = draft.sandbox === true;
  $("wzCard").value = textOf(draft.card);
  $("wzHeadline").value = textOf(draft.headline);
  $("wzInfo").value = textOf(draft.info);
  setTags(Array.isArray(draft.tags) ? draft.tags.filter((t) => typeof t === "string") : []);
  renderExamples(Array.isArray(draft.examples) ? draft.examples : []);
  $("wzContext").value = textOf(draft.context);
  $("wzAuthorNote").value = textOf(draft.authorNote);
  setRel("trust", draft.trust);
  setRel("affection", draft.affection);
  updateSpeedPreview();
  palaceEntries = Array.isArray(draft.palace) ? draft.palace : [];
  refreshPlaceholder();
  furthest = Math.max(0, Math.min(sections().length - 1, Number(draft.furthest) || 0));
  goTo(Math.max(0, Math.min(furthest, Number(draft.step) || 0)));
  const lost = [];
  if (draft.hadPicture) lost.push(tr("the picture"));
  if (draft.backgrounds) lost.push(tr("the backgrounds"));
  $("wzDraftNoticeText").textContent = lost.length
    ? tr("Restored your unfinished character. Pictures and backgrounds are not kept in a draft, so choose {what} again.", { what: lost.join(" and ") })
    : tr("Restored your unfinished character from earlier in this browser.");
  $("wzDraftNotice").hidden = false;
  return true;
}

function discardDraft() {
  clearDraft();
  settled = true;
  location.reload();
}

// Cancel, Back to chat and Log out keep the draft and leave without the warning. If the browser refuses storage, the
// user is asked first, because leaving would lose what is typed.
function leaveWithDraft(event) {
  if (settled || leaving) return;
  if (!saveDraft() && hasText() && !window.confirm(tr("This draft could not be saved in this browser. Leave anyway?"))) {
    event.preventDefault();
    return;
  }
  leaving = true;
}

// Any other way out of the page, such as a closed tab or the browser's back button, warns while text is not yet saved.
window.addEventListener("beforeunload", (event) => {
  if (leaving || settled || !dirty || !hasText()) return;
  event.preventDefault();
  event.returnValue = "";
});

// ---- Wiring

$("wzForm").addEventListener("submit", onSubmit);
$("wzForm").addEventListener("input", touch);
$("wzForm").addEventListener("change", touch);
$("wzCancel").addEventListener("click", leaveWithDraft);
for (const link of document.querySelectorAll('header a[href="/chat"]')) link.addEventListener("click", leaveWithDraft);
$("wzDiscard").addEventListener("click", discardDraft);
$("wzBack").addEventListener("click", () => {
  if (!busy && stepIndex > 0) goTo(stepIndex - 1);
});
for (const btn of document.querySelectorAll(".wz-route")) {
  btn.addEventListener("click", () => chooseRoute(btn.dataset.route));
}
$("wzDraftBtn").addEventListener("click", draftFromFile);
$("wzImportBtn").addEventListener("click", importCard);
$("wzZipFile").addEventListener("change", () => {
  const f = $("wzZipFile").files[0];
  const tooBig = !!f && f.size > ZIP_MAX;
  $("wzZipMsg").textContent = tooBig ? tr("Archives can be up to 60 MB.") : "";
  $("wzZipBtn").disabled = !f || tooBig;
});
$("wzZipBtn").addEventListener("click", importZip);
$("wzExample").addEventListener("click", () => loadExample().catch((err) => { $("wzErr").textContent = err.message; }));
$("wzAuto").addEventListener("click", autoFill);
$("wzExampleAdd").addEventListener("click", () => {
  const row = addExampleRow(null);
  if (row) row.querySelector(".example-user").focus();
});
$("wzTagText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    addTag(e.target.value);
  } else if (e.key === "Backspace" && !e.target.value && tags.length) {
    tags.pop();
    renderTags();
  }
});
$("wzTagText").addEventListener("blur", flushTagText);
$("wzTagText").addEventListener("focus", loadTagSuggestions);
$("wzTagBox").addEventListener("click", (e) => {
  if (e.target === $("wzTagBox")) $("wzTagText").focus();
});

$("wzColour").addEventListener("input", () => {
  $("wzColourText").value = $("wzColour").value;
  refreshPlaceholder();
});
$("wzColourText").addEventListener("input", () => {
  const typed = $("wzColourText").value.trim();
  const ok = /^#[0-9a-fA-F]{6}$/.test(typed);
  if (ok) $("wzColour").value = typed.toLowerCase();
  $("wzColourText").classList.toggle("invalid", !!typed && !ok);
  refreshPlaceholder();
});

for (const key of Object.keys(REL)) {
  const field = REL[key];
  $(field.range).addEventListener("input", () => {
    setRel(key, $(field.range).value);
    updateSpeedPreview();
  });
  $(field.number).addEventListener("input", () => {
    const raw = $(field.number).value;
    const n = Number(raw);
    if (raw !== "" && n >= 1 && n <= 6) $(field.range).value = n.toFixed(2);
    updateSpeedPreview();
  });
  $(field.number).addEventListener("change", () => {
    const raw = $(field.number).value;
    setRel(key, raw === "" ? field.fallback : raw);
    updateSpeedPreview();
  });
}

$("wzIconFile").addEventListener("change", () => {
  const file = $("wzIconFile").files[0];
  if (!file) return;
  if (file.size > ICON_MAX) {
    $("wzIconErr").textContent = tr("Icon uploads can be up to 10 MB.");
    $("wzIconFile").value = "";
    return;
  }
  $("wzIconErr").textContent = "";
  setIcon(file);
});
$("wzIconClear").addEventListener("click", () => {
  $("wzIconFile").value = "";
  $("wzIconErr").textContent = "";
  setIcon(null);
});
$("wzBgFile").addEventListener("change", () => {
  const files = Array.from($("wzBgFile").files || []);
  if (files.length) queueBackgrounds(files);
});

$("wzAgain").addEventListener("click", () => { location.href = "/create/character"; });
$("logoutBtn").addEventListener("click", async (event) => {
  leaveWithDraft(event);
  if (event.defaultPrevented) return;
  try { await api("POST", "/api/logout"); } catch (err) { /* the session is gone either way */ }
  location.href = "/login";
});

async function init() {
  try {
    me = await api("GET", "/api/me");
  } catch (err) {
    fail(err.message);
    return;
  }
  if (me.must_change) {
    location.href = "/chat";
    return;
  }
  $("who").textContent = me.username;
  if (me.is_admin) $("adminLink").hidden = false;
  $("wzSandboxRow").hidden = !me.is_admin;
  for (const key of Object.keys(REL)) setRel(key, REL[key].fallback);
  updateSpeedPreview();
  setIcon(null);
  renderExamples([]);
  renderBackgrounds();
  buildPills();
  loadTagSuggestions();
  $("loading").hidden = true;
  $("wizard").hidden = false;
  if (!restoreDraft()) goTo(0);
  quiet = false;
}

init().catch((err) => fail(err.message));
