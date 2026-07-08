import SwiftUI
import HappyRanchSupervisor

// MARK: - RemoteConnectionView

/// Role-aware surface for the BYO-Tailscale remote-connection feature.
///
/// Detects the role (HOME vs CLIENT) via ``ConnectionRole/detect(supervisor:)``
/// and renders the appropriate UI.
struct RemoteConnectionView: View {
    @EnvironmentObject var appDelegate: AppDelegate

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            switch appDelegate.connectionRole {
            case .home:
                homeView
            case .client:
                clientView
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding()
    }

    // MARK: - HOME view

    @ViewBuilder
    private var homeView: some View {
        Text("Remote Access (Home)")
            .font(.title2)
            .fontWeight(.semibold)

        if let ip = appDelegate.tailnetSelfIP {
            Label("Tailnet address: \(ip)", systemImage: "network")
                .font(.callout)
                .foregroundColor(.secondary)
        }

        // Connector controls
        HStack(spacing: 12) {
            if appDelegate.homeConnector == nil {
                Button("Start Connector") {
                    appDelegate.startHomeConnector()
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button("Stop Connector") {
                    appDelegate.stopHomeConnector()
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }

            if appDelegate.homeConnector != nil {
                Text("Listening on port \(appDelegate.homeConnectorPort)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }

        Divider()

        // Pairing code section
        if appDelegate.homeConnector != nil {
            Text("Device Pairing")
                .font(.headline)

            HStack(spacing: 12) {
                Button("Generate Pairing Code") {
                    appDelegate.generatePairingCodeAction()
                }
                .buttonStyle(.bordered)

                if let code = appDelegate.pairingCode {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Pairing code:")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(code)
                            .font(.system(.title3, design: .monospaced))
                            .fontWeight(.bold)
                            .textSelection(.enabled)

                        if let ip = appDelegate.tailnetSelfIP {
                            Text("Share: \(ip):\(appDelegate.homeConnectorPort)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                    .padding(8)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color(nsColor: .textBackgroundColor))
                    )

                    Button("Copy Address") {
                        if let ip = appDelegate.tailnetSelfIP {
                            let text = "\(ip):\(appDelegate.homeConnectorPort)"
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(text, forType: .string)
                        }
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }
            }

            Text("Code is one-time use and expires in 5 minutes")
                .font(.caption2)
                .foregroundColor(.secondary)
        }

        // Paired devices list
        if appDelegate.homeConnector != nil {
            Divider()

            Text("Paired Devices")
                .font(.headline)

            let devices = appDelegate.pairingStore.pairedDevices()
            if devices.isEmpty {
                Text("No paired devices")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                List(devices, id: \.credential) { device in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(device.name)
                                .font(.body)
                            Text(device.credential.prefix(20) + "...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .monospaced()
                        }
                        Spacer()
                        Button("Revoke") {
                            appDelegate.revokeDeviceAction(credential: device.credential)
                        }
                        .buttonStyle(.borderless)
                        .foregroundColor(.red)
                    }
                    .padding(.vertical, 2)
                }
                .listStyle(.plain)
                .frame(minHeight: CGFloat(min(devices.count * 40, 200)))
            }
        }
    }

    // MARK: - CLIENT view

    @ViewBuilder
    private var clientView: some View {
        Text("Connect to Remote Runtime")
            .font(.title2)
            .fontWeight(.semibold)

        if appDelegate.isConnecting {
            HStack(spacing: 8) {
                ProgressView()
                    .scaleEffect(0.7)
                    .frame(width: 16, height: 16)
                Text("Connecting...")
                    .font(.callout)
            }
        }

        // Connection state badge
        connectionStateBadge

        // Error display
        if let error = appDelegate.clientConnectError {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundColor(.red)
                Text(error)
                    .font(.callout)
                    .foregroundColor(.red)
            }
            .padding(8)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.red.opacity(0.1))
            )
        }

        if appDelegate.clientBridge == nil {
            // Input form
            VStack(alignment: .leading, spacing: 8) {
                Text("Home Tailnet Address")
                    .font(.caption)
                    .foregroundColor(.secondary)
                TextField("e.g. 100.64.0.1", text: $appDelegate.clientHomeHost)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 300)

                Text("Home Connector Port")
                    .font(.caption)
                    .foregroundColor(.secondary)
                TextField("e.g. 8443", text: $appDelegate.clientHomePort)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 200)

                Text("Pairing Code")
                    .font(.caption)
                    .foregroundColor(.secondary)
                TextField("8-character code from home", text: $appDelegate.clientPairingCode)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 300)

                Button("Connect") {
                    appDelegate.connectToRemote()
                }
                .buttonStyle(.borderedProminent)
                .disabled(appDelegate.isConnecting)
                .padding(.top, 8)
            }
        } else {
            // Connected state
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("Connected to home runtime")
                    .font(.callout)
            }

            Button("Disconnect") {
                appDelegate.disconnectRemote()
            }
            .buttonStyle(.bordered)
            .tint(.red)
        }
    }

    // MARK: - Connection state badge

    @ViewBuilder
    private var connectionStateBadge: some View {
        let state = appDelegate.connectionState
        if state != .tailnetNotDetected || appDelegate.clientBridge != nil {
            HStack(spacing: 6) {
                Circle()
                    .fill(stateColor(for: state, isConnecting: appDelegate.isConnecting))
                    .frame(width: 8, height: 8)
                Text(stateLabel(for: state, isConnecting: appDelegate.isConnecting))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func stateColor(for state: ConnectionState, isConnecting: Bool) -> Color {
        if isConnecting { return .orange }
        switch state {
        case .online: return .green
        case .reconnecting: return .orange
        case .offline, .pairedUnreachable: return .red
        case .tailnetNotDetected: return .gray
        }
    }

    private func stateLabel(for state: ConnectionState, isConnecting: Bool) -> String {
        if isConnecting { return "Connecting…" }
        switch state {
        case .online: return "Connected"
        case .reconnecting: return "Reconnecting…"
        case .offline: return "Disconnected"
        case .pairedUnreachable: return "Unreachable"
        case .tailnetNotDetected: return "Tailscale not detected"
        }
    }
}
