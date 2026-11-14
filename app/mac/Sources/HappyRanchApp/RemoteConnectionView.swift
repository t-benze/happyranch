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

    private var clientView: some View {
        RemoteClientForm(model: appDelegate.managedJoin)
    }
}

/// The same remote form for both lanes. Only this private, non-restored draft
/// holds the SecureField text; no enrollment text enters AppDelegate state.
private struct RemoteClientForm: View {
    @EnvironmentObject var appDelegate: AppDelegate
    @ObservedObject var model: ManagedJoinModel
    @State private var enrollmentKey = ""
    @State private var confirmingRevocation = false

    private var managed: Bool { appDelegate.clientLane == .managed }
    private var busy: Bool { appDelegate.isConnecting || model.busy }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Connect to Remote Runtime").font(.title2).fontWeight(.semibold)
                Picker("Network", selection: $appDelegate.clientLane) {
                    Text("DIY Tailscale").tag(ClientBridge.Lane.diy)
                    Text("Managed network").tag(ClientBridge.Lane.managed)
                }
                .pickerStyle(.segmented)
                .disabled(busy || appDelegate.clientBridge != nil || model.bridge != nil)

                if managed { managedSection }
                else if appDelegate.isConnecting { ProgressView("Connecting…") }

                if !managed, appDelegate.connectionState != .tailnetNotDetected || appDelegate.clientBridge != nil {
                    HStack(spacing: 6) {
                        Circle().fill(appDelegate.connectionState == .online ? Color.green : Color.gray).frame(width: 8, height: 8)
                        Text(diyStateLabel).font(.caption).foregroundColor(.secondary)
                    }
                }
                if let error = appDelegate.clientConnectError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundColor(.red).font(.callout)
                }

                if appDelegate.clientBridge == nil {
                    targetFields
                    Text("Pairing Code").font(.caption).foregroundColor(.secondary)
                    if managed {
                        SecureField("8-character code from home", text: $appDelegate.clientPairingCode)
                            .textFieldStyle(.roundedBorder).frame(maxWidth: 300)
                            .disabled(busy || !model.canPair)
                    } else {
                        TextField("8-character code from home", text: $appDelegate.clientPairingCode)
                            .textFieldStyle(.roundedBorder).frame(maxWidth: 300)
                    }
                    Button(managed ? "Pair & Connect" : "Connect") { appDelegate.connectToRemote() }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || (managed && !model.canPair))
                } else {
                    Label(managed ? model.phase.message : "Connected to home runtime",
                          systemImage: appDelegate.connectionState == .online ? "checkmark.circle.fill" : "network")
                    Button("Disconnect") { appDelegate.disconnectRemote() }
                        .buttonStyle(.bordered).tint(.red)
                }
                if managed, busy, model.phase != .checkingKeychain {
                    Button("Cancel") {
                        enrollmentKey = ""
                        appDelegate.cancelManagedConnection()
                    }.disabled(model.phase == .canceling)
                }
                if managed, model.bridge != nil, !busy {
                    Button("Device was revoked / Rejoin") { confirmingRevocation = true }
                        .foregroundColor(.red)
                        .confirmationDialog("Only confirm if your operator revoked this device. Denial or timeout alone does not prove revocation.",
                                            isPresented: $confirmingRevocation) {
                            Button("Confirm device was revoked", role: .destructive) {
                                enrollmentKey = ""
                                appDelegate.confirmManagedRevocation()
                            }
                            Button("Cancel", role: .cancel) {}
                        }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { if managed { model.prepare() } }
        .onChange(of: appDelegate.clientLane) { _, lane in
            enrollmentKey = ""
            appDelegate.clientConnectError = nil
            if lane == .managed { model.prepare() }
        }
        .onDisappear { enrollmentKey = "" }
    }

    private var diyStateLabel: String {
        if appDelegate.isConnecting { return "Connecting…" }
        switch appDelegate.connectionState {
        case .online: return "Connected"
        case .reconnecting: return "Reconnecting…"
        case .offline: return "Disconnected"
        case .pairedUnreachable: return "Unreachable"
        case .tailnetNotDetected: return "Tailscale not detected"
        }
    }

    private var targetFields: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Home Tailnet Address").font(.caption).foregroundColor(.secondary)
            TextField("e.g. 100.64.0.1 or full tailnet DNS name", text: $appDelegate.clientHomeHost)
                .textFieldStyle(.roundedBorder).frame(maxWidth: 350)
            Text("Home Connector Port").font(.caption).foregroundColor(.secondary)
            TextField("e.g. 8443", text: $appDelegate.clientHomePort)
                .textFieldStyle(.roundedBorder).frame(maxWidth: 200)
        }
        .disabled(managed && (busy || model.bridge != nil))
    }

    @ViewBuilder
    private var managedSection: some View {
        Text(model.phase.message)
            .font(.callout)
            .accessibilityIdentifier("managed-join-status")
        if model.busy { ProgressView().controlSize(.small) }
        if model.bridge == nil, !model.busy {
            Text("1 · Join managed network").font(.headline)
            TextField("Trusted control server URL (https://…)", text: $model.controlURL)
                .textFieldStyle(.roundedBorder)
            if let url = try? ManagedJoinModel.validatedControlURL(model.controlURL),
               let host = URL(string: url)?.host {
                Text("Control server: \(host)").font(.caption).foregroundColor(.secondary)
            }
            SecureField("Single-use enrollment key", text: $enrollmentKey)
                .textFieldStyle(.roundedBorder)
                .disabled(!model.custodyReady)
            Text("Enter the home address and port below before joining. They stay fixed for pairing. The key field is cleared on submission; this does not guarantee forensic memory erasure.")
                .font(.caption).foregroundColor(.secondary)
            Button("Join Network") {
                let bytes = Data(enrollmentKey.utf8)
                enrollmentKey = ""
                appDelegate.clientConnectError = nil
                model.join(key: bytes, homeHost: appDelegate.clientHomeHost, port: appDelegate.clientHomePort)
            }
            .buttonStyle(.borderedProminent)
            .disabled(!model.custodyReady || enrollmentKey.isEmpty)

            if !model.profiles.isEmpty {
                Picker("Saved membership", selection: $model.selectedProfileID) {
                    Text("Choose explicitly…").tag("")
                    ForEach(model.profiles) { profile in
                        Text("\(profile.hostname) · \(profile.phase)").tag(profile.uuid)
                    }
                }
                Button("Check saved membership") {
                    enrollmentKey = ""
                    appDelegate.clientConnectError = nil
                    model.resume(homeHost: appDelegate.clientHomeHost, port: appDelegate.clientHomePort)
                }
                .disabled(!model.custodyReady || model.selectedProfileID.isEmpty)
                Text("No key is replayed. Missing or conflicting saved state is refused. Cancellation cannot undo remote registration; ask your operator to remove an unused device.")
                    .font(.caption).foregroundColor(.secondary)
            }
            Button("Check Keychain and saved profiles") { enrollmentKey = ""; model.refreshCustody() }
        } else if !model.busy {
            Text("2 · Pair with home runtime").font(.headline)
            Button("Change target / Disconnect membership") { appDelegate.disconnectRemote() }
        }
    }
}
