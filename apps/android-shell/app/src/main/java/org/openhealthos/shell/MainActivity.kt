package org.openhealthos.shell

import android.annotation.SuppressLint
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Bundle
import android.webkit.ServiceWorkerClient
import android.webkit.ServiceWorkerController
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.webkit.WebViewFeature
import androidx.webkit.WebViewClientCompat

/**
 * OpenHealth OS Android shell — a thin native vessel for the offline-first
 * PWA. The shell's only jobs: (1) load the live app when the edge node or
 * gateway is reachable, (2) fall back to the bundled PWA snapshot in
 * assets/ when it is not, (3) enable every WebView capability the PWA's
 * offline guarantees depend on (DOM storage, IndexedDB, service workers).
 *
 * All clinical logic lives in the PWA — this class must stay boring.
 */
class MainActivity : ComponentActivity() {

    private lateinit var web: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        configure(web)

        val target = if (isOnline()) LIVE_URL else FALLBACK_ASSET
        web.loadUrl(target)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configure(w: WebView) {
        w.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // IndexedDB / localStorage
            databaseEnabled = true
            allowFileAccess = true            // bundled snapshot only (FALLBACK_ASSET)
            mediaPlaybackRequiresUserGesture = true
        }
        w.webViewClient = object : WebViewClientCompat() {
            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest
            ): Boolean = false // everything stays inside the shell
        }

        // Service workers carry the offline guarantee — allow them for our
        // origins on WebViews old enough to need the compat controller.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_BASIC_USAGE)) {
            ServiceWorkerController.getInstance()
                .setServiceWorkerClient(object : ServiceWorkerClient() {
                    override fun shouldInterceptRequest(
                        request: WebResourceRequest
                    ): WebResourceResponse? = null // default path
                })
        }
    }

    private fun isOnline(): Boolean {
        val cm = getSystemService(CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    companion object {
        /** State gateway URL; per-state builds override via buildConfigField. */
        const val LIVE_URL = "https://gateway.openhealthos.example/app/"
        /** Bundled PWA snapshot — the app opens fully with zero connectivity. */
        const val FALLBACK_ASSET = "file:///android_asset/pwa/index.html"
    }
}
