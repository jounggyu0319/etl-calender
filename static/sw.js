/* PWA용 최소 서비스 워커 — 나중에 오프라인·캐시 정책을 붙일 수 있습니다. */
self.addEventListener("install", (event) => {
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
