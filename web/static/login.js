// Login page. Everyone goes to "/" after login; the app page handles a forced password change.
// With two-factor sign-in on, the password step opens a short pending session. The code step (or the
// first set-up, shown in a pop-up with a QR code and a key) finishes it.
const byId = (id) => document.getElementById(id);

function showCodeStep(message) {
  byId("loginForm").hidden = true;
  byId("otpForm").hidden = false;
  byId("otpErr").textContent = message || "";
  byId("otpCode").value = "";
  byId("otpCode").focus();
}

byId("loginForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = byId("err");
  err.textContent = "";
  try {
    const res = await api("POST", "/api/login", {
      username: byId("user").value,
      password: byId("pass").value,
      remember: byId("remember").checked,
    });
    byId("pass").value = "";
    if (res.setup_2fa) {
      // The account must set up two-factor sign-in before anything else. The set-up page does that.
      location.href = "/2fa-setup";
      return;
    }
    if (res.otp_required) {
      showCodeStep("");
      return;
    }
    location.href = "/";
  } catch (e) {
    err.textContent = e.message;
  }
});

byId("otpForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  byId("otpErr").textContent = "";
  try {
    await api("POST", "/api/login/2fa", { code: byId("otpCode").value.trim() });
    location.href = "/";
  } catch (e) {
    byId("otpErr").textContent = e.message;
  }
});

byId("otpBack").addEventListener("click", () => {
  byId("otpForm").hidden = true;
  byId("loginForm").hidden = false;
  byId("otpErr").textContent = "";
  byId("pass").focus();
});

