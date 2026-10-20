// Admin page: users, caps, password resets, and server-wide limits.
const $ = (id) => document.getElementById(id);

function capInput(u) {
  const input = el("input");
  input.type = "number";
  input.min = "0";
  input.value = u.daily_cap === null ? "" : String(u.daily_cap);
  input.placeholder = "default (" + u.effective_cap + ")";
  input.className = "cap";
  return input;
}

function button(label, onClick) {
  const b = el("button", label, "ghost small");
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

async function act(fn) {
  $("err").textContent = "";
  try {
    await fn();
    await loadUsers();
  } catch (err) {
    $("err").textContent = err.message;
  }
}

const numOrNull = (input) => (input.value === "" ? null : parseInt(input.value, 10));

function userRow(u) {
  const tr = el("tr");
  tr.appendChild(el("td", u.username));
  tr.appendChild(el("td", u.is_admin ? "admin" : "user"));
  const status = [];
  if (u.disabled) status.push("disabled");
  if (u.must_change) status.push("must change password");
  tr.appendChild(el("td", status.join(", ") || "active"));
  tr.appendChild(el("td", u.requests_today));
  const capCell = el("td");
  const cap = capInput(u);
  capCell.appendChild(cap);
  capCell.appendChild(button("save", () => act(() => api("POST", `/api/admin/users/${u.id}`,
    { daily_cap: cap.value === "" ? null : parseInt(cap.value, 10) }))));
  tr.appendChild(capCell);
  tr.appendChild(el("td", u.tokens_today.toLocaleString()));
  tr.appendChild(el("td", u.tokens_week.toLocaleString()));
  tr.appendChild(el("td", u.tokens_month.toLocaleString()));
  // token budgets: blank means no budget; a budget counts tokens the API reported for this account
  const budgetCell = el("td");
  const budgets = {};
  for (const [key, label] of [["daily", "day"], ["weekly", "week"], ["monthly", "month"]]) {
    const input = el("input");
    input.type = "number";
    input.min = "0";
    input.step = "1";
    input.className = "budget";
    input.placeholder = label;
    input.title = `${label} token budget (blank = no budget)`;
    const v = u.token_budgets[key];
    input.value = v === null || v === undefined ? "" : String(v);
    budgets[key] = input;
    budgetCell.appendChild(input);
  }
  budgetCell.appendChild(button("save", () => act(() => api("POST", `/api/admin/users/${u.id}`, {
    token_daily: numOrNull(budgets.daily),
    token_weekly: numOrNull(budgets.weekly),
    token_monthly: numOrNull(budgets.monthly),
  }))));
  tr.appendChild(budgetCell);
  const actions = el("td");
  actions.appendChild(button(u.disabled ? "enable" : "disable",
    () => act(() => api("POST", `/api/admin/users/${u.id}`, { disabled: !u.disabled }))));
  actions.appendChild(button(u.is_admin ? "remove admin" : "make admin",
    () => act(() => api("POST", `/api/admin/users/${u.id}`, { is_admin: !u.is_admin }))));
  actions.appendChild(button("reset password", () => {
    const pw = window.prompt(`New starting password for ${u.username} (8+ characters):`);
    if (pw) act(() => api("POST", `/api/admin/users/${u.id}`, { reset_password: pw }));
  }));
  tr.appendChild(actions);
  return tr;
}

async function loadUsers() {
  const data = await api("GET", "/api/admin/users");
  const body = $("users");
  body.textContent = "";
  for (const u of data.users) body.appendChild(userRow(u));
  $("globalUse").textContent = `All web users today: ${data.global_requests_today} requests`;
}

async function loadSettings() {
  const s = await api("GET", "/api/admin/settings");
  $("s_user_daily_cap").value = s.user_daily_cap;
  $("s_global_daily_cap").value = s.global_daily_cap;
  $("s_rpm_limit").value = s.rpm_limit;
  $("s_reply_min_tokens").value = s.reply_min_tokens;
  $("s_reply_max_tokens").value = s.reply_max_tokens;
  $("s_memory_enabled").checked = s.memory_enabled === "1";
  $("s_palace_enabled").checked = s.palace_enabled === "1";
}

$("createForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("createMsg").textContent = "";
  const cap = $("newCap").value;
  try {
    const r = await api("POST", "/api/admin/users", {
      username: $("newName").value.trim(),
      password: $("newPass").value,
      is_admin: $("newAdmin").checked,
      daily_cap: cap === "" ? null : parseInt(cap, 10),
    });
    $("createMsg").textContent = `Created ${r.username}. They'll be asked to change the password on first login.`;
    $("createForm").reset();
    await loadUsers();
  } catch (err) {
    $("createMsg").textContent = err.message;
  }
});

$("settingsForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("settingsMsg").textContent = "";
  try {
    await api("POST", "/api/admin/settings", {
      user_daily_cap: parseInt($("s_user_daily_cap").value, 10),
      global_daily_cap: parseInt($("s_global_daily_cap").value, 10),
      rpm_limit: parseInt($("s_rpm_limit").value, 10),
      reply_min_tokens: parseInt($("s_reply_min_tokens").value, 10),
      reply_max_tokens: parseInt($("s_reply_max_tokens").value, 10),
      memory_enabled: $("s_memory_enabled").checked,
      palace_enabled: $("s_palace_enabled").checked,
    });
    $("settingsMsg").textContent = "Saved.";
    await loadUsers();
  } catch (err) {
    $("settingsMsg").textContent = err.message;
  }
});

(async () => {
  try {
    await loadUsers();
    await loadSettings();
  } catch (err) {
    $("err").textContent = err.message;
  }
})();
