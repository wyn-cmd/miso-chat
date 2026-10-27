// Admin: model provider settings. Keys are write-only: the page shows a masked hint, and a key box is
// only sent when it has text, so a blank box keeps the saved key.
const pv = (id) => document.getElementById(id);

function showProviders(view) {
  pv("p_deepseek_enabled").checked = view.deepseek.enabled;
  pv("p_deepseek_hint").textContent = view.deepseek.key_set ? window.tr("Saved key: {hint}", { hint: view.deepseek.key_hint }) : window.tr("No key saved.");
  pv("p_local_enabled").checked = view.local.enabled;
  pv("p_local_url").value = view.local.base_url || "";
  pv("p_local_hint").textContent = view.local.key_set ? window.tr("Saved key: {hint}", { hint: view.local.key_hint }) : window.tr("No key saved (fine for a local server with no auth).");
  pv("p_claude_enabled").checked = view.claude.enabled;
  pv("p_claude_exe").value = view.claude.exe || "";
  pv("p_claude_hint").textContent = view.claude.found
    ? window.tr("claude CLI found.")
    : window.tr("claude CLI not found on this machine. Install Claude Code and sign in, or give the full path.");
}

async function loadProviders() {
  showProviders(await api("GET", "/api/admin/providers"));
}

pv("providerForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  pv("providerMsg").textContent = "";
  const patch = {
    deepseek: { enabled: pv("p_deepseek_enabled").checked },
    local: { enabled: pv("p_local_enabled").checked, base_url: pv("p_local_url").value.trim() },
    claude: { enabled: pv("p_claude_enabled").checked, exe: pv("p_claude_exe").value.trim() },
  };
  const dk = pv("p_deepseek_key").value.trim();
  const lk = pv("p_local_key").value.trim();
  if (dk) patch.deepseek.api_key = dk;
  if (lk) patch.local.api_key = lk;
  try {
    const view = await api("POST", "/api/admin/providers", patch);
    pv("p_deepseek_key").value = "";
    pv("p_local_key").value = "";
    showProviders(view);
    pv("providerMsg").textContent = window.tr("Saved. New chats use the updated providers.");
  } catch (err) {
    pv("providerMsg").textContent = err.message;
  }
});

loadProviders().catch((err) => { pv("providerMsg").textContent = err.message; });
