/* Service worker — the offline guarantee for the app shell.
 *
 * Precache-first for the shell (HTML/CSS/JS/icons): the app opens fully
 * with zero connectivity. API/sync calls are never cached — they go to the
 * network and fail soft into the outbox (sync.js owns retry).
 *
 * Version bump CACHE on every release; old caches are purged on activate,
 * and the OTA story (tools/ota) ships new shells through staged rollouts.
 */

const CACHE = "openhealth-shell-v4";
const SHELL = [
  "./",
  "./index.html",
  "../design-tokens.css",
  "../ambulance-map.js",
  "./dispatch.html",
  "./css/app.css",
  "./js/app.js",
  "./js/clinical.js",
  "./js/crypto.js",
  "./js/db.js",
  "./js/crdt.js",
  "./js/emr.js",
  "./js/i18n.js",
  "./js/sync.js",
  "./js/views.js",
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
  // never cache API traffic — sync failures must reach the app as failures
  if (url.pathname.startsWith("/api/") || url.pathname.includes("/sync/") || url.pathname.includes("/ingest")) {
    return; // default network behavior; sync.js handles the failure
  }
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
        // navigation request offline → serve the shell (SPA route)
        if (e.request.mode === "navigate") return caches.match("./index.html");
        return new Response("", { status: 504 });
      });
    })
  );
});
