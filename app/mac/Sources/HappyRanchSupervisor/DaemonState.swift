import Foundation

/// All possible states of the HappyRanch daemon lifecycle.
public enum DaemonState: String, Sendable, Equatable, CustomStringConvertible {
    /// No home directory has been configured yet.
    case notConfigured

    /// Configured but daemon is not running.
    case stopped

    /// A daemon is running but was NOT started by this app (external process).
    case externalRunning

    /// App-initiated launch in progress — process spawned, waiting for health check.
    case starting

    /// Daemon is running and health check is passing.
    case running

    /// Daemon process is alive but health check is failing (transient).
    case unhealthy

    /// A PID file exists but the referenced process is dead (stale state file).
    case stalePid

    /// App-initiated shutdown in progress — SIGTERM sent, waiting for exit.
    case stopping

    /// Daemon exited unexpectedly (non-zero exit code or signal).
    case crashed

    /// The daemon failed to start or encountered an unrecoverable error.
    case failed

    // MARK: - Computed properties

    /// Whether this state indicates the daemon process is alive.
    public var isRunning: Bool {
        switch self {
        case .running, .externalRunning, .unhealthy:
            return true
        default:
            return false
        }
    }

    /// Whether this is a terminal state that requires explicit restart.
    public var isTerminal: Bool {
        switch self {
        case .crashed, .failed:
            return true
        default:
            return false
        }
    }

    public var description: String { rawValue }
}
