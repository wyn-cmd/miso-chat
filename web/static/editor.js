// Character editor (admin): create and edit character cards, colours, context and icons.
// Every value goes into fields with .value or textContent, never innerHTML.
let editing = null;      // name of the character being edited, or null for a new one
let loadedColour = ""; // colour as it was loaded (may be a name such as 'Crimson')

function showEditor(show) {
  $("editorSection").hidden = !show;
  $("chatSection").hidden = show;
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
    setIconPreview(name, data.has_icon);
  } else {
    $("editorTitle").textContent = "New character";
    $("edName").value = "";
    $("edName").readOnly = false;
    $("edCard").value = "";
    $("edContext").value = "";
    $("edColour").value = "#808080";
    $("edColourText").value = "#808080";
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
    $("edOk").textContent = "Saved " + saved.name + ". Chats pick up the change on their next message.";
    $("edColourText").value = saved.colour || $("edColourText").value;
    await loadCharacters();
  } catch (err) {
    $("edErr").textContent = err.message;
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
