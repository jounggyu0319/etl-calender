const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("fs");
const path = require("path");

const COLLECT_PATH = path.join(__dirname, "..", "extension", "lib", "myetl-collect.js");

function readCollectScript() {
  return fs.readFileSync(COLLECT_PATH, "utf8");
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 880,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  });
  win.loadFile(path.join(__dirname, "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

ipcMain.handle("get-collect-script", () => readCollectScript());
