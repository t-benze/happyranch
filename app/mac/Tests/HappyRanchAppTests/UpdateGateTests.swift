import AppKit
import Darwin
import Foundation
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

@Suite("Update gate", .serialized)
@MainActor
struct UpdateGateTests {
    @Test("owned idle update requires a click, then observes exit before releasing Sparkle")
    func idleStopsBeforeRelease() throws {
        let (delegate, factory, handle) = try ownedDaemon()
        var releases = 0
        delegate.postponeUpdateRelaunch {
            #expect(!handle.isRunning)
            #expect(factory.terminateCallCount == 1)
            releases += 1
        }
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: handle.isRunning,
            runningSessions: 0) == .confirmRestart)
        #expect(factory.terminateCallCount == 0)
        #expect(releases == 0)
        delegate.applyUpdateGate(runningSessions: 0, choice: .restart)
        #expect(releases == 1)
        #expect(!delegate.hasPendingUpdate)
        delegate.applyUpdateGate(runningSessions: 0, choice: nil)
        #expect(releases == 1)
    }

    @Test("wait rechecks busy to idle; Later requires explicit resume")
    func waitAndLater() throws {
        let (delegate, factory, _) = try ownedDaemon()
        var releases = 0
        delegate.postponeUpdateRelaunch { releases += 1 }
        delegate.applyUpdateGate(runningSessions: 3, choice: .whenIdle)
        #expect(releases == 0)
        #expect(factory.terminateCallCount == 0)
        delegate.applyUpdateGate(runningSessions: 3, choice: .later)
        #expect(delegate.hasPendingUpdate)
        #expect(factory.terminateCallCount == 0)
        // Production polling/resume supplies a fresh observation to this boundary.
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: true,
            runningSessions: 0, choice: .whenIdle) == .confirmRestart)
        #expect(factory.terminateCallCount == 0)
        #expect(releases == 0)
        delegate.applyUpdateGate(runningSessions: 0, choice: .restart)
        #expect(factory.terminateCallCount == 1)
        #expect(releases == 1)
    }

    @Test("fresh busy or unknown after Restart now returns to a prompt without stopping")
    func busyAfterRestartConsentReturnsToPrompt() throws {
        let (delegate, factory, handle) = try ownedDaemon()
        var releases = 0
        delegate.postponeUpdateRelaunch { releases += 1 }
        for count in [2, nil] as [Int?] {
            #expect(UpdateGate.action(ownsDaemon: true, processRunning: handle.isRunning,
                runningSessions: count, choice: .restart) == .prompt(runningSessions: count))
        }
        delegate.applyUpdateGate(runningSessions: 2, choice: .whenIdle)
        #expect(factory.terminateCallCount == 0)
        #expect(releases == 0)
        delegate.applyUpdateGate(runningSessions: nil, choice: .later)
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
    }

    @Test("Update now stops known busy work only after user choice")
    func explicitNow() throws {
        let (delegate, factory, handle) = try ownedDaemon()
        var releases = 0
        delegate.postponeUpdateRelaunch {
            #expect(!handle.isRunning)
            releases += 1
        }
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: true, runningSessions: 4)
            == .prompt(runningSessions: 4))
        #expect(factory.terminateCallCount == 0)
        delegate.applyUpdateGate(runningSessions: 4, choice: .now)
        #expect(releases == 1)
        #expect(factory.terminateCallCount == 1)
    }

    @Test("unreadable busy signal and missing process never authorize stop or install")
    func uncertaintyHolds() throws {
        let counts: [Int?] = [nil, -1]
        for count in counts {
            #expect(UpdateGate.action(ownsDaemon: true, processRunning: true, runningSessions: count)
                == .prompt(runningSessions: nil))
            #expect(UpdateGate.action(ownsDaemon: true, processRunning: true,
                runningSessions: count, choice: .now) == .prompt(runningSessions: nil))
            let (delegate, factory, _) = try ownedDaemon()
            var releases = 0
            delegate.postponeUpdateRelaunch { releases += 1 }
            delegate.applyUpdateGate(runningSessions: count, choice: .whenIdle)
            #expect(releases == 0)
            #expect(factory.terminateCallCount == 0)
            delegate.applyUpdateGate(runningSessions: nil, choice: .later)
            delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        }
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: nil, runningSessions: 0) == .wait)
        #expect(!UpdateGate.canReleaseAfterStop(sameProcess: false, processRunning: false))
        #expect(!UpdateGate.canReleaseAfterStop(sameProcess: true, processRunning: nil))
    }

    @Test("Stop incomplete retains old app and never releases even if the child exits later")
    func incompleteStop() throws {
        let (delegate, _, _) = try ownedDaemon()
        let survivor = UpdateSurvivor()
        delegate.currentHandle = survivor
        var releases = 0
        delegate.postponeUpdateRelaunch { releases += 1 }
        delegate.applyUpdateGate(runningSessions: 0, choice: .restart)
        #expect(releases == 0)
        #expect(delegate.hasPendingUpdate)
        #expect(delegate.stateText.contains("Stop incomplete"))
        #expect(survivor.terms == 1 && survivor.kills == 1)
        #expect(!delegate.canStart)
        survivor.isRunning = false
        #expect(releases == 0)
        // End only the test's pending task; no real process exists.
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
    }

    @Test("a replaced process during stop cannot release the held install")
    func replacementDuringStopHolds() throws {
        let (delegate, _, handle) = try ownedDaemon()
        let original = try #require(handle as? FakeProcessHandle)
        let replacement = FakeProcessHandle(processIdentifier: 54321)
        let oldCallback = original.onTerminate
        original.onTerminate = {
            delegate.currentHandle = replacement
            oldCallback?()
        }
        defer { original.onTerminate = nil }
        var releases = 0
        delegate.postponeUpdateRelaunch { releases += 1 }
        delegate.applyUpdateGate(runningSessions: 0, choice: .restart)
        #expect(!original.isRunning)
        #expect(replacement.isRunning)
        #expect(releases == 0)
        #expect(delegate.hasPendingUpdate)
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
    }

    @Test("external daemon and client role pass through without signals", arguments: [false, true])
    func unownedPassesThrough(client: Bool) throws {
        let saved = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer { UserDefaults.standard.set(saved, forKey: "connectionRolePreference") }
        let delegate = AppDelegate()
        let handle = FakeProcessHandle()
        delegate.currentHandle = handle
        if client {
            delegate.supervisor.configure(homeDir: "/unused")
            try delegate.supervisor.start()
            delegate.connectionRolePreference = .client
        }
        else { delegate.supervisor.onExternalDaemonDetected(pid: 12345, port: 8765) }
        var releases = 0
        delegate.postponeUpdateRelaunch { releases += 1 }
        delegate.applyUpdateGate(runningSessions: nil, choice: nil)
        #expect(releases == 1)
        #expect(handle.isRunning)
        #expect(!delegate.hasPendingUpdate)
    }

    @Test("install on quit uses the existing stop path and never invokes the relaunch block")
    func quitStopsWithoutRelaunch() throws {
        let (delegate, factory, handle) = try ownedDaemon()
        var releases = 0
        delegate.postponeUpdateRelaunch { releases += 1 }
        delegate.applyUpdateGate(runningSessions: 2, choice: .later)
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        #expect(!handle.isRunning)
        #expect(factory.terminateCallCount == 1)
        #expect(releases == 0)
        #expect(!delegate.hasPendingUpdate)
    }

    private func ownedDaemon() throws -> (AppDelegate, FakeProcessController, any ProcessHandle) {
        let delegate = AppDelegate()
        let factory = FakeProcessController()
        let handle = try factory.launch(executableURL: URL(fileURLWithPath: "/unused"),
            arguments: [], currentDirectoryURL: nil, environment: nil, terminationHandler: nil)
        delegate.currentHandle = handle
        delegate.supervisor.configure(homeDir: "/unused")
        try delegate.supervisor.start()
        delegate.supervisor.onHealthCheckPassed(pid: handle.processIdentifier, port: 8765)
        delegate.refreshDerivedState()
        return (delegate, factory, handle)
    }
}

private final class UpdateSurvivor: ProcessHandle, @unchecked Sendable {
    var isRunning = true
    let processIdentifier: Int32 = 321
    var terms = 0
    var kills = 0
    var terminationStatus: Int32 { precondition(!isRunning); return 0 }
    var terminationReason: Process.TerminationReason { .exit }
    var capturedStandardError: String? { nil }
    var capturedStandardOutput: String? { nil }
    func terminate() { terms += 1 }
    func forceKill() -> Int32? { kills += 1; return EPERM }
}
