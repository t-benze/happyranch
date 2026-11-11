import Testing
@testable import HappyRanchSupervisor

@Suite("Managed health monitoring schedule")
struct ManagedHealthPollingTests {
    @Test("healthy monitoring survives startup budget, reports failure, and recovers")
    func keepsMonitoringRunningDaemon() throws {
        let supervisor = DaemonSupervisor()
        supervisor.configure(homeDir: "/unused")
        try supervisor.start()
        var polling = ManagedHealthPolling()
        #expect(polling.nextDelay(state: supervisor.state) == 500_000_000)
        supervisor.onHealthCheckPassed(pid: 42, port: 1234)
        for _ in 0..<35 {
            #expect(polling.nextDelay(state: supervisor.state) == 5_000_000_000)
        }
        supervisor.onHealthCheckFailed()
        #expect(supervisor.state == .unhealthy)
        #expect(polling.nextDelay(state: supervisor.state) == 5_000_000_000)
        supervisor.onHealthCheckPassed(pid: 42, port: 1234)
        #expect(supervisor.state == .running)
        #expect(polling.nextDelay(state: supervisor.state) == 5_000_000_000)
        try supervisor.stop()
        #expect(polling.nextDelay(state: supervisor.state) == nil)
    }

    @Test("startup remains bounded and inactive states end monitoring")
    func startupAndTerminalBounds() {
        var polling = ManagedHealthPolling()
        for _ in 0..<30 {
            #expect(polling.nextDelay(state: .starting) == 500_000_000)
        }
        #expect(polling.nextDelay(state: .starting) == nil)
        for state in [DaemonState.notConfigured, .stopped, .stopping, .crashed,
                      .failed, .stalePid, .externalRunning] {
            var fresh = ManagedHealthPolling()
            #expect(fresh.nextDelay(state: state) == nil)
        }
    }
}
