// First-time two-factor set-up after a password sign-in. The pending session from the password step is
// the only thing that can read the QR code and finish it. Uses its own fetch, not common.js api(), because
// api() sends any 401 to the login page and a wrong code must stay on this page.
(function () {
  const byId = (id) => document.getElementById(id);

  async function call(method, path, body) {
    const r = await fetch(path, {
      method,
      headers: { "X-Requested-With": "roleplay", "Content-Type": "application/json" },
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) {
      const detail = data.detail;
      const err = new Error(typeof detail === "string" ? detail : (detail && detail.message) || "Something went wrong.");
      err.status = r.status;
      throw err;
    }
    return data;
  }

  function groupKey(secret) {
    // Groups of four make the key easier to type into an app.
    return secret.replace(/(.{4})(?=.)/g, "$1 ");
  }

  async function load() {
    try {
      const s = await call("GET", "/api/login/2fa/setup");
      byId("qr").src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(s.qr)));
      byId("secret").textContent = groupKey(s.secret);
      byId("code").focus();
    } catch (e) {
      // No pending first set-up (expired, or never signed in): start again from the login page.
      location.href = "/login";
    }
  }

  byId("setupForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    byId("err").textContent = "";
    try {
      await call("POST", "/api/login/2fa", { code: byId("code").value.trim() });
      location.href = "/";
    } catch (e) {
      byId("err").textContent = e.message;
      byId("code").select();
    }
  });

  load();
})();
