// Voice call: the reader speaks, the speech server writes the words down, the character answers in the chat and
// that answer is read back with only the spoken lines. Turn-taking is driven by the microphone level, so a call
// needs no buttons beyond mute and end.
(function () {
  const el = (id) => document.getElementById(id);
  const label = (key, vars) => (window.tr ? window.tr(key, vars) : key);

  const SPEAK_MAX = 35000;      // one turn of speech is cut here so a call cannot record forever
  const SILENCE_MS = 800;       // silence after speech ends the turn
  const MIN_SPEECH_MS = 450;    // a cough or a click is not a turn
  const TICK_MS = 80;

  const S = {
    active: false, token: 0, char: "", phase: "idle",
    stream: null, ctx: null, analyser: null, gain: null, data: null,
    recorder: null, chunks: [], recording: false, discard: false,
    monitor: null, noise: 0.004, hits: 0, recordStart: 0, lastVoice: 0,
    hadVoice: false, muted: false, busy: false, audio: null, audioUrl: null, audioResolve: null,
    turnStart: 0, firstClip: false,
    volume: 1, timer: null, startedAt: 0,
  };

  function setPhase(phase) {
    S.phase = phase;
    const card = document.querySelector("#callOverlay .call-card");
    if (card) card.dataset.phase = phase;
  }

  function setStatus(text) {
    const node = el("callStatus");
    if (node) node.textContent = text;
  }

  function setHint(text) {
    const node = el("callHint");
    if (node) node.textContent = text || "";
  }

  function addLine(speaker, text, cls) {
    const log = el("callLog");
    if (!log) return null;
    const row = document.createElement("div");
    row.className = "call-line " + (cls || "");
    if (speaker) {
      const who = document.createElement("span");
      who.className = "call-who";
      who.textContent = speaker;
      row.appendChild(who);
    }
    const body = document.createElement("span");
    body.textContent = text;
    row.appendChild(body);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function addNote(text) {
    return addLine("", text, "note");
  }

  function clockText(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function startTimer() {
    S.startedAt = Date.now();
    const node = el("callTimer");
    if (node) node.textContent = "00:00";
    clearInterval(S.timer);
    S.timer = setInterval(() => {
      if (node) node.textContent = clockText(Date.now() - S.startedAt);
    }, 1000);
  }

  function stopTimer() {
    clearInterval(S.timer);
    S.timer = null;
  }

  function myName() {
    return (typeof me !== "undefined" && me && me.persona && me.persona.name) || "You";
  }

  // Only the text between quotation marks is spoken in a call. The client pulls the lines out itself so it can
  // start the first one before the rest of the reply has finished streaming.
  const QUOTED = /"([^"]+)"|\u201c([^\u201d]+)\u201d|\u300c([^\u300d]+)\u300d|\u300e([^\u300f]+)\u300f/g;
  function quotedList(text) {
    const out = [];
    QUOTED.lastIndex = 0;
    let hit;
    while ((hit = QUOTED.exec(String(text || "")))) {
      const line = (hit[1] || hit[2] || hit[3] || hit[4] || "").trim();
      if (line) out.push(line);
    }
    return out;
  }

  // Turnaround is measured from the end of the reader's speech to the first reply clip. The bars show the pace of the
  // speech server and the model: three for two seconds or less, two for five or less, one past that and red.
  function showSignal(ms) {
    const node = el("callSignal");
    if (!node) return;
    const bars = ms > 5000 ? 1 : ms > 2000 ? 2 : 3;
    node.dataset.bars = String(bars);
    const text = label("Reply in {sec}s", { sec: (ms / 1000).toFixed(1) });
    node.title = text;
    node.setAttribute("aria-label", text);
  }

  function resetSignal() {
    const node = el("callSignal");
    if (!node) return;
    node.dataset.bars = "3";
    node.title = "";
    node.setAttribute("aria-label", label("Response speed"));
  }

  function quiet(audio) {
    if (!audio) return;
    audio.onended = null;
    audio.onerror = null;
    try { audio.pause(); } catch (e) { /* already stopped */ }
  }

  function stopAudio() {
    if (S.audio) {
      quiet(S.audio);
      S.audio = null;
    }
    if (S.audioUrl) {
      try { URL.revokeObjectURL(S.audioUrl); } catch (e) { /* already revoked */ }
      S.audioUrl = null;
    }
    if (S.audioResolve) {
      const done = S.audioResolve;
      S.audioResolve = null;
      done();
    }
  }

  function playBlob(blob) {
    return new Promise((resolve) => {
      stopAudio();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.volume = Math.max(0, Math.min(1, S.volume));
      S.audio = audio;
      S.audioUrl = url;
      S.audioResolve = () => { S.audioResolve = null; resolve(); };
      const finish = () => {
        if (S.audio !== audio) return;
        stopAudio();
        resolve();
      };
      audio.onended = finish;
      audio.onerror = finish;
      audio.play().catch(finish);
    });
  }

  function clearMonitor() {
    clearInterval(S.monitor);
    S.monitor = null;
  }

  function level() {
    if (!S.analyser || !S.data) return 0;
    S.analyser.getFloatTimeDomainData(S.data);
    let sum = 0;
    for (let i = 0; i < S.data.length; i++) sum += S.data[i] * S.data[i];
    return Math.sqrt(sum / S.data.length);
  }

  function monitor() {
    if (S.monitor || !S.active) return;
    S.monitor = setInterval(tick, TICK_MS);
  }

  function tick() {
    if (!S.active) return;
    const rms = level();
    const threshold = Math.max(0.008, S.noise * 1.8);
    if (S.recording) {
      const now = performance.now();
      if (rms > threshold) { S.lastVoice = now; S.hadVoice = true; }
      if (now - S.recordStart > SPEAK_MAX) { stopRecording(); return; }
      if (now - S.lastVoice > SILENCE_MS && now - S.recordStart > MIN_SPEECH_MS) stopRecording();
      return;
    }
    if (S.busy || S.muted || S.audio) return;
    S.noise = Math.min(0.03, S.noise * 0.92 + rms * 0.08);
    if (rms > threshold) {
      S.hits += 1;
      if (S.hits >= 3) startRecording();
    } else {
      S.hits = 0;
    }
  }

  function recorderType() {
    if (typeof MediaRecorder === "undefined") return "";
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    for (const type of types) {
      try { if (MediaRecorder.isTypeSupported(type)) return type; } catch (e) { /* not supported */ }
    }
    return "";
  }

  function startRecording() {
    if (!S.stream || S.recording || !S.active) return;
    const type = recorderType();
    let rec;
    try {
      rec = type ? new MediaRecorder(S.stream, { mimeType: type }) : new MediaRecorder(S.stream);
    } catch (e) {
      setStatus(label("The microphone could not start."));
      setPhase("error");
      return;
    }
    S.chunks = [];
    S.discard = false;
    S.recorder = rec;
    S.recording = true;
    S.hadVoice = false;
    S.hits = 0;
    S.recordStart = performance.now();
    S.lastVoice = S.recordStart;
    setPhase("listening");
    setStatus(label("Listening…"));
    rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) S.chunks.push(ev.data); };
    rec.onerror = () => { S.recording = false; S.recorder = null; };
    rec.onstop = () => {
      const blob = new Blob(S.chunks, { type: rec.mimeType || "audio/webm" });
      S.recording = false;
      S.recorder = null;
      if (S.discard) { S.chunks = []; return; }
      const hadVoice = S.hadVoice;
      if (!hadVoice || blob.size < 1200) { resume(400); return; }
      handleUtterance(blob, S.token).catch(() => {});
    };
    try {
      rec.start();
    } catch (e) {
      S.recording = false;
      S.recorder = null;
      setStatus(label("The microphone could not start."));
      setPhase("error");
    }
  }

  function stopRecording() {
    const rec = S.recorder;
    if (!rec || !S.recording) return;
    if (rec.state === "recording") {
      try { rec.stop(); } catch (e) { S.recording = false; S.recorder = null; }
    } else {
      S.recording = false;
      S.recorder = null;
    }
  }

  function resume(delay) {
    setTimeout(() => {
      if (!S.active || S.busy) return;
      if (S.muted) { setPhase("idle"); setStatus(label("Microphone muted")); return; }
      setPhase("listening");
      setStatus(label("Listening…"));
      S.hits = 0;
      monitor();
    }, delay == null ? 250 : delay);
  }

  async function readJson(response) {
    try { return await response.json(); } catch (e) { return {}; }
  }

  async function transcribe(blob) {
    const url = "/api/voice/transcribe?character=" + encodeURIComponent(S.char);
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "roleplay",
        "Content-Type": blob.type || "application/octet-stream",
      },
      body: blob,
    });
    if (response.status === 401 && window.sendToLogin) window.sendToLogin();
    if (!response.ok) {
      const data = await readJson(response);
      throw new Error((data && data.detail) || label("The speech server could not hear that."));
    }
    const data = await readJson(response);
    return String((data && data.text) || "").trim();
  }

  function replyText(replies) {
    return (replies || []).map((reply) => String(reply.text || "").trim()).filter(Boolean).join("\n");
  }

  async function speak(text) {
    if (!text) return;
    setPhase("speaking");
    setStatus(label("{name} is speaking…", { name: S.char }));
    const response = await fetch("/api/voice/speak", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "roleplay" },
      body: JSON.stringify({
        text: text,
        speaker: S.char,
        character: S.char,
        dialogue_only: false,
        strict_dialogue: false,
      }),
    });
    if (response.status === 401 && window.sendToLogin) window.sendToLogin();
    if (response.status === 204) return;
    if (!response.ok) {
      const data = await readJson(response);
      throw new Error((data && data.detail) || label("The speech server could not read that."));
    }
    const blob = await response.blob();
    if (!S.firstClip) {
      S.firstClip = true;
      showSignal(performance.now() - S.turnStart);
    }
    await playBlob(blob);
  }

  async function handleUtterance(blob, token) {
    if (!S.active || token !== S.token) return;
    S.busy = true;
    S.turnStart = performance.now();
    S.firstClip = false;
    clearMonitor();
    setPhase("thinking");
    setStatus(label("Hearing you…"));
    let text = "";
    try {
      text = await transcribe(blob);
    } catch (err) {
      if (token !== S.token) return;
      S.busy = false;
      setPhase("error");
      setStatus(err.message);
      resume(1400);
      return;
    }
    if (token !== S.token) return;
    if (!text) {
      S.busy = false;
      setStatus(label("I did not catch that."));
      resume(900);
      return;
    }
    addLine(myName(), text, "you");
    setStatus(label("{name} is thinking…", { name: S.char }));

    // Speech starts on the first complete quoted line and runs while the rest of the reply streams, so the reader
    // does not wait for the whole turn. Each line is read once, because deltas carry the text so far, not a chunk.
    const queue = [];
    let seen = 0;
    let pumping = false;
    let speechError = null;
    let wake = null;
    let replies = [];
    let streamError = null;

    function pump() {
      if (pumping) return;
      pumping = true;
      (async () => {
        try {
          while (queue.length && token === S.token) {
            const line = queue.shift();
            await speak(line);
          }
        } catch (err) {
          speechError = err;
          queue.length = 0;
        } finally {
          pumping = false;
          if (wake) { const done = wake; wake = null; done(); }
        }
      })();
    }

    function feed(full) {
      const lines = quotedList(full);
      for (; seen < lines.length; seen++) {
        const line = lines[seen];
        if (!line) continue;
        addLine(S.char, line, "them");
        queue.push(line);
      }
      pump();
    }

    function idle() {
      if (!pumping) return Promise.resolve();
      return new Promise((res) => { wake = res; });
    }

    async function drain() {
      while (queue.length || pumping) {
        pump();
        await idle();
      }
    }

    try {
      await streamChat({ character: S.char, message: text, call: true }, {
        delta(data) { feed(String((data && data.text) || "")); },
        done(data) { replies = data.replies || []; },
        error(err) { streamError = err; },
      }, "/api/chat");
    } catch (err) {
      streamError = err;
    }
    if (token !== S.token) return;

    const full = replyText(replies);
    if (full) feed(full);
    await drain();
    if (token !== S.token) return;

    if (streamError) {
      S.busy = false;
      setPhase("error");
      setStatus(streamError.message);
      resume(1400);
      return;
    }
    if (speechError) {
      S.busy = false;
      setPhase("error");
      setStatus(speechError.message);
      resume(1400);
      return;
    }
    if (!seen && full) addNote(label("{name} stays quiet.", { name: S.char }));
    if (!S.firstClip && seen) {
      S.firstClip = true;
      showSignal(performance.now() - S.turnStart);
    }
    S.busy = false;
    resume(300);
  }

  function stopEverything() {
    clearMonitor();
    if (S.recorder && S.recording) {
      S.discard = true;
      try { S.recorder.stop(); } catch (e) { /* already stopped */ }
    }
    S.recording = false;
    S.recorder = null;
    S.chunks = [];
    stopAudio();
    if (S.stream) {
      for (const track of S.stream.getTracks()) {
        try { track.stop(); } catch (e) { /* already stopped */ }
      }
      S.stream = null;
    }
    if (S.ctx) {
      try { S.ctx.close(); } catch (e) { /* already closed */ }
      S.ctx = null;
    }
    S.analyser = null;
    S.gain = null;
    S.data = null;
  }

  function endCall() {
    if (!S.active) return;
    const who = S.char;
    S.active = false;
    S.token += 1;
    S.busy = false;
    stopEverything();
    stopTimer();
    const overlay = el("callOverlay");
    if (overlay) overlay.hidden = true;
    document.body.classList.remove("on-call");
    setPhase("idle");
    setStatus("");
    setHint(label("Use headphones so the microphone does not pick up the character."));
    const log = el("callLog");
    if (log) log.textContent = "";
    const mic = el("callMic");
    if (mic) {
      mic.setAttribute("aria-pressed", "false");
      mic.title = label("Mute your microphone");
      mic.setAttribute("aria-label", label("Mute your microphone"));
    }
    S.muted = false;
    S.char = "";
    if (who && typeof current !== "undefined" && current === who) {
      if (typeof loadHistory === "function") loadHistory().catch(() => {});
      if (typeof refreshStateCards === "function") refreshStateCards().catch(() => {});
    }
  }

  function goodbye() {
    endCall();
  }

  function paintAvatar() {
    const src = el("chatIcon");
    const dst = el("callAvatar");
    if (!dst) return;
    dst.className = "char-icon call-avatar" + (src && src.classList.contains("placeholder") ? " placeholder" : "");
    dst.innerHTML = src ? src.innerHTML : "";
    if (src) dst.style.background = src.style.background || "";
  }

  async function openCall() {
    if (S.active) return;
    if (typeof current === "undefined" || !current) return;
    if (typeof soloNames !== "undefined" && !soloNames.includes(current)) return;
    const who = current;
    let prefs;
    try {
      const response = await fetch("/api/voice?character=" + encodeURIComponent(who), { credentials: "same-origin" });
      if (!response.ok) throw new Error(label("Speech is turned off on this server."));
      prefs = await response.json();
    } catch (err) {
      const note = el("chatErr");
      if (note) note.textContent = err.message || label("Speech is turned off on this server.");
      return;
    }
    if (!prefs || !prefs.available) {
      const note = el("chatErr");
      if (note) note.textContent = label("Server speech is off. An admin can enable it under Admin, Model providers, Speech.");
      return;
    }

    S.char = who;
    S.volume = Number(prefs.volume);
    if (!Number.isFinite(S.volume)) S.volume = 1;
    S.muted = false;
    S.busy = false;
    S.noise = 0.004;
    S.hits = 0;
    S.active = true;
    S.token += 1;
    S.turnStart = 0;
    S.firstClip = false;
    resetSignal();

    const name = typeof displayName === "function" ? displayName(who) : who;
    const nameNode = el("callName");
    if (nameNode) nameNode.textContent = name;
    const log = el("callLog");
    if (log) log.textContent = "";
    paintAvatar();
    const overlay = el("callOverlay");
    if (overlay) overlay.hidden = false;
    document.body.classList.add("on-call");
    setPhase("idle");
    setStatus(label("Connecting…"));
    setHint(label("Use headphones so the microphone does not pick up the character."));
    startTimer();

    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error(label("This browser cannot use a microphone here."));
      }
      try {
        S.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        });
      } catch (e) {
        S.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      }
    } catch (err) {
      stopTimer();
      setPhase("error");
      setStatus(label("Microphone access is blocked. Allow it in the browser, then start the call again."));
      return;
    }
    if (!S.active) {
      stopEverything();
      return;
    }

    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      S.ctx = new Ctx();
      if (S.ctx.state === "suspended") await S.ctx.resume();
      const source = S.ctx.createMediaStreamSource(S.stream);
      S.analyser = S.ctx.createAnalyser();
      S.analyser.fftSize = 1024;
      S.analyser.smoothingTimeConstant = 0.6;
      S.data = new Float32Array(S.analyser.fftSize);
      S.gain = S.ctx.createGain();
      S.gain.gain.value = 0;
      source.connect(S.analyser);
      S.analyser.connect(S.gain);
      S.gain.connect(S.ctx.destination);
    } catch (err) {
      stopEverything();
      stopTimer();
      setPhase("error");
      setStatus(label("The microphone could not start on this browser."));
      return;
    }

    addNote(label("Call started"));
    setPhase("listening");
    setStatus(label("Listening…"));
    monitor();
    const end = el("callEnd");
    if (end) end.focus();
  }

  function toggleMic() {
    if (!S.active) return;
    S.muted = !S.muted;
    const mic = el("callMic");
    if (mic) {
      mic.setAttribute("aria-pressed", S.muted ? "true" : "false");
      const text = label(S.muted ? "Unmute your microphone" : "Mute your microphone");
      mic.title = text;
      mic.setAttribute("aria-label", text);
    }
    if (S.muted) {
      clearMonitor();
      if (S.recording) {
        S.discard = true;
        stopRecording();
      }
      setPhase("idle");
      setStatus(label("Microphone muted"));
    } else {
      S.hits = 0;
      setPhase("listening");
      setStatus(label("Listening…"));
      monitor();
    }
  }

  const callBtn = el("callBtn");
  if (callBtn) callBtn.addEventListener("click", () => { openCall().catch(() => {}); });
  const endBtn = el("callEnd");
  if (endBtn) endBtn.addEventListener("click", goodbye);
  const micBtn = el("callMic");
  if (micBtn) micBtn.addEventListener("click", toggleMic);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && S.active) goodbye();
  });
  window.addEventListener("pagehide", () => { if (S.active) endCall(); });

  window.misoCallActive = () => S.active;
  window.misoCallSetCharacter = (name) => {
    if (S.active && name !== S.char) endCall();
  };
})();
