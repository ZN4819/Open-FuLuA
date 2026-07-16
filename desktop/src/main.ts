import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell } from "electron";
import electronUpdater = require("electron-updater");
import { execFile } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { promisify } from "node:util";

import { BackendProcessController } from "./backendProcess.js";
import { BackupCoordinator } from "./backupActions.js";
import { diagnosticsPage, sanitizeDiagnostics } from "./diagnostics.js";
import { focusExistingWindow, QuitGuard, runSingleInstance } from "./lifecycle.js";
import { MigrationCoordinator, type MigrationOutcome } from "./migrationWindow.js";
import { RestoreWindowCoordinator } from "./restoreWindow.js";
import { RuntimeApiClient } from "./runtimeApi.js";
import { JsonRecoveryMarkerStore, RecoveryCoordinator } from "./recovery.js";
import { UpdateCoordinator, type AutoUpdaterPort } from "./updater.js";
import {
  GuardedStartupCoordinator,
  GuardedStartupSingleFlight,
  RecoverySessionGate,
  UnexpectedExitRecovery,
} from "./startupGate.js";

const execFileAsync = promisify(execFile);
const { autoUpdater } = electronUpdater;
const CURRENT_SCHEMA_VERSION = "9";

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
const recoveryMarkers = new JsonRecoveryMarkerStore(path.join(dataRoot, "recovery"));
let updateCoordinator: UpdateCoordinator | undefined;
const recoverySessionGate = new RecoverySessionGate();

function backendCommand(): { executable: string; prefix: string[]; cwd?: string; webDist: string } {
  const webDist = app.isPackaged
    ? path.join(process.resourcesPath, "frontend")
    : path.resolve(app.getAppPath(), "..", "frontend", "dist");
  return app.isPackaged
    ? { executable: path.join(process.resourcesPath, "backend", "fulua-backend.exe"), prefix: [], webDist }
    : {
        executable: process.env.FULUA_BACKEND_PYTHON?.trim() || path.resolve(app.getAppPath(), "..", "backend", ".venv", "Scripts", "python.exe"),
        prefix: ["-m", "app.desktop_server"],
        cwd: path.resolve(app.getAppPath(), "..", "backend"),
        webDist,
      };
}

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
  const command = backendCommand();
  sessionToken = randomBytes(32).toString("hex");
  const controller = new BackendProcessController({
    executable: command.executable,
    commandArguments: command.prefix,
    cwd: command.cwd,
    dataRoot,
    webDist: command.webDist,
    sessionToken,
  });
  controller.onUnexpectedExit((error) => void unexpectedExitRecovery.handle(error));
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

async function runOfflineRecovery(
  action: "integrity" | "list" | "restore" | "prepare-schema-upgrade",
  backupId?: string,
): Promise<Record<string, unknown> | undefined> {
  if (backupId !== undefined && !/^[A-Za-z0-9._-]+$/.test(backupId)) return undefined;
  const controller = backend;
  try {
    if (controller) await controller.stop();
  } catch {
    return undefined;
  }
  backend = undefined;
  backendOrigin = undefined;
  const command = backendCommand();
  try {
    const commandArguments = [
      ...command.prefix,
      "--data-root", dataRoot,
      "--offline-recovery", action,
      ...(backupId ? ["--backup-id", backupId] : []),
    ];
    const result = await execFileAsync(command.executable, commandArguments, { cwd: command.cwd, windowsHide: true, encoding: "utf8" });
    return JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1) ?? "{}") as Record<string, unknown>;
  } catch {
    return undefined;
  }
}

async function prepareSchemaUpgrade(): Promise<void> {
  if (await recoveryMarkers.readPendingUpgrade()) return;
  const event = await runOfflineRecovery("prepare-schema-upgrade");
  if (event?.event !== "FULUA_OFFLINE_SCHEMA_UPGRADE" || typeof event.prepared !== "boolean") {
    throw new Error("无法完成数据库升级前检查");
  }
  const sourceSchema = typeof event.source_schema === "string" ? event.source_schema : "";
  const targetSchema = typeof event.target_schema === "string" ? event.target_schema : "";
  if (targetSchema && targetSchema !== CURRENT_SCHEMA_VERSION) {
    throw new Error("数据库升级目标版本与客户端不一致");
  }
  if (!event.prepared) {
    if (sourceSchema && Number(sourceSchema) > Number(CURRENT_SCHEMA_VERSION)) {
      throw new Error("本地数据库版本高于当前客户端");
    }
    return;
  }
  if (!sourceSchema || targetSchema !== CURRENT_SCHEMA_VERSION
    || typeof event.backup_id !== "string" || !/^[A-Za-z0-9._-]+$/.test(event.backup_id)) {
    throw new Error("数据库升级前备份结果无效");
  }
  await recoveryMarkers.writePendingUpgrade({
    kind: "schema_migration",
    fromVersion: app.getVersion(),
    targetVersion: app.getVersion(),
    fromSchemaVersion: sourceSchema,
    createdAt: new Date().toISOString(),
    backupId: event.backup_id,
  });
}

async function offlineIntegrity(): Promise<{ integrity: string; schema_version: string } | undefined> {
  const event = await runOfflineRecovery("integrity");
  if (event?.event !== "FULUA_OFFLINE_INTEGRITY" || typeof event.integrity !== "string" || typeof event.schema_version !== "string") return undefined;
  return { integrity: event.integrity, schema_version: event.schema_version };
}

async function restoreOffline(backupId: string): Promise<boolean> {
  const event = await runOfflineRecovery("restore", backupId);
  return event?.event === "FULUA_OFFLINE_RESTORE" && event.restored === true;
}

function recoveryCoordinator(loadBusinessPage: () => Promise<void> = loadBackendPage): RecoveryCoordinator {
  return new RecoveryCoordinator(recoveryMarkers, {
    checkIntegrity: async () => await runtimeApi().integrity(),
    chooseCrashAction: async (canContinue) => {
      const pending = await recoveryMarkers.readPendingUpgrade();
      const buttons = canContinue
        ? ["继续打开", "查看日志", pending ? "恢复升级前备份" : "恢复最近备份"]
        : ["查看日志", pending ? "恢复升级前备份" : "恢复最近备份"];
      const result = await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: canContinue ? "warning" : "error",
        title: "检测到上次异常关闭",
        message: canContinue ? "本地数据完整，可以继续打开。" : "本地数据库完整性检查未通过，已阻止继续写入。",
        buttons,
        defaultId: 0,
        cancelId: canContinue ? 1 : 0,
      });
      if (canContinue && result.response === 0) return "continue";
      if (buttons[result.response] === "查看日志") return "logs";
      return "restore";
    },
    loadBusinessPage,
    restoreOffline,
    listOfflineBackups: async () => {
      const event = await runOfflineRecovery("list");
      if (event?.event !== "FULUA_OFFLINE_BACKUPS" || !Array.isArray(event.backups)) return [];
      return event.backups.filter((item): item is { id: string; type: string; created_at: string } => {
        if (!item || typeof item !== "object") return false;
        const value = item as Record<string, unknown>;
        return typeof value.id === "string" && /^[A-Za-z0-9._-]+$/.test(value.id)
          && typeof value.type === "string" && typeof value.created_at === "string";
      });
    },
    chooseOfflineBackup: async (backups) => {
      if (!backups.length) return undefined;
      const result = await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: "warning",
        title: "本地服务无法启动",
        message: "可以从本机备份恢复",
        buttons: [...backups.map(backupLabel), "取消"],
        defaultId: backups.length,
        cancelId: backups.length,
      });
      return result.response < backups.length ? backups[result.response]?.id : undefined;
    },
    restartSidecar: async () => {
      const controller = backend;
      if (controller) await controller.stop();
      if (backend === controller) backend = undefined;
      backendOrigin = undefined;
      await startBackend();
    },
    showLogs: async () => {
      await mkdir(app.getPath("logs"), { recursive: true });
      await shell.openPath(app.getPath("logs"));
    },
    showRecoveryFailure: async () => {
      await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: "error",
        title: "恢复未完成",
        message: "备份未能恢复，现场和待恢复标记已保留。",
        detail: "请查看日志，不要手工覆盖本地数据目录。",
      });
    },
  });
}

function guardedStartupCoordinator(isFirstRun = false): GuardedStartupCoordinator {
  return new GuardedStartupCoordinator(recoverySessionGate, {
    prepareSchemaUpgrade,
    hasRecoveryMarker: async () => {
      const runMarker = await recoveryMarkers.hasRunMarker();
      const pending = await recoveryMarkers.readPendingUpgrade();
      return runMarker || pending !== undefined;
    },
    offlineIntegrity,
    startBackend: async () => { await startBackend(); },
    recoverWithSidecar: async () => {
      let loaded = false;
      await recoveryCoordinator(async () => {
        if (isFirstRun) await offerFirstRunChoice();
        await loadBackendPage();
        loaded = true;
      }).openAfterStartup({ currentVersion: app.getVersion(), schemaVersion: CURRENT_SCHEMA_VERSION });
      return loaded;
    },
    recoverWithoutSidecar: async () => {
      let loaded = false;
      const recovered = await recoveryCoordinator(async () => {
        await loadBackendPage();
        loaded = true;
      }).recoverWhenSidecarUnavailable({ currentVersion: app.getVersion(), schemaVersion: CURRENT_SCHEMA_VERSION });
      return recovered && loaded;
    },
    startUpdater: async () => {
      if (updateCoordinator) return;
      updateCoordinator = configureUpdater();
      updateCoordinator.start();
    },
    diagnose: async (error) => {
      await showDiagnostics(error instanceof Error ? error : new Error("恢复闸门未通过"));
    },
  });
}

const guardedStartupFlight = new GuardedStartupSingleFlight(
  async (isFirstRun) => await guardedStartupCoordinator(isFirstRun).enter(),
);
const unexpectedExitRecovery = new UnexpectedExitRecovery(recoverySessionGate, {
  enterGuarded: () => guardedStartupFlight.enter(),
});

async function readLastUpdateCheck(): Promise<number> {
  try {
    const value: unknown = JSON.parse(await readFile(path.join(dataRoot, "recovery", "update-check.json"), "utf8"));
    return typeof value === "object" && value !== null && "checkedAt" in value && typeof value.checkedAt === "number" ? value.checkedAt : 0;
  } catch { return 0; }
}

async function writeLastUpdateCheck(checkedAt: number): Promise<void> {
  const directory = path.join(dataRoot, "recovery");
  const destination = path.join(directory, "update-check.json");
  await mkdir(directory, { recursive: true });
  await writeFile(destination, JSON.stringify({ checkedAt }), { encoding: "utf8" });
}

function configureUpdater(): UpdateCoordinator {
  return new UpdateCoordinator(autoUpdater as unknown as AutoUpdaterPort, {
    isPackaged: app.isPackaged,
    now: Date.now,
    readLastCheck: readLastUpdateCheck,
    writeLastCheck: writeLastUpdateCheck,
    schedule: (delay, callback) => setTimeout(callback, delay),
    runtimeStatus: async () => await runtimeApi().status(),
    createUpgradeLeaseId: () => randomBytes(16).toString("hex"),
    prepareUpgrade: async (leaseId) => await runtimeApi().prepareUpgrade(leaseId),
    cancelUpgrade: async (leaseId) => { await runtimeApi().cancelUpgrade(leaseId); },
    writePendingUpgrade: async (marker) => await recoveryMarkers.writePendingUpgrade(marker),
    clearPendingUpgrade: async (backupId) => await recoveryMarkers.clearPendingUpgrade(backupId),
    stopSidecar: async () => {
      if (backend) await backend.stop();
      backend = undefined;
      backendOrigin = undefined;
    },
    restartSidecar: async () => {
      backend = undefined;
      backendOrigin = undefined;
      await startBackend();
    },
    reloadBusinessPage: loadBackendPage,
    clearRunMarker: async () => await recoveryMarkers.clearRunMarker(),
    writeRunMarker: async (version) => await recoveryMarkers.writeRunMarker(version),
    approveControlledQuit: async () => { quitApproved = true; },
    revokeControlledQuit: async () => { quitApproved = false; },
    confirmInstall: async () => {
      const result = await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: "question",
        title: "更新已下载",
        message: "现在退出并安装更新吗？",
        detail: "安装前会创建升级备份。选择稍后安装不会停止当前工作。",
        buttons: ["稍后", "退出并安装"],
        defaultId: 0,
        cancelId: 0,
      });
      return result.response === 1;
    },
    notifyError: async (message) => { await dialog.showMessageBox({ type: "warning", title: "无法更新", message }); },
    notifyFatal: async (message) => { await dialog.showMessageBox({ type: "error", title: "更新恢复失败", message }); },
    version: app.getVersion(),
  });
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

function backupLabel(backup: { type: string; created_at: string }): string {
  return `${backup.type} · ${backup.created_at}`;
}

async function openRestoreEntry(): Promise<void> {
  const coordinator = new RestoreWindowCoordinator({
    listBackups: async () => await runtimeApi().listBackups(),
    chooseBackup: async (backups) => {
      const result = await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: "question",
        title: "从备份恢复",
        message: "选择要恢复的备份",
        detail: "恢复会替换当前本地数据。请选择一个备份后继续确认。",
        buttons: [...backups.map(backupLabel), "取消"],
        cancelId: backups.length,
        defaultId: backups.length,
      });
      return result.response === backups.length ? undefined : backups[result.response]?.id;
    },
    confirmRestore: async (backup, detail) => {
      const result = await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: "warning",
        title: "确认恢复备份",
        message: `恢复“${backupLabel(backup)}”吗？`,
        detail,
        buttons: ["取消", "确认恢复"],
        defaultId: 0,
        cancelId: 0,
      });
      return result.response === 1;
    },
    restore: restoreBackup,
    notify: async (outcome) => {
      await dialog.showMessageBox(mainWindow ?? createWindow(), {
        type: outcome.status === "restored" ? "info" : "error",
        title: outcome.status === "restored" ? "恢复完成" : "无法恢复备份",
        message: outcome.message,
      });
    },
  });
  await coordinator.open();
}

function installApplicationMenu(): void {
  const menu = Menu.buildFromTemplate([
    {
      label: "数据",
      submenu: [{ label: "从备份恢复…", click: () => void openRestoreEntry() }],
    },
    {
      label: "帮助",
      submenu: [{ label: "检查更新", enabled: app.isPackaged, click: () => void updateCoordinator?.checkNow() }],
    },
  ]);
  Menu.setApplicationMenu(menu);
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
  await guardedStartupFlight.enter();
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
    installApplicationMenu();
    const isFirstRun = !existsSync(path.join(dataRoot, "data", "app.db"));
    createWindow();
    await guardedStartupFlight.enter(isFirstRun);
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
    const finishQuit = () => {
      if (backend === controller) backend = undefined;
      quitApproved = true;
      app.quit();
    };
    if (!recoverySessionGate.canClearRunMarker(true)) {
      finishQuit();
      return;
    }
    void recoveryMarkers.clearRunMarker().then(finishQuit,
      (error: unknown) => void showDiagnostics(error instanceof Error ? error : new Error("无法清理运行标记"), controller));
  });
});
