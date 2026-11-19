import SwiftUI
import WebKit

/// NSViewRepresentable wrapping WKWebView.
struct WebView: NSViewRepresentable {
    let url: URL
    var verifiedHome = false
    var onLoaded: (@MainActor (URL) -> Void)?
    var onFailed: (@MainActor (URL) -> Void)?

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        var owner: WebView
        private var authorizedRoot = false
        init(_ owner: WebView) { self.owner = owner }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
            guard owner.verifiedHome else { decisionHandler(.allow); return }
            guard let target = navigationAction.request.url,
                  target.scheme == owner.url.scheme, target.host == owner.url.host,
                  target.port == owner.url.port else { decisionHandler(.cancel); owner.onFailed?(owner.url); return }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void) {
            guard owner.verifiedHome, navigationResponse.isForMainFrame else { decisionHandler(.allow); return }
            guard let response = navigationResponse.response as? HTTPURLResponse,
                  (200...299).contains(response.statusCode), let target = response.url,
                  target.scheme == owner.url.scheme, target.host == owner.url.host, target.port == owner.url.port,
                  authorizedRoot || target == owner.url else {
                decisionHandler(.cancel); owner.onFailed?(owner.url); return
            }
            authorizedRoot = true
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard owner.verifiedHome, authorizedRoot, let target = webView.url,
                  target.scheme == owner.url.scheme, target.host == owner.url.host, target.port == owner.url.port else { return }
            owner.onLoaded?(owner.url)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            authorizedRoot = false; owner.onFailed?(owner.url)
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            authorizedRoot = false; owner.onFailed?(owner.url)
        }
    }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {
        context.coordinator.owner = self
        if nsView.url != url {
            nsView.load(URLRequest(url: url))
        }
    }
}

/// Main content view: full-window WebView (no in-window toolbar).
/// Daemon controls live in the Daemon menu bar menu.
struct ContentView: View {
    @EnvironmentObject var appDelegate: AppDelegate

    var body: some View {
        ZStack {
            if let urlString = appDelegate.webViewURL, let url = URL(string: urlString) {
                ZStack(alignment: .top) {
                    let lifetime = appDelegate.homeInvite.lifetime
                    WebView(url: url, verifiedHome: appDelegate.clientLane == .managed && appDelegate.connectionRole == .client,
                        onLoaded: { appDelegate.homeInvite.webLoaded(url: $0, lifetime: lifetime) },
                        onFailed: { appDelegate.homeInvite.webFailed(url: $0, lifetime: lifetime) })
                        .id(lifetime)
                    if appDelegate.connectionRole == .client && appDelegate.clientLane == .managed {
                        HomeSessionStatus(model: appDelegate.homeInvite)
                            .environmentObject(appDelegate)
                    }
                    if appDelegate.supervisor.state == .unhealthy ||
                       appDelegate.supervisor.state == .failed {
                        unhealthyBanner
                    }
                }
            } else {
                placeholderView
            }
        }
        .sheet(isPresented: $appDelegate.showDiagnostics) {
            DiagnosticsView(diagnostics: appDelegate.diagnostics, supervisor: appDelegate.supervisor)
        }
        .sheet(isPresented: $appDelegate.showRemoteAccess) {
            RemoteAccessSheet()
                .environmentObject(appDelegate)
        }
        .navigationTitle(windowTitle)
    }

    private var windowTitle: String {
        let state = appDelegate.supervisor.state.description
        return "HappyRanch — \(state)"
    }

    // MARK: - Placeholder

    /// Whether this is the first launch (no role preference persisted yet).
    private var isFirstLaunch: Bool {
        appDelegate.connectionRolePreference == nil
    }

    @ViewBuilder
    private var placeholderView: some View {
        if isFirstLaunch {
            roleSelectionView
        } else {
            normalPlaceholderView
        }
    }

    /// First-launch welcome screen: user picks HOME (run daemon) or CLIENT (connect to remote).
    private var roleSelectionView: some View {
        VStack(spacing: 24) {
            Image(systemName: "desktopcomputer")
                .font(.system(size: 48))
                .foregroundColor(.secondary)

            Text("Welcome to HappyRanch")
                .font(.title)
                .fontWeight(.semibold)

            Text("How will you use this Mac?")
                .font(.callout)
                .foregroundColor(.secondary)

            HStack(spacing: 32) {
                // HOME option
                Button(action: {
                    appDelegate.setRolePreference(.home)
                }) {
                    VStack(spacing: 8) {
                        Image(systemName: "house.fill")
                            .font(.system(size: 28))
                        Text("Run HappyRanch on this Mac")
                            .font(.headline)
                            .multilineTextAlignment(.center)
                        Text("Host the HappyRanch runtime\non this machine")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(width: 240, height: 140)
                }
                .buttonStyle(.borderedProminent)

                // CLIENT option
                Button(action: {
                    appDelegate.setRolePreference(.client)
                }) {
                    VStack(spacing: 8) {
                        Image(systemName: "arrow.triangle.swap")
                            .font(.system(size: 28))
                        Text("Connect to another computer")
                            .font(.headline)
                            .multilineTextAlignment(.center)
                        Text("Connect to a remote\nHappyRanch runtime")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(width: 240, height: 140)
                }
                .buttonStyle(.bordered)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Normal placeholder (after role has been selected).
    private var normalPlaceholderView: some View {
        VStack(spacing: 16) {
            Image(systemName: "desktopcomputer")
                .font(.system(size: 48))
                .foregroundColor(.secondary)

            Text("HappyRanch Daemon")
                .font(.title2)

            Text(appDelegate.stateText)
                .foregroundColor(.secondary)

            if appDelegate.connectionRole == .home {
                // LOCAL daemon controls
                daemonControls
            }

            Divider()
                .padding(.horizontal, 40)

            // A2 Remote connection surface (shown in both roles)
            RemoteConnectionView()
                .environmentObject(appDelegate)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var daemonControls: some View {
        if appDelegate.supervisor.state == .notConfigured ||
           appDelegate.supervisor.state == .stopped ||
           appDelegate.supervisor.state == .crashed ||
           appDelegate.supervisor.state == .stalePid ||
           appDelegate.supervisor.state == .failed {
            Button("Start Daemon") {
                appDelegate.startDaemon()
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }

        if appDelegate.supervisor.state == .externalRunning {
            Text("External daemon detected — attach to view it")
                .font(.callout)
                .foregroundColor(.secondary)

            Button("Probe & Connect") {
                if let port = appDelegate.supervisor.observedPort {
                    Task { @MainActor in
                        await appDelegate.probeAndLoad(port: port)
                    }
                }
            }
            .buttonStyle(.bordered)
        }
    }

    // MARK: - Unhealthy / failed banner

    /// Lightweight in-window warning banner shown when the daemon is
    /// unhealthy or failed WHILE the WebView is up (the WebView stays visible;
    /// the banner overlays the top edge with a recovery action).
    private var unhealthyBanner: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.yellow)
            VStack(alignment: .leading, spacing: 2) {
                Text("Daemon issue")
                    .font(.headline)
                Text(appDelegate.supervisor.state == .unhealthy
                     ? "Health check is failing — daemon may recover on its own."
                     : "Daemon has failed and needs a restart.")
                    .font(.caption)
            }
            Spacer()
            if appDelegate.supervisor.state == .failed {
                Button("Restart Daemon") {
                    appDelegate.startDaemon()
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
                .shadow(radius: 4)
        )
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }
}

/// Saving failure remains visible even after the web UI replaces the form.
private struct HomeSessionStatus: View {
    @EnvironmentObject var appDelegate: AppDelegate
    @ObservedObject var model: HomeInviteModel
    private func copy(_ en: String, _ zh: String) -> String {
        Locale.preferredLanguages.first?.hasPrefix("zh") == true ? zh : en
    }
    var body: some View {
        if model.phase != .connected || model.saveFailure != nil {
            HStack {
                if model.phase != .connected { Text(model.phase.message) }
                if model.saveFailure != nil {
                    VStack(alignment: .leading) {
                        Text(copy("Access was not saved", "访问权限未保存")).foregroundStyle(.red)
                        Text(copy("Reopening may need a new invite. Review saved access in Connection.",
                                  "重新打开可能需要新邀请。请在“连接”中查看已保存的访问权限。"))
                            .font(.caption)
                    }
                    Button(copy("Retry save…", "重试保存…")) { appDelegate.showConnection() }
                        .accessibilityHint(copy("Opens the packaged Connection controls", "打开应用内置的连接控制页"))
                }
            }
            .padding(10)
            .background(.regularMaterial)
        }
    }
}

// MARK: - Remote Access sheet wrapper (TASK-2298)

/// Sheet wrapper around RemoteConnectionView with a Done button for dismissal.
/// Presented from the body-level .sheet in ContentView when showRemoteAccess
/// is toggled — reachable in ALL app states (placeholder AND WebView).
struct RemoteAccessSheet: View {
    @EnvironmentObject var appDelegate: AppDelegate

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                Button("Done") {
                    appDelegate.showRemoteAccess = false
                }
                .keyboardShortcut(.escape, modifiers: [])
                .padding(.horizontal)
                .padding(.vertical, 8)
            }
            Divider()
            RemoteConnectionView()
                .environmentObject(appDelegate)
        }
        .frame(minWidth: 500, minHeight: 400)
    }
}
