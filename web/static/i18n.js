// Interface translations. Every page loads this file, and one locale file from /static/locales beside it.
// English is the source language: the English words are the keys, so a missing translation simply shows the
// English the page already has. To add a language, copy locales/en.js to locales/<code>.js, translate the
// values only, keep the keys as they are, change the code at the top and bottom of the file, and add the code
// to SHIPPED below.
//
// The static pages keep their English text in the markup. At DOMContentLoaded this file walks the page and
// swaps any text node or placeholder/title/aria-label/alt value that has a translation, so a translated page
// needs no markup changes. Text that scripts write later uses t() or tn().
(function () {
  const STORAGE = "rp_lang";
  const BASE = "/static/locales/";
  // Locales that ship. Each entry is [code, name shown in the picker]. Add one here when its file exists.
  const SHIPPED = [["en", "English"]];
  const LOCALES = (window.MISO_I18N_LOCALES = window.MISO_I18N_LOCALES || {});
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "PRE"]);

  let current = null;       // the locale in use, once its file is in place
  let numberFormat = null;
  let pluralRules = null;
  const dateFormats = new Map();   // formatters by locale and options, so repeated dates do not rebuild them

  function stored() {
    try { return localStorage.getItem(STORAGE) || ""; } catch (e) { return ""; }
  }

  function validCode(code) {
    return /^[a-z]{2,3}(-[a-z0-9]{2,8})?$/.test(code);
  }

  // A ?lang= query is for previewing a file that is not listed yet. A saved choice must be one that ships,
  // so a typo cannot cause a 404 on every page.
  function wanted() {
    const query = new URLSearchParams(location.search).get("lang") || "";
    if (query) {
      const code = query.toLowerCase().replace("_", "-");
      if (validCode(code)) return code;
    }
    const saved = stored().toLowerCase().replace("_", "-");
    if (saved && SHIPPED.some(([code]) => code === saved)) return saved;
    const candidates = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""];
    for (const raw of candidates) {
      const lower = String(raw || "").toLowerCase().replace("_", "-");
      for (const [code] of SHIPPED) {
        if (code === lower || code === lower.split("-")[0]) return code;
      }
    }
    return "en";
  }

  const code = wanted();

  function messagesFor(locale) {
    return (LOCALES[locale] && LOCALES[locale].messages) || (LOCALES.en && LOCALES.en.messages) || {};
  }

  function syncPicker() {
    const pick = document.getElementById("langPick");
    if (!pick) return;
    const lang = current || code;
    if ([...pick.options].some((option) => option.value === lang)) pick.value = lang;
  }

  function install(locale) {
    if (!LOCALES[locale]) return false;
    current = locale;
    const meta = LOCALES[locale].meta || {};
    document.documentElement.lang = locale;
    document.documentElement.dir = meta.dir || "ltr";
    try { numberFormat = new Intl.NumberFormat(locale); } catch (e) { numberFormat = new Intl.NumberFormat("en"); }
    try { pluralRules = new Intl.PluralRules(locale); } catch (e) { pluralRules = new Intl.PluralRules("en"); }
    dateFormats.clear();
    syncPicker();
    return true;
  }

  // Locale files call this once they have registered their catalog. The English file loads before this
  // runtime, so its call is skipped; every other locale is written below and calls back when it runs.
  window.MISO_I18N_LOADED = function (locale) {
    if (locale === code) {
      install(locale);
      apply(document.documentElement);
    }
  };

  if (code !== "en" && !LOCALES[code] && validCode(code)) {
    // Parser-blocking, so the catalog is in place before the page scripts run.
    document.write('<script src="' + BASE + code + '.js"><\/script>');
  }
  install(LOCALES[code] ? code : "en");

  function ensure() {
    if (current) return;
    install(LOCALES[code] ? code : "en");
  }

  function number(value) {
    ensure();
    return typeof value === "number" && isFinite(value) ? numberFormat.format(value) : String(value);
  }

  function lookup(key) {
    if (key === undefined || key === null) return null;
    ensure();
    const messages = messagesFor(current);
    if (Object.prototype.hasOwnProperty.call(messages, key)) return messages[key];
    const english = messagesFor("en");
    if (Object.prototype.hasOwnProperty.call(english, key)) return english[key];
    return null;
  }

  function fill(text, vars) {
    if (text === undefined || text === null) return "";
    if (!vars) return String(text);
    return String(text).replace(/\{(\w+)\}/g, (whole, name) => {
      if (!Object.prototype.hasOwnProperty.call(vars, name)) return whole;
      const value = vars[name];
      return typeof value === "number" ? number(value) : String(value);
    });
  }

  // t("Showing {shown} of {total} characters", {shown, total})
  function t(key, vars) {
    const value = lookup(key);
    return fill(value === null ? key : value, vars);
  }

  // One or many, chosen by the locale's plural rule: tn("{n} message", "{n} messages", count)
  function tn(one, other, count, vars) {
    ensure();
    let key = other;
    try { key = pluralRules.select(Number(count)) === "one" ? one : other; } catch (e) { key = Number(count) === 1 ? one : other; }
    const value = lookup(key);
    return fill(value === null ? key : value, Object.assign({}, vars, { n: count }));
  }

  function hasTranslation(el) {
    for (let node = el; node; node = node.parentElement) {
      if (node.nodeType !== 1) continue;
      if (node.hasAttribute("data-no-i18n")) return true;
      if (SKIP_TAGS.has(node.tagName)) return true;
    }
    return false;
  }

  function translateText(node) {
    const raw = node.nodeValue;
    const key = raw.trim();
    if (key.length < 2 || !/[A-Za-z]{2}/.test(key)) return;
    if (hasTranslation(node.parentElement)) return;
    const value = lookup(key);
    if (value === null || value === key) return;
    const lead = raw.slice(0, raw.length - raw.trimStart().length);
    const trail = raw.slice(raw.trimEnd().length);
    node.nodeValue = lead + value + trail;
  }

  function translateAttrs(el) {
    if (hasTranslation(el)) return;
    for (const attr of ATTRS) {
      if (!el.hasAttribute(attr)) continue;
      const raw = el.getAttribute(attr);
      const key = String(raw || "").trim();
      if (key.length < 2 || !/[A-Za-z]{2}/.test(key)) continue;
      const value = lookup(key);
      if (value === null || value === key) continue;
      el.setAttribute(attr, raw.replace(raw.trim(), value));
    }
  }

  function apply(root) {
    ensure();
    const start = root || document.documentElement;
    if (!start) return;
    if (start.nodeType === 3) { translateText(start); return; }
    if (start.nodeType === 1) translateAttrs(start);
    const walker = document.createTreeWalker(start, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode(node) {
        if (node.nodeType === 1) return hasTranslation(node) ? NodeFilter.FILTER_SKIP : NodeFilter.FILTER_ACCEPT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType === 3) translateText(node);
      else translateAttrs(node);
    }
  }

  function setLang(next) {
    const target = String(next || "").toLowerCase().replace("_", "-");
    if (!validCode(target)) return;
    try { localStorage.setItem(STORAGE, target); } catch (e) { /* storage blocked: this page keeps its language */ }
    const url = new URL(location.href);
    url.searchParams.delete("lang");
    location.replace(url.pathname + url.search + url.hash);
  }

  function buildPicker() {
    const pick = document.getElementById("langPick");
    if (!pick) return;
    pick.textContent = "";
    for (const [codeName, name] of SHIPPED) {
      const option = document.createElement("option");
      option.value = codeName;
      option.textContent = name;
      pick.appendChild(option);
    }
    syncPicker();
    const row = document.getElementById("langRow");
    if (row) row.hidden = SHIPPED.length < 2;
    pick.addEventListener("change", () => setLang(pick.value));
  }

  function boot() {
    ensure();
    apply(document.documentElement);
    buildPicker();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.t = t;
  window.tn = tn;
  // Short aliases for pages that have a local variable called t (a temperature, usually).
  window.tr = t;
  window.trn = tn;
  window.MISO_I18N = {
    get lang() { return current || code; },
    get dir() { return document.documentElement.dir || "ltr"; },
    t: t,
    tn: tn,
    number: number,
    date: (value, options) => {
      try {
        const locale = current || code || "en";
        const key = locale + "|" + JSON.stringify(options || null);
        let formatter = dateFormats.get(key);
        if (!formatter) {
          formatter = new Intl.DateTimeFormat(locale, options);
          dateFormats.set(key, formatter);
        }
        return formatter.format(value instanceof Date ? value : new Date(value));
      } catch (e) { return String(value); }
    },
    apply: apply,
    setLang: setLang,
    locales: SHIPPED.map(([codeName, name]) => ({ code: codeName, name: name })),
  };
})();
