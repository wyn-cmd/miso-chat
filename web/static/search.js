// Global search: characters, worlds, Mind Palace entries and saved chat messages. The button sits in the
// sidebar top row. Results open the chat or the settings panel that owns the match.
(function () {
  let dialog = null;
  let input = null;
  let note = null;
  let results = null;
  let timer = null;
  let seq = 0;

  function makeDialog() {
    dialog = el("dialog", "", "home-dialog global-search");
    dialog.id = "globalSearch";
    dialog.setAttribute("aria-labelledby", "gsTitle");
    const head = el("div", "", "sheet-head");
    const title = el("h2", t("Search"), "");
    title.id = "gsTitle";
    const close = el("button", t("Close"), "ghost small");
    close.type = "button";
    close.addEventListener("click", () => dialog.close());
    head.append(title, close);
    input = document.createElement("input");
    input.type = "search";
    input.id = "gsInput";
    input.autocomplete = "off";
    input.placeholder = t("Search characters, worlds, Mind Palace and chats");
    input.setAttribute("aria-label", t("Search everything"));
    note = el("p", t("Type at least two characters."), "muted search-note");
    note.id = "gsNote";
    note.setAttribute("aria-live", "polite");
    results = el("div", "", "search-results");
    results.id = "gsResults";
    dialog.append(head, input, note, results);
    document.body.appendChild(dialog);
    input.addEventListener("input", schedule);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); run(); }
    });
    dialog.addEventListener("close", () => { seq += 1; });
  }

  function open() {
    if (!dialog) makeDialog();
    if (!dialog.open) dialog.showModal();
    input.focus();
    if (input.value.trim().length < 2) note.textContent = t("Type at least two characters.");
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(run, 220);
  }

  function addGroup(title, items, render) {
    if (!items || !items.length) return;
    const group = el("section", "", "search-group");
    group.appendChild(el("h3", title + " \u00b7 " + items.length, ""));
    for (const item of items) group.appendChild(render(item));
    results.appendChild(group);
  }

  function hit(href, name, meta, snippet) {
    const a = el("a", "", "search-hit");
    a.href = href;
    a.appendChild(el("span", name, "search-hit-name"));
    if (meta) a.appendChild(el("span", meta, "search-hit-meta"));
    if (snippet) a.appendChild(el("span", snippet, "search-hit-snippet"));
    a.addEventListener("click", () => dialog.close());
    return a;
  }

  function chatHref(item) {
    return "/chat?c=" + encodeURIComponent(item.chat) + "&branch=" + encodeURIComponent(item.branch || "main")
      + "&msg=" + encodeURIComponent(item.index);
  }

  function draw(data) {
    results.textContent = "";
    const q = input.value.trim();
    const total = (data.characters || []).length + (data.worlds || []).length
      + (data.palace || []).length + (data.chats || []).length;
    if (!total) {
      note.textContent = t("No matches for \u201c{query}\u201d.", { query: q });
      return;
    }
    note.textContent = "";
    addGroup(t("Characters"), data.characters, (c) => {
      const meta = ["@" + (c.creator || "admin"), c.headline].filter(Boolean).join(" \u00b7 ");
      return hit("/chat?c=" + encodeURIComponent(c.name), c.name, meta, c.info ? c.info.slice(0, 160) : "");
    });
    addGroup(t("Worlds"), data.worlds, (w) => {
      const meta = [t("World"), w.section_title ? t(w.section_title) : ""].filter(Boolean).join(" \u00b7 ");
      return hit("/chat?settings=worlds&world=" + encodeURIComponent(w.id), w.name, meta, w.snippet);
    });
    addGroup(t("Mind Palace"), data.palace, (p) => {
      return hit("/chat?c=" + encodeURIComponent(p.chat) + "&settings=palace",
        p.title || t("Untitled entry"), p.name, p.snippet);
    });
    addGroup(t("Chats"), data.chats, (m) => {
      const who = m.role === "user" ? t("You") : (m.speaker || m.name);
      return hit(chatHref(m), m.name, who, m.snippet);
    });
  }

  async function run() {
    const q = input.value.trim();
    const mine = ++seq;
    if (q.length < 2) {
      results.textContent = "";
      note.textContent = t("Type at least two characters.");
      return;
    }
    note.textContent = t("Searching...");
    try {
      const data = await api("GET", "/api/search?q=" + encodeURIComponent(q));
      if (mine !== seq) return;
      draw(data);
    } catch (err) {
      if (mine !== seq) return;
      results.textContent = "";
      note.textContent = err.message || t("Search failed. Try again.");
    }
  }

  const button = document.getElementById("sideSearch");
  if (button) button.addEventListener("click", open);
})();
