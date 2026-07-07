import Foundation

/// Parsed Tailscale status data from `tailscale status --json`
/// or the tailscaled LocalAPI.
///
/// All fields are immutable and `Sendable` so this struct can cross
/// actor boundaries safely.
public struct TailscaleStatus: Sendable, Equatable {
    /// Whether tailscaled is running (`BackendState` == "Running").
    public let isRunning: Bool

    /// Backend state string from the JSON (e.g. "Running", "Stopped").
    public let backendState: String?

    /// This machine's tailnet node name (DNS name, e.g. "my-mac.tailnet.ts.net").
    public let selfNodeName: String?

    /// This machine's Tailscale IP addresses (usually one 100.x.y.z).
    public let selfTailscaleIPs: [String]

    /// All peer nodes visible to this tailnet.
    public let peers: [TailscalePeer]

    /// The Tailscale version string.
    public let version: String?

    public init(
        isRunning: Bool,
        backendState: String? = nil,
        selfNodeName: String? = nil,
        selfTailscaleIPs: [String] = [],
        peers: [TailscalePeer] = [],
        version: String? = nil
    ) {
        self.isRunning = isRunning
        self.backendState = backendState
        self.selfNodeName = selfNodeName
        self.selfTailscaleIPs = selfTailscaleIPs
        self.peers = peers
        self.version = version
    }
}

/// A single peer node on the tailnet.
public struct TailscalePeer: Sendable, Equatable {
    /// Tailscale-stable node ID (e.g. "nXXXXXXXXXXXXXXXXXX").
    public let nodeID: String?

    /// Hostname of the peer machine.
    public let hostName: String?

    /// Full DNS name on the tailnet (e.g. "machine.tailnet.ts.net").
    public let dnsName: String?

    /// Whether the peer is currently online.
    public let online: Bool

    /// The peer's Tailscale IP addresses (usually one 100.x.y.z).
    public let tailscaleIPs: [String]

    /// ISO-8601 timestamp of when the peer was last seen.
    public let lastSeen: String?

    /// Operating system reported by the peer.
    public let os: String?

    public init(
        nodeID: String? = nil,
        hostName: String? = nil,
        dnsName: String? = nil,
        online: Bool,
        tailscaleIPs: [String] = [],
        lastSeen: String? = nil,
        os: String? = nil
    ) {
        self.nodeID = nodeID
        self.hostName = hostName
        self.dnsName = dnsName
        self.online = online
        self.tailscaleIPs = tailscaleIPs
        self.lastSeen = lastSeen
        self.os = os
    }
}

// MARK: - TailscaleStatusProviding protocol

/// Injectable protocol seam for querying local Tailscale state.
///
/// Mirrors the ``ProcessControlling`` pattern: a `Sendable` protocol
/// that the real implementation satisfies via `tailscale status --json`,
/// and tests inject a ``FakeTailscaleStatusProvider``.
///
/// Ride-installed integration ONLY — no embedded tsnet, no Network
/// Extension entitlement, no new bundled dependency.
public protocol TailscaleStatusProviding: AnyObject, Sendable {
    /// Fetch the current Tailscale status by calling `tailscale status --json`
    /// or querying the tailscaled LocalAPI.
    ///
    /// - Throws: If tailscale is not installed, tailscaled is not running,
    ///   or the JSON output cannot be parsed.
    func fetchStatus() throws -> TailscaleStatus

    /// Quick check: is tailscaled running?  Cached or cheap, not a full status fetch.
    /// Returns `true` if the tailscaled socket or process is detected.
    func isTailscaleRunning() -> Bool

    /// Resolve the home node's Tailscale address.
    ///
    /// Phase A1 is manual-fallback-ONLY: returns `fallbackAddress`
    /// (if non-empty) or `nil`.  Automatic home-node discovery via
    /// hostname/DNS-name matching on the peer list is deferred to A2.
    ///
    /// - Returns: The home node's Tailscale IP (100.x.y.z) or `nil`.
    /// - Parameter fallbackAddress: A manually-entered address to use
    ///   as the home-node target. v1: user pastes the home node's tailnet IP.
    func resolveHomeNode(fallbackAddress: String?) -> String?
}
