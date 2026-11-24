import AppKit
import CryptoKit
import Darwin
import WebKit
import XCTest
@testable import HappyRanchApp

final class MainDialogTests: XCTestCase {
    @MainActor
    private func eventually(_ condition: () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(5)
        while Date() < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return false
    }

    @MainActor
    func testDialogRoutingDropsUnsubscribedAndRetiredRequestsWithoutTargetChanges() async throws {
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        let root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let assets = root.appendingPathComponent("web", isDirectory: true)
        try FileManager.default.createDirectory(at: assets, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let html = Data("<!doctype html><html><body><main>Main</main></body></html>".utf8)
        try html.write(to: assets.appendingPathComponent("index.html"))
        let hash = SHA256.hash(data: html).map { String(format: "%02x", $0) }.joined()
        try JSONSerialization.data(withJSONObject: ["version": 1, "entry": "index.html", "resources": [
            ["path": "index.html", "bytes": html.count, "sha256": hash]]]).write(to: assets.appendingPathComponent("resource-manifest.json"))
        let policy = root.appendingPathComponent("policy.json")
        try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/config","class":"config"}]}"#.utf8).write(to: policy)
        let scheme = try AppScheme(root: assets, transport: nil, remote: false, policyURL: policy)
        defer { scheme.close() }
        let app = AppDelegate(); app.bindMainScheme(scheme)
        let initial = scheme.generation
        XCTAssertFalse(scheme.requestDialog("settings"), "Loading clicks must be dropped")
        var loads = 0
        let owner = WebView(url: URL(string: AppSchemeRoute.origin + "/")!, transport: nil, remote: false,
                            onLoaded: { _ in loads += 1 })
        let coordinator = owner.makeCoordinator(); coordinator.scheme = scheme
        let configuration = WKWebViewConfiguration(); scheme.configure(configuration)
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 760, height: 500), configuration: configuration)
        view.navigationDelegate = coordinator
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
        let loaded = await eventually { loads == 1 }; XCTAssertTrue(loaded, "\(coordinator.callbackURLs)")
        XCTAssertFalse(scheme.requestDialog("connection"), "Admission without subscription does not queue")
        _ = try await view.callAsyncJavaScript("window.dialogs = []; window.states = []; window.off = window.happyranchConnection.on('app.dialogRequested', raw => dialogs.push(JSON.parse(raw))); window.happyranchConnection.on('app.stateChanged', raw => states.push(JSON.parse(raw)));",
            arguments: [:], in: nil, contentWorld: .page)
        let subscribed = await eventually { scheme.requestDialog("settings") }; XCTAssertTrue(subscribed)
        try await Task.sleep(for: .milliseconds(100))
        let received = try await view.callAsyncJavaScript("return JSON.stringify({dialogs, states});", arguments: [:], in: nil, contentWorld: .page) as? String
        let result = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(received).utf8)) as? [String: Any])
        let dialogs = try XCTUnwrap(result["dialogs"] as? [[String: Any]])
        XCTAssertEqual(dialogs.count, 1)
        let first = try XCTUnwrap(dialogs.first)
        XCTAssertEqual(Set(first.keys), ["schema", "dialog"])
        XCTAssertEqual(first["schema"] as? Int, 2); XCTAssertEqual(first["dialog"] as? String, "settings")
        XCTAssertEqual((result["states"] as? [Any])?.count, 0, "Dialog payload cannot enter state listeners")
        XCTAssertFalse(scheme.requestDialog("preferences"))
        XCTAssertEqual(scheme.generation, initial)
        // The same admitted Main can read the existing command projection.
        let snapshot = try await view.callAsyncJavaScript("return await window.happyranchConnection.call('client.status', '{}');",
            arguments: [:], in: nil, contentWorld: .page) as? String
        let client = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(try XCTUnwrap(snapshot).utf8)) as? [String: Any])
        XCTAssertNotNil(client["stage"]); XCTAssertNil(client["protocol"], "Main unwraps the existing v1 result")
        XCTAssertEqual(scheme.generation, initial)
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        view.reload()
        let reloaded = await eventually { loads == 2 }; XCTAssertTrue(reloaded)
        XCTAssertFalse(scheme.requestDialog("connection"), "Replacement Main has no inherited subscriber")
        _ = try await view.callAsyncJavaScript("window.dialogs = []; window.off = window.happyranchConnection.on('app.dialogRequested', raw => dialogs.push(JSON.parse(raw)));",
            arguments: [:], in: nil, contentWorld: .page)
        try await Task.sleep(for: .milliseconds(100))
        let empty = try await view.callAsyncJavaScript("return dialogs.length;", arguments: [:], in: nil, contentWorld: .page) as? Int
        XCTAssertEqual(empty, 0, "Retired/loading clicks are never replayed on subscription")
        let next = await eventually { scheme.requestDialog("connection") }; XCTAssertTrue(next)
        _ = try await view.callAsyncJavaScript("off();", arguments: [:], in: nil, contentWorld: .page)
        let unsubscribed = await eventually { !scheme.requestDialog("settings") }; XCTAssertTrue(unsubscribed)
        scheme.documentDidStart()
        XCTAssertFalse(scheme.requestDialog("settings"))
        XCTAssertEqual(scheme.generation, initial, "Dialog entry never selects a target")
    }

    @MainActor
    func testNativeToolbarEntriesRetryManuallyAndRetainUnsavedWarningAcrossDocumentLoss() throws {
        let controller = MainToolbarController()
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                              styleMask: [.titled], backing: .buffered, defer: false)
        controller.attach(window)
        let toolbar = try XCTUnwrap(window.toolbar)
        func button(_ id: String) -> NSButton? { toolbar.items.first { $0.itemIdentifier.rawValue == id }?.view as? NSButton }
        var requests: [String] = []
        var available = false
        controller.request = { requests.append($0); return available }
        try XCTUnwrap(button("settings")).performClick(nil)
        XCTAssertEqual(requests, ["settings"])
        XCTAssertNotNil(button("dialog-retry"))
        controller.refresh(#"{"state":"ready","target":{"kind":"local"},"candidate":null,"access_unsaved":true}"#)
        XCTAssertEqual(requests, ["settings"], "State refresh cannot replay a dropped entry")
        XCTAssertNotNil(button("access-unsaved"))
        available = true
        try XCTUnwrap(button("dialog-retry")).performClick(nil)
        XCTAssertEqual(requests, ["settings", "settings"])
        XCTAssertNil(button("dialog-retry"))
        try XCTUnwrap(button("access-unsaved")).performClick(nil)
        XCTAssertEqual(requests.last, "connection", "Warning opens the shared existing Retry presentation")
        controller.refresh("{}")
        XCTAssertNotNil(button("access-unsaved"), "Document loss is not a confirmed protected save")
        controller.refresh(#"{"state":"ready","target":{"kind":"local"},"candidate":null,"access_unsaved":false}"#)
        XCTAssertNil(button("access-unsaved"))
    }
}
