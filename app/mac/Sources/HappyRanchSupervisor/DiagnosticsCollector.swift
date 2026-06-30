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
        lastHealthProbeError = errorMessage
    }

    public func recordExit(exitCode: Int32, signal: Int32?) {
        self.lastExitCode = exitCode
        lastExitSignal = signal
    }

    public func recordStartCommand(_ command: String) {
        startCommand = command
    }

    public func recordActiveRuntimePath(_ path: String) {
        activeRuntimePath = path
    }

    public func recordToken(_ token: String) {
        tokenValue = DiagnosticsRedactor.redact(token)
    }

    // MARK: - Collect

    /// Returns a dictionary of all collected diagnostics.
    /// All string values are redacted before inclusion.
    public func collect() -> [String: Any] {
        var bundle: [String: Any] = [:]

        bundle["app_version"] = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.1.0"
        bundle["app_build"] = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
        bundle["runtime_home"] = homeDir

        if let pid = daemonPid { bundle["daemon_pid"] = pid }
        if let port = daemonPort { bundle["daemon_port"] = port }
        if let host = daemonBindHost { bundle["daemon_bind_host"] = host }
        if let st = daemonStateValue { bundle["daemon_state"] = st }

        if let log = launchLogContent { bundle["launcher_log"] = log }
        if let log = daemonLogTailContent { bundle["daemon_log_tail"] = log }

        if let success = lastHealthProbeSuccess {
            bundle["last_health_probe_success"] = success
        }
        if let latency = lastHealthProbeLatencyMs {
            bundle["last_health_probe_latency_ms"] = latency
        }
        if let error = lastHealthProbeError {
            bundle["last_health_probe_error"] = error
        }

        if let code = lastExitCode { bundle["last_exit_code"] = code }
        if let sig = lastExitSignal { bundle["last_exit_signal"] = sig }

        if let cmd = startCommand { bundle["start_command"] = cmd }
        if let path = activeRuntimePath { bundle["active_runtime_path"] = path }
        if let token = tokenValue { bundle["token"] = token }

        return bundle
    }

    /// Exports the diagnostics bundle as a redacted JSON string.
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
