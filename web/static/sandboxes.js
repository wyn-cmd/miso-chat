// Admin page: pick a character, then see every account's sandbox for it. Read-only.
// Everything from the model is put on the page with textContent, never innerHTML, so saved text can't run as markup.
const $ = (id) => document.getElementById(id);
let currentChar = "";
let currentUid = null;
let currentPath = "/";

function setErr(msg) { $("err").textContent = msg || ""; }

function nameOf(entry) { return typeof entry === "string" ? entry : (entry.name || ""); }

async function loadCharacters() {
  const data = await api("GET", "/api/characters");
  const picker = $("charPick");
  picker.textContent = "";
  const first = document.createElement("option");
  first.value = "";
  first.textContent = "Choose a character";
  picker.appendChild(first);
  for (const entry of data.solo || []) {
    const name = nameOf(entry);
    if (!name) continue;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    picker.appendChild(opt);
  }
}

async function showCharacter(name) {
  currentChar = name;
  currentUid = null;
  $("browseSection").hidden = true;
  $("accountsSection").hidden = true;
  if (!name) return;
  const data = await api("GET", "/api/admin/sandboxes/" + encodeURIComponent(name));
  $("accountsTitle").textContent = "Sandboxes for " + data.character;
  $("flagNote").textContent = data.enabled
    ? "This character's sandbox is switched on."
    : "This character's sandbox is switched off. Existing files are kept, and the model can't reach them.";
  const body = $("accounts");
  body.textContent = "";
  for (const u of data.users) {
    const tr = document.createElement("tr");
    const cells = [u.username, String(u.files), (u.bytes / 1024).toFixed(1) + " KB"];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    const td = document.createElement("td");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ghost small";
    b.textContent = "Browse";
    b.disabled = u.files === 0;
    b.title = u.files === 0 ? "No files yet" : "";
    b.addEventListener("click", () => openAccount(u.id, u.username));
    td.appendChild(b);
    tr.appendChild(td);
    body.appendChild(tr);
  }
  if (!data.users.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "muted";
    td.textContent = "No accounts yet.";
    tr.appendChild(td);
    body.appendChild(tr);
  }
  $("accountsSection").hidden = false;
}

function openAccount(uid, username) {
  currentUid = uid;
  $("browseTitle").textContent = username + "'s sandbox for " + currentChar;
  $("browseSection").hidden = false;
  browse("/").catch((err) => setErr(err.message));
}

async function browse(path) {
  setErr("");
  $("fileText").hidden = true;
  $("fileTitle").hidden = true;
  $("fileNote").textContent = "";
  const base = "/api/admin/sandboxes/" + encodeURIComponent(currentChar) + "/" + currentUid;
  const data = await api("GET", base + "/list?path=" + encodeURIComponent(path));
  currentPath = data.path;
  $("browsePath").textContent = currentPath;
  const list = $("listing");
  list.textContent = "";
  for (const e of data.entries) {
    const li = document.createElement("li");
    li.textContent = (e.type === "folder" ? "[folder] " : "") + e.name;
    li.className = e.type === "folder" ? "sandbox-folder" : "sandbox-item";
    li.tabIndex = 0;
    const child = (currentPath === "/" ? "" : currentPath) + "/" + e.name;
    const open = () => (e.type === "folder" ? browse(child) : readFile(child));
    li.addEventListener("click", () => open().catch((err) => setErr(err.message)));
    li.addEventListener("keydown", (ev) => { if (ev.key === "Enter") open().catch((err) => setErr(err.message)); });
    list.appendChild(li);
  }
  if (!data.entries.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "This folder is empty.";
    list.appendChild(li);
  }
  if (data.truncated) $("fileNote").textContent = "Only the first 200 entries are shown.";
  $("browseUp").onclick = () => {
    if (currentPath === "/") return;
    const cut = currentPath.lastIndexOf("/");
    browse(cut <= 0 ? "/" : currentPath.slice(0, cut)).catch((err) => setErr(err.message));
  };
}

async function readFile(path) {
  setErr("");
  const base = "/api/admin/sandboxes/" + encodeURIComponent(currentChar) + "/" + currentUid;
  const data = await api("GET", base + "/file?path=" + encodeURIComponent(path));
  $("fileTitle").textContent = data.path;
  $("fileTitle").hidden = false;
  const pre = $("fileText");
  pre.textContent = data.text;   // plain text only
  pre.hidden = false;
  $("fileNote").textContent = data.truncated ? "Showing the first 20,000 characters." : "";
}

document.addEventListener("DOMContentLoaded", () => {
  $("charPick").addEventListener("change", () => {
    showCharacter($("charPick").value).catch((err) => setErr(err.message));
  });
  loadCharacters().catch((err) => setErr(err.message));
});
