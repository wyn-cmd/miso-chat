// Admin stats: usage over time with a provider-key filter, today's usage, character and group tables.
// Every value from the server is written with textContent or created as DOM/SVG nodes (never innerHTML).
const SVG_NS = "http://www.w3.org/2000/svg";
const PROVIDER_NAMES = {
  all: "All providers", gemini: "Gemini key", deepseek: "DeepSeek key", local: "Local LLM key", claude: "Claude Code",
};
const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n || 0).toLocaleString();
const money = (value, currency) => `${Number(value || 0).toFixed(4)} ${currency.toUpperCase()}`;
let lastPayload = null;
let currency = localStorage.getItem("miso.stats.currency") === "cny" ? "cny" : "usd";

function row(cells) {
  const tr = document.createElement("tr");
  for (const c of cells) tr.appendChild(el("td", c));
  return tr;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function svgText(x, y, anchor, text) {
  const t = svgEl("text", { x, y, "text-anchor": anchor, class: "axis" });
  t.textContent = text;
  return t;
}

// Line chart for one measure. Hover a point to see its day and value.
function drawChart(holder, label, points, key) {
  holder.textContent = "";
  const W = 760, H = 220, L = 58, R = 12, T = 12, B = 30;
  const values = points.map((p) => Number(p[key] || 0));
  const max = Math.max(1, ...values);
  const n = values.length;
  const x = (i) => (n <= 1 ? L : L + (i * (W - L - R)) / (n - 1));
  const y = (v) => H - B - (v / max) * (H - T - B);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label });
  for (const frac of [0, 0.5, 1]) {
    const yy = y(max * frac);
    svg.appendChild(svgEl("line", { x1: L, x2: W - R, y1: yy, y2: yy, class: "grid" }));
    svg.appendChild(svgText(L - 6, yy + 4, "end", fmt(Math.round(max * frac))));
  }
  if (n) {
    const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    svg.appendChild(svgEl("path", { d, class: "series", fill: "none" }));
    values.forEach((v, i) => {
      const c = svgEl("circle", { cx: x(i).toFixed(1), cy: y(v).toFixed(1), r: 3.5, class: "pt" });
      const title = svgEl("title", {});
      title.textContent = window.tr("{day}: {value}", { day: points[i].day, value: fmt(v) });
      c.appendChild(title);
      svg.appendChild(c);
    });
    const short = (day) => day.slice(0, 5);   // dd/mm
    const mid = Math.floor((n - 1) / 2);
    svg.appendChild(svgText(x(0), H - 8, "start", short(points[0].day)));
    if (n > 2) svg.appendChild(svgText(x(mid), H - 8, "middle", short(points[mid].day)));
    if (n > 1) svg.appendChild(svgText(x(n - 1), H - 8, "end", short(points[n - 1].day)));
  }
  holder.appendChild(svg);
}

function render(d) {
  const label = window.tr(PROVIDER_NAMES[d.provider] || PROVIDER_NAMES.all);

  const t = d.today;
  const pct = Math.min(100, (t.global_requests / Math.max(1, t.global_cap)) * 100);
  $("todayBar").style.width = pct.toFixed(1) + "%";
  $("todayText").textContent = window.tr("All accounts: {used} of {cap} API calls ({percent}%).", { used: fmt(t.global_requests), cap: fmt(t.global_cap), percent: pct.toFixed(1) }) + " "
    + window.tr("The count resets at midnight Pacific.");

  $("noteLine").textContent = window.tr("Showing {label}, last {days} days. {note}", { label: label, days: d.days, note: d.note });
  $("hTokens").textContent = window.tr("Tokens per day · {label}", { label: label });
  $("hRequests").textContent = window.tr("API calls per day · {label}", { label: label });
  $("hMessages").textContent = window.tr("Messages per day · {label}", { label: label });
  drawChart($("chartTokens"), window.tr("Tokens per day"), d.series, "tokens");
  drawChart($("chartRequests"), window.tr("API calls per day"), d.series, "requests");
  drawChart($("chartMessages"), window.tr("Messages per day"), d.series, "messages");

  const totals = { requests: 0, tokens: 0, messages: 0 };
  const tb = $("totalsBody");
  tb.textContent = "";
  for (const p of d.providers) {
    const v = d.totals[p];
    totals.requests += v.requests;
    totals.tokens += v.tokens;
    totals.messages += v.messages;
    const line = row([window.tr(PROVIDER_NAMES[p]), fmt(v.requests), fmt(v.tokens), fmt(v.messages)]);
    if (p === d.provider) line.classList.add("active");
    tb.appendChild(line);
  }
  tb.appendChild(row([window.tr("Total"), fmt(totals.requests), fmt(totals.tokens), fmt(totals.messages)]));

  const ub = $("usersBody");
  ub.textContent = "";
  if (!t.users.length) ub.appendChild(row([window.tr("No usage today for this key"), "", "", ""]));
  for (const u of t.users) ub.appendChild(row([u.username, fmt(u.requests), fmt(u.tokens), fmt(u.messages)]));

  const cb = $("charBody");
  cb.textContent = "";
  if (!d.characters.length) cb.appendChild(row([window.tr("No character history yet"), "", "", "", "", ""]));
  for (const c of d.characters) {
    cb.appendChild(row([c.name, fmt(c.accounts), fmt(c.messages), fmt(c.history_tokens), fmt(c.memory_tokens), c.last_memory]));
  }

  const gb = $("groupBox");
  gb.textContent = "";
  if (!d.groups.length) gb.appendChild(el("p", window.tr("No group history yet."), "muted"));
  for (const g of d.groups) {
    const block = el("div", null, "group-block");
    block.appendChild(el("h3", g.name));
    const members = g.members.length ? g.members.join(", ") : window.tr("none listed");
    block.appendChild(el("p", window.tr("Members: {members}. {messages} messages, {history} history tokens, ", { members: members, messages: fmt(g.messages), history: fmt(g.history_tokens) })
      + window.tr("{memory} memory-bank tokens. Last memory update: {last}.", { memory: fmt(g.memory_tokens), last: g.last_memory }), "muted"));
    const ul = el("ul", null, "breakdown");
    for (const b of g.breakdown) ul.appendChild(el("li", window.tr("{speaker}: {messages} msgs ({tokens} tokens)", { speaker: b.speaker, messages: fmt(b.messages), tokens: fmt(b.tokens) })));
    block.appendChild(ul);
    gb.appendChild(block);
  }

  renderSplit(d);
  renderDeepSeek(d);
}

// The cached/uncached/output split per provider key.
function renderSplit(d) {
  const body = $("splitBody");
  body.textContent = "";
  const sum = { input_tokens: 0, cached_tokens: 0, output_tokens: 0, seconds: 0 };
  for (const p of d.providers) {
    const v = d.totals[p] || {};
    const input = Number(v.input_tokens || 0);
    const cached = Math.min(Number(v.cached_tokens || 0), input);
    sum.input_tokens += input;
    sum.cached_tokens += cached;
    sum.output_tokens += Number(v.output_tokens || 0);
    sum.seconds += Number(v.seconds || 0);
    const line = row([window.tr(PROVIDER_NAMES[p]), fmt(input), fmt(cached), fmt(Math.max(0, input - cached)),
      fmt(v.output_tokens), Number(v.seconds || 0).toFixed(1)]);
    if (p === d.provider) line.classList.add("active");
    body.appendChild(line);
  }
  body.appendChild(row([window.tr("Total"), fmt(sum.input_tokens), fmt(sum.cached_tokens),
    fmt(Math.max(0, sum.input_tokens - sum.cached_tokens)), fmt(sum.output_tokens), sum.seconds.toFixed(1)]));
}

// DeepSeek cost estimate. Both currencies arrive from the server; the select only picks which to show.
function renderDeepSeek(d) {
  const body = $("dsBody");
  body.textContent = "";
  const ds = d.deepseek || { models: [], totals: { cost: {} } };
  if (!ds.models.length) {
    body.appendChild(row([window.tr("No DeepSeek usage in this range"), "", "", "", "", "", "", ""]));
    $("dsNote").textContent = "";
    return;
  }
  for (const m of ds.models) {
    const cached = Math.min(Number(m.cached_tokens || 0), Number(m.input_tokens || 0));
    const label = m.fallback_rates ? window.tr("{model} (fallback rates)", { model: m.model }) : m.model;
    body.appendChild(row([label, fmt(m.requests), fmt(cached), fmt(Math.max(0, Number(m.input_tokens) - cached)),
      fmt(m.output_tokens), fmt(m.peak_tokens), Number(m.seconds || 0).toFixed(1), money(m.cost[currency], currency)]));
  }
  const t = ds.totals;
  const line = row([window.tr("Total"), fmt(t.requests), fmt(t.cached_tokens),
    fmt(Math.max(0, Number(t.input_tokens || 0) - Number(t.cached_tokens || 0))), fmt(t.output_tokens),
    fmt(t.peak_tokens), Number(t.seconds || 0).toFixed(1), money(t.cost[currency], currency)]);
  line.classList.add("active");
  body.appendChild(line);
  const rates = (ds.prices && ds.prices["deepseek-flash"] && ds.prices["deepseek-flash"][currency]) || null;
  $("dsNote").textContent = rates
    ? window.tr("Per million tokens in {currency} at the peak rate: cached input {cached}, uncached input {uncached}, ", { currency: currency.toUpperCase(), cached: rates.peak.cache_hit, uncached: rates.peak.cache_miss })
      + window.tr("output {output}. Off-peak hours (01:00-04:00 and 06:00-10:00 UTC, Monday to Friday) bill half of this; your own plan is not included.", { output: rates.peak.output })
    : window.tr("No rates are loaded for this currency.");
}

async function load() {
  $("err").textContent = "";
  const provider = $("providerSel").value;
  const days = $("daysSel").value;
  try {
    const d = await api("GET", `/api/admin/stats?days=${encodeURIComponent(days)}&provider=${encodeURIComponent(provider)}`);
    lastPayload = d;
    render(d);
    $("stamp").textContent = window.tr("Updated {time}", { time: MISO_I18N.date(new Date(), { timeStyle: "medium" }) });
  } catch (err) {
    $("err").textContent = err.message;
  }
}

$("providerSel").addEventListener("change", load);
$("daysSel").addEventListener("change", load);
$("reloadBtn").addEventListener("click", load);
$("currencySel").value = currency;
$("currencySel").addEventListener("change", () => {
  currency = $("currencySel").value === "cny" ? "cny" : "usd";
  localStorage.setItem("miso.stats.currency", currency);
  if (lastPayload) renderDeepSeek(lastPayload);
});
load();
