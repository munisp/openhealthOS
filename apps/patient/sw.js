/* Patient app service worker — same offline guarantee as the nurse PWA:
 * precached shell, API traffic never cached (the feed comes from the
 * network and falls back to the app's localStorage last-known copy). */

const CACHE = "openhealth-patient-v4"; // v4: wellness tab (ADR-0035)
const SHELL = [
  "./",
  "./index.html",
  "../design-tokens.css",
  "../ambulance-map.js",
  "./track.html",
  "./css/app.css",
  "./js/app.js",
  "./js/i18n.js",
  "./js/logic.js",
  "./js/geo.js",
  "./manifest.webmanifest",
  "./icons/icon.svg",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache the rail
  if (e.request.method !== "GET") return;
  e.respondWith(
    caches.match(e.request, { ignoreSearch: true }).then((hit) => {
      if (hit) return hit;
      return fetch(e.request).then((resp) => {
        if (resp.ok && url.origin === location.origin) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      }).catch(() => {
        if (e.request.mode === "navigate") return caches.match("./index.html");
        return new Response("", { status: 504 });
      });
    })
  );
});
