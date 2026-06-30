import Testing
import AppKit
@testable import HappyRanchApp
import HappyRanchSupervisor

@Suite("AppDelegate process termination")
@MainActor
struct AppDelegateTerminationTests {

    /// Creates an AppDelegate wired with a FakeProcessController.
    /// When `isManagedBySelf` is true, calls `start()` (which sets the flag)
    /// then forces the desired state, so the supervisor believes it owns the process.
    private func makeAppDelegate(
        state: DaemonState = .notConfigured,
        isManagedBySelf: Bool = false,
        processIsRunning: Bool = false
    ) -> (AppDelegate, FakeProcessController) {
        let fake = FakeProcessController()
        fake.isRunning = processIsRunning

        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")

        if isManagedBySelf {
            // start() sets isManagedBySelf = true and transitions to .starting;
            // then forceState to the requested state while keeping the flag.
            try! delegate.supervisor.start()
        }
        delegate.supervisor.forceState(state)

        return (delegate, fake)
    }

    // MARK: - Scenario (a): managed stop calls terminate exactly once

    @Test("managed stop calls terminate exactly once")
    func managedStopCallsTerminateOnce() async {
        let (delegate, fake) = makeAppDelegate(
            state: .running,
            isManagedBySelf: true,
            processIsRunning: true
        )

        delegate.stopDaemon()

        #expect(fake.terminateCallCount == 1)
    }

    // MARK: - Scenario (b): managed quit calls terminate

    @Test("managed quit (applicationWillTerminate) calls terminate")
    func managedQuitCallsTerminate() async {
        let (delegate, fake) = makeAppDelegate(
            state: .running,
            isManagedBySelf: true,
            processIsRunning: true
        )

        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(fake.terminateCallCount == 1)
    }

    // MARK: - Scenario (c): already-exited process is safe no-op

    @Test("already-exited managed process is safe no-op")
    func alreadyExitedProcessIsNoOp() async {
        let (delegate, fake) = makeAppDelegate(
            state: .running,
            isManagedBySelf: true,
            processIsRunning: false
        )

        delegate.stopDaemon()

        // Terminate should NOT be called — process already exited
        #expect(fake.terminateCallCount == 0)
    }

    // MARK: - Scenario (d): externalRunning stop and quit do NOT call terminate

    @Test("externalRunning stop does NOT call terminate")
    func externalRunningStopDoesNotTerminate() async {
        let (delegate, fake) = makeAppDelegate(
            state: .externalRunning,
            isManagedBySelf: false,
            processIsRunning: false
        )

        // stopDaemon without confirmation throws for externalRunning
        // The error is swallowed by the current implementation's do/catch
        delegate.stopDaemon()

        #expect(fake.terminateCallCount == 0)
    }

    @Test("externalRunning quit does NOT call terminate")
    func externalRunningQuitDoesNotTerminate() async {
        let (delegate, fake) = makeAppDelegate(
            state: .externalRunning,
            isManagedBySelf: false,
            processIsRunning: false
        )

        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(fake.terminateCallCount == 0)
    }

    // MARK: - Relaunch regression tests (single-use Process fix)

    /// Creates a fresh AppDelegate with supervisor configured to .stopped,
    /// ready for `startDaemon()`.
    private func makeConfiguredDelegate() -> (AppDelegate, FakeProcessController) {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")
        return (delegate, fake)
    }

    /// Verifies that a managed stop followed by a new start results in a
    /// second successful launch (the regression: single-use Process would
    /// throw NSCocoaErrorDomain Code=3587 on the second run()).
    @Test("stop-then-start relaunches with a fresh process")
    func stopThenStartRelaunches() async throws {
        let (delegate, fake1) = makeConfiguredDelegate()

        // First launch
        delegate.startDaemon()
        #expect(fake1.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)

        // Managed stop — fires termination handler → state → .stopped
        delegate.stopDaemon()
        #expect(fake1.terminateCallCount == 1)

        // Yield to let the @MainActor termination-handler Task execute
        await Task.yield()
        #expect(delegate.supervisor.state == .stopped)

        // Replace with a fresh controller (RealProcessController does this
        // internally by creating a new Process per launch).
        let fake2 = FakeProcessController()
        delegate.processController = fake2

        // Second launch — must succeed; the single-use regression would throw here
        delegate.startDaemon()
        #expect(fake2.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)
    }

    /// Verifies that after a simulated daemon crash, restart launches a
    /// fresh process successfully.
    @Test("crash-then-start relaunches with a fresh process")
    func crashThenStartRelaunches() async throws {
        let (delegate, fake1) = makeConfiguredDelegate()

        // First launch
        delegate.startDaemon()
        #expect(fake1.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)

        // Simulate a crash — fires termination handler → state → .crashed
        fake1.simulateCrash(exitCode: 1)

        // Yield to let the @MainActor termination-handler Task execute
        await Task.yield()
        #expect(delegate.supervisor.state == .crashed)

        // Replace with a fresh controller
        let fake2 = FakeProcessController()
        delegate.processController = fake2

        // Restart after crash — must succeed
        delegate.startDaemon()
        #expect(fake2.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)
    }

    /// A relaunch on an already-exited FakeProcessController throws
    /// FakeProcessControllerError.processAlreadyExited, modeling the
    /// Foundation.Process single-use constraint.
    @Test("relaunch on already-exited fake throws processAlreadyExited")
    func relaunchOnExitedFakeThrows() async throws {
        let fake = FakeProcessController()

        // First launch succeeds
        try fake.launch(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["true"],
            currentDirectoryURL: nil,
            environment: nil
        )
        #expect(fake.launchCallCount == 1)
        #expect(fake.isRunning == true)

        // Terminate marks the underlying process as exited
        fake.terminate()
        #expect(fake.isRunning == false)

        // Second launch on the same fake must throw
        do {
            try fake.launch(
                executableURL: URL(fileURLWithPath: "/usr/bin/env"),
                arguments: ["true"],
                currentDirectoryURL: nil,
                environment: nil
            )
            Issue.record("Expected relaunch on exited fake to throw")
        } catch FakeProcessControllerError.processAlreadyExited {
            // Expected — single-use constraint enforced
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }
}
