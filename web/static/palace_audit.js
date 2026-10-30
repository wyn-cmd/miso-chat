// The Mind Palace check: the upkeep view for the open character's palace (web/palace_audit.py). It lists entries that
// nothing has used, entries whose keys come up in most messages, and capitalised names that appear nowhere else in the
// lore. Every action goes through the server and reloads the view. Values go into textContent, never innerHTML.
const AUDIT_FLAG_LABEL = {
  orphaned: "Orphaned",
  over_firing: "Over-firing",
  dead_reference: "Possible dead reference",
  never_triggered: "Never triggered",
};
let auditFor = "";   // the character the check was opened for

function auditUrl(path) {
  return path + "?character=" + encodeURIComponent(auditFor);
}

async function openAudit() {
  if (!current) return;
  auditFor = current;
  $("auditMsg").textContent = "";
  $("auditSummary").textContent = "";
  $("auditList").replaceChildren(el("p", tr("Checking..."), "muted"));
  if (!$("auditDlg").open) $("auditDlg").showModal();
  await loadAudit();
}

async function loadAudit() {
  try {
    renderAudit(await api("GET", auditUrl("/api/character-palace/audit")));
  } catch (err) {
    $("auditList").replaceChildren();
    $("auditMsg").textContent = err.message;
  }
}

function renderAudit(data) {
  const c = data.counts;
  $("auditSummary").textContent = tr("{n} entries checked over {turns} messages. Orphaned: {o}. Over-firing: {f}. Possible dead references: {d}. Core memory: {k}.", {
    n: c.entries, turns: data.turns, o: c.orphaned, f: c.over_firing, d: c.dead_references, k: c.core,
  });
  const list = $("auditList");
  list.replaceChildren();
  const shown = data.entries.filter((row) => row.flags.length || row.core);
  if (!data.turns) {
    list.appendChild(el("p", tr("No messages have been checked yet. The check starts counting from your next message."), "muted"));
  } else if (!shown.length) {
    list.appendChild(el("p", tr("No entry needs attention."), "muted"));
  }
  for (const row of shown) list.appendChild(auditCard(row, data.limits));
}

function auditStats(row, limits) {
  const parts = [];
  if (row.constant) parts.push(tr("Always on."));
  if (row.hits === 0) {
    parts.push(tr("Never triggered in {n} messages.", { n: row.seen }));
  } else if (row.days_unused < 1) {
    parts.push(tr("Triggered {hits} times. Last used today.", { hits: row.hits }));
  } else {
    parts.push(tr("Triggered {hits} times. Last used {days} days ago.", { hits: row.hits, days: Math.round(row.days_unused) }));
  }
  if (row.rate !== null) {
    parts.push(tr("Keys came up in {rate}% of the last {n} messages, about {chars} characters a reply.", {
      rate: row.rate, n: limits.recent_turns, chars: row.per_reply_chars,
    }));
  }
  parts.push(tr("{chars} characters when it fires.", { chars: row.chars }));
  return parts.join(" ");
}

function auditCard(row, limits) {
  const card = el("div", "", "audit-entry");
  const head = el("div", "", "audit-head");
  head.appendChild(el("strong", row.title));
  for (const flag of row.flags) head.appendChild(el("span", tr(AUDIT_FLAG_LABEL[flag]), "audit-flag audit-" + flag));
  if (row.core) head.appendChild(el("span", tr("Core memory"), "audit-flag audit-core"));
  if (!row.enabled) head.appendChild(el("span", tr("Off"), "audit-flag"));
  card.appendChild(head);
  card.appendChild(el("p", auditStats(row, limits), "muted"));
  if (row.refs.length) {
    card.appendChild(el("p", tr("Names not found elsewhere: {names}", { names: row.refs.join(", ") }), "muted"));
  }
  const actions = el("div", "", "audit-actions");
  actions.appendChild(auditButton(row.core ? tr("Remove core mark") : tr("Mark as core memory"),
    () => setAuditCore(row.key, !row.core)));
  if (row.enabled) actions.appendChild(auditButton(tr("Turn off"), () => changeAuditEntry(row, "off")));
  actions.appendChild(auditButton(tr("Delete"), () => changeAuditEntry(row, "delete"), true));
  card.appendChild(actions);
  return card;
}

function auditButton(label, onClick, danger = false) {
  const btn = el("button", label, danger ? "danger small" : "ghost small");
  btn.type = "button";
  btn.addEventListener("click", () => {
    $("auditMsg").textContent = "";
    onClick().catch((err) => { $("auditMsg").textContent = err.message; });
  });
  return btn;
}

async function setAuditCore(key, core) {
  await api("POST", auditUrl("/api/character-palace/core"), { key, core });
  await loadAudit();
}

async function changeAuditEntry(row, action) {
  if (action === "delete" && !window.confirm(tr("Delete the entry '{title}'? This cannot be undone.", { title: row.title }))) return;
  await api("POST", auditUrl("/api/character-palace/entry"), { key: row.key, action });
  $("auditMsg").textContent = action === "delete" ? tr("Deleted.") : tr("Turned off.");
  await loadAudit();
}

$("palAudit").addEventListener("click", () => { openAudit(); });
$("auditClose").addEventListener("click", () => $("auditDlg").close());
