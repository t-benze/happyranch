import SwiftUI
import WebKit

/// NSViewRepresentable wrapping WKWebView.
struct WebView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        config.defaultWebpagePreferences = prefs

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {
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
                    WebView(url: url)
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
                        Text("Run Daemon Here")
                            .font(.headline)
                        Text("Host the HappyRanch runtime\non this machine")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(width: 180, height: 140)
                }
                .buttonStyle(.borderedProminent)

                // CLIENT option
                Button(action: {
                    appDelegate.setRolePreference(.client)
                }) {
                    VStack(spacing: 8) {
                        Image(systemName: "arrow.triangle.swap")
                            .font(.system(size: 28))
                        Text("Connect to Remote")
                            .font(.headline)
                        Text("Connect to a remote\nHappyRanch runtime")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(width: 180, height: 140)
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
