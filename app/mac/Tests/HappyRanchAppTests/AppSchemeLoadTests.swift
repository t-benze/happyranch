import XCTest
import WebKit
import HappyRanchSupervisor
@testable import HappyRanchApp

private final class SchemeHealthFixture: AppAPITransport, @unchecked Sendable {
    let body: Data
    private let lock = NSLock()
    private var writes = 0
    var mutations: Int { lock.withLock { writes } }
    init(body: Data) { self.body = body }
    func appRequest(method: String, target: String, headers: [String: String], body: Data) -> AppAPITransfer {
        let transfer = AppAPITransfer()
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
