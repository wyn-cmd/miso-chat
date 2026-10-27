// Two-factor sign-in on the chat page's Settings sheet: turn it on, finish set-up, or turn it off.
// Uses common.js (api). Every dialog is built with DOM calls, so server text is never parsed as markup.
(function () {
  const byId = (id) => document.getElementById(id);
  let state = "off";      // "on", "setup" (enabled, not yet confirmed) or "off"
  let required = false;   // an admin requires it on this account, so it can't be turned off here

  function openDialog(title, build) {
    const d = document.createElement("dialog");
    d.className = "twofa-dialog";
    const h = document.createElement("h2");
    h.textContent = title;
    const msg = document.createElement("p");
    msg.className = "error";
    msg.setAttribute("aria-live", "polite");
    d.append(h, msg);
    build(d, msg);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost";
    cancel.textContent = window.tr("Cancel");
    // Remove explicitly: some browsers do not fire the close event, and a stale dialog would stack up.
    cancel.addEventListener("click", () => { d.close(); d.remove(); });
    d.append(cancel);
    d.addEventListener("close", () => d.remove());
    document.body.append(d);
    d.showModal();
    return d;
  }

  function codeField(labelText) {
    const label = document.createElement("label");
    label.textContent = labelText + " ";
    const input = document.createElement("input");
    input.inputMode = "numeric";
    input.autocomplete = "one-time-code";
    input.pattern = "[0-9]{6}";
    input.maxLength = 6;
    input.required = true;
    label.append(input);
    return { label, input };
  }

  function turnOn(onDone) {
    api("POST", "/api/2fa/start", {}).then((data) => {
      openDialog(window.tr("Set up two-factor sign-in"), (d, msg) => {
        const img = document.createElement("img");
        img.alt = window.tr("QR code for your authenticator app");
        img.width = 220;
        img.height = 220;
        img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(data.qr)));
        const hint = document.createElement("p");
        hint.className = "muted";
        hint.textContent = window.tr("Scan the code in your authenticator app, or type the key in. Then enter the 6-digit code it shows.");
        const key = document.createElement("code");
        key.className = "secret";
        key.textContent = data.secret;
        const form = document.createElement("form");
        form.className = "stack narrow";
        const field = codeField(window.tr("Code from the app"));
        const go = document.createElement("button");
        go.type = "submit";
        go.textContent = window.tr("Turn on");
        form.append(field.label, go);
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          try {
            await api("POST", "/api/2fa/confirm", { code: field.input.value.trim() });
            d.close();
            d.remove();
            await refresh();
            if (onDone) onDone();
          } catch (e) {
            msg.textContent = e.message;
          }
        });
        d.append(img, hint, key, form);
      });
    }).catch((e) => window.alert(e.message));
  }

  function turnOff() {
    openDialog(window.tr("Turn off two-factor sign-in"), (d, msg) => {
      const form = document.createElement("form");
      form.className = "stack narrow";
      const pwLabel = document.createElement("label");
      pwLabel.textContent = window.tr("Password") + " ";
      const pw = document.createElement("input");
      pw.type = "password";
      pw.autocomplete = "current-password";
      pw.required = true;
      pwLabel.append(pw);
      const field = codeField(window.tr("Code from the app"));
      const go = document.createElement("button");
      go.type = "submit";
      go.textContent = window.tr("Turn off");
      form.append(pwLabel, field.label, go);
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        try {
          await api("POST", "/api/2fa/disable", { password: pw.value, code: field.input.value.trim() });
          d.close();
          d.remove();
          await refresh();
        } catch (e) {
          msg.textContent = e.message;
        }
      });
      d.append(form);
    });
  }

  function paint(btn, note) {
    if (state === "on") {
      note.textContent = required ? window.tr("On, required by an admin") : window.tr("On");
      btn.textContent = window.tr("Turn off");
      btn.disabled = required;
    } else if (state === "setup") {
      note.textContent = window.tr("Not finished");
      btn.textContent = window.tr("Finish set-up");
      btn.disabled = false;
    } else {
      note.textContent = required ? window.tr("Required by an admin, not yet set up") : window.tr("Off");
      btn.textContent = window.tr("Turn on");
      btn.disabled = false;
    }
    btn.hidden = false;
  }

  async function refresh() {
    const btn = byId("twofaBtn");
    const note = byId("twofaNote");
    if (!btn || !note) return;
    const s = await api("GET", "/api/2fa");
    state = s.state;
    required = s.required;
    paint(btn, note);
  }

  const btn = byId("twofaBtn");
  if (btn) {
    btn.addEventListener("click", () => {
      if (state === "on") turnOff();
      else turnOn();
    });
    refresh().catch(() => { /* signed out or in set-up: the sheet keeps its default text */ });
  }
  // The admin page opens the same set-up dialog from its own row; onDone reloads that page's table.
  window.misoTwoFactor = { setup: (onDone) => turnOn(onDone) };
})();
