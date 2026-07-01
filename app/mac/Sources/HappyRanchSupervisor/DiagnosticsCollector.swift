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

    public func recordToken(_ token: String) {
        tokenValue = DiagnosticsRedactor.redact(token)
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

        if let pid = daemonPid { bundle["daemon_pid"] = pid }
        if let port = daemonPort { bundle["daemon_port"] = port }
        if let host = daemonBindHost { bundle["daemon_bind_host"] = DiagnosticsRedactor.redact(host) }
        if let st = daemonStateValue { bundle["daemon_state"] = DiagnosticsRedactor.redact(st) }

        if let log = launchLogContent { bundle["launcher_log"] = DiagnosticsRedactor.redact(log) }
        if let log = daemonLogTailContent { bundle["daemon_log_tail"] = DiagnosticsRedactor.redact(log) }

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
}
