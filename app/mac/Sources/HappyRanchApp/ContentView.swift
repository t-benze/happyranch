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
        Group {
            if let urlString = appDelegate.webViewURL, let url = URL(string: urlString) {
                WebView(url: url)
            } else {
                placeholderView
            }
        }
        .sheet(isPresented: $appDelegate.showDiagnostics) {
            DiagnosticsView(diagnostics: appDelegate.diagnostics, supervisor: appDelegate.supervisor)
        }
        .navigationTitle(windowTitle)
    }

    private var windowTitle: String {
        let state = appDelegate.supervisor.state.description
        return "HappyRanch — \(state)"
    }

    // MARK: - Placeholder

    private var placeholderView: some View {
        VStack(spacing: 16) {
            Image(systemName: "desktopcomputer")
                .font(.system(size: 48))
                .foregroundColor(.secondary)

            Text("HappyRanch Daemon")
                .font(.title2)

            Text(appDelegate.stateText)
                .foregroundColor(.secondary)

            if appDelegate.supervisor.state == .notConfigured ||
               appDelegate.supervisor.state == .stopped ||
               appDelegate.supervisor.state == .crashed ||
               appDelegate.supervisor.state == .stalePid {
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
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
