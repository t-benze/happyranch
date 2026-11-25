import SwiftUI
import WebKit
import HappyRanchSupervisor
import os

/// Main uses the one packaged app origin. `url` identifies the selected native
/// owner for existing release-load callbacks; it is never loaded by WebKit.
struct WebView: NSViewRepresentable {
    let url: URL
    let transport: (any AppAPITransport)?
    let remote: Bool
    var accessUnsaved = false
    var readiness: AppAPIHealth?
    var candidate: [String: Any]?
    var onScheme: (@MainActor (AppScheme) -> Void)?
    var onLoaded: (@MainActor (URL) -> Void)?
    var onFailed: (@MainActor (URL, AppSchemeLoadFailure) -> Void)?
    private static let logger = Logger(subsystem: "com.happyranch.app", category: "AppSchemeLoad")

    private static func logFailure(_ failure: AppSchemeLoadFailure, callbacks: [String] = []) {
        logger.error("App load failed: \(failure.stage.rawValue, privacy: .public); \(failure.description, privacy: .private); callbacks=\(callbacks.joined(separator: "; "), privacy: .private)")
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKDownloadDelegate {
        var owner: WebView
        var scheme: AppScheme?
        private var navigation: WKNavigation?
        private var requestedDocumentURL: URL?
        private var loadID = UUID()
        private var downloads: [WKDownload] = []
        private(set) var callbackURLs: [String] = []
        init(_ owner: WebView) { self.owner = owner }
        private func recordCallback(_ event: String, url: URL?) {
            // Keep app path/trailing-slash evidence, never credentials/query or
            // a data/blob/foreign URL that could contain a body or a secret.
            var parts = url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
            parts?.user = nil; parts?.password = nil; parts?.query = nil; parts?.fragment = nil
            let appURL = parts?.scheme == "happyranch-app" && parts?.host == "app"
            let value = DiagnosticsRedactor.redact(appURL ? (parts?.string ?? "nil") : (url == nil ? "nil" : "non-app-url"))
            recordDiagnostic(event, value)
        }
        private func recordDiagnostic(_ event: String, _ value: String) {
            if callbackURLs.count == 16 { callbackURLs.removeFirst() }
            callbackURLs.append("\(event)=\(String(DiagnosticsRedactor.redact(value).prefix(1024)))")
        }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
            recordCallback("action", url: action.request.url)
            func decide(_ policy: WKNavigationActionPolicy, _ reason: String) {
                let name = policy == .allow ? "allow" : policy == .download ? "download" : "cancel"
                recordDiagnostic("action-policy", "\(name); guard=\(reason)")
                decisionHandler(policy)
            }
            guard let target = action.request.url else { decide(.cancel, "missing-url"); return }
            if target.scheme == "blob", action.shouldPerformDownload {
                decide(.download, "blob-download"); return
            }
            let route = try? AppSchemeRoute(target)
            let refusal: String?
            if action.targetFrame?.isMainFrame != true { refusal = "not-main-frame" }
            else if route == nil { refusal = "invalid-app-route" }
            else if route?.path.hasPrefix("/api/") == true { refusal = "api-navigation" }
            else { refusal = nil }
            if let refusal {
                decide(.cancel, refusal)
                if ["https", "http"].contains(target.scheme ?? ""), action.navigationType == .linkActivated {
                    NSWorkspace.shared.open(target)
                }
                return
            }
            guard scheme?.allowsNavigation == true else { decide(.cancel, "owner-protection"); return }
            requestedDocumentURL = target
            decide(.allow, "passed")
        }
        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void) {
            recordCallback("response", url: response.response.url)
            let received = response.response
            let emitted = scheme?.documentResponse
            let http = received as? HTTPURLResponse
            recordCallback("response-source", url: emitted?.url)
            // MIME metadata is untrusted text; keep no embedded URL or secret.
            let mime = DiagnosticsRedactor.redact(received.mimeType ?? "nil").replacingOccurrences(
                of: #"[A-Za-z][A-Za-z0-9+.-]*://[^\s"<>]+"#, with: "[URL]", options: .regularExpression)
            recordDiagnostic("response-metadata", "class=\(String(reflecting: type(of: received))); mimeType=\(String(mime.prefix(128))); status=\(http.map { String($0.statusCode) } ?? "unavailable"); sourceStatus=\(emitted.map { String($0.statusCode) } ?? "unavailable"); mainFrame=\(response.isForMainFrame); canShowMIMEType=\(response.canShowMIMEType)")
            func decide(_ policy: WKNavigationResponsePolicy, _ reason: String) {
                recordDiagnostic("response-policy", "\(policy == .allow ? "allow" : "cancel"); guard=\(reason)")
                decisionHandler(policy)
            }
            guard response.isForMainFrame else { decide(.cancel, "not-main-frame"); return }
            guard let url = received.url else { decide(.cancel, "missing-url"); return }
            guard (try? AppSchemeRoute(url)) != nil else { decide(.cancel, "invalid-app-route"); return }
            // WebKit reconstructs non-HTTP schemes as URLResponse, dropping
            // the HTTP subclass/status. Validate the exact native response
            // emitted for this document instead of depending on that cast.
            guard let emitted else { decide(.cancel, "missing-document-response"); return }
            guard emitted.url == url else { decide(.cancel, "document-response-url-mismatch"); return }
            guard (200...299).contains(emitted.statusCode) else { decide(.cancel, "source-status-not-2xx"); return }
            if let http, !(200...299).contains(http.statusCode) { decide(.cancel, "response-status-not-2xx"); return }
            guard received.mimeType == emitted.mimeType else { decide(.cancel, "mime-type-mismatch"); return }
            guard response.canShowMIMEType else { decide(.cancel, "mime-type-not-displayable"); return }
            decide(.allow, "passed")
        }
        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            recordCallback("provisional", url: webView.url)
            self.navigation = navigation; loadID = UUID()
            scheme?.documentDidStart(url: requestedDocumentURL)
            requestedDocumentURL = nil
        }
        func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
            recordCallback("commit", url: webView.url)
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            recordCallback("finish", url: webView.url)
            guard let navigation, self.navigation === navigation, let scheme,
                  let url = webView.url, (try? AppSchemeRoute(url)) != nil else { return }
            let expected = loadID
            Task {
                do {
                    try await scheme.admitDocument()
                    guard loadID == expected, self.navigation === navigation, self.scheme === scheme,
                          webView.url == url else { return }
                    WebView.logger.debug("App load admitted: \(scheme.documentAdmission.description, privacy: .public)")
                    owner.onLoaded?(owner.url)
                } catch {
                    guard loadID == expected, self.scheme === scheme else { return }
                    let failure = (error as? AppSchemeLoadFailure) ?? AppSchemeLoadFailure(stage: .readiness, error: error)
                    WebView.logFailure(failure, callbacks: callbackURLs)
                    owner.onFailed?(owner.url, failure)
                }
            }
        }
        private func failed(_ navigation: WKNavigation?, failure: AppSchemeLoadFailure) {
            guard let navigation, self.navigation === navigation else { return }
            loadID = UUID(); self.navigation = nil; scheme?.documentDidStart()
            WebView.logFailure(failure, callbacks: callbackURLs)
            owner.onFailed?(owner.url, failure)
        }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            recordCallback("navigation-commit-failed", url: webView.url)
            recordCallback("failing-url", url: (error as NSError).userInfo[NSURLErrorFailingURLErrorKey] as? URL)
            failed(navigation, failure: AppSchemeLoadFailure(stage: .navigationCommit, error: error))
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            recordCallback("navigation-provisional-failed", url: webView.url)
            recordCallback("failing-url", url: (error as NSError).userInfo[NSURLErrorFailingURLErrorKey] as? URL)
            failed(navigation, failure: AppSchemeLoadFailure(stage: .navigationProvisional, error: error))
        }
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            recordCallback("content-process-terminated", url: webView.url)
            loadID = UUID(); navigation = nil; scheme?.close()
            let failure = AppSchemeLoadFailure(stage: .contentProcessTermination)
            WebView.logFailure(failure, callbacks: callbackURLs)
            owner.onFailed?(owner.url, failure)
        }
        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { downloads.append(download); download.delegate = self }
        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { downloads.append(download); download.delegate = self }
        func downloadDidFinish(_ download: WKDownload) { downloads.removeAll { $0 === download } }
        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { downloads.removeAll { $0 === download } }
        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                      completionHandler: @escaping @MainActor @Sendable (URL?) -> Void) {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
            panel.begin { result in completionHandler(result == .OK ? panel.url : nil) }
        }
    }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        do {
            guard let root = Bundle.main.resourceURL?.appendingPathComponent("web/dist", isDirectory: true) else { throw AppAPIError.unavailable }
            let scheme = try AppScheme(root: root, transport: transport, remote: remote,
                promptURL: remote || transport == nil ? nil : url.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/v1", accessUnsaved: accessUnsaved)
            scheme.configure(config); context.coordinator.scheme = scheme
            onScheme?(scheme)
            scheme.updateCandidate(candidate, accessUnsaved: accessUnsaved)
            let view = WKWebView(frame: .zero, configuration: config)
            view.navigationDelegate = context.coordinator
            scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
            return view
        } catch {
            let view = WKWebView(frame: .zero, configuration: config)
            let failure = AppSchemeLoadFailure(stage: .initialization, error: error)
            Self.logFailure(failure)
            Task { onFailed?(url, failure) }
            return view
        }
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {
        context.coordinator.owner = self
        if let scheme = context.coordinator.scheme {
            onScheme?(scheme)
            scheme.updateCandidate(candidate, accessUnsaved: accessUnsaved)
        }
        if let readiness { context.coordinator.scheme?.updateReadiness(readiness) }
    }
    static func dismantleNSView(_ nsView: WKWebView, coordinator: Coordinator) {
        coordinator.scheme?.close(); coordinator.scheme = nil
    }
}

/// Main content view: full-window WebView; its header owns window controls.
/// Daemon controls live in the Daemon menu bar menu.
struct ContentView: View {
    @EnvironmentObject var appDelegate: AppDelegate

    var body: some View {
        ZStack {
            // The packaged document hosts dialogs even before a target exists.
            // This URL is callback identity only; WebKit always loads app origin.
            let url = appDelegate.webViewURL.flatMap { URL(string: $0) }
                ?? URL(string: AppSchemeRoute.origin + "/")!
            ZStack(alignment: .top) {
                let lifetime = appDelegate.homeInvite.lifetime
                WebView(url: url, transport: appDelegate.appAPITransport,
                    remote: appDelegate.homeInvite.hasActiveSession || appDelegate.connectionRole == .client,
                    accessUnsaved: appDelegate.homeInvite.hasUnsavedAccessWarning,
                    readiness: appDelegate.clientLane == .managed ? appDelegate.homeInvite.appReadiness : nil,
                    candidate: appDelegate.homeInvite.bridgeCandidate,
                    onScheme: { appDelegate.bindMainScheme($0) },
                    onLoaded: {
                        appDelegate.homeInvite.mainDocumentAdmitted()
                        appDelegate.homeInvite.webLoaded(url: $0, lifetime: lifetime)
                    },
                    onFailed: { selected, _ in appDelegate.homeInvite.webFailed(url: selected, lifetime: lifetime) })
                    .id("happyranch-main-document")
                if appDelegate.connectionRole == .client && appDelegate.clientLane == .managed {
                    HomeSessionStatus(model: appDelegate.homeInvite)
                        .environmentObject(appDelegate)
                }
                if appDelegate.supervisor.state == .unhealthy ||
                   appDelegate.supervisor.state == .failed {
                    unhealthyBanner
                }
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

/// Retained legacy phase strip; its MAC-3 cutover is separate from the web header.
private struct HomeSessionStatus: View {
    @ObservedObject var model: HomeInviteModel
    var body: some View {
        if model.phase != .connected {
            HStack {
                if model.phase != .connected { Text(model.phase.message) }
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
