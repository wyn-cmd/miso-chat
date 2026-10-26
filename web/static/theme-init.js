// Runs in <head> before the page paints, so a saved light theme never flashes dark.
// Pages set the theme attribute here; common.js handles the toggle button and the accent colour.
try {
  if (localStorage.getItem("rp_theme") === "light") document.documentElement.dataset.theme = "light";
} catch (e) { /* storage blocked: stay on the dark default */ }
try {
  if (localStorage.getItem("rp_avatar") === "circle") document.documentElement.dataset.avatar = "circle";
} catch (e) { /* storage blocked: icons stay squircle */ }

// The chat and interface font. "theme" (the default) keeps each look's own faces. Any other choice sets the body,
// display, sans and chat-text faces on <html>, so the chat, its bubbles and the rest of the page all follow it. The
// old saved values sans, serif and mono still mean what they did. Applied before first paint, and again when the
// picker changes (app.js), so every page agrees.
window.MISO_FONTS = {
  instrument: { label: "Instrument Sans", stack: "'Instrument Sans', ui-sans-serif, system-ui, sans-serif" },
  inter: { label: "Inter", stack: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif" },
  atkinson: { label: "Atkinson Hyperlegible", stack: "'Atkinson Hyperlegible', ui-sans-serif, system-ui, sans-serif" },
  merriweather: { label: "Merriweather (serif)", stack: "Merriweather, Georgia, 'Times New Roman', serif" },
  literata: { label: "Literata (serif)", stack: "Literata, Georgia, 'Times New Roman', serif" },
  mono: { label: "JetBrains Mono", stack: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" },
};
window.MISO_FONT_ALIASES = { sans: "instrument", serif: "merriweather" };
window.applyFontChoice = function applyFontChoice(choice) {
  const root = document.documentElement;
  const key = window.MISO_FONT_ALIASES[choice] || choice;
  const face = window.MISO_FONTS[key];
  if (!face) {
    for (const p of ["--font-body", "--font-display", "--font-sans", "--chat-font"]) root.style.removeProperty(p);
    return "theme";
  }
  for (const p of ["--font-body", "--font-display", "--font-sans", "--chat-font"]) root.style.setProperty(p, face.stack);
  return key;
};
try {
  window.applyFontChoice(localStorage.getItem("rp_font"));
} catch (e) { /* storage blocked: the look's own faces */ }
