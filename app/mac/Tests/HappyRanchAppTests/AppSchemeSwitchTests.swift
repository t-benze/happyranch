import XCTest
import WebKit
import Darwin
import HappyRanchSupervisor
@testable import HappyRanchApp

private final class SwitchTransport: AppAPITransport, @unchecked Sendable {
    private let lock = NSLock()
    private var held: [AppAPITransfer] = []
    var count: Int { lock.withLock { held.count } }
    func appRequest(method: String, target: String, headers: [String: String], body: Data) -> AppAPITransfer {
        let transfer = AppAPITransfer()
        transfer.emit(.response(200, ["content-type": target == "/api/v1/events" ? "text/event-stream" : "application/json"]))
        if target == "/api/v1/health" {
            transfer.emit(.bytes(Data(#"{"api_contract":{"current":1,"min_client":1}}"#.utf8)))
            transfer.finish()
        } else {
            _ = transfer.beginRequest()
            lock.withLock { held.append(transfer) }
            transfer.emit(.bytes(Data("data: pending\n\n".utf8)))
        }
        return transfer
    }
}

@MainActor
private final class SwitchSchemeTask: NSObject, WKURLSchemeTask {
    let request: URLRequest
    var finished = false
    var failed = false
    var received = false
    var status: Int?
    var onFailure: (() -> Void)?
    init(method: String, path: String, generation: String) {
        var request = URLRequest(url: URL(string: AppSchemeRoute.origin + path)!)
        request.httpMethod = method
        request.setValue(generation, forHTTPHeaderField: "X-HappyRanch-Target")
        request.setValue(UUID().uuidString, forHTTPHeaderField: "X-HappyRanch-Request-ID")
        if method != "GET" { request.setValue("1", forHTTPHeaderField: "X-HappyRanch-Body-Bytes") }
        self.request = request
    }
    func didReceive(_ response: URLResponse) { received = true; status = (response as? HTTPURLResponse)?.statusCode }
    func didReceive(_ data: Data) {}
    func didFinish() { finished = true }
    func didFailWithError(_ error: Error) { failed = true; onFailure?() }
}

final class AppSchemeSwitchTests: XCTestCase {
    private let clean = #"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#

    @MainActor
    private func fixture() throws -> (URL, AppScheme, WKWebView, SwitchTransport) {
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        let root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try Data("hello".utf8).write(to: root.appendingPathComponent("index.html"))
        try Data(#"{"version":1,"entry":"index.html","resources":[{"path":"index.html","bytes":5,"sha256":"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}]}"#.utf8)
            .write(to: root.appendingPathComponent("resource-manifest.json"))
        // Policy is outside the manifest-pinned asset directory.
        let policy = root.appendingPathExtension("json")
        try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/config","class":"config"}]}"#.utf8).write(to: policy)
        let transport = SwitchTransport()
        let scheme = try AppScheme(root: root, transport: transport, remote: false, policyURL: policy)
        let config = WKWebViewConfiguration()
        scheme.configure(config)
        let view = WKWebView(frame: .zero, configuration: config)
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
        return (root, scheme, view, transport)
    }

    @MainActor
    private func eventually(_ condition: () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(3)
        while !condition(), Date() < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        return condition()
    }

    @MainActor
    func testCommitAndDisconnectRefuseCurrentNonGETAndPendingOrUnknownSaves() async throws {
        let (root, scheme, view, transport) = try fixture()
        defer { scheme.close(); try? FileManager.default.removeItem(at: root); try? FileManager.default.removeItem(at: root.appendingPathExtension("json")) }
        let initial = scheme.generation
        var commits = 0, disconnects = 0
        let health = try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":1,"min_client":1}}"#.utf8))
        scheme.switchCandidate = { commits += 1; return .init(id: "B", transport: transport, remote: true, health: health, accessUnsaved: false) }
        scheme.disconnectSelected = { disconnects += 1; return true }
        XCTAssertEqual(scheme.commitSwitch(), "owner_save_unknown")
        try scheme.receiveOwnerSummary(clean)
        let mutation = SwitchSchemeTask(method: "POST", path: "/api/v1/pending", generation: initial)
        scheme.webView(view, start: mutation)
        let sent = await eventually { transport.count == 1 }
        XCTAssertTrue(sent)
        XCTAssertEqual(scheme.commitSwitch(), "non_get_pending")
        XCTAssertEqual(scheme.disconnect(), "non_get_pending")
        XCTAssertFalse(mutation.failed || mutation.finished)
        scheme.webView(view, stop: mutation)
        for (pending, unknown, reason) in [(1, 0, "owner_save_pending"), (0, 1, "owner_save_unknown")] {
            try scheme.receiveOwnerSummary("{\"schema\":2,\"blocking\":\"hold\",\"counts\":{\"dirty\":0,\"failed\":0,\"pending\":\(pending),\"unknown\":\(unknown)}}")
            XCTAssertEqual(scheme.commitSwitch(), reason)
            XCTAssertEqual(scheme.disconnect(), reason)
        }
        XCTAssertEqual(commits, 0); XCTAssertEqual(disconnects, 0)
        XCTAssertEqual(scheme.generation, initial)
        try scheme.receiveOwnerSummary(clean)
        XCTAssertNil(scheme.commitSwitch())
        XCTAssertEqual(commits, 1)
        XCTAssertNil(scheme.disconnect())
        XCTAssertEqual(disconnects, 1)
        let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertEqual(status["state"] as? String, "no_target")
        XCTAssertTrue(status["target"] is NSNull)
    }

    @MainActor
    func testSwapPrecedesOldStreamErrorAndFullStateEmissionWithoutDocumentReplacement() async throws {
        let (root, scheme, view, transport) = try fixture()
        defer { scheme.close(); try? FileManager.default.removeItem(at: root); try? FileManager.default.removeItem(at: root.appendingPathExtension("json")) }
        try scheme.receiveOwnerSummary(clean)
        let previous = scheme.generation
        let nextTransport = SwitchTransport()
        let stream = SwitchSchemeTask(method: "GET", path: "/api/v1/events", generation: previous)
        scheme.webView(view, start: stream)
        let opened = await eventually { stream.received && transport.count == 1 }
        XCTAssertTrue(opened)
        let candidate: [String: Any] = ["kind": "remote", "id": "B", "name": "Computer B", "phase": "ready_to_switch", "deadline": NSNull()]
        scheme.updateCandidate(candidate, accessUnsaved: false)
        let before = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertEqual((before["candidate"] as? [String: Any])?["id"] as? String, "B")
        var order: [String] = []
        stream.onFailure = {
            let state = (try? JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8))) as? [String: Any]
            XCTAssertEqual((state?["target"] as? [String: Any])?["id"] as? String, "B")
            XCTAssertNotEqual(scheme.generation, previous)
            order.append("stream-error")
        }
        scheme.observe = { event, _ in if event == "native_state_changed" { order.append("state-emission") } }
        let health = try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":1,"min_client":1}}"#.utf8))
        scheme.switchCandidate = { .init(id: "B", transport: nextTransport, remote: true, health: health, accessUnsaved: true) }
        XCTAssertNil(scheme.commitSwitch())
        XCTAssertTrue(stream.failed); XCTAssertFalse(stream.finished)
        XCTAssertEqual(order, ["stream-error", "state-emission"])
        XCTAssertNil(scheme.switchRefusal, "Main owner evidence survives target/generation changes")
        let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertTrue(status["candidate"] is NSNull)
        XCTAssertEqual(status["access_unsaved"] as? Bool, true)
        XCTAssertEqual(Set(status.keys), ["schema", "generation", "target", "state", "incompatible", "local", "candidate", "access_unsaved", "locale"])
        // Same scheme and WKWebView: new-generation traffic reaches the new owner.
        let next = SwitchSchemeTask(method: "GET", path: "/api/v1/events", generation: scheme.generation)
        scheme.webView(view, start: next)
        let forwarded = await eventually { next.received && nextTransport.count == 1 }
        XCTAssertTrue(forwarded)
        XCTAssertEqual(transport.count, 1)
        let stale = SwitchSchemeTask(method: "GET", path: "/api/v1/events", generation: previous)
        scheme.webView(view, start: stale)
        XCTAssertTrue(stale.finished)
        XCTAssertEqual(stale.status, 409)
        XCTAssertEqual(nextTransport.count, 1, "Old generation cannot forward into B")
        scheme.webView(view, stop: next)
    }
}
