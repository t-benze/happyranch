import Foundation

/// Collects diagnostics information about the app and daemon for display
/// and export. All sensitive data (tokens, secrets) is redacted.
public final class DiagnosticsCollector: @unchecked Sendable {

    private let homeDir: String
    public private(set) var daemonPid: Int32?
    public private(set) var daemonPort: UInt16?
    public private(set) var daemonBindHost: String?
    public private(set) var daemonStateValue: String?
    public private(set) var launchLogContent: String?
    public private(set) var daemonLogTailContent: String?
    public private(set) var daemonStderrContent: String?
    public private(set) var daemonStdoutContent: String?
    public private(set) var lastHealthProbeSuccess: Bool?
    public private(set) var lastHealthProbeLatencyMs: Int?
    public private(set) var lastHealthProbeError: String?
    public private(set) var lastExitCode: Int32?
    public private(set) var lastExitSignal: Int32?
    public private(set) var startCommand: String?
    public private(set) var activeRuntimePath: String?
    private var tokenValue: String?

    public init(homeDir: String) {
        self.homeDir = homeDir
    }

    // MARK: - Record methods

    public func recordDaemonState(pid: Int32, port: UInt16, bindHost: String, state: String) {
        daemonPid = pid
        daemonPort = port
        daemonBindHost = bindHost
        daemonStateValue = state
    }

    public func recordLaunchLog(_ log: String) {
        launchLogContent = DiagnosticsRedactor.redact(log)
    }

    public func recordDaemonLogTail(_ log: String) {
        daemonLogTailContent = DiagnosticsRedactor.redact(log)
    }

    public func recordHealthProbe(success: Bool, latencyMs: Int, errorMessage: String?) {
        lastHealthProbeSuccess = success
        lastHealthProbeLatencyMs = latencyMs
        lastHealthProbeError = errorMessage.map { DiagnosticsRedactor.redact($0) }
    }

    public func recordExit(exitCode: Int32, signal: Int32?) {
        self.lastExitCode = exitCode
        lastExitSignal = signal
    }

    public func recordStartCommand(_ command: String) {
        startCommand = DiagnosticsRedactor.redact(command)
    }

    public func recordActiveRuntimePath(_ path: String) {
        activeRuntimePath = path
    }

    public func recordDaemonStderr(_ stderr: String) {
        daemonStderrContent = DiagnosticsRedactor.redact(stderr)
    }

    public func recordDaemonStdout(_ stdout: String) {
        daemonStdoutContent = DiagnosticsRedactor.redact(stdout)
    }

    public func recordToken(_ token: String) {
        tokenValue = DiagnosticsRedactor.redact(token)
    }

    /// The diagnostics persistence directory under the daemon home.
    public var diagnosticsDirectory: URL {
        URL(fileURLWithPath: homeDir).appendingPathComponent("diagnostics")
    }

    // MARK: - Collect

    /// Returns a dictionary of all collected diagnostics.
    /// All string values are redacted at the boundary — live display and
    /// export share ONE redaction guarantee.
    public func collect() -> [String: Any] {
        var bundle: [String: Any] = [:]

        bundle["app_version"] = DiagnosticsRedactor.redact(
            Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.1.0")
        bundle["app_build"] = DiagnosticsRedactor.redact(
            Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1")
        bundle["runtime_home"] = DiagnosticsRedactor.redact(homeDir)

        // macOS version
        let osVersion = ProcessInfo.processInfo.operatingSystemVersion
        bundle["macos_version"] = "\(osVersion.majorVersion).\(osVersion.minorVersion).\(osVersion.patchVersion)"
        bundle["macos_major"] = osVersion.majorVersion
        bundle["macos_minor"] = osVersion.minorVersion
        bundle["macos_patch"] = osVersion.patchVersion

        // Architecture via utsname
        bundle["architecture"] = Self.machineArchitecture()

        // Build SHA (best-effort, Info.plist key GitCommitSHA)
        let sha = Bundle.main.infoDictionary?["GitCommitSHA"] as? String ?? "unknown"
        bundle["build_sha"] = DiagnosticsRedactor.redact(sha)

        // Env/PATH summary (redacted)
        bundle["env_path_summary"] = Self.buildEnvPathSummary()

        if let pid = daemonPid { bundle["daemon_pid"] = pid }
        if let port = daemonPort { bundle["daemon_port"] = port }
        if let host = daemonBindHost { bundle["daemon_bind_host"] = DiagnosticsRedactor.redact(host) }
        if let st = daemonStateValue { bundle["daemon_state"] = DiagnosticsRedactor.redact(st) }

        if let log = launchLogContent { bundle["launcher_log"] = DiagnosticsRedactor.redact(log) }
        if let log = daemonLogTailContent { bundle["daemon_log_tail"] = DiagnosticsRedactor.redact(log) }
        if let stderr = daemonStderrContent { bundle["daemon_stderr"] = DiagnosticsRedactor.redact(stderr) }
        if let stdout = daemonStdoutContent { bundle["daemon_stdout"] = DiagnosticsRedactor.redact(stdout) }

        if let success = lastHealthProbeSuccess {
            bundle["last_health_probe_success"] = success
        }
        if let latency = lastHealthProbeLatencyMs {
            bundle["last_health_probe_latency_ms"] = latency
        }
        if let error = lastHealthProbeError {
            bundle["last_health_probe_error"] = DiagnosticsRedactor.redact(error)
        }

        if let code = lastExitCode { bundle["last_exit_code"] = code }
        if let sig = lastExitSignal { bundle["last_exit_signal"] = sig }

        if let cmd = startCommand { bundle["start_command"] = DiagnosticsRedactor.redact(cmd) }
        if let path = activeRuntimePath { bundle["active_runtime_path"] = DiagnosticsRedactor.redact(path) }
        if let token = tokenValue { bundle["token"] = DiagnosticsRedactor.redact(token) }

        return bundle
    }

    /// Exports the diagnostics bundle as a JSON string.
    /// collect() already redacts all string values at the boundary,
    /// so exportJSON inherits the same redaction guarantee.
    public func exportJSON() -> String {
        let bundle = collect()
        // Convert to JSON-serializable dictionary, filtering out nil values
        var jsonDict: [String: Any] = [:]
        for (key, value) in bundle {
            if let v = value as? Int32 { jsonDict[key] = v }
            else if let v = value as? Int { jsonDict[key] = v }
            else if let v = value as? UInt16 { jsonDict[key] = v }
            else if let v = value as? Bool { jsonDict[key] = v }
            else if let v = value as? String {
                // collect() already redacted — extra redact is a no-op defense-in-depth
                jsonDict[key] = DiagnosticsRedactor.redact(v)
            }
        }

        guard let data = try? JSONSerialization.data(withJSONObject: jsonDict, options: .prettyPrinted),
              let jsonString = String(data: data, encoding: .utf8) else {
            return "{}"
        }
        return jsonString
    }

    // MARK: - Persist + export

    /// Persist the collected diagnostics to the diagnostics directory.
    /// Writes: diagnostics.json, launcher_log.txt, daemon_stderr.txt,
    /// daemon_stdout.txt, daemon_log_tail.txt
    /// Returns the output directory URL.
    public func persist() throws -> URL {
        let dir = diagnosticsDirectory
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let json = exportJSON()
        try json.write(to: dir.appendingPathComponent("diagnostics.json"), atomically: true, encoding: .utf8)

        if let log = launchLogContent {
            try log.write(to: dir.appendingPathComponent("launcher_log.txt"), atomically: true, encoding: .utf8)
        }
        if let stderr = daemonStderrContent {
            try stderr.write(to: dir.appendingPathComponent("daemon_stderr.txt"), atomically: true, encoding: .utf8)
        }
        if let stdout = daemonStdoutContent {
            try stdout.write(to: dir.appendingPathComponent("daemon_stdout.txt"), atomically: true, encoding: .utf8)
        }
        if let logTail = daemonLogTailContent {
            try logTail.write(to: dir.appendingPathComponent("daemon_log_tail.txt"), atomically: true, encoding: .utf8)
        }

        return dir
    }

    /// Export diagnostics as a redacted ZIP bundle.
    /// Contains: diagnostics.json (summary), launcher_log.txt, daemon_stderr.txt,
    /// daemon_log_tail.txt (if any). No live daemon required.
    public func exportZip(to outputURL: URL) throws {
        let dir = try persist()
        defer { try? FileManager.default.removeItem(at: dir) }

        // Use `ditto -c -k` to create a zip archive (available on all macOS systems).
        // This is more reliable than NSFileCoordinator for programmatic zip creation.
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-c", "-k", dir.path, outputURL.path]
        try process.run()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            throw DiagnosticsExportError.zipCreationFailed
        }
    }

    // MARK: - Private helpers

    /// Returns the machine architecture as "arm64" or "x86_64".
    private static func machineArchitecture() -> String {
        var sysInfo = utsname()
        uname(&sysInfo)
        let mirror = Mirror(reflecting: sysInfo.machine)
        let identifier = mirror.children.reduce("") { identifier, element in
            guard let value = element.value as? Int8, value != 0 else { return identifier }
            return identifier + String(UnicodeScalar(UInt8(value)))
        }
        return identifier
    }

    /// Build a redacted env/PATH summary.
    /// Captures PATH + a small whitelist of operational vars, redacting everything.
    private static func buildEnvPathSummary() -> String {
        let env = ProcessInfo.processInfo.environment
        var lines: [String] = []

        // PATH is the critical diagnostic field
        if let path = env["PATH"] {
            lines.append("PATH=\(DiagnosticsRedactor.redact(path))")
        }
        // HOME for context
        if let home = env["HOME"] {
            lines.append("HOME=\(DiagnosticsRedactor.redact(home))")
        }
        // Whitelist operational HappyRanch vars
        for key in ["HAPPYRANCH_DAEMON_HOME", "HAPPYRANCH_WEB_DIST", "PACKAGING_MODE"] {
            if let value = env[key] {
                lines.append("\(key)=\(DiagnosticsRedactor.redact(value))")
            }
        }

        if lines.isEmpty {
            return "(no environment available)"
        }
        return lines.joined(separator: "\n")
    }
}

/// Errors during diagnostics export.
public enum DiagnosticsExportError: Error {
    case zipCreationFailed
}
