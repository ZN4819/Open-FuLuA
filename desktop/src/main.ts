import { app, BrowserWindow, ipcMain, shell } from "electron";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const STARTUP_HTML = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>附录A编写工具</title>
    <style>
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: "Microsoft YaHei UI", sans-serif; background: #f7f7f7; color: #171717; }
      main { padding: 32px 40px; border: 1px solid #e5e5e5; border-radius: 12px; background: #fff; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.08); }
      h1 { margin: 0; font-size: 20px; font-weight: 600; }
    </style>
  </head>
  <body><main><h1>正在启动附录A编写工具</h1></main></body>
</html>`;

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 720,
    height: 480,
    minWidth: 560,
    minHeight: 360,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  window.once("ready-to-show", () => window.show());
  void window.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(STARTUP_HTML)}`);
  return window;
}

ipcMain.handle("app:get-version", () => app.getVersion());
ipcMain.handle("app:open-logs-directory", async () => {
  const logsDirectory = app.getPath("logs");
  await mkdir(logsDirectory, { recursive: true });
  const errorMessage = await shell.openPath(logsDirectory);
  if (errorMessage) {
    throw new Error(errorMessage);
  }
});

void app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  app.quit();
});
