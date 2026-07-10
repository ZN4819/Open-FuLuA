import { contextBridge, ipcRenderer } from "electron";

const desktopApi = Object.freeze({
  getVersion: (): Promise<string> => ipcRenderer.invoke("app:get-version"),
  openLogsDirectory: (): Promise<void> => ipcRenderer.invoke("app:open-logs-directory"),
  retryBackend: (): Promise<void> => ipcRenderer.invoke("app:retry-backend"),
  copyDiagnostics: (details: string): Promise<void> => ipcRenderer.invoke("app:copy-diagnostics", details),
});

contextBridge.exposeInMainWorld("fuluaDesktop", desktopApi);
