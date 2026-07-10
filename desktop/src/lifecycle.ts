export interface ExistingWindow {
  isMinimized(): boolean;
  restore(): void;
  focus(): void;
}

export interface StoppableBackend {
  stop(): Promise<void>;
}

export function runSingleInstance(
  hasLock: boolean,
  quit: () => void,
  startPrimary: () => void,
): boolean {
  if (!hasLock) {
    quit();
    return false;
  }
  startPrimary();
  return true;
}

export function focusExistingWindow(window: ExistingWindow | undefined): void {
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.focus();
}

export class QuitGuard<TBackend extends StoppableBackend> {
  constructor(
    private backend: TBackend | undefined,
    private readonly showDiagnostics: (error: Error, backend: TBackend) => Promise<void>,
  ) {}

  currentBackend(): TBackend | undefined {
    return this.backend;
  }

  async stopForQuit(): Promise<boolean> {
    const backend = this.backend;
    if (!backend) return true;
    try {
      await backend.stop();
      this.backend = undefined;
      return true;
    } catch (error) {
      await this.showDiagnostics(error instanceof Error ? error : new Error("本地服务未能停止"), backend);
      return false;
    }
  }
}
