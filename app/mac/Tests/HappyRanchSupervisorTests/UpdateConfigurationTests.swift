import Foundation
import Testing
@testable import HappyRanchSupervisor

@Suite("Update inputs")
struct UpdateConfigurationTests {
    @Test("only a canonical 32-byte public key enables the updater")
    func publicKeyValidation() {
        #expect(UpdateConfiguration.hasValidPublicKey(Data((0..<32).map(UInt8.init)).base64EncodedString()))
        let invalid: [Any?] = [nil, "", "bad", true, 32,
            Data(repeating: 0, count: 31).base64EncodedString(),
            Data(repeating: 0, count: 33).base64EncodedString(),
            Data(repeating: 0, count: 32).base64EncodedString() + "\n",
            String(repeating: "A", count: 42) + "B="]
        for value in invalid {
            #expect(!UpdateConfiguration.hasValidPublicKey(value))
        }
    }

    @Test("host admission count is typed and degraded or unavailable health stays unknown")
    func sessionGaugeParsing() throws {
        func payload(active: Any = 0, queue: Any = 0, healthy: Bool = true,
                     wired: Bool = true, shutdown: Bool = false, residue: Bool = false,
                     survivors: Int = 0) throws -> Data {
            try JSONSerialization.data(withJSONObject: ["status": "ok", "host_sessions": [
                "wired": wired, "backend": ["healthy": healthy],
                "admission": ["active": active, "queue_depth": queue, "shutdown": shutdown],
                "residue": ["admission_blocked": residue, "survivors_count": survivors]]])
        }
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload()) == 0)
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload(active: 12)) == 12)
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload(active: 2, queue: 1)) == 2)
        let invalidCounts: [Any] = [-1, true, 1.5, "0", NSNull()]
        for invalid in invalidCounts {
            #expect(UpdateSessionProbe.runningSessionCount(in: try payload(active: invalid)) == nil)
        }
        for data in [try payload(queue: 1), try payload(queue: -1),
                     try payload(healthy: false), try payload(wired: false),
                     try payload(shutdown: true), try payload(residue: true),
                     try payload(survivors: 1), try payload(survivors: -1),
                     Data("{}".utf8), Data("not json".utf8),
                     Data(#"{"status":"ok","executor_sessions_active":0}"#.utf8)] {
            #expect(UpdateSessionProbe.runningSessionCount(in: data) == nil)
        }
    }

    @Test("move notice covers Downloads, disk images and translocation only for app bundles")
    func moveNoticeLocations() {
        let home = URL(fileURLWithPath: "/Users/test")
        for (path, readOnly, expected) in [
            ("/Users/test/Downloads/HappyRanch.app", false, true),
            ("/Volumes/HappyRanch/HappyRanch.app", false, true),
            ("/private/var/folders/x/AppTranslocation/y/HappyRanch.app", false, true),
            ("/Applications/HappyRanch.app", true, true),
            ("/Applications/HappyRanch.app", false, false),
            ("/Users/test/Applications/HappyRanch.app", false, false),
            ("/Users/test/DownloadsExtra/HappyRanch.app", false, false),
            ("/Users/test/Downloads/HappyRanch", false, false)] {
            #expect(UpdateConfiguration.needsMoveNotice(bundleURL: URL(fileURLWithPath: path),
                homeURL: home, readOnlyVolume: readOnly) == expected)
        }
    }

    @Test("updater diagnostics retain startup and failure, redact, bound and export history")
    func updaterDiagnostics() throws {
        let collector = DiagnosticsCollector(homeDir: "/unused", registerAsShared: false)
        collector.recordUpdaterEvent("Disabled: SUPublicEDKey absent or invalid")
        collector.recordLaunchLog("ordinary daemon log")
        collector.recordUpdaterEvent("Download failed")
        let log = try #require(collector.collect()["updater_log"] as? String)
        #expect(log.contains("Disabled:") && log.contains("Download failed"))
        #expect(collector.exportJSON().contains("Download failed"))
        collector.recordUpdaterEvent("Bearer synthetic-secret")
        #expect(!collector.exportJSON().contains("synthetic-secret"))
        collector.recordUpdaterEvent(String(repeating: "x", count: 20_000))
        let bounded = try #require(collector.updaterLogContent)
        #expect(bounded.count <= 16_384)
    }
}
