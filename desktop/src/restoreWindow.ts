import type { BackupSummary, RestoreOutcome } from "./backupActions.js";

export interface RestoreWindowBridge {
  listBackups(): Promise<BackupSummary[]>;
  chooseBackup(backups: readonly BackupSummary[]): Promise<string | undefined>;
  confirmRestore(backup: BackupSummary, detail: string): Promise<boolean>;
  restore(backupId: string): Promise<RestoreOutcome>;
  notify(outcome: RestoreOutcome): Promise<void>;
}

export type RestoreWindowOutcome = RestoreOutcome | { status: "cancelled" };

/**
 * 仅在主进程编排恢复交互。渲染进程不获取文件路径或 Node 能力。
 */
export class RestoreWindowCoordinator {
  constructor(private readonly bridge: RestoreWindowBridge) {}

  async open(): Promise<RestoreWindowOutcome> {
    const backups = await this.bridge.listBackups();
    if (backups.length === 0) {
      const outcome: RestoreOutcome = { status: "failed", message: "当前没有可用于恢复的备份。" };
      await this.bridge.notify(outcome);
      return outcome;
    }

    const backupId = await this.bridge.chooseBackup(backups);
    if (!backupId) return { status: "cancelled" };

    const backup = backups.find((candidate) => candidate.id === backupId);
    if (!backup) {
      const outcome: RestoreOutcome = { status: "failed", message: "所选备份已不可用，请重新打开恢复入口。" };
      await this.bridge.notify(outcome);
      return outcome;
    }

    const confirmed = await this.bridge.confirmRestore(
      backup,
      "恢复将替换当前本地数据；继续后本地服务将重启。",
    );
    if (!confirmed) return { status: "cancelled" };

    const outcome = await this.bridge.restore(backup.id);
    await this.bridge.notify(outcome);
    return outcome;
  }
}
