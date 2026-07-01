import SwiftUI
import HappyRanchSupervisor

/// Diagnostics panel showing app version, daemon state, logs, and health probe results.
/// All sensitive data is redacted before display.
struct DiagnosticsView: View {
    let diagnostics: DiagnosticsCollector
    let supervisor: DaemonSupervisor
    @State private var exportMessage: String?
    @State private var refreshID = UUID()

    var body: some View {
        let bundle = diagnostics.collect()
        // Force refresh when the sheet appears
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
                    GroupBox("App") {
                        infoRow("Version", bundle["app_version"] as? String ?? "—")
                        infoRow("Build", bundle["app_build"] as? String ?? "—")
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
        .frame(width: 560, height: 520)
        .onAppear {
            refreshID = UUID()
        }
    }

    private func infoRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label + ":")
                .font(.caption)
                .foregroundColor(.secondary)
                .frame(width: 100, alignment: .leading)
            Text(value)
                .font(.caption)
                .textSelection(.enabled)
        }
    }

    private func exportDiagnostics() {
        let json = diagnostics.exportJSON()

        let savePanel = NSSavePanel()
        savePanel.title = "Export Diagnostics"
        savePanel.nameFieldStringValue = "happyranch-diagnostics-\(ISO8601DateFormatter().string(from: Date())).json"
        savePanel.allowedContentTypes = [.json]

        savePanel.begin { response in
            if response == .OK, let url = savePanel.url {
                do {
                    try json.write(to: url, atomically: true, encoding: .utf8)
                    exportMessage = "Exported to \(url.lastPathComponent)"
                } catch {
                    exportMessage = "Export failed: \(error.localizedDescription)"
                }
            }
        }
    }
}
