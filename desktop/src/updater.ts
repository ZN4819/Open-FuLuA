const DAY_MS = 24 * 60 * 60 * 1000;
const BUSY_RETRY_MS = 5 * 60 * 1000;

export interface AutoUpdaterPort {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  on(event: "update-available" | "update-downloaded" | "error", listener: (value?: unknown) => void): unknown;
  checkForUpdates(): Promise<unknown>;
  downloadUpdate(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

export interface UpdateDependencies {
  isPackaged: boolean;
  now(): number;
  readLastCheck(): Promise<number>;
  writeLastCheck(value: number): Promise<void>;
  schedule(delay: number, callback: () => void): unknown;
  runtimeStatus(): Promise<{ maintenance_active: boolean; business_writes_active: number }>;
  prepareUpgrade(): Promise<{ ready: boolean; backup_id: string; schema_version: string }>;
  writePendingUpgrade(marker: { version: string; createdAt: string; backupId: string }): Promise<void>;
  stopSidecar(): Promise<void>;
  clearRunMarker(): Promise<void>;
  approveControlledQuit(): Promise<void>;
  confirmInstall(): Promise<boolean>;
  notifyError(message: string): Promise<void>;
  version?: string;
  startupDelayMs?: number;
}

export class UpdateCoordinator {
  constructor(private readonly updater: AutoUpdaterPort, private readonly dependencies: UpdateDependencies) {
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.on("update-available", () => void this.handleUpdateAvailable());
    updater.on("update-downloaded", () => void this.handleUpdateDownloaded());
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
    try {
      await this.updater.checkForUpdates();
      await this.dependencies.writeLastCheck(now);
    } catch (error) {
      await this.handleError(error);
    }
  }

  private async idle(): Promise<boolean> {
    const status = await this.dependencies.runtimeStatus();
    return !status.maintenance_active && status.business_writes_active === 0;
  }

  async handleUpdateAvailable(): Promise<void> {
    try {
      if (!(await this.idle())) {
        this.dependencies.schedule(BUSY_RETRY_MS, () => void this.handleUpdateAvailable());
        return;
      }
      await this.updater.downloadUpdate();
    } catch (error) {
      await this.handleError(error);
    }
  }

  async handleUpdateDownloaded(): Promise<void> {
    try {
      if (!(await this.dependencies.confirmInstall())) return;
      if (!(await this.idle())) return;
      const prepared = await this.dependencies.prepareUpgrade();
      if (!prepared.ready || !/^[A-Za-z0-9._-]+$/.test(prepared.backup_id)) throw new Error("升级备份无效");
      await this.dependencies.writePendingUpgrade({
        version: this.dependencies.version ?? "unknown",
        createdAt: new Date(this.dependencies.now()).toISOString(),
        backupId: prepared.backup_id,
      });
      await this.dependencies.stopSidecar();
      await this.dependencies.clearRunMarker();
      await this.dependencies.approveControlledQuit();
      this.updater.quitAndInstall(false, true);
    } catch (error) {
      await this.handleError(error);
    }
  }

  async handleError(_error: unknown): Promise<void> {
    await this.dependencies.notifyError("更新校验或下载失败。当前版本仍可继续使用，请稍后重试或查看日志。");
  }
}
