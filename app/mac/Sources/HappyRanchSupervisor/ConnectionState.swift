import Foundation

/// Connection state for the BYO-Tailscale remote-connection surface.
///
/// Mirrors the shipped ``DaemonState`` state-machine pattern:
/// a `String`/`Sendable`/`Equatable`/`CustomStringConvertible` enum
/// with computed-property predicates and a companion state manager
/// (``ConnectionStateManager``) that owns the transition logic.
///
/// This is **scaffolding** — the state model, transitions, and
/// a minimal view-binding shape. Phase A1 includes NO remote wiring,
/// NO daemon traffic, and NO connector/bridge code.
public enum ConnectionState: String, Sendable, Equatable, CustomStringConvertible {
    /// Tailscale is not running or its LocalAPI socket is unreachable.
    /// Shown to the user as: "Tailscale not detected — start Tailscale to connect".
    case tailnetNotDetected

    /// Tailscale is detected and the home node is known, but the
    /// connection is not currently active.
    case offline

    /// A connection is established and heartbeats are succeeding.
    case online

    /// The connection was lost and automatic reconnection is in progress.
    case reconnecting

    /// The paired home node is unreachable after reconnection attempts
    /// have timed out.  Requires user intervention or manual retry.
    case pairedUnreachable

    // MARK: - Computed properties

    /// Whether this state indicates a live connection to the home node.
    public var isConnected: Bool {
        self == .online
    }

    /// Whether this is a transient state where the system is actively
    /// trying to establish or restore a connection.
    public var isTransient: Bool {
        switch self {
        case .online, .reconnecting:
            return true
        default:
            return false
        }
    }

    public var description: String { rawValue }

    /// User-facing display message for the connection-degradation surface.
    /// Returns a human-readable string suitable for the status bar,
    /// menu-bar extra, and connection-health popover.
    public var displayMessage: String {
        switch self {
        case .tailnetNotDetected:
            return "Tailscale not detected \u{2014} start Tailscale to connect"
        case .offline:
            return "Disconnected \u{2014} home node offline"
        case .online:
            return "Connected to home node"
        case .reconnecting:
            return "Reconnecting to home node\u{2026}"
        case .pairedUnreachable:
            return "Home node unreachable \u{2014} check connection"
        }
    }
}

// MARK: - ConnectionStateManager

/// Manages the remote-connection state machine and last-heartbeat timestamp.
///
/// Mirrors the shipped ``DaemonSupervisor`` pattern: an `@unchecked Sendable`
/// class that owns the current ``ConnectionState``, exposes event-driven
/// transition methods, and maintains a `lastHeartbeat` timestamp.
///
/// Phase A1 scaffolding: the manager transitions on logical events
/// (tailscale detected/lost, heartbeat success/failure, connection drop/
/// reconnect, reconnect timeout).  Phase A2 will wire these events to
/// real network I/O.
public final class ConnectionStateManager: @unchecked Sendable {

    // MARK: - State

    /// Current connection state.
    public private(set) var state: ConnectionState = .tailnetNotDetected

    /// Timestamp of the last successful heartbeat (nil until the first
    /// online transition).
    public private(set) var lastHeartbeat: Date?

    // MARK: - Init

    public init() {}

    // MARK: - Tailscale presence events

    /// Called when tailscaled is detected on the local machine.
    ///
    /// - Parameter homeNodeOnline: Whether the home node is reachable on the tailnet.
    public func onTailscaleDetected(homeNodeOnline: Bool) {
        if homeNodeOnline {
            transition(to: .online)
        } else {
            transition(to: .offline)
        }
    }

    /// Called when tailscaled disappears (process exits, socket gone).
    /// Resets to `.tailnetNotDetected` regardless of current state.
    public func onTailscaleLost() {
        transition(to: .tailnetNotDetected)
    }

    // MARK: - Heartbeat events

    /// Called when a heartbeat to the home node succeeds.
    /// Transitions from `.offline` to `.online`.
    public func onHeartbeatSucceeded() {
        switch state {
        case .offline:
            transition(to: .online)
        case .online:
            // Already online — just refresh the heartbeat timestamp.
            lastHeartbeat = Date()
        default:
            break
        }
    }

    /// Called when a heartbeat to the home node fails.
    /// Transitions from `.online` to `.offline`.
    public func onHeartbeatFailed() {
        switch state {
        case .online:
            transition(to: .offline)
        default:
            break
        }
    }

    // MARK: - Connection events

    /// Called when an established connection drops unexpectedly.
    /// Transitions to `.reconnecting` to start automatic recovery.
    public func onConnectionDrop() {
        switch state {
        case .online, .offline:
            transition(to: .reconnecting)
        default:
            break
        }
    }

    /// Called when a dropped connection is successfully re-established.
    public func onReconnected() {
        switch state {
        case .reconnecting, .pairedUnreachable:
            transition(to: .online)
        default:
            break
        }
    }

    /// Called when automatic reconnection attempts have timed out.
    /// Transitions from `.reconnecting` to `.pairedUnreachable`.
    public func onReconnectTimeout() {
        switch state {
        case .reconnecting:
            transition(to: .pairedUnreachable)
        default:
            break
        }
    }

    // MARK: - Diagnostics

    /// Forces a specific state (for testing and escalation).
    public func forceState(_ newState: ConnectionState) {
        state = newState
    }

    // MARK: - Private helpers

    private func transition(to newState: ConnectionState) {
        state = newState
        if newState == .online {
            lastHeartbeat = Date()
        }
    }
}
