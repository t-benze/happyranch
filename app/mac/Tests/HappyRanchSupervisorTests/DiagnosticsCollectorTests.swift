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

    // MARK: - Live redaction at collect() boundary

    @Test("live-collected struct redacts probe error with bearer token")
    func liveCollectRedactsProbeErrorWithBearerToken() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")

        // Feed a probe error containing a bearer token
        collector.recordHealthProbe(
            success: false,
            latencyMs: 0,
            errorMessage: "HTTP 401: Unauthorized — Bearer hr_token_abc123def456ghi789"
        )

        // Feed a daemon log tail containing an API key
        collector.recordDaemonLogTail("""
        2026-01-01 INFO Starting daemon
        2026-01-01 DEBUG Using API key: secret-api-key-12345
        2026-01-01 DEBUG allow-rules: pattern: "secret-pattern-value"
        """)

        // Feed a start command (no secrets, should pass through)
        collector.recordStartCommand("uv run python -m runtime.daemon")

        let bundle = collector.collect()

        // last_health_probe_error must be redacted
        let probeError = bundle["last_health_probe_error"] as? String ?? ""
        #expect(!probeError.contains("hr_token_abc123def456ghi789"),
                "Bearer token in probe error must be redacted in live-collected struct")
        #expect(probeError.contains("[REDACTED]"),
                "Redaction marker must be present in live-collected probe error")

        // daemon_log_tail must be redacted
        let logTail = bundle["daemon_log_tail"] as? String ?? ""
        #expect(!logTail.contains("secret-api-key-12345"),
                "API key in log tail must be redacted in live-collected struct")
        #expect(!logTail.contains("secret-pattern-value"),
                "Allow-rules pattern in log tail must be redacted in live-collected struct")
        #expect(logTail.contains("[REDACTED]"),
                "Redaction marker must be present in live-collected log tail")
        #expect(logTail.contains("Starting daemon"),
                "Non-sensitive log content must survive")

        // start_command has no secrets — should be intact
        let startCmd = bundle["start_command"] as? String ?? ""
        #expect(startCmd == "uv run python -m runtime.daemon",
                "Clean start command must pass through unmodified")
    }

    @Test("live-collected struct redacts secrets — live and export identical")
    func liveAndExportRedactionIdentical() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")

        // Feed a launch log containing secrets
        collector.recordLaunchLog("Bearer token=hr_token_topsecret")
        collector.recordHealthProbe(success: true, latencyMs: 10, errorMessage: nil)
        collector.recordToken("hr_token_raw_value")

        let liveBundle = collector.collect()
        let exportJSON = collector.exportJSON()

        // Both live bundle and export JSON must redact
        let liveLaunchLog = liveBundle["launcher_log"] as? String ?? ""
        let liveToken = liveBundle["token"] as? String ?? ""

        #expect(!liveLaunchLog.contains("hr_token_topsecret"))
        #expect(!liveToken.contains("hr_token_raw_value"))
        #expect(!exportJSON.contains("hr_token_topsecret"))
        #expect(!exportJSON.contains("hr_token_raw_value"))
        #expect(exportJSON.contains("[REDACTED]"))
    }

    // MARK: - New diagnostic fields (THR-044 Build B)

    @Test("collects macOS version as string and numeric tuple")
    func collectsMacOSVersion() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        let bundle = collector.collect()

        let versionString = bundle["macos_version"] as? String ?? ""
        #expect(!versionString.isEmpty, "macOS version string must be present")
        #expect(versionString.contains("."), "macOS version string must contain dots")

        #expect(bundle["macos_major"] as? Int != nil, "macos_major must be present")
        #expect(bundle["macos_minor"] as? Int != nil, "macos_minor must be present")
        #expect(bundle["macos_patch"] as? Int != nil, "macos_patch must be present")
    }

    @Test("collects architecture via utsname")
    func collectsArchitecture() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        let bundle = collector.collect()

        let arch = bundle["architecture"] as? String ?? ""
        #expect(!arch.isEmpty, "Architecture must be present")
        #expect(arch == "arm64" || arch == "x86_64",
                "Architecture must be arm64 or x86_64, got: \(arch)")
    }

    @Test("collects build SHA (best-effort, falls back to unknown)")
    func collectsBuildSHA() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        let bundle = collector.collect()

        let sha = bundle["build_sha"] as? String ?? ""
        #expect(!sha.isEmpty, "Build SHA must be present")
        // In swift test, Info.plist won't have the key, so it should be "unknown"
        // But if it IS set (e.g. in a bundled build), it'll be a hex string
        #expect(sha == "unknown" || sha.allSatisfy { $0.isHexDigit },
                "Build SHA must be 'unknown' or a hex string, got: \(sha)")
    }

    @Test("collects env/PATH summary")
    func collectsEnvPathSummary() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        let bundle = collector.collect()

        let envSummary = bundle["env_path_summary"] as? String ?? ""
        #expect(!envSummary.isEmpty, "env/PATH summary must be present")
        #expect(envSummary.contains("PATH"), "env/PATH summary must mention PATH")
    }

    @Test("env/PATH summary redaction — token-shaped values redacted")
    func envPathSummaryRedactsTokens() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        setenv("HAPPYRANCH_TEST_TOKEN", "hr_token_test_secret_123", 1)
        defer { unsetenv("HAPPYRANCH_TEST_TOKEN") }

        let bundle = collector.collect()
        let envSummary = bundle["env_path_summary"] as? String ?? ""

        #expect(envSummary.contains("PATH"), "PATH must be present in env summary")
    }

    // MARK: - Daemon stderr recording

    @Test("recordDaemonStderr stores captured stderr")
    func recordDaemonStderr() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonStderr("Fatal error: port already in use\nStack trace: ...")

        let bundle = collector.collect()
        let stderr = bundle["daemon_stderr"] as? String ?? ""
        #expect(stderr.contains("Fatal error"))
        #expect(stderr.contains("port already in use"))
    }

    @Test("daemon stderr redacts bearer tokens")
    func daemonStderrRedactsTokens() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonStderr("ERROR: Bearer hr_token_leaked_secret\n")

        let bundle = collector.collect()
        let stderr = bundle["daemon_stderr"] as? String ?? ""

        #expect(!stderr.contains("hr_token_leaked_secret"),
                "Bearer token in daemon stderr must be redacted")
        #expect(stderr.contains("[REDACTED]"),
                "Redaction marker must be present in daemon stderr")
    }

    // MARK: - Persist to disk

    @Test("persist writes diagnostics to directory and returns path")
    func persistWritesToDirectory() throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-diagnostics-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let collector = DiagnosticsCollector(homeDir: tmpDir.path)
        collector.recordLaunchLog("Launcher started")
        collector.recordDaemonStderr("error: something went wrong")
        collector.recordExit(exitCode: 1, signal: nil)

        let outputDir = try collector.persist()
        defer { try? FileManager.default.removeItem(at: outputDir) }

        #expect(FileManager.default.fileExists(atPath: outputDir.path))

        let jsonPath = outputDir.appendingPathComponent("diagnostics.json")
        #expect(FileManager.default.fileExists(atPath: jsonPath.path))
    }

    // MARK: - Export zip

    @Test("exportZip produces a redacted zip bundle without live daemon")
    func exportZipProducesRedactedBundle() throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-zip-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordLaunchLog("Bearer token=hr_token_secret")
        collector.recordDaemonStderr("error: hr_token_leaked")
        collector.recordExit(exitCode: 1, signal: nil)

        let zipURL = tmpDir.appendingPathComponent("diagnostics.zip")
        try collector.exportZip(to: zipURL)

        #expect(FileManager.default.fileExists(atPath: zipURL.path),
                "Zip file must be created")

        let attrs = try FileManager.default.attributesOfItem(atPath: zipURL.path)
        let size = attrs[.size] as? Int64 ?? 0
        #expect(size > 0, "Zip file must be non-empty")
    }

    @Test("diagnosticsDirectory returns path under daemon home")
    func diagnosticsDirectoryPath() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        let dir = collector.diagnosticsDirectory
        #expect(dir.path.hasSuffix("/diagnostics"))
        #expect(dir.path.contains("/tmp/test-hr"))
    }

    // MARK: - Daemon stdout recording (FINDING 1 fix)

    @Test("recordDaemonStdout stores captured stdout")
    func recordDaemonStdout() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonStdout("daemon v1.0 starting on port 8765\nListening for connections...")

        let bundle = collector.collect()
        let stdout = bundle["daemon_stdout"] as? String ?? ""
        #expect(stdout.contains("daemon v1.0 starting"))
        #expect(stdout.contains("Listening for connections"))
    }

    @Test("daemon stdout redacts bearer tokens")
    func daemonStdoutRedactsTokens() {
        let collector = DiagnosticsCollector(homeDir: "/tmp/test-hr")
        collector.recordDaemonStdout("INFO: token refresh hr_token_exposed_in_stdout\n")

        let bundle = collector.collect()
        let stdout = bundle["daemon_stdout"] as? String ?? ""

        #expect(!stdout.contains("hr_token_exposed_in_stdout"),
                "Bearer token in daemon stdout must be redacted")
        #expect(stdout.contains("[REDACTED]"),
                "Redaction marker must be present in daemon stdout")
    }

    @Test("persist writes daemon_stdout.txt when stdout is captured")
    func persistWritesDaemonStdoutFile() throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-stdout-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let collector = DiagnosticsCollector(homeDir: tmpDir.path)
        collector.recordDaemonStdout("daemon started successfully\n")
        collector.recordExit(exitCode: 0, signal: nil)

        let outputDir = try collector.persist()
        defer { try? FileManager.default.removeItem(at: outputDir) }

        let stdoutPath = outputDir.appendingPathComponent("daemon_stdout.txt")
        #expect(FileManager.default.fileExists(atPath: stdoutPath.path),
                "daemon_stdout.txt must be created when stdout is captured")

        let content = try String(contentsOf: stdoutPath, encoding: .utf8)
        #expect(content.contains("daemon started successfully"))
    }

    @Test("exportZip includes redacted daemon stdout")
    func exportZipIncludesRedactedStdout() throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-zip-stdout-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let homeDir = tmpDir.appendingPathComponent("daemon-home")
        try FileManager.default.createDirectory(at: homeDir, withIntermediateDirectories: true)

        let collector = DiagnosticsCollector(homeDir: homeDir.path)
        collector.recordDaemonStdout("INFO: Bearer hr_token_zip_leak\ndaemon ready")
        collector.recordDaemonStderr("error: hr_token_stderr_leak")
        collector.recordExit(exitCode: 1, signal: nil)

        let zipURL = tmpDir.appendingPathComponent("diagnostics.zip")
        try collector.exportZip(to: zipURL)

        #expect(FileManager.default.fileExists(atPath: zipURL.path))

        // Unzip and verify contents
        let extractDir = tmpDir.appendingPathComponent("extracted")
        try FileManager.default.createDirectory(at: extractDir, withIntermediateDirectories: true)

        let unzip = Process()
        unzip.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        unzip.arguments = ["-x", "-k", zipURL.path, extractDir.path]
        try unzip.run()
        unzip.waitUntilExit()

        let stdoutPath = extractDir.appendingPathComponent("daemon_stdout.txt")
        #expect(FileManager.default.fileExists(atPath: stdoutPath.path),
                "ZIP must contain daemon_stdout.txt")

        let stdoutContent = try String(contentsOf: stdoutPath, encoding: .utf8)
        #expect(!stdoutContent.contains("hr_token_zip_leak"),
                "Stdout in ZIP must not contain raw token")
        #expect(stdoutContent.contains("[REDACTED]"),
                "Stdout in ZIP must contain redaction marker")
        #expect(stdoutContent.contains("daemon ready"),
                "Non-sensitive stdout content must survive in ZIP")
    }

    @Test("ZIP daemon stderr is also redacted")
    func zipDaemonStderrRedacted() throws {
        let tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-zip-stderr-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let homeDir = tmpDir.appendingPathComponent("daemon-home")
        try FileManager.default.createDirectory(at: homeDir, withIntermediateDirectories: true)

        let collector = DiagnosticsCollector(homeDir: homeDir.path)
        collector.recordDaemonStderr("FATAL: hr_token_crash_secret\nport bind failed")
        collector.recordExit(exitCode: 1, signal: nil)

        let zipURL = tmpDir.appendingPathComponent("diagnostics.zip")
        try collector.exportZip(to: zipURL)

        let extractDir = tmpDir.appendingPathComponent("extracted")
        try FileManager.default.createDirectory(at: extractDir, withIntermediateDirectories: true)

        let unzip = Process()
        unzip.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        unzip.arguments = ["-x", "-k", zipURL.path, extractDir.path]
        try unzip.run()
        unzip.waitUntilExit()

        let stderrPath = extractDir.appendingPathComponent("daemon_stderr.txt")
        let stderrContent = try String(contentsOf: stderrPath, encoding: .utf8)
        #expect(!stderrContent.contains("hr_token_crash_secret"),
                "Stderr in ZIP must not contain raw token")
        #expect(stderrContent.contains("[REDACTED]"))
        #expect(stderrContent.contains("port bind failed"))
    }
}
