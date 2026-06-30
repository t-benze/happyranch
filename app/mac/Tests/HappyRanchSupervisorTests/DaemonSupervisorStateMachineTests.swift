import Testing
@testable import HappyRanchSupervisor

@Suite("DaemonSupervisor state machine")
struct DaemonSupervisorStateMachineTests {

    // MARK: - Managed daemon transitions

    @Test("notConfigured -> stopped after configure")
    func notConfiguredToStopped() {
        let supervisor = DaemonSupervisor()
        #expect(supervisor.state == .notConfigured)

        supervisor.configure(homeDir: "/tmp/test-hr")
        #expect(supervisor.state == .stopped)
    }

    @Test("stopped -> starting -> running")
    func stoppedToStartingToRunning() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")

        // When we request a start, state should go to starting
        try supervisor.start()
        #expect(supervisor.state == .starting)

        // Simulate the health probe succeeding
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        #expect(supervisor.state == .running)
        #expect(supervisor.observedPid == 12345)
        #expect(supervisor.observedPort == 9876)
    }

    @Test("starting -> crashed when process dies during start")
    func startingToCrashed() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")

        try supervisor.start()
        #expect(supervisor.state == .starting)

        supervisor.onProcessExited(exitCode: 1, signal: nil)
        #expect(supervisor.state == .crashed)
    }

    @Test("running -> stopping -> stopped")
    func runningToStoppingToStopped() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        #expect(supervisor.state == .running)

        try supervisor.stop()
        #expect(supervisor.state == .stopping)

        supervisor.onProcessExited(exitCode: 0, signal: nil)
        #expect(supervisor.state == .stopped)
    }

    @Test("running -> crashed on unexpected exit")
    func runningToCrashed() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        #expect(supervisor.state == .running)

        supervisor.onProcessExited(exitCode: 1, signal: nil)
        #expect(supervisor.state == .crashed)
    }

    @Test("running -> unhealthy -> running (recovery)")
    func runningToUnhealthyToRunning() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        #expect(supervisor.state == .running)

        supervisor.onHealthCheckFailed()
        #expect(supervisor.state == .unhealthy)

        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        #expect(supervisor.state == .running)
    }

    @Test("unhealthy -> crashed on process exit")
    func unhealthyToCrashed() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        supervisor.onHealthCheckFailed()
        #expect(supervisor.state == .unhealthy)

        supervisor.onProcessExited(exitCode: 1, signal: nil)
        #expect(supervisor.state == .crashed)
    }

    @Test("crashed -> starting on restart")
    func crashedToStarting() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        try supervisor.start()
        supervisor.onHealthCheckPassed(pid: 12345, port: 9876)
        supervisor.onProcessExited(exitCode: 1, signal: nil)
        #expect(supervisor.state == .crashed)

        try supervisor.start()
        #expect(supervisor.state == .starting)
    }

    // MARK: - Stale PID handling

    @Test("stalePid when PID file references dead process")
    func stalePidDetection() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")

        // Simulate discovering a PID file with a dead process
        supervisor.onStalePidDetected(pid: 99999)
        #expect(supervisor.state == .stalePid)
    }

    @Test("stalePid -> starting after cleanup")
    func stalePidToStarting() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        supervisor.onStalePidDetected(pid: 99999)
        #expect(supervisor.state == .stalePid)

        try supervisor.start()
        #expect(supervisor.state == .starting)
    }

    // MARK: - External daemon handling

    @Test("external daemon: notConfigured -> externalRunning on detection")
    func externalRunningDetection() {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")

        supervisor.onExternalDaemonDetected(pid: 55555, port: 8080)
        #expect(supervisor.state == .externalRunning)
        #expect(supervisor.isManagedBySelf == false)
    }

    @Test("external daemon: stop is rejected without confirmation")
    func externalStopRejected() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        supervisor.onExternalDaemonDetected(pid: 55555, port: 8080)

        do {
            try supervisor.stop()
            Issue.record("Expected stop to throw for external daemon")
        } catch DaemonSupervisorError.externalStopRequiresConfirmation {
            // Expected
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    @Test("external daemon: stop succeeds after confirmation")
    func externalStopAfterConfirmation() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        supervisor.onExternalDaemonDetected(pid: 55555, port: 8080)

        try supervisor.stop(confirmed: true)
        #expect(supervisor.state == .stopping)
    }

    // MARK: - Invalid transitions

    @Test("stop is a no-op when already stopped")
    func stopWhenStopped() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        #expect(supervisor.state == .stopped)

        try supervisor.stop()
        #expect(supervisor.state == .stopped)
    }

    @Test("start is rejected in terminal states")
    func startRejectedInTerminal() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/tmp/test-hr")
        supervisor.forceState(.failed)

        do {
            try supervisor.start()
            Issue.record("Expected start to throw in failed state")
        } catch DaemonSupervisorError.invalidStateTransition {
            // Expected
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }
}
