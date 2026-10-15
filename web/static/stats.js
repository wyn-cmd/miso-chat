// Admin stats: usage over time with a provider-key filter, today's usage, character and group tables.
// Every value from the server is written with textContent or created as DOM/SVG nodes (never innerHTML).
const SVG_NS = "http://www.w3.org/2000/svg";
const PROVIDER_NAMES = {
  all: "All providers", gemini: "Gemini key", deepseek: "DeepSeek key", local: "Local LLM key", claude: "Claude Code",
};
const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n || 0).toLocaleString();

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

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
      title.textContent = `${points[i].day}: ${fmt(v)}`;
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
  const label = PROVIDER_NAMES[d.provider] || PROVIDER_NAMES.all;

  const t = d.today;
  const pct = Math.min(100, (t.global_requests / Math.max(1, t.global_cap)) * 100);
  $("todayBar").style.width = pct.toFixed(1) + "%";
  $("todayText").textContent = `All accounts: ${fmt(t.global_requests)} of ${fmt(t.global_cap)} API calls (${pct.toFixed(1)}%). `
    + "The count resets at midnight Pacific.";

  $("noteLine").textContent = `Showing ${label}, last ${d.days} days. ${d.note}`;
  $("hTokens").textContent = `Tokens per day · ${label}`;
  $("hRequests").textContent = `API calls per day · ${label}`;
  $("hMessages").textContent = `Messages per day · ${label}`;
  drawChart($("chartTokens"), "Tokens per day", d.series, "tokens");
  drawChart($("chartRequests"), "API calls per day", d.series, "requests");
  drawChart($("chartMessages"), "Messages per day", d.series, "messages");

  const totals = { requests: 0, tokens: 0, messages: 0 };
  const tb = $("totalsBody");
  tb.textContent = "";
  for (const p of d.providers) {
    const v = d.totals[p];
    totals.requests += v.requests;
    totals.tokens += v.tokens;
    totals.messages += v.messages;
    const tr = row([PROVIDER_NAMES[p], fmt(v.requests), fmt(v.tokens), fmt(v.messages)]);
    if (p === d.provider) tr.classList.add("active");
    tb.appendChild(tr);
  }
  tb.appendChild(row(["Total", fmt(totals.requests), fmt(totals.tokens), fmt(totals.messages)]));

  const ub = $("usersBody");
  ub.textContent = "";
  if (!t.users.length) ub.appendChild(row(["No usage today for this key", "", "", ""]));
  for (const u of t.users) ub.appendChild(row([u.username, fmt(u.requests), fmt(u.tokens), fmt(u.messages)]));

  const cb = $("charBody");
  cb.textContent = "";
  if (!d.characters.length) cb.appendChild(row(["No character history yet", "", "", "", "", ""]));
  for (const c of d.characters) {
    cb.appendChild(row([c.name, fmt(c.accounts), fmt(c.messages), fmt(c.history_tokens), fmt(c.memory_tokens), c.last_memory]));
  }

  const gb = $("groupBox");
  gb.textContent = "";
  if (!d.groups.length) gb.appendChild(el("p", "No group history yet.", "muted"));
  for (const g of d.groups) {
    const block = el("div", null, "group-block");
    block.appendChild(el("h3", g.name));
    const members = g.members.length ? g.members.join(", ") : "none listed";
    block.appendChild(el("p", `Members: ${members}. ${fmt(g.messages)} messages, ${fmt(g.history_tokens)} history tokens, `
      + `${fmt(g.memory_tokens)} memory-bank tokens. Last memory update: ${g.last_memory}.`, "muted"));
    const ul = el("ul", null, "breakdown");
    for (const b of g.breakdown) ul.appendChild(el("li", `${b.speaker}: ${fmt(b.messages)} msgs (${fmt(b.tokens)} tokens)`));
    block.appendChild(ul);
    gb.appendChild(block);
  }
}

async function load() {
  $("err").textContent = "";
  const provider = $("providerSel").value;
  const days = $("daysSel").value;
  try {
    const d = await api("GET", `/api/admin/stats?days=${encodeURIComponent(days)}&provider=${encodeURIComponent(provider)}`);
    render(d);
    $("stamp").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (err) {
    $("err").textContent = err.message;
  }
}

$("providerSel").addEventListener("change", load);
$("daysSel").addEventListener("change", load);
$("reloadBtn").addEventListener("click", load);
load();
