async function api(path, opts) {
  const base = { credentials: "same-origin", headers: { "X-Requested-With": "roleplay" } };
  const o = opts || {};
  const headers = Object.assign({}, base.headers, o.headers || {});
  const r = await fetch(path, Object.assign({}, base, o, { headers }));
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : "Request failed");
    err.status = r.status;
    throw err;
  }
  return r.json();
}
async function load() {
  const d = await api("/api/credits");
  document.getElementById("tier").textContent = d.tier || "none";
  document.getElementById("balance").textContent = d.balance;
  const sec = document.getElementById("adminSection");
  // check admin via /api/me
  try {
    const me = await api("/api/me");
    document.getElementById("who").textContent = me.username;
    document.getElementById("adminLink").hidden = !me.is_admin;
    if (me.is_admin) {
      sec.hidden = false;
      const r = await api("/api/admin/credit-requests");
      const list = document.getElementById("reqList");
      list.textContent = "";
      for (const q of r.requests || []) {
        const row = document.createElement("div");
        row.className = "balance-req";
        const label = document.createElement("span");
        label.textContent = `${q.username}: ${q.tokens} tokens @ ${q.price_mcc} MCC (${q.status})`;
        row.appendChild(label);
        if (q.status === "pending") {
          const sp = document.createElement("span"); sp.className = "spacer"; row.appendChild(sp);
          const a = document.createElement("button"); a.className = "ghost small"; a.textContent = "Approve";
          a.onclick = async () => { await api("/api/admin/credit-requests/" + q.id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: true }) }); load(); };
          const rej = document.createElement("button"); rej.className = "ghost small"; rej.textContent = "Reject";
          rej.onclick = async () => { await api("/api/admin/credit-requests/" + q.id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: false }) }); load(); };
          row.appendChild(a); row.appendChild(rej);
        }
        list.appendChild(row);
      }
    } else {
      sec.hidden = true;
    }
  } catch (e) { sec.hidden = true; }
}
document.getElementById("backBtn").addEventListener("click", () => {
  if (history.length > 1) history.back(); else location.href = "/";
});
document.getElementById("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch (e) { /* already signed out */ }
  location.href = "/login";
});
document.getElementById("topupBtn").addEventListener("click", () => document.getElementById("topupDlg").showModal());
document.getElementById("topupCancel").addEventListener("click", () => document.getElementById("topupDlg").close());
document.getElementById("topupForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("topupMsg");
  msg.textContent = "";
  try {
    const res = await api("/api/credits/request", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tokens: Number(document.getElementById("tTokens").value),
                             price: Number(document.getElementById("tPrice").value) }) });
    document.getElementById("msg").textContent = res && res.auto ? "Credits added." : "Request sent.";
    document.getElementById("topupDlg").close();
    load();
  } catch (err) { msg.textContent = err.message; }
});
load().catch((err) => {
  if (err && err.status === 401) { location.href = "/login"; return; }
  const m = document.getElementById("msg");
  if (m) m.textContent = err && err.message ? err.message : "Could not load the balance.";
});