import Darwin
import Foundation
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

/// A process observation double, never a substitute for signal-delivery proof.
private final class StopObservation: ProcessHandle, @unchecked Sendable {
    var isRunning = true
    let processIdentifier: Int32 = 42
    let status: Int32?
    var terms = 0
    var kills = 0
    var terminationStatus: Int32 {
        precondition(!isRunning, "must not read exit status from a live process")
        return status ?? 0
    }
    var terminationReason: Process.TerminationReason { status == 0 ? .exit : .uncaughtSignal }
    var capturedStandardError: String? { nil }
    var capturedStandardOutput: String? { nil }

    init(statusOnTerm: Int32? = nil) { status = statusOnTerm }
    func terminate() {
        terms += 1
        if status != nil { isRunning = false }
    }
    func forceKill() -> Int32? {
        kills += 1
        return EPERM
    }
}

@Suite("AppDelegate Stop honesty", .serialized)
@MainActor
struct AppDelegateStopHonestyTests {
    @Test("TERM outcome is observed without KILL", arguments: [Int32(0), Int32(15)])
    func termOutcomeIsObserved(status: Int32) {
        let handle = StopObservation(statusOnTerm: status)
        #expect(ManagedProcessStop.stop(handle) == .exited(
            exitCode: status, signal: status == 0 ? nil : status))
        #expect(handle.terms == 1)
        #expect(handle.kills == 0)
    }

    @Test("both launch callbacks preserve actual uncaught signal", arguments: ["dev", "bundled"])
    func signalCallbackRecordsActualSignal(mode: String) async {
        AppDelegate._testPackagingMode = mode
        AppDelegate._testSkipBundledDaemonPreflight = true
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
        }
        let delegate = AppDelegate()
        let factory = FakeProcessController()
        delegate.processController = factory
        delegate.supervisor.configure(homeDir: "/unused")
        factory.simulateImmediateExitOnNextLaunch = true
        factory.immediateExitCode = SIGTERM
        delegate.startDaemon()
        await Task.yield()
        #expect(factory.launchCallCount == 1)
        #expect(delegate.supervisor.state == .crashed)
        #expect(delegate.supervisor.lastExitSignal == SIGTERM)
        #expect(delegate.diagnostics.collect()["last_exit_signal"] as? Int32 == SIGTERM)
    }

    @Test("failed KILL retains ownership, honest diagnostics and Start disabled")
    func failedKillKeepsStartDisabled() throws {
        let delegate = AppDelegate()
        let factory = FakeProcessController()
        delegate.processController = factory
        delegate.supervisor.configure(homeDir: "/unused")
        try delegate.supervisor.start()
        let handle = StopObservation()
        delegate.currentHandle = handle
        delegate.refreshDerivedState()

        delegate.restartDaemon()

        #expect(handle.terms == 1)
        #expect(handle.kills == 1)
        #expect(delegate.supervisor.state == .stopping)
        #expect(!delegate.canStart)
        #expect(delegate.supervisor.lastExitSignal == nil)
        #expect(delegate.diagnostics.collect()["last_exit_signal"] == nil)
        #expect(delegate.stateText.contains("Stop incomplete"))
        delegate.startDaemon()
        #expect(factory.launchCallCount == 0)
    }

    @Test("scripted Stop escalates a disposable TERM-ignoring child")
    func stopEscalatesRealChild() throws {
        let scratch = FileManager.default.temporaryDirectory
            .appendingPathComponent("stop-child-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: scratch) }
        let ready = scratch.appendingPathComponent("ready")
        let controller = RealProcessController()
        // exec retains ignored TERM and leaves exactly one disposable process.
        let child = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "trap '' TERM; printf ready > \"$1\"; exec /bin/sleep 60",
                        "stop-child", ready.path],
            currentDirectoryURL: scratch, environment: nil, terminationHandler: nil)
        defer {
            if child.isRunning { _ = child.forceKill() }
            let end = ProcessInfo.processInfo.systemUptime + 2
            while child.isRunning && ProcessInfo.processInfo.systemUptime < end {
                RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.01))
            }
            #expect(!child.isRunning, "disposable child must be reaped")
        }
        let end = ProcessInfo.processInfo.systemUptime + 2
        while !FileManager.default.fileExists(atPath: ready.path)
                && child.isRunning && ProcessInfo.processInfo.systemUptime < end {
            RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.01))
        }
        try #require(FileManager.default.fileExists(atPath: ready.path))
        let delegate = AppDelegate()
        delegate.supervisor.configure(homeDir: scratch.path)
        try delegate.supervisor.start()
        delegate.currentHandle = child
        delegate.stopDaemon()

        try #require(!child.isRunning, "Stop must actually kill the TERM-ignoring child")
        #expect(child.terminationReason == .uncaughtSignal)
        #expect(child.terminationStatus == SIGKILL)
        #expect(delegate.diagnostics.collect()["launcher_log"] as? String
                == "Managed Stop: exited, status=9, signal=9")
    }
}
