import Foundation

// MARK: - ConnectionRolePreference

/// Explicit, persisted role preference selected by the user.
///
/// Stored in `UserDefaults` under `connectionRolePreference`.
/// When the preference is `.undetermined` (first launch), the role is
/// derived from the supervisor state.  Once the user selects HOME or CLIENT,
/// the preference takes priority over the supervisor state.
public enum ConnectionRolePreference: String, Sendable, Equatable, CaseIterable {
    /// First launch — no choice has been made yet.
    case undetermined
    /// User chose to run the daemon locally.
    case home
    /// User chose to connect to a remote home runtime.
    case client
}

// MARK: - ConnectionRole

/// The connection role of this HappyRanch Mac app instance.
///
/// v1 assumes the two roles are **disjoint**: a machine is either HOME
/// (runs the daemon locally) or CLIENT (no local daemon, connects to
/// a remote home).  Role detection reuses the existing
/// ``DaemonSupervisor`` state — no re-implementation of daemon discovery.
///
/// An explicit ``ConnectionRolePreference`` (stored in UserDefaults)
/// takes priority over the supervisor-based heuristic.  This ensures a
/// CLIENT machine (e.g. MacBook Air with no local daemon) reaches the
/// client connect/redeem form even though the app unconditionally
/// configures the supervisor on launch.
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

    /// Detect the connection role from the daemon supervisor state,
    /// with an optional explicit preference that takes priority.
    ///
    /// - Parameter supervisor: the daemon supervisor whose state is the fallback.
    /// - Parameter preference: an explicit user-chosen role.  When non-nil
    ///   and not `.undetermined`, this is returned directly.  When
    ///   `.undetermined` or nil, the supervisor state is consulted:
    ///   `.notConfigured` → CLIENT, any other state → HOME.
    ///
    /// Reuses the existing ``DaemonSupervisor`` and its
    /// ``DaemonSupervisor/state`` — no re-implementation of daemon discovery.
    public static func detect(
        supervisor: DaemonSupervisor,
        preference: ConnectionRolePreference? = nil
    ) -> ConnectionRole {
        // Explicit preference takes priority over supervisor state
        if let pref = preference, pref != .undetermined {
            switch pref {
            case .home: return .home
            case .client: return .client
            case .undetermined: break // unreachable due to guard, but exhaustive
            }
        }
        // Fallback: derive from supervisor state
        if supervisor.state == .notConfigured {
            return .client
        }
        return .home
    }
}
