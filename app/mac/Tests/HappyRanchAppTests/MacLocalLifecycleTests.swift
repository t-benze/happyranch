import AppKit
import Darwin
import WebKit
import XCTest
import HappyRanchSupervisor
@testable import HappyRanchApp

/// Substitute only the executable at the existing process boundary. Handles,
/// signals, exit callbacks, health HTTP and all supervisor transitions are real.
private final class LocalLifecycleChild: ProcessControlling, @unchecked Sendable {
    private let script: URL
    private let home: URL
    private let lock = NSLock()
    private var launched: [any ProcessHandle] = []
    var children: [any ProcessHandle] { lock.withLock { launched } }

    init(home: URL) throws {
        self.home = home
        script = home.appendingPathComponent("health-child.py")
        try Data(Self.source.utf8).write(to: script)
    }

    func launch(executableURL: URL, arguments: [String], currentDirectoryURL: URL?,
                environment: [String: String]?,
                terminationHandler: (@Sendable (any ProcessHandle) -> Void)?) throws -> any ProcessHandle {
        for name in ["daemon.port", "daemon.pid", "admit-ready", "ready-read"] {
            let file = home.appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
        }
        // Python is already a prerequisite of the existing Jenkins native job.
        // SwiftPM's test bundle does not contain the frozen product daemon.
        let child = try RealProcessController().launch(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["python3", "-I", "-B", script.path, home.path],
            currentDirectoryURL: currentDirectoryURL, environment: environment,
            terminationHandler: terminationHandler)
        lock.withLock { launched.append(child) }
        return child
    }

    private static let source = #"""
    import json, os, pathlib, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    home = pathlib.Path(sys.argv[1])
    token = "mac-local-lifecycle-synthetic"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path != "/api/v1/health":
                self.send_error(404)
                return
            ready = (home / "admit-ready").exists()
            if ready and self.headers.get("Authorization") == "Bearer " + token:
                (home / "ready-read").write_text(str(os.getpid()))
            body = json.dumps({"api_contract": {"current": 1, "min_client": 1},
                               "update_busy": {"busy": not ready}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    for name, value in [("daemon.token", token), ("daemon.pid", str(os.getpid())),
                        ("daemon.port", str(server.server_port))]:
        temporary = home / (name + ".tmp")
        temporary.write_text(value)
        os.replace(temporary, home / name)
    server.serve_forever()
    """#
}

extension AppSchemeSwitchTests {
    /// Runs in the existing unfiltered headless XCTest command; no daemon data,
    /// Jenkins change, synthetic supervisor state or simulated termination.
    @MainActor
    func testBridgeOwnsRealChildStartStopRestartAndQuit() async throws {
        NSApplication.shared.finishLaunching()
        let (root, scheme, view, _) = try fixture()
        defer {
            scheme.close()
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: root.appendingPathExtension("json"))
        }
        let home = root.appendingPathComponent("daemon", isDirectory: true)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: false)
        let defaults = UserDefaults.standard
        let oldRole = defaults.object(forKey: "connectionRolePreference")
        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        // The existing unfiltered XCTest invocation is serial and runs the
        // SwiftPM test bundle, not an installed .app. Use the ordinary dev-home
        // override rather than adding or enabling a production test-only seam.
        XCTAssertFalse(AppDelegate.isRunningInAppBundle())
        XCTAssertEqual(AppDelegate.packagingMode(), "dev")
        setenv("HAPPYRANCH_DAEMON_HOME", home.path, 1)
        defer {
            if let oldHome { setenv("HAPPYRANCH_DAEMON_HOME", oldHome, 1) }
            else { unsetenv("HAPPYRANCH_DAEMON_HOME") }
            defaults.set(oldRole, forKey: "connectionRolePreference")
        }
        let child = try LocalLifecycleChild(home: home)
        let app = AppDelegate()
        app.connectionRolePreference = .home
        app.processController = child
        app.supervisor.configure(homeDir: home.path)
        app.bindMainScheme(scheme)
        defer {
            app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            // Failure cleanup does not satisfy any lifecycle assertion below.
            for handle in child.children where handle.isRunning {
                _ = ManagedProcessStop.stop(handle, grace: .init(term: 1, kill: 1))
                XCTAssertFalse(handle.isRunning, "Failure cleanup must reap the disposable child")
            }
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

        func wait(_ label: String, until condition: () async throws -> Bool) async throws {
            let clock = ContinuousClock(), deadline = ContinuousClock().now.advanced(by: .seconds(10))
            while clock.now < deadline {
                if try await condition() { return }
                await macLocalAppKitTurn()
                try await clock.sleep(for: .milliseconds(10))
            }
            XCTFail("Timed out: \(label)")
            throw AppAPIError.unavailable
        }
        func js(_ source: String, _ arguments: [String: Any] = [:]) async throws -> Any? {
            try await view.callAsyncJavaScript(source, arguments: arguments, in: nil, contentWorld: .page)
        }
        try await wait("Main relay loaded") {
            (try? await js("return typeof window.__hrAppContext === 'string';")) as? Bool == true
        }
        try await scheme.admitDocument()
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        let generation = scheme.generation
        let context = try await js("return window.__hrAppContext;") as? String
        _ = try await js("window.lifecycleStates=[]; window.happyranchConnection.on('app.stateChanged', raw => { const s=JSON.parse(raw); lifecycleStates.push({state:s.state,running:s.local.running}); }); return true;")

        func begin(_ command: String) async throws {
            _ = try await js("""
                window.lifecycleStates=[]; window.lifecycleResult=null; window.lifecycleError=null;
                window.happyranchConnection.call(command, '{}').then(
                  raw => { window.lifecycleResult=JSON.parse(raw); },
                  error => { window.lifecycleError=String(error); });
                return true;
                """, ["command": command])
        }
        func result() async throws -> [String: Any] {
            try await wait("bridge reply") {
                try await js("return lifecycleResult !== null || lifecycleError !== null;") as? Bool == true
            }
            let error = try await js("return lifecycleError;")
            XCTAssertTrue(error == nil || error is NSNull, "Bridge command must succeed: \(String(describing: error))")
            let value = try await js("return lifecycleResult;")
            return try XCTUnwrap(value as? [String: Any])
        }
        func status(_ command: String) async throws -> [String: Any] {
            let value = try await js("return JSON.parse(await window.happyranchConnection.call(command, '{}'));",
                                     ["command": command])
            return try XCTUnwrap(value as? [String: Any])
        }
        func service(_ value: [String: Any]) throws -> [String: Any] {
            try XCTUnwrap(value["service"] as? [String: Any])
        }
        func readmission(_ value: [String: Any]) throws -> String? {
            try XCTUnwrap(value["readmission"] as? [String: Any])["state"] as? String
        }
        func assertExited(_ handle: any ProcessHandle) {
            XCTAssertFalse(handle.isRunning, "Must observe the actual child exit")
            let observed = kill(handle.processIdentifier, 0), error = errno
            XCTAssertEqual(observed, -1)
            XCTAssertEqual(error, ESRCH, "No process may remain at the owned PID")
        }
        func observeBusyThenReady() async throws {
            try await wait("published busy") {
                try await js("return lifecycleStates.some(s => s.state === 'busy');") as? Bool == true
            }
            let busy = try await status("app.status")
            XCTAssertEqual(busy["state"] as? String, "busy", "A live PID/HTTP 200 is insufficient for ready")
            XCTAssertEqual((busy["local"] as? [String: Any])?["running"] as? String, "running")
            let premature = try await js("return lifecycleStates.some(s => s.state === 'ready');") as? Bool
            XCTAssertEqual(premature, false, "No ready publication while child health is busy")
            XCTAssertFalse(FileManager.default.fileExists(atPath: home.appendingPathComponent("ready-read").path))
            try Data().write(to: home.appendingPathComponent("admit-ready"))
            let ready = try await status("app.status")
            XCTAssertEqual(ready["state"] as? String, "ready")
            try await wait("published ready after busy") {
                try await js("return lifecycleStates.at(-1)?.state === 'ready';") as? Bool == true
            }
            let rawStates = try await js("return lifecycleStates.map(s => s.state);")
            let states = try XCTUnwrap(rawStates as? [String])
            XCTAssertTrue(states.contains("target_starting"))
            XCTAssertLessThan(try XCTUnwrap(states.firstIndex(of: "busy")), try XCTUnwrap(states.firstIndex(of: "ready")))
            let readPID = try String(contentsOf: home.appendingPathComponent("ready-read"), encoding: .utf8)
            XCTAssertEqual(Int32(readPID), app.currentHandle?.processIdentifier,
                           "Ready must follow authenticated HTTP health from the actual current child")
        }

        let initial = try await status("local.status")
        XCTAssertEqual(try service(initial)["running"] as? String, "stopped")
        try await begin("local.start")
        let started = try await result()
        let first = try XCTUnwrap(app.currentHandle)
        XCTAssertGreaterThan(first.processIdentifier, 1)
        XCTAssertTrue(first.isRunning)
        XCTAssertEqual(kill(first.processIdentifier, 0), 0)
        let firstPID = try String(contentsOf: home.appendingPathComponent("daemon.pid"), encoding: .utf8)
        XCTAssertEqual(Int32(firstPID), first.processIdentifier)
        XCTAssertEqual(try service(started)["running"] as? String, "running")
        XCTAssertEqual(try readmission(started), "unknown", "Busy must not be mapped to ready")
        try await observeBusyThenReady()

        try await begin("local.stop")
        let stopped = try await result()
        assertExited(first)
        XCTAssertEqual(app.supervisor.state, .stopped)
        XCTAssertEqual(try service(stopped)["running"] as? String, "stopped")
        try await wait("published stopped") {
            try await js("return lifecycleStates.some(s => s.state === 'unreachable' && s.running === 'stopped');") as? Bool == true
        }

        try await begin("local.start")
        _ = try await result()
        let second = try XCTUnwrap(app.currentHandle)
        XCTAssertTrue(second.isRunning)
        XCTAssertNotEqual(second.processIdentifier, first.processIdentifier)
        try await observeBusyThenReady()
        try await begin("local.restart")
        try await wait("real native Restart consent") { window.attachedSheet != nil }
        XCTAssertTrue(second.isRunning, "Restart must wait for consent")
        let consenting = try await status("local.status")
        XCTAssertEqual(try service(consenting)["busy"] as? Bool, true)
        XCTAssertEqual(try service(consenting)["running"] as? String, "running")
        window.endSheet(try XCTUnwrap(window.attachedSheet), returnCode: .alertSecondButtonReturn)
        let restarted = try await result()
        let third = try XCTUnwrap(app.currentHandle)
        assertExited(second)
        XCTAssertTrue(third.isRunning)
        XCTAssertNotEqual(third.processIdentifier, second.processIdentifier)
        XCTAssertEqual(try service(restarted)["running"] as? String, "running")
        XCTAssertEqual(try service(restarted)["busy"] as? Bool, false)
        XCTAssertEqual(try readmission(restarted), "unknown")
        try await observeBusyThenReady()
        XCTAssertEqual(child.children.count, 3, "Health Recheck must never launch another child")
        XCTAssertEqual(scheme.generation, generation)
        let retained = try await js("return window.__hrAppContext;") as? String
        XCTAssertEqual(retained, context)

        app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        XCTAssertFalse(third.isRunning, "Quit must stop its child before returning")
        XCTAssertEqual(app.supervisor.state, .stopped)
        // Yield queued termination callbacks; a pending Restart must not orphan
        // a newly launched child after the synchronous Quit path returns.
        await macLocalAppKitTurn()
        XCTAssertEqual(child.children.count, 3)
        for handle in child.children {
            assertExited(handle)
        }
    }
}
