// electron-builder configuration for the Miso Chat desktop shell.
// The Python backend is built separately (see build_backend.py) and copied in as an extra resource.
const path = require("path");

const backendDir = path.join(__dirname, "backend-dist", "miso-backend");

module.exports = {
  appId: "chat.miso.desktop",
  productName: "Miso Chat",
  copyright: "MIT licensed",
  directories: { output: "../dist/desktop", buildResources: "build" },
  // Only the shell ships here; the server and the web pages travel in backend-dist.
  files: ["main.js", "preload.js", "package.json", "node_modules/**/*"],
  extraResources: [{ from: backendDir, to: "backend" }],
  // Where the app looks for updates: the releases of the public repository. electron-builder writes
  // app-update.yml from this, and the latest*.yml files it makes are uploaded with each release.
  publish: [{ provider: "github", owner: "wyn-cmd", repo: "miso-chat" }],
  win: {
    target: [{ target: "nsis", arch: ["x64"] }, { target: "msi", arch: ["x64"] }],
    icon: "build/icon.png",
  },
  nsis: { oneClick: false, allowToChangeInstallationDirectory: true, perMachine: false },
  msi: { oneClick: false, perMachine: false, runAfterFinish: true },
  mac: {
    target: [{ target: "dir", arch: ["arm64"] }],
    category: "public.app-category.social-networking",
    icon: "build/icon.png",
    extendInfo: {
      NSMicrophoneUsageDescription:
        "Miso Chat records your voice while you are in a call with a character.",
    },
  },
  linux: {
    target: [{ target: "AppImage", arch: ["x64"] }],
    category: "Network",
    icon: "build/icon.png",
  },
};
