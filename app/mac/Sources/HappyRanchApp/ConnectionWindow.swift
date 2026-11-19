import AppKit
import Combine
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
    private var admittedFrame: WKFrameInfo?
    private var wantsEvents = false
    private var observations: Set<AnyCancellable> = []
    private var eventScheduled = false
    private var eventInFlight = false
    private var eventDirty = false
    private var eventSequence = 0
    private var reads = 0
    private var mutations = 0
    private var cancellations = 0
    private let catalog = ConnectionComputerCatalog()
    private var ledger = ConnectionMutationLedger()
    private var mutationLifetime: HomeInviteModel.Lifetime?
    private var pendingMutation: (id: String, reply: CheckedContinuation<String, Never>)?

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
        observations.removeAll(); wantsEvents = false; admittedFrame = nil
        if let pending = pendingMutation {
            pendingMutation = nil
            ledger.timedOut(pending.id)
            pending.reply.resume(returning: ConnectionRequest.reply(id: pending.id,
                error: .init(.documentChanged, effect: .unknown, recovery: .explicitRetry)))
        }
        if let mutationLifetime, app?.homeInvite.lifetime == mutationLifetime { app?.homeInvite.documentDidChange() }
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
            replyHandler(ConnectionRequest.reply(id: "r0", error: .init(.documentChanged)), nil); invalidate(); return
        }
        // Fixed transport lifecycle for on/unsubscribe; it grants no command
        // capability and still requires native frame/document admission.
        if let subscribe = message.body as? Bool {
            guard subscribe != wantsEvents else { replyHandler(nil, nil); return }
            wantsEvents = subscribe
            if admitted { setEventSubscription() }
            replyHandler(nil, nil); return
        }
        guard let raw = message.body as? String else {
            replyHandler(ConnectionRequest.reply(id: "r0", error: .init(.invalidRequest)), nil); invalidate(); return
        }
        guard let request = try? ConnectionRequest(raw) else {
            replyHandler(ConnectionRequest.reply(id: "r0", error: .init(.invalidRequest)), nil); return
        }
        if admitted {
            respond(request, message: message, controller: userContentController, token: token, replyHandler: replyHandler)
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
                replyHandler(ConnectionRequest.reply(id: request.id, error: .init(.documentChanged)), nil); return
            }
            guard case .success(let value) = result, value as? Bool == true else {
                replyHandler(ConnectionRequest.reply(id: request.id), nil); self.invalidate(); return
            }
            view.callAsyncJavaScript("return typeof window.webkit?.messageHandlers?.connection === 'undefined';",
                                     arguments: [:], in: message.frameInfo, in: .page) { [weak self] result in
                guard let self, self.valid(message, controller: userContentController, token: token) else {
                    replyHandler(ConnectionRequest.reply(id: request.id, error: .init(.documentChanged)), nil); return
                }
                guard case .success(let value) = result, value as? Bool == true else {
                    replyHandler(ConnectionRequest.reply(id: request.id), nil); self.invalidate(); return
                }
                self.admitting = false; self.admitted = true; self.admittedFrame = message.frameInfo
                self.setEventSubscription()
                self.respond(request, message: message, controller: userContentController, token: token, replyHandler: replyHandler)
            }
        }
    }

    private func respond(_ request: ConnectionRequest, message: WKScriptMessage,
                         controller: WKUserContentController, token: UUID,
                         replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
        if let outcome = ledger.recorded(request) { replyHandler(outcome.reply(id: request.id), nil); return }
        let cancelling = request.command == "client.cancelAttempt"
        guard request.isMutation ? (cancelling ? cancellations < 1 : mutations < 1) : reads < 8 else {
            replyHandler(ConnectionRequest.reply(id: request.id, error: .init(.busy)), nil); return
        }
        if !request.isMutation { reads += 1 }
        else if cancelling { cancellations += 1 }
        else { mutations += 1 }
        Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                if !request.isMutation { self.reads -= 1 }
                else if cancelling { self.cancellations -= 1 }
                else { self.mutations -= 1 }
            }
            guard self.valid(message, controller: controller, token: token) else {
                replyHandler(ConnectionRequest.reply(id: request.id, error: .init(.documentChanged)), nil); return
            }
            let reply = await self.dispatch(request, token: token)
            // Never send a stale completion into a replacement document.
            guard self.valid(message, controller: controller, token: token) else { return }
            replyHandler(reply, nil)
        }
    }

    private func dispatch(_ request: ConnectionRequest, token: UUID) async -> String {
        guard live, admitted, generation == token, let app else {
            return ConnectionRequest.reply(id: request.id, error: .init(.documentChanged))
        }
        switch request.command {
        case "bridge.hello":
            return ConnectionRequest.reply(id: request.id, result: ConnectionClientProjection.hello(app: app, catalog: catalog))
        case "client.status":
            return ConnectionRequest.reply(id: request.id, result: ConnectionClientProjection.snapshot(app: app, catalog: catalog))
        default: break
        }
        guard app.connectionRole == .client, app.clientLane == .managed else { return ConnectionRequest.reply(id: request.id) }
        let model = app.homeInvite
        switch request.command {
        case "client.savedComputers":
            return ConnectionRequest.reply(id: request.id, result: ConnectionClientProjection.savedComputers(model, catalog: catalog))
        case "client.health":
            // A read observes native cadence only. It cannot retry, prompt,
            // reconnect, replace the originating action or declare Connected.
            return ConnectionRequest.reply(id: request.id, result: ConnectionClientProjection.health(model, now: Date()))
        case "client.connectSaved", "client.removeSaved":
            catalog.update(model.savedHomes)
            guard let ref = request.args["computerRef"]?.string, catalog.resolve(ref, in: model) != nil else {
                return ConnectionRequest.reply(id: request.id, error: .init(.stateChanged))
            }
            return ConnectionRequest.reply(id: request.id) // native consent is 3b-2b
        case "client.retrySave": break
        default:
            // Includes web Cancel and Retry: native target/default-Cancel
            // confirmations land in 3b-2b. No unsafe no-sheet shortcut.
            return ConnectionRequest.reply(id: request.id)
        }
        switch ledger.begin(request) {
        case .replay(let outcome): return outcome.reply(id: request.id)
        case .start: break
        }
        guard !model.busy, !model.bridgeHasConfirmation, ledger.reservePrompt(for: request.id) else {
            let outcome = ConnectionOutcome.failure(.init(.busy))
            ledger.finish(request.id, outcome: outcome); return outcome.reply(id: request.id)
        }
        guard model.bridgeCanRetrySave else {
            let outcome = ConnectionOutcome.failure(.init(model.needsReplacementConsent || model.saveFailure == .identityChanged ? .unavailable : .stateChanged,
                                                          recovery: .nativeRecovery))
            ledger.finish(request.id, outcome: outcome); return outcome.reply(id: request.id)
        }
        let lifetime = model.lifetime
        mutationLifetime = lifetime
        ledger.markDispatched(request.id)
        return await withCheckedContinuation { continuation in
            pendingMutation = (request.id, continuation)
            let deadline = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .seconds(130)) } catch { return }
                guard let self, let pending = self.pendingMutation, pending.id == request.id else { return }
                self.pendingMutation = nil
                self.ledger.timedOut(request.id)
                pending.reply.resume(returning: ConnectionOutcome.failure(.init(.outcomeUnknown,
                    effect: .unknown, recovery: .explicitRetry)).reply(id: request.id))
            }
            Task { @MainActor [self] in
                let settlement = await model.bridgeRetrySave()
                deadline.cancel()
                let outcome: ConnectionOutcome
                switch settlement {
                case .saved: outcome = .result(ConnectionClientProjection.savedAction(model, catalog: catalog))
                case .refused: outcome = .failure(.init(.stateChanged))
                case .changed: outcome = .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .explicitRetry))
                case .failed(let failure): outcome = .failure(ConnectionClientProjection.saveError(failure))
                }
                ledger.finish(request.id, outcome: outcome)
                mutationLifetime = nil
                guard let pending = pendingMutation, pending.id == request.id else { return }
                pendingMutation = nil
                guard live, admitted, generation == token else { return }
                pending.reply.resume(returning: outcome.reply(id: request.id))
            }
        }
    }

    private func setEventSubscription() {
        observations.removeAll()
        guard live, admitted, wantsEvents, let app else { return }
        // objectWillChange precedes @Published writes; coalescing yields before
        // projection, so a delivered snapshot contains the settled new values.
        app.homeInvite.objectWillChange.sink { [weak self] in self?.scheduleEvent() }.store(in: &observations)
        app.managedJoin.objectWillChange.sink { [weak self] in self?.scheduleEvent() }.store(in: &observations)
        app.objectWillChange.sink { [weak self] in self?.scheduleEvent() }.store(in: &observations)
        scheduleEvent() // every subscription starts with a full native snapshot
    }
    private func scheduleEvent() {
        guard live, admitted, wantsEvents else { return }
        eventDirty = true
        guard !eventScheduled, !eventInFlight else { return }
        eventScheduled = true
        let token = generation
        Task { @MainActor [weak self] in
            await Task.yield()
            guard let self else { return }
            self.eventScheduled = false
            guard self.live, self.admitted, self.generation == token, self.wantsEvents else { return }
            self.deliverEvent(token: token)
        }
    }
    private func deliverEvent(token: UUID) {
        guard live, admitted, generation == token, wantsEvents, let app, let view, let frame = admittedFrame,
              app.connectionRole == openingRole, view.url?.absoluteString == ConnectionAssets.entry,
              frame.isMainFrame, frame.request.url?.absoluteString == ConnectionAssets.entry,
              frame.securityOrigin.protocol == "happyranch-shell", frame.securityOrigin.host == "connection", frame.securityOrigin.port == 0,
              eventSequence < 9007199254740991 else { return }
        eventDirty = false; eventInFlight = true
        // Re-probe the admitted named world immediately before delivery. Values
        // are structured arguments, never interpolated into executable source.
        view.callAsyncJavaScript(ConnectionScripts.probe, arguments: [:], in: frame, in: Self.world) { [weak self] result in
            guard let self else { return }
            guard self.live, self.admitted, self.generation == token, self.wantsEvents,
                  self.view === view, app.connectionRole == self.openingRole,
                  view.url?.absoluteString == ConnectionAssets.entry else { self.eventInFlight = false; return }
            guard case .success(let value) = result, value as? Bool == true else { self.invalidate(); return }
            self.eventSequence += 1
            let event: [String: Any] = ["version": 1, "sequence": self.eventSequence,
                "observedAt": ConnectionClientProjection.timestamp(Date()),
                "snapshot": ConnectionClientProjection.snapshot(app: app, catalog: self.catalog)]
            guard let data = try? JSONSerialization.data(withJSONObject: event, options: [.sortedKeys]), data.count <= 32768 else {
                self.invalidate(); return
            }
            view.callAsyncJavaScript(ConnectionScripts.event, arguments: ["data": String(decoding: data, as: UTF8.self)],
                                     in: frame, in: Self.world) { [weak self] result in
                guard let self, self.live, self.generation == token else { return }
                self.eventInFlight = false
                if case .failure = result { self.invalidate(); return }
                if self.eventDirty { self.scheduleEvent() }
            }
        }
    }
}
