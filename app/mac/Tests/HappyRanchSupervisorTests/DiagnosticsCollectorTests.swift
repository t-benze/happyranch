import Foundation
import Testing
@testable import HappyRanchSupervisor

@Suite("DiagnosticsCollector")
struct DiagnosticsCollectorTests {

    @Test("collects basic system information")
    func collectsBasicInfo() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        let bundle = collector.collect()

        // Should contain these keys
        #expect(bundle["app_version"] != nil)
        #expect(bundle["app_build"] != nil)
        #expect(bundle["runtime_home"] as? String == "/tmp/test-hr")
    }

    @Test("collects daemon state information")
    func collectsDaemonState() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonState(pid: 12345, port: 9876, bindHost: "127.0.0.1", state: "running")
        let bundle = collector.collect()

        #expect(bundle["daemon_pid"] as? Int32 == 12345)
        #expect(bundle["daemon_port"] as? UInt16 == 9876)
        #expect(bundle["daemon_bind_host"] as? String == "127.0.0.1")
        #expect(bundle["daemon_state"] as? String == "running")
    }

    @Test("collects launch log safely")
    func collectsLaunchLog() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordLaunchLog("Starting daemon...\nBearer token: secret123\nDaemon running on port 8888")

        let bundle = collector.collect()
        let launchLog = bundle["launcher_log"] as? String ?? ""

        // Token must be redacted
        #expect(!launchLog.contains("secret123"))
        #expect(launchLog.contains("[REDACTED]"))
        // Non-sensitive preserved
        #expect(launchLog.contains("Starting daemon"))
    }

    @Test("collects daemon log tail safely")
    func collectsDaemonLogTail() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonLogTail("""
        INFO: Health check passed
        DEBUG: Token refresh: abc-def-ghi
        INFO: Task dispatched
        """)

        let bundle = collector.collect()
        let logTail = bundle["daemon_log_tail"] as? String ?? ""

        #expect(!logTail.contains("abc-def-ghi"))
        #expect(logTail.contains("[REDACTED]"))
        #expect(logTail.contains("Health check passed"))
    }

    @Test("collects health probe result")
    func collectsHealthProbeResult() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordHealthProbe(success: true, latencyMs: 42, errorMessage: nil)

        let bundle = collector.collect()
        #expect(bundle["last_health_probe_success"] as? Bool == true)
        #expect(bundle["last_health_probe_latency_ms"] as? Int == 42)
    }

    @Test("collects failed health probe result")
    func collectsFailedHealthProbe() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordHealthProbe(success: false, latencyMs: 0, errorMessage: "Connection refused")

        let bundle = collector.collect()
        #expect(bundle["last_health_probe_success"] as? Bool == false)
        #expect(bundle["last_health_probe_error"] as? String == "Connection refused")
    }

    @Test("collects last exit information")
    func collectsExitInfo() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordExit(exitCode: 1, signal: nil)

        let bundle = collector.collect()
        #expect(bundle["last_exit_code"] as? Int32 == 1)
        #expect(bundle["last_exit_signal"] == nil)
    }

    @Test("collects signal exit information")
    func collectsSignalExit() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordExit(exitCode: -1, signal: 9)

        let bundle = collector.collect()
        #expect(bundle["last_exit_code"] as? Int32 == -1)
        #expect(bundle["last_exit_signal"] as? Int32 == 9)
    }

    @Test("export bundle does not contain raw token")
    func exportBundleNoRawToken() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonState(pid: 123, port: 456, bindHost: "127.0.0.1", state: "running")

        // Simulate a token being present in the environment
        collector.recordToken("hr_token_super_secret_do_not_leak")

        let export = collector.exportJSON()
        // The JSON string must not contain the raw token
        #expect(!export.contains("hr_token_super_secret_do_not_leak"))
        // But should indicate it was redacted
        #expect(export.contains("[REDACTED]"))
    }

    @Test("start command mode is captured")
    func capturesStartCommandMode() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordStartCommand("uv run python -m runtime.daemon")

        let bundle = collector.collect()
        #expect(bundle["start_command"] as? String == "uv run python -m runtime.daemon")
    }

    @Test("active runtime path is captured")
    func capturesActiveRuntimePath() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordActiveRuntimePath("/Users/user/happyranch")

        let bundle = collector.collect()
        #expect(bundle["active_runtime_path"] as? String == "/Users/user/happyranch")
    }
}
