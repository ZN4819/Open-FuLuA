export interface OfflineIntegrityResult { integrity: string; schema_version: string }

export class RecoverySessionGate {
  private allowed = false;

  get passed(): boolean { return this.allowed; }
  reset(): void { this.allowed = false; }
  markPassed(): void { this.allowed = true; }
  canClearRunMarker(sidecarStopped: boolean): boolean { return this.allowed && sidecarStopped; }
}

export interface GuardedStartupDependencies {
  hasRecoveryMarker(): Promise<boolean>;
  offlineIntegrity(): Promise<OfflineIntegrityResult | undefined>;
  startBackend(): Promise<void>;
  recoverWithSidecar(): Promise<boolean>;
  recoverWithoutSidecar(): Promise<boolean>;
  startUpdater(): Promise<void>;
  diagnose(error: unknown): Promise<void>;
}

export class GuardedStartupCoordinator {
  constructor(private readonly gate: RecoverySessionGate, private readonly dependencies: GuardedStartupDependencies) {}

  async enter(): Promise<boolean> {
    this.gate.reset();
    let hasMarker: boolean;
    try {
      hasMarker = await this.dependencies.hasRecoveryMarker();
    } catch (error) {
      await this.dependencies.diagnose(error);
      return false;
    }
    try {
      if (hasMarker) {
        const offline = await this.dependencies.offlineIntegrity();
        if (!offline || offline.integrity !== "ok") {
          const recovered = await this.dependencies.recoverWithoutSidecar();
          if (!recovered) {
            await this.dependencies.diagnose(new Error("离线完整性检查未通过且未完成恢复"));
            return false;
          }
          this.gate.markPassed();
          await this.dependencies.startUpdater();
          return true;
        }
      }
      await this.dependencies.startBackend();
      if (!(await this.dependencies.recoverWithSidecar())) return false;
      this.gate.markPassed();
      await this.dependencies.startUpdater();
      return true;
    } catch (error) {
      await this.dependencies.diagnose(error);
      return false;
    }
  }
}
