import WebKit
import XCTest
import HappyRanchSupervisor
@testable import HappyRanchApp

// Reuse the existing packaged-asset/transport fixture, with no real daemon or port.
extension AppSchemeSwitchTests {
    @MainActor
    func testLocalReadmissionPreservesMainOwnersAndRejectsStaleReconnect() throws {
        let (root, scheme, view, transport) = try fixture()
        defer {
            scheme.close()
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        }
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        let generation = scheme.generation, url = view.url
        let first = scheme.localServiceWillChange(starting: true)
        let second = scheme.localServiceWillChange(starting: true)
        let ready = try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":1,"min_client":1}}"#.utf8))
        let incompatible = try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":2,"min_client":2}}"#.utf8))
        scheme.localServiceDidChange(transport: transport, promptURL: nil, health: ready, revision: first)
        let pending = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertEqual(pending["state"] as? String, "target_starting")
        scheme.localServiceDidChange(transport: transport, promptURL: nil, health: incompatible, revision: second)
        let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertEqual(status["state"] as? String, "incompatible")
        XCTAssertEqual((status["incompatible"] as? [String: Any])?["side"] as? String, "app")
        XCTAssertEqual(scheme.generation, generation)
        XCTAssertEqual(view.url, url)
        XCTAssertNil(scheme.switchRefusal, "Restart admission must retain clean Main owners")
    }

    @MainActor
    func testLocalAdmissionCannotChangeRemoteAppStateC5C6() throws {
        let (root, scheme, _, transport) = try fixture()
        defer {
            scheme.close()
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        }
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        XCTAssertNil(scheme.selectTransport(id: "saved-host", transport: transport, remote: true))
        let before = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? NSDictionary)
        let generation = scheme.generation
        let revision = scheme.localServiceWillChange(starting: true)
        let incompatible = try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":2,"min_client":2}}"#.utf8))
        scheme.localServiceDidChange(transport: transport, promptURL: nil, health: incompatible, revision: revision)
        let after = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? NSDictionary)
        XCTAssertEqual(before, after)
        XCTAssertEqual(scheme.generation, generation)
        XCTAssertEqual(MacLocalReadmission(health: incompatible).json["state"] as? String, "incompatible")
    }
    @MainActor
    func testNativeRestartConsentDefaultsToCancelAndRetainsMain() async throws {
        _ = NSApplication.shared
        let (root, scheme, view, _) = try fixture()
        defer {
            scheme.close()
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.contentView = view
        window.makeKeyAndOrderFront(nil)
        defer { window.orderOut(nil) }
        let app = AppDelegate(); app.bindMainScheme(scheme)
        var answer: Bool?
        let task = Task { @MainActor in answer = await app.confirmLocalRestart() }
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(2))
        while window.attachedSheet == nil, answer == nil, clock.now < deadline {
            try await clock.sleep(for: .milliseconds(5))
        }
        let sheet = try XCTUnwrap(window.attachedSheet)
        func buttons(_ view: NSView) -> [NSButton] {
            (view as? NSButton).map { [$0] } ?? view.subviews.flatMap { buttons($0) }
        }
        let choices = sheet.contentView.map { buttons($0) } ?? []
        XCTAssertEqual(choices.filter { $0.keyEquivalent == "\r" }.count, 1)
        XCTAssertTrue(choices.contains { ["Cancel", "取消"].contains($0.title) && $0.keyEquivalent == "\r" })
        window.endSheet(sheet, returnCode: .alertFirstButtonReturn)
        await task.value
        XCTAssertEqual(answer, false)
        XCTAssertTrue(window.isVisible)
        XCTAssertTrue(window.contentView === view)
    }

}
