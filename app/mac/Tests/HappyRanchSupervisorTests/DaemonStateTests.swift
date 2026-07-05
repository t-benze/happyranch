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

    // MARK: - canStop predicate

    @Test("canStop: managed running/unhealthy/starting are stoppable")
    func canStopManagedRunningStates() {
        #expect(DaemonState.running.canStop)
        #expect(DaemonState.unhealthy.canStop)
        #expect(DaemonState.starting.canStop)
    }

    @Test("canStop: externalRunning is NOT stoppable")
    func canStopExternalRunning() {
        #expect(!DaemonState.externalRunning.canStop)
    }

    @Test("canStop: stopped and terminal states are NOT stoppable")
    func canStopStoppedAndTerminal() {
        #expect(!DaemonState.stopped.canStop)
        #expect(!DaemonState.notConfigured.canStop)
        #expect(!DaemonState.crashed.canStop)
        #expect(!DaemonState.failed.canStop)
        #expect(!DaemonState.stalePid.canStop)
        #expect(!DaemonState.stopping.canStop)
    }
}
