# OpenHealth OS — Android shell

The nurse/CHW app is an offline-first PWA (`apps/pwa`). This shell wraps
it natively for the Play-store-free distribution reality of Nigerian PHCs:
**APK sideloaded at training, updates ship through the OTA channel
(`tools/ota`) or a plain WebView reload.**

Two supported paths:

1. **This WebView shell** (default) — bundles a snapshot of the PWA in
   `app/src/main/assets/pwa/` and loads it with `file://` when the edge
   node is unreachable, so the app opens fully with zero connectivity and
   zero server. When the edge node (or gateway) is reachable it loads the
   live URL instead, and the service worker keeps it offline-capable from
   then on.
2. **Trusted Web Activity (TWA)** — for states that want Play distribution:
   point `build.gradle`'s `applicationId` and the assetlinks.json at the
   state gateway host. The PWA itself is unchanged; TWA is packaging.

## Build

```sh
./gradlew assembleDebug   # produces app-debug.apk (~4 MB)
```

No native code, no Google Play Services dependency, minSdk 24
(Android 7.0, 2016) — five-year-old phones are the design target, not an
afterthought.

## Offline behaviour contract

| State | Behaviour |
|---|---|
| First launch, no network | Bundled PWA snapshot loads from assets; all workflows run locally (IndexedDB + CRDT replica) |
| Edge node reachable | Live URL loads; service worker precaches the shell |
| Later launches, no network | Service worker serves the shell; data syncs when connectivity returns |

The WebView enables DOM storage, database, and the ServiceWorkerController
allowlist — the PWA's offline guarantees survive inside the shell.
