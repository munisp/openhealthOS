import UIKit

/// OpenHealth OS iOS shell — a thin native vessel for the offline-first
/// PWA. Same doctrine as the Android shell: the shell's only jobs are
/// (1) load the live app when the edge node or gateway is reachable,
/// (2) fall back to the bundled PWA snapshot when it is not,
/// (3) stay boring — all clinical logic lives in the PWA.
@main
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        window = UIWindow(frame: UIScreen.main.bounds)
        window?.rootViewController = ShellViewController()
        window?.makeKeyAndVisible()
        return true
    }
}
