// Markdown for chat messages. Builds DOM nodes only (never innerHTML), so model or user text
// cannot inject markup. Supported: paragraphs and line breaks, ***bold italic***, **bold**, __bold__,
// *italic*, _italic_, `code`, fenced code blocks, > quotes, - / * / + lists, 1. lists, # headings,
// --- rules and [text](https://...) links (http and https only).
// Emphasis markers are tidied first (fixEmphasis), because models often write them slightly wrong.
// The stored text is never changed; only what is shown.

// One compiled pattern for every call. Nested calls share it, so each call restores the read position it found.
const MD_INLINE = /`(?<code>[^`\n]+)`|\[(?<linkText>[^\]\n]+)\]\((?<linkUrl>https?:\/\/[^\s)]+)\)|\*\*\*(?=\S)(?<boldItalic>[\s\S]*?\S)\*\*\*|\*\*(?=\S)(?<bold>[\s\S]*?\S)\*\*|__(?=\S)(?<bold2>[\s\S]*?\S)__|\*(?=[^\s*])(?<italic>[^*\n]*?[^\s*])\*|(?<![A-Za-z0-9])_(?=[^\s_])(?<italic2>[^_\n]*?[^\s_])_(?![A-Za-z0-9])|["“](?<quote>[^"“”\n]+)["”]/g;

function mdInline(text, parent) {
  const resume = MD_INLINE.lastIndex;
  MD_INLINE.lastIndex = 0;
  let last = 0;
  let m;
  while ((m = MD_INLINE.exec(text)) !== null) {
    const g = m.groups;
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (g.code !== undefined) {
      const code = document.createElement("code");
      code.textContent = g.code;
      parent.appendChild(code);
    } else if (g.linkText !== undefined) {
      const a = document.createElement("a");
      a.href = g.linkUrl;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      mdInline(g.linkText, a);
      parent.appendChild(a);
    } else if (g.boldItalic !== undefined) {
      const strong = document.createElement("strong");
      const em = document.createElement("em");
      mdInline(g.boldItalic, em);
      strong.appendChild(em);
      parent.appendChild(strong);
    } else if (g.bold !== undefined || g.bold2 !== undefined) {
      const strong = document.createElement("strong");
      mdInline(g.bold !== undefined ? g.bold : g.bold2, strong);
      parent.appendChild(strong);
    } else if (g.quote !== undefined) {
      // "dialogue": quoted speech gets a little extra weight; the quote marks stay visible
      const q = document.createElement("span");
      q.className = "dialogue";
      q.appendChild(document.createTextNode(m[0].charAt(0)));
      mdInline(g.quote, q);
      q.appendChild(document.createTextNode(m[0].charAt(m[0].length - 1)));
      parent.appendChild(q);
    } else {
      const em = document.createElement("em");
      mdInline(g.italic !== undefined ? g.italic : g.italic2, em);
      parent.appendChild(em);
    }
    last = MD_INLINE.lastIndex;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  MD_INLINE.lastIndex = resume;
}

// ---- emphasis repair (display only)
// Per line, outside code fences, code spans and horizontal rules:
// - a run of 1 to 3 asterisks pairs with the nearest open run of the same length; a closer one star
//   longer than the open run closes it, and the extra star is dropped ("**EAT.***" -> "**EAT.**");
// - spaces just inside a pair are removed ("* text*" and "*text *" -> "*text*");
// - a letter directly before an opening marker gets a space ("word*clack*" -> "word *clack*");
// - a stray marker that never pairs is dropped, so it does not show as a bare star;
// - a lone star with spaces on both sides ("2 * 3") is kept as it is unless it pairs.
// Running it twice gives the same result as running it once.
function fixEmphasis(src) {
  const out = [];
  let inFence = false;
  for (const line of String(src == null ? "" : src).replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*```/.test(line)) { inFence = !inFence; out.push(line); continue; }
    out.push(inFence ? line : fixLine(line));
  }
  return out.join("\n");
}

function fixLine(line) {
  if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) return line;   // horizontal rule
  const lead = line.match(/^\s{0,3}(?:#{1,3}\s+|>\s?|(?:[-*+]|\d+[.)])\s+)/);
  const prefix = lead ? lead[0] : "";
  const body = line.slice(prefix.length);
  // odd entries of this split are code spans; they are left exactly as written
  return prefix + body.split(/(`[^`\n]+`)/).map((part, i) => (i % 2 ? part : fixRuns(part))).join("");
}

function fixRuns(s) {
  const isSpace = (c) => c === "" || /\s/.test(c);
  const runs = [];
  const re = /\*+/g;
  let m;
  while ((m = re.exec(s)) !== null) {
    if (m[0].length > 3) continue;                 // longer runs are left alone
    const start = m.index;
    const end = start + m[0].length;
    const before = start > 0 ? s[start - 1] : "";
    const after = end < s.length ? s[end] : "";
    const lenient = isSpace(before) && isSpace(after);
    runs.push({
      start, end, len: m[0].length, lenient,
      canOpen: lenient || !isSpace(after),
      canClose: lenient || !isSpace(before),
      role: null, keep: 0, partner: null,
    });
  }

  // pair each closer with the nearest open run of the same length (or the open run just below it when
  // the closer is longer). A run with a space before it closes an open run only when no later run of its
  // length can close properly; otherwise it opens an inner span. Later closers and open runs are counted
  // by length, so each step stays constant time.
  const laterClosers = [0, 0, 0, 0];
  const hasLaterCloser = new Array(runs.length);
  for (let idx = runs.length - 1; idx >= 0; idx--) {
    const r = runs[idx];
    hasLaterCloser[idx] = laterClosers[r.len] > 0;
    if (r.canClose && !r.lenient) laterClosers[r.len] += 1;
  }
  const stack = [];
  const openCount = [0, 0, 0, 0];
  for (let idx = 0; idx < runs.length; idx++) {
    const r = runs[idx];
    const sameLen = openCount[r.len] > 0;
    if (r.canClose || (r.canOpen && sameLen && !hasLaterCloser[idx])) {
      let j = -1;
      for (let k = stack.length - 1; k >= 0; k--) {
        if (stack[k].len === r.len) { j = k; break; }
      }
      if (j < 0 && stack.length && r.len > stack[stack.length - 1].len) j = stack.length - 1;
      if (j >= 0) {
        const o = stack[j];
        if (s.slice(o.end, r.start).trim() !== "") {
          const k = Math.min(o.len, r.len);
          o.role = "open"; o.keep = k; o.partner = r;
          r.role = "close"; r.keep = k; r.partner = o;
          for (let n = stack.length - 1; n >= j; n--) openCount[stack[n].len] -= 1;
          stack.length = j;
          continue;
        }
      }
    }
    if (r.canOpen) { stack.push(r); openCount[r.len] += 1; }
  }
  // a pair inside another pair cannot be shown (the renderer does not nest emphasis), so the inner
  // markers are dropped and the outer pair keeps its emphasis. Pairs are properly nested or disjoint,
  // so one sweep over them by start finds every contained pair.
  const pairs = runs.filter((r) => r.role === "open").map((o) => [o, o.partner]);
  pairs.sort((a, b) => a[0].start - b[0].start);
  let outerEnd = -1;
  for (const [o, c] of pairs) {
    if (c.end < outerEnd) { o.role = "dropped"; c.role = "dropped"; }
    else outerEnd = c.end;
  }

  const edits = [];
  for (const r of runs) {
    if (r.role === "open") {
      const lead = /[A-Za-z0-9]/.test(s[r.start - 1] || "") ? " " : "";
      edits.push({ start: r.start, end: r.end, text: lead + "*".repeat(r.keep) });
      let j = r.end;
      while (j < r.partner.start && /\s/.test(s[j])) j++;
      if (j > r.end) edits.push({ start: r.end, end: j, text: "" });
    } else if (r.role === "close") {
      // a space pulled in from before the closer is kept after it when a word follows ("*smiled* and")
      let j = r.start;
      while (j > r.partner.end && /\s/.test(s[j - 1])) j--;
      const gap = j < r.start && /[A-Za-z0-9]/.test(s[r.end] || "") ? " " : "";
      edits.push({ start: r.start, end: r.end, text: "*".repeat(r.keep) + gap });
      if (j < r.start) edits.push({ start: j, end: r.start, text: "" });
    } else if (r.role === "dropped" || (!r.lenient && !(r.canOpen && r.canClose))) {
      edits.push({ start: r.start, end: r.end, text: "" });   // a stray marker that never paired
    }
  }
  if (!edits.length) return s;

  edits.sort((a, b) => a.start - b.start);
  let res = "";
  let pos = 0;
  for (const e of edits) {
    if (e.start < pos) continue;
    res += s.slice(pos, e.start) + e.text;
    pos = e.end;
  }
  return res + s.slice(pos);
}

function renderMarkdown(src, root) {
  root.textContent = "";
  const lines = fixEmphasis(src == null ? "" : src).split("\n");
  let para = [];
  const flush = () => {
    if (!para.length) return;
    const p = document.createElement("p");
    para.forEach((ln, idx) => {
      if (idx) p.appendChild(document.createElement("br"));
      mdInline(ln, p);
    });
    root.appendChild(p);
    para = [];
  };
  const ulRe = /^\s{0,3}[-*+]\s+(.*)$/;
  const olRe = /^\s{0,3}\d+[.)]\s+(.*)$/;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
      i++; // closing fence; an unclosed fence runs to the end, which is normal while a reply streams
      const pre = document.createElement("pre");
      const c = document.createElement("code");
      c.textContent = code.join("\n");
      pre.appendChild(c);
      root.appendChild(pre);
      continue;
    }

    if (!line.trim()) { flush(); i++; continue; }

    const heading = line.match(/^\s{0,3}(#{1,3})\s+(.*)$/);
    if (heading) {
      flush();
      const h = document.createElement("h4");
      mdInline(heading[2].replace(/\s+#+\s*$/, ""), h);
      root.appendChild(h);
      i++;
      continue;
    }

    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      root.appendChild(document.createElement("hr"));
      i++;
      continue;
    }

    if (/^\s{0,3}>/.test(line)) {
      flush();
      const quoted = [];
      while (i < lines.length && /^\s{0,3}>/.test(lines[i])) {
        quoted.push(lines[i].replace(/^\s{0,3}>\s?/, ""));
        i++;
      }
      const bq = document.createElement("blockquote");
      renderMarkdown(quoted.join("\n"), bq);
      root.appendChild(bq);
      continue;
    }

    if (ulRe.test(line) || olRe.test(line)) {
      flush();
      const ordered = !ulRe.test(line);
      const itemRe = ordered ? olRe : ulRe;
      const list = document.createElement(ordered ? "ol" : "ul");
      while (i < lines.length) {
        const m = lines[i].match(itemRe);
        if (!m) break;
        const li = document.createElement("li");
        mdInline(m[1], li);
        list.appendChild(li);
        i++;
      }
      root.appendChild(list);
      continue;
    }

    para.push(line);
    i++;
  }
  flush();
}
