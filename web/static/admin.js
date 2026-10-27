// Admin page: users, caps, password resets, and server-wide limits.
const $ = (id) => document.getElementById(id);

function capInput(u) {
  const input = el("input");
  input.type = "number";
  input.min = "0";
  input.value = u.daily_cap === null ? "" : String(u.daily_cap);
  input.placeholder = window.tr("default ({n})", { n: u.effective_cap });
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

// The signed-in admin's account id, from the users list, so its row can offer two-factor set-up.
let meId = null;

// tokens used in a window, against its limit when there is one
function limitCell(used, limit) {
  const td = el("td");
  if (limit === null || limit === undefined) {
    td.textContent = used.toLocaleString("en");
    return td;
  }
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 100;
  td.textContent = window.tr("{used} / {limit} ({pct}%)", { used: used.toLocaleString(), limit: limit.toLocaleString(), pct: pct });
  return td;
}

function userRow(u) {
  const tr = el("tr");
  tr.appendChild(el("td", u.username));
  tr.appendChild(el("td", u.is_admin ? "admin" : "user"));
  const status = [];
  if (u.disabled) status.push(window.tr("disabled"));
  if (u.must_change) status.push(window.tr("must change password"));
  if (u.login_lock === "permanent") status.push(window.tr("sign-in locked until an admin unlocks it"));
  else if (u.login_lock === "timed") status.push(window.tr("sign-in locked until {when}", { when: MISO_I18N.date(new Date(u.locked_until * 1000)) }));
  if (u.blocked_ips && u.blocked_ips.length) status.push(trn("{n} address blocked from signing in", "{n} addresses blocked from signing in", u.blocked_ips.length));
  if (u.twofa === "on") status.push(window.tr("two-factor sign-in on") + (u.twofa_required ? " " + window.tr("(required by admin)") : ""));
  else if (u.twofa === "setup") status.push(window.tr("two-factor set-up not finished"));
  tr.appendChild(el("td", status.join(", ") || window.tr("active")));
  tr.appendChild(el("td", u.requests_today));
  const capCell = el("td");
  const cap = capInput(u);
  capCell.appendChild(cap);
  capCell.appendChild(button(window.tr("save"), () => act(() => api("POST", `/api/admin/users/${u.id}`,
    { daily_cap: cap.value === "" ? null : parseInt(cap.value, 10) }))));
  tr.appendChild(capCell);
  tr.appendChild(limitCell(u.tokens_today, u.budget_limits.daily));
  tr.appendChild(limitCell(u.tokens_week, u.budget_limits.weekly));
  tr.appendChild(limitCell(u.tokens_month, u.budget_limits.monthly));
  // tier: sets the monthly token cap (admins have none)
  const tierCell = el("td");
  {
    // admins can have a tier too; "none" leaves an admin with no cap. Admin rights are separate from the tier.
    const sel = el("select");
    const choices = u.is_admin ? ["", "free", "lite", "standard", "pro"] : ["free", "lite", "standard", "pro"];
    for (const t of choices) {
      const o = el("option", t ? window.tr(t) : window.tr("none (no cap)"));
      o.value = t;
      sel.appendChild(o);
    }
    sel.value = u.tier || "";
    if (!u.tier && !u.is_admin) sel.value = "lite";
    sel.title = u.tier_monthly_limit ? window.tr("monthly cap {n} tokens", { n: u.tier_monthly_limit.toLocaleString() }) : window.tr("no cap");
    sel.addEventListener("change", () => act(() => api("POST", `/api/admin/users/${u.id}`, { tier: sel.value || null })));
    tierCell.appendChild(sel);
  }
  tr.appendChild(tierCell);
  // token limits: a figure the admin sets for this account; blank uses the tier default (shown as the placeholder)
  const budgetCell = el("td");
  const budgets = {};
  for (const [key, label] of [["daily", "day"], ["weekly", "week"], ["monthly", "month"]]) {
    const input = el("input");
    input.type = "number";
    input.min = "0";
    input.step = "1";
    input.className = "budget";
    const effective = u.budget_limits[key];
    input.placeholder = effective === null || effective === undefined ? window.tr(label) : effective.toLocaleString();
    input.title = effective === null || effective === undefined
      ? window.tr("{label} token limit (blank = none)", { label: window.tr(label) })
      : window.tr("{label} token limit (blank = tier default, {n} tokens)", { label: window.tr(label), n: effective.toLocaleString() });
    const v = u.token_budgets[key];
    input.value = v === null || v === undefined ? "" : String(v);
    budgets[key] = input;
    budgetCell.appendChild(input);
  }
  budgetCell.appendChild(button(window.tr("save"), () => act(() => api("POST", `/api/admin/users/${u.id}`, {
    token_daily: numOrNull(budgets.daily),
    token_weekly: numOrNull(budgets.weekly),
    token_monthly: numOrNull(budgets.monthly),
  }))));
  tr.appendChild(budgetCell);
  const actions = el("td");
  if (u.id === meId && u.twofa !== "on") {
    actions.appendChild(button(window.tr("enable 2FA"), () => window.misoTwoFactor.setup(loadUsers)));
  }
  actions.appendChild(button(u.disabled ? window.tr("enable") : window.tr("disable"),
    () => act(() => api("POST", `/api/admin/users/${u.id}`, { disabled: !u.disabled }))));
  actions.appendChild(button(u.is_admin ? window.tr("remove admin") : window.tr("make admin"),
    () => act(() => api("POST", `/api/admin/users/${u.id}`, { is_admin: !u.is_admin }))));
  actions.appendChild(button(window.tr("reset password"), () => {
    const pw = window.prompt(window.tr("New starting password for {name} (10+ characters):", { name: u.username }));
    if (pw) act(() => api("POST", `/api/admin/users/${u.id}`, { reset_password: pw }));
  }));
  if (u.login_lock || (u.blocked_ips && u.blocked_ips.length)) actions.appendChild(button(window.tr("unlock sign-in"), () => act(() => api("POST", `/api/admin/users/${u.id}`, { unlock_sign_in: true }))));
  actions.appendChild(button(u.twofa_required ? window.tr("lift 2FA requirement") : window.tr("require 2FA"),
    () => act(() => api("POST", `/api/admin/users/${u.id}`, { require_2fa: !u.twofa_required }))));
  if (u.twofa !== "off") actions.appendChild(button(window.tr("reset 2FA"), () => {
    if (window.confirm(window.tr("Reset two-factor sign-in for {name}? They set up a new code at their next sign-in, and their sessions end.", { name: u.username })))
      act(() => api("POST", `/api/admin/users/${u.id}`, { reset_2fa: true }));
  }));
  tr.appendChild(actions);
  return tr;
}

async function loadUsers() {
  const data = await api("GET", "/api/admin/users");
  const body = $("users");
  body.textContent = "";
  meId = data.me_id;
  for (const u of data.users) body.appendChild(userRow(u));
  $("globalUse").textContent = window.tr("All web users today: {n} requests", { n: data.global_requests_today });
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
  $("s_episodes_enabled").checked = s.episodes_enabled === "1";
  $("s_dreams_enabled").checked = s.dreams_enabled === "1";
  $("s_dreams_in_novels").checked = s.dreams_in_novels === "1";
  $("s_mood_enabled").checked = s.mood_enabled === "1";
  $("s_attitude_enabled").checked = s.attitude_enabled === "1";
  $("s_event_memory_enabled").checked = s.event_memory_enabled === "1";
  $("s_memory_policy_enabled").checked = s.memory_policy_enabled === "1";
  $("s_dream_daily_cap").value = s.dream_daily_cap;
  $("s_dream_every_days").value = s.dream_every_days;
  $("s_budget_webhook_url").value = s.budget_webhook_url || "";
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
    $("createMsg").textContent = window.tr("Created {name}. They'll be asked to change the password on first login.", { name: r.username });
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
      episodes_enabled: $("s_episodes_enabled").checked,
      dreams_enabled: $("s_dreams_enabled").checked,
      dreams_in_novels: $("s_dreams_in_novels").checked,
      mood_enabled: $("s_mood_enabled").checked,
      attitude_enabled: $("s_attitude_enabled").checked,
      event_memory_enabled: $("s_event_memory_enabled").checked,
      memory_policy_enabled: $("s_memory_policy_enabled").checked,
      dream_daily_cap: parseInt($("s_dream_daily_cap").value, 10),
      dream_every_days: parseInt($("s_dream_every_days").value, 10),
      budget_webhook_url: $("s_budget_webhook_url").value.trim(),
    });
    $("settingsMsg").textContent = window.tr("Saved.");
    await loadUsers();
  } catch (err) {
    $("settingsMsg").textContent = err.message;
  }
});


// ---- Tiers: monthly token cap, allowed providers and blocked models per tier
const TIER_ORDER = ["free", "lite", "standard", "pro"];

async function loadTiers() {
  const data = await api("GET", "/api/admin/tiers");
  const body = $("tiers");
  body.textContent = "";
  for (const t of TIER_ORDER) {
    const cfg = data.tiers[t];
    const tr = el("tr");
    tr.dataset.tier = t;
    tr.appendChild(el("td", t));
    const use = data.usage[t];
    tr.appendChild(el("td", String(use.users)));
    tr.appendChild(el("td", `${use.tokens_today.toLocaleString("en")} / ${use.tokens_week.toLocaleString("en")} / ${use.tokens_month.toLocaleString("en")}`));
    const capCell = el("td");
    const cap = el("input");
    cap.type = "number";
    cap.min = "1";
    cap.step = "1";
    cap.value = String(cfg.monthly_tokens);
    cap.className = "cap";
    cap.dataset.field = "monthly_tokens";
    capCell.appendChild(cap);
    tr.appendChild(capCell);
    // daily and weekly limits as a percent of the monthly cap; the token amount each gives is shown beside it
    const shareInputs = {};
    const shareAmounts = {};
    const refreshShares = () => {
      for (const field of ["daily_pct", "weekly_pct"]) {
        const pct = parseInt(shareInputs[field].value, 10);
        const monthly = parseInt(cap.value, 10);
        shareAmounts[field].textContent = Number.isFinite(pct) && Number.isFinite(monthly)
          ? window.tr("= {n} tokens", { n: Math.floor((monthly * pct) / 100).toLocaleString() }) : "";
      }
    };
    cap.addEventListener("input", refreshShares);
    for (const [field, label] of [["daily_pct", "Daily"], ["weekly_pct", "Weekly"]]) {
      const shareCell = el("td");
      const input = el("input");
      input.type = "number";
      input.min = "1";
      input.max = "100";
      input.step = "1";
      input.value = String(cfg[field]);
      input.className = "cap";
      input.dataset.field = field;
      input.title = window.tr("{label} limit, as a percent of the monthly cap", { label: window.tr(label) });
      input.addEventListener("input", refreshShares);
      shareInputs[field] = input;
      shareAmounts[field] = el("span", "", "muted");
      shareCell.append(input, " % ", shareAmounts[field]);
      tr.appendChild(shareCell);
    }
    refreshShares();
    const provCell = el("td");
    for (const p of data.providers) {
      const label = el("label", "");
      label.className = "inline";
      const box = el("input");
      box.type = "checkbox";
      box.value = p;
      box.checked = cfg.providers.includes(p);
      box.dataset.field = "providers";
      label.appendChild(box);
      label.append(" " + p + " ");
      provCell.appendChild(label);
    }
    tr.appendChild(provCell);
    const modelCell = el("td");
    if (!data.models.length) modelCell.appendChild(el("span", window.tr("no models set up"), "muted"));
    for (const m of data.models) {
      const label = el("label", "");
      label.className = "inline";
      const box = el("input");
      box.type = "checkbox";
      box.value = m;
      box.checked = cfg.blocked_models.includes(m);
      box.dataset.field = "blocked_models";
      label.appendChild(box);
      label.append(" block " + m + " ");
      modelCell.appendChild(label);
    }
    tr.appendChild(modelCell);
    const saveCell = el("td");
    saveCell.appendChild(button(window.tr("save"), () => saveTier(tr)));
    tr.appendChild(saveCell);
    body.appendChild(tr);
  }
}

async function saveTier(tr) {
  const t = tr.dataset.tier;
  const msg = $("tiersMsg");
  msg.textContent = "";
  const cfg = {
    monthly_tokens: parseInt(tr.querySelector("[data-field=monthly_tokens]").value, 10),
    daily_pct: parseInt(tr.querySelector("[data-field=daily_pct]").value, 10),
    weekly_pct: parseInt(tr.querySelector("[data-field=weekly_pct]").value, 10),
    providers: [...tr.querySelectorAll("[data-field=providers]")].filter((b) => b.checked).map((b) => b.value),
    blocked_models: [...tr.querySelectorAll("[data-field=blocked_models]")].filter((b) => b.checked).map((b) => b.value),
  };
  try {
    await api("POST", "/api/admin/tiers", { tiers: { [t]: cfg } });
    msg.textContent = window.tr("Saved {tier}. Users on it see the new cap on their next request.", { tier: window.tr(t) });
    await loadTiers();
  } catch (err) {
    msg.textContent = err.message;
  }
}

(async () => {
  try {
    await loadUsers();
    await loadSettings();
    await loadTiers();
  } catch (err) {
    $("err").textContent = err.message;
  }
})();
