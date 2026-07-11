const DAY_MS = 24 * 60 * 60 * 1000;
const BUSY_RETRY_MS = 5 * 60 * 1000;

interface UpdateInfo { version?: unknown }

export interface AutoUpdaterPort {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  on(event: "update-available" | "update-downloaded" | "error", listener: (value?: unknown) => void): unknown;
  checkForUpdates(): Promise<unknown>;
  downloadUpdate(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

export interface UpgradePreparation {
  ready: boolean;
  backup_id: string;
  schema_version: string;
  lease_id: string;
}

export interface UpdateDependencies {
  isPackaged: boolean;
  now(): number;
  readLastCheck(): Promise<number>;
  writeLastCheck(value: number): Promise<void>;
  schedule(delay: number, callback: () => void): unknown;
  runtimeStatus(): Promise<{ maintenance_active: boolean; business_writes_active: number }>;
  createUpgradeLeaseId(): string;
  prepareUpgrade(leaseId: string): Promise<UpgradePreparation>;
  cancelUpgrade(leaseId: string): Promise<void>;
  writePendingUpgrade(marker: { fromVersion: string; targetVersion: string; fromSchemaVersion: string; createdAt: string; backupId: string }): Promise<void>;
  clearPendingUpgrade(backupId: string): Promise<void>;
  stopSidecar(): Promise<void>;
  restartSidecar(): Promise<void>;
  reloadBusinessPage(): Promise<void>;
  clearRunMarker(): Promise<void>;
  writeRunMarker(version: string): Promise<void>;
  approveControlledQuit(): Promise<void>;
  revokeControlledQuit(): Promise<void>;
  confirmInstall(): Promise<boolean>;
  notifyError(message: string): Promise<void>;
  notifyFatal?(message: string): Promise<void>;
  version: string;
  startupDelayMs?: number;
}

function updateVersion(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const version = (value as UpdateInfo).version;
  return typeof version === "string" && /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version) ? version : undefined;
}

export class UpdateCoordinator {
  private checkFlight: Promise<void> | undefined;
  private downloadFlight: Promise<void> | undefined;
  private installFlight: Promise<void> | undefined;
  private availableRetryScheduled = false;
  private downloadedRetryScheduled = false;
  private downloadRequested = false;
  private downloaded = false;
  private userDeclined = false;
  private installStarted = false;
  private targetVersion: string | undefined;

  constructor(private readonly updater: AutoUpdaterPort, private readonly dependencies: UpdateDependencies) {
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.on("update-available", (info) => void this.handleUpdateAvailable(info));
    updater.on("update-downloaded", (info) => void this.handleUpdateDownloaded(info));
    updater.on("error", (error) => void this.handleError(error));
  }

  start(): void {
    if (!this.dependencies.isPackaged) return;
    this.dependencies.schedule(this.dependencies.startupDelayMs ?? 30_000, () => void this.checkWhenDue());
  }

  async checkNow(): Promise<void> {
    if (!this.dependencies.isPackaged) return;
    await this.performCheck();
  }

  private async checkWhenDue(): Promise<void> {
    try {
      const now = this.dependencies.now();
      if (now - await this.dependencies.readLastCheck() < DAY_MS) return;
      await this.performCheck(now);
    } catch (error) {
      await this.handleError(error);
    }
  }

  private async performCheck(now = this.dependencies.now()): Promise<void> {
    if (this.checkFlight) return await this.checkFlight;
    const operation = (async () => {
      try {
        await this.updater.checkForUpdates();
        await this.dependencies.writeLastCheck(now);
      } catch (error) {
        await this.handleError(error);
      }
    })();
    this.checkFlight = operation;
    try { await operation; } finally { if (this.checkFlight === operation) this.checkFlight = undefined; }
  }

  private async idle(): Promise<boolean> {
    const status = await this.dependencies.runtimeStatus();
    return !status.maintenance_active && status.business_writes_active === 0;
  }

  private acceptTarget(info: unknown): boolean {
    const version = updateVersion(info);
    if (!version) return false;
    if (this.targetVersion && this.targetVersion !== version) return false;
    this.targetVersion = version;
    return true;
  }

  async handleUpdateAvailable(info: unknown): Promise<void> {
    if (!this.acceptTarget(info) || this.downloadRequested || this.downloaded) return;
    if (this.downloadFlight) return await this.downloadFlight;
    const operation = (async () => {
      try {
        if (!(await this.idle())) {
          if (!this.availableRetryScheduled) {
            this.availableRetryScheduled = true;
            this.dependencies.schedule(BUSY_RETRY_MS, () => {
              this.availableRetryScheduled = false;
              void this.handleUpdateAvailable({ version: this.targetVersion });
            });
          }
          return;
        }
        this.downloadRequested = true;
        await this.updater.downloadUpdate();
      } catch (error) {
        this.downloadRequested = false;
        await this.handleError(error);
      }
    })();
    this.downloadFlight = operation;
    try { await operation; } finally { if (this.downloadFlight === operation) this.downloadFlight = undefined; }
  }

  async handleUpdateDownloaded(info: unknown): Promise<void> {
    if (!this.acceptTarget(info) || this.installStarted || this.userDeclined) return;
    this.downloaded = true;
    if (this.installFlight) return await this.installFlight;
    const operation = this.attemptInstall();
    this.installFlight = operation;
    try { await operation; } finally { if (this.installFlight === operation) this.installFlight = undefined; }
  }

  private async attemptInstall(): Promise<void> {
    if (!(await this.idle())) {
      if (!this.downloadedRetryScheduled) {
        this.downloadedRetryScheduled = true;
        this.dependencies.schedule(BUSY_RETRY_MS, () => {
          this.downloadedRetryScheduled = false;
          void this.handleUpdateDownloaded({ version: this.targetVersion });
        });
      }
      return;
    }
    if (!(await this.dependencies.confirmInstall())) {
      this.userDeclined = true;
      return;
    }
    if (!(await this.idle())) {
      if (!this.downloadedRetryScheduled) {
        this.downloadedRetryScheduled = true;
        this.dependencies.schedule(BUSY_RETRY_MS, () => {
          this.downloadedRetryScheduled = false;
          void this.handleUpdateDownloaded({ version: this.targetVersion });
        });
      }
      return;
    }

    let prepared: UpgradePreparation | undefined;
    let requestedLeaseId: string | undefined;
    let stopAttempted = false;
    let stopped = false;
    let pendingWritten = false;
    let runCleared = false;
    let approved = false;
    try {
      requestedLeaseId = this.dependencies.createUpgradeLeaseId();
      if (!/^[A-Za-z0-9._-]{1,255}$/.test(requestedLeaseId)) throw new Error("升级维护租约无效");
      prepared = await this.dependencies.prepareUpgrade(requestedLeaseId);
      if (!prepared.ready || !/^[A-Za-z0-9._-]+$/.test(prepared.backup_id)
        || prepared.lease_id !== requestedLeaseId) {
        throw new Error("升级备份或维护租约无效");
      }
      stopAttempted = true;
      await this.dependencies.stopSidecar();
      stopped = true;
      await this.dependencies.writePendingUpgrade({
        fromVersion: this.dependencies.version,
        targetVersion: this.targetVersion!,
        fromSchemaVersion: prepared.schema_version,
        createdAt: new Date(this.dependencies.now()).toISOString(),
        backupId: prepared.backup_id,
      });
      pendingWritten = true;
      await this.dependencies.clearRunMarker();
      runCleared = true;
      await this.dependencies.approveControlledQuit();
      approved = true;
      this.updater.quitAndInstall(false, true);
      this.installStarted = true;
    } catch (error) {
      await this.rollbackInstall(prepared, requestedLeaseId, { stopAttempted, stopped, pendingWritten, runCleared, approved });
      await this.handleError(error);
    }
  }

  private async rollbackInstall(
    prepared: UpgradePreparation | undefined,
    requestedLeaseId: string | undefined,
    state: { stopAttempted: boolean; stopped: boolean; pendingWritten: boolean; runCleared: boolean; approved: boolean },
  ): Promise<void> {
    let rollbackFailed = false;
    if (state.approved) {
      try { await this.dependencies.revokeControlledQuit(); } catch { rollbackFailed = true; }
    }
    if (state.pendingWritten && prepared) {
      try { await this.dependencies.clearPendingUpgrade(prepared.backup_id); } catch { rollbackFailed = true; }
    }
    if (!state.stopped && requestedLeaseId) {
      try { await this.dependencies.cancelUpgrade(requestedLeaseId); } catch { rollbackFailed = true; }
    }
    if (state.stopAttempted && !state.stopped) rollbackFailed = true;
    if (state.stopped) {
      try {
        await this.dependencies.restartSidecar();
        if (state.runCleared) await this.dependencies.writeRunMarker(this.dependencies.version);
        await this.dependencies.reloadBusinessPage();
      } catch {
        rollbackFailed = true;
      }
    }
    if (rollbackFailed) {
      await (this.dependencies.notifyFatal?.("更新安装已中止，但本地服务或恢复标记未能安全复原。请查看日志，暂勿继续编辑。")
        ?? this.dependencies.notifyError("更新安装已中止且无法确认本地服务状态。请查看日志，暂勿继续编辑。"));
    }
  }

  async handleError(_error: unknown): Promise<void> {
    await this.dependencies.notifyError("更新校验、下载或安装准备失败。未开始安装；请查看日志确认本地服务状态。");
  }
}
