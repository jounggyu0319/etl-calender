// extension/background.js
// content script에서 보내는 MYETL_PROGRESS 메시지를 popup으로 릴레이하는 service worker.

chrome.runtime.onMessage.addListener((msg, _sender, _sendResponse) => {
  if (msg && msg.type === "MYETL_PROGRESS") {
    // popup으로 브로드캐스트 (popup이 열려 있을 때만 도달)
    chrome.runtime.sendMessage(msg).catch(() => {});
  }
  return false;
});

