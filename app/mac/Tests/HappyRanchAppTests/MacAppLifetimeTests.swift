import AppKit
import CryptoKit
import Darwin
import WebKit
import XCTest
import HappyRanchSupervisor
@testable import HappyRanchApp

/// Headless input backend: replaces only presentation and the session-count
/// observation. Policy, response consumption, relay and final effects ship.
@MainActor
final class LifetimeBackend: MacAppLifetimeBackend {
    let app: AppDelegate
    var count: Int? = 0
    var prompts: [MacAppLifetime.Prompt] = []
    var effects: [MacLifetimeIntent] = []
    var held: [String] = []
    init(_ app: AppDelegate) { self.app = app }
    var lifetimeScheme: AppScheme? { app.lifetimeScheme }
    var lifetimeObservation: MacLifetimeObservation { app.lifetimeObservation }
    var lifetimeRefusal: String? { app.lifetimeRefusal }
    func prepareLifecycleConfirmation(token: UUID, intent: MacLifetimeIntent) {
        if intent == .update { app.applyUpdateGate(runningSessions: count, choice: nil) }
        else { app.appLifetime.offerConfirmation(token: token, kind: .exit) }
    }
    func presentLifecycleConfirmation(_ prompt: MacAppLifetime.Prompt) { prompts.append(prompt) }
    func lifecycleConfirmed(token: UUID, choice: MacAppLifetime.Choice) {
        switch choice {
        case .restart: app.applyUpdateGate(runningSessions: count, choice: .restart)
        case .now: app.applyUpdateGate(runningSessions: count, choice: .now)
        default: app.lifecycleConfirmed(token: token, choice: choice)
        }
    }
    func completeLifecycle(_ intent: MacLifetimeIntent, token: UUID) -> Bool {
        effects.append(intent); return app.completeLifecycle(intent, token: token)
    }
    func lifecycleHeld(_ reason: String) { held.append(reason) }
}

@MainActor
final class LifetimeTermination: MacTerminationDelivery {
    var calls = 0
    var closes = 0
    func completeQuit() { calls += 1 }
}

@MainActor
private final class LifetimeLocalConsent: MacLocalControlBackend {
    var pending: CheckedContinuation<Bool, Never>?
    var localObservation: MacLocalObservation { .init(supported: true, running: "running") }
    var localMutationAvailable: Bool { true }
    var localUpdateObservation: String { "unknown" }
    func reconcileLocalService() async -> Bool { false }
    func confirmLocalRestart() async -> Bool { await withCheckedContinuation { pending = $0 } }
    func performLocalCommand(_ command: String) async throws -> MacLocalReadmission? { throw AppAPIError.unavailable }
}

@MainActor
final class LifetimeFixture {
    static let clean = #"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#
    let root: URL
    let scheme: AppScheme
    let preference: AppLocalePreference
    let view: WKWebView
    let window: NSWindow
    let app = AppDelegate()
    let termination = LifetimeTermination()
    lazy var backend = LifetimeBackend(app)
    init(transport: (any AppAPITransport)? = nil) throws {
        _ = NSApplication.shared
        let physical = try XCTUnwrap(realpath(FileManager.default.temporaryDirectory.path, nil))
        defer { free(physical) }
        root = URL(fileURLWithFileSystemRepresentation: physical, isDirectory: true, relativeTo: nil)
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        let bytes = Data("<!doctype html><html><body><main>Main</main></body></html>".utf8)
        try bytes.write(to: root.appendingPathComponent("index.html"))
        let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        try JSONSerialization.data(withJSONObject: ["version": 1, "entry": "index.html", "resources": [
            ["path": "index.html", "bytes": bytes.count, "sha256": hash]]]).write(to: root.appendingPathComponent("resource-manifest.json"))
        let policy = root.appendingPathExtension("json")
        try Data(#"{"version":1,"entries":[{"method":"PUT","path_template":"/api/v1/config","class":"config"}]}"#.utf8).write(to: policy)
        preference = AppLocalePreference(directory: root.appendingPathExtension("preferences"))
        scheme = try AppScheme(root: root, transport: transport, remote: false, policyURL: policy, localePreference: preference)
        let config = WKWebViewConfiguration(); scheme.configure(config)
        view = WKWebView(frame: NSRect(x: 0, y: 0, width: 760, height: 560), configuration: config)
        window = NSWindow(contentRect: view.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false; window.contentView = view
        app.bindMainScheme(scheme); app.bindMainWindow(window)
        app.terminationDelivery = termination
        app.appLifetime = MacAppLifetime(backend: backend)
        scheme.attach(view, load: URL(string: AppSchemeRoute.origin + "/")!)
    }
    func eventually(seconds: Int = 5, _ condition: () async throws -> Bool) async throws {
        let clock = ContinuousClock(), deadline = ContinuousClock().now.advanced(by: .seconds(seconds))
        while clock.now < deadline {
            if try await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out waiting for the actual Main relay")
        throw AppAPIError.unavailable
    }
    func js(_ code: String, _ arguments: [String: Any] = [:]) async throws -> Any? {
        try await view.callAsyncJavaScript(code, arguments: arguments, in: nil, contentWorld: .page)
    }
    func admit(summary: Bool = true, subscribe: Bool = true, waitForAnchor: Bool = true) async throws {
        try await eventually { (try? await self.js("return typeof window.__hrAppContext === 'string';")) as? Bool == true }
        try await scheme.admitDocument()
        if summary { _ = try await call("app.ownersChanged", Self.clean) }
        if subscribe {
            _ = try await js("window.requests=[]; window.off=window.happyranchConnection.on('app.lifecycleRequested', raw => requests.push(JSON.parse(raw))); return true;")
            if summary && waitForAnchor { try await eventually { self.scheme.lifecycleAnchor != nil } }
            else { _ = try await call("app.status", "{}") }
        }
    }
    func call(_ command: String, _ data: String) async throws -> String {
        let value = try await js("try { return await window.happyranchConnection.call(command, data); } catch (error) { return typeof error === 'string' ? error : error.message; }", ["command": command, "data": data]) as? String
        return try XCTUnwrap(value)
    }
    func respond(_ response: NSApplication.ModalResponse = .alertSecondButtonReturn) throws {
        let prompt = try XCTUnwrap(app.appLifetime.confirmation)
        app.appLifetime.respondToLifecycleConfirmation(token: prompt.token, response: response)
    }
    func answer(_ decision: String = "proceed", id: String? = nil, summary: String = clean) async throws -> String {
        let request = try XCTUnwrap(id ?? app.appLifetime.token?.uuidString)
        let raw = String(summary.dropLast()) + ",\"gate\":{\"requestId\":\"\(request)\",\"decision\":\"\(decision)\"}}"
        return try await call("app.ownersChanged", raw)
    }
    func dispose() {
        app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        scheme.close(); window.close(); view.stopLoading()
        try? FileManager.default.removeItem(at: root)
        try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        try? FileManager.default.removeItem(at: root.appendingPathExtension("preferences"))
    }
}

final class MacAppLifetimeTests: XCTestCase {
    @MainActor
    func testCapturedIntentRequiresNativeConsentThenCorrelatedGateAndConsumesOnce() async throws {
        for intent in [MacLifetimeIntent.close, .quit, .update] {
            let f = try LifetimeFixture(); defer { f.dispose() }
            try await f.admit()
            if intent == .update { f.app.postponeUpdateRelaunch {} }
            else { XCTAssertTrue(f.app.appLifetime.request(intent)) }
            let token = try XCTUnwrap(f.app.appLifetime.token)
            XCTAssertTrue(f.backend.effects.isEmpty)
            XCTAssertFalse(f.app.appLifetime.request(.quit))
            if intent != .update { XCTAssertEqual(f.backend.prompts.count, 1); try f.respond() }
            XCTAssertTrue(f.backend.effects.isEmpty, "Native acceptance alone cannot exit")
            let wrong = try await f.answer(id: "wrong")
            XCTAssertTrue(wrong.contains("\"effect\":\"none\""))
            _ = try await f.call("app.ownersChanged", LifetimeFixture.clean)
            let keep = try await f.answer("keep", id: token.uuidString)
            XCTAssertEqual(keep, "{}")
            XCTAssertFalse(f.app.appLifetime.busy)
            let replay = try await f.answer(id: token.uuidString)
            XCTAssertTrue(replay.contains("\"effect\":\"none\""))
            XCTAssertTrue(f.backend.effects.isEmpty)
            XCTAssertEqual(f.termination.calls, 0)
        }
    }

    @MainActor
    func testAdmissionNoSummaryNoMainNoSubscriptionAndNoReplay() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        XCTAssertFalse(f.app.appLifetime.request(.close), "Unadmitted Main")
        try await f.admit(summary: false, subscribe: false)
        XCTAssertFalse(f.app.appLifetime.request(.quit), "No initial summary")
        _ = try await f.call("app.ownersChanged", LifetimeFixture.clean)
        XCTAssertFalse(f.app.appLifetime.request(.quit), "No subscriber")
        XCTAssertTrue(f.backend.prompts.isEmpty)
        _ = try await f.js("window.requests=[]; window.off=window.happyranchConnection.on('app.lifecycleRequested', raw => requests.push(JSON.parse(raw))); return true;")
        try await f.eventually { f.scheme.lifecycleAnchor != nil }
        let before = try await f.js("return requests.length;") as? Int
        XCTAssertEqual(before, 0, "Subscription never replays a dropped request")
        XCTAssertTrue(f.app.appLifetime.request(.quit))
        try f.respond()
        let old = try XCTUnwrap(f.app.appLifetime.token?.uuidString)
        _ = try await f.js("off(); return true;")
        try await f.eventually { !f.app.appLifetime.busy }
        _ = try await f.js("window.off=window.happyranchConnection.on('app.lifecycleRequested', raw => requests.push(JSON.parse(raw))); return true;")
        try await f.eventually { f.scheme.lifecycleAnchor != nil }
        let stale = try await f.answer(id: old)
        XCTAssertTrue(stale.contains("\"effect\":\"none\""))
        _ = try await f.call("app.ownersChanged", LifetimeFixture.clean)
        XCTAssertTrue(f.app.appLifetime.request(.quit))
        XCTAssertNotEqual(f.app.appLifetime.token?.uuidString, old)
        XCTAssertTrue(f.backend.effects.isEmpty)
    }

    @MainActor
    func testCancelAbortAndStaleResponseNeverEmitGateOrEffects() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        for response in [NSApplication.ModalResponse.alertFirstButtonReturn, .abort, .stop, .init(rawValue: 123)] {
            XCTAssertTrue(f.app.appLifetime.request(.close))
            let token = try XCTUnwrap(f.app.appLifetime.token)
            try f.respond(response)
            f.app.appLifetime.respondToLifecycleConfirmation(token: token, response: .alertSecondButtonReturn)
            XCTAssertFalse(f.app.appLifetime.busy)
            XCTAssertTrue(f.backend.effects.isEmpty)
        }
        let count = try await f.js("return requests.length;") as? Int
        XCTAssertEqual(count, 0, "All human time and Cancel occur before the web save freeze")
    }

    @MainActor
    func testFinalRecheckRefusesChangedTargetChildNativeCustodyAndHeldOwnersSynchronously() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        for held in ["dirty", "failed", "pending", "unknown"] {
            XCTAssertTrue(f.app.appLifetime.request(.quit)); try f.respond()
            let blocking = ["pending", "unknown"].contains(held) ? "hold" : "prompt"
            let counts = ["dirty", "failed", "pending", "unknown"].map { "\"\($0)\":\($0 == held ? 1 : 0)" }.joined(separator: ",")
            let summary = "{\"schema\":2,\"blocking\":\"\(blocking)\",\"counts\":{\(counts)}}"
            let refusal = try await f.answer(summary: summary)
            XCTAssertTrue(refusal.contains("\"effect\":\"none\""))
            XCTAssertTrue(f.backend.effects.isEmpty)
            _ = try await f.call("app.ownersChanged", LifetimeFixture.clean)
        }
        XCTAssertNil(f.scheme.selectTransport(id: "native-transfer", transport: SwitchTransport(), remote: false))
        XCTAssertTrue(f.app.appLifetime.request(.quit)); try f.respond()
        let mutation = SwitchSchemeTask(method: "POST", path: "/api/v1/pending", generation: f.scheme.generation)
        f.scheme.webView(f.view, start: mutation)
        let nativeHeld = try await f.answer()
        XCTAssertTrue(nativeHeld.contains("\"effect\":\"none\""))
        f.scheme.webView(f.view, stop: mutation)
        XCTAssertTrue(f.app.appLifetime.request(.quit)); try f.respond()
        XCTAssertNil(f.scheme.selectTransport(id: "changed", transport: SwitchTransport(), remote: true))
        let targetChanged = try await f.answer()
        XCTAssertTrue(targetChanged.contains("\"effect\":\"none\""))
        XCTAssertTrue(f.backend.effects.isEmpty)
        XCTAssertTrue(f.app.appLifetime.request(.quit)); try f.respond()
        f.app.currentHandle = FakeProcessHandle()
        let replaced = try await f.answer()
        XCTAssertTrue(replaced.contains("\"effect\":\"none\""))
        XCTAssertTrue(f.backend.effects.isEmpty)
        XCTAssertEqual(f.termination.calls, 0)
    }

    @MainActor
    func testUnresponsiveRequestAndRetirementRetainUnknownWithoutReplaying() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        XCTAssertTrue(f.app.appLifetime.request(.close)); try f.respond()
        XCTAssertTrue(f.app.appLifetime.busy)
        XCTAssertFalse(f.app.appLifetime.request(.quit))
        let old = try XCTUnwrap(f.app.appLifetime.token?.uuidString)
        _ = try await f.call("app.ownersChanged", #"{"schema":2,"blocking":"hold","counts":{"dirty":0,"failed":0,"pending":0,"unknown":1}}"#)
        f.scheme.documentDidStart()
        // Retired unknown saves deliberately prevent an anchor even after the
        // replacement relay and its subscription have been admitted.
        try await f.admit(waitForAnchor: false)
        XCTAssertEqual(f.scheme.switchRefusal, "owner_save_unknown")
        XCTAssertNil(f.scheme.lifecycleAnchor)
        XCTAssertFalse(f.app.appLifetime.request(.close), "Empty replacement cannot reconcile old save")
        let stale = try await f.answer(id: old)
        XCTAssertTrue(stale.contains("\"effect\":\"none\""))
        XCTAssertTrue(f.backend.effects.isEmpty)
    }

    @MainActor
    func testNativeAlertCopyAndCancelDefaultAcrossLocalesAndConsequences() {
        for zh in [false, true] {
            for owned in [false, true] {
                for remote in [false, true] {
                    for kind in [MacAppLifetime.Kind.exit, .updateIdle, .updateBusy(3), .updateBusy(nil)] {
                        let prompt = MacAppLifetime.Prompt(token: UUID(), intent: .quit, kind: kind)
                        let alert = AppDelegate.lifecycleAlert(for: prompt, zh: zh, ownedRunning: owned, remote: remote, accessUnsaved: true)
                        switch kind {
                        case .exit:
                            XCTAssertEqual(alert.messageText, zh ? "退出 HappyRanch？" : "Quit HappyRanch?")
                            XCTAssertEqual(alert.buttons[1].title, zh ? "退出" : "Quit")
                        case .updateIdle:
                            XCTAssertEqual(alert.messageText, zh ? "重新启动 HappyRanch 以安装更新？" : "Restart HappyRanch to install the update?")
                            XCTAssertEqual(alert.buttons[1].title, zh ? "立即重新启动" : "Restart now")
                        case .updateBusy(let count):
                            XCTAssertEqual(alert.messageText, count == nil ? (zh ? "工作状态不可用" : "Work status is unavailable")
                                : (zh ? "智能体工作正在运行" : "Agent work is running"))
                            XCTAssertEqual(alert.buttons[1].title, zh ? "工作完成后更新" : "Update when they finish")
                            XCTAssertEqual(alert.buttons[2].title, zh ? "立即更新并停止工作" : "Update now and stop them")
                        }
                        XCTAssertEqual(alert.buttons[0].title, zh ? "取消" : "Cancel")
                        XCTAssertEqual(alert.buttons[0].keyEquivalent, "\r")
                        XCTAssertTrue(alert.buttons.dropFirst().allSatisfy { $0.keyEquivalent.isEmpty })
                        XCTAssertTrue(alert.informativeText.contains(owned
                            ? (zh ? "正在运行的工作将被中断" : "Running work will be interrupted")
                            : (zh ? "没有正在运行" : "no running local daemon")))
                        if remote {
                            XCTAssertTrue(alert.informativeText.contains(zh ? "授权和已保存的访问权限将保留" : "grant and saved access will remain"))
                            XCTAssertTrue(alert.informativeText.contains(zh ? "可能需要新的邀请" : "may require a new invite"))
                        }
                        if kind == .updateBusy(nil) { XCTAssertFalse(alert.buttons[2].isEnabled) }
                    }
                }
            }
        }
    }

    @MainActor
    func testMainCloseAndSystemQuitObserveRealOwnedChildExitBeforeRetirement() async throws {
        for close in [true, false] {
            let f = try LifetimeFixture(); defer { f.dispose() }
            try await f.admit()
            let child = try RealProcessController().launch(executableURL: URL(fileURLWithPath: "/bin/sleep"),
                arguments: ["30"], currentDirectoryURL: nil, environment: nil, terminationHandler: nil)
            defer {
                let alive = child.isRunning, observed = kill(child.processIdentifier, 0), code = errno
                print("[MAC-4b child-census] pid=\(child.processIdentifier) running=\(alive) kill0=\(observed) errno=\(code)")
                XCTAssertFalse(alive); XCTAssertEqual(observed, -1); XCTAssertEqual(code, ESRCH)
                if alive { _ = ManagedProcessStop.stop(child, grace: .init(term: 1, kill: 1)) }
            }
            f.app.currentHandle = child
            f.app.supervisor.configure(homeDir: f.root.path); try f.app.supervisor.start()
            f.app.supervisor.onHealthCheckPassed(pid: child.processIdentifier, port: 8765)
            let observer = NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification, object: f.window, queue: nil) { _ in
                MainActor.assumeIsolated { XCTAssertFalse(child.isRunning); f.termination.closes += 1 }
            }
            defer { NotificationCenter.default.removeObserver(observer) }
            if close { f.window.performClose(nil) }
            else { XCTAssertEqual(f.app.applicationShouldTerminate(NSApplication.shared), .terminateCancel) }
            XCTAssertTrue(child.isRunning); XCTAssertEqual(f.termination.closes, 0)
            try f.respond()
            _ = try await f.answer()
            // The gate ACK precedes teardown. ManagedProcessStop pumps the
            // run loop, so the JS completion can resume before effects finish.
            try await f.eventually { !f.app.appLifetime.busy }
            XCTAssertFalse(child.isRunning)
            XCTAssertEqual(f.termination.closes, 1); XCTAssertEqual(f.termination.calls, 1)
            XCTAssertNil(f.scheme.lifecycleAnchor)
        }
    }
}

extension MacAppLifetimeTests {
    @MainActor
    func testForeignCallerCannotRetireSubscriptionPoisonEvidenceOrImplementRelaunch() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        let anchor = f.scheme.lifecycleAnchor
        for item in [
            ["context": "foreign", "command": "app.ownersChanged", "data": "malformed"],
            ["context": "foreign", "command": "subscribe", "event": "app.lifecycleRequested", "active": "0"],
            ["context": "foreign", "command": "app.relaunch", "data": "{}"],
        ] {
            let raw = try await f.js("try { await window.webkit.messageHandlers.appTransport.postMessage(item); return 'accepted'; } catch { return 'refused'; }", ["item": item]) as? String
            XCTAssertEqual(raw, "refused")
            XCTAssertEqual(f.scheme.lifecycleAnchor, anchor)
        }
        let relaunch = try await f.call("app.relaunch", "{}")
        XCTAssertEqual(relaunch, "unavailable")
        XCTAssertTrue(f.app.appLifetime.request(.quit)); try f.respond()
        let rawContext = try await f.js("return window.__hrAppContext;") as? String
        let context = try XCTUnwrap(rawContext)
        let request = try XCTUnwrap(f.app.appLifetime.token?.uuidString)
        let data = String(LifetimeFixture.clean.dropLast()) + ",\"gate\":{\"requestId\":\"\(request)\",\"decision\":\"proceed\"}}"
        let item = ["context": context, "command": "app.ownersChanged", "data": data]
        let subframe = try await f.js("""
            const frame = document.createElement('iframe'); document.body.append(frame);
            try {
              const native = frame.contentWindow.webkit?.messageHandlers?.appTransport;
              if (!native) return 'missing-native-frame';
              try { await native.postMessage(item); return 'accepted'; } catch { return 'refused'; }
            } finally { frame.remove(); }
            """, ["item": item]) as? String
        XCTAssertEqual(subframe, "refused", "Must reach native's main-frame boundary")
        let otherConfig = WKWebViewConfiguration(); f.scheme.configure(otherConfig)
        let other = WKWebView(frame: .zero, configuration: otherConfig)
        defer { other.stopLoading() }
        other.loadHTMLString("<html><body>Foreign view</body></html>", baseURL: URL(string: AppSchemeRoute.origin + "/"))
        try await f.eventually {
            (try? await other.callAsyncJavaScript("return document.readyState === 'complete';", arguments: [:], in: nil, contentWorld: .page)) as? Bool == true
        }
        let foreignView = try await other.callAsyncJavaScript("try { await window.webkit.messageHandlers.appTransport.postMessage(item); return 'accepted'; } catch { return 'refused'; }",
            arguments: ["item": item], in: nil, contentWorld: .page) as? String
        XCTAssertEqual(foreignView, "refused")
        XCTAssertEqual(f.scheme.lifecycleAnchor, anchor)
        let kept = try await f.answer("keep")
        XCTAssertEqual(kept, "{}", "The actual current Main remains admitted")
        XCTAssertTrue(f.backend.effects.isEmpty)
    }

    @MainActor
    func testNativeNonGETAndLocalCustodyHoldAllThreeEntriesAndCleanRetirementAllowsFreshAttempt() async throws {
        let transport = SwitchTransport()
        let transferFixture = try LifetimeFixture(transport: transport)
        defer { transferFixture.dispose() }
        try await transferFixture.admit()
        let scheme = transferFixture.scheme, app = transferFixture.app
        let mutation = SwitchSchemeTask(method: "POST", path: "/api/v1/pending", generation: scheme.generation)
        scheme.webView(transferFixture.view, start: mutation)
        try await transferFixture.eventually { transport.count == 1 }
        XCTAssertTrue(scheme.lifecycleNonGETPending)
        XCTAssertNotNil(app.lifetimeRefusal, "Real transfer custody holds despite a clean summary")
        for intent in [MacLifetimeIntent.close, .quit, .update] { XCTAssertFalse(app.appLifetime.request(intent)) }
        XCTAssertEqual(transport.count, 1, "Lifecycle admission never replays the transfer")
        scheme.webView(transferFixture.view, stop: mutation)
        let local = LifetimeLocalConsent()
        app.localControls = MacLocalControls(backend: local)
        app.localControls.call(command: "local.restart", data: "{}", current: { true }, gate: { nil }) { _, _ in }
        for intent in [MacLifetimeIntent.close, .quit, .update] { XCTAssertFalse(app.appLifetime.request(intent)) }
        try await transferFixture.eventually { local.pending != nil }
        local.pending?.resume(returning: false); local.pending = nil
        try await transferFixture.eventually { !app.localControls.busy }
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        XCTAssertTrue(f.app.appLifetime.request(.quit)); try f.respond()
        let previous = try XCTUnwrap(f.app.appLifetime.token)
        f.scheme.documentDidStart()
        try await f.admit()
        XCTAssertTrue(f.app.appLifetime.request(.quit), "Clean old retirement admits a fresh explicit attempt")
        XCTAssertNotEqual(f.app.appLifetime.token, previous)
        XCTAssertTrue(f.backend.effects.isEmpty)
    }

    @MainActor
    func testStatusStateAndLocaleNeverAdvertiseRelaunchAndNativeLocaleRemainsFrozen() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        _ = try await f.js("window.states=[]; window.happyranchConnection.on('app.stateChanged', raw => states.push(JSON.parse(raw))); return true;")
        _ = try await f.call("app.status", "{}") // Serialize subscription admission.
        f.scheme.updateReadiness(try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":2,"min_client":2}}"#.utf8)))
        let prompt = MacAppLifetime.Prompt(token: UUID(), intent: .quit, kind: .exit)
        let nativeBefore = AppDelegate.lifecycleAlert(for: prompt, zh: f.preference.nativeChinese,
            ownedRunning: false, remote: false, accessUnsaved: false).messageText
        let launch = f.preference.launchLocale
        let locale = try await f.call("app.locale.set", #"{"schema":2,"locale":"zh-CN"}"#)
        XCTAssertTrue(locale.contains("zh-CN"))
        XCTAssertEqual(f.preference.launchLocale, launch)
        let nativeAfter = AppDelegate.lifecycleAlert(for: prompt, zh: f.preference.nativeChinese,
            ownedRunning: false, remote: false, accessUnsaved: false).messageText
        XCTAssertEqual(nativeAfter, nativeBefore, "A later confirmation keeps the frozen launch locale")

        let status = try await f.call("app.status", "{}")
        XCTAssertFalse(status.contains("app_relaunch"))
        let states = try await f.js("return states.length > 0 && states.every(s => !Object.prototype.hasOwnProperty.call(s, 'app_relaunch'));") as? Bool
        XCTAssertEqual(states, true)
        XCTAssertTrue(f.backend.effects.isEmpty)
    }

    @MainActor
    func testSparkleReleaseObservesRealOwnedChildExitEvenWithRemoteSelected() async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        let child = try RealProcessController().launch(executableURL: URL(fileURLWithPath: "/bin/sleep"),
            arguments: ["30"], currentDirectoryURL: nil, environment: nil, terminationHandler: nil)
        defer {
            let alive = child.isRunning, result = kill(child.processIdentifier, 0), code = errno
            print("[MAC-4b update-child-census] pid=\(child.processIdentifier) running=\(alive) kill0=\(result) errno=\(code)")
            XCTAssertFalse(alive); XCTAssertEqual(result, -1); XCTAssertEqual(code, ESRCH)
            if alive { _ = ManagedProcessStop.stop(child, grace: .init(term: 1, kill: 1)) }
        }
        f.app.currentHandle = child
        f.app.supervisor.configure(homeDir: f.root.path); try f.app.supervisor.start()
        f.app.supervisor.onHealthCheckPassed(pid: child.processIdentifier, port: 8765)
        XCTAssertNil(f.scheme.selectTransport(id: "remote-synthetic", transport: SwitchTransport(), remote: true))
        var releases = 0
        f.app.postponeUpdateRelaunch { XCTAssertFalse(child.isRunning); releases += 1 }
        try f.respond()
        XCTAssertTrue(child.isRunning); XCTAssertEqual(releases, 0)
        _ = try await f.answer()
        try await f.eventually { !f.app.appLifetime.busy }
        XCTAssertFalse(child.isRunning); XCTAssertEqual(releases, 1)
        XCTAssertEqual(f.termination.calls, 0, "Sparkle, not ordinary Quit, owns update installation")
        XCTAssertEqual(f.app.applicationShouldTerminate(NSApplication.shared), .terminateNow)
        XCTAssertEqual(f.app.applicationShouldTerminate(NSApplication.shared), .terminateCancel,
                       "One platform termination callback consumes the update grant")
    }
}
