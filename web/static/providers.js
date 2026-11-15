// Admin: model provider settings. Keys are write-only: the page shows a masked hint, and a key box is
// only sent when it has text, so a blank box keeps the saved key.
const pv = (id) => document.getElementById(id);

// The speech languages the audio model understands, kept in step with web/voice.py.
const SPEECH_LANGS = [
  ["en", "English"], ["en-GB", "British English"], ["en-US", "American English"],
  ["ar", "Arabic"], ["da", "Danish"], ["de", "German"], ["el", "Greek"], ["fi", "Finnish"],
  ["fr", "French"], ["he", "Hebrew"], ["it", "Italian"], ["ja", "Japanese"], ["ko", "Korean"],
  ["nl", "Dutch"], ["pl", "Polish"], ["pt", "Portuguese"], ["es", "Spanish"], ["sv", "Swedish"],
  ["tr", "Turkish"], ["zh", "Chinese"],
];

function fillSpeechLangs() {
  const sel = pv("p_audio_lang");
  if (!sel || sel.options.length) return;
  for (const [code, name] of SPEECH_LANGS) sel.appendChild(new Option(name + " (" + code + ")", code));
}

function showProviders(view) {
  pv("p_deepseek_enabled").checked = view.deepseek.enabled;
  pv("p_deepseek_hint").textContent = view.deepseek.key_set ? window.tr("Saved key: {hint}", { hint: view.deepseek.key_hint }) : window.tr("No key saved.");
  const orv = view.openrouter || {};
  pv("p_openrouter_enabled").checked = !!orv.enabled;
  pv("p_openrouter_hint").textContent = orv.key_set
    ? window.tr("{n} key(s) saved: {hints}", { n: (orv.key_hints || []).length, hints: (orv.key_hints || []).join(", ") })
    : window.tr("No key saved.");
  pv("p_local_enabled").checked = view.local.enabled;
  pv("p_local_url").value = view.local.base_url || "";
  pv("p_local_hint").textContent = view.local.key_set ? window.tr("Saved key: {hint}", { hint: view.local.key_hint }) : window.tr("No key saved (fine for a local server with no auth).");
  pv("p_claude_enabled").checked = view.claude.enabled;
  pv("p_claude_exe").value = view.claude.exe || "";
  pv("p_claude_hint").textContent = view.claude.found
    ? window.tr("claude CLI found.")
    : window.tr("claude CLI not found on this machine. Install Claude Code and sign in, or give the full path.");
  const oc = view.opencode || {};
  pv("p_opencode_enabled").checked = !!oc.enabled;
  pv("p_opencode_exe").value = oc.exe || "";
  pv("p_opencode_hint").textContent = oc.found
    ? window.tr("opencode CLI found.")
    : window.tr("opencode CLI not found on this machine. Install opencode, or give the full path.");
  const audio = view.audio || {};
  pv("p_audio_enabled").checked = !!audio.enabled;
  pv("p_audio_url").value = audio.base_url || "";
  pv("p_audio_style").value = audio.style === "openai" ? "openai" : "llama";
  pv("p_audio_model").value = audio.model || "";
  fillSpeechLangs();
  pv("p_audio_lang").value = audio.lang || "en";
  pv("p_audio_voice").value = audio.voice === "male" ? "male" : "female";
  pv("p_audio_hint").textContent = audio.key_set
    ? window.tr("Saved key: {hint}", { hint: audio.key_hint })
    : window.tr("No key saved (fine for a local speech server with no auth).");
  const vision = view.vision || {};
  pv("p_vision_enabled").checked = !!vision.enabled;
  pv("p_vision_model").value = vision.model || "";
  const image = view.image || {};
  pv("p_image_enabled").checked = !!image.enabled;
  pv("p_image_model").value = image.model || "";
}

async function loadProviders() {
  showProviders(await api("GET", "/api/admin/providers"));
}

pv("p_audio_detect").addEventListener("click", async () => {
  const hint = pv("p_audio_model_hint");
  const button = pv("p_audio_detect");
  hint.textContent = window.tr("Looking for models...");
  button.disabled = true;
  try {
    const data = await api("POST", "/api/admin/voice/models", {
      base_url: pv("p_audio_url").value.trim(),
      style: pv("p_audio_style").value,
      api_key: pv("p_audio_key").value.trim(),
    });
    const ids = Array.isArray(data.models) ? data.models : [];
    const list = pv("p_audio_models");
    list.textContent = "";
    for (const id of ids) list.appendChild(new Option(id, id));
    if (ids.length && !pv("p_audio_model").value.trim()) pv("p_audio_model").value = ids[0];
    hint.textContent = ids.length
      ? window.tr("Found {n} models. Pick one or type another.", { n: ids.length })
      : window.tr("No models found. You can still type one in.");
  } catch (err) {
    hint.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

pv("providerForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  pv("providerMsg").textContent = "";
  const patch = {
    deepseek: { enabled: pv("p_deepseek_enabled").checked },
    openrouter: { enabled: pv("p_openrouter_enabled").checked },
    local: { enabled: pv("p_local_enabled").checked, base_url: pv("p_local_url").value.trim() },
    claude: { enabled: pv("p_claude_enabled").checked, exe: pv("p_claude_exe").value.trim() },
    opencode: { enabled: pv("p_opencode_enabled").checked, exe: pv("p_opencode_exe").value.trim() },
    audio: {
      enabled: pv("p_audio_enabled").checked,
      base_url: pv("p_audio_url").value.trim(),
      style: pv("p_audio_style").value,
      model: pv("p_audio_model").value.trim(),
      lang: pv("p_audio_lang").value,
      voice: pv("p_audio_voice").value,
    },
    vision: { enabled: pv("p_vision_enabled").checked, model: pv("p_vision_model").value.trim() },
    image: { enabled: pv("p_image_enabled").checked, model: pv("p_image_model").value.trim() },
  };
  const dk = pv("p_deepseek_key").value.trim();
  const ok = pv("p_openrouter_key").value.trim();
  const lk = pv("p_local_key").value.trim();
  const ak = pv("p_audio_key").value.trim();
  if (dk) patch.deepseek.api_key = dk;
  if (ok) patch.openrouter.api_keys = ok;
  if (lk) patch.local.api_key = lk;
  if (ak) patch.audio.api_key = ak;
  try {
    const view = await api("POST", "/api/admin/providers", patch);
    pv("p_deepseek_key").value = "";
    pv("p_openrouter_key").value = "";
    pv("p_local_key").value = "";
    pv("p_audio_key").value = "";
    showProviders(view);
    pv("providerMsg").textContent = window.tr("Saved. New chats use the updated providers.");
  } catch (err) {
    pv("providerMsg").textContent = err.message;
  }
});

pv("p_openrouter_check").addEventListener("click", async () => {
  const hint = pv("p_openrouter_model_hint");
  const out = pv("p_openrouter_models_out");
  const button = pv("p_openrouter_check");
  hint.textContent = window.tr("Looking for models...");
  out.textContent = "";
  button.disabled = true;
  try {
    const data = await api("POST", "/api/admin/openrouter/models", {
      api_key: pv("p_openrouter_key").value.trim(),
    });
    const ids = Array.isArray(data.models) ? data.models : [];
    hint.textContent = ids.length
      ? window.tr("Found {n} free models.", { n: ids.length })
      : window.tr("No free models found.");
    out.textContent = ids.join(", ");
  } catch (err) {
    hint.textContent = err.message;
    out.textContent = "";
  } finally {
    button.disabled = false;
  }
});

pv("p_vision_check").addEventListener("click", async () => {
  const hint = pv("p_vision_model_hint");
  const list = pv("p_vision_models");
  const button = pv("p_vision_check");
  hint.textContent = window.tr("Looking for models...");
  list.textContent = "";
  button.disabled = true;
  try {
    const data = await api("POST", "/api/admin/openrouter/vision-models", {
      api_key: pv("p_openrouter_key").value.trim(),
    });
    const ids = Array.isArray(data.models) ? data.models : [];
    for (const id of ids) list.appendChild(new Option(id, id));
    hint.textContent = ids.length ? window.tr("Found {n} models.", { n: ids.length }) : window.tr("No models found.");
  } catch (err) {
    hint.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

pv("p_image_check").addEventListener("click", async () => {
  const hint = pv("p_image_model_hint");
  const list = pv("p_image_models");
  const button = pv("p_image_check");
  hint.textContent = window.tr("Looking for models...");
  list.textContent = "";
  button.disabled = true;
  try {
    const data = await api("POST", "/api/admin/openrouter/image-models", {
      api_key: pv("p_openrouter_key").value.trim(),
    });
    const ids = Array.isArray(data.models) ? data.models : [];
    for (const id of ids) list.appendChild(new Option(id, id));
    hint.textContent = ids.length ? window.tr("Found {n} models.", { n: ids.length }) : window.tr("No models found.");
  } catch (err) {
    hint.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

pv("p_opencode_check").addEventListener("click", async () => {
  const hint = pv("p_opencode_model_hint");
  const out = pv("p_opencode_models_out");
  const button = pv("p_opencode_check");
  hint.textContent = window.tr("Checking every model...");
  out.textContent = "";
  button.disabled = true;
  try {
    const data = await api("POST", "/api/admin/opencode/models", {
      exe: pv("p_opencode_exe").value.trim(),
    });
    const rows = Array.isArray(data.models) ? data.models : [];
    const good = rows.filter((r) => r.available).length;
    hint.textContent = window.tr("{good} of {n} models available.", { good: good, n: rows.length });
    for (const r of rows) {
      const line = document.createElement("div");
      line.textContent = (r.available ? "OK  " : "OUT ") + r.id;
      out.appendChild(line);
    }
  } catch (err) {
    hint.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

loadProviders().catch((err) => { pv("providerMsg").textContent = err.message; });
