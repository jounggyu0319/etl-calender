chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "MYETL_COLLECT") {
    const delayMs =
      msg.delayMs != null && typeof msg.delayMs === "number" ? msg.delayMs : 350;
    const run = typeof collectMyetlAssignments === "function" ? collectMyetlAssignments : null;
    if (!run) {
      sendResponse({ error: "collectMyetlAssignments not loaded", items: [] });
      return false;
    }
    run({ delayMs })
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ error: String(e), items: [] }));
    return true;
  }
  return false;
});
