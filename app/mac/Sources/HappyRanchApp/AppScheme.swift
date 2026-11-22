import AppKit
import WebKit
import CoreFoundation
import HappyRanchSupervisor

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

/// Main-frame app loader. All WK task callbacks are serialized on MainActor;
/// stop removes ownership before cancelling upstream, so late bytes are ignored.
@MainActor
final class AppScheme: NSObject, WKURLSchemeHandler, WKScriptMessageHandlerWithReply {
    static let csp = "default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    let generation = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    let remote: Bool
    private let assets: ConnectionAssets
    private let transport: any AppAPITransport
    private weak var view: WKWebView?
    private struct Owner {
        let requestID: String
        let task: any WKURLSchemeTask
        var worker: Task<Void, Never>?
        var upstream: AppAPITransfer?
    }
    private var owners: [ObjectIdentifier: Owner] = [:]
    private var cancelled: Set<String> = []
    private var admission: Task<Void, Error>?
    private var state = "target_starting"
    private var hostContract: [String: Int]?
    private var incompatibleSide = "host"
    private let promptURL: String?
    private let accessUnsaved: Bool
    private let configPolicy: [(String, [String])]
    /// Optional diagnostic observer receives only closed lifecycle facts, no URL,
    /// headers or bytes. The commissioned real-app qualification uses this seam.
    var observe: ((String, String) -> Void)?

    init(root: URL, transport: any AppAPITransport, remote: Bool, policyURL: URL? = nil, promptURL: String? = nil, accessUnsaved: Bool = false) throws {
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
        for owner in owners.values { owner.worker?.cancel(); owner.upstream?.cancel() }
        owners.removeAll(); admission?.cancel(); admission = nil
        view?.configuration.userContentController.removeScriptMessageHandler(forName: "appTransport", contentWorld: .page)
        view = nil
    }

    func webView(_ webView: WKWebView, start urlSchemeTask: any WKURLSchemeTask) {
        let key = ObjectIdentifier(urlSchemeTask)
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
            owners[key] = Owner(requestID: id, task: urlSchemeTask)
            owners[key]?.worker = Task { [weak self] in
                guard let self else { return }
                await self.forward(key, request: urlSchemeTask.request, route: route, method: method, headers: headers, bootstrap: bootstrap)
            }
        } catch { urlSchemeTask.didFailWithError(AppAPIError.unavailable) }
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: any WKURLSchemeTask) {
        stop(ObjectIdentifier(urlSchemeTask))
    }
    private func stop(_ key: ObjectIdentifier) {
        guard let owner = owners.removeValue(forKey: key) else { return }
        owner.worker?.cancel(); owner.upstream?.cancel(); observe?("native_stop", owner.requestID)
    }

    private func checkAdmission() async throws {
        if let admission { try await admission.value; return }
        let transfer = transport.appRequest(method: "GET", target: "/api/v1/health", headers: [:], body: Data())
        let task = Task { [weak self] in
            let deadline = Task { try await Task.sleep(for: .seconds(10)); transfer.cancel() }
            defer { deadline.cancel() }
            var data = Data(), status = 0
            for try await event in transfer.events {
                switch event {
                case .response(let code, _): status = code
                case .bytes(let chunk): data.append(chunk); if data.count > 65536 { transfer.cancel(); throw AppAPIError.invalidResponse }
                }
            }
            guard let self else { throw AppAPIError.cancelled }
            guard status == 200, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let contract = object["api_contract"] as? [String: Any],
                  let current = Self.integer(contract["current"]), let minimum = Self.integer(contract["min_client"]) else {
                self.hostContract = nil; self.state = "incompatible"; throw AppAPIError.unavailable
            }
            self.hostContract = ["current": current, "min_client": minimum]
            guard current >= 1, minimum <= 1 else {
                self.incompatibleSide = minimum > 1 ? "app" : "host"
                self.state = "incompatible"; throw AppAPIError.unavailable
            }
            if (object["update_busy"] as? [String: Any])?["busy"] as? Bool == true {
                self.state = "busy"; throw AppAPIError.unavailable
            }
            self.state = "ready"
            self.emitStatus()
        }
        admission = task
        do { try await task.value }
        catch { admission = nil; if state == "target_starting" { state = "unreachable" }; emitStatus(); throw error }
    }

    private func forward(_ key: ObjectIdentifier, request: URLRequest, route: AppSchemeRoute,
                         method: String, headers: [String: String], bootstrap: Bool) async {
        var began = false
        defer { owners.removeValue(forKey: key) }
        do {
            try await checkAdmission()
            try Task.checkCancellation()
            guard let owner = owners[key] else { return }
            if bootstrap {
                let bytes = Data(#"{"token":"hr_native_session","user":"founder","role":"admin"}"#.utf8)
                respond(owner.task, status: 200, headers: ["content-type": "application/json"])
                owner.task.didReceive(bytes); owner.task.didFinish(); return
            }
            let body = try Self.body(request)
            guard owners[key] != nil else { return }
            let transfer = transport.appRequest(method: method, target: route.target, headers: headers, body: body)
            owners[key]?.upstream = transfer
            for try await event in transfer.events {
                try Task.checkCancellation()
                guard let owner = owners[key] else { transfer.cancel(); return }
                switch event {
                case .response(let status, let fields):
                    guard !began else { throw AppAPIError.invalidResponse }
                    respond(owner.task, status: status, headers: fields); began = true
                    if status == 200 && fields["content-type"]?.lowercased().hasPrefix("text/event-stream") == true {
                        observe?("native_stream_open", owner.requestID)
                    }
                case .bytes(let data):
                    guard began else { throw AppAPIError.invalidResponse }
                    owner.task.didReceive(data); observe?("native_write", owner.requestID)
                }
            }
            guard let owner = owners[key], began else { return }
            owner.task.didFinish(); observe?("native_finish", owner.requestID)
        } catch {
            owners[key]?.upstream?.cancel()
            guard let owner = owners[key] else { return }
            if state == "ready" { state = "unreachable"; admission = nil; emitStatus() }
            if began { owner.task.didFailWithError(AppAPIError.unavailable) }
            else { reply(owner.task, status: 503, code: state == "busy" ? "busy" : state == "incompatible" ? "incompatible" : "target_unreachable") }
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
    private func respond(_ task: any WKURLSchemeTask, status: Int, headers: [String: String]) {
        var fields = headers
        fields["Content-Security-Policy"] = Self.csp; fields["X-Content-Type-Options"] = "nosniff"; fields["Cache-Control"] = "no-store"
        task.didReceive(HTTPURLResponse(url: task.request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: fields)!)
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
              (try? AppSchemeRoute(url)) != nil, let item = message.body as? [String: String] else {
            replyHandler(nil, "unavailable"); return
        }
        if item == ["command": "app.status"] { replyHandler(statusJSON(), nil); return }
        if item["command"] == "cancel", item.count == 2, let id = item["id"], UUID(uuidString: id) != nil {
            if let key = owners.first(where: { $0.value.requestID == id })?.key { stop(key) }
            else if cancelled.count < 256 { cancelled.insert(id) }
            replyHandler("{}", nil); return
        }
        replyHandler(nil, "unavailable")
    }
    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue == Double(number.intValue) else { return nil }
        return number.intValue
    }
    private func emitStatus() {
        view?.callAsyncJavaScript("window.__hrAppState?.(data);", arguments: ["data": statusJSON()], in: nil, in: .page, completionHandler: nil)
    }
    private func statusJSON() -> String {
        let zh = Locale.preferredLanguages.first?.hasPrefix("zh") == true
        let object: [String: Any] = ["schema": 2, "generation": generation,
            "target": ["kind": remote ? "remote" : "local", "id": remote ? generation : "local",
                       "name": remote ? (zh ? "远程电脑" : "Remote computer") : (zh ? "此电脑" : "This computer")],
            "state": state, "incompatible": state == "incompatible" ? ["side": incompatibleSide, "host_contract": hostContract as Any? ?? NSNull(), "client_contract": 1, "min_daemon": 1] as [String: Any] : NSNull(),
            "local": ["supported": false, "mode": "unsupported", "running": remote ? "unknown" : "running", "prompt_base_url": remote ? NSNull() : (promptURL as Any? ?? NSNull())],
            "candidate": NSNull(), "access_unsaved": accessUnsaved,
            "locale": ["current": zh ? "zh-CN" : "en", "persisted": true]]
        return String(decoding: (try? JSONSerialization.data(withJSONObject: object)) ?? Data(), as: UTF8.self)
    }
}
