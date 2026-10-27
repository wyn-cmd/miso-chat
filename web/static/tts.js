// Read character replies aloud with the browser's own speech voices. The voice choices and sliders are kept
// in this browser only; no text or audio is sent to the server.
(function () {
  const KEY = "rp_tts";
  const DEFAULTS = { auto: false, voice: "", rate: 1, pitch: 1, volume: 1, voices: {} };
  const synth = window.speechSynthesis;
  const canSpeak = !!synth && typeof window.SpeechSynthesisUtterance === "function";
  let prefs = load();
  let activeBtn = null;
  let activeUtt = null;
  let panelBound = false;
  let activeCharacter = "";
  let activeSolo = true;

  function label(key, vars) {
    return window.tr ? window.tr(key, vars) : key;
  }

  function clamp(value, lo, hi) {
    const n = Number(value);
    if (!Number.isFinite(n)) return lo;
    return Math.max(lo, Math.min(hi, n));
  }

  function load() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { raw = null; }
    const p = Object.assign({}, DEFAULTS, raw && typeof raw === "object" ? raw : {});
    p.auto = !!p.auto;
    p.voice = typeof p.voice === "string" ? p.voice : "";
    p.rate = clamp(p.rate, 0.5, 2);
    p.pitch = clamp(p.pitch, 0, 2);
    p.volume = clamp(p.volume, 0, 1);
    p.voices = p.voices && typeof p.voices === "object" ? p.voices : {};
    return p;
  }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked: this page keeps the choice */ }
  }

  function voices() {
    if (!canSpeak) return [];
    try { return synth.getVoices().filter((v) => v && v.voiceURI); } catch (e) { return []; }
  }

  function sortedVoices() {
    return voices().slice().sort((a, b) => {
      const al = a.localService !== false, bl = b.localService !== false;
      if (al !== bl) return al ? -1 : 1;
      const lang = String(a.lang || "").localeCompare(String(b.lang || ""));
      return lang || String(a.name || "").localeCompare(String(b.name || ""));
    });
  }

  function voiceLabel(voice) {
    const lang = voice.lang ? " (" + voice.lang + ")" : "";
    return (voice.name || voice.voiceURI) + lang;
  }

  function chooseVoice(speaker) {
    const wanted = (speaker && prefs.voices[String(speaker)]) || prefs.voice;
    if (!wanted) return null;
    return voices().find((v) => v.voiceURI === wanted || v.name === wanted) || null;
  }

  function setButton(btn, on) {
    if (!btn) return;
    btn.classList.toggle("speaking", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    const text = label(on ? "Stop reading" : "Read aloud");
    btn.title = text;
    btn.setAttribute("aria-label", text);
  }

  function finish(utterance) {
    if (activeUtt !== utterance) return;
    activeUtt = null;
    setButton(activeBtn, false);
    activeBtn = null;
  }

  function stopSpeech() {
    if (!canSpeak) return;
    const old = activeUtt;
    activeUtt = null;
    if (old) {
      old.onend = null;
      old.onerror = null;
    }
    try { synth.cancel(); } catch (e) { /* nothing is playing */ }
    setButton(activeBtn, false);
    activeBtn = null;
  }

  function utteranceFor(text, speaker) {
    const utterance = new SpeechSynthesisUtterance(text);
    const voice = chooseVoice(speaker);
    if (voice) {
      utterance.voice = voice;
      utterance.lang = voice.lang || "";
    }
    utterance.rate = prefs.rate;
    utterance.pitch = prefs.pitch;
    utterance.volume = prefs.volume;
    return utterance;
  }

  function speak(text, speaker, btn) {
    if (!canSpeak) return false;
    text = String(text || "").trim();
    if (!text) return false;
    if (btn && btn === activeBtn && activeUtt) {
      stopSpeech();
      return true;
    }
    stopSpeech();
    const utterance = utteranceFor(text, speaker);
    utterance.onend = () => finish(utterance);
    utterance.onerror = () => finish(utterance);
    activeUtt = utterance;
    activeBtn = btn || null;
    setButton(activeBtn, true);
    try { synth.speak(utterance); } catch (e) { finish(utterance); return false; }
    return true;
  }

  function textOf(box) {
    const body = box && box.querySelector(".text");
    return body ? body.textContent.trim() : "";
  }

  function messageButton(box, speaker) {
    if (!canSpeak) return null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "speak-btn";
    btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 8a5 5 0 0 1 0 8M18.5 5.5a8.5 8.5 0 0 1 0 13"/></svg>';
    setButton(btn, false);
    btn.addEventListener("click", () => speak(textOf(box), speaker, btn));
    return btn;
  }

  function autoSpeak(box, speaker) {
    if (!canSpeak || !prefs.auto) return;
    const text = textOf(box);
    if (!text) return;
    const utterance = utteranceFor(text, speaker);
    if (activeUtt) {
      utterance.onend = utterance.onerror = () => {};
      try { synth.speak(utterance); } catch (e) { /* the browser refused the queued speech */ }
      return;
    }
    utterance.onend = () => finish(utterance);
    utterance.onerror = () => finish(utterance);
    activeUtt = utterance;
    activeBtn = box.querySelector(".speak-btn");
    setButton(activeBtn, true);
    try { synth.speak(utterance); } catch (e) { finish(utterance); }
  }

  function currentCharacter() {
    return activeCharacter;
  }

  function setCharacter(name, solo) {
    activeCharacter = String(name || "");
    activeSolo = solo !== false;
    const sheet = document.getElementById("settingsSheet");
    if (sheet && !sheet.hidden) initPanel();
  }

  function fillVoiceSelect(select, saved, emptyLabel) {
    if (!select) return;
    select.textContent = "";
    select.appendChild(new Option(emptyLabel, ""));
    const list = sortedVoices();
    for (const voice of list) select.appendChild(new Option(voiceLabel(voice), voice.voiceURI));
    if (saved && !list.some((v) => v.voiceURI === saved)) {
      select.appendChild(new Option(label("Saved voice (unavailable)"), saved));
    }
    select.value = saved || "";
    if (select.value !== (saved || "")) select.value = "";
  }

  function setNote(text) {
    const note = document.getElementById("ttsNote");
    if (note) note.textContent = text || "";
  }

  function initPanel() {
    const auto = document.getElementById("ttsAuto");
    if (!auto) return;
    const voice = document.getElementById("ttsVoice");
    const charVoice = document.getElementById("ttsCharVoice");
    const charRow = document.getElementById("ttsCharVoiceRow");
    const charLabel = document.getElementById("ttsCharVoiceLabel");
    const rate = document.getElementById("ttsRate");
    const pitch = document.getElementById("ttsPitch");
    const volume = document.getElementById("ttsVolume");
    const rateVal = document.getElementById("ttsRateVal");
    const pitchVal = document.getElementById("ttsPitchVal");
    const volumeVal = document.getElementById("ttsVolumeVal");
    const test = document.getElementById("ttsTest");
    const stop = document.getElementById("ttsStop");
    const controls = [auto, voice, charVoice, rate, pitch, volume, test, stop];

    if (!panelBound) {
      panelBound = true;
      auto.addEventListener("change", () => { prefs.auto = auto.checked; save(); });
      voice.addEventListener("change", () => { prefs.voice = voice.value; save(); });
      charVoice.addEventListener("change", () => {
        const who = currentCharacter();
        if (!who) return;
        if (charVoice.value) prefs.voices[who] = charVoice.value;
        else delete prefs.voices[who];
        save();
      });
      rate.addEventListener("input", () => {
        prefs.rate = clamp(rate.value, 0.5, 2);
        rateVal.textContent = prefs.rate.toFixed(2) + "\u00d7";
        save();
      });
      pitch.addEventListener("input", () => {
        prefs.pitch = clamp(pitch.value, 0, 2);
        pitchVal.textContent = prefs.pitch.toFixed(2);
        save();
      });
      volume.addEventListener("input", () => {
        prefs.volume = clamp(volume.value, 0, 1);
        volumeVal.textContent = Math.round(prefs.volume * 100) + "%";
        save();
      });
      test.addEventListener("click", () => {
        const who = currentCharacter();
        speak(label("This is how {name} will sound.", { name: who || label("this character") }), who, null);
      });
      stop.addEventListener("click", stopSpeech);
      if (synth.addEventListener) {
        synth.addEventListener("voiceschanged", () => {
          const sheet = document.getElementById("settingsSheet");
          if (sheet && !sheet.hidden) initPanel();
        });
      }
    }

    if (!canSpeak) {
      for (const el of controls) if (el) el.disabled = true;
      setNote(label("This browser does not support speech playback."));
      return;
    }

    const who = currentCharacter();
    auto.checked = prefs.auto;
    fillVoiceSelect(voice, prefs.voice, label("Browser default"));
    if (charRow) charRow.hidden = !who || !activeSolo;
    if (charLabel) charLabel.textContent = who ? label("Voice for {name}", { name: who }) : label("Character voice");
    fillVoiceSelect(charVoice, who ? (prefs.voices[who] || "") : "", label("Use default voice"));
    rate.value = prefs.rate;
    pitch.value = prefs.pitch;
    volume.value = prefs.volume;
    rateVal.textContent = prefs.rate.toFixed(2) + "\u00d7";
    pitchVal.textContent = prefs.pitch.toFixed(2);
    volumeVal.textContent = Math.round(prefs.volume * 100) + "%";
    for (const el of controls) if (el) el.disabled = false;
    setNote(voices().length ? "" : label("No speech voices are available in this browser."));
  }

  window.ttsMessageButton = messageButton;
  window.ttsAutoSpeak = autoSpeak;
  window.ttsInitPanel = initPanel;
  window.ttsSetCharacter = setCharacter;
})();
