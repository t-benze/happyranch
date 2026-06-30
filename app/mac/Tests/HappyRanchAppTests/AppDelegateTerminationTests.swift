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
}
