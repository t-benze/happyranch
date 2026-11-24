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
    private var pendingMutations: [String: CheckedContinuation<String, Never>] = [:]
    private var activeSheet: NSAlert?

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
        if let sheet = activeSheet, let parent = sheet.window.sheetParent {
            parent.endSheet(sheet.window, returnCode: .alertFirstButtonReturn)
        }
        for (id, reply) in pendingMutations {
            ledger.timedOut(id)
            reply.resume(returning: ConnectionRequest.reply(id: id,
                error: .init(.documentChanged, effect: .unknown, recovery: .explicitRetry)))
        }
        pendingMutations.removeAll()
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
        guard request.isMutation ? (cancelling || mutations < 1) : reads < 8 else {
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
        default: break
        }
        guard ConnectionClientProjection.backedCommands.contains(request.command) else {
            return ConnectionRequest.reply(id: request.id)
        }
        if request.command == "client.cancelAttempt", let outcome = ledger.pendingCancellation() {
            activeSheet?.window.makeKeyAndOrderFront(nil)
            return outcome.reply(id: request.id)
        }
        if request.command == "client.cancelAttempt", let sheet = activeSheet {
            // Only an app-owned sheet is observable/focusable. It keeps its
            // own Cancel; OS Keychain UI is never inspected or manipulated.
            sheet.window.makeKeyAndOrderFront(nil)
            return ConnectionRequest.reply(id: request.id,
                result: ConnectionClientProjection.savedAction(model, catalog: catalog, effect: "none"))
        }
        let captured = model.lifetime
        switch ledger.begin(request, cancelling: model.bridgeCanCancel ? captured.attempt : nil) {
        case .replay(let outcome): return outcome.reply(id: request.id)
        case .start: break
        }
        return await withCheckedContinuation { continuation in
            pendingMutations[request.id] = continuation
            let deadline = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .seconds(130)) } catch { return }
                guard let self, let reply = self.pendingMutations.removeValue(forKey: request.id) else { return }
                self.ledger.timedOut(request.id)
                reply.resume(returning: ConnectionOutcome.failure(.init(.outcomeUnknown,
                    effect: .unknown, recovery: .explicitRetry)).reply(id: request.id))
            }
            Task { @MainActor [self] in
                let outcome = await clientAction(request, model: model, token: token, context: captured)
                deadline.cancel()
                ledger.finish(request.id, outcome: outcome)
                if !model.busy { mutationLifetime = nil }
                guard let reply = pendingMutations.removeValue(forKey: request.id) else { return }
                reply.resume(returning: outcome.reply(id: request.id))
            }
        }
    }

    private func current(_ token: UUID, _ context: HomeInviteModel.Lifetime, _ model: HomeInviteModel) -> Bool {
        live && admitted && generation == token && app?.connectionRole == openingRole
            && app?.clientLane == .managed && model.lifetime == context
    }

    /// Default Keep/Cancel, no queue. The OS-owned Keychain dialog never owns
    /// this slot: SecItem exposes only its eventual result, not prompt state.
    private func sheet(_ request: ConnectionRequest, title: String, detail: String, action: String,
                       input: NSSecureTextField? = nil) async -> Bool {
        guard activeSheet == nil, let window, window.attachedSheet == nil,
              ledger.reservePrompt(for: request.id) else { return false }
        defer { ledger.releasePrompt(request.id) }
        let alert = NSAlert()
        alert.messageText = title; alert.informativeText = detail
        alert.addButton(withTitle: Self.copy("Keep / Cancel", "保持现状 / 取消"))
        alert.addButton(withTitle: action)
        alert.buttons[0].keyEquivalent = "\r"
        alert.buttons[1].keyEquivalent = ""
        if let input { alert.accessoryView = input }
        activeSheet = alert
        let timeout = Task { @MainActor [weak self, weak alert] in
            do { try await Task.sleep(for: .seconds(120)) } catch { return }
            guard let self, let alert, self.activeSheet === alert, let parent = alert.window.sheetParent else { return }
            parent.endSheet(alert.window, returnCode: .alertFirstButtonReturn)
        }
        let answer: NSApplication.ModalResponse = await withCheckedContinuation { reply in
            alert.beginSheetModal(for: window) { response in reply.resume(returning: response) }
            if let input { alert.window.makeFirstResponder(input) }
        }
        timeout.cancel()
        if activeSheet === alert { activeSheet = nil }
        return answer == .alertSecondButtonReturn
    }

    private func targetDescription(_ trust: SavedHomeTrust?, binding: SavedDeviceBinding? = nil) -> String {
        guard let trust else { return Self.copy("the current computer", "当前电脑") }
        // Native identity details never cross into a bridge payload or log.
        var details = [trust.homeEndpoint, trust.homeID, trust.controlPlaneID, trust.caSHA256]
        if let binding { details.append(binding.stableNodeID) }
        return details.map(ConnectionClientProjection.displayText).joined(separator: "\n")
    }

    private func clientAction(_ request: ConnectionRequest, model: HomeInviteModel, token: UUID,
                              context: HomeInviteModel.Lifetime) async -> ConnectionOutcome {
        guard current(token, context, model) else { return .failure(.init(.stateChanged)) }
        var ownsConfirmation = false
        defer { if ownsConfirmation { model.endConfirmation(for: context) } }
        @MainActor func unchanged() -> Bool { current(token, context, model) }
        @MainActor func action(_ effect: String = "confirmed", cleanup: String = "notApplicable") -> ConnectionOutcome {
            .result(ConnectionClientProjection.savedAction(model, catalog: catalog, effect: effect, localCleanup: cleanup))
        }
        @MainActor func hold() -> Bool {
            guard model.beginConfirmation(for: context) else { return false }
            ownsConfirmation = true
            return true
        }
        switch request.command {
        case "client.cancelAttempt", "client.disconnect":
            let cancelling = request.command == "client.cancelAttempt"
            guard cancelling ? model.bridgeCanCancel : [.connected, .runtimeBusy].contains(model.phase) else { return .failure(.init(.stateChanged)) }
            guard hold() else { return .failure(.init(.busy)) }
            let target = model.target
            let yes = await sheet(request, title: Self.copy("Stop this connection attempt?", "停止此次连接？"),
                detail: targetDescription(target) + "\n" + Self.copy(
                    "This stops only this device's local connection. Saved access remains; Host approval is not revoked. A storage write/readback cannot be interrupted; stopping waits for it to finish.",
                    "仅停止此设备的本地连接。已保存的访问权限会保留，不会撤销主机批准。存储写入和读回不能中断，停止操作会等待其完成。"),
                action: Self.copy("Stop local connection", "停止本地连接"))
            guard unchanged() else { return .failure(.init(.stateChanged)) }
            guard yes else { return action("none") }
            ledger.markDispatched(request.id)
            model.cancel() // invalidates now; storage teardown waits at its safe point
            mutationLifetime = model.lifetime
            let stopped = model.lifetime
            await model.bridgeSettle()
            guard live, generation == token, model.lifetime == stopped else {
                return .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .explicitRetry))
            }
            guard model.phase == .idle, model.removalFailures.isEmpty, model.saveFailure != .deletionFailed else {
                return .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .nativeRecovery))
            }
            return action(cleanup: "confirmed")
        case "client.retrySave":
            guard !model.bridgeHasConfirmation, model.bridgeCanRetrySave else {
                if model.bridgeCanReplace { return .failure(.init(.identityChanged, recovery: .nativeRecovery)) }
                return .failure(.init(model.busy ? .busy : .stateChanged, recovery: .nativeRecovery))
            }
            return await save(request, model: model, replacing: false)
        case "client.removeSaved", "client.connectSaved":
            catalog.update(model.savedHomes)
            guard let ref = request.args["computerRef"]?.string, let id = catalog.resolve(ref, in: model),
                  let target = model.savedHomes.first(where: { $0.id == id }) else { return .failure(.init(.stateChanged)) }
            return await savedTarget(request, model: model, target: target, token: token,
                                     removing: request.command == "client.removeSaved")
        case "client.retry":
            guard !model.busy else { return .failure(.init(.busy)) }
            if let target = model.bridgeRemovalTarget {
                return await savedTarget(request, model: model, target: target, token: token, removing: true)
            }
            if model.bridgeHasUnsavedAccess {
                guard hold(), let target = model.bridgeReplacementTarget else { return .failure(.init(.stateChanged)) }
                let replacing = model.bridgeCanReplace
                let yes = await sheet(request, title: Self.copy(replacing ? "Replace saved access?" : "Retry saving access?",
                    replacing ? "替换已保存的访问权限？" : "重试保存访问权限？"),
                    detail: targetDescription(target.trust, binding: target.binding) + "\n" + Self.copy(
                        replacing ? "Replace only this local item with the freshly approved device. The old Host grant remains revocable on the Host. Failed or unknown deletion is not successful replacement." : "Retry the delivered access in login Keychain. No Host approval or network membership is changed.",
                        replacing ? "仅以刚获批准的设备替换此本地项目。旧主机授权仍可在主机上撤销。删除失败或结果未知不代表替换成功。" : "在登录钥匙串中重试保存已交付的访问权限。不会改变主机批准或网络成员关系。"),
                    action: Self.copy(replacing ? "Replace local access" : "Retry save", replacing ? "替换本地访问权限" : "重试保存"))
                guard unchanged(), model.bridgeReplacementTarget == target,
                      replacing ? model.bridgeCanReplace : model.bridgeCanRetrySave else { return .failure(.init(.stateChanged)) }
                guard yes else { return action("none") }
                model.endConfirmation(for: context); ownsConfirmation = false
                return await save(request, model: model, replacing: replacing)
            }
            if !model.bridgeNeedsNewInvite, let target = model.savedHomes.first(where: { $0.id == model.selectedHomeID }) {
                return await savedTarget(request, model: model, target: target, token: token, removing: false)
            }
            fallthrough
        case "client.acceptInvite":
            guard model.bridgeCanAccept else { return .failure(.init(.busy)) }
            // Discovery is an explicit action here, never a hello/read side effect.
            if !model.discoveryComplete {
                model.prepare(reconnectSingle: false)
                mutationLifetime = model.lifetime
                await model.bridgeSettle()
                guard unchanged(), model.bridgeCanAccept else { return .failure(.init(.stateChanged)) }
            }
            guard hold() else { return .failure(.init(.busy)) }
            let input = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 440, height: 24))
            input.placeholderString = Self.copy("Paste one invite from Linux", "粘贴一份 Linux 邀请")
            input.setAccessibilityLabel(Self.copy("Private invite", "私密邀请"))
            defer { input.stringValue = "" }
            guard await sheet(request, title: Self.copy("Accept a home invite", "接受家庭邀请"),
                detail: Self.copy("Paste explicitly here. Trust an invite only from your home computer. The invite stays in native memory.",
                                  "请在此明确粘贴。仅信任来自您家庭电脑的邀请。邀请仅保留在原生内存中。"),
                action: Self.copy("Validate invite", "验证邀请"), input: input) else { return action("none") }
            guard unchanged() else { return .failure(.init(.stateChanged)) }
            var raw = input.stringValue; input.stringValue = ""
            defer { raw.removeAll() }
            guard raw.utf8.count <= 5500 else { return .failure(.init(.invalidInvite, recovery: .newInviteOnHost)) }
            let parsed: RemoteInvite
            do { parsed = try await Task.detached { [raw] in try RemoteInvite.parse(raw) }.value }
            catch { return .failure(.init((error as? RemoteInviteError) == .time ? .expired : .invalidInvite, recovery: .newInviteOnHost)) }
            guard unchanged() else { return .failure(.init(.stateChanged)) }
            let replacement = model.needsReplacementConsent
            let yes = await sheet(request, title: Self.copy("Connect to this computer?", "连接此电脑？"),
                detail: targetDescription(parsed.trust) + "\n" + Self.copy(
                    replacement ? "Saved access could not be opened. Continue only with fresh Host approval. Obsolete local access is never silently replaced; its old Host grant remains revocable there. Saving uses login Keychain and its existing permission rules." : "Connect and request Host approval. Delivered access will be saved in login Keychain using its existing permission rules. If saving fails, this session may continue, but reopening may need a new invite.",
                    replacement ? "无法打开已保存的访问权限。仅在重新获得主机批准后继续。不会静默替换旧本地访问权限；旧主机授权仍可在主机上撤销。保存使用登录钥匙串及其现有权限规则。" : "连接并请求主机批准。交付的访问权限将按现有权限规则保存在登录钥匙串中。若保存失败，本次会话仍可继续，但重新打开可能需要新邀请。"),
                action: Self.copy("Connect and save access", "连接并保存访问权限"))
            guard unchanged(), model.bridgeCanAccept, model.needsReplacementConsent == replacement else { return .failure(.init(.stateChanged)) }
            guard yes else { return action("none") }
            guard Date() < parsed.expiresAt else { return .failure(.init(.expired, recovery: .newInviteOnHost)) }
            ledger.markDispatched(request.id)
            model.accept(raw, replacementConsent: replacement)
            raw.removeAll(); mutationLifetime = model.lifetime
            let started = model.lifetime
            await model.bridgeSettle()
            return settled(model, context: started, token: token)
        default: return .failure(.init(.unavailable))
        }
    }

    private func savedTarget(_ request: ConnectionRequest, model: HomeInviteModel, target: HomeInviteModel.SavedHome,
                             token: UUID, removing: Bool) async -> ConnectionOutcome {
        let context = model.lifetime
        guard model.bridgeRemovalTarget == nil || model.bridgeRemovalTarget == target else { return .failure(.init(.stateChanged)) }
        guard !model.busy, model.bridgeActiveComputerID == nil || model.bridgeActiveComputerID == target.id,
              removing || model.bridgeCanAccept, model.beginConfirmation(for: context) else { return .failure(.init(.busy)) }
        defer { model.endConfirmation(for: context) }
        let yes = await sheet(request, title: Self.copy(removing ? "Remove local saved access?" : "Connect with saved access?",
            removing ? "移除本地已保存的访问权限？" : "使用已保存的访问权限连接？"),
            detail: targetDescription(target.trust, binding: target.binding) + "\n" + Self.copy(
                removing ? "Stop this local session and erase only this computer's local credential, trust and owned network state. This does not revoke Host approval. Incomplete removal remains visible for explicit retry." : "Reread this exact saved binding and trust from login Keychain, then reconnect without an invite. Keychain permissions are unchanged.",
                removing ? "停止此本地会话，仅删除此电脑的本地凭据、信任和所属网络状态。不会撤销主机批准。移除不完整时会保留提示，需明确重试。" : "从登录钥匙串重新读取此精确绑定和信任，然后无需邀请即可重新连接。钥匙串权限保持不变。"),
            action: Self.copy(removing ? "Remove local access" : "Connect", removing ? "移除本地访问权限" : "连接"))
        guard current(token, context, model), model.savedHomes.contains(target) || model.bridgeRemovalTarget == target else {
            return .failure(.init(.stateChanged))
        }
        guard yes else { return .result(ConnectionClientProjection.savedAction(model, catalog: catalog, effect: "none")) }
        model.selectedHomeID = target.id
        ledger.markDispatched(request.id)
        if removing { model.removeSavedComputer(expected: target) } else { model.reconnect() }
        mutationLifetime = model.lifetime
        let started = model.lifetime
        await model.bridgeSettle()
        if removing {
            guard live, generation == token, model.lifetime == started,
                  model.bridgeRemovalTarget == nil, !model.savedHomes.contains(target), model.removalFailures.isEmpty else {
                return .failure(.init(.removalIncomplete, effect: .unknown, recovery: .nativeRecovery))
            }
            return .result(ConnectionClientProjection.savedAction(model, catalog: catalog, localCleanup: "confirmed"))
        }
        return settled(model, context: started, token: token)
    }

    private func save(_ request: ConnectionRequest, model: HomeInviteModel, replacing: Bool) async -> ConnectionOutcome {
        ledger.markDispatched(request.id)
        mutationLifetime = model.lifetime
        switch await model.bridgeRetrySave(replacing: replacing) {
        case .saved: return .result(ConnectionClientProjection.savedAction(model, catalog: catalog))
        case .refused: return .failure(.init(.stateChanged))
        case .changed: return .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .explicitRetry))
        case .failed(let failure): return .failure(ConnectionClientProjection.saveError(failure))
        }
    }

    private func settled(_ model: HomeInviteModel, context: HomeInviteModel.Lifetime, token: UUID) -> ConnectionOutcome {
        guard current(token, context, model) else { return .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .explicitRetry)) }
        if model.phase == .connected {
            // Connection succeeded; custody is an independent observed dimension.
            // The persistent native banner retains any failed save outcome.
            return .result(ConnectionClientProjection.savedAction(model, catalog: catalog))
        }
        if let failure = model.saveFailure { return .failure(ConnectionClientProjection.saveError(failure)) }
        if case .keychain(let failure) = model.phase { return .failure(ConnectionClientProjection.saveError(failure)) }
        if case .failed(let failure) = model.phase {
            let code: ConnectionError.Code
            switch failure {
            case .malformedInvite: code = .invalidInvite
            case .expiredInvite: code = .expired
            case .identityChanged, .addressChanged: code = .trustMismatch
            case .identityUnavailable: code = .identityChanged
            case .reapprovalRequired, .authorizationIncomplete, .joinDenied: code = .newInviteRequired
            case .approvalDenied: code = .accessDenied
            case .revoked: code = .revokedConfirmed
            case .daemonUnavailable: code = .runtimeUnavailable
            case .cleanupIncomplete: code = .outcomeUnknown
            default: code = .networkUnavailable
            }
            return .failure(.init(code, effect: .unknown, recovery: .explicitRetry))
        }
        return .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .explicitRetry))
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
