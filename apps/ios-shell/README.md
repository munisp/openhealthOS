# OpenHealth OS — iOS shell

Yes: the platform ships **both** native shells — Android
(`apps/android-shell`, Kotlin WebView) and iOS (this directory, Swift
WKWebView). Both are deliberately boring vessels for the same
offline-first PWA (`apps/pwa`); all clinical logic, offline guarantees,
and the ambulance-tracker live in the PWA exactly once.

Two supported paths, same as Android:

1. **This WKWebView shell** (default) — bundles a snapshot of the PWA in
   `OpenHealthShell/Resources/pwa/` and loads it when the edge node is
   unreachable; loads the live URL when it is. The PWA's service worker
   and IndexedDB work inside WKWebView, so the offline contract survives.
2. **PWA via Add to Home Screen** — for states that prefer zero app
   store process: Safari's Add-to-Home-Screen installs the PWA with its
   manifest. Nothing in this directory is needed for that path.

## Build

This repository cannot compile iOS (no macOS/Xcode in CI or the dev
sandbox — that is a deliberate, stated limit, not an oversight). On a
Mac:

```sh
open OpenHealthShell.xcodeproj   # or: xcodebuild -scheme OpenHealthShell
./bundle-pwa.sh                  # refreshes Resources/pwa from apps/pwa
```

Target: iOS 15+ (2021 devices — the same five-year-old-phone doctrine as
Android's minSdk 24).

## Offline behaviour contract (mirrors Android exactly)

| State | Behaviour |
|---|---|
| First launch, no network | Bundled PWA snapshot loads from the bundle; workflows run locally |
| Edge node reachable | Live URL loads; service worker precaches the shell |
| Later launches, no network | Service worker serves the shell; syncs on reconnect |

## What the shell adds natively

Nothing clinical. Reachability detection, the fallback load, and
`navigator.geolocation` permission for the paramedic ping loop — iOS
location prompts are configured in `Info.plist`
(`NSLocationWhenInUseUsageDescription` is present and truthful: it is
used only while a unit is on duty).
