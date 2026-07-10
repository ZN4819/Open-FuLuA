export type FirstRunChoice = "new" | "migrate";

export interface MigrationPreflight {
  can_migrate: boolean;
  blocking_reasons: string[];
}

export interface MigrationExecution {
  migrated: boolean;
  restart_required: boolean;
  message: string;
}

export interface MigrationBridge {
  chooseSourceDirectory(): Promise<string | undefined>;
  preflight(sourceRoot: string): Promise<MigrationPreflight>;
  migrate(sourceRoot: string): Promise<MigrationExecution>;
  restartSidecar(): Promise<void>;
}

export type MigrationOutcome =
  | { status: "new-data" }
  | { status: "cancelled" }
  | { status: "blocked"; message: string }
  | { status: "failed"; message: string }
  | { status: "migrated"; message: string };

/** 主进程迁移协调器；渲染进程只接收已脱敏的结果。 */
export class MigrationCoordinator {
  constructor(private readonly bridge: MigrationBridge) {}

  async begin(choice: FirstRunChoice): Promise<MigrationOutcome> {
    if (choice === "new") return { status: "new-data" };

    const sourceRoot = await this.bridge.chooseSourceDirectory();
    if (!sourceRoot) return { status: "cancelled" };

    const preflight = await this.bridge.preflight(sourceRoot);
    if (!preflight.can_migrate) {
      return { status: "blocked", message: preflight.blocking_reasons.join("；") || "无法确认旧数据完整性" };
    }

    const result = await this.bridge.migrate(sourceRoot);
    if (!result.migrated) return { status: "failed", message: result.message };
    if (result.restart_required) await this.bridge.restartSidecar();
    return { status: "migrated", message: result.message };
  }
}
