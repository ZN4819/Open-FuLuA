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
    if (hasMarker) {
      try {
        const offline = await this.dependencies.offlineIntegrity();
        if (!offline || offline.integrity !== "ok") {
          return await this.recoverOffline(new Error("离线完整性检查未通过且未完成恢复"));
        }
      } catch (error) {
        await this.dependencies.diagnose(error);
        return false;
      }
    }
    try {
      await this.dependencies.startBackend();
    } catch (error) {
      if (hasMarker) return await this.recoverOffline(error);
      await this.dependencies.diagnose(error);
      return false;
    }
    try {
      if (!(await this.dependencies.recoverWithSidecar())) return false;
      return await this.passGateAndStartUpdater();
    } catch (error) {
      await this.dependencies.diagnose(error);
      return false;
    }
  }

  private async recoverOffline(error: unknown): Promise<boolean> {
    try {
      if (!(await this.dependencies.recoverWithoutSidecar())) {
        await this.dependencies.diagnose(error);
        return false;
      }
      return await this.passGateAndStartUpdater();
    } catch (recoveryError) {
      await this.dependencies.diagnose(recoveryError);
      return false;
    }
  }

  private async passGateAndStartUpdater(): Promise<boolean> {
    this.gate.markPassed();
    await this.dependencies.startUpdater();
    return true;
  }
}

export class GuardedStartupSingleFlight {
  private active: Promise<boolean> | undefined;

  constructor(private readonly enterGuarded: (isFirstRun: boolean) => Promise<boolean>) {}

  enter(isFirstRun = false): Promise<boolean> {
    if (this.active) return this.active;
    const flight = Promise.resolve()
      .then(async () => await this.enterGuarded(isFirstRun))
      .finally(() => {
        if (this.active === flight) this.active = undefined;
      });
    this.active = flight;
    return flight;
  }
}

export interface UnexpectedExitDependencies {
  enterGuarded(): Promise<boolean>;
}

export class UnexpectedExitRecovery {
  constructor(private readonly gate: RecoverySessionGate, private readonly dependencies: UnexpectedExitDependencies) {}

  async handle(_error: unknown): Promise<boolean> {
    this.gate.reset();
    const recovered = await this.dependencies.enterGuarded();
    if (recovered) this.gate.markPassed();
    return recovered;
  }
}
