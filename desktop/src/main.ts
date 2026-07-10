import { app, BrowserWindow, clipboard, dialog, ipcMain, shell } from "electron";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";

import { BackendProcessController } from "./backendProcess.js";
import { BackupCoordinator } from "./backupActions.js";
import { diagnosticsPage, sanitizeDiagnostics } from "./diagnostics.js";
import { focusExistingWindow, QuitGuard, runSingleInstance } from "./lifecycle.js";
import { MigrationCoordinator, type MigrationOutcome } from "./migrationWindow.js";
import { RuntimeApiClient } from "./runtimeApi.js";

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
let backendOrigin: string | undefined;
let sessionToken = "";
let quitApproved = false;

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
  sessionToken = randomBytes(32).toString("hex");
  const controller = new BackendProcessController({
    executable,
    commandArguments,
    cwd: app.isPackaged ? undefined : path.resolve(app.getAppPath(), "..", "backend"),
    dataRoot,
    webDist,
    sessionToken,
  });
  controller.onUnexpectedExit((error) => void recoverOrDiagnose(error));
  return controller;
}

async function startBackend(): Promise<string> {
  if (backendOrigin) return backendOrigin;
  backend ??= backendController();
  backendOrigin = await backend.start();
  return backendOrigin;
}

async function loadBackendPage(): Promise<void> {
  const window = mainWindow ?? createWindow();
  const url = await startBackend();
  await window.loadURL(url);
}

function runtimeApi(): RuntimeApiClient {
  if (!backendOrigin) throw new Error("本地服务尚未就绪");
  return new RuntimeApiClient(backendOrigin, sessionToken);
}

async function restartSidecarAndReload(): Promise<void> {
  const controller = backend;
  if (controller) await controller.stop();
  if (backend === controller) backend = undefined;
  backendOrigin = undefined;
  await loadBackendPage();
}

async function runMigrationFlow(): Promise<MigrationOutcome> {
  const coordinator = new MigrationCoordinator({
    chooseSourceDirectory: async () => {
      const result = await dialog.showOpenDialog(mainWindow ?? createWindow(), {
        title: "选择旧版附录A数据目录",
        properties: ["openDirectory"],
      });
      return result.canceled ? undefined : result.filePaths[0];
    },
    preflight: async (sourceRoot) => await runtimeApi().preflight(sourceRoot),
    migrate: async (sourceRoot) => await runtimeApi().migrate(sourceRoot),
    restartSidecar: restartSidecarAndReload,
  });
  return await coordinator.begin("migrate");
}

async function restoreBackup(backupId: string) {
  const coordinator = new BackupCoordinator({
    listBackups: async () => await runtimeApi().listBackups(),
    restore: async (id) => await runtimeApi().restore(id),
    restartSidecar: restartSidecarAndReload,
  });
  return await coordinator.restore(backupId);
}

async function offerFirstRunChoice(): Promise<void> {
  const choice = await dialog.showMessageBox(mainWindow ?? createWindow(), {
    type: "question",
    buttons: ["新建空数据", "迁移旧数据"],
    defaultId: 0,
    cancelId: 0,
    title: "附录A编写工具",
    message: "欢迎使用附录A编写工具",
    detail: "您可以新建空数据，或从旧版目录复制数据。旧数据始终保留。",
  });
  if (choice.response !== 1) return;

  const outcome = await runMigrationFlow();
  if (outcome.status === "migrated" || outcome.status === "cancelled" || outcome.status === "new-data") return;
  await dialog.showMessageBox(mainWindow ?? createWindow(), {
    type: "warning",
    title: "未迁移旧数据",
    message: outcome.message,
    detail: "旧数据没有被修改。您可以继续使用空数据，或稍后重新选择旧数据目录。",
  });
}

async function recoverOrDiagnose(error: Error): Promise<void> {
  const window = mainWindow ?? createWindow();
  try {
    if (!backend) throw error;
    const url = await backend.restartOnce();
    backendOrigin = url;
    await window.loadURL(url);
  } catch (restartError) {
    await showDiagnostics(restartError instanceof Error ? restartError : error, backend, window);
  }
}

async function showDiagnostics(error: Error, controller = backend, preferredWindow?: BrowserWindow): Promise<void> {
  const window = preferredWindow && !preferredWindow.isDestroyed()
    ? preferredWindow
    : mainWindow && !mainWindow.isDestroyed()
      ? mainWindow
      : createWindow();
  const details = controller?.diagnostics() ?? "未收到侧车错误输出。";
  await window.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(diagnosticsPage(error.message, details))}`);
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
  clipboard.writeText(sanitizeDiagnostics(details));
});
ipcMain.handle("app:retry-backend", async () => {
  if (!backend) backend = backendController();
  try {
    await backend.stop();
    backend = backendController();
    const url = await backend.start();
    backendOrigin = url;
    await (mainWindow ?? createWindow()).loadURL(url);
  } catch (error) {
    await recoverOrDiagnose(error instanceof Error ? error : new Error("本地服务未能启动"));
  }
});
ipcMain.handle("runtime:migrate-legacy", async () => await runMigrationFlow());
ipcMain.handle("runtime:list-backups", async () => await runtimeApi().listBackups());
ipcMain.handle("runtime:restore-backup", async (_event, backupId: unknown) => {
  if (typeof backupId !== "string" || !/^[a-zA-Z0-9._-]+$/.test(backupId)) throw new Error("备份标识无效");
  return await restoreBackup(backupId);
});

runSingleInstance(app.requestSingleInstanceLock(), () => app.quit(), () => {
  app.on("second-instance", () => {
    focusExistingWindow(mainWindow);
  });
  void app.whenReady().then(async () => {
    await mkdir(dataRoot, { recursive: true });
    const isFirstRun = !existsSync(path.join(dataRoot, "data", "app.db"));
    createWindow();
    try {
      await startBackend();
      if (isFirstRun) await offerFirstRunChoice();
      await loadBackendPage();
    } catch (error) {
      await recoverOrDiagnose(error instanceof Error ? error : new Error("本地服务未能启动"));
    }
  });
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", (event) => {
  if (quitApproved || !backend) return;
  event.preventDefault();
  const controller = backend;
  const guard = new QuitGuard(controller, async (error, retainedController) => {
    await showDiagnostics(error, retainedController);
  });
  void guard.stopForQuit().then((canQuit) => {
    if (!canQuit) return;
    if (backend === controller) backend = undefined;
    quitApproved = true;
    app.quit();
  });
});
