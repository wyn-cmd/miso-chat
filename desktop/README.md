# Desktop app

One window around the local server, packaged for Windows, macOS and Linux. The window is an
Electron shell; the server inside it is the same FastAPI app, frozen into a single executable with
PyInstaller. Nothing about the web app changes: the shell starts it on a free loopback port, waits
for the `MISO_URL=` line, and loads that address.

```
desktop/
  launcher.py              starts the server for the shell, prints MISO_URL, stops on input close
  miso-backend.spec        PyInstaller recipe for the server bundle
  build_backend.py         builds backend-dist/miso-backend on the current OS
  main.js                  the window: starts the backend, permission and link handling
  preload.js               marks the page as running in the desktop window
  electron-builder.config.js
  package.json
  build/icon.png           drawn by tools/make_icon.py
  tools/make_icon.py
```

## Where the data lives

The app never writes next to itself, because a macOS bundle is read-only and an installed app
should not mix with user data. Everything goes to the per-user folder:

| OS | Folder |
| --- | --- |
| Windows | `%APPDATA%\Miso Chat` |
| macOS | `~/Library/Application Support/Miso Chat` |
| Linux | `$XDG_DATA_HOME/miso-chat`, usually `~/.local/share/miso-chat` |

Inside it: `app.db`, `secret.key`, `characters/` and a `.env` for the API key. Uploaded icons,
backgrounds and voice profiles live in `app.db` and the account folders as they do on the server.

`characters/` is the character root. A build made with `MISO_BUNDLE_CHARACTERS=1` carries starter
characters that are copied there once, on first start; a build for other people has none, and the
characters are created or imported in the app.

## The first start

Without a Gemini API key the app writes a template `.env` into the data folder, says where it is,
and stops. Put the key in it and start the app again. The key is only read from that file and from
the environment; the admin page handles the other providers.

## Running the shell from source

    pip install -r requirements.txt
    cd desktop
    npm install
    npm start

`npm start` runs Electron, which starts `launcher.py` with `python` from `PATH`. Set `MISO_PYTHON`
to the interpreter of the project venv if `python` is not that one. The backend can also be run
alone, without the shell, which is how it is tested:

    python desktop/launcher.py --data-dir C:\temp\miso-data --char-root C:\temp\miso-chars

## Building

Build on the OS you are shipping. PyInstaller freezes the running platform and cannot cross-compile,
and the macOS `.app` needs macOS tools, so one machine cannot make all three.

    pip install -r requirements.txt -r desktop/requirements-build.txt
    python desktop/tools/make_icon.py
    python desktop/build_backend.py
    cd desktop
    npm install
    npm run dist:win      or: npm run dist:mac      or: npm run dist:linux

The backend lands in `desktop/backend-dist/miso-backend/` and is copied into the package as an
extra resource; the package itself is written to `dist/desktop/`.

| OS | Output | Notes |
| --- | --- | --- |
| Windows | NSIS installer `.exe` and `.msi` | Unsigned, so SmartScreen warns once |
| macOS | `.app` in `dist/desktop`, arm64 | Sign ad hoc, then zip it; see below |
| Linux | AppImage | Needs FUSE, or run it with `--appimage-extract-and-run` |

The NSIS installer is the one the built-in updater replaces on its own. The MSI is for a plain
Windows install, a scripted one, or a machine where an MSI is expected; it installs the same app,
and a newer release still reaches it through the update check, which runs the standard installer.

## Updates

The packaged app checks the repository's releases for a newer version: once about twenty seconds
after it starts, then every six hours while it stays open. The check uses the GitHub release the
packaging config points at (`wyn-cmd/miso-chat`), so nothing has to be configured on a user's
machine.

- Only a release whose version has no beta tag is offered. A version such as `1.6.2-beta-1` is
  skipped, so a beta never reaches people who are on a stable build.
- The download runs in the background. When it is ready, a dialog offers Restart now, and otherwise
  the update installs when the app closes.
- The updater needs the `latest.yml` and `.blockmap` files that electron-builder writes, plus the
  installer, all on the same release. `.github/workflows/desktop.yml` attaches them whenever a
  release is published, so tagging a release is the whole process.
- NSIS and AppImage installs update themselves. A macOS build is unsigned, so its update check stays
  quiet: macOS updates need a signed and notarised app.

## Building all three from one place

`.github/workflows/desktop.yml` runs the whole build on three GitHub runners, one per OS, and
uploads the artifacts. Start it from the Actions tab, or push a tag like `v1.6.1`. Publishing a
release builds all three and attaches the packages and update files to that release, which is where
the app's own update check reads them. This is the only way to get the macOS package without owning
a Mac: GitHub's `macos-latest` runner is Apple Silicon.

The workflow takes the package version from `VERSION`, so the desktop package follows the app
version without a second place to edit.

### macOS

`npm run dist:mac` builds an unpacked `.app` for Apple Silicon. electron-builder is told to leave it
`dir` rather than a `.dmg` because the app has to be signed after the backend is copied in, and the
PyInstaller binary inside is only ad hoc signed:

    app="$(find dist/desktop -maxdepth 2 -name '*.app' -type d | head -1)"
    codesign --force --deep --sign - "$app"
    ditto -c -k --keepParent "$app" dist/desktop/Miso-Chat-macos-arm64.zip

An unsigned build opens after a right-click, Open, or after

    xattr -dr com.apple.quarantine "/Applications/Miso Chat.app"

A `.dmg` and a Gatekeeper-clean install need a Developer ID certificate and notarisation, which
means an Apple Developer account. Intel Macs need an x64 build made on an Intel Mac; the arm64
package does not run there.

### Windows and Linux

No signing is set up. Windows warns once and remembers; Linux AppImages need the executable bit
(`chmod +x Miso-Chat-*.AppImage`) and FUSE 2, which most desktops have.

If a Windows build stops with `A required privilege is not held by the client` while unpacking
`winCodeSign`, the account cannot create symbolic links. Turn on Windows Developer Mode (Settings,
System, For developers) or build from an elevated prompt. The unpacked app can also be built without
the installer, which skips that download:

    npx electron-builder --config electron-builder.config.js --win --x64 --dir

The first build downloads Electron and the packaging tools, about 200 MB together.

## What the shell does beyond showing the page

- One window, single instance: a second launch focuses the first.
- Links to other sites open in the real browser, never in the app window.
- The microphone is allowed for the app page only, for voice calls; other permissions are refused.
  macOS asks for microphone access the first time, using the usage string in
  `electron-builder.config.js`.
- Closing the window ends the server: the shell closes its input and the launcher shuts the server
  down, with a hard kill after four seconds if it hangs.
