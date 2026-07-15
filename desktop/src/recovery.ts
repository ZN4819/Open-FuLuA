import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";

export interface PendingUpgradeMarker {
  kind?: "app_upgrade" | "schema_migration";
  fromVersion: string;
  targetVersion: string;
  fromSchemaVersion: string;
  createdAt: string;
  backupId: string;
}

export interface StartupContext { currentVersion: string; schemaVersion: string }

export interface RecoveryMarkerStore {
  hasRunMarker(): Promise<boolean>;
  writeRunMarker(version: string): Promise<void>;
  clearRunMarker(): Promise<void>;
  readPendingUpgrade(): Promise<PendingUpgradeMarker | undefined>;
  writePendingUpgrade(marker: PendingUpgradeMarker): Promise<void>;
  clearPendingUpgrade(expectedBackupId?: string): Promise<void>;
}

function validBackupId(value: string): boolean {
  return /^[A-Za-z0-9._-]+$/.test(value) && value.length <= 255;
}

function validVersion(value: unknown): value is string {
  return typeof value === "string" && /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(value);
}

function validSchemaVersion(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9._-]{1,64}$/.test(value);
}

function isMissing(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code?: unknown }).code === "ENOENT";
}

async function atomicJson(filePath: string, value: object): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.tmp-${process.pid}-${randomUUID()}`;
  try {
    await writeFile(temporary, JSON.stringify(value), { encoding: "utf8", flag: "wx" });
    await rename(temporary, filePath);
  } finally {
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

export class JsonRecoveryMarkerStore implements RecoveryMarkerStore {
  private readonly runPath: string;
  private readonly pendingPath: string;

  constructor(private readonly stateRoot: string) {
    this.runPath = path.join(stateRoot, "runtime.json");
    this.pendingPath = path.join(stateRoot, "pending-upgrade.json");
  }

  async hasRunMarker(): Promise<boolean> {
    let text: string;
    try { text = await readFile(this.runPath, "utf8"); } catch (error) {
      if (isMissing(error)) return false;
      throw error;
    }
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object") throw new Error("运行标记损坏");
    const marker = value as Record<string, unknown>;
    if (!validVersion(marker.version) || typeof marker.startedAt !== "string" || !Number.isFinite(Date.parse(marker.startedAt))) {
      throw new Error("运行标记字段无效");
    }
    return true;
  }

  async writeRunMarker(version: string): Promise<void> {
    await atomicJson(this.runPath, { version, startedAt: new Date().toISOString() });
  }

  async clearRunMarker(): Promise<void> { await rm(this.runPath, { force: true }); }

  async readPendingUpgrade(): Promise<PendingUpgradeMarker | undefined> {
    let text: string;
    try {
      text = await readFile(this.pendingPath, "utf8");
    } catch (error) {
      if (isMissing(error)) return undefined;
      throw error;
    }
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object") throw new Error("待升级标记损坏");
    const marker = value as Record<string, unknown>;
    const kind = marker.kind === undefined ? "app_upgrade" : marker.kind;
    const sameVersionAllowed = kind === "schema_migration";
    if ((kind !== "app_upgrade" && kind !== "schema_migration")
      || !validVersion(marker.fromVersion) || !validVersion(marker.targetVersion)
      || (!sameVersionAllowed && marker.fromVersion === marker.targetVersion)
      || !validSchemaVersion(marker.fromSchemaVersion)
      || typeof marker.createdAt !== "string" || !Number.isFinite(Date.parse(marker.createdAt))
      || typeof marker.backupId !== "string" || !validBackupId(marker.backupId)) {
      throw new Error("待升级标记字段无效");
    }
    return {
      kind,
      fromVersion: marker.fromVersion,
      targetVersion: marker.targetVersion,
      fromSchemaVersion: marker.fromSchemaVersion,
      createdAt: marker.createdAt,
      backupId: marker.backupId,
    };
  }

  async writePendingUpgrade(marker: PendingUpgradeMarker): Promise<void> {
    const kind = marker.kind ?? "app_upgrade";
    if ((kind !== "app_upgrade" && kind !== "schema_migration")
      || !validBackupId(marker.backupId) || !validVersion(marker.fromVersion) || !validVersion(marker.targetVersion)
      || (kind !== "schema_migration" && marker.fromVersion === marker.targetVersion)
      || !validSchemaVersion(marker.fromSchemaVersion)
      || !Number.isFinite(Date.parse(marker.createdAt))) {
      throw new Error("待升级标记无效");
    }
    await atomicJson(this.pendingPath, marker);
  }

  async clearPendingUpgrade(expectedBackupId?: string): Promise<void> {
    if (expectedBackupId !== undefined) {
      const marker = await this.readPendingUpgrade();
      if (!marker || marker.backupId !== expectedBackupId) throw new Error("待升级标记已变化");
    }
    await rm(this.pendingPath, { force: true });
  }
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
  now?(): number;
}

export class RecoveryCoordinator {
  constructor(private readonly markers: RecoveryMarkerStore, private readonly dependencies: RecoveryDependencies) {}

  private pendingMode(marker: PendingUpgradeMarker, context: StartupContext): "target" | "rollback" | undefined {
    const now = this.dependencies.now?.() ?? Date.now();
    const createdAt = Date.parse(marker.createdAt);
    const kind = marker.kind ?? "app_upgrade";
    const valid = (kind === "app_upgrade" || kind === "schema_migration")
      && validVersion(marker.fromVersion)
      && validVersion(marker.targetVersion)
      && (kind === "schema_migration" || marker.fromVersion !== marker.targetVersion)
      && validSchemaVersion(marker.fromSchemaVersion)
      && validBackupId(marker.backupId)
      && createdAt <= now + 5 * 60_000
      && createdAt >= now - 7 * 24 * 60 * 60_000;
    if (!valid) return undefined;
    if (kind === "schema_migration") {
      return marker.targetVersion === context.currentVersion ? "target" : undefined;
    }
    if (marker.targetVersion === context.currentVersion) return "target";
    if (marker.fromVersion === context.currentVersion) return "rollback";
    return undefined;
  }

  async openAfterStartup(context: StartupContext): Promise<void> {
    const pending = await this.markers.readPendingUpgrade();
    if (pending) {
      const mode = this.pendingMode(pending, context);
      if (!mode) {
        await this.dependencies.showRecoveryFailure?.();
        return;
      }
      const integrity = await this.dependencies.checkIntegrity();
      const expectedSchema = mode === "target" ? context.schemaVersion : pending.fromSchemaVersion;
      if (integrity.integrity === "ok" && integrity.schema_version === expectedSchema) {
        await this.markers.clearPendingUpgrade();
        if (mode === "rollback" || !(await this.markers.hasRunMarker())) {
          await this.markers.writeRunMarker(context.currentVersion);
        }
        await this.dependencies.loadBusinessPage();
        return;
      }
      if (mode === "rollback") {
        await this.dependencies.showRecoveryFailure?.();
        return;
      }
      const action = await this.dependencies.chooseCrashAction(false);
      if (action === "logs") return await this.dependencies.showLogs();
      if (action === "restore" && !(await this.restorePendingUpgrade(context))) await this.dependencies.showRecoveryFailure?.();
      return;
    }

    if (!(await this.markers.hasRunMarker())) {
      await this.markers.writeRunMarker(context.currentVersion);
      await this.dependencies.loadBusinessPage();
      return;
    }
    const integrity = await this.dependencies.checkIntegrity();
    const canContinue = integrity.integrity === "ok" && integrity.schema_version === context.schemaVersion;
    const action = await this.dependencies.chooseCrashAction(canContinue);
    if (action === "logs") return await this.dependencies.showLogs();
    if (action === "restore") {
      const restored = await this.recoverWhenSidecarUnavailable(context);
      if (!restored) await this.dependencies.showRecoveryFailure?.();
      return;
    }
    if (action === "continue" && canContinue) await this.dependencies.loadBusinessPage();
  }

  async restorePendingUpgrade(context: StartupContext): Promise<boolean> {
    const pending = await this.markers.readPendingUpgrade();
    if (!pending || this.pendingMode(pending, context) !== "target" || !validBackupId(pending.backupId)) return false;
    if (!(await this.dependencies.restoreOffline(pending.backupId))) return false;
    await this.dependencies.restartSidecar();
    const integrity = await this.dependencies.checkIntegrity();
    if (integrity.integrity !== "ok" || integrity.schema_version !== context.schemaVersion) return false;
    await this.markers.clearPendingUpgrade();
    if (!(await this.markers.hasRunMarker())) await this.markers.writeRunMarker(context.currentVersion);
    await this.dependencies.loadBusinessPage();
    return true;
  }

  async recoverWhenSidecarUnavailable(context: StartupContext): Promise<boolean> {
    const pending = await this.markers.readPendingUpgrade();
    if (pending) return this.pendingMode(pending, context) === "target" ? await this.restorePendingUpgrade(context) : false;
    if (!this.dependencies.listOfflineBackups || !this.dependencies.chooseOfflineBackup) return false;
    const backups = await this.dependencies.listOfflineBackups();
    const selected = await this.dependencies.chooseOfflineBackup(backups);
    if (!selected || !validBackupId(selected) || !backups.some((backup) => backup.id === selected)) return false;
    if (!(await this.dependencies.restoreOffline(selected))) return false;
    await this.dependencies.restartSidecar();
    const integrity = await this.dependencies.checkIntegrity();
    if (integrity.integrity !== "ok" || integrity.schema_version !== context.schemaVersion) return false;
    if (!(await this.markers.hasRunMarker())) await this.markers.writeRunMarker(context.currentVersion);
    await this.dependencies.loadBusinessPage();
    return true;
  }
}
