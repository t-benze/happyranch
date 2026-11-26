import AppKit
import WebKit
import XCTest
import HappyRanchSupervisor
@testable import HappyRanchApp

// Reuse the existing packaged-asset/transport fixture, with no real daemon or port.
extension AppSchemeSwitchTests {
    @MainActor
    static func logNativeSheetState(_ stage: String, window: NSWindow? = nil, scheme: AppScheme? = nil) {
        let application = NSApplication.shared
        func identity(_ window: NSWindow?) -> String {
            window.map { String(describing: ObjectIdentifier($0)) } ?? "nil"
        }
        var details = "running=\(application.isRunning) active=\(application.isActive) activationPolicy=\(application.activationPolicy())"
        details += " window=\(identity(window)) localPromptWindow=\(identity(scheme?.localPromptWindow))"
        if let window {
            details += " visible=\(window.isVisible) key=\(window.isKeyWindow) onActiveSpace=\(window.isOnActiveSpace) windowNumber=\(window.windowNumber) occlusionState=\(window.occlusionState.rawValue)"
            details += " screen=\(window.screen.map { "\($0.localizedName) frame=\($0.frame)" } ?? "nil") attachedSheet=\(identity(window.attachedSheet))"
        }
        details += " runLoopMode=\(RunLoop.current.currentMode?.rawValue ?? "nil")"
        // Write directly so a later stalled task cannot leave these buffered.
        let line = "[MAC-3 sheet t=\(Date().timeIntervalSince1970) uptime=\(ProcessInfo.processInfo.systemUptime)] \(stage) \(details)\n"
        FileHandle.standardError.write(Data(line.utf8))
    }

    @MainActor
    static func probeNativeSheetVenue(_ label: String) async {
        // Diagnostic only: never replace the real Main/Restart assertions.
        let probe = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 240),
                             styleMask: [.titled, .closable], backing: .buffered, defer: false)
        probe.makeKeyAndOrderFront(nil)
        probe.displayIfNeeded()
        let alert = NSAlert()
        defer {
            if let sheet = probe.attachedSheet { probe.endSheet(sheet, returnCode: .alertFirstButtonReturn) }
            alert.window.orderOut(nil)
            probe.orderOut(nil)
        }
        Self.logNativeSheetState("\(label) venue-capability probe: before plain NSAlert.beginSheetModal", window: probe)
        alert.beginSheetModal(for: probe) { response in
            Self.logNativeSheetState("\(label) venue-capability probe: completion response=\(response.rawValue)")
        }
        Self.logNativeSheetState("\(label) venue-capability probe: beginSheetModal returned", window: probe)
        let clock = ContinuousClock(), deadline = ContinuousClock().now.advanced(by: .seconds(1))
        while probe.attachedSheet == nil, clock.now < deadline {
            try? await Task.sleep(for: .milliseconds(10))
        }
        Self.logNativeSheetState("\(label) venue-capability probe: attachedWithin1s=\(probe.attachedSheet === alert.window); diagnostic only", window: probe)
    }

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
        let application = NSApplication.shared
        let previousPolicy = application.activationPolicy()
        Self.logNativeSheetState("consent setup: before activation")
        // Unlike the product launch path, SwiftPM does not install AppDelegate
        // as NSApp's delegate. Unbundled executables default to .prohibited.
        let activated = application.setActivationPolicy(.regular)
        application.finishLaunching()
        application.activate(ignoringOtherApps: true)
        Self.logNativeSheetState("consent setup: setActivationPolicy returned \(activated)")
        defer { application.setActivationPolicy(previousPolicy) }
        await Self.probeNativeSheetVenue("consent")
        let (root, scheme, view, _) = try fixture()
        defer {
            scheme.close()
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        view.setFrameSize(window.contentLayoutRect.size)
        window.contentView = view
        window.makeKeyAndOrderFront(nil)
        window.displayIfNeeded()
        defer {
            if let sheet = window.attachedSheet { window.endSheet(sheet, returnCode: .alertFirstButtonReturn) }
            window.orderOut(nil)
        }
        let app = AppDelegate(); app.bindMainScheme(scheme)
        XCTAssertTrue(scheme.localPromptWindow === window)
        XCTAssertTrue(window.isVisible)
        // Register before scheduling the production MainActor task. XCTest's
        // async waiter suspends this test instead of nesting a main-queue run
        // loop, allowing both the task and AppKit's sheet work to drain.
        let presented = XCTNSNotificationExpectation(name: NSWindow.willBeginSheetNotification, object: window)
        var answer: Bool?
        Self.logNativeSheetState("consent: scheduling Task", window: window, scheme: scheme)
        let task = Task { @MainActor in
            Self.logNativeSheetState("consent: Task body entered; calling confirmLocalRestart", window: window, scheme: scheme)
            answer = await app.confirmLocalRestart()
            Self.logNativeSheetState("consent: Task completed answer=\(String(describing: answer))", window: window, scheme: scheme)
        }
        defer { task.cancel() }
        let presentation = await XCTWaiter.fulfillment(of: [presented], timeout: 5)
        Self.logNativeSheetState("consent: waiter=\(presentation) answer=\(String(describing: answer))", window: window, scheme: scheme)
        XCTAssertEqual(presentation, .completed, "Main must announce the real sheet presentation")
        let sheet = try XCTUnwrap(window.attachedSheet, "Restart must present on Main; answer=\(String(describing: answer)), visible=\(window.isVisible)")
        func buttons(_ view: NSView) -> [NSButton] {
            (view as? NSButton).map { [$0] } ?? view.subviews.flatMap { buttons($0) }
        }
        let choices = sheet.contentView.map { buttons($0) } ?? []
        XCTAssertEqual(choices.filter { $0.keyEquivalent == "\r" }.count, 1)
        XCTAssertTrue(choices.contains { ["Cancel", "取消"].contains($0.title) && $0.keyEquivalent == "\r" })
        func labels(_ view: NSView) -> [String] {
            (view as? NSTextField).map { [$0.stringValue] } ?? view.subviews.flatMap { labels($0) }
        }
        let zh = Locale.preferredLanguages.first?.hasPrefix("zh") == true
        let name = Host.current().localizedName ?? (zh ? "此电脑" : "This computer")
        XCTAssertTrue(sheet.contentView.map { labels($0).contains { $0.contains(name) } } == true,
                      "Restart consent must name this computer")
        window.endSheet(sheet, returnCode: .alertFirstButtonReturn)
        await task.value
        XCTAssertEqual(answer, false)
        XCTAssertTrue(window.isVisible)
        XCTAssertTrue(window.contentView === view)
    }

}
