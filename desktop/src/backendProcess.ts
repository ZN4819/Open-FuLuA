import { ChildProcess, spawn as nodeSpawn } from "node:child_process";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const STDERR_TAIL_LIMIT = 4_000;

export interface BackendProcessOptions {
  executable: string;
  dataRoot: string;
  webDist: string;
  sessionToken: string;
  commandArguments?: string[];
  cwd?: string;
  startTimeoutMs?: number;
  stopTimeoutMs?: number;
  forceKillTimeoutMs?: number;
  spawn?: (command: string, commandArgs: readonly string[], options: { cwd?: string; windowsHide: boolean; stdio: "pipe"[] }) => ChildProcess;
  fetch?: typeof globalThis.fetch;
  forceKill?: (pid: number) => Promise<void>;
}

interface ReadyEvent {
  event: "FULUA_READY";
  port: number;
  health_url: string;
}

interface FailedEvent {
  event: "FULUA_FAILED";
  message?: string;
}

type DesktopEvent = ReadyEvent | FailedEvent;

export class BackendProcessController {
  private readonly options: Required<Pick<BackendProcessOptions, "startTimeoutMs" | "stopTimeoutMs" | "forceKillTimeoutMs">> & BackendProcessOptions;
  private child: ChildProcess | undefined;
  private starting: Promise<string> | undefined;
  private stopping = false;
  private restarted = false;
  private stderrTail = "";
  private blockedReason: Error | undefined;
  private unexpectedExitHandler: ((reason: Error) => void) | undefined;

  constructor(options: BackendProcessOptions) {
    const stopTimeoutMs = options.stopTimeoutMs ?? 5_000;
    this.options = {
      ...options,
      startTimeoutMs: options.startTimeoutMs ?? 15_000,
      stopTimeoutMs,
      forceKillTimeoutMs: options.forceKillTimeoutMs ?? Math.min(1_000, Math.max(1, Math.floor(stopTimeoutMs / 2))),
    };
  }

  onUnexpectedExit(handler: (reason: Error) => void): void {
    this.unexpectedExitHandler = handler;
  }

  async start(): Promise<string> {
    if (this.blockedReason) {
      throw this.blockedReason;
    }
    if (this.child || this.starting) {
      throw new Error("侧车已在启动或运行");
    }
    this.starting = this.startChild();
    try {
      return await this.starting;
    } finally {
      this.starting = undefined;
    }
  }

  async restartOnce(): Promise<string> {
    if (this.blockedReason) {
      throw this.blockedReason;
    }
    if (this.restarted) {
      throw new Error("侧车仅允许自动恢复一次");
    }
    this.restarted = true;
    await this.stop();
    return this.start();
  }

  async stop(): Promise<void> {
    if (this.blockedReason) {
      throw this.blockedReason;
    }
    const child = this.child;
    if (!child) {
      return;
    }
    this.stopping = true;
    let stopped = false;
    try {
      await this.stopChild(child);
      stopped = true;
    } catch (error) {
      throw this.blockAfterCleanupFailure(error);
    } finally {
      if (stopped && this.child === child) this.child = undefined;
      this.stopping = false;
    }
  }

  diagnostics(): string {
    const details = this.stderrTail || "未收到侧车错误输出。";
    return this.blockedReason ? `${this.blockedReason.message}\n${details}` : details;
  }

  private startChild(): Promise<string> {
    this.stderrTail = "";
    const spawn = this.options.spawn ?? nodeSpawn;
    const commandArgs = [
      ...(this.options.commandArguments ?? []),
      "--data-root", this.options.dataRoot,
      "--web-dist", this.options.webDist,
      "--session-token", this.options.sessionToken,
    ];
    const child = spawn(this.options.executable, commandArgs, { cwd: this.options.cwd, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    this.child = child;

    return new Promise<string>((resolve, reject) => {
      let stdoutBuffer = "";
      let settled = false;
      const timeout = setTimeout(() => finish(new Error("侧车未能在 15 秒内完成健康检查")), this.options.startTimeoutMs);
      const finish = (result: string | Error, waitForChildExit = true): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (result instanceof Error) {
          if (!waitForChildExit) {
            if (this.child === child) this.child = undefined;
            reject(result);
            return;
          }
          this.stopping = true;
          void this.stopChild(child).then(
            () => {
              if (this.child === child) this.child = undefined;
            },
            (cleanupError) => {
              this.blockAfterCleanupFailure(cleanupError);
            },
          ).finally(() => {
            this.stopping = false;
            reject(result);
          });
        } else {
          resolve(result);
        }
      };

      child.stdout?.on("data", (chunk: Buffer) => {
        stdoutBuffer += chunk.toString("utf-8");
        const lines = stdoutBuffer.split(/\r?\n/);
        stdoutBuffer = lines.pop() ?? "";
        for (const line of lines) {
          const event = this.parseEvent(line);
          if (!event) continue;
          if (event.event === "FULUA_FAILED") {
            finish(new Error(event.message ?? "本地服务未能启动"));
          } else {
            void this.waitForHealth(event.health_url).then(
              () => finish(new URL(event.health_url).origin),
              (error: Error) => finish(error),
            );
          }
        }
      });
      child.stderr?.on("data", (chunk: Buffer) => this.appendStderr(chunk.toString("utf-8")));
      child.once("error", (error) => finish(error));
      child.once("exit", (code) => {
        if (!settled) {
          finish(new Error(`侧车在就绪前退出（代码 ${code ?? "未知"}）`), false);
          return;
        }
        if (!this.stopping && this.child === child) {
          this.child = undefined;
          this.unexpectedExitHandler?.(new Error(`本地服务意外退出（代码 ${code ?? "未知"}）`));
        }
      });
    });
  }

  private parseEvent(line: string): DesktopEvent | undefined {
    try {
      const event = JSON.parse(line) as DesktopEvent;
      if (event.event === "FULUA_FAILED") return event;
      if (event.event === "FULUA_READY" && this.isValidReadyEvent(event)) return event;
    } catch {
      // 只接受协议约定的单行 JSON，其他 stdout 不影响生命周期判断。
    }
    return undefined;
  }

  private async waitForHealth(healthUrl: string): Promise<void> {
    const fetch = this.options.fetch ?? globalThis.fetch;
    const deadline = Date.now() + this.options.startTimeoutMs;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(healthUrl);
        if (response.ok) return;
      } catch {
        // 服务启动窗口内允许连接尚未建立。
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("侧车未能在 15 秒内完成健康检查");
  }

  private waitForExit(child: ChildProcess, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("侧车停止超时")), timeoutMs);
      child.once("exit", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  private async stopChild(child: ChildProcess): Promise<void> {
    const forceKillTimeoutMs = Math.min(this.options.forceKillTimeoutMs, this.options.stopTimeoutMs);
    const gracefulStopTimeoutMs = Math.max(1, this.options.stopTimeoutMs - forceKillTimeoutMs);
    try {
      child.kill("SIGTERM");
      await this.waitForExit(child, gracefulStopTimeoutMs);
    } catch {
      if (process.platform === "win32" && child.pid) {
        try {
          const forceKill = this.options.forceKill ?? (async (pid: number): Promise<void> => {
            await execFileAsync("taskkill", ["/pid", String(pid), "/t", "/f"]);
          });
          await this.withTimeout(forceKill(child.pid), forceKillTimeoutMs, "强制清理侧车进程超时");
          return;
        } catch {
          throw new Error("无法确认侧车已退出，已阻止再次启动");
        }
      }
      throw new Error("无法确认侧车已退出，已阻止再次启动");
    }
  }

  private blockAfterCleanupFailure(error: unknown): Error {
    this.blockedReason = error instanceof Error ? error : new Error("无法确认侧车已退出，已阻止再次启动");
    return this.blockedReason;
  }

  private isValidReadyEvent(event: ReadyEvent): boolean {
    if (!Number.isInteger(event.port) || event.port < 1 || event.port > 65_535) return false;
    try {
      const healthUrl = new URL(event.health_url);
      return healthUrl.protocol === "http:"
        && healthUrl.hostname === "127.0.0.1"
        && healthUrl.port === String(event.port)
        && healthUrl.pathname === "/api/health"
        && !healthUrl.search
        && !healthUrl.hash;
    } catch {
      return false;
    }
  }

  private async withTimeout<T>(operation: Promise<T>, timeoutMs: number, message: string): Promise<T> {
    return await new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(message)), timeoutMs);
      void operation.then(
        (result) => {
          clearTimeout(timeout);
          resolve(result);
        },
        (error: unknown) => {
          clearTimeout(timeout);
          reject(error);
        },
      );
    });
  }

  private appendStderr(value: string): void {
    this.stderrTail = (this.stderrTail + value).slice(-STDERR_TAIL_LIMIT);
  }
}
