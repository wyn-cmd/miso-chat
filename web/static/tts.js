// Read character replies aloud through the speech server, with a character's own voice or an account's saved
// voice profile. There is no browser fallback: if the server is off, replies stay silent.
(function () {
  const KEY = "rp_tts";
  const DEFAULTS = { auto: false, rate: 1, pitch: 1, volume: 1, dialogueOnly: false };
  const SAMPLE_MAX = 20 * 1024 * 1024;
  // Kept in step with web/voice.py; the character editor reads this list too.
  const LANGS = [
    { code: "en", name: "English" }, { code: "en-GB", name: "British English" },
    { code: "en-US", name: "American English" }, { code: "ar", name: "Arabic" },
    { code: "da", name: "Danish" }, { code: "de", name: "German" }, { code: "el", name: "Greek" },
    { code: "fi", name: "Finnish" }, { code: "fr", name: "French" }, { code: "he", name: "Hebrew" },
    { code: "it", name: "Italian" }, { code: "ja", name: "Japanese" }, { code: "ko", name: "Korean" },
    { code: "nl", name: "Dutch" }, { code: "pl", name: "Polish" }, { code: "pt", name: "Portuguese" },
    { code: "es", name: "Spanish" }, { code: "sv", name: "Swedish" }, { code: "tr", name: "Turkish" },
    { code: "zh", name: "Chinese" },
  ];

  let prefs = loadLocal();
  const state = {
    available: false, auto: prefs.auto, rate: prefs.rate, pitch: prefs.pitch,
    volume: prefs.volume, dialogue_only: !!prefs.dialogueOnly, choice: "character",
    profiles: [], character: { name: "", voice: "", lang: "", sample: false }, langs: LANGS, loaded: false,
  };
  let activeBtn = null;
  let activeAudio = null;
  let activeUrl = null;
  let speakToken = 0;
  let busy = false;
  const queue = [];
  let panelBound = false;
  let activeCharacter = "";
  let activeSolo = true;
  let loadToken = 0;
  let editingProfile = null;
  let pendingPatch = {};
  let pendingTimer = null;

  function $(id) { return document.getElementById(id); }

  function label(key, vars) {
    return window.tr ? window.tr(key, vars) : key;
  }

  function clamp(value, lo, hi) {
    const n = Number(value);
    if (!Number.isFinite(n)) return lo;
    return Math.max(lo, Math.min(hi, n));
  }

  function loadLocal() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { raw = null; }
    const p = Object.assign({}, DEFAULTS, raw && typeof raw === "object" ? raw : {});
    p.auto = !!p.auto;
    p.rate = clamp(p.rate, 0.5, 2);
    p.pitch = clamp(p.pitch, 0, 2);
    p.volume = clamp(p.volume, 0, 1);
    p.dialogueOnly = !!p.dialogueOnly;
    return p;
  }

  function saveLocal() {
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked: this page keeps the choice */ }
  }

  function setButton(btn, on) {
    if (!btn) return;
    btn.classList.toggle("speaking", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    const text = label(on ? "Stop reading" : "Read aloud");
    btn.title = text;
    btn.setAttribute("aria-label", text);
  }

  function setNote(text) {
    const note = $("ttsNote");
    if (note) note.textContent = text || "";
  }

  function safeJson(response) {
    return response.json().catch(() => ({}));
  }

  function offNote() {
    return label("Server speech is off. An admin can enable it under Admin, Model providers, Speech.");
  }

  // ---- server settings and profiles

  async function load() {
    const token = ++loadToken;
    try {
      const r = await fetch("/api/voice?character=" + encodeURIComponent(activeCharacter), { credentials: "same-origin" });
      if (!r.ok) throw new Error("unavailable");
      const data = await r.json();
      if (token !== loadToken) return;
      state.available = !!data.available;
      state.auto = !!data.auto;
      state.rate = clamp(data.rate, 0.5, 2);
      state.pitch = clamp(data.pitch, 0, 2);
      state.volume = clamp(data.volume, 0, 1);
      state.dialogue_only = !!data.dialogue_only;
      state.choice = data.choice || "character";
      state.profiles = Array.isArray(data.profiles) ? data.profiles : [];
      state.character = data.character || state.character;
      state.langs = Array.isArray(data.langs) ? data.langs : state.langs;
      state.loaded = true;
      if (!state.available) {
        // The speaker buttons only exist for a live server, so drop any left from before it went off.
        document.querySelectorAll(".speak-btn").forEach((b) => b.remove());
      }
      prefs.auto = state.auto;
      prefs.rate = state.rate;
      prefs.pitch = state.pitch;
      prefs.volume = state.volume;
      prefs.dialogueOnly = state.dialogue_only;
      saveLocal();
    } catch (e) {
      if (token !== loadToken) return;
      state.available = false;
      state.loaded = true;
      document.querySelectorAll(".speak-btn").forEach((b) => b.remove());
    }
  }

  function savePrefs(patch, character) {
    Object.assign(state, patch);
    if ("auto" in patch) prefs.auto = patch.auto;
    if ("rate" in patch) prefs.rate = patch.rate;
    if ("pitch" in patch) prefs.pitch = patch.pitch;
    if ("volume" in patch) prefs.volume = patch.volume;
    if ("dialogue_only" in patch) prefs.dialogueOnly = patch.dialogue_only;
    saveLocal();
    Object.assign(pendingPatch, patch);
    if (character) pendingPatch.character = character;
    clearTimeout(pendingTimer);
    pendingTimer = setTimeout(flushPrefs, 250);
  }

  async function flushPrefs() {
    const body = pendingPatch;
    pendingPatch = {};
    if (!Object.keys(body).length) return;
    try {
      const r = await fetch("/api/voice/prefs", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "roleplay" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const j = await safeJson(r);
        setNote((j && j.detail) || label("The voice setting was not saved."));
      }
    } catch (e) { /* offline: the local copy still applies */ }
  }

  async function serverSpeak(text, speaker) {
    const r = await fetch("/api/voice/speak", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "roleplay" },
      body: JSON.stringify({ text: text, speaker: speaker || "", character: activeCharacter }),
    });
    if (!r.ok) {
      const j = await safeJson(r);
      setNote((j && j.detail) || label("The speech server could not read that."));
      return "handled";
    }
    return r.blob();
  }

  // ---- playback

  function stopAudioOnly() {
    if (activeAudio) {
      try { activeAudio.pause(); } catch (e) { /* already stopped */ }
      activeAudio.onended = null;
      activeAudio.onerror = null;
      activeAudio = null;
    }
    if (activeUrl) {
      try { URL.revokeObjectURL(activeUrl); } catch (e) { /* already revoked */ }
      activeUrl = null;
    }
  }

  function finishAudio(audio) {
    if (activeAudio !== audio) return;
    stopAudioOnly();
    setButton(activeBtn, false);
    activeBtn = null;
    busy = false;
    drainQueue();
  }

  function playBlob(blob, btn) {
    stopAudioOnly();
    busy = true;
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.volume = clamp(state.volume, 0, 1);
    activeAudio = audio;
    activeUrl = url;
    activeBtn = btn || null;
    setButton(activeBtn, true);
    audio.onended = () => finishAudio(audio);
    audio.onerror = () => finishAudio(audio);
    audio.play().catch(() => {
      finishAudio(audio);
      setNote(label("Press the speaker again to start playback."));
    });
  }

  function stopSpeech(keepQueue) {
    speakToken++;
    busy = false;
    if (!keepQueue) queue.length = 0;
    stopAudioOnly();
    setButton(activeBtn, false);
    activeBtn = null;
  }

  // Auto-read queues replies instead of cutting off the one playing, so a group turn is read in order.
  function enqueue(text, speaker, btn) {
    if (!text) return;
    queue.push({ text: text, speaker: speaker, btn: btn });
    drainQueue();
  }

  function drainQueue() {
    if (busy || activeAudio) return;
    const item = queue.shift();
    if (item) speak(item.text, item.speaker, item.btn, true);
  }

  function speak(text, speaker, btn, keepQueue) {
    text = String(text || "").trim();
    if (!text) return false;
    if (!state.available) {
      setNote(offNote());
      return false;
    }
    if (btn && btn === activeBtn && (busy || activeAudio)) {
      stopSpeech();
      return true;
    }
    stopSpeech(keepQueue);
    busy = true;
    const token = ++speakToken;
    activeBtn = btn || null;
    setButton(activeBtn, true);
    serverSpeak(text, speaker).then((result) => {
      if (token !== speakToken) return;
      if (result === "handled") {
        activeBtn = null;
        setButton(btn, false);
        busy = false;
        drainQueue();
        return;
      }
      playBlob(result, btn);
    }).catch(() => {
      if (token !== speakToken) return;
      activeBtn = null;
      setButton(btn, false);
      busy = false;
      setNote(label("The speech server could not be reached."));
      drainQueue();
    });
    return true;
  }

  function textOf(box) {
    const body = box && box.querySelector(".text");
    return body ? body.textContent.trim() : "";
  }

  function messageButton(box, speaker) {
    if (state.loaded && !state.available) return null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "speak-btn";
    btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 8a5 5 0 0 1 0 8M18.5 5.5a8.5 8.5 0 0 1 0 13"/></svg>';
    setButton(btn, false);
    btn.addEventListener("click", () => speak(textOf(box), speaker, btn));
    return btn;
  }

  function autoSpeak(box, speaker) {
    if (window.misoCallActive && window.misoCallActive()) return;
    if (!state.auto || !state.available) return;
    const text = textOf(box);
    if (!text) return;
    enqueue(text, speaker, box.querySelector(".speak-btn"));
  }

  // ---- the settings panel

  function setCharacter(name, solo) {
    const next = String(name || "");
    const changed = next !== activeCharacter;
    activeCharacter = next;
    activeSolo = solo !== false;
    if (changed) state.loaded = false;
    const sheet = $("settingsSheet");
    if (sheet && !sheet.hidden) refreshPanel();
  }

  function fillChoiceSelect(select, choice) {
    if (!select) return;
    select.textContent = "";
    select.appendChild(new Option(label("Character's own voice"), "character"));
    if (state.profiles.length) {
      const group = document.createElement("optgroup");
      group.label = label("My voice profiles");
      for (const prof of state.profiles) group.appendChild(new Option(prof.name, prof.id));
      select.appendChild(group);
    }
    const known = choice === "character" || state.profiles.some((p) => p.id === choice);
    select.value = known ? choice : "character";
  }

  function langName(code) {
    const list = state.langs.length ? state.langs : LANGS;
    const item = list.find((i) => i.code === code);
    return item ? item.name : code;
  }

  function fillLangSelect(select, saved) {
    if (!select) return;
    select.textContent = "";
    select.appendChild(new Option(label("Server default"), ""));
    const list = state.langs.length ? state.langs : LANGS;
    for (const item of list) select.appendChild(new Option(item.name + " (" + item.code + ")", item.code));
    select.value = saved === "" || list.some((i) => i.code === saved) ? saved : "en";
  }

  function renderProfiles() {
    const list = $("ttsProfileList");
    if (!list) return;
    list.textContent = "";
    if (!state.profiles.length) {
      list.appendChild(el("p", label("No voice profiles yet."), "muted"));
      return;
    }
    for (const prof of state.profiles) {
      const row = el("div", null, "tts-profile-row");
      const main = el("div", null, "tts-profile-main");
      main.appendChild(el("span", prof.name, "tts-profile-name"));
      const meta = [prof.voice || label("server default"),
                    prof.lang ? langName(prof.lang) : label("server default"),
                    label(prof.sample ? "clip" : "no clip")];
      main.appendChild(el("span", meta.join(" \u00b7 "), "muted"));
      row.appendChild(main);
      const edit = el("button", label("Edit"), "ghost small");
      edit.type = "button";
      edit.addEventListener("click", () => openProfile(prof));
      row.appendChild(edit);
      list.appendChild(row);
    }
  }

  function openProfile(prof) {
    editingProfile = prof ? prof.id : "";
    const form = $("ttsProfileForm");
    if (!form) return;
    form.hidden = false;
    $("ttsProfileName").value = prof ? prof.name : "";
    $("ttsProfileVoice").value = (prof && prof.voice) || "";
    fillLangSelect($("ttsProfileLang"), prof ? (prof.lang || "") : (state.character.lang || "en"));
    $("ttsProfileFile").value = "";
    $("ttsProfileDelete").hidden = !prof;
    $("ttsProfileMsg").textContent = "";
    $("ttsProfileName").focus();
  }

  function closeProfile() {
    editingProfile = null;
    const form = $("ttsProfileForm");
    if (form) form.hidden = true;
  }

  async function saveProfile() {
    const msg = $("ttsProfileMsg");
    msg.textContent = "";
    const name = $("ttsProfileName").value.trim();
    if (!name) { msg.textContent = label("Give the profile a name."); return; }
    const file = $("ttsProfileFile").files[0];
    if (file && file.size > SAMPLE_MAX) { msg.textContent = label("A voice sample can be up to 20 MB."); return; }
    try {
      const body = { name: name, voice: $("ttsProfileVoice").value, lang: $("ttsProfileLang").value };
      if (editingProfile) body.id = editingProfile;
      const saved = await api("POST", "/api/voice/profiles", body);
      if (file) {
        const r = await fetch("/api/voice/profiles/" + encodeURIComponent(saved.profile.id) + "/sample", {
          method: "POST", credentials: "same-origin",
          headers: { "X-Requested-With": "roleplay" }, body: file,
        });
        if (!r.ok) {
          const j = await safeJson(r);
          throw new Error((j && j.detail) || label("That clip could not be saved."));
        }
      }
      closeProfile();
      await load();
      renderPanel();
    } catch (err) {
      msg.textContent = err.message;
    }
  }

  async function deleteProfile() {
    if (!editingProfile) return;
    if (!window.confirm(label("Delete this voice profile and its clip?"))) return;
    try {
      await api("DELETE", "/api/voice/profiles/" + encodeURIComponent(editingProfile));
      closeProfile();
      await load();
      renderPanel();
    } catch (err) {
      $("ttsProfileMsg").textContent = err.message;
    }
  }

  async function preview() {
    if (!state.available) {
      setNote(offNote());
      return;
    }
    setNote("");
    stopSpeech();
    try {
      const pid = state.choice !== "character" ? state.choice : "";
      const r = await fetch("/api/voice/preview", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "roleplay" },
        body: JSON.stringify({ character: activeCharacter, profile: pid }),
      });
      if (r.ok) { playBlob(await r.blob(), null); return; }
      const j = await safeJson(r);
      setNote((j && j.detail) || label("The speech server could not read the sample."));
    } catch (e) {
      setNote(label("The speech server could not be reached."));
    }
  }

  function renderPanel() {
    const auto = $("ttsAuto");
    if (!auto) return;
    auto.checked = !!state.auto;
    const charRow = $("ttsCharVoiceRow");
    const charLabel = $("ttsCharVoiceLabel");
    if (charRow) charRow.hidden = !activeCharacter || !activeSolo;
    if (charLabel) charLabel.textContent = activeCharacter
      ? label("Voice for {name}", { name: activeCharacter }) : label("Character voice");
    fillChoiceSelect($("ttsCharVoice"), state.choice);
    const rate = $("ttsRate"), pitch = $("ttsPitch"), volume = $("ttsVolume");
    if (rate) { rate.value = state.rate; $("ttsRateVal").textContent = state.rate.toFixed(2) + "\u00d7"; }
    if (pitch) { pitch.value = state.pitch; $("ttsPitchVal").textContent = state.pitch.toFixed(2); }
    if (volume) { volume.value = state.volume; $("ttsVolumeVal").textContent = Math.round(state.volume * 100) + "%"; }
    const dialogue = $("ttsDialogue");
    if (dialogue) dialogue.checked = !!state.dialogue_only;
    renderProfiles();
    setNote(state.available ? "" : offNote());
  }

  async function refreshPanel() {
    if (!$("ttsAuto")) return;
    await load();
    renderPanel();
  }

  function initPanel() {
    const auto = $("ttsAuto");
    if (!auto) return;
    if (!panelBound) {
      panelBound = true;
      auto.addEventListener("change", () => savePrefs({ auto: auto.checked }));
      $("ttsCharVoice").addEventListener("change", () => {
        savePrefs({ choice: $("ttsCharVoice").value }, activeCharacter || undefined);
      });
      $("ttsRate").addEventListener("input", () => {
        const value = clamp($("ttsRate").value, 0.5, 2);
        $("ttsRateVal").textContent = value.toFixed(2) + "\u00d7";
        savePrefs({ rate: value });
      });
      $("ttsPitch").addEventListener("input", () => {
        const value = clamp($("ttsPitch").value, 0, 2);
        $("ttsPitchVal").textContent = value.toFixed(2);
        savePrefs({ pitch: value });
      });
      $("ttsVolume").addEventListener("input", () => {
        const value = clamp($("ttsVolume").value, 0, 1);
        $("ttsVolumeVal").textContent = Math.round(value * 100) + "%";
        if (activeAudio) activeAudio.volume = value;
        savePrefs({ volume: value });
      });
      $("ttsDialogue").addEventListener("change", () => savePrefs({ dialogue_only: $("ttsDialogue").checked }));
      $("ttsTest").addEventListener("click", preview);
      $("ttsStop").addEventListener("click", () => stopSpeech());
      $("ttsProfileNew").addEventListener("click", () => openProfile(null));
      $("ttsProfileSave").addEventListener("click", saveProfile);
      $("ttsProfileCancel").addEventListener("click", closeProfile);
      $("ttsProfileDelete").addEventListener("click", deleteProfile);
    }
    refreshPanel();
  }

  window.ttsMessageButton = messageButton;
  window.ttsAutoSpeak = autoSpeak;
  window.ttsInitPanel = initPanel;
  window.ttsSetCharacter = setCharacter;
  window.ttsLanguages = LANGS;

  // The speech server's state is needed before the first reply, so a speaker button knows where to send it.
  load();
})();
