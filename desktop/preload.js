// Runs before the page loads, in the same sandbox. The shell needs nothing from the page, so this
// only marks that the page is running inside the desktop window and reports the shell version.

"use strict";

const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("misoDesktop", {
  shell: true,
  electron: process.versions.electron,
});
