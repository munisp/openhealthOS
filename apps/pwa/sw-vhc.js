/* sw-vhc.js — offline shell for the VHC surfaces.
 *
 * Doctrine at the border: a border post with zero connectivity still
 * renders the console and the last-known payload cache. App shell is
 * cache-first; payloads are network-first with cache fallback — a
 * stale payload renders with its checksum and a STALE badge, never as
 * if it were fresh. Absence is not a value; staleness is stated. */
const SHELL = "vhc-shell-v11";
const PAYLOADS = "vhc-payloads-v1";
const SHELL_URLS = [
  "./vhc-verify.html", "./traveler.html", "./dispatch.html",
  "./vhc-fund.html", "./vhc-br10.html", "./vhc-helpdesk.html",
  "./vhc-demo.html", "./vhc-tour.html", "./vhc-growth.html",
  "./clinical-depth.html", "./inpatient-depth.html",
  "./national-depth.html", "./national.ng.json", "./national.cv.json",
  "./sync-depth.html", "./sync.ng.json", "./sync.cv.json",
  "./css/depth-theme.css", "./css/brand.ng.css", "./css/brand.cv.css",
  "./js/depth-strings.json", "./js/depth-i18n.js",
  "./specialty-depth.html", "./design-tokens.css",
  "./agentwrite.html", "./agentwrite.json",
  "./deployproof.html", "./deployproof.json",
  "./tamperproof.html", "./tamperproof.json",
  "./proofindex.html", "./proofindex.json",
  "./anchorproof.html", "./anchorproof.json",
  "./shipproof.html", "./shipproof.json",
  "./inferproof.html", "./inferproof.json",
  "./kgproof.html", "./kgproof.json",
  "./geoproof.html", "./geoproof.json",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL)
    .then((c) => c.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => ![SHELL, PAYLOADS].includes(k))
        .map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.endsWith(".json")) {
    // payload: network-first, cache fallback, stamp the response date
    e.respondWith(
      fetch(e.request, { cache: "no-store" }).then((resp) => {
        const copy = resp.clone();
        caches.open(PAYLOADS).then((c) => c.put(e.request, copy));
        return resp;
      }).catch(() => caches.match(e.request).then((cached) => {
        if (!cached) throw new Error("offline and no cached payload");
        const h = new Headers(cached.headers);
        h.set("X-VHC-Served", "stale-cache");
        return cached.text().then((body) =>
          new Response(body, { status: 200, headers: h }));
      })));
    return;
  }
  // shell: cache-first
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
