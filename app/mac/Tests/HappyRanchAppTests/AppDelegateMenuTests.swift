import Testing
import AppKit
import Foundation
@testable import HappyRanchApp
import HappyRanchSupervisor

// MARK: - Helpers

/// Creates an AppDelegate wired with a FakeProcessController, with the
/// supervisor configured and a currentHandle already set up via the fake's
/// launch().  The returned handle models a running managed process unless
/// `processIsRunning` is false.
///
/// The AppDelegate's `refreshDerivedState()` is called so derived
/// Published properties (canStart, canStop, canRestart) reflect the
/// supervisor state.
@MainActor
private func makeAppDelegateForMenu(
    state: DaemonState,
    isManagedBySelf: Bool = false,
    processIsRunning: Bool = false
) -> (AppDelegate, FakeProcessController, FakeProcessHandle) {
    let fake = FakeProcessController()

    let delegate = AppDelegate()
    delegate.processController = fake
    delegate.supervisor.configure(homeDir: "/tmp/test-hr-menu")

    if isManagedBySelf {
        try! delegate.supervisor.start()
    }
    delegate.supervisor.forceState(state)

    let handle = try! fake.launch(
        executableURL: URL(fileURLWithPath: "/usr/bin/env"),
        arguments: ["test"],
        currentDirectoryURL: nil,
        environment: nil,
        terminationHandler: nil
    ) as! FakeProcessHandle

    // Reset counters — setup, not test action.
    fake.launchCallCount = 0
    fake.terminateCallCount = 0

    if !processIsRunning {
        handle.terminate()
        fake.terminateCallCount = 0
    }

    delegate.currentHandle = handle
    delegate.refreshDerivedState()
    return (delegate, fake, handle)
}

// MARK: - Suite (a): derived enable/disable across supervisor states

@Suite("Menu command enable/disable derivation")
@MainActor
struct MenuCommandDerivationTests {

    /// Verifies canStart / canStop / canRestart for every supervisor state
    /// reachable by the menu, across both managed and external ownership.
    ///
    /// canStart = .stopped, .crashed, .stalePid
    /// canStop  = .running, .unhealthy, .starting
    /// canRestart = canStop && isManagedBySelf
    @Test("derived booleans across all supervisor states, managed and external",
          arguments: [
            // (state,         managed, canStart, canStop, canRestart)
            (DaemonState.notConfigured, false, false, false, false),
            (DaemonState.stopped,       false, true,  false, false),
            (DaemonState.stopped,       true,  true,  false, false),
            (DaemonState.externalRunning, false, false, false, false),
            (DaemonState.starting,      false, false, true,  false),
            (DaemonState.starting,      true,  false, true,  true),
            (DaemonState.running,       false, false, true,  false),
            (DaemonState.running,       true,  false, true,  true),
            (DaemonState.unhealthy,     false, false, true,  false),
            (DaemonState.unhealthy,     true,  false, true,  true),
            (DaemonState.stalePid,      false, true,  false, false),
            (DaemonState.stopping,      true,  false, false, false),
            (DaemonState.crashed,       true,  true,  false, false),
            (DaemonState.failed,        false, false, false, false),
          ] as [(DaemonState, Bool, Bool, Bool, Bool)])
    func derivedBooleans(
        state: DaemonState,
        managed: Bool,
        expectCanStart: Bool,
        expectCanStop: Bool,
        expectCanRestart: Bool
    ) async {
        let (delegate, _, _) = makeAppDelegateForMenu(
            state: state,
            isManagedBySelf: managed,
            processIsRunning: expectCanStop
        )

        #expect(delegate.canStart == expectCanStart,
                "state=\(state) managed=\(managed): canStart expected \(expectCanStart), got \(delegate.canStart)")
        #expect(delegate.canStop == expectCanStop,
                "state=\(state) managed=\(managed): canStop expected \(expectCanStop), got \(delegate.canStop)")
        #expect(delegate.canRestart == expectCanRestart,
                "state=\(state) managed=\(managed): canRestart expected \(expectCanRestart), got \(delegate.canRestart)")
    }
}

// MARK: - Suite (b): restart is two-phase (red→green test)

@Suite("Restart two-phase (FIX 1 verification)")
@MainActor
struct RestartTwoPhaseTests {

    /// Proves that restartDaemon() increments launchCallCount 1→2 ONLY
    /// after the managed process's termination settles asynchronously.
    ///
    /// RED against the current stop-only bug (the synchronous canStart
    /// check after stopDaemon() is always false because the supervisor
    /// is still .stopping).  GREEN after FIX 1 because the termination
    /// handler checks pendingRestart and issues the follow-on start.
    ///
    /// Uses startDaemon() to properly wire the termination handler
    /// (through launchDaemonProcess) before exercising restart.
    @Test("restart increments launchCallCount 1→2 after termination settles")
    func restartIncrementsLaunchCountAfterTerminationSettles() async throws {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-restart")

        // First launch — wires the real terminationHandler into the fake.
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)

        // Simulate health-check success to reach .running.
        delegate.supervisor.onHealthCheckPassed(
            pid: fake.activeHandle!.processIdentifier,
            port: 9876
        )
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .running)
        #expect(delegate.canRestart == true)

        // Issue the restart.
        delegate.restartDaemon()

        // Immediately after restartDaemon() returns, the daemon has been
        // stopped (terminate fired) but the termination handler's MainActor
        // task has NOT run yet — the follow-on start hasn't happened.
        #expect(fake.terminateCallCount >= 1,
                "restartDaemon must have terminated the managed process")

        // Yield so the termination handler's @MainActor Task executes.
        // This is where the pending-start fires.
        await Task.yield()

        // Now the restart follow-on start MUST have happened.
        // With the old stop-only bug this would still be 1 (RED);
        // with FIX 1 it's 2 (GREEN).
        #expect(fake.launchCallCount == 2,
                "After termination settles, launchCallCount must be 2 (first start + restart follow-on); got \(fake.launchCallCount).  RED = stop-only bug drops the restart; GREEN = termination-completion path fires the pending start.")
    }

    /// Restart from a stopped (already-not-running) daemon is a no-op
    /// because canRestart is false when canStop is false.
    @Test("restartDaemon is a no-op when daemon is not running")
    func restartIsNoOpWhenNotRunning() async {
        let (delegate, fake, _) = makeAppDelegateForMenu(
            state: .stopped,
            isManagedBySelf: true,
            processIsRunning: false
        )

        #expect(delegate.canRestart == false)

        delegate.restartDaemon()

        // No terminate, no launch — restartDaemon guards on canStop
        #expect(fake.terminateCallCount == 0)
        #expect(fake.launchCallCount == 0)
    }
}

// MARK: - Suite (c): external-daemon guard

@Suite("External daemon guard")
@MainActor
struct ExternalDaemonGuardTests {

    @Test("externalRunning stop (unconfirmed) does NOT terminate")
    func externalStopUnconfirmedDoesNotTerminate() async {
        let (delegate, fake, _) = makeAppDelegateForMenu(
            state: .externalRunning,
            isManagedBySelf: false,
            processIsRunning: false
        )

        delegate.stopDaemon() // unconfirmed → throws externalStopRequiresConfirmation

        #expect(fake.terminateCallCount == 0,
                "External daemon must never be terminated on unconfirmed stop")
    }

    @Test("externalRunning restartDaemon is a no-op")
    func externalRestartIsNoOp() async {
        let (delegate, fake, _) = makeAppDelegateForMenu(
            state: .externalRunning,
            isManagedBySelf: false,
            processIsRunning: false
        )

        delegate.restartDaemon()

        // The guard `supervisor.isManagedBySelf, canStop` rejects the restart
        // because canStop is false for .externalRunning.
        #expect(fake.terminateCallCount == 0,
                "restartDaemon must never terminate an external daemon")
        #expect(fake.launchCallCount == 0,
                "restartDaemon must never launch over an external daemon")
    }

    @Test("external-daemon quit does NOT terminate")
    func externalQuitDoesNotTerminate() async {
        let (delegate, fake, _) = makeAppDelegateForMenu(
            state: .externalRunning,
            isManagedBySelf: false,
            processIsRunning: false
        )

        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(fake.terminateCallCount == 0,
                "applicationWillTerminate must never terminate an external daemon")
    }
}

// MARK: - Suite (d): Show Diagnostics toggle

@Suite("Show Diagnostics toggle")
@MainActor
struct ShowDiagnosticsToggleTests {

    @Test("showDiagnostics toggles from false to true")
    func togglesFromFalse() async {
        let (delegate, _, _) = makeAppDelegateForMenu(
            state: .stopped,
            isManagedBySelf: false,
            processIsRunning: false
        )

        #expect(delegate.showDiagnostics == false)

        delegate.showDiagnostics = true

        #expect(delegate.showDiagnostics == true,
                "showDiagnostics must be settable to true")
    }

    @Test("showDiagnostics toggles back from true to false")
    func togglesBackToFalse() async {
        let (delegate, _, _) = makeAppDelegateForMenu(
            state: .stopped,
            isManagedBySelf: false,
            processIsRunning: false
        )
        delegate.showDiagnostics = true
        #expect(delegate.showDiagnostics == true)

        delegate.showDiagnostics = false
        #expect(delegate.showDiagnostics == false,
                "showDiagnostics must be settable back to false")
    }

    @Test("showDiagnostics does not affect supervisor state")
    func doesNotAffectSupervisorState() async {
        let (delegate, _, _) = makeAppDelegateForMenu(
            state: .running,
            isManagedBySelf: true,
            processIsRunning: true
        )

        let beforeState = delegate.supervisor.state
        delegate.showDiagnostics = true

        #expect(delegate.supervisor.state == beforeState,
                "Toggling showDiagnostics must not change supervisor state")
    }
}
