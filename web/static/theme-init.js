// Runs in <head> before the page paints, so a saved light theme never flashes dark.
// Pages set the theme attribute here; common.js handles the toggle button and the accent colour.
try {
  if (localStorage.getItem("rp_theme") === "light") document.documentElement.dataset.theme = "light";
} catch (e) { /* storage blocked: stay on the dark default */ }
