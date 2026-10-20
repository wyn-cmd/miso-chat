// Admin backups. The backup password is set once; each backup needs it and a two-factor code.
// The download is a Blob, so the page never shows backup contents, only the file.
(function () {
  const byId = (id) => document.getElementById(id);

  function say(text) {
    byId("bkMsg").textContent = text || "";
  }

  async function loadStatus() {
    const s = await api("GET", "/api/admin/backup");
    const status = byId("bkStatus");
    const setForm = byId("bkSetForm");
    const makeForm = byId("bkMakeForm");
    setForm.hidden = true;
    makeForm.hidden = true;
    if (!s.two_factor_on) {
      status.textContent = "Turn on two-factor sign-in for your admin account (Settings on the chat page) before making backups.";
    } else if (!s.password_set) {
      status.textContent = "No backup password yet. Set one now; it can't be changed later, so keep it somewhere safe.";
      setForm.hidden = false;
    } else {
      status.textContent = "Backup password set. Each backup asks for it, along with your two-factor code.";
      makeForm.hidden = false;
    }
  }

  byId("bkSetForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    say("");
    try {
      await api("POST", "/api/admin/backup/password", {
        password: byId("bkNewPw").value,
        code: byId("bkSetCode").value.trim(),
      });
      byId("bkNewPw").value = "";
      byId("bkSetCode").value = "";
      say("Backup password set.");
      await loadStatus();
    } catch (e) {
      say(e.message);
    }
  });

  byId("bkMakeForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    say("Making the backup. This can take a minute for a large server.");
    const body = {
      kind: byId("bkKind").value,
      password: byId("bkPw").value,
      code: byId("bkCode").value.trim(),
    };
    try {
      const r = await fetch("/api/admin/backup", {
        method: "POST",
        headers: { "X-Requested-With": "roleplay", "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        let detail = "The backup failed.";
        try { detail = (await r.json()).detail || detail; } catch (e) { /* not JSON */ }
        throw new Error(typeof detail === "string" ? detail : detail.message || "The backup failed.");
      }
      const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "")?.[1] || "miso-backup.mbk";
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      byId("bkPw").value = "";
      byId("bkCode").value = "";
      say("Backup downloaded: " + name + ". Keep the backup password apart from the file.");
    } catch (e) {
      say(e.message);
    }
  });

  loadStatus().catch((e) => say(e.message));
})();
