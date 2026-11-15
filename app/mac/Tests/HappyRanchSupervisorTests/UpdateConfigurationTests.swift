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

    @Test("only the new all-producer field can report idle; old or unknown health stays busy")
    func sessionGaugeParsing() throws {
        func payload(host: Any = 0, jobs: Any = 0, invocations: Any = 0,
                     busy: Bool = false, unknown: Bool = false) throws -> Data {
            try JSONSerialization.data(withJSONObject: ["status": "ok", "update_busy": [
                "busy": busy, "unknown": unknown, "counts": [
                    "host_active": host, "host_queued": 0, "jobs_inflight": 0,
                    "jobs_nonterminal": jobs, "invocations_nonterminal": invocations]]])
        }
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload()) == 0)
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload(host: 12, busy: true)) == 12)
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload(jobs: 1, busy: true)) == 1)
        #expect(UpdateSessionProbe.runningSessionCount(in: try payload(invocations: 1, busy: true)) == 1)
        let invalidCounts: [Any] = [-1, true, 1.5, "0", NSNull(), 1001]
        for invalid in invalidCounts {
            #expect(UpdateSessionProbe.runningSessionCount(in: try payload(host: invalid)) == nil)
        }
        for data in [try payload(unknown: true), try payload(busy: true),
                     try payload(jobs: 1), try payload(jobs: 1, busy: true, unknown: true),
                     Data("{}".utf8), Data("not json".utf8),
                     Data(#"{"status":"ok","host_sessions":{"admission":{"active":0,"queue_depth":0}}}"#.utf8),
                     Data(#"{"status":"ok","update_busy":{"busy":false,"unknown":false}}"#.utf8)] {
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
