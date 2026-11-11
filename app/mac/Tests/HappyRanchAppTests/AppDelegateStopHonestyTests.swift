import AppKit
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

/// Retains the factory's real launch callback while the first Stop cannot exit.
private final class QuitRetryController: ProcessControlling, @unchecked Sendable {
    let factory = FakeProcessController()

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?,
        terminationHandler: (@Sendable (any ProcessHandle) -> Void)?
    ) throws -> any ProcessHandle {
        let child = try factory.launch(
            executableURL: executableURL, arguments: arguments,
            currentDirectoryURL: currentDirectoryURL, environment: environment,
            terminationHandler: terminationHandler)
        return QuitRetryHandle(child: child)
    }
}

private final class QuitRetryHandle: ProcessHandle, @unchecked Sendable {
    let child: any ProcessHandle
    var terms = 0
    var kills = 0
    var onRetryTerm: (@Sendable () -> Void)?
    var isRunning: Bool { child.isRunning }
    var processIdentifier: Int32 { child.processIdentifier }
    var terminationStatus: Int32 { child.terminationStatus }
    var terminationReason: Process.TerminationReason { child.terminationReason }
    var capturedStandardError: String? { child.capturedStandardError }
    var capturedStandardOutput: String? { child.capturedStandardOutput }

    init(child: any ProcessHandle) { self.child = child }
    func terminate() {
        terms += 1
        if terms > 1 { onRetryTerm?() }
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

    @Test("Quit cancels an incomplete Restart before either launch callback", arguments: ["dev", "bundled"])
    func quitCancelsIncompleteRestart(mode: String) throws {
        AppDelegate._testPackagingMode = mode
        AppDelegate._testSkipBundledDaemonPreflight = true
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
        }
        let delegate = AppDelegate()
        let controller = QuitRetryController()
        delegate.processController = controller
        delegate.supervisor.configure(homeDir: "/unused")
        delegate.startDaemon()
        let handle = try #require(delegate.currentHandle as? QuitRetryHandle)
        let child = try #require(controller.factory.activeHandle)
        try #require(controller.factory.launchCallCount == 1)
        delegate.supervisor.onHealthCheckPassed(pid: handle.processIdentifier, port: 9876)
        delegate.refreshDerivedState()
        try #require(delegate.canRestart)

        delegate.restartDaemon()

        try #require(handle.terms == 1 && handle.kills == 1)
        try #require(handle.isRunning)
        try #require(delegate.currentHandle === handle)
        try #require(delegate.supervisor.state == .stopping)
        try #require(delegate.stateText.contains("Stop incomplete"))
        try #require(controller.factory.launchCallCount == 1)

        handle.onRetryTerm = {
            // Deliver the registered launch callback from Quit's pumped wait,
            // then let its real MainActor continuation settle before returning.
            RunLoop.main.perform {
                child.terminate()
                MainActor.assumeIsolated {
                    let end = ProcessInfo.processInfo.systemUptime + 1
                    while delegate.supervisor.state == .stopping
                            && ProcessInfo.processInfo.systemUptime < end {
                        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.01))
                    }
                }
            }
        }
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(handle.terms == 2)
        #expect(handle.kills == 1)
        #expect(controller.factory.terminateCallCount == 1)
        #expect(controller.factory.launchCallCount == 1)
        #expect(controller.factory.handles.count == 1)
        #expect(controller.factory.handles.allSatisfy { !$0.isRunning })
        #expect(!handle.isRunning)
        #expect(delegate.currentHandle === handle)
        #expect(delegate.currentHandle?.isRunning == false)
        #expect(delegate.supervisor.state == .stopped)
        #expect(delegate.supervisor.lastExitCode == 0)
    }

    @Test("HOME to CLIENT refuses a surviving managed child, and Quit retries Stop", arguments: [false, true])
    func roleSwitchKeepsHomeWhileChildSurvives(stoppedEarlier: Bool) throws {
        let savedPreference = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer {
            if let savedPreference {
                UserDefaults.standard.set(savedPreference, forKey: "connectionRolePreference")
            } else {
                UserDefaults.standard.removeObject(forKey: "connectionRolePreference")
            }
        }
        let delegate = AppDelegate()
        delegate.processController = FakeProcessController()
        delegate.connectionRolePreference = .home
        delegate.supervisor.configure(homeDir: "/unused")
        try delegate.supervisor.start()
        let handle = StopObservation()
        delegate.currentHandle = handle
        delegate.supervisor.onHealthCheckPassed(pid: handle.processIdentifier, port: 9876)
        delegate.refreshDerivedState()
        try #require(delegate.supervisor.state == .running)

        var earlierDiagnostic: String?
        if stoppedEarlier {
            delegate.stopDaemon()
            try #require(delegate.supervisor.state == .stopping)
            try #require(handle.isRunning)
            try #require(delegate.stateText.contains("Stop incomplete"))
            earlierDiagnostic = delegate.stateText
        }

        delegate.switchConnectionRole(to: .client)

        #expect(handle.terms == 1)
        #expect(handle.kills == 1)
        #expect(handle.isRunning)
        #expect(delegate.currentHandle === handle)
        #expect(delegate.supervisor.isManagedBySelf)
        #expect(delegate.connectionRole == .home)
        #expect(delegate.connectionRolePreference == .home)
        #expect(UserDefaults.standard.string(forKey: "connectionRolePreference") == "home")
        #expect(delegate.supervisor.state == .stopping)
        #expect(!delegate.canStart)
        #expect(delegate.stateText == "Stop incomplete: daemon still running; SIGKILL errno: \(EPERM)")
        let launchLog = delegate.diagnostics.collect()["launcher_log"] as? String
        #expect(launchLog?.contains("Managed Stop: still running") == true)
        if let earlierDiagnostic {
            #expect(delegate.stateText == earlierDiagnostic)
        }

        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(handle.terms == 2)
        #expect(handle.kills == 2)
        #expect(handle.isRunning)
        #expect(delegate.currentHandle === handle)
        #expect(delegate.supervisor.isManagedBySelf)
        #expect(delegate.supervisor.state == .stopping)
        #expect(!delegate.canStart)
        #expect(delegate.supervisor.lastExitCode == nil)
        #expect(delegate.supervisor.lastExitSignal == nil)
        #expect(delegate.stateText == "Stop incomplete: daemon still running; SIGKILL errno: \(EPERM)")
        #expect(delegate.diagnostics.collect()["launcher_log"] as? String
                == "Managed Stop: still running, SIGKILL errno=\(EPERM)")
    }

    @Test("Quit leaves a live external handle untouched", arguments: [false, true])
    func quitLeavesLiveExternalHandleUntouched(stopping: Bool) {
        let delegate = AppDelegate()
        let factory = FakeProcessController()
        delegate.processController = factory
        delegate.supervisor.configure(homeDir: "/unused")
        let handle = StopObservation()
        delegate.currentHandle = handle
        delegate.supervisor.onExternalDaemonDetected(pid: handle.processIdentifier, port: 9876)
        if stopping { delegate.supervisor.forceState(.stopping) }
        delegate.refreshDerivedState()

        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(handle.terms == 0)
        #expect(handle.kills == 0)
        #expect(handle.isRunning)
        #expect(delegate.currentHandle === handle)
        #expect(!delegate.supervisor.isManagedBySelf)
        #expect(delegate.supervisor.state == (stopping ? .stopping : .externalRunning))
        #expect(delegate.supervisor.lastExitCode == nil)
        #expect(delegate.supervisor.lastExitSignal == nil)
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
