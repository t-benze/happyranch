import AppKit
import HappyRanchSupervisor
@preconcurrency import WebKit

/// App-owned connection surface. The runtime WKWebView never receives this
/// configuration, controller, scheme handler, relay or window reference.
@MainActor
final class ConnectionWindow: NSWindowController, NSWindowDelegate, WKNavigationDelegate,
                              WKUIDelegate, WKURLSchemeHandler, WKScriptMessageHandlerWithReply {
    private static let world = WKContentWorld.world(name: "happyranch.connection.v1")
    private let content = WKUserContentController()
    private weak var app: AppDelegate?
    private let openingRole: ConnectionRole
    private var view: WKWebView?
    private var assets: ConnectionAssets?
    private var generation = UUID()
    private var live = true
    private var admitted = false
    private var admitting = false
    private var initialAction = true
    private var initialStart = true
    private var initialResponse = true
    private var registered = false
    private var urlObservation: NSKeyValueObservation?

    init(app: AppDelegate) {
        self.app = app
        self.openingRole = app.connectionRole
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 640),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = Self.copy("Connection", "连接")
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
        window.center()
        do {
            guard let root = Bundle.main.resourceURL?.appendingPathComponent("Connection", isDirectory: true) else { throw ConnectionRefusal.invalid }
            let assets = try ConnectionAssets(root: root)
            self.assets = assets
            let configuration = WKWebViewConfiguration()
            configuration.websiteDataStore = .nonPersistent()
            configuration.userContentController = content
            configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
            configuration.setURLSchemeHandler(self, forURLScheme: "happyranch-shell")
            content.addScriptMessageHandler(self, contentWorld: Self.world, name: "connection")
            registered = true
            content.addUserScript(WKUserScript(source: ConnectionScripts.relay, injectionTime: .atDocumentStart,
                                              forMainFrameOnly: true, in: Self.world))
            content.addUserScript(WKUserScript(source: ConnectionScripts.page, injectionTime: .atDocumentStart,
                                              forMainFrameOnly: true, in: .page))
            let view = WKWebView(frame: .zero, configuration: configuration)
            self.view = view
            urlObservation = view.observe(\.url, options: [.new]) { [weak self] view, _ in
                MainActor.assumeIsolated {
                    guard let self, self.live, let url = view.url else { return }
                    if url.absoluteString != ConnectionAssets.entry { self.invalidate() }
                }
            }
            view.navigationDelegate = self
            view.uiDelegate = self
            window.contentView = view
            // Defense in depth beside response CSP and the manifest-only scheme.
            var rules: [[String: Any]] = [["trigger": ["url-filter": ".*"], "action": ["type": "block"]]]
            for path in assets.resources.keys.sorted() {
                rules.append(["trigger": ["url-filter": "^" + NSRegularExpression.escapedPattern(for: ConnectionAssets.origin + "/" + path) + "$"],
                              "action": ["type": "ignore-previous-rules"]])
            }
            let data = try JSONSerialization.data(withJSONObject: rules)
            let token = generation
            WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "happyranch.connection.assets.v1",
                encodedContentRuleList: String(decoding: data, as: UTF8.self)) { [weak self] list, error in
                guard let self, self.live, self.generation == token else { return }
                guard error == nil, let list, let view = self.view else { self.invalidate(); return }
                self.content.add(list)
                view.load(URLRequest(url: URL(string: ConnectionAssets.entry)!))
            }
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(5))
                guard let self, self.live, self.generation == token, !self.admitted else { return }
                self.invalidate()
            }
        } catch { invalidate() }
    }

    required init?(coder: NSCoder) { nil }

    private static func copy(_ en: String, _ zh: String) -> String {
        Locale.preferredLanguages.first?.hasPrefix("zh") == true ? zh : en
    }

    func invalidate(showNotice: Bool = true) {
        guard live else { return }
        live = false; admitted = false; admitting = false; generation = UUID()
        if registered {
            content.removeScriptMessageHandler(forName: "connection", contentWorld: Self.world)
            registered = false
        }
        content.removeAllUserScripts()
        content.removeAllContentRuleLists()
        urlObservation?.invalidate(); urlObservation = nil
        view?.stopLoading()
        view?.navigationDelegate = nil
        view?.uiDelegate = nil
        view?.removeFromSuperview()
        view = nil
        assets = nil
        if showNotice {
            let label = NSTextField(wrappingLabelWithString: Self.copy(
                "Connection view unavailable. Reopen Connection to try a fresh view. The planned happyranch connection CLI is not available yet; command-line connection actions remain pending.",
                "连接视图不可用。请重新打开“连接”以创建新视图。计划中的 happyranch connection 命令行功能尚未提供；命令行连接操作仍待实现。"))
            let container = NSView()
            label.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(label)
            NSLayoutConstraint.activate([label.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 32),
                label.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -32),
                label.centerYAnchor.constraint(equalTo: container.centerYAnchor)])
            window?.contentView = container
        }
    }

    func windowWillClose(_ notification: Notification) { invalidate(showNotice: false) }

    func invalidateIfRoleChanged(_ role: ConnectionRole) {
        if role != openingRole { invalidate() }
    }

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        do {
            guard live, webView === view, task.request.httpMethod == "GET", let url = task.request.url,
                  let assets else { throw ConnectionRefusal.invalid }
            let (data, mime) = try assets.resolve(url)
            guard let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: [
                "Content-Type": mime, "Content-Length": String(data.count), "Content-Security-Policy": ConnectionAssets.csp,
                "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"
            ]) else { throw ConnectionRefusal.invalid }
            task.didReceive(response); task.didReceive(data); task.didFinish()
        } catch {
            task.didFailWithError(NSError(domain: "HappyRanch.Connection", code: 1))
            invalidate()
        }
    }
    func webView(_ webView: WKWebView, stop task: any WKURLSchemeTask) { /* delivery is synchronous */ }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard live, webView === view, initialAction, action.targetFrame?.isMainFrame == true,
              action.request.url?.absoluteString == ConnectionAssets.entry, action.request.httpMethod == "GET",
              action.navigationType == .other else {
            decisionHandler(.cancel); invalidate(); return
        }
        initialAction = false
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationResponsePolicy) -> Void) {
        guard live, webView === view, initialResponse, response.isForMainFrame,
              response.response.url?.absoluteString == ConnectionAssets.entry,
              let http = response.response as? HTTPURLResponse, http.statusCode == 200,
              http.value(forHTTPHeaderField: "Content-Security-Policy") == ConnectionAssets.csp else {
            decisionHandler(.cancel); invalidate(); return
        }
        initialResponse = false
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        guard live, webView === view, initialStart else { invalidate(); return }
        initialStart = false
    }
    func webView(_ webView: WKWebView, didReceiveServerRedirectForProvisionalNavigation navigation: WKNavigation!) { invalidate() }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { invalidate() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { invalidate() }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { invalidate() }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        invalidate(); return nil
    }
    func webViewDidClose(_ webView: WKWebView) { invalidate() }

    private func valid(_ message: WKScriptMessage, controller: WKUserContentController, token: UUID) -> Bool {
        let origin = message.frameInfo.securityOrigin
        return live && app?.connectionRole == openingRole && generation == token
            && controller === content && message.name == "connection" && message.webView === view
            && message.frameInfo.isMainFrame && origin.protocol == "happyranch-shell"
            && origin.host == "connection" && origin.port == 0
            && message.frameInfo.request.url?.absoluteString == ConnectionAssets.entry
            && view?.url?.absoluteString == ConnectionAssets.entry
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
        let token = generation
        guard valid(message, controller: userContentController, token: token) else {
            replyHandler(ConnectionRequest.reply(id: "r0", error: "documentChanged"), nil); invalidate(); return
        }
        guard let raw = message.body as? String else {
            replyHandler(ConnectionRequest.reply(id: "r0", error: "invalidRequest"), nil); invalidate(); return
        }
        guard let request = try? ConnectionRequest(raw) else {
            replyHandler(ConnectionRequest.reply(id: "r0", error: "invalidRequest"), nil); return
        }
        if admitted {
            replyHandler(dispatch(request), nil)
            return
        }
        // The initial hello can only probe admission. Dispatch/capabilities wait
        // for native frame provenance AND structured world/origin checks.
        guard request.command == "bridge.hello", !admitting, !initialResponse, let view else {
            replyHandler(ConnectionRequest.reply(id: request.id), nil); return
        }
        admitting = true
        view.callAsyncJavaScript(ConnectionScripts.probe, arguments: [:], in: message.frameInfo, in: Self.world) { [weak self] result in
            guard let self, self.valid(message, controller: userContentController, token: token) else {
                replyHandler(ConnectionRequest.reply(id: request.id, error: "documentChanged"), nil); return
            }
            guard case .success(let value) = result, value as? Bool == true else {
                replyHandler(ConnectionRequest.reply(id: request.id), nil); self.invalidate(); return
            }
            view.callAsyncJavaScript("return typeof window.webkit?.messageHandlers?.connection === 'undefined';",
                                     arguments: [:], in: message.frameInfo, in: .page) { [weak self] result in
                guard let self, self.valid(message, controller: userContentController, token: token) else {
                    replyHandler(ConnectionRequest.reply(id: request.id, error: "documentChanged"), nil); return
                }
                guard case .success(let value) = result, value as? Bool == true else {
                    replyHandler(ConnectionRequest.reply(id: request.id), nil); self.invalidate(); return
                }
                self.admitting = false; self.admitted = true
                replyHandler(self.dispatch(request), nil)
            }
        }
    }

    private func dispatch(_ request: ConnectionRequest) -> String {
        guard live, admitted, let app else { return ConnectionRequest.reply(id: request.id, error: "documentChanged") }
        switch request.command {
        case "bridge.hello":
            return ConnectionRequest.reply(id: request.id, result: [
                "version": 1, "platform": "mac", "roles": ["client"],
                "capabilities": [["command": "client.status", "availability": "available"]],
                "observationLimits": ["host": [], "client": ["savedAccess", "route", "observedAt", "computers",
                    "activeComputerRef", "expiresAt", "comparisonPhrase"]]
            ])
        case "client.status":
            return ConnectionRequest.reply(id: request.id, result: ConnectionClientProjection.snapshot(app: app))
        default: return ConnectionRequest.reply(id: request.id)
        }
    }
}

/// Read existing in-memory coarse state only. No discovery, health request,
/// Keychain access, native operation or serialization of a model/trust record.
@MainActor
enum ConnectionClientProjection {
    static func snapshot(app: AppDelegate) -> [String: Any] {
        var stage = "idle", runtime = "unknown"
        if app.connectionRole == .client && app.clientLane == .managed {
            switch app.homeInvite.phase {
            case .idle:
                switch app.managedJoin.phase {
                case .draft, .checkingKeychain, .waitingForKeychain: stage = "idle"
                case .joining: stage = "joining"
                case .joined, .pairing: stage = "pairing"
                case .paired, .online, .reconnecting, .unreachable: stage = "opening"
                case .canceling: stage = "stopping"
                case .interrupted, .accessDenied, .revoked, .error: stage = "failed"
                }
            case .checkingSavedAccess: stage = "idle"
            case .validating: stage = "validating"
            case .joining: stage = "joining"
            case .pairing: stage = "pairing"
            case .awaitingApproval: stage = "waitingApproval"
            case .opening: stage = "opening"
            case .connected: stage = "connected"; runtime = "available"
            case .stopping: stage = "stopping"
            case .failed, .keychain: stage = "failed"
            }
        } else if app.connectionRole == .client {
            // Legacy DIY health is not the expected-release-UI load gate.
            if app.isConnecting { stage = "pairing" }
            else if app.connectionState == .online || app.connectionState == .reconnecting { stage = "opening" }
            else if app.connectionState == .pairedUnreachable { stage = "failed" }
        }
        return ["stage": stage, "runtime": runtime, "savedAccess": "unknown", "route": "unknown",
                "observedAt": NSNull(), "observation": "known", "computers": NSNull(),
                "activeComputerRef": NSNull(), "expiresAt": NSNull(), "comparisonPhrase": NSNull()]
    }
}
