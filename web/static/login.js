// Login page. Everyone goes to "/" after login; the app page handles a forced password change.
document.getElementById("loginForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const err = document.getElementById("err");
  err.textContent = "";
  try {
    await api("POST", "/api/login", {
      username: document.getElementById("user").value,
      password: document.getElementById("pass").value,
      remember: document.getElementById("remember").checked,
    });
    document.getElementById("pass").value = "";
    location.href = "/";
  } catch (e) {
    err.textContent = e.message;
  }
});
