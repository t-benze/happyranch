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
    ///
    /// The returned `activeHandle` is the handle that `AppDelegate.currentHandle`
    /// will point to — set up via a launch through the fake so the handle
    /// lifecycle is modeled correctly.
    private func makeAppDelegate(
        state: DaemonState = .notConfigured,
        isManagedBySelf: Bool = false,
        processIsRunning: Bool = false
    ) -> (AppDelegate, FakeProcessController, FakeProcessHandle) {
        let fake = FakeProcessController()

        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")

        if isManagedBySelf {
            // start() sets isManagedBySelf = true and transitions to .starting;
            // then forceState to the requested state while keeping the flag.
            try! delegate.supervisor.start()
        }
        delegate.supervisor.forceState(state)

        // Set up a currentHandle via the fake's launch so the AppDelegate
        // has a handle to read isRunning/terminate from.
        let handle = try! fake.launch(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["test"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        ) as! FakeProcessHandle

        // Reset call counters since this was setup, not a test action.
        fake.launchCallCount = 0
        fake.terminateCallCount = 0

        if !processIsRunning {
            handle.terminate()
            // onTerminate incremented terminateCallCount; reset it.
            fake.terminateCallCount = 0
        }

        // Wire the handle into the AppDelegate's currentHandle slot.
        delegate.currentHandle = handle

        return (delegate, fake, handle)
    }

    // MARK: - Scenario (a): managed stop calls terminate exactly once

    @Test("managed stop calls terminate exactly once")
    func managedStopCallsTerminateOnce() async {
        let (delegate, fake, _) = makeAppDelegate(
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
        let (delegate, fake, _) = makeAppDelegate(
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
        let (delegate, fake, _) = makeAppDelegate(
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
        let (delegate, fake, _) = makeAppDelegate(
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
        let (delegate, fake, _) = makeAppDelegate(
            state: .externalRunning,
            isManagedBySelf: false,
            processIsRunning: false
        )

        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))

        #expect(fake.terminateCallCount == 0)
    }

    // MARK: - Regression: controller-as-factory (Finding 1 fix)

    /// Creates a fresh AppDelegate with supervisor configured to .stopped,
    /// ready for `startDaemon()`.
    private func makeConfiguredDelegate() -> (AppDelegate, FakeProcessController) {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")
        return (delegate, fake)
    }

    /// Verifies that a managed stop followed by a new start uses the SAME
    /// controller (no swapping) and the controller vends a fresh handle.
    ///
    /// This test FAILS if the production controller reuses one underlying
    /// process (the old self.process-slot design) and PASSES only with
    /// fresh-per-launch factory behavior.
    @Test("stop-then-start relaunches with same controller, fresh handle")
    func stopThenStartRelaunchesSameController() async throws {
        let (delegate, fake) = makeConfiguredDelegate()

        // First launch
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)
        let firstHandle = fake.activeHandle
        #expect(firstHandle != nil)

        // Managed stop — fires termination handler → state → .stopped
        delegate.stopDaemon()
        #expect(fake.terminateCallCount == 1)

        // Yield to let the @MainActor termination-handler Task execute
        await Task.yield()
        #expect(delegate.supervisor.state == .stopped)

        // Second launch — SAME controller, must vend a FRESH handle.
        // The regression: if the controller reused the old handle or one
        // underlying process, this would throw processAlreadyExited.
        delegate.startDaemon()
        #expect(fake.launchCallCount == 2, "Second launch should succeed with same controller")
        #expect(delegate.supervisor.state == .starting)
        let secondHandle = fake.activeHandle
        #expect(secondHandle != nil)
        #expect(firstHandle !== secondHandle, "Controller must vend a fresh handle per launch")
    }

    /// Verifies that after a simulated daemon crash, restart uses the SAME
    /// controller and vends a fresh handle.
    @Test("crash-then-start relaunches with same controller, fresh handle")
    func crashThenStartRelaunchesSameController() async throws {
        let (delegate, fake) = makeConfiguredDelegate()

        // First launch
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        #expect(delegate.supervisor.state == .starting)
        let firstHandle = fake.activeHandle
        #expect(firstHandle != nil)

        // Simulate a crash — fires termination handler → state → .crashed
        fake.simulateCrash(exitCode: 1)

        // Yield to let the @MainActor termination-handler Task execute
        await Task.yield()
        #expect(delegate.supervisor.state == .crashed)

        // Restart after crash — SAME controller, must vend a FRESH handle
        delegate.startDaemon()
        #expect(fake.launchCallCount == 2, "Restart should succeed with same controller")
        #expect(delegate.supervisor.state == .starting)
        let secondHandle = fake.activeHandle
        #expect(secondHandle != nil)
        #expect(firstHandle !== secondHandle, "Controller must vend a fresh handle after crash")
    }

    /// A relaunch on an already-exited FakeProcessHandle throws
    /// FakeProcessControllerError.processAlreadyExited, modeling the
    /// Foundation.Process single-use constraint.
    @Test("relaunch on already-exited handle throws processAlreadyExited")
    func relaunchOnExitedHandleThrows() async throws {
        let fake = FakeProcessController()

        // First launch succeeds
        let handle1 = try fake.launch(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["true"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        ) as! FakeProcessHandle
        #expect(fake.launchCallCount == 1)
        #expect(handle1.isRunning == true)

        // Terminate marks the underlying handle as exited
        handle1.terminate()
        #expect(handle1.isRunning == false)

        // Second launch on the same controller must succeed (factory behavior)
        let handle2 = try fake.launch(
            executableURL: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["true"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        ) as! FakeProcessHandle
        #expect(fake.launchCallCount == 2)
        #expect(handle1 !== handle2, "Second launch must return a fresh handle")

        // But the first handle must reject reuse
        do {
            try handle1.assertNotExited()
            Issue.record("Expected assertion on exited handle to throw")
        } catch FakeProcessControllerError.processAlreadyExited {
            // Expected — single-use constraint enforced
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    // MARK: - Immediate exit during launch (REVISE finding — ordering race)

    /// Verifies that when the managed daemon exits immediately during launch
    /// (between process.run() and AppDelegate wiring the termination handler),
    /// the supervisor reaches .crashed, NOT .starting.
    ///
    /// RED against the current launch-then-register-termination-handler ordering
    /// (the handler is nil when the immediate exit fires, so the exit is
    /// dropped and the supervisor stays .starting). GREEN only after the handler
    /// is registered atomically with launch (passed as a parameter and wired
    /// before process.run()).
    @Test("immediate exit during launch reaches .crashed, not .starting")
    func immediateExitDuringLaunch() async throws {
        let fake = FakeProcessController()
        fake.simulateImmediateExitOnNextLaunch = true
        fake.immediateExitCode = 1

        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")

        delegate.startDaemon()

        // Yield to let any @MainActor Tasks execute
        await Task.yield()

        #expect(fake.launchCallCount == 1)
        #expect(delegate.supervisor.state == .crashed,
                "Immediate exit during launch must reach .crashed, got \(delegate.supervisor.state)")
    }

    // MARK: - Stale callback regression (Finding 2 fix)

    /// Verifies that a stale exit callback from a PRIOR launch does NOT
    /// stop/crash the new daemon.
    ///
    /// This test is RED against the old self.process-slot implementation
    /// (where terminationStatus/terminationReason read the mutable current
    /// process slot) and GREEN only when the terminationHandler delivers a
    /// per-launch handle AND AppDelegate guards by handle identity.
    @Test("stale exit callback from prior launch does not affect new daemon")
    func staleExitCallbackDoesNotStopNewDaemon() async throws {
        let (delegate, fake) = makeConfiguredDelegate()

        // Launch A
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        let handleA = fake.activeHandle!
        let pidA = handleA.processIdentifier

        // Simulate A's daemon exiting (fire termination callback)
        fake.simulateCrash(exitCode: 1)
        await Task.yield()
        #expect(delegate.supervisor.state == .crashed, "Should be crashed after first daemon exit")

        // Launch B (fresh lifecycle) — SAME controller, fresh handle
        delegate.startDaemon()
        #expect(fake.launchCallCount == 2)
        let handleB = fake.activeHandle!
        let pidB = handleB.processIdentifier
        #expect(pidA != pidB, "Second launch must have different PID")
        #expect(delegate.supervisor.state == .starting)

        // Now fire A's OLD termination callback AFTER B is live.
        // The guard in AppDelegate's terminationHandler must reject this
        // stale callback because handleA.processIdentifier != currentHandle.processIdentifier.
        fake.fireTermination(for: handleA)
        await Task.yield()

        // B must still be in the starting lifecycle — the stale callback
        // must NOT have driven supervisor.onProcessExited for B.
        #expect(delegate.supervisor.state == .starting,
                "Stale callback from handle A must not stop/crash the new daemon (handle B)")
    }

    /// Variant: stale callback from handle A after B has reached .running.
    @Test("stale exit callback does not crash running daemon")
    func staleExitCallbackDoesNotCrashRunningDaemon() async throws {
        let (delegate, fake) = makeConfiguredDelegate()

        // Launch A
        delegate.startDaemon()
        let handleA = fake.activeHandle!

        // Simulate A crash
        fake.simulateCrash(exitCode: 1)
        await Task.yield()
        #expect(delegate.supervisor.state == .crashed)

        // Launch B and transition to running
        delegate.startDaemon()
        _ = fake.activeHandle!
        delegate.supervisor.forceState(.running)
        #expect(delegate.supervisor.state == .running)

        // Fire A's stale callback
        fake.fireTermination(for: handleA)
        await Task.yield()

        // B must remain running
        #expect(delegate.supervisor.state == .running,
                "Stale callback from handle A must not crash the running daemon (handle B)")
    }
}
