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

/// Main content view: toolbar + WebView + diagnostics.
struct ContentView: View {
    @EnvironmentObject var appDelegate: AppDelegate

    var body: some View {
        VStack(spacing: 0) {
            // Toolbar
            toolbarView

            // Content area
            if let urlString = appDelegate.webViewURL, let url = URL(string: urlString) {
                WebView(url: url)
            } else {
                placeholderView
            }
        }
        .sheet(isPresented: $appDelegate.showDiagnostics) {
            DiagnosticsView(diagnostics: appDelegate.diagnostics, supervisor: appDelegate.supervisor)
        }
    }

    // MARK: - Toolbar

    private var toolbarView: some View {
        HStack(spacing: 12) {
            Text("HappyRanch")
                .font(.headline)
                .padding(.leading, 12)

            Divider()
                .frame(height: 20)

            stateBadge

            Spacer()

            HStack(spacing: 8) {
                Button(action: { appDelegate.startDaemon() }) {
                    Label("Start", systemImage: "play.fill")
                }
                .disabled(!canStart)

                Button(action: { appDelegate.stopDaemonWithConfirmation() }) {
                    Label("Stop", systemImage: "stop.fill")
                }
                .disabled(!canStop)

                Button(action: { appDelegate.showDiagnostics = true }) {
                    Label("Diagnostics", systemImage: "gearshape")
                }
            }
            .padding(.trailing, 12)
        }
        .frame(height: 36)
        .background(.ultraThinMaterial)
    }

    private var stateBadge: some View {
        Text(appDelegate.stateText)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(stateColor.opacity(0.2))
            .foregroundColor(stateColor)
            .cornerRadius(4)
    }

    private var stateColor: Color {
        switch appDelegate.supervisor.state {
        case .running: return .green
        case .starting, .stopping: return .orange
        case .externalRunning: return .blue
        case .crashed, .failed: return .red
        case .unhealthy: return .yellow
        case .stalePid: return .purple
        case .stopped, .notConfigured: return .secondary
        }
    }

    private var canStart: Bool {
        let s = appDelegate.supervisor.state
        return s == .stopped || s == .crashed || s == .stalePid
    }

    private var canStop: Bool {
        appDelegate.supervisor.state.isRunning
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
