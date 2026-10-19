/* Service worker — dashboards shell is precache-first; API reads are
 * never cached here (IndexedDB last-known cache in data.js owns that). */
const CACHE = "openhealth-dash-v3"; // v3: economics + ML-health boards (ADR-0033)
const SHELL = [
  "./", "./index.html", "./css/dash.css",
  "./js/app.js", "./js/dom.js", "./js/data.js", "./js/charts.js", "./js/views.js",
  "./js/map.js", "./assets/nigeria-boundary.json",
  "./manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return; // never intercept API reads
  e.respondWith(
    caches.match(e.request, { ignoreSearch: true }).then((hit) =>
      hit || fetch(e.request).then((resp) => {
        if (resp.ok && e.request.method === "GET") {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      }).catch(() => caches.match("./index.html"))));
});
