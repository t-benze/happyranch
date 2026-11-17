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
        VStack(alignment: .leading, spacing: 16) {
            Picker("Connection", selection: $appDelegate.clientLane) {
                Text("Home invite").tag(ClientBridge.Lane.managed)
                Text("Existing DIY Tailscale").tag(ClientBridge.Lane.diy)
            }
            .pickerStyle(.segmented)
            .disabled(appDelegate.homeInvite.busy || appDelegate.homeInvite.phase == .connected || appDelegate.clientBridge != nil)
            if appDelegate.clientLane == .managed {
                HomeInviteForm(model: appDelegate.homeInvite)
            } else {
                DIYClientForm()
            }
        }
    }
}

private struct HomeInviteForm: View {
    @ObservedObject var model: HomeInviteModel
    @State private var draft = ""
    @State private var replacementConsent = false
    @State private var connectConsent = false
    @State private var pendingInvite = ""
    @State private var removalConsent = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Connect to another computer").font(.title2).fontWeight(.semibold)
                Text(model.phase.message).accessibilityIdentifier("home-invite-status")
                if let target = model.target {
                    Text("Control host: \(URL(string: target.controlURL)?.host ?? "")")
                    Text("Home address: \(target.dialIP)")
                    Text("Home identity: \(target.homeID) / \(target.controlPlaneID)")
                    Text("Release runtime: \(target.homeEndpoint)")
                }
                if let expiry = model.expiresAt {
                    TimelineView(.periodic(from: .now, by: 1)) { timeline in
                        let remaining = max(0, Int(ceil(expiry.timeIntervalSince(timeline.date))))
                        Text(String(format: "Invite expires in %d:%02d", remaining / 60, remaining % 60))
                    }
                }
                if model.phase == .awaitingApproval {
                    Text("Waiting for approval on \(model.target?.homeEndpoint ?? "your home computer")")
                    if let phrase = model.comparisonPhrase {
                        Text(phrase).monospaced().textSelection(.enabled)
                    }
                    Text("Compare this phrase on Linux, then approve this device there.").font(.caption)
                }
                if model.busy { ProgressView() }
                if !model.busy, model.phase != .connected {
                    SecureField("Paste one invite from Linux", text: $draft)
                        .textFieldStyle(.roundedBorder)
                        .privacySensitive()
                    Text("Trust an invite only from your home computer. Its sender selects the control host and home CA.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Connect") {
                        let input = draft
                        draft = ""
                        if model.needsReplacementConsent { pendingInvite = input; connectConsent = true }
                        else { model.accept(input) }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(draft.isEmpty)
                    .confirmationDialog("Saved access could not be opened. Create new local access after fresh Linux approval? The obsolete host grant remains revocable on Linux; the old item is never silently replaced.", isPresented: $connectConsent) {
                        Button("Continue with fresh approval") {
                            let input = pendingInvite; pendingInvite = ""
                            model.accept(input, replacementConsent: true)
                        }
                        Button("Cancel", role: .cancel) { pendingInvite = "" }
                    }
                    Text(model.retryGuidance).font(.caption)
                    Button("Retry") { draft = ""; model.retry() }
                }
                if model.busy {
                    Button("Cancel") { draft = ""; model.cancel() }
                        .disabled(model.phase == .stopping)
                } else if model.phase == .connected {
                    Button("Disconnect") { draft = ""; model.cancel() }
                    if model.saveFailure == nil, !model.savedHomes.isEmpty {
                        Text("Disconnect keeps saved access.").font(.caption)
                    }
                }
                if let failure = model.saveFailure {
                    Text(failure.localizedDescription).foregroundStyle(.red)
                    if failure == .identityChanged {
                        Button("Replace saved access…") { replacementConsent = true }
                            .confirmationDialog("Replace the old local item with this freshly approved device? The old host grant remains revocable on Linux.", isPresented: $replacementConsent) {
                                Button("Replace saved access", role: .destructive) { model.retrySave(replacing: true) }
                            }
                    } else {
                        Button("Retry saving") { model.retrySave() }
                    }
                }
                if !model.discoveryFailures.isEmpty {
                    Text("Some saved access is unavailable. Other approved computers can still reconnect.").font(.caption)
                    ForEach(model.discoveryFailures, id: \.self) { Text($0.localizedDescription).font(.caption) }
                }
                if !model.savedHomes.isEmpty {
                    Divider()
                    Picker("Saved computer", selection: $model.selectedHomeID) {
                        ForEach(model.savedHomes) { home in
                            Text(home.trust.homeEndpoint).tag(Optional(home.id))
                        }
                    }.disabled(model.busy || model.phase == .connected)
                    Button("Reconnect") { draft = ""; model.reconnect() }
                        .disabled(model.busy || model.phase == .connected)
                    Button("Remove saved computer…", role: .destructive) { removalConsent = true }
                        .disabled(model.busy)
                        .confirmationDialog("Erase local access? This does not revoke the device on Linux.", isPresented: $removalConsent) {
                            Button("Remove saved computer", role: .destructive) { draft = ""; model.removeSavedComputer() }
                        }
                }
                if !model.removalFailures.isEmpty {
                    Text("Removal incomplete").foregroundStyle(.red)
                    ForEach(model.removalFailures, id: \.self) { Text($0).font(.caption) }
                    Button("Retry removal") { model.removeSavedComputer() }.disabled(model.busy)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { model.prepare() }
        .onDisappear { draft = ""; pendingInvite = "" }
        .onChange(of: model.phase) { _, phase in
            if phase == .connected || phase == .failed(.expiredInvite) || phase == .stopping { draft = ""; pendingInvite = "" }
        }
    }
}

/// Explicit legacy DIY selection is retained; managed failure never selects it.
private struct DIYClientForm: View {
    @EnvironmentObject var appDelegate: AppDelegate
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Existing DIY Tailscale").font(.headline)
            if appDelegate.isConnecting { ProgressView("Connecting…") }
            if let error = appDelegate.clientConnectError {
                Text(error).foregroundStyle(.red)
            }
            if appDelegate.clientBridge == nil {
                TextField("Home Tailnet Address", text: $appDelegate.clientHomeHost).textFieldStyle(.roundedBorder)
                TextField("Home Connector Port", text: $appDelegate.clientHomePort).textFieldStyle(.roundedBorder)
                TextField("Pairing Code", text: $appDelegate.clientPairingCode).textFieldStyle(.roundedBorder)
                Button("Connect") { appDelegate.connectToRemote() }.disabled(appDelegate.isConnecting)
            } else {
                Text(appDelegate.connectionState == .online ? "Connected" : "Disconnected")
                Button("Disconnect") { appDelegate.disconnectRemote() }
            }
        }
    }
}
