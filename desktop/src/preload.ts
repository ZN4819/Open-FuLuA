import { contextBridge, ipcRenderer } from "electron";

const desktopApi = Object.freeze({
  getVersion: (): Promise<string> => ipcRenderer.invoke("app:get-version"),
  openLogsDirectory: (): Promise<void> => ipcRenderer.invoke("app:open-logs-directory"),
});

contextBridge.exposeInMainWorld("fuluaDesktop", desktopApi);
