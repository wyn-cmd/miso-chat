// Theme music: the tracks a character or an account has chosen, played while a reader is in that character's chat or
// card, or on that account's profile page. The music never stops on its own: a list of several cycles through its
// tracks in a random order, and a single track loops. A track fades
// in when it starts, fades out before the next one, and fades out when the reader leaves. A browser holds back sound
// until the reader has touched the page, so a track that is not yet allowed to start waits for the first touch.
// Volume and fade lengths are playback settings held on this browser, changed under Settings, Voice & audio.

const themeMusic = (function () {
  const STORE_KEY = "rp_theme_music";
  const OFF_KEY = "rp_theme_music_off";
  const DEFAULTS = { level: 0.4, fadeIn: 1.6, fadeOut: 1.2 };

  // Playback settings live on this browser: music volume and the fade lengths, changed under
  // Settings, Voice & audio. Defaults are the levels the music used to play at.
  function clamp(value, lo, hi, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(lo, Math.min(hi, n));
  }
  function loadPrefs() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { raw = null; }
    const p = Object.assign({}, DEFAULTS, raw && typeof raw === "object" ? raw : {});
    p.level = clamp(p.level, 0, 1, DEFAULTS.level);
    p.fadeIn = clamp(p.fadeIn, 0, 5, DEFAULTS.fadeIn);
    p.fadeOut = clamp(p.fadeOut, 0, 5, DEFAULTS.fadeOut);
    return p;
  }
  let prefs = loadPrefs();

  function savePrefs() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked: the choice lasts this page */ }
  }

  // Whether the music button has turned the music off, held on this browser.
  let muted = false;
  try { muted = localStorage.getItem(OFF_KEY) === "1"; } catch (e) { /* storage refused: music stays on */ }

  let scope = null;             // the chat, card or page the music belongs to; null when nothing should play
  let tracks = [];
  let index = 0;
  let audio = null;
  let ctx = null;
  let gain = null;
  let loadGen = 0;              // bumped on every new load, so a late error from an old track is ignored
  let fadeGen = 0;              // bumped on every fade, so a fade that was overtaken does not finish the job
  let failures = 0;             // tracks in a row that could not be played
  let fadingOut = false;        // the end of this track is already fading out
  let holding = false;          // a switch, stop or mute is fading the track out: its end must not start anything
  let touchWaiting = false;
  let resumeOnShow = false;
  let fadeTimer = null;
  let volTimer = null;
  let button = null;

  // The volume now, as the gain node or the element sees it.
  function volumeNow() {
    if (gain) return gain.gain.value;
    return audio ? audio.volume : 0;
  }

  // Sets the volume at once, with no fade. Used to silence a track before it is paused or changed.
  function setVolume(v) {
    fadeGen++;
    clearTimeout(fadeTimer);
    clearInterval(volTimer);
    if (gain) {
      const now = ctx.currentTime;
      gain.gain.cancelScheduledValues(now);
      gain.gain.setValueAtTime(v, now);
    } else if (audio) {
      audio.volume = v;
    }
  }

  // Moves the volume towards target over secs seconds, then calls done unless another fade has started since.
  function fadeTo(target, secs, done) {
    const g = ++fadeGen;
    clearTimeout(fadeTimer);
    clearInterval(volTimer);
    const finish = () => { if (g === fadeGen && done) done(); };
    if (gain) {
      const now = ctx.currentTime;
      gain.gain.cancelScheduledValues(now);
      gain.gain.setValueAtTime(gain.gain.value, now);
      gain.gain.linearRampToValueAtTime(target, now + secs);
      fadeTimer = setTimeout(finish, secs * 1000 + 30);
      return;
    }
    // A browser without Web Audio: the element's own volume is stepped instead.
    if (!audio) { finish(); return; }
    const from = audio.volume;
    const steps = Math.max(1, Math.round(secs * 1000 / 50));
    let step = 0;
    volTimer = setInterval(() => {
      step++;
      audio.volume = Math.min(1, Math.max(0, from + (target - from) * step / steps));
      if (step >= steps) { clearInterval(volTimer); finish(); }
    }, 50);
  }

  // Creates the element once. With Web Audio, the sound goes through a gain node, which makes the fades smooth on
  // every browser that has it; without it, the element's volume is used.
  function ensureAudio() {
    if (audio) return;
    audio = document.createElement("audio");
    audio.preload = "auto";
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("playing", () => { failures = 0; });
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      try {
        ctx = new AC();
        gain = ctx.createGain();
        gain.gain.value = 0;
        ctx.createMediaElementSource(audio).connect(gain).connect(ctx.destination);
        audio.volume = 1;
      } catch (e) {
        ctx = null;
        gain = null;
        audio.volume = 0;
      }
    } else {
      audio.volume = 0;
    }
  }

  // Points the element at the current track. Each copy is a source; the browser takes the first it can play.
  function loadCurrent() {
    const gen = ++loadGen;
    const track = tracks[index];
    fadingOut = false;
    audio.pause();
    audio.loop = tracks.length === 1;   // one track loops by itself, with no fade at the join
    audio.textContent = "";
    let left = track.sources.length;
    for (const s of track.sources) {
      const node = document.createElement("source");
      node.src = s.src;
      node.type = s.type;
      // A copy that fails, or that this browser cannot play, is passed over. Only when every copy has failed is the
      // track given up.
      node.addEventListener("error", () => {
        if (gen !== loadGen) return;
        if (--left === 0) trackFailed();
      });
      audio.appendChild(node);
    }
    audio.load();
  }

  // Plays the current track and fades it in. A browser that holds sound back waits for the first touch.
  function playNow() {
    if (!scope || muted || !audio) return;
    if (ctx && ctx.state !== "running") { waitForTouch(); return; }
    const gen = loadGen;
    audio.play().then(() => {
      if (gen === loadGen) fadeTo(prefs.level, prefs.fadeIn);
    }).catch((err) => {
      if (gen !== loadGen) return;
      if (err && err.name === "NotSupportedError") trackFailed();
      else waitForTouch();
    });
  }

  function waitForTouch() {
    if (touchWaiting) return;
    touchWaiting = true;
    const go = () => {
      for (const type of ["pointerdown", "keydown", "touchend"]) document.removeEventListener(type, go, true);
      touchWaiting = false;
      if (ctx && ctx.state !== "running") ctx.resume().catch(() => {});
      playNow();
    };
    for (const type of ["pointerdown", "keydown", "touchend"]) document.addEventListener(type, go, true);
  }

  // Starts the current track fresh: from the start of the list, with the element loaded and then faded in.
  function start() {
    holding = false;
    if (!scope || muted || !tracks.length) return;
    ensureAudio();
    setVolume(0);
    loadCurrent();
    playNow();
  }

  function isPlaying() {
    return !!audio && !audio.paused;
  }

  // The next track is drawn at random from the list. With more than one track it is never the one that just played,
  // so the music does not repeat a track back to back.
  function pickNext() {
    if (tracks.length < 2) return 0;
    let next;
    do {
      next = Math.floor(Math.random() * tracks.length);
    } while (next === index);
    return next;
  }

  // A track that cannot be played is passed over. If none of the list can be played, the music stops.
  function trackFailed() {
    failures++;
    if (failures >= tracks.length) {
      failures = 0;
      leave(null);
      return;
    }
    index = pickNext();
    setVolume(0);
    loadCurrent();
    playNow();
  }

  // Playlists: the end of a track fades out before it finishes, then the next one fades in. With a fade
  // out of zero the track simply ends, and a single looping track never fades at the join in either case.
  function onTime() {
    if (holding || tracks.length < 2 || fadingOut || !audio || !isFinite(audio.duration) || audio.duration <= 0) return;
    const left = audio.duration - audio.currentTime;
    if (prefs.fadeOut > 0 && left <= prefs.fadeOut) {
      fadingOut = true;
      fadeTo(0, Math.max(0.1, left));
    }
  }

  function onEnded() {
    if (holding || tracks.length < 2) return;
    index = pickNext();
    setVolume(0);
    loadCurrent();
    playNow();
  }

  // Fades the music out, pauses it, and then runs then(), so a page can leave once the sound has gone.
  function leave(then) {
    scope = null;
    tracks = [];
    updateButton();
    if (!audio || (!isPlaying() && volumeNow() === 0)) {
      if (audio) audio.pause();
      if (then) then();
      return;
    }
    holding = true;
    fadeTo(0, prefs.fadeOut, () => {
      holding = false;
      audio.pause();
      if (then) then();
    });
  }

  // Sets what should play now: a key for the chat, card or page, and its tracks. The same key keeps playing where it
  // was, with the list updated. A new key fades the old track out and fades the new one in. No tracks means silence.
  function set(key, list) {
    const next = Array.isArray(list) ? list.filter((t) => t && t.sources && t.sources.length) : [];
    if (!key || !next.length) {
      if (scope) leave(null);
      return;
    }
    if (key === scope) {
      // Same chat or page: keep the place in the list, and only update the tracks.
      tracks = next;
      if (index >= tracks.length) index = 0;
      updateButton();
      return;
    }
    scope = key;
    tracks = next;
    index = Math.floor(Math.random() * tracks.length);   // the first track is drawn at random too
    failures = 0;
    updateButton();
    if (audio && (isPlaying() || volumeNow() > 0)) {
      // Fade the old track out first, then start the new one; the fade-out stops if the scope changes again.
      const g = ++loadGen;
      holding = true;
      fadeTo(0, prefs.fadeOut, () => {
        if (g !== loadGen || !scope) return;
        audio.pause();
        start();
      });
      return;
    }
    start();
  }

  // A page's own music, such as an account's profile page. It plays unless a card is open over the page; closing the
  // card brings it back.
  let pageKey = null;
  let pageList = null;
  let overlay = false;

  function setPage(key, list) {
    pageKey = key;
    pageList = list;
    if (!overlay) set(key, list);
  }

  function show(key, list) {
    overlay = true;
    set(key, list);
  }

  function hide() {
    overlay = false;
    set(pageKey, pageList);
  }

  // The mute button: shown while a track is set for this page. It remembers the choice on this browser.
  function ensureButton() {
    if (button) return button;
    button = document.createElement("button");
    button.type = "button";
    button.className = "theme-music-toggle";
    button.hidden = true;
    button.addEventListener("click", toggleMuted);
    document.body.appendChild(button);
    return button;
  }

  function updateButton() {
    const b = ensureButton();
    const on = !muted;
    b.hidden = !scope || !tracks.length;
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.textContent = on ? "Music on" : "Music off";
    b.title = on && tracks[index] ? "Playing: " + tracks[index].title : "Theme music";
  }

  function toggleMuted() {
    muted = !muted;
    try { localStorage.setItem(OFF_KEY, muted ? "1" : "0"); } catch (e) { /* storage refused: the choice lasts this page */ }
    if (muted) {
      holding = true;
      fadeTo(0, prefs.fadeOut, () => { holding = false; if (audio) audio.pause(); });
    } else {
      start();
    }
    updateButton();
  }

  // The volume a slider just changed to reaches the track that is already playing with a short fade.
  function applyLevel() {
    if (!audio || !isPlaying()) return;
    fadeTo(prefs.level, 0.15);
  }

  // ---- the settings panel: music volume and fade lengths, under Voice & audio. The page calls this
  // when the sheet opens; missing controls mean the page has no sheet, so nothing is wired.
  function initPanel() {
    const volume = document.getElementById("tmVolume");
    if (!volume) return;
    const fadeIn = document.getElementById("tmFadeIn");
    const fadeOut = document.getElementById("tmFadeOut");
    const volumeVal = document.getElementById("tmVolumeVal");
    const fadeInVal = document.getElementById("tmFadeInVal");
    const fadeOutVal = document.getElementById("tmFadeOutVal");
    const show = () => {
      volumeVal.textContent = Math.round(prefs.level * 100) + "%";
      fadeInVal.textContent = prefs.fadeIn.toFixed(1) + " s";
      fadeOutVal.textContent = prefs.fadeOut.toFixed(1) + " s";
    };
    if (!volume.dataset.bound) {
      volume.dataset.bound = "1";
      volume.addEventListener("input", () => {
        prefs.level = clamp(volume.value, 0, 1, DEFAULTS.level);
        savePrefs();
        show();
        applyLevel();
      });
      fadeIn.addEventListener("input", () => {
        prefs.fadeIn = clamp(fadeIn.value, 0, 5, DEFAULTS.fadeIn);
        savePrefs();
        show();
      });
      fadeOut.addEventListener("input", () => {
        prefs.fadeOut = clamp(fadeOut.value, 0, 5, DEFAULTS.fadeOut);
        savePrefs();
        show();
      });
    }
    volume.value = prefs.level;
    fadeIn.value = prefs.fadeIn;
    fadeOut.value = prefs.fadeOut;
    show();
  }

  // A tab that is hidden stops the music, and it starts again, faded in, when the tab is shown.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (isPlaying()) {
        resumeOnShow = true;
        setVolume(0);
        audio.pause();
      }
    } else if (resumeOnShow) {
      resumeOnShow = false;
      playNow();
    }
  });

  window.addEventListener("pagehide", () => {
    if (audio) audio.pause();
  });

  // A link to another page of this site fades the music out first, so it does not stop with a cut. Links that open
  // in a new tab, or that the page itself handles, are left alone.
  document.addEventListener("click", (e) => {
    if (!scope || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || a.target || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    if (url.pathname === location.pathname && url.search === location.search) return;
    e.preventDefault();
    leave(() => { location.href = a.href; });
  });

  // ---- lists in the editors: one row per track, with move and remove. The page supplies the calls.

  // Draws the tracks into box. opts: onMove(id, direction) and onRemove(id), both optional.
  function renderList(box, items, opts) {
    const o = opts || {};
    box.textContent = "";
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "No theme music yet.";
      box.appendChild(p);
      return;
    }
    items.forEach((item, i) => {
      const row = document.createElement("div");
      row.className = "music-item";
      const name = document.createElement("span");
      name.className = "music-title";
      name.textContent = item.title + (item.duration ? "  " + formatLength(item.duration) : "");
      row.appendChild(name);
      if (o.onMove) {
        const up = actionButton("Move up", i === 0, () => o.onMove(item.id, -1));
        const down = actionButton("Move down", i === items.length - 1, () => o.onMove(item.id, 1));
        row.append(up, down);
      }
      if (o.onRemove) row.appendChild(actionButton("Remove", false, () => o.onRemove(item.id)));
      box.appendChild(row);
    });
  }

  function actionButton(label, disabled, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ghost small";
    b.textContent = label;
    b.disabled = disabled;
    b.addEventListener("click", onClick);
    return b;
  }

  function formatLength(seconds) {
    const s = Math.round(seconds);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  // Uploads one track to url as the raw file, with its title. Throws with the server's message when it is refused.
  async function upload(url, file, title) {
    const r = await fetch(url + "?title=" + encodeURIComponent(title || ""), {
      method: "POST",
      headers: { "X-Requested-With": "roleplay", "Content-Type": file.type || "application/octet-stream" },
      credentials: "same-origin",
      body: file,
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Upload failed.");
    return data;
  }

  return { set, setPage, show, hide, leave, renderList, upload, isMuted: () => muted, initPanel };
})();

window.themeMusicInitPanel = themeMusic.initPanel;
