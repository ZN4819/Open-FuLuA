export interface BackupSummary {
  id: string;
  type: string;
  created_at: string;
}

export interface RestoreExecution {
  restored: boolean;
  restart_required: boolean;
  message: string;
}

export interface BackupBridge {
  listBackups(): Promise<BackupSummary[]>;
  restore(backupId: string): Promise<RestoreExecution>;
  restartSidecar(): Promise<void>;
}

export type RestoreOutcome = { status: "restored"; message: string } | { status: "failed"; message: string };

/** 主进程恢复协调器；不向渲染器暴露 Node 文件系统能力。 */
export class BackupCoordinator {
  constructor(private readonly bridge: BackupBridge) {}

  static canStartMaintenance(isPrimaryInstance: boolean): boolean {
    return isPrimaryInstance;
  }

  async list(): Promise<BackupSummary[]> {
    return await this.bridge.listBackups();
  }

  async restore(backupId: string): Promise<RestoreOutcome> {
    const result = await this.bridge.restore(backupId);
    if (!result.restored) return { status: "failed", message: result.message };
    if (result.restart_required) await this.bridge.restartSidecar();
    return { status: "restored", message: result.message };
  }
}
