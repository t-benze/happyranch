import Foundation

/// Real implementation of ``TailscaleStatusProviding``.
///
/// Queries the locally-installed `tailscale` CLI via `Process` to
/// obtain the same data as `tailscale status --json`.  This is a
/// **ride-installed** integration: it depends on the system Tailscale
/// app being installed and running (the BYO premise).  No embedded
/// tsnet, no Network Extension entitlement, no new dependency.
///
/// Home-node discovery uses the peer list from the tailnet status:
/// the first online peer whose hostname or DNS name matches a known
/// home-node pattern, or falls back to a manually-entered address.
public final class TailscaleStatusProvider: TailscaleStatusProviding, @unchecked Sendable {

    /// Path to the `tailscale` CLI binary.
    private let tailscalePath: String

    /// Socket path for the tailscaled LocalAPI (macOS default).
    private let socketPath: String

    // MARK: - Init

    /// - Parameters:
    ///   - tailscalePath: Path to the `tailscale` binary (default: `/usr/local/bin/tailscale`).
    ///   - socketPath: Path to the tailscaled unix socket (default: `/var/run/tailscaled.sock`).
    public init(
        tailscalePath: String = "/usr/local/bin/tailscale",
        socketPath: String = "/var/run/tailscaled.sock"
    ) {
        self.tailscalePath = tailscalePath
        self.socketPath = socketPath
    }

    // MARK: - TailscaleStatusProviding

    public func isTailscaleRunning() -> Bool {
        // Quick check: does the tailscaled socket exist?
        if FileManager.default.fileExists(atPath: socketPath) {
            return true
        }

        // Fallback: can we run `tailscale status`?
        return (try? fetchStatus())?.isRunning ?? false
    }

    public func fetchStatus() throws -> TailscaleStatus {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: tailscalePath)
        process.arguments = ["status", "--json"]

        let stdoutPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = Pipe()

        try process.run()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            throw TailscaleStatusProviderError.commandFailed(exitCode: process.terminationStatus)
        }

        let data = try stdoutPipe.fileHandleForReading.readToEnd()
            ?? Data()
        guard !data.isEmpty else {
            throw TailscaleStatusProviderError.emptyOutput
        }

        return try parseStatusJSON(data)
    }

    public func resolveHomeNode(fallbackAddress: String?) -> String? {
        // Try automatic discovery from the peer list.
        if let status = try? fetchStatus(), status.isRunning {
            for peer in status.peers where peer.online {
                if let ip = peer.tailscaleIPs.first {
                    return ip
                }
            }
        }

        // Fall back to the manually-entered address.
        if let fallback = fallbackAddress, !fallback.isEmpty {
            return fallback
        }

        return nil
    }

    // MARK: - JSON parsing

    /// Parse `tailscale status --json` output into a ``TailscaleStatus``.
    ///
    /// The JSON schema is Tailscale's `ipnlocal.Status` serialized form.
    /// We parse only the fields needed for presence/health detection and
    /// home-node discovery.
    func parseStatusJSON(_ data: Data) throws -> TailscaleStatus {
        let json: [String: Any]
        do {
            guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw TailscaleStatusProviderError.invalidJSON
            }
            json = obj
        } catch is TailscaleStatusProviderError {
            throw TailscaleStatusProviderError.invalidJSON
        } catch {
            throw TailscaleStatusProviderError.invalidJSON
        }

        let backendState = json["BackendState"] as? String
        let isRunning = backendState == "Running"
        let version = json["Version"] as? String

        // Self node
        let selfDict = json["Self"] as? [String: Any]
        let selfNodeName = selfDict?["DNSName"] as? String
        let selfIPs = selfDict?["TailscaleIPs"] as? [String] ?? []

        // Peers
        let peerDict = json["Peer"] as? [String: [String: Any]] ?? [:]
        var peers: [TailscalePeer] = []
        for (nodeID, nodeDict) in peerDict {
            let peer = TailscalePeer(
                nodeID: nodeID,
                hostName: nodeDict["HostName"] as? String,
                dnsName: nodeDict["DNSName"] as? String,
                online: nodeDict["Online"] as? Bool ?? false,
                tailscaleIPs: nodeDict["TailscaleIPs"] as? [String] ?? [],
                lastSeen: nodeDict["LastSeen"] as? String,
                os: nodeDict["OS"] as? String
            )
            peers.append(peer)
        }

        return TailscaleStatus(
            isRunning: isRunning,
            backendState: backendState,
            selfNodeName: selfNodeName,
            selfTailscaleIPs: selfIPs,
            peers: peers,
            version: version
        )
    }
}

// MARK: - Errors

public enum TailscaleStatusProviderError: Error, Equatable {
    /// The `tailscale` command exited with a non-zero status.
    case commandFailed(exitCode: Int32)

    /// The command produced no output.
    case emptyOutput

    /// The output could not be parsed as valid JSON.
    case invalidJSON

    /// The tailscale binary was not found at the expected path.
    case binaryNotFound
}
