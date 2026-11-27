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
        Text(NativeCopy.remoteAccessHome.text)
            .font(.title2)
            .fontWeight(.semibold)

        if let ip = appDelegate.tailnetSelfIP {
            Label(NativeCopy.tailnetAddress.format(value: ip), systemImage: "network")
                .font(.callout)
                .foregroundColor(.secondary)
        }

        // Connector controls
        HStack(spacing: 12) {
            if appDelegate.homeConnector == nil {
                Button(NativeCopy.startConnector.text) {
                    appDelegate.startHomeConnector()
                }
                .buttonStyle(.borderedProminent)
            } else {
                Button(NativeCopy.stopConnector.text) {
                    appDelegate.stopHomeConnector()
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }

            if appDelegate.homeConnector != nil {
                Text(NativeCopy.listeningPort.format(value: String(appDelegate.homeConnectorPort)))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }

        Divider()

        // Pairing code section
        if appDelegate.homeConnector != nil {
            Text(NativeCopy.devicePairing.text)
                .font(.headline)

            HStack(spacing: 12) {
                Button(NativeCopy.generatePairingCode.text) {
                    appDelegate.generatePairingCodeAction()
                }
                .buttonStyle(.bordered)

                if let code = appDelegate.pairingCode {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(NativeCopy.pairingCodeLabel.text)
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(code)
                            .font(.system(.title3, design: .monospaced))
                            .fontWeight(.bold)
                            .textSelection(.enabled)

                        if let ip = appDelegate.tailnetSelfIP {
                            Text(NativeCopy.shareAddress.format(value: "\(ip):\(appDelegate.homeConnectorPort)"))
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

                    Button(NativeCopy.copyAddress.text) {
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

            Text(NativeCopy.pairingExpiry.text)
                .font(.caption2)
                .foregroundColor(.secondary)
        }

        // Paired devices list
        if appDelegate.homeConnector != nil {
            Divider()

            Text(NativeCopy.pairedDevices.text)
                .font(.headline)

            let devices = appDelegate.pairingStore.pairedDevices()
            if devices.isEmpty {
                Text(NativeCopy.noPairedDevices.text)
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
                        Button(NativeCopy.revoke.text) {
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
            Picker(NativeCopy.connection.text, selection: $appDelegate.clientLane) {
                Text(NativeCopy.homeInvite.text).tag(ClientBridge.Lane.managed)
                Text(NativeCopy.existingDIY.text).tag(ClientBridge.Lane.diy)
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
    @EnvironmentObject var appDelegate: AppDelegate
    @ObservedObject var model: HomeInviteModel
    @State private var draft = ""
    @State private var replacementConsent = false
    @State private var connectConsent = false
    @State private var pendingInvite = ""
    @State private var removalConsent = false
    @State private var actionRefusal: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(NativeCopy.connectComputer.text).font(.title2).fontWeight(.semibold)
                Text(NativeCopy.phaseMessage(model.phase)).accessibilityIdentifier("home-invite-status")
                if let target = model.target {
                    Text(NativeCopy.controlHost.format(value: URL(string: target.controlURL)?.host ?? ""))
                    Text(NativeCopy.homeAddress.format(value: target.dialIP))
                    Text(NativeCopy.homeIdentity.format(value: "\(target.homeID) / \(target.controlPlaneID)"))
                    Text(NativeCopy.releaseRuntime.format(value: target.homeEndpoint))
                }
                if let expiry = model.expiresAt {
                    TimelineView(.periodic(from: .now, by: 1)) { timeline in
                        let remaining = max(0, Int(ceil(expiry.timeIntervalSince(timeline.date))))
                        Text(NativeCopy.inviteExpiry.format(value: String(format: "%d:%02d", remaining / 60, remaining % 60)))
                    }
                }
                if model.phase == .awaitingApproval {
                    Text(NativeCopy.waitingApproval.format(value: model.target?.homeEndpoint ?? NativeCopy.yourHomeComputer.text))
                    if let phrase = model.comparisonPhrase {
                        Text(phrase).monospaced().textSelection(.enabled)
                    }
                    Text(NativeCopy.comparePhrase.text).font(.caption)
                }
                if let actionRefusal { Text(actionRefusal).foregroundStyle(.red) }
                if model.busy { ProgressView() }
                if !model.busy, model.phase != .connected {
                    SecureField(NativeCopy.pasteInvite.text, text: $draft)
                        .textFieldStyle(.roundedBorder)
                        .privacySensitive()
                    Text(NativeCopy.trustInvite.text)
                        .font(.caption).foregroundStyle(.secondary)
                    Button(NativeCopy.connect.text) {
                        let input = draft
                        draft = ""
                        if model.needsReplacementConsent { pendingInvite = input; connectConsent = true }
                        else { model.accept(input) }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(draft.isEmpty)
                    .confirmationDialog(NativeCopy.freshAccessConsent.text, isPresented: $connectConsent) {
                        Button(NativeCopy.continueFreshApproval.text) {
                            let input = pendingInvite; pendingInvite = ""
                            model.accept(input, replacementConsent: true)
                        }
                        Button(NativeCopy.cancel.text, role: .cancel) { pendingInvite = "" }
                    }
                    Text(NativeCopy.localize(model.retryGuidance)).font(.caption)
                    Button(NativeCopy.retry.text) { draft = ""; model.retry() }
                }
                if model.bridgeCanCancel || model.busy {
                    Button(NativeCopy.cancel.text) { draft = ""; model.cancel() }
                        .disabled(model.phase == .stopping)
                } else if model.hasActiveSession {
                    Button(NativeCopy.disconnect.text) {
                        draft = ""
                        Task { actionRefusal = await appDelegate.disconnectHomeSelection() }
                    }
                    if model.saveFailure == nil, !model.savedHomes.isEmpty {
                        Text(NativeCopy.disconnectKeepsAccess.text).font(.caption)
                    }
                }
                if let failure = model.saveFailure {
                    Text(NativeCopy.localize(failure.localizedDescription)).foregroundStyle(.red)
                    if failure == .identityChanged {
                        Button(NativeCopy.replaceSavedAccessPrompt.text) { replacementConsent = true }
                            .confirmationDialog(NativeCopy.replaceAccessConsent.text, isPresented: $replacementConsent) {
                                Button(NativeCopy.replaceSavedAccess.text, role: .destructive) { model.retrySave(replacing: true) }
                            }
                    } else {
                        Button(NativeCopy.retrySaving.text) { model.retrySave() }
                    }
                }
                if !model.discoveryFailures.isEmpty {
                    Text(NativeCopy.someAccessUnavailable.text).font(.caption)
                    ForEach(model.discoveryFailures, id: \.self) { Text(NativeCopy.localize($0.localizedDescription)).font(.caption) }
                }
                if !model.savedHomes.isEmpty {
                    Divider()
                    Picker(NativeCopy.savedComputer.text, selection: $model.selectedHomeID) {
                        ForEach(model.savedHomes) { home in
                            Text(home.trust.homeEndpoint).tag(Optional(home.id))
                        }
                    }.disabled(model.busy || model.phase == .connected)
                    Button(NativeCopy.reconnect.text) { draft = ""; model.reconnect() }
                        .disabled(model.busy || model.phase == .connected)
                    Button(NativeCopy.removeComputerPrompt.text, role: .destructive) { removalConsent = true }
                        .disabled(model.busy)
                        .confirmationDialog(NativeCopy.removeConsent.text, isPresented: $removalConsent) {
                            Button(NativeCopy.removeComputer.text, role: .destructive) {
                                draft = ""
                                Task { actionRefusal = await appDelegate.removeHomeSelection() }
                            }
                        }
                }
                if !model.removalFailures.isEmpty {
                    Text(NativeCopy.removalIncomplete.text).foregroundStyle(.red)
                    ForEach(model.removalFailures, id: \.self) { Text(NativeCopy.localize($0)).font(.caption) }
                    Button(NativeCopy.retryRemoval.text) {
                        Task { actionRefusal = await appDelegate.removeHomeSelection() }
                    }.disabled(model.busy)
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
            Text(NativeCopy.existingDIY.text).font(.headline)
            if appDelegate.isConnecting { ProgressView(NativeCopy.connecting.text) }
            if let error = appDelegate.clientConnectError {
                Text(NativeCopy.localize(error)).foregroundStyle(.red)
            }
            if appDelegate.clientBridge == nil {
                TextField(NativeCopy.homeTailnetAddress.text, text: $appDelegate.clientHomeHost).textFieldStyle(.roundedBorder)
                TextField(NativeCopy.homeConnectorPort.text, text: $appDelegate.clientHomePort).textFieldStyle(.roundedBorder)
                TextField(NativeCopy.pairingCode.text, text: $appDelegate.clientPairingCode).textFieldStyle(.roundedBorder)
                Button(NativeCopy.connect.text) { appDelegate.connectToRemote() }.disabled(appDelegate.isConnecting)
            } else {
                Text(appDelegate.connectionState == .online ? NativeCopy.connected.text : NativeCopy.disconnected.text)
                Button(NativeCopy.disconnect.text) { appDelegate.disconnectRemote() }
            }
        }
    }
}
