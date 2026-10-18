import UIKit
import WebKit

/// Loads the PWA: live URL when reachable, bundled snapshot otherwise.
/// IndexedDB, service workers, and localStorage all work inside
/// WKWebView — the PWA's offline guarantees survive in the shell.
final class ShellViewController: UIViewController {

    /// State deployment sets these at build time (Info.plist or edit here).
    static let liveURL = URL(string: "https://gateway.openhealth.example/")!
    static let fallbackPage = "index" // Resources/pwa/index.html

    private var webView: WKWebView!

    override func viewDidLoad() {
        super.viewDidLoad()

        let config = WKWebViewConfiguration()
        // Service workers + IndexedDB are on by default in modern WKWebView;
        // the data store must persist so offline data survives restarts.
        config.websiteDataStore = .default()
        config.preferences.isTextInteractionEnabled = true

        webView = WKWebView(frame: view.bounds, configuration: config)
        webView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        webView.allowsBackForwardNavigationGestures = true
        view.addSubview(webView)

        loadTarget()
    }

    private func loadTarget() {
        if Reachability.isOnline {
            webView.load(URLRequest(url: Self.liveURL,
                                    cachePolicy: .reloadIgnoringLocalCacheData))
        } else if let url = Bundle.main.url(forResource: Self.fallbackPage,
                                            withExtension: "html",
                                            subdirectory: "pwa") {
            // Bundled snapshot: file access scoped to the pwa folder only.
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
    }
}

/// Minimal reachability — no third-party dependency, stdlib only.
enum Reachability {
    static var isOnline: Bool {
        // SCNetworkReachability would be the full answer; URLSession probe
        // keeps the shell dependency-free and honest: on failure we fall
        // back to the bundled snapshot and the service worker takes over.
        var flags = SCNetworkReachabilityFlags()
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        guard let reachability = withUnsafePointer(to: &address, {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                SCNetworkReachabilityCreateWithAddress(nil, $0)
            }
        }) else { return false }
        guard SCNetworkReachabilityGetFlags(reachability, &flags) else { return false }
        return flags.contains(.reachable) && !flags.contains(.connectionRequired)
    }
}

