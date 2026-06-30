import Testing
@testable import HappyRanchSupervisor

@Suite("DaemonState enum")
struct DaemonStateTests {

    @Test("all states are defined")
    func allStatesDefined() {
        let states: [DaemonState] = [
            .notConfigured,
            .stopped,
            .externalRunning,
            .starting,
            .running,
            .unhealthy,
            .stalePid,
            .stopping,
            .crashed,
            .failed
        ]
        #expect(states.count == 10)
    }

    @Test("terminal states are correctly identified")
    func terminalStates() {
        // crashed, failed are terminal (cannot transition out without restart)
        #expect(DaemonState.crashed.isTerminal)
        #expect(DaemonState.failed.isTerminal)
        #expect(!DaemonState.running.isTerminal)
        #expect(!DaemonState.stopped.isTerminal)
        #expect(!DaemonState.starting.isTerminal)
        #expect(!DaemonState.stopping.isTerminal)
        #expect(!DaemonState.unhealthy.isTerminal)
        #expect(!DaemonState.externalRunning.isTerminal)
        #expect(!DaemonState.stalePid.isTerminal)
        #expect(!DaemonState.notConfigured.isTerminal)
    }

    @Test("running states (daemon is alive)")
    func runningStates() {
        #expect(DaemonState.running.isRunning)
        #expect(DaemonState.externalRunning.isRunning)
        #expect(!DaemonState.stopped.isRunning)
        #expect(!DaemonState.starting.isRunning)
        #expect(!DaemonState.crashed.isRunning)
    }
}
