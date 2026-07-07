import Foundation

/// The connection role of this HappyRanch Mac app instance.
///
/// v1 assumes the two roles are **disjoint**: a machine is either HOME
/// (runs the daemon locally) or CLIENT (no local daemon, connects to
/// a remote home).  Role detection reuses the existing
/// ``DaemonSupervisor`` state — no re-implementation of daemon discovery.
public enum ConnectionRole: String, Sendable, Equatable, CustomStringConvertible {
    /// This machine runs the HappyRanch daemon — a daemon home exists
    /// and is configured.
    case home

    /// No local daemon; this instance connects to a remote home
    /// over the BYO-Tailscale tailnet.
    case client

    // MARK: - Computed properties

    /// Whether a local daemon drives this role.
    public var isLocal: Bool {
        self == .home
    }

    public var description: String { rawValue }

    // MARK: - Detection

    /// Detect the connection role from the daemon supervisor state.
    ///
    /// - HOME: the supervisor has been configured with a home directory
    ///   (state != `.notConfigured`), indicating a daemon home exists
    ///   on this machine.
    /// - CLIENT: the supervisor is in `.notConfigured` — no daemon
    ///   home has been set up.
    ///
    /// Reuses the existing ``DaemonSupervisor`` and its
    /// ``DaemonSupervisor/state`` — no re-implementation of daemon discovery.
    public static func detect(supervisor: DaemonSupervisor) -> ConnectionRole {
        if supervisor.state == .notConfigured {
            return .client
        }
        return .home
    }
}
