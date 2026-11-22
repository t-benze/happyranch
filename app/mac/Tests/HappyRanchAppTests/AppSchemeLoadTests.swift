import XCTest
import WebKit
import HappyRanchSupervisor
@testable import HappyRanchApp

private final class SchemeHealthFixture: AppAPITransport, @unchecked Sendable {
    let body: Data
    private let lock = NSLock()
    private var writes = 0
    private var held: AppAPITransfer?
    var mutations: Int { lock.withLock { writes } }
    var pending: AppAPITransfer? { lock.withLock { held } }
    init(body: Data) { self.body = body }
    func appRequest(method: String, target: String, headers: [String: String], body: Data) -> AppAPITransfer {
        let transfer = AppAPITransfer()
        if method == "POST", target == "/api/v1/mac-qualification/pending" {
            lock.withLock { writes += 1; held = transfer }
            _ = transfer.beginRequest()
            return transfer
        }
        if method == "POST", target == "/api/v1/mac-qualification/lost-reply" {
            lock.withLock { writes += 1 }
            _ = transfer.beginRequest()
            transfer.finish(AppAPIError.unavailable)
            return transfer
        }
        guard method == "GET", target == "/api/v1/health" else {
            transfer.finish(AppAPIError.invalidResponse); return transfer
        }
        transfer.emit(.response(200, ["content-type": "application/json"]))
        transfer.emit(.bytes(self.body)); transfer.finish()
        return transfer
    }
}

final class AppSchemeLoadTests: XCTestCase {
    /// The production navigation delegate, pinned loader and readiness reader
    /// all run; only the upstream health bytes are a synthetic transport input.
    @MainActor
    func testPackagedPageFinishCannotAdmitMissingIncompatibleOrBusyOwner() async throws {
        for (health, ready) in [
            (#"{}"#, false),
            (#"{"api_contract":{"current":0,"min_client":1}}"#, false),
            (#"{"api_contract":{"current":2,"min_client":2}}"#, false),
            (#"{"api_contract":{"current":1,"min_client":1},"update_busy":{"busy":true}}"#, false),
            (#"{"api_contract":{"current":1,"min_client":1},"update_busy":{"busy":true,"unknown":true}}"#, false),
            (#"{"api_contract":{"current":1,"min_client":1},"update_busy":{"busy":false}}"#, true)
        ] {
            let root = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent(UUID().uuidString)
            let assets = root.appendingPathComponent("web")
            try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: root) }
            try Data("hello".utf8).write(to: assets.appendingPathComponent("index.html"))
            let manifest = #"{"version":1,"entry":"index.html","resources":[{"path":"index.html","bytes":5,"sha256":"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}]}"#
            try Data(manifest.utf8).write(to: assets.appendingPathComponent("resource-manifest.json"))
            let policy = root.appendingPathComponent("policy.json")
            try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/orgs/{org}/config","class":"config"}]}"#.utf8).write(to: policy)
            let transport = SchemeHealthFixture(body: Data(health.utf8))
            let selected = URL(string: "http://127.0.0.1:49152/")!
            var loaded: [URL] = [], failed: [URL] = []
            let owner = WebView(url: selected, transport: transport, remote: false,
                onLoaded: { loaded.append($0) }, onFailed: { failed.append($0) })
            let coordinator = owner.makeCoordinator()
            let scheme = try AppScheme(root: assets, transport: transport, remote: false, policyURL: policy)
            coordinator.scheme = scheme
            let config = WKWebViewConfiguration()
            scheme.configure(config)
            let view = WKWebView(frame: .zero, configuration: config)
            view.navigationDelegate = coordinator
            scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
            let deadline = Date().addingTimeInterval(5)
            while loaded.isEmpty && failed.isEmpty && Date() < deadline {
                try await Task.sleep(for: .milliseconds(10))
            }
            XCTAssertEqual(loaded, ready ? [selected] : [], health)
            XCTAssertEqual(failed, ready ? [] : [selected], health)
            if ready {
                // The transport input models a send followed by loss before any
                // response headers. The shipping adapter/handler must fail fetch,
                // never synthesize a pre-forward 503 result.
                let outcome = try await view.callAsyncJavaScript(
                    """
                    try {
                      const response = await fetch('/api/v1/mac-qualification/lost-reply', {
                        method: 'POST', headers: {'X-HappyRanch-Target': generation}, body: '{}'
                      });
                      return response.status;
                    } catch { return 'failed'; }
                    """, arguments: ["generation": scheme.generation], in: nil, in: .page)
                XCTAssertEqual(transport.mutations, 1, "Must reach the post-forward failure input")
                XCTAssertEqual(outcome as? String, "failed", "No headers is not proof of no send")

                // Readiness publication is the same production seam used by
                // WebView.updateNSView after HomeInviteModel's periodic check.
                let context = try await view.callAsyncJavaScript("window.unsentDraft = 'unsent task brief'; window.pendingResult = fetch('/api/v1/mac-qualification/pending', {method:'POST', headers:{'X-HappyRanch-Target':generation}, body:'{}'}).then(r => r.text(), () => 'failed'); return window.__hrAppContext;",
                    arguments: ["generation": scheme.generation], in: nil, in: .page) as? String
                let pendingDeadline = Date().addingTimeInterval(5)
                while transport.pending == nil && Date() < pendingDeadline { try await Task.sleep(for: .milliseconds(10)) }
                let pending = try XCTUnwrap(transport.pending)
                let documentURL = view.url
                for busy in [true, false] {
                    let health = try AppAPIHealth(status: 200, data: Data("{\"api_contract\":{\"current\":1,\"min_client\":1},\"update_busy\":{\"busy\":\(busy),\"unknown\":\(busy)}}".utf8))
                    scheme.updateReadiness(health)
                    let status = try await view.callAsyncJavaScript("return JSON.parse(await window.happyranchConnection.call('app.status', '{}')).state;", arguments: [:], in: nil, in: .page)
                    XCTAssertEqual(status as? String, busy ? "busy" : "ready")
                    let retained = try await view.callAsyncJavaScript("return [window.__hrAppContext, window.unsentDraft];", arguments: [:], in: nil, in: .page) as? [String]
                    XCTAssertEqual(retained, [try XCTUnwrap(context), "unsent task brief"])
                    XCTAssertEqual(view.url, documentURL)
                    XCTAssertFalse(pending.isCancelled, "Busy/ready must not stop an in-flight mutation")
                    XCTAssertEqual(transport.mutations, 2, "Readiness recovery must never replay")
                }
                pending.emit(.response(200, ["content-type": "text/plain"]))
                pending.emit(.bytes(Data("original result".utf8))); pending.finish()
                let result = try await view.callAsyncJavaScript("return await window.pendingResult;", arguments: [:], in: nil, in: .page)
                XCTAssertEqual(result as? String, "original result")

                // Real handler messages carry the sending relay's context;
                // a retired clean summary cannot replace new unknown evidence.
                let clean = #"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#
                let held = #"{"schema":2,"blocking":"hold","counts":{"dirty":0,"failed":0,"pending":1,"unknown":0}}"#
                func push(_ summary: String, context: String) async throws -> String? {
                    try await view.callAsyncJavaScript("try { await window.webkit.messageHandlers.appTransport.postMessage({context, command:'app.ownersChanged', data}); return 'accepted'; } catch { return 'rejected'; }",
                        arguments: ["context": context, "data": summary], in: nil, in: .page) as? String
                }
                func replace(_ path: String) async throws -> String {
                    // Own lifecycle delivery explicitly for the retirement race;
                    // the unknown owner gate intentionally refuses voluntary navigation.
                    view.navigationDelegate = nil
                    view.load(URLRequest(url: URL(string: AppSchemeRoute.origin + path)!))
                    let deadline = Date().addingTimeInterval(5)
                    while (view.isLoading || view.url?.path != path) && Date() < deadline { try await Task.sleep(for: .milliseconds(10)) }
                    XCTAssertEqual(view.url?.path, path)
                    try await scheme.admitDocument()
                    let raw = try await view.callAsyncJavaScript("return window.__hrAppContext;", arguments: [:], in: nil, in: .page)
                    return try XCTUnwrap(raw as? String)
                }
                let old = try XCTUnwrap(context)
                let cleanReceipt = try await push(clean, context: old)
                XCTAssertEqual(cleanReceipt, "accepted")
                XCTAssertTrue(scheme.allowsNavigation)
                scheme.documentDidStart()
                let lateClean = try await push(clean, context: old)
                XCTAssertEqual(lateClean, "rejected")
                XCTAssertFalse(scheme.allowsNavigation, "Late clean cannot replace documentDidStart's unknown evidence")
                let current = try await replace("/settings")
                XCTAssertNotEqual(current, old)
                let currentClean = try await push(clean, context: current)
                XCTAssertEqual(currentClean, "accepted")
                XCTAssertTrue(scheme.allowsNavigation)
                let latePending = try await push(held, context: old)
                XCTAssertEqual(latePending, "rejected")
                XCTAssertTrue(scheme.allowsNavigation, "Foreign evidence must not alter current clean evidence")
                let pendingReceipt = try await push(held, context: current)
                XCTAssertEqual(pendingReceipt, "accepted")
                scheme.documentDidStart()
                let retiredPending = try await push(held, context: current)
                XCTAssertEqual(retiredPending, "rejected")
                let replacement = try await replace("/onboarding")
                let replacementClean = try await push(clean, context: replacement)
                XCTAssertEqual(replacementClean, "accepted")
                XCTAssertFalse(scheme.allowsNavigation, "Retired pending uncertainty survives late pushes and replacement clean evidence")
            }
            scheme.close()
            // A late completion from a document whose scheme was dismantled
            // must not admit/re-admit that selected owner.
            coordinator.scheme = nil
            coordinator.webView(view, didFinish: nil)
            XCTAssertEqual(loaded.count, ready ? 1 : 0)
        }
    }
}
