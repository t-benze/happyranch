import AppKit
import WebKit
import XCTest
import HappyRanchSupervisor
@testable import HappyRanchApp

// Test-side input for the existing attached-sheet guard, not a presenter.
@MainActor
private final class LocalRestartGuardWindow: NSWindow {
    var existingSheet: NSWindow?
    override var attachedSheet: NSWindow? { existingSheet }
}

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
        // Build92 demonstrated that this SwiftPM venue cannot attach sheets.
        // Exercise the production dialog and mapping without presentation.
        let name = "MAC-3 测试电脑"
        for zh in [false, true] {
            let alert = AppDelegate.localRestartAlert(zh: zh, computerName: name)
            XCTAssertEqual(alert.messageText, zh ? "重新启动 \(name) 上的 HappyRanch？" : "Restart HappyRanch on \(name)?")
            XCTAssertTrue(alert.messageText.contains(name))
            XCTAssertEqual(alert.informativeText, zh
                ? "这会中断此电脑上的本地工作。仅重新启动守护进程，应用和当前对话框将保持打开。不会保证工作自动恢复。"
                : "This interrupts local work on this computer. Only the daemon restarts; the app and current dialog stay open. Work may not resume automatically.")
            XCTAssertEqual(alert.buttons.map { $0.title }, zh ? ["取消", "重新启动"] : ["Cancel", "Restart"])
            XCTAssertEqual(alert.buttons.map { $0.keyEquivalent }, ["\r", ""])
        }
        // The production timeout ends the sheet with alertFirstButtonReturn.
        XCTAssertFalse(AppDelegate.localRestartConfirmed(.alertFirstButtonReturn))
        XCTAssertFalse(AppDelegate.localRestartConfirmed(.abort))
        XCTAssertTrue(AppDelegate.localRestartConfirmed(.alertSecondButtonReturn))
        let app = AppDelegate()
        let missingMain = await app.confirmLocalRestart()
        XCTAssertFalse(missingMain)
        let (root, scheme, view, _) = try fixture()
        defer {
            scheme.close()
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        }
        let window = LocalRestartGuardWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        view.setFrameSize(window.contentLayoutRect.size)
        window.contentView = view
        defer { window.contentView = nil }
        app.bindMainScheme(scheme)
        XCTAssertTrue(scheme.localPromptWindow === window)
        // Model an already-owned sheet without attempting unsupported attachment.
        let existingSheet = NSWindow()
        window.existingSheet = existingSheet
        let alreadyPresenting = await app.confirmLocalRestart()
        XCTAssertFalse(alreadyPresenting)
        XCTAssertTrue(window.attachedSheet === existingSheet)
        XCTAssertTrue(window.contentView === view)
        XCTAssertTrue(scheme.localPromptWindow === window)
    }

}
