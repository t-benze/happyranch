import Foundation

/// The closed v1 projection from tsnet_health_json. Unknown JSON fields are ignored.
public struct TsnetHealthRecord: Codable, Sendable {
    public let v: Int
    public let at, trigger, backend, control: String
    public let health: [String]
    public let udp, nat_varies: Bool
    public let portmap, derp_home: String
    public let derp_ms: [String: Int]
    public let peer: Peer

    public struct Peer: Codable, Sendable {
        public let name: String
        public let online: Bool
        public let path, relay: String
        public let rtt_ms, handshake_age_s: Int64
        public let direct_kind: String?

        enum CodingKeys: String, CodingKey {
            case name, online, path, relay, rtt_ms, handshake_age_s, direct_kind
        }

        public func encode(to encoder: any Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            try container.encode(name, forKey: .name)
            try container.encode(online, forKey: .online)
            try container.encode(path, forKey: .path)
            try container.encode(relay, forKey: .relay)
            try container.encode(rtt_ms, forKey: .rtt_ms)
            try container.encode(handshake_age_s, forKey: .handshake_age_s)
            try container.encode(direct_kind, forKey: .direct_kind) // preserve v1 null
        }
    }

    static func decode(_ json: String) -> Self? {
        guard json.utf8.count <= 2048,
              let record = try? JSONDecoder().decode(Self.self, from: Data(json.utf8)),
              record.v == 1 else { return nil }
        return record
    }

    /// Display only fixed path/kind/health labels, bounded region codes and RTT.
    /// Never interpolate the expected peer's name or arbitrary health text.
    public var summary: String {
        var parts: [String]
        switch peer.path {
        case "direct":
            let kind = ["lan": "LAN", "public": "public", "cgnat": "CGNAT", "ipv6": "IPv6"][peer.direct_kind ?? ""]
            parts = ["Connected", kind.map { "direct (\($0))" } ?? "direct"]
        case "relay":
            let validRegion = peer.relay.range(of: "^[a-z][a-z0-9-]{0,15}\\z", options: .regularExpression) != nil
            parts = ["Connected", validRegion ? "relayed via \(peer.relay)" : "relayed"]
        default:
            parts = ["No path"]
        }
        if ["direct", "relay"].contains(peer.path), peer.rtt_ms >= 0 {
            parts.append("\(peer.rtt_ms) ms")
        }
        if health.contains("no-udp") { parts.append("UDP blocked") }
        return parts.joined(separator: " · ")
    }

    var diagnosticJSON: String? {
        guard let data = try? JSONEncoder().encode(self) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
