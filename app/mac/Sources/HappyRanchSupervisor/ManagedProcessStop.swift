import Foundation

/// A signal request is never an exit observation. Retain ownership on timeout.
public enum ManagedProcessStop {
    /// Production defaults remain sixty seconds for TERM and two for KILL.
    /// Fake-child fixtures can inject shorter waits without changing outcomes.
    public struct Grace: Sendable {
        public let term: TimeInterval
        public let kill: TimeInterval
        public init(term: TimeInterval = 60, kill: TimeInterval = 2) {
            self.term = term; self.kill = kill
        }
    }

    public enum Outcome: Equatable {
        case exited(exitCode: Int32, signal: Int32?)
        case stillRunning(killError: Int32?)
    }

    /// Allow the daemon sixty seconds of TERM grace, then allow two seconds for KILL.
    /// The main run loop stays available for the process termination callback.
    @MainActor
    public static func stop(_ handle: any ProcessHandle, grace: Grace = .init()) -> Outcome {
        if handle.isRunning {
            handle.terminate()
            waitForExit(handle, seconds: grace.term)
        }
        var killError: Int32?
        if handle.isRunning {
            killError = handle.forceKill()
            waitForExit(handle, seconds: grace.kill)
        }
        guard !handle.isRunning else { return .stillRunning(killError: killError) }
        let status = handle.terminationStatus
        return .exited(exitCode: status,
                       signal: handle.terminationReason == .uncaughtSignal ? status : nil)
    }

    @MainActor
    private static func waitForExit(_ handle: any ProcessHandle, seconds: TimeInterval) {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        while handle.isRunning {
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0 else { return }
            RunLoop.current.run(until: Date(timeIntervalSinceNow: min(0.1, remaining)))
        }
    }
}
