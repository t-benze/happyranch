import Foundation

/// Errors thrown by DaemonSupervisor.
public enum DaemonSupervisorError: Error, Equatable {
    case notConfigured
    case invalidStateTransition(from: DaemonState, to: DaemonState)
    case externalStopRequiresConfirmation
    case processLaunchFailed(String)
    case processAlreadyRunning
}

/// Supervises the HappyRanch daemon lifecycle.
///
/// Manages a state machine, process lifecycle, and health monitoring.
/// Distinguishes between app-managed and externally-started daemons.
public final class DaemonSupervisor: @unchecked Sendable {

    // MARK: - State

    /// Current daemon lifecycle state.
    public private(set) var state: DaemonState = .notConfigured

    /// Whether this supervisor launched the current daemon process.
    public private(set) var isManagedBySelf: Bool = false

    /// PID of the observed daemon process (nil if not running or unknown).
    public private(set) var observedPid: Int32?

    /// Port the daemon is listening on.
    public private(set) var observedPort: UInt16?

    /// Path to the daemon home directory (~/.happyranch/ or custom).
    public private(set) var homeDir: String?

    /// Last observed process exit code.
    public private(set) var lastExitCode: Int32?

    /// Last observed signal that killed the process.
    public private(set) var lastExitSignal: Int32?

    /// Start command used to launch the daemon.
    public private(set) var startCommand: String?

    // MARK: - Init

    public init() {}

    // MARK: - Configuration

    /// Configure the supervisor with a daemon home directory.
    /// Moves from `.notConfigured` to `.stopped`.
    public func configure(homeDir: String) {
        self.homeDir = homeDir
        transition(to: .stopped)
    }

    // MARK: - Managed lifecycle

    /// Start the daemon process (app-managed).
    /// Moves from `.stopped`, `.crashed`, or `.stalePid` to `.starting`.
    public func start() throws {
        guard state != .notConfigured else {
            throw DaemonSupervisorError.notConfigured
        }

        switch state {
        case .stopped, .crashed, .stalePid:
            break
        case .running, .starting, .externalRunning, .unhealthy:
            throw DaemonSupervisorError.processAlreadyRunning
        case .stopping:
            throw DaemonSupervisorError.invalidStateTransition(from: state, to: .starting)
        case .failed:
            throw DaemonSupervisorError.invalidStateTransition(from: state, to: .starting)
        case .notConfigured:
            throw DaemonSupervisorError.notConfigured
        }

        isManagedBySelf = true
        transition(to: .starting)
    }

    /// Stop the daemon process.
    /// - Parameter confirmed: Required when stopping an externally-managed daemon.
    public func stop(confirmed: Bool = false) throws {
        guard state != .notConfigured else {
            throw DaemonSupervisorError.notConfigured
        }

        switch state {
        case .stopped:
            // Already stopped — no-op
            return
        case .externalRunning:
            guard confirmed else {
                throw DaemonSupervisorError.externalStopRequiresConfirmation
            }
            transition(to: .stopping)
            isManagedBySelf = true  // We're taking over for the stop
        case .running, .unhealthy, .starting:
            transition(to: .stopping)
        case .crashed, .stalePid, .failed:
            // Already down — clean up to stopped
            transition(to: .stopped)
            isManagedBySelf = false
        case .stopping:
            break // Already stopping
        case .notConfigured:
            throw DaemonSupervisorError.notConfigured
        }
    }

    // MARK: - External daemon detection

    /// Called when an external (not app-launched) daemon is detected.
    public func onExternalDaemonDetected(pid: Int32, port: UInt16) {
        observedPid = pid
        observedPort = port
        isManagedBySelf = false
        transition(to: .externalRunning)
    }

    // MARK: - Health check events

    /// Called when a health check against the daemon succeeds.
    public func onHealthCheckPassed(pid: Int32, port: UInt16) {
        observedPid = pid
        observedPort = port

        switch state {
        case .starting, .unhealthy:
            transition(to: .running)
        case .running:
            break // Already healthy
        case .stopped, .stalePid:
            // Might have been started externally while we were idle
            isManagedBySelf = false
            transition(to: .externalRunning)
        default:
            break
        }
    }

    /// Called when a health check against the daemon fails.
    public func onHealthCheckFailed() {
        switch state {
        case .running:
            transition(to: .unhealthy)
        case .starting:
            // Still in startup grace period — stay in starting
            break
        default:
            break
        }
    }

    // MARK: - Process lifecycle events

    /// Called when the daemon process exits.
    public func onProcessExited(exitCode: Int32, signal: Int32?) {
        lastExitCode = exitCode
        lastExitSignal = signal

        switch state {
        case .starting, .running, .unhealthy:
            if exitCode == 0 && signal == nil {
                transition(to: .stopped)
            } else {
                transition(to: .crashed)
            }
        case .stopping:
            transition(to: .stopped)
            isManagedBySelf = false
        case .externalRunning:
            // External daemon exited — go to stopped
            transition(to: .stopped)
            isManagedBySelf = false
            observedPid = nil
            observedPort = nil
        default:
            break
        }
    }

    // MARK: - Stale PID

    /// Called when a PID file exists but the referenced process is dead.
    public func onStalePidDetected(pid: Int32) {
        observedPid = pid
        transition(to: .stalePid)
    }

    // MARK: - Diagnostics

    /// Forces a specific state (for testing and escalation).
    public func forceState(_ newState: DaemonState) {
        state = newState
    }

    // MARK: - Private helpers

    private func transition(to newState: DaemonState) {
        state = newState
    }
}
