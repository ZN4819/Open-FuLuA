import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";

export interface PendingUpgradeMarker {
  version: string;
  createdAt: string;
  backupId: string;
}

export interface RecoveryMarkerStore {
  hasRunMarker(): Promise<boolean>;
  writeRunMarker(version: string): Promise<void>;
  clearRunMarker(): Promise<void>;
  readPendingUpgrade(): Promise<PendingUpgradeMarker | undefined>;
  writePendingUpgrade(marker: PendingUpgradeMarker): Promise<void>;
  clearPendingUpgrade(): Promise<void>;
}

function validBackupId(value: string): boolean {
  return /^[A-Za-z0-9._-]+$/.test(value) && value.length <= 255;
}

async function atomicJson(filePath: string, value: object): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.tmp`;
  await writeFile(temporary, JSON.stringify(value), { encoding: "utf8", flag: "wx" });
  await rename(temporary, filePath);
}

export class JsonRecoveryMarkerStore implements RecoveryMarkerStore {
  private readonly runPath: string;
  private readonly pendingPath: string;

  constructor(private readonly stateRoot: string) {
    this.runPath = path.join(stateRoot, "runtime.json");
    this.pendingPath = path.join(stateRoot, "pending-upgrade.json");
  }

  async hasRunMarker(): Promise<boolean> {
    try { await readFile(this.runPath); return true; } catch { return false; }
  }

  async writeRunMarker(version: string): Promise<void> {
    await atomicJson(this.runPath, { version, startedAt: new Date().toISOString() });
  }

  async clearRunMarker(): Promise<void> { await rm(this.runPath, { force: true }); }

  async readPendingUpgrade(): Promise<PendingUpgradeMarker | undefined> {
    try {
      const value: unknown = JSON.parse(await readFile(this.pendingPath, "utf8"));
      if (!value || typeof value !== "object") return undefined;
      const marker = value as Record<string, unknown>;
      if (typeof marker.version !== "string" || typeof marker.createdAt !== "string" || typeof marker.backupId !== "string" || !validBackupId(marker.backupId)) return undefined;
      return { version: marker.version, createdAt: marker.createdAt, backupId: marker.backupId };
    } catch { return undefined; }
  }

  async writePendingUpgrade(marker: PendingUpgradeMarker): Promise<void> {
    if (!validBackupId(marker.backupId)) throw new Error("备份标识无效");
    await atomicJson(this.pendingPath, marker);
  }

  async clearPendingUpgrade(): Promise<void> { await rm(this.pendingPath, { force: true }); }
}

export type CrashAction = "continue" | "logs" | "restore";

export interface RecoveryDependencies {
  checkIntegrity(): Promise<{ integrity: string; schema_version: string }>;
  chooseCrashAction(canContinue: boolean): Promise<CrashAction>;
  loadBusinessPage(): Promise<void>;
  restoreOffline(backupId: string): Promise<boolean>;
  listOfflineBackups?(): Promise<Array<{ id: string; type: string; created_at: string }>>;
  chooseOfflineBackup?(backups: Array<{ id: string; type: string; created_at: string }>): Promise<string | undefined>;
  restartSidecar(): Promise<void>;
  showLogs(): Promise<void>;
  showRecoveryFailure?(): Promise<void>;
}

export class RecoveryCoordinator {
  constructor(private readonly markers: RecoveryMarkerStore, private readonly dependencies: RecoveryDependencies) {}

  async openAfterStartup(): Promise<void> {
    if (!(await this.markers.hasRunMarker())) {
      await this.dependencies.loadBusinessPage();
      return;
    }
    const integrity = await this.dependencies.checkIntegrity();
    const canContinue = integrity.integrity === "ok";
    const action = await this.dependencies.chooseCrashAction(canContinue);
    if (action === "logs") return await this.dependencies.showLogs();
    if (action === "restore") {
      const restored = await this.recoverWhenSidecarUnavailable();
      if (!restored) await this.dependencies.showRecoveryFailure?.();
      return;
    }
    if (action === "continue" && canContinue) await this.dependencies.loadBusinessPage();
  }

  async restorePendingUpgrade(): Promise<boolean> {
    const pending = await this.markers.readPendingUpgrade();
    if (!pending || !validBackupId(pending.backupId)) return false;
    if (!(await this.dependencies.restoreOffline(pending.backupId))) return false;
    await this.dependencies.restartSidecar();
    await this.markers.clearPendingUpgrade();
    return true;
  }

  async recoverWhenSidecarUnavailable(): Promise<boolean> {
    if (await this.markers.readPendingUpgrade()) return await this.restorePendingUpgrade();
    if (!this.dependencies.listOfflineBackups || !this.dependencies.chooseOfflineBackup) return false;
    const backups = await this.dependencies.listOfflineBackups();
    const selected = await this.dependencies.chooseOfflineBackup(backups);
    if (!selected || !validBackupId(selected) || !backups.some((backup) => backup.id === selected)) return false;
    if (!(await this.dependencies.restoreOffline(selected))) return false;
    await this.dependencies.restartSidecar();
    return true;
  }
}
