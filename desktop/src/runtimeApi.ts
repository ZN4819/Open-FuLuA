import type { BackupSummary, RestoreExecution } from "./backupActions.js";
import type { MigrationExecution, MigrationPreflight } from "./migrationWindow.js";

type Fetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface RuntimeStatus {
  maintenance_active: boolean;
  business_writes_active: number;
}

export interface IntegrityStatus { integrity: string; schema_version: string }
export interface UpgradePreparation { ready: boolean; backup_id: string; schema_version: string; lease_id: string }

/** 与同一台本机侧车通信的最小客户端，不接受远程地址。 */
export class RuntimeApiClient {
  private readonly origin: string;

  constructor(origin: string, private readonly sessionToken: string, private readonly fetchImpl: Fetch = globalThis.fetch) {
    const url = new URL(origin);
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.pathname !== "/" || url.search || url.hash) {
      throw new Error("仅允许连接本机侧车");
    }
    this.origin = url.origin;
  }

  async preflight(sourceRoot: string): Promise<MigrationPreflight> {
    return await this.request("/api/runtime/migration/preflight", "POST", { source_root: sourceRoot });
  }

  async migrate(sourceRoot: string): Promise<MigrationExecution> {
    return await this.request("/api/runtime/migration", "POST", { source_root: sourceRoot });
  }

  async listBackups(): Promise<BackupSummary[]> {
    return await this.request("/api/runtime/backups", "GET");
  }

  async restore(backupId: string): Promise<RestoreExecution> {
    return await this.request(`/api/runtime/backups/${encodeURIComponent(backupId)}/restore`, "POST");
  }

  async status(): Promise<RuntimeStatus> { return await this.request("/api/runtime/status", "GET"); }

  async integrity(): Promise<IntegrityStatus> { return await this.request("/api/runtime/integrity", "GET"); }

  async prepareUpgrade(): Promise<UpgradePreparation> { return await this.request("/api/runtime/upgrade/prepare", "POST"); }

  async cancelUpgrade(leaseId: string): Promise<{ cancelled: boolean }> {
    return await this.request("/api/runtime/upgrade/cancel", "POST", { lease_id: leaseId });
  }

  private async request<T>(path: string, method: "GET" | "POST", body?: object): Promise<T> {
    const response = await this.fetchImpl(`${this.origin}${path}`, {
      method,
      headers: { ...(body ? { "content-type": "application/json" } : {}), "x-fulua-session-token": this.sessionToken },
      body: body ? JSON.stringify(body) : undefined,
    });
    const payload: unknown = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = typeof payload === "object" && payload !== null && "detail" in payload && typeof payload.detail === "string"
        ? payload.detail
        : "本地数据操作失败";
      throw new Error(message);
    }
    return payload as T;
  }
}
