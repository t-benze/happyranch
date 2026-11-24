import AppKit
import CoreFoundation
import WebKit
import HappyRanchSupervisor

/// Bounded native diagnostics; never retain the relay token or error userInfo.
struct AppSchemeAdmissionDiagnostic: Sendable, CustomStringConvertible {
    var contextRead = "not-attempted"
    var bind = "not-attempted"
    var readiness = "not-attempted"
    var pageFacts = "not-attempted"
    var contextResult = "not-attempted"
    var bindResult = "not-attempted"
    var diagnosticsResult = "not-attempted"
    static func resultType(_ raw: Any?) -> String {
        guard let raw else { return "nil" }
        if raw is NSNull { return "null" }
        if raw is String { return "string" }
        if raw is NSDictionary { return "dictionary" }
        if let number = raw as? NSNumber {
            return CFGetTypeID(number) == CFBooleanGetTypeID() ? "bool" : "number"
        }
        return "other"
    }
    mutating func recordPageFacts(_ raw: Any?) {
        let facts = raw as? [String: String] ?? [:]
        let types = ["undefined", "object", "function", "boolean", "number", "string", "symbol", "bigint", "threw"]
        let fields: [(String, [String])] = [
            ("bootstrap", ["missing", "entered", "top-frame-check", "refused-top-frame", "origin-check", "refused-origin",
                           "native-handler", "context-random", "context-define", "facade-define", "fetch-install", "complete", "threw"]),
            ("initialOrigin", ["missing", "not-read", "app", "null", "other", "threw"]),
            ("initialTop", ["missing", "not-read", "true", "false", "threw"]),
            ("origin", ["app", "null", "other", "threw"]),
            ("protocol", ["app", "other", "threw"]), ("host", ["app", "other", "threw"]),
            ("top", ["true", "false", "threw"]), ("secure", ["true", "false", "undefined", "threw"]),
            ("webkit", types), ("handlers", types), ("appTransport", types), ("postMessage", types),
            ("crypto", types), ("random", types), ("readyState", ["loading", "interactive", "complete", "threw"])
        ]
        // Do not retain arbitrary page strings (including a forged marker/token).
        pageFacts = fields.map { key, allowed in
            let value = facts[key] ?? "invalid"
            return "\(key)=\(allowed.contains(value) ? value : "invalid")"
        }.joined(separator: ", ")
    }
    var description: String {
        "context=\(contextRead), bind=\(bind), readiness=\(readiness), nativeResults={context=\(contextResult), bind=\(bindResult), diagnostics=\(diagnosticsResult)}, page={\(pageFacts)}"
    }
}

struct AppSchemeLoadFailure: Error, Sendable, CustomStringConvertible {
    enum Stage: String, Sendable {
        case navigationProvisional = "navigation-provisional"
        case navigationCommit = "navigation-commit"
        case relayContextRead = "relay-context-read"
        case bind
        case documentIdentityCheck = "document-identity-check"
        case readiness
        case cancellationFence = "cancellation-fence"
        case initialization
        case contentProcessTermination = "content-process-termination"
    }
    let stage: Stage
    let admission: AppSchemeAdmissionDiagnostic
    let errorDomain: String?
    let errorCode: Int?
    let errorDescription: String?

    init(stage: Stage, error: Error? = nil, admission: AppSchemeAdmissionDiagnostic = .init()) {
        self.stage = stage; self.admission = admission
        let native = error.map { $0 as NSError }
        errorDomain = native.map { Self.safeText($0.domain) }
        errorCode = native?.code
        errorDescription = native.map { Self.safeText($0.localizedDescription) }
    }
    // Error descriptions can embed URLs. Omit their authority/query entirely,
    // redact known credential forms, and bound the result before retaining it.
    private static func safeText(_ text: String) -> String {
        let redacted = DiagnosticsRedactor.redact(text).replacingOccurrences(
            of: #"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"<>]+"#, with: "[URL]", options: .regularExpression)
        return String(redacted.prefix(1024))
    }
    var description: String {
        "stage=\(stage.rawValue), \(admission), NSError(domain=\(errorDomain ?? "none"), code=\(errorCode.map(String.init) ?? "none"), description=\(errorDescription ?? "none"))"
    }
}

struct AppSchemeRoute {
    static let origin = "happyranch-app://app"
    let path: String
    let target: String
    init(_ url: URL) throws {
        guard url.absoluteString.utf8.count <= 16384, url.absoluteString.hasPrefix(Self.origin + "/"),
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "happyranch-app", parts.host == "app", parts.port == nil,
              parts.user == nil, parts.password == nil, parts.fragment == nil,
              let decoded = parts.percentEncodedPath.removingPercentEncoding,
              decoded.hasPrefix("/"), !decoded.contains("%"), !decoded.contains("\\"), !decoded.contains("//"),
              !decoded.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              !decoded.split(separator: "/").contains(where: { $0 == "." || $0 == ".." }),
              url.absoluteString.utf8.allSatisfy({ $0 >= 32 && $0 < 127 }) else { throw AppAPIError.unavailable }
        path = decoded
        target = parts.percentEncodedPath + (parts.percentEncodedQuery.map { "?" + $0 } ?? "")
    }
}

/// Current-document owner evidence. Invalid input removes clearance; losing a
/// document with unresolved saves permanently retains uncertainty in this view.
struct AppSchemeOwnerSummary {
    private(set) var blocking: String?
    private(set) var counts: [String: Int64]?
    private var unresolved = false
    private var uncertain = false

    mutating func accept(_ raw: String) throws {
        blocking = nil; self.counts = nil
        let value = try ConnectionJSON.parse(raw, limit: 1024)
        guard let object = value.object, Set(object.keys) == ["schema", "blocking", "counts"],
              object["schema"] == .integer(2), let counts = object["counts"]?.object,
              Set(counts.keys) == ["dirty", "failed", "pending", "unknown"],
              counts.values.allSatisfy({ $0.integer.map { (0...255).contains($0) } == true }) else {
            throw ConnectionRefusal.invalid
        }
        let held = counts["pending"]!.integer! > 0 || counts["unknown"]!.integer! > 0
        let level = held ? "hold" : counts["dirty"]!.integer! > 0 || counts["failed"]!.integer! > 0 ? "prompt" : "none"
        guard object["blocking"] == .string(level) else { throw ConnectionRefusal.invalid }
        // No native lifecycle request is offered in MAC-1, so unsolicited gate
        // decisions cannot authorize an effect and are refused above.
        blocking = level; self.counts = counts.mapValues { $0.integer! }; unresolved = held
    }
    mutating func retire(nonGETPending: Bool) {
        uncertain = uncertain || unresolved || nonGETPending
        blocking = nil; counts = nil
    }
    var allowsNavigation: Bool { !uncertain && blocking == "none" }
    var switchRefusal: String? {
        guard !uncertain, let counts else { return "owner_save_unknown" }
        if counts["unknown", default: 0] > 0 { return "owner_save_unknown" }
        if counts["pending", default: 0] > 0 { return "owner_save_pending" }
        if blocking != "none" { return "owner_edits_pending" }
        return nil
    }
}

/// Closed lifecycle facts; header/body/context values never enter this record.
struct AppSchemeStreamDiagnostic {
    let requestID: String
    let headerNames: [String]
    let contentLengthPresent: Bool
    let transferEncodingPresent: Bool
    let expectedContentLength: Int64
    var bytesDelivered = 0
    var didFinish = false
    var finishOnMainThread = false
    var finishTaskCancelled = false
    var taskStopped = false

    init(requestID: String, response: HTTPURLResponse) {
        self.requestID = requestID
        let names = response.allHeaderFields.keys.compactMap { ($0 as? String)?.lowercased() }
        let allowed = AppAPIHeaders.response.union(["content-length", "transfer-encoding", "content-security-policy",
                                                    "x-content-type-options", "cache-control"])
        headerNames = Set(names.map { allowed.contains($0) ? $0 : "other" }).sorted()
        contentLengthPresent = names.contains("content-length")
        transferEncodingPresent = names.contains("transfer-encoding")
        expectedContentLength = response.expectedContentLength
    }
    var fields: [String: Any] {
        ["requestID": requestID, "headerNames": headerNames, "contentLengthPresent": contentLengthPresent,
         "transferEncodingPresent": transferEncodingPresent, "expectedContentLength": expectedContentLength,
         "bytesDelivered": bytesDelivered, "didFinish": didFinish, "finishOnMainThread": finishOnMainThread,
         "finishActor": didFinish ? "main" : "not-called", "finishTaskCancelled": finishTaskCancelled,
         "taskStopped": taskStopped]
    }
}

/// Main-frame app loader. All WK task callbacks are serialized on MainActor;
/// stop removes ownership before cancelling upstream, so late bytes are ignored.
@MainActor
final class AppScheme: NSObject, WKURLSchemeHandler, WKScriptMessageHandlerWithReply {
    static let csp = "default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    private(set) var generation = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    private(set) var remote: Bool
    struct Target {
        let id: String
        let transport: any AppAPITransport
        let remote: Bool
        let health: AppAPIHealth
        let accessUnsaved: Bool
    }
    var switchCandidate: (() throws -> Target)?
    var disconnectSelected: (() -> Bool)?
    var actionResult: (() -> [String: Any])?
    weak var connectionApp: AppDelegate?
    var statusChanged: ((String) -> Void)?
    private var connectionCommands: ConnectionWindow?
    private var subscriptions: Set<String> = []
    private var targetID: String?
    private var candidate: [String: Any]?
    var candidateSnapshot: (() -> [String: Any]?)?
    private let assets: ConnectionAssets
    private var transport: (any AppAPITransport)?
    private weak var view: WKWebView?
    private struct Owner {
        let requestID: String
        let generation: String
        let task: any WKURLSchemeTask
        var worker: Task<Void, Never>?
        var upstream: AppAPITransfer?
        var responseSent = false
        var bytesDelivered = 0
        var dataCallbacks = 0
        var completionClaimed = false
    }
    private var owners: [ObjectIdentifier: Owner] = [:]
    private var cancelled: Set<String> = []
    private var admission: Task<Void, Error>?
    private var readinessRevision = UUID()
    private var documentOwners = AppSchemeOwnerSummary()
    private var document = UUID()
    private var documentContext: String?
    private var documentURL: URL?
    private(set) var documentResponse: HTTPURLResponse?
    private var hasDocument = false
    private var state = "target_starting"
    private var hostContract: [String: Int]?
    private var incompatibleSide = "host"
    private(set) var documentAdmission = AppSchemeAdmissionDiagnostic()
    private var promptURL: String?
    private var accessUnsaved: Bool
    private let configPolicy: [(String, [String])]
    /// Optional diagnostic observer receives lifecycle IDs and closed document
    /// path classes, never raw URLs, headers or bytes. Used by qualification.
    var observe: ((String, String) -> Void)?
    var observeLifecycle: (([String: Any]) -> Void)?
    private var completedOwners: [(ObjectIdentifier, String)] = []
    // Only the existing qualification observer enables capture; emitted on failure.
    private(set) var streamDiagnostics: [AppSchemeStreamDiagnostic] = []

    private func updateStream(_ id: String, _ change: (inout AppSchemeStreamDiagnostic) -> Void) {
        if let index = streamDiagnostics.firstIndex(where: { $0.requestID == id }) { change(&streamDiagnostics[index]) }
    }

    private func lifecycle(_ key: ObjectIdentifier, _ stage: String) {
        guard let owner = owners[key] else { return }
        observeLifecycle?(["requestID": owner.requestID, "stage": stage, "responseSent": owner.responseSent,
            "bytesDelivered": owner.bytesDelivered, "dataCallbacks": owner.dataCallbacks,
            "completionClaimed": owner.completionClaimed, "mainThread": Thread.isMainThread,
            "taskCancelled": Task.isCancelled])
    }

    /// Claim completion before entering WebKit; no subsequent path may complete
    /// this owner again. Stop removes ownership before cancelling its worker.
    private func claimCompletion(_ key: ObjectIdentifier) -> Owner? {
        guard let owner = owners[key], !owner.completionClaimed else {
            observeLifecycle?(["stage": "completion-refused"]); return nil
        }
        owners[key]?.completionClaimed = true
        lifecycle(key, "completion-claimed")
        return owner
    }

    init(root: URL, transport: (any AppAPITransport)?, remote: Bool, policyURL: URL? = nil, promptURL: String? = nil, accessUnsaved: Bool = false) throws {
        assets = try ConnectionAssets(root: root)
        guard let policy = policyURL ?? Bundle.main.resourceURL?.appendingPathComponent("remote-client-config-policy.v1.json"),
              let object = try JSONSerialization.jsonObject(with: Data(contentsOf: policy)) as? [String: Any],
              object["version"] as? Int == 1, let entries = object["entries"] as? [[String: String]], !entries.isEmpty else { throw AppAPIError.unavailable }
        configPolicy = try entries.map { entry in
            guard Set(entry.keys) == ["method", "path_template", "class"], entry["class"] == "config",
                  let method = entry["method"], ["POST", "PUT", "PATCH", "DELETE"].contains(method),
                  let path = entry["path_template"], path.hasPrefix("/api/v1/") else { throw AppAPIError.unavailable }
            return (method, path.split(separator: "/").map(String.init))
        }
        self.transport = transport; self.remote = remote; self.promptURL = promptURL; self.accessUnsaved = accessUnsaved
        super.init()
        targetID = transport == nil ? nil : (remote ? generation : promptURL.map { $0.replacingOccurrences(of: "/api/v1", with: "") } ?? "local")
        if transport == nil { state = "no_target" }
    }

    func configure(_ configuration: WKWebViewConfiguration) {
        configuration.setURLSchemeHandler(self, forURLScheme: "happyranch-app")
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "appTransport")
        configuration.userContentController.addUserScript(WKUserScript(source: AppSchemeScripts.page,
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
    }

    func attach(_ view: WKWebView, load url: URL) {
        self.view = view
        // Independent resource boundary in addition to CSP/navigation policy.
        let rules: [[String: Any]] = [
            ["trigger": ["url-filter": ".*"], "action": ["type": "block"]],
            ["trigger": ["url-filter": "^happyranch-app://app/"], "action": ["type": "ignore-previous-rules"]],
            ["trigger": ["url-filter": "^blob:happyranch-app://app/"], "action": ["type": "ignore-previous-rules"]],
            ["trigger": ["url-filter": "^data:", "resource-type": ["image"]], "action": ["type": "ignore-previous-rules"]]
        ]
        guard let json = try? JSONSerialization.data(withJSONObject: rules), let text = String(data: json, encoding: .utf8) else { return }
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "happyranch.app.transport.v1", encodedContentRuleList: text) { [weak self, weak view] list, _ in
            MainActor.assumeIsolated {
                guard let self, let view, self.view === view, let list else { return }
                view.configuration.userContentController.add(list)
                view.load(URLRequest(url: url))
            }
        }
    }

    func close() {
        documentDidStart()
        for key in Array(owners.keys) { stop(key, closing: true) }
        admission?.cancel(); admission = nil
        view?.configuration.userContentController.removeScriptMessageHandler(forName: "appTransport", contentWorld: .page)
        view = nil
    }

    /// Voluntary replacement must not discard protected owners or an active
    /// native mutation. Forced document loss still cancels transfers separately.
    var allowsNavigation: Bool {
        !hasDocument || (documentOwners.allowsNavigation && !nonGETPending)
    }
    private var nonGETPending: Bool {
        owners.values.contains { $0.generation == generation && ($0.task.request.httpMethod ?? "GET") != "GET" }
    }
    var switchRefusal: String? { nonGETPending ? "non_get_pending" : documentOwners.switchRefusal }
    var hasLocalTarget: Bool { targetID != nil && !remote }

    /// No suspension between admission, selection and generation swap. Owner
    /// evidence belongs to Main and survives this transition.
    @discardableResult
    func commitSwitch() -> String? {
        if let reason = switchRefusal { return reason }
        guard let switchCandidate else { return "target_starting" }
        let next: Target
        do { next = try switchCandidate() } catch { return "target_starting" }
        transport = next.transport; remote = next.remote; targetID = next.id
        accessUnsaved = next.accessUnsaved; hostContract = next.health.contract
        incompatibleSide = next.health.incompatibleSide; state = next.health.state
        candidate = nil
        advanceGeneration()
        return nil
    }

    /// Retained DIY/local native producers explicitly select a fresh transport.
    /// This is never called by candidate readiness or a SwiftUI metadata update.
    @discardableResult
    func selectTransport(id: String, transport: any AppAPITransport, remote: Bool, promptURL: String? = nil) -> String? {
        if targetID == id { return nil }
        if let reason = switchRefusal { return reason }
        self.transport = transport; self.remote = remote; targetID = id
        self.promptURL = remote ? nil : promptURL
        hostContract = nil; state = "target_starting"
        advanceGeneration()
        return nil
    }

    @discardableResult
    func disconnect() -> String? {
        if let reason = switchRefusal { return reason }
        guard disconnectSelected?() == true else { return "busy" }
        transport = nil; targetID = nil
        state = "no_target"; hostContract = nil
        advanceGeneration()
        return nil
    }

    private func advanceGeneration() {
        let old = generation
        generation = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        readinessRevision = UUID(); admission?.cancel(); admission = nil
        // Remove custody before cancelling or calling WebKit. An old worker
        // cannot complete, publish readiness, or finish a stream successfully.
        for key in Array(owners.keys) {
            guard let owner = owners[key], owner.generation == old else { continue }
            owners.removeValue(forKey: key)
            owner.worker?.cancel(); owner.upstream?.cancel()
            owner.task.didFailWithError(AppAPIError.cancelled)
        }
        cancelled.removeAll()
        emitStatus()
    }

    func updateCandidate(_ value: [String: Any]?, accessUnsaved: Bool) {
        let previous = candidate.flatMap { try? JSONSerialization.data(withJSONObject: $0, options: .sortedKeys) }
        let next = value.flatMap { try? JSONSerialization.data(withJSONObject: $0, options: .sortedKeys) }
        guard previous != next || self.accessUnsaved != accessUnsaved else { return }
        candidate = value; self.accessUnsaved = accessUnsaved
        emitStatus()
    }
    func receiveOwnerSummary(_ raw: String) throws { try documentOwners.accept(raw) }
    func documentDidStart(url: URL? = nil) {
        statusChanged?("{}") // Keep unsaved access until a confirmed state says otherwise.
        subscriptions.removeAll()
        connectionCommands?.invalidate(showNotice: false); connectionCommands = nil
        documentURL = url; documentResponse = nil
        documentContext = nil
        document = UUID(); hasDocument = true
        documentOwners.retire(nonGETPending: nonGETPending)
        admission?.cancel(); admission = nil
    }
    func admitDocument() async throws {
        let expected = document
        var diagnostic = AppSchemeAdmissionDiagnostic()
        var stage = AppSchemeLoadFailure.Stage.cancellationFence
        defer { documentAdmission = diagnostic }
        do {
            guard let view else { throw AppAPIError.cancelled }
            stage = .relayContextRead; diagnostic.contextRead = "in-flight"
            diagnostic.contextResult = "in-flight"
            documentAdmission = diagnostic
            // contentWorld selects the value-returning async overload. The
            // callback overload's second `in` label returns Void without a handler.
            let raw: Any? = try await view.callAsyncJavaScript("return window.__hrAppContext;", arguments: [:], in: nil, contentWorld: .page)
            diagnostic.contextResult = AppSchemeAdmissionDiagnostic.resultType(raw)
            diagnostic.contextRead = raw is String ? "string-unchecked" : "missing-or-non-string"
            stage = .documentIdentityCheck
            guard document == expected, self.view === view else { throw AppAPIError.cancelled }
            stage = .relayContextRead
            guard let context = raw as? String,
                  context.range(of: "^[0-9a-f]{32}$", options: .regularExpression) != nil else { throw AppAPIError.cancelled }
            diagnostic.contextRead = "valid"
            documentContext = context
            stage = .bind; diagnostic.bind = "in-flight"
            diagnostic.bindResult = "in-flight"
            documentAdmission = diagnostic
            let bound: Any? = try await view.callAsyncJavaScript("return window.__hrAppAdmit?.(context);",
                arguments: ["context": context], in: nil, contentWorld: .page)
            diagnostic.bindResult = AppSchemeAdmissionDiagnostic.resultType(bound)
            diagnostic.bind = (bound as? Bool).map { $0 ? "true" : "false" } ?? "missing-or-non-boolean"
            stage = .documentIdentityCheck
            guard document == expected, self.view === view else { throw AppAPIError.cancelled }
            stage = .bind
            guard bound as? Bool == true else { throw AppAPIError.cancelled }
            stage = .readiness; diagnostic.readiness = "in-flight"
            documentAdmission = diagnostic
            if transport != nil { try await checkAdmission(refresh: true) }
            diagnostic.readiness = state
            stage = .documentIdentityCheck
            guard document == expected, self.view === view else { throw AppAPIError.cancelled }
            statusChanged?(statusJSON())
        } catch {
            if diagnostic.contextRead == "in-flight" { diagnostic.contextRead = "threw"; diagnostic.contextResult = "threw" }
            if diagnostic.bind == "in-flight" { diagnostic.bind = "threw"; diagnostic.bindResult = "threw" }
            if stage == .relayContextRead, let view, document == expected {
                diagnostic.pageFacts = "in-flight"
                diagnostic.diagnosticsResult = "in-flight"
                do {
                    let facts: Any? = try await view.callAsyncJavaScript(AppSchemeScripts.diagnostics, arguments: [:], in: nil, contentWorld: .page)
                    diagnostic.diagnosticsResult = AppSchemeAdmissionDiagnostic.resultType(facts)
                    if document == expected, self.view === view { diagnostic.recordPageFacts(facts) }
                    else { diagnostic.pageFacts = "document-changed"; stage = .documentIdentityCheck }
                } catch {
                    diagnostic.pageFacts = "read-threw"
                    diagnostic.diagnosticsResult = "threw"
                    if document != expected || self.view !== view { stage = .documentIdentityCheck }
                }
            }
            if stage == .readiness {
                diagnostic.readiness = state
                if error is CancellationError || (error as? AppAPIError) == .cancelled { stage = .cancellationFence }
            }
            throw AppSchemeLoadFailure(stage: stage, error: error, admission: diagnostic)
        }
    }

    /// A post-admission readiness observation changes status and future send
    /// admission, never the document, owner evidence or existing transfers.
    func updateReadiness(_ health: AppAPIHealth) {
        hostContract = health.contract; incompatibleSide = health.incompatibleSide
        guard state != health.state else { return }
        state = health.state; readinessRevision = UUID()
        admission?.cancel(); admission = nil
        emitStatus()
    }
    func selectedUnavailable() {
        guard targetID != nil, state != "unreachable" else { return }
        state = "unreachable"; readinessRevision = UUID()
        admission?.cancel(); admission = nil; emitStatus()
    }

    func webView(_ webView: WKWebView, start urlSchemeTask: any WKURLSchemeTask) {
        let key = ObjectIdentifier(urlSchemeTask)
        completedOwners.removeAll { $0.0 == key }
        do {
            guard webView === view, let url = urlSchemeTask.request.url else { throw AppAPIError.unavailable }
            let route = try AppSchemeRoute(url), method = urlSchemeTask.request.httpMethod ?? "GET"
            if !route.path.hasPrefix("/api/") && route.path != "/api" {
                guard method == "GET" || method == "HEAD" else { reply(urlSchemeTask, status: 405, code: "method_not_allowed"); return }
                let name = String(route.path.dropFirst())
                guard !route.path.hasSuffix("/") || route.path == "/" else { throw AppAPIError.unavailable }
                // Unknown routes use the pinned index, never a directory read.
                let selected = assets.resources[name] == nil ? "index.html" : name
                let data = try assets.asset(selected)
                let mime = Self.mime(selected)
                respond(urlSchemeTask, status: 200, headers: ["content-type": mime])
                if method != "HEAD" { urlSchemeTask.didReceive(data) }
                if selected == "index.html" {
                    observe?("native_document_served", ["/", "/onboarding", "/settings"].contains(route.path) ? route.path : "other")
                }
                urlSchemeTask.didFinish(); return
            }
            guard route.path.hasPrefix("/api/v1/"),
                  ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].contains(method) else {
                reply(urlSchemeTask, status: 404, code: "not_found"); return
            }
            let bootstrapPath = route.path == "/api/v1/auth/bootstrap" || route.path == "/api/v1/auth/bootstrap/"
            let bootstrap = bootstrapPath && method == "GET"
            if bootstrapPath && !bootstrap {
                reply(urlSchemeTask, status: 405, code: "method_not_allowed"); return
            }
            let headers = urlSchemeTask.request.allHTTPHeaderFields ?? [:]
            guard headers.reduce(0, { $0 + $1.key.utf8.count + $1.value.utf8.count }) <= 65536 else {
                reply(urlSchemeTask, status: 431, code: "headers_too_large"); return
            }
            if !bootstrap && urlSchemeTask.request.value(forHTTPHeaderField: "X-HappyRanch-Target") != generation {
                reply(urlSchemeTask, status: 409, code: "target_changed"); return
            }
            guard let id = urlSchemeTask.request.value(forHTTPHeaderField: "X-HappyRanch-Request-ID"), UUID(uuidString: id) != nil,
                  !owners.values.contains(where: { $0.requestID == id }), owners.count < 32 else {
                reply(urlSchemeTask, status: 429, code: "too_many_requests"); return
            }
            if !["GET", "HEAD"].contains(method) && urlSchemeTask.request.value(forHTTPHeaderField: "X-HappyRanch-Body-Bytes") != "1" {
                reply(urlSchemeTask, status: 415, code: "byte_body_required"); return
            }
            // Reuse Linux's native read-only config boundary; no remote policy expansion.
            if remote && remoteConfig(method, route.path) {
                reply(urlSchemeTask, status: 403, code: "remote_read_only"); return
            }
            guard cancelled.remove(id) == nil else { throw AppAPIError.cancelled }
            if !["GET", "HEAD"].contains(method) && owners.values.filter({ !["GET", "HEAD"].contains($0.task.request.httpMethod ?? "GET") }).count >= 2 {
                reply(urlSchemeTask, status: 429, code: "too_many_requests"); return
            }
            guard transport != nil else { reply(urlSchemeTask, status: 503, code: "no_target"); return }
            owners[key] = Owner(requestID: id, generation: generation, task: urlSchemeTask)
            lifecycle(key, "start")
            owners[key]?.worker = Task { [weak self] in
                guard let self else { return }
                await self.forward(key, request: urlSchemeTask.request, route: route, method: method, headers: headers, bootstrap: bootstrap)
            }
        } catch { urlSchemeTask.didFailWithError(AppAPIError.unavailable) }
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: any WKURLSchemeTask) {
        stop(ObjectIdentifier(urlSchemeTask))
    }
    private func stop(_ key: ObjectIdentifier, closing: Bool = false) {
        guard let owner = owners[key] else {
            if let id = completedOwners.last(where: { $0.0 == key })?.1 {
                observeLifecycle?(["requestID": id, "stage": "stop-after-completion"])
            }
            return
        }
        lifecycle(key, closing ? "close" : "stop")
        owners.removeValue(forKey: key)
        updateStream(owner.requestID) { $0.taskStopped = true }
        owner.worker?.cancel(); owner.upstream?.cancel(); observe?(closing ? "native_close" : "native_stop", owner.requestID)
    }

    private func checkAdmission(refresh: Bool = false) async throws {
        guard let transport else { throw AppAPIError.unavailable }
        if refresh, state == "ready" { admission = nil }
        if let admission { try await admission.value; return }
        let expected = document
        let expectedReadiness = readinessRevision
        let expectedGeneration = generation
        let task = Task { [weak self] in
            guard let self else { throw AppAPIError.cancelled }
            let health = try await AppAPIHealth.read(from: transport)
            try Task.checkCancellation()
            guard self.document == expected, self.readinessRevision == expectedReadiness,
                  self.generation == expectedGeneration else { throw AppAPIError.cancelled }
            self.hostContract = health.contract
            self.incompatibleSide = health.incompatibleSide
            self.state = health.state
            self.emitStatus()
            try health.requireReady()
        }
        admission = task
        do { try await task.value }
        catch {
            guard document == expected, readinessRevision == expectedReadiness else { throw error }
            admission = nil
            if state == "target_starting" || state == "ready" { state = "unreachable" }
            emitStatus(); throw error
        }
    }

    private func forward(_ key: ObjectIdentifier, request: URLRequest, route: AppSchemeRoute,
                         method: String, headers: [String: String], bootstrap: Bool) async {
        guard let initialOwner = owners[key], initialOwner.generation == generation else { return }
        let expectedGeneration = initialOwner.generation
        let selectedTransport = transport
        var began = false
        var forwarded = false
        defer {
            lifecycle(key, "worker-return")
            let id = owners[key]?.requestID
            if observeLifecycle != nil, let owner = owners[key] {
                if completedOwners.count == 32 { completedOwners.removeFirst() }
                completedOwners.append((key, owner.requestID))
            }
            if owners[key]?.generation == expectedGeneration { owners.removeValue(forKey: key) }
            if let id { observeLifecycle?(["requestID": id, "stage": "owner-retired"]) }
        }
        do {
            try Task.checkCancellation()
            try await checkAdmission()
            try Task.checkCancellation()
            guard let owner = owners[key] else { return }
            if bootstrap {
                let bytes = Data(#"{"token":"hr_native_session","user":"founder","role":"admin"}"#.utf8)
                owners[key]?.responseSent = true; lifecycle(key, "response-enter")
                respond(owner.task, status: 200, headers: ["content-type": "application/json"])
                lifecycle(key, "response-return")
                owners[key]?.bytesDelivered += bytes.count; owners[key]?.dataCallbacks += 1
                lifecycle(key, "data-enter"); owner.task.didReceive(bytes); lifecycle(key, "data-return")
                guard let completing = claimCompletion(key) else { return }
                lifecycle(key, "finish-enter"); completing.task.didFinish(); lifecycle(key, "finish-return"); return
            }
            let body = try Self.body(request)
            guard owners[key] != nil else { return }
            forwarded = true
            guard let selectedTransport, generation == expectedGeneration else { throw AppAPIError.cancelled }
            let transfer = selectedTransport.appRequest(method: method, target: route.target, headers: headers, body: body)
            owners[key]?.upstream = transfer
            for try await event in transfer.events {
                try Task.checkCancellation()
                guard let owner = owners[key] else { transfer.cancel(); return }
                switch event {
                case .response(let status, let fields):
                    guard !began else { throw AppAPIError.invalidResponse }
                    owners[key]?.responseSent = true; lifecycle(key, "response-enter")
                    let response = respond(owner.task, status: status, headers: fields); began = true
                    lifecycle(key, "response-return")
                    if status == 200 && fields["content-type"]?.lowercased().hasPrefix("text/event-stream") == true {
                        if observe != nil {
                            if streamDiagnostics.count == 32 { streamDiagnostics.removeFirst() }
                            streamDiagnostics.append(AppSchemeStreamDiagnostic(requestID: owner.requestID, response: response))
                        }
                        observe?("native_stream_open", owner.requestID)
                    }
                case .bytes(let data):
                    guard began else { throw AppAPIError.invalidResponse }
                    owners[key]?.bytesDelivered += data.count; owners[key]?.dataCallbacks += 1
                    lifecycle(key, "data-enter")
                    owner.task.didReceive(data)
                    lifecycle(key, "data-return")
                    updateStream(owner.requestID) { $0.bytesDelivered += data.count }
                    observe?("native_write", owner.requestID)
                }
            }
            guard began else { throw AppAPIError.invalidResponse }
            guard let owner = claimCompletion(key) else { return }
            lifecycle(key, "finish-enter")
            owner.task.didFinish()
            lifecycle(key, "finish-return")
            updateStream(owner.requestID) {
                $0.didFinish = true; $0.finishOnMainThread = Thread.isMainThread; $0.finishTaskCancelled = Task.isCancelled
            }
            observe?("native_finish", owner.requestID)
        } catch {
            owners[key]?.upstream?.cancel()
            guard let owner = claimCompletion(key) else { return }
            if state == "ready", !(error is CancellationError), (error as? AppAPIError) != .cancelled {
                state = "unreachable"; admission = nil; emitStatus()
            }
            lifecycle(key, "failure-enter")
            if began || (forwarded && (error as? AppAPIError) != .notSent) { owner.task.didFailWithError(AppAPIError.unavailable) }
            else { reply(owner.task, status: 503, code: state == "busy" ? "busy" : state == "incompatible" ? "incompatible" : "target_unreachable") }
            lifecycle(key, "failure-return")
        }
    }

    private static func body(_ request: URLRequest) throws -> Data {
        if let body = request.httpBody { guard body.count <= 67108864 else { throw AppAPIError.unavailable }; return body }
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var data = Data(), buffer = [UInt8](repeating: 0, count: 65536)
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count == 0 { return data }
            guard count > 0, data.count + count <= 67108864 else { throw AppAPIError.unavailable }
            data.append(contentsOf: buffer.prefix(count))
        }
        throw AppAPIError.unavailable
    }
    @discardableResult
    private func respond(_ task: any WKURLSchemeTask, status: Int, headers: [String: String]) -> HTTPURLResponse {
        var fields = headers
        fields["Content-Security-Policy"] = Self.csp; fields["X-Content-Type-Options"] = "nosniff"; fields["Cache-Control"] = "no-store"
        let response = HTTPURLResponse(url: task.request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: fields)!
        // Keep the producer's status for the current navigation: WebKit's
        // non-HTTP URLResponse reconstruction does not retain that subclass.
        if response.url == documentURL { documentResponse = response }
        task.didReceive(response)
        return response
    }
    private func reply(_ task: any WKURLSchemeTask, status: Int, code: String) {
        respond(task, status: status, headers: ["content-type": "application/json"])
        let detail: [String: String] = code == "incompatible" ? ["code": code, "side": incompatibleSide] : ["code": code]
        task.didReceive((try? JSONSerialization.data(withJSONObject: ["detail": detail])) ?? Data()); task.didFinish()
    }
    private static func mime(_ name: String) -> String {
        switch (name as NSString).pathExtension {
        case "html": return "text/html; charset=utf-8"
        case "js", "mjs": return "text/javascript; charset=utf-8"
        case "css": return "text/css; charset=utf-8"
        case "svg": return "image/svg+xml"
        case "woff2": return "font/woff2"
        case "woff": return "font/woff"
        case "png": return "image/png"
        case "ico": return "image/x-icon"
        default: return "application/octet-stream"
        }
    }
    private func remoteConfig(_ method: String, _ path: String) -> Bool {
        let parts = path.split(separator: "/").map(String.init)
        return configPolicy.contains { verb, template in
            verb == method && parts.count == template.count && zip(parts, template).allSatisfy { actual, expected in
                actual == expected || (!actual.isEmpty && expected.hasPrefix("{") && expected.hasSuffix("}"))
            }
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
        guard message.webView === view, message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.protocol == "happyranch-app", message.frameInfo.securityOrigin.host == "app",
              message.frameInfo.securityOrigin.port == 0, let url = message.frameInfo.request.url,
              (try? AppSchemeRoute(url)) != nil, let item = message.body as? [String: Any],
              let context = documentContext, item["context"] as? String == context else {
            replyHandler(nil, "unavailable"); return
        }
        if item["command"] as? String == "app.ownersChanged" {
            do {
                // Parsing and storage are synchronous on MainActor, before ACK.
                // Invalid current-document input cannot retain clean clearance.
                try receiveOwnerSummary(item.count == 3 ? item["data"] as? String ?? "" : "")
                replyHandler("{}", nil)
            } catch { replyHandler(nil, #"{"code":"invalidRequest","effect":"none","recovery":"refresh"}"#) }
            return
        }
        guard let item = message.body as? [String: String] else { replyHandler(nil, "unavailable"); return }
        if item == ["context": context, "command": "app.status"] { replyHandler(statusJSON(), nil); return }
        if item["command"] == "subscribe", Set(item.keys) == ["context", "command", "event", "active"],
           let event = item["event"], ["app.stateChanged", "app.dialogRequested", "client.stateChanged"].contains(event),
           let active = item["active"], ["0", "1"].contains(active) {
            if active == "1" { subscriptions.insert(event) } else { subscriptions.remove(event) }
            if event == "client.stateChanged" { mainConnectionCommands()?.subscribeMain(active == "1") }
            replyHandler("{}", nil); return
        }
        if let command = item["command"], ["client.commitSwitch", "client.disconnect"].contains(command) {
            guard Set(item.keys) == ["context", "command", "data"], item["data"] == "{}" else {
                replyHandler(nil, #"{"code":"invalidRequest","effect":"none","recovery":"refresh"}"#); return
            }
            let reason = command == "client.commitSwitch" ? commitSwitch() : disconnect()
            if let reason {
                replyHandler(nil, String(decoding: (try? JSONSerialization.data(withJSONObject:
                    ["code": reason, "effect": "none", "recovery": "explicitRetry"])) ?? Data(), as: UTF8.self))
            } else if let actionResult {
                replyHandler(String(decoding: (try? JSONSerialization.data(withJSONObject: actionResult())) ?? Data(), as: UTF8.self), nil)
            } else { replyHandler(nil, #"{"code":"outcomeUnknown","effect":"unknown","recovery":"refresh"}"#) }
            return
        }
        if let command = item["command"], ConnectionRequest.commands.contains(command), command != "client.disconnect",
           Set(item.keys) == ["context", "command", "data"], let data = item["data"],
           let commands = mainConnectionCommands() {
            commands.mainCall(command: command, data: data, reply: replyHandler); return
        }
        if item["command"] == "cancel", item.count == 3, let id = item["id"], UUID(uuidString: id) != nil {
            if let key = owners.first(where: { $0.value.requestID == id })?.key { stop(key) }
            else if cancelled.count < 256 { cancelled.insert(id) }
            replyHandler("{}", nil); return
        }
        replyHandler(nil, "unavailable")
    }
    private func emitStatus() {
        statusChanged?(statusJSON())
        view?.callAsyncJavaScript("window.__hrAppState?.(data);", arguments: ["data": statusJSON()], in: nil, in: .page, completionHandler: nil)
        observe?("native_state_changed", generation)
    }

    private func mainConnectionCommands() -> ConnectionWindow? {
        if let connectionCommands { return connectionCommands }
        guard let app = connectionApp, let view, let context = documentContext else { return nil }
        let identity = document
        let commands = ConnectionWindow(app: app, mainHost: view, currentDocument: { [weak self, weak view] in
            guard let self, let view else { return false }
            return self.document == identity && self.documentContext == context && self.view === view
        }, event: { [weak self] raw in
            guard let self, self.document == identity, self.documentContext == context else { return }
            self.emitEvent("client.stateChanged", raw: raw)
        })
        connectionCommands = commands
        return commands
    }

    /// Entry only: no queue, acknowledgement, target selection or effect.
    @discardableResult
    func requestDialog(_ dialog: String) -> Bool {
        guard ["settings", "connection"].contains(dialog), documentContext != nil,
              view != nil, subscriptions.contains("app.dialogRequested") else { return false }
        let raw = String(decoding: (try? JSONSerialization.data(withJSONObject: ["schema": 2, "dialog": dialog])) ?? Data(), as: UTF8.self)
        emitEvent("app.dialogRequested", raw: raw)
        return true
    }

    private func emitEvent(_ name: String, raw: String) {
        guard let context = documentContext, subscriptions.contains(name) else { return }
        view?.callAsyncJavaScript("window.__hrAppEvent?.(context, name, data);",
            arguments: ["context": context, "name": name, "data": raw], in: nil, in: .page, completionHandler: nil)
    }
    func statusJSON() -> String {
        let zh = Locale.preferredLanguages.first?.hasPrefix("zh") == true
        let currentCandidate: [String: Any]?
        if let candidateSnapshot { currentCandidate = candidateSnapshot() } else { currentCandidate = candidate }
        let object: [String: Any] = ["schema": 2, "generation": generation,
            "target": targetID.map { ["kind": remote ? "remote" : "local", "id": $0,
                       "name": remote ? (zh ? "远程电脑" : "Remote computer") : (zh ? "此电脑" : "This computer")] } as Any? ?? NSNull(),
            "state": state, "incompatible": state == "incompatible" ? ["side": incompatibleSide, "host_contract": hostContract as Any? ?? NSNull(), "client_contract": 1, "min_daemon": 1] as [String: Any] : NSNull(),
            "local": ["supported": false, "mode": "unsupported", "running": remote ? "unknown" : "running", "prompt_base_url": remote ? NSNull() : (promptURL as Any? ?? NSNull())],
            "candidate": currentCandidate as Any? ?? NSNull(), "access_unsaved": accessUnsaved,
            "locale": ["current": zh ? "zh-CN" : "en", "persisted": true]]
        return String(decoding: (try? JSONSerialization.data(withJSONObject: object)) ?? Data(), as: UTF8.self)
    }
}
