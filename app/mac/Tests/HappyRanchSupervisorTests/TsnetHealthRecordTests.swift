import Foundation
import Testing
@testable import HappyRanchSupervisor

@Suite("Tsnet health record")
struct TsnetHealthRecordTests {
    private func json(path: String = "direct", kind: String? = "lan", relay: String = "hk",
                      latency: Int = 12, health: [String] = [], version: Int = 1,
                      name: String = "home") throws -> String {
        let object: [String: Any] = [
            "v": version, "at": "2026-11-15T05:10:00Z", "trigger": "on_demand",
            "backend": "Running", "control": "connected", "health": health,
            "udp": true, "nat_varies": false, "portmap": "unknown", "derp_home": "hk",
            "derp_ms": ["hk": 38], "ignored_future_field": "nodekey:PRIVATE_SENTINEL",
            "peer": ["name": name, "online": true, "path": path, "relay": relay,
                     "rtt_ms": latency, "handshake_age_s": 40, "direct_kind": kind.map { $0 as Any } ?? NSNull()]
        ]
        return String(decoding: try JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    @Test("v1 paths, direct kinds, RTT and fixed UDP warning format")
    func formatsPaths() throws {
        let cases: [(String, String?, String, Int, [String], String)] = [
            ("direct", "lan", "hk", 12, [], "Connected · direct (LAN) · 12 ms"),
            ("direct", "public", "hk", 0, [], "Connected · direct (public) · 0 ms"),
            ("direct", "cgnat", "hk", 9, [], "Connected · direct (CGNAT) · 9 ms"),
            ("direct", "ipv6", "hk", 9, [], "Connected · direct (IPv6) · 9 ms"),
            ("direct", nil, "hk", -1, [], "Connected · direct"),
            ("relay", nil, "hk", 84, ["no-udp"], "Connected · relayed via hk · 84 ms · UDP blocked"),
            ("none", nil, "", 99, [], "No path")
        ]
        for (path, kind, relay, latency, health, expected) in cases {
            let record = try #require(TsnetHealthRecord.decode(json(path: path, kind: kind, relay: relay, latency: latency, health: health)))
            #expect(record.summary == expected)
        }
    }

    @Test("unsupported, malformed and oversized records are unavailable")
    func rejectsUnavailable() throws {
        #expect(TsnetHealthRecord.decode(try json(version: 2)) == nil)
        #expect(TsnetHealthRecord.decode("garbage") == nil)
        #expect(TsnetHealthRecord.decode("{}") == nil)
        #expect(TsnetHealthRecord.decode(try json(name: String(repeating: "x", count: 2048))) == nil)
    }

    @Test("formatter never interpolates key, IP, peer name or raw health sentinels")
    func excludesSentinels() throws {
        let key = "nodekey:PRIVATE_SENTINEL"
        for path in ["direct", "relay", "none", key] {
            let record = try #require(TsnetHealthRecord.decode(json(path: path, kind: key, relay: "203.0.113.7",
                                                                  health: [key, "100.64.0.9"], name: key)))
            #expect(!record.summary.contains(key))
            #expect(!record.summary.contains("203.0.113.7"))
            #expect(!record.summary.contains("100.64.0.9"))
        }
    }

    @Test("Diagnostics includes all v1 fields, redacts and persists the cached record")
    func diagnosticsRecord() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let collector = DiagnosticsCollector(homeDir: dir.path, registerAsShared: false)
        let record = try #require(TsnetHealthRecord.decode(json(kind: nil, name: "hr_token_SYNTHETIC_ONLY")))
        collector.recordTsnetHealth(record)
        let cached = try #require(collector.collect()["tsnet_health"] as? String)
        #expect(!cached.contains("hr_token_SYNTHETIC_ONLY"))
        #expect(cached.contains("[REDACTED]"))
        #expect(!cached.contains("ignored_future_field"))
        let object = try #require(JSONSerialization.jsonObject(with: Data(cached.utf8)) as? [String: Any])
        #expect(Set(object.keys) == Set(["v", "at", "trigger", "backend", "control", "health", "udp", "nat_varies", "portmap", "derp_home", "derp_ms", "peer"]))
        let peer = try #require(object["peer"] as? [String: Any])
        #expect(peer["direct_kind"] is NSNull)
        #expect(peer["rtt_ms"] as? Int == 12)
        #expect(peer["handshake_age_s"] as? Int == 40)
        #expect((object["derp_ms"] as? [String: Int])?["hk"] == 38)
        let persisted = try collector.persist().appendingPathComponent("diagnostics.json")
        let bundle = try #require(JSONSerialization.jsonObject(with: Data(contentsOf: persisted)) as? [String: Any])
        #expect(bundle["tsnet_health"] as? String == cached)
    }

    @Test("unavailable health does not prevent a Diagnostics export")
    func diagnosticsUnavailable() throws {
        let collector = DiagnosticsCollector(homeDir: "/tmp/health-unavailable", registerAsShared: false)
        let bundle = try #require(JSONSerialization.jsonObject(with: Data(collector.exportJSON().utf8)) as? [String: Any])
        #expect(bundle["tsnet_health"] as? String == "unavailable")
        #expect(bundle["app_version"] != nil)
    }

    @Test("DIY and unconfigured managed bridges have no health record")
    func bridgeUnavailable() async {
        for lane in [ClientBridge.Lane.diy, .managed] {
            let bridge = ClientBridge(homeConnectorHost: "127.0.0.1", homeConnectorPort: 8443, lane: lane)
            let record = await bridge.healthRecord(timeoutMs: 1)
            #expect(record == nil)
        }
    }
}
