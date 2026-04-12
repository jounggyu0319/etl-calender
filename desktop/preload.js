const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desk", {
  getCollectScript: () => ipcRenderer.invoke("get-collect-script"),
});
