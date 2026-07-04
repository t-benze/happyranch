import SwiftUI
import HappyRanchSupervisor

/// Diagnostics panel showing app version, daemon state, logs, health probe results,
/// and crash/failure details. All sensitive data is redacted before display.
struct DiagnosticsView: View {
    let diagnostics: DiagnosticsCollector
    let supervisor: DaemonSupervisor
    @State private var exportMessage: String?
    @State private var refreshID = UUID()

    var body: some View {
        let bundle = diagnostics.collect()
        let _ = refreshID

        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack {
                Text("Diagnostics")
                    .font(.title2)
                    .bold()
                Spacer()
                Button("Export Bundle") {
                    exportDiagnostics()
                }
                .buttonStyle(.bordered)
            }
            .padding()

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 12) {

                    // Crash / failure indicator (Req 3)
                    if supervisor.state == .crashed || supervisor.state == .failed {
                        crashIndicator
                    }

                    GroupBox("App") {
                        infoRow("Version", bundle["app_version"] as? String ?? "—")
                        infoRow("Build", bundle["app_build"] as? String ?? "—")
                        infoRow("macOS", bundle["macos_version"] as? String ?? "—")
                        infoRow("Architecture", bundle["architecture"] as? String ?? "—")
                        if let sha = bundle["build_sha"] as? String {
                            infoRow("Build SHA", sha)
                        }
                        infoRow("Runtime Home", bundle["runtime_home"] as? String ?? "—")
                        if let path = bundle["active_runtime_path"] as? String {
                            infoRow("Runtime Path", path)
                        }
                        if let cmd = bundle["start_command"] as? String {
                            infoRow("Start Command", cmd)
                        }
                    }

                    GroupBox("Daemon") {
                        infoRow("State", supervisor.state.description)
                        if let pid = bundle["daemon_pid"] {
                            infoRow("PID", "\(pid)")
                        }
                        if let port = bundle["daemon_port"] {
                            infoRow("Port", "\(port)")
                        }
                        infoRow("Bind Host", bundle["daemon_bind_host"] as? String ?? "127.0.0.1")
                        if let code = bundle["last_exit_code"] {
                            infoRow("Last Exit Code", "\(code)")
                        }
                        if let sig = bundle["last_exit_signal"] {
                            infoRow("Last Signal", "\(sig)")
                        }
                    }

                    // Daemon stderr (Req 3)
                    if let stderr = bundle["daemon_stderr"] as? String, !stderr.isEmpty {
                        GroupBox("Daemon Stderr (captured)") {
                            ScrollView {
                                Text(stderr)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 200)
                        }
                    }

                    GroupBox("Health Probe") {
                        if let success = bundle["last_health_probe_success"] as? Bool {
                            HStack {
                                Text("Result:")
                                Image(systemName: success ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundColor(success ? .green : .red)
                                Text(success ? "Success" : "Failed")
                            }
                            .font(.caption)
                        }
                        if let latency = bundle["last_health_probe_latency_ms"] {
                            infoRow("Latency", "\(latency) ms")
                        }
                        if let error = bundle["last_health_probe_error"] as? String {
                            infoRow("Error", error)
                        }
                    }

                    if let log = bundle["daemon_log_tail"] as? String, !log.isEmpty {
                        GroupBox("Daemon Log (tail, redacted)") {
                            ScrollView {
                                Text(log)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 150)
                        }
                    }

                    if let log = bundle["launcher_log"] as? String, !log.isEmpty {
                        GroupBox("Launcher Log (redacted)") {
                            ScrollView {
                                Text(log)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 150)
                        }
                    }

                    // On-disk diagnostics path (Req 3)
                    GroupBox("Diagnostics Location") {
                        HStack(alignment: .top) {
                            Text("Path:")
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .frame(width: 60, alignment: .leading)
                            Text(diagnostics.diagnosticsDirectory.path)
                                .font(.caption)
                                .textSelection(.enabled)
                        }
                        .padding(.vertical, 4)
                    }

                    // Env/PATH summary
                    if let envSummary = bundle["env_path_summary"] as? String, !envSummary.isEmpty {
                        GroupBox("Environment Summary (redacted)") {
                            ScrollView {
                                Text(envSummary)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 100)
                        }
                    }
                }
                .padding()
            }

            // Export status
            if let msg = exportMessage {
                Text(msg)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .padding(.horizontal)
                    .padding(.bottom, 8)
            }
        }
        .frame(width: 600, height: 600)
        .onAppear {
            refreshID = UUID()
        }
    }

    // MARK: - Crash indicator

    private var crashIndicator: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.red)
                .font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text(supervisor.state == .crashed
                     ? "Daemon Crashed"
                     : "Daemon Failed")
                    .font(.headline)
                    .foregroundColor(.red)
                Text("Review the captured stderr and diagnostics below to identify the cause.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.red.opacity(0.1))
        )
    }

    // MARK: - Helpers

    private func infoRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label + ":")
                .font(.caption)
                .foregroundColor(.secondary)
                .frame(width: 120, alignment: .leading)
            Text(value)
                .font(.caption)
                .textSelection(.enabled)
        }
    }

    private func exportDiagnostics() {
        let savePanel = NSSavePanel()
        savePanel.title = "Export Diagnostics"
        let dateStr = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        savePanel.nameFieldStringValue = "happyranch-diagnostics-\(dateStr).zip"
        savePanel.allowedContentTypes = []

        savePanel.begin { response in
            if response == .OK, let url = savePanel.url {
                do {
                    try diagnostics.exportZip(to: url)
                    exportMessage = "Exported to \(url.lastPathComponent)"
                } catch {
                    exportMessage = "Export failed: \(error.localizedDescription)"
                }
            }
        }
    }
}
