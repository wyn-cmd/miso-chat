// Miso Chat desktop shell.
//
// One window around the local server. The shell starts the bundled Python backend, waits for its
// "MISO_URL=..." line, and loads that address. Closing the window closes the backend's input, and
// the backend stops itself when its input ends.

"use strict";

const { app, BrowserWindow, Menu, dialog, session, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { spawn } = require("child_process");
const path = require("path");

const TITLE = "Miso Chat";
const BACKGROUND = "#10151f";
const START_TIMEOUT = 60000;
const UPDATE_FIRST_CHECK = 20000;          // long enough for the window to settle
const UPDATE_EVERY = 6 * 3600 * 1000;      // then every six hours while the app stays open

let win = null;
let backend = null;
let backendLog = "";
let serverOrigin = "";

function backendCommand() {
  // Packaged: the frozen server sits in resources/backend. From source: run the launcher with the
  // project's Python, which must have the server requirements installed.
  if (app.isPackaged) {
    const exe = process.platform === "win32" ? "miso-backend.exe" : "miso-backend";
    return { cmd: path.join(process.resourcesPath, "backend", exe), args: [] };
  }
  const python = process.env.MISO_PYTHON || (process.platform === "win32" ? "python" : "python3");
  return { cmd: python, args: [path.join(__dirname, "launcher.py")] };
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const { cmd, args } = backendCommand();
    let settled = false;
    let buffered = "";
    const finish = (err, url) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (err) reject(err); else resolve(url);
    };
    const timer = setTimeout(() => {
      finish(new Error("The server did not start within a minute."));
    }, START_TIMEOUT);

    backend = spawn(cmd, args, {
      cwd: app.getPath("home"),
      env: Object.assign({}, process.env, { PYTHONUNBUFFERED: "1" }),
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });

    backend.stdout.setEncoding("utf8");
    backend.stdout.on("data", (chunk) => {
      buffered += chunk;
      let cut;
      while ((cut = buffered.indexOf("\n")) >= 0) {
        const line = buffered.slice(0, cut).trim();
        buffered = buffered.slice(cut + 1);
        if (line.indexOf("MISO_URL=") === 0) finish(null, line.slice("MISO_URL=".length));
      }
    });

    backend.stderr.setEncoding("utf8");
    backend.stderr.on("data", (chunk) => {
      backendLog = (backendLog + chunk).slice(-4000);
      const line = chunk.split("\n").find((part) => part.indexOf("MISO_ERROR=") === 0);
      if (line) finish(new Error(line.slice("MISO_ERROR=".length).trim()));
    });

    backend.on("error", (err) => {
      finish(new Error("The server could not be started: " + err.message + "\n\n" + backendLog));
    });

    backend.on("exit", (code) => {
      if (!settled) {
        finish(new Error("The server stopped while starting (exit " + code + ").\n\n" + backendLog));
      }
    });
  });
}

function stopBackend() {
  const child = backend;
  backend = null;
  if (!child) return;
  try {
    child.stdin.end();
  } catch (err) {
    // already gone
  }
  // Giving the server a moment to shut down on its own keeps the database clean; a stuck one is
  // ended outright rather than left running without a window.
  setTimeout(() => {
    try {
      child.kill();
    } catch (err) {
      // already gone
    }
  }, 4000).unref();
}

function allowMediaOnly() {
  // The page needs the microphone for voice calls. Everything else a page could ask for is refused:
  // this window only ever shows the local server.
  const allowed = (permission) => permission === "media" || permission === "fullscreen";
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback) => {
    const from = contents.getURL() || "";
    callback(Boolean(serverOrigin) && from.indexOf(serverOrigin) === 0 && allowed(permission));
  });
  session.defaultSession.setPermissionCheckHandler((contents, permission) => {
    const from = (contents && contents.getURL()) || "";
    return Boolean(serverOrigin) && from.indexOf(serverOrigin) === 0 && allowed(permission);
  });
}

function buildMenu() {
  const template = [
    { role: "appMenu" },
    { role: "editMenu" },
    { role: "windowMenu" },
  ];
  if (!app.isPackaged) {
    template.splice(2, 0, {
      label: "View",
      submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "resetZoom" }],
    });
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow(url) {
  serverOrigin = new URL(url).origin;
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    show: false,
    title: TITLE,
    backgroundColor: BACKGROUND,
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  win.once("ready-to-show", () => win.show());
  win.on("closed", () => { win = null; });
  // A link to somewhere else belongs in the real browser, never in this window.
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    if (target.indexOf("http://") === 0 || target.indexOf("https://") === 0) shell.openExternal(target);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, target) => {
    if (target.indexOf(serverOrigin) !== 0) {
      event.preventDefault();
      if (target.indexOf("http") === 0) shell.openExternal(target);
    }
  });
  win.loadURL(url);
}

function report(err) {
  const message = (err && err.message) || String(err);
  dialog.showErrorBox(TITLE + " could not start", message);
  app.quit();
}

// Updates come from the repository's releases. Only a release without a beta tag is installed: a
// version with a dash after the patch number is a pre-release, and those are never offered here.
// The download runs in the background and is installed when the app closes, or at once if the
// reader says yes.
function setupUpdates() {
  if (!app.isPackaged) return;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;

  autoUpdater.on("update-available", (info) => {
    const version = String((info && info.version) || "");
    if (!version || version.indexOf("-") !== -1) return;   // betas wait for the next stable release
    autoUpdater.downloadUpdate().catch(() => { /* a failed download is tried again on the next check */ });
  });

  autoUpdater.on("update-downloaded", (info) => {
    const version = String((info && info.version) || "").replace(/^v/, "");
    const choice = dialog.showMessageBoxSync(win || null, {
      type: "info",
      title: TITLE,
      message: "Miso Chat " + version + " is ready to install.",
      detail: "It downloaded in the background. Restart now to use it, or it will install the next time the app closes.",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (choice === 0) autoUpdater.quitAndInstall();
  });

  // An update check must never disturb the app: a missing network, a rate limit or an unknown
  // release is simply tried again later.
  autoUpdater.on("error", () => {});

  const check = () => {
    autoUpdater.checkForUpdates().catch(() => {});
  };
  setTimeout(check, UPDATE_FIRST_CHECK);
  setInterval(check, UPDATE_EVERY);
}

async function boot() {
  allowMediaOnly();
  buildMenu();
  const url = await startBackend();
  createWindow(url);
  setupUpdates();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.on("before-quit", stopBackend);
  app.on("window-all-closed", () => app.quit());
  app.whenReady().then(boot).catch(report);
}
