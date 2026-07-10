import { app, BrowserWindow, clipboard, ipcMain, shell } from "electron";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";

import { BackendProcessController } from "./backendProcess.js";
import { diagnosticsPage } from "./diagnostics.js";

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

const dataRoot = path.join(process.env.LOCALAPPDATA?.trim() || app.getPath("appData"), "附录A编写工具");
app.setPath("userData", dataRoot);
app.setAppLogsPath(path.join(dataRoot, "logs"));

let mainWindow: BrowserWindow | undefined;
let backend: BackendProcessController | undefined;

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
  mainWindow = window;
  return window;
}

function backendController(): BackendProcessController {
  const webDist = app.isPackaged
    ? path.join(process.resourcesPath, "frontend")
    : path.resolve(app.getAppPath(), "..", "frontend", "dist");
  const executable = app.isPackaged
    ? path.join(process.resourcesPath, "backend", "fulua-backend.exe")
    : process.env.FULUA_BACKEND_PYTHON?.trim() || path.resolve(app.getAppPath(), "..", "backend", ".venv", "Scripts", "python.exe");
  const commandArguments = app.isPackaged ? [] : ["-m", "app.desktop_server"];
  const controller = new BackendProcessController({
    executable,
    commandArguments,
    cwd: app.isPackaged ? undefined : path.resolve(app.getAppPath(), "..", "backend"),
    dataRoot,
    webDist,
    sessionToken: randomBytes(32).toString("hex"),
  });
  controller.onUnexpectedExit((error) => void recoverOrDiagnose(error));
  return controller;
}

async function loadBackendPage(): Promise<void> {
  const window = mainWindow ?? createWindow();
  backend ??= backendController();
  const url = await backend.start();
  await window.loadURL(url);
}

async function recoverOrDiagnose(error: Error): Promise<void> {
  const window = mainWindow ?? createWindow();
  try {
    if (!backend) throw error;
    const url = await backend.restartOnce();
    await window.loadURL(url);
  } catch (restartError) {
    const details = backend?.diagnostics() ?? "未收到侧车错误输出。";
    await window.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(diagnosticsPage(restartError instanceof Error ? restartError.message : error.message, details))}`);
  }
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
ipcMain.handle("app:copy-diagnostics", (_event, details: unknown) => {
  if (typeof details !== "string") throw new Error("诊断信息无效");
  clipboard.writeText(details.replace(/session-token\s+\S+/gi, "session-token [已隐藏]"));
});
ipcMain.handle("app:retry-backend", async () => {
  if (!backend) backend = backendController();
  try {
    await backend.stop();
    backend = backendController();
    const url = await backend.start();
    await (mainWindow ?? createWindow()).loadURL(url);
  } catch (error) {
    await recoverOrDiagnose(error instanceof Error ? error : new Error("本地服务未能启动"));
  }
});

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });
  void app.whenReady().then(async () => {
    await mkdir(dataRoot, { recursive: true });
    createWindow();
    try {
      await loadBackendPage();
    } catch (error) {
      await recoverOrDiagnose(error instanceof Error ? error : new Error("本地服务未能启动"));
    }
  });
}

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (!backend) return;
  event.preventDefault();
  const controller = backend;
  backend = undefined;
  void controller.stop().finally(() => app.quit());
});
