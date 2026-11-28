import AppKit
import Combine
import HappyRanchSupervisor
@preconcurrency import Sparkle

/// Application delegate — owns the daemon supervisor and app-level state.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, ObservableObject, SPUUpdaterDelegate, MacLocalControlBackend, MacAppLifetimeBackend {

    let supervisor = DaemonSupervisor()
    lazy var localControls = MacLocalControls(backend: self)
    lazy var appLifetime = MacAppLifetime(backend: self)
    var terminationDelivery: any MacTerminationDelivery = AppKitTerminationDelivery()
    private var mainWindowOwner: MacMainWindowOwner?
    private var lifecycleAlert: NSAlert?
    private var lifecycleAlertDeadline: Task<Void, Never>?
    private enum TerminationAdmission: Equatable { case quit, update }
    private var admittedTermination: TerminationAdmission?
    private lazy var updaterController = SPUStandardUpdaterController(
        startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
    private var updaterObservation: AnyCancellable?
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var hasPendingUpdate = false
    private var pendingUpdateInstall: (() -> Void)?
    private var updateRequestID = UUID()
    private var updateTask: Task<Void, Never>?
    private var updateStopping = false
    private var waitingForUpdateIdle = false
    private var terminating = false
    let diagnostics = DiagnosticsCollector(homeDir: daemonHome())

    /// Transport for constructing daemon URLs. Only LocalLoopbackTransport is wired.
    let localTransport: RuntimeTransport = LocalLoopbackTransport()

    /// Injected process controller — wraps Foundation.Process in production,
    /// replaced with a FakeProcessController in tests.
    var processController: ProcessControlling = RealProcessController()

    /// The handle for the currently active managed daemon process.
    /// Set on each launch; used to guard stale exit callbacks by identity.
    /// Internal to allow tests to inject a handle for termination-path coverage.
    var currentHandle: (any ProcessHandle)?
    /// Shorter values are injected only by fake-child fixtures.
    var managedStopGrace = ManagedProcessStop.Grace()

    /// Selected native endpoint/owner identity, never the WebKit page URL.
    @Published var webViewURL: String?
    var appAPITransport: (any AppAPITransport)? {
        if let selected = homeInvite.appTransport { return selected }
        if connectionRole == .client {
            return clientBridge
        }
        guard let value = webViewURL, let url = URL(string: value), url.host == "127.0.0.1",
              let port = url.port, let checked = UInt16(exactly: port) else { return nil }
        return LocalAppAPITransport(port: checked, credential: LocalTokenCredentialProvider(homeDir: Self.daemonHome()))
    }
    @Published var stateText: String = DaemonState.notConfigured.description
    @Published var showDiagnostics = false
    @Published var showRemoteAccess = false
    private var connectionWindow: ConnectionWindow?
    private weak var mainScheme: AppScheme?
    private let mainComputerCatalog = ConnectionComputerCatalog()

    func bindMainScheme(_ scheme: AppScheme) {
        mainScheme = scheme
        scheme.connectionApp = self
        scheme.candidateSnapshot = { [weak self] in self?.homeInvite.bridgeCandidate }
        if homeInvite.selectedFailure != nil, !homeInvite.hasActiveSession { scheme.selectedUnavailable() }
        scheme.switchCandidate = { [weak self] in
            guard let self else { throw AppAPIError.unavailable }
            let target = try self.homeInvite.commitCandidate()
            if let old = self.clientBridge {
                self.clientBridge = nil
                self.homeInvite.retireBridge(old)
                if self.managedJoin.bridge === old { self.managedJoin.disconnect() }
            }
            return target
        }
        scheme.disconnectSelected = { [weak self] in
            guard let self, self.homeInvite.canDisconnect else { return false }
            self.homeInvite.disconnectActive()
            self.remoteHealthTask?.cancel(); self.remoteHealthTask = nil
            if self.managedJoin.bridge != nil || self.managedJoin.busy { self.managedJoin.disconnect() }
            if let old = self.clientBridge { self.homeInvite.retireBridge(old); self.clientBridge = nil }
            return true
        }
        resumeStartupSelection()
        scheme.actionResult = { [weak self] in
            guard let self else { return [:] }
            let outcomes = self.homeInvite.cleanupOutcomes.values
            let cleanup = outcomes.contains("incomplete") ? "incomplete" : outcomes.contains("unknown") ? "unknown" : outcomes.isEmpty ? "notApplicable" : "confirmed"
            return ConnectionClientProjection.savedAction(self.homeInvite, catalog: self.mainComputerCatalog, localCleanup: cleanup)
        }
    }

    func bindMainWindow(_ window: NSWindow) {
        guard mainWindowOwner?.window !== window else { return }
        mainWindowOwner = MacMainWindowOwner(app: self, window: window)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let admission = admittedTermination {
            admittedTermination = nil
            if admission == .quit || updateStopping { return .terminateNow }
        }
        appLifetime.request(.quit)
        return .terminateCancel
    }

    /// Shared native entries use exactly the same Main owner/transfer gate.
    func disconnectSelectedTarget() -> String? {
        guard let mainScheme else { return "owner_save_unknown" }
        return mainScheme.disconnect()
    }

    func disconnectHomeSelection() async -> String? {
        await homeInvite.disconnectSelected(using: mainScheme)
    }

    func removeHomeSelection() async -> String? {
        await homeInvite.removeSavedSelection(using: mainScheme)
    }

    /// Explicit direct-connection completion rebinds the retained Main. Candidate
    /// readiness never calls this; its only selection path is commitSwitch.
    func selectMainTransport(id: String, transport: any AppAPITransport, remote: Bool, promptURL: String? = nil) -> String? {
        let refusal = mainScheme?.selectTransport(id: id, transport: transport, remote: remote, promptURL: promptURL)
        if refusal == nil {
            startupProfile = nil
            startupSelection.commit(remote ? .unsaved : .local)
        }
        return refusal
    }

    func showConnection() {
        requestMainDialog("connection")
    }

    func showSettings() { requestMainDialog("settings") }

    @discardableResult
    func requestMainDialog(_ dialog: String) -> Bool {
        mainScheme?.requestDialog(dialog) == true
    }

    // MARK: - A2 Remote connection state

    /// Persisted role preference (nil = first launch / undetermined).
    /// Stored in UserDefaults under `connectionRolePreference`.
    /// When the user explicitly picks HOME or CLIENT on first launch,
    /// this preference takes priority over supervisor-state heuristics.
    @Published var connectionRolePreference: ConnectionRolePreference? {
        didSet {
            if oldValue != connectionRolePreference {
                connectionWindow?.invalidate()
                mainScheme?.connectionRoleDidChange()
            }
            if let pref = connectionRolePreference {
                UserDefaults.standard.set(pref.rawValue, forKey: "connectionRolePreference")
            } else {
                UserDefaults.standard.removeObject(forKey: "connectionRolePreference")
            }
        }
    }

    /// The connection role of this machine (HOME vs CLIENT).
    /// Consults the explicit ``connectionRolePreference`` first;
    /// falls back to ``DaemonSupervisor/state`` when undetermined.
    var connectionRole: ConnectionRole {
        ConnectionRole.detect(supervisor: supervisor, preference: connectionRolePreference)
    }

    /// Shared pairing store used by both the HomeConnector and the UI.
    let pairingStore = RealPairingStore()

    /// Tailscale status provider (injectable for tests; production = real).
    var tailscaleProvider: TailscaleStatusProviding = TailscaleStatusProvider()

    /// The home connector (only active in HOME role).
    var homeConnector: HomeConnector?

    /// Direct client identity; explicit pairing completion rebinds the stable Main.
    @Published private(set) var appTargetGeneration = UUID()
    var clientBridge: ClientBridge? {
        didSet { if oldValue !== clientBridge { appTargetGeneration = UUID() } }
    }
    let managedJoin = ManagedJoinModel()
    let homeInvite: HomeInviteModel
    private let startupSelection: AppStartupSelection
    private var startupProfile: UUID?
    private var startupCandidate: UUID?
    private var startupBegan = false
    @Published var clientLane: ClientBridge.Lane = .managed {
        didSet { if oldValue != clientLane { connectionWindow?.invalidate() } }
    }
    private var remoteHealthTask: Task<Void, Never>?
    private var managedJoinObservation: AnyCancellable?
    private var homeInviteObservations: Set<AnyCancellable> = []

    override convenience init() { self.init(startupSelection: .shared) }

    init(startupSelection: AppStartupSelection, homeInvite: HomeInviteModel = HomeInviteModel()) {
        self.startupSelection = startupSelection
        self.homeInvite = homeInvite
        super.init()
        homeInvite.startupSelection = startupSelection
        managedJoinObservation = managedJoin.$phase.sink { [weak self] phase in
            guard let self, self.clientLane == .managed, !self.homeInvite.hasActiveSession else { return }
            self.connectionState = phase.connectionState
        }
        homeInvite.$webURL.sink { [weak self] url in
            guard let self, self.clientLane == .managed else { return }
            // Once created, Main survives target loss, Cancel and disconnect.
            if let url { self.webViewURL = url.absoluteString }
        }.store(in: &homeInviteObservations)
        homeInvite.objectWillChange.sink { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                if self.clientLane == .managed { self.connectionState = self.homeInvite.connectionStates.state }
                self.resumeStartupSelection()
                self.objectWillChange.send()
            }
        }.store(in: &homeInviteObservations)
    }

    /// Connection state manager for the remote-connection surface.
    let connectionStateManager = ConnectionStateManager()

    /// The chosen port for the home connector to listen on.
    @Published var homeConnectorPort: UInt16 = 8443

    /// The one-time pairing code generated by the home side.
    @Published var pairingCode: String?

    /// The home machine's tailnet IP (for display to the client user).
    @Published var tailnetSelfIP: String?

    /// Connection state for UI binding.
    @Published var connectionState: ConnectionState = .tailnetNotDetected

    // --- CLIENT-side input fields ---
    @Published var clientHomeHost: String = ""
    @Published var clientHomePort: String = "8443"
    @Published var clientPairingCode: String = ""
    @Published var clientConnectError: String?
    @Published var isConnecting: Bool = false

    /// Derived state published for menu-command enable/disable bindings.
    @Published var canStart = false
    @Published var canStop = false
    @Published var canRestart = false

    /// When true, the termination-completion path will start the daemon
    /// after it settles to .stopped.  Set by restartDaemon(), cleared
    /// after the follow-on start is issued.
    private var pendingRestart = false

    /// Quit may observe exit before the queued launch callback can settle it.
    /// Both paths consume the current launch's exit at most once.
    private var managedExitSettled = false

    /// Cold-launch policy only. Dialog entry/status reads never call this.
    func beginStartupSelection(home: String) {
        guard !startupBegan else { return }
        startupBegan = true
        switch startupSelection.choice {
        case .local:
            connectionRolePreference = .home
            supervisor.configure(homeDir: home)
            discoverExistingDaemon(home: home)
            refreshDerivedState()
            if canStart { startDaemon() }
        case .saved(let profile):
            connectionRolePreference = .client
            startupProfile = profile
            homeInvite.prepare(reconnectSingle: false, startupProfile: profile)
        case .unsaved, .unavailable:
            connectionRolePreference = .client
            homeInvite.startupUnavailable()
            // Main remains available with Connection recovery; no saved access
            // is guessed and no local child/discovery is started.
        }
        refreshDerivedState()
    }

    /// An exact saved choice may complete through the existing Main owner gate.
    /// Failure/cancellation never substitutes another target or starts local.
    func resumeStartupSelection() {
        guard let profile = startupProfile else { return }
        switch homeInvite.phase {
        case .failed, .keychain: startupProfile = nil; return
        default: break
        }
        if homeInvite.candidateOutcome != nil || homeInvite.selectedFailure != nil {
            startupProfile = nil; return
        }
        if let candidate = homeInvite.candidateID {
            if let original = startupCandidate, original != candidate { startupProfile = nil; return }
            startupCandidate = candidate
        }
        guard homeInvite.phase == .readyToSwitch else { return }
        guard homeInvite.bridgeActiveComputerID == profile else { startupProfile = nil; return }
        if let mainScheme, mainScheme.commitSwitch() == nil { startupProfile = nil }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Foreground the app as a regular Dock-present application so a bare
        // `swift run` executable gets a visible window without requiring a .app bundle.
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)

        // Load persisted role preference from UserDefaults
        if let raw = UserDefaults.standard.string(forKey: "connectionRolePreference"),
           let pref = ConnectionRolePreference(rawValue: raw) {
            connectionRolePreference = pref
        }

        // Resolve the daemon home. In bundled mode, persist the app's intended
        // home so daemonHome() does not silently defer to a stale inherited
        // HAPPYRANCH_DAEMON_HOME env var.
        if Self.isRunningInAppBundle() {
            let bundledHome = "\(NSHomeDirectory())/Library/Application Support/HappyRanch"
            UserDefaults.standard.set(bundledHome, forKey: "HappyRanchDaemonHome")
        }

        let home = AppDelegate.resolvedDaemonHome()

        // Log the resolution for diagnostics (branch disambiguation).
        diagnostics.recordLaunchLog(
            AppDelegate.launchDiagnosticLogLine(resolvedHome: home)
        )

        beginStartupSelection(home: home)
        configureUpdater()
        showMoveToApplicationsNoticeIfNeeded()
    }

    func applicationWillTerminate(_ notification: Notification) {
        admittedTermination = nil
        terminating = true
        updateTask?.cancel()
        pendingUpdateInstall = nil
        hasPendingUpdate = false
        // Sparkle's install-on-quit helper waits for this existing exit path.
        // Quit cancels Restart before teardown pumps the exit callbacks.
        pendingRestart = false
        // Retry bounded teardown for any live owned child, including .stopping
        // after an incomplete Stop or refused role switch. Keep the actual outcome.
        // External daemons are NEVER terminated on quit.
        _ = stopOwnedChildForExit()
    }

    private func stopOwnedChildForExit() -> Bool {
        guard supervisor.isManagedBySelf else { return true }
        guard let handle = currentHandle else { return supervisor.state == .stopped }
        if handle.isRunning {
            terminateManagedProcess()
        }
        // Termination is synchronous: a nested run loop need not run a queued
        // MainActor Task before this method returns and the app exits.
        guard currentHandle === handle, !handle.isRunning else { return false }
        let status = handle.terminationStatus
        settleManagedProcessExit(
            exitCode: status,
            signal: handle.terminationReason == .uncaughtSignal ? status : nil)
        return true
    }

    // MARK: - Updates

    private func configureUpdater() {
        guard UpdateConfiguration.hasValidPublicKey(Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey")) else {
            diagnostics.recordUpdaterEvent("Disabled: SUPublicEDKey absent or invalid")
            return
        }
        // Keep optional profile parameters out of feed requests. Automatic
        // check/download preferences remain owned and persisted by Sparkle.
        updaterController.updater.sendsSystemProfile = false
        updaterObservation = updaterController.updater.publisher(for: \.canCheckForUpdates)
            .sink { [weak self] enabled in
                // Sparkle's updater and its KVO notifications are main-thread APIs.
                MainActor.assumeIsolated { self?.canCheckForUpdates = enabled }
            }
        // Use the throwing core API so startup errors stay in Diagnostics.
        do {
            try updaterController.updater.start()
            diagnostics.recordUpdaterEvent("Started: public key valid")
        } catch {
            canCheckForUpdates = false
            diagnostics.recordUpdaterEvent("Start failed: \(error.localizedDescription)")
        }
    }

    func checkForUpdates() {
        guard canCheckForUpdates, !hasPendingUpdate else { return }
        updaterController.checkForUpdates(nil)
    }

    func showUpdateSettings() {
        let chinese = AppLocalePreference.shared.nativeChinese
        let updater = updaterController.updater
        let configured = UpdateConfiguration.hasValidPublicKey(Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey"))
        let alert = NSAlert()
        alert.messageText = chinese ? "更新设置" : "Update Settings"
        alert.informativeText = chinese
            ? "启用后，HappyRanch 会自动检查 https://app.happyranch.ai/mac/appcast.xml，并可能自动下载更新。请求会向 app.happyranch.ai 暴露你的 IP 地址，并在 User-Agent 中发送应用名称、应用版本和 Sparkle 版本。系统信息发送已关闭；Sparkle 不会在此检查请求中添加 macOS 版本或硬件信息。\n\n关闭下方选项会停用后续自动检查和自动下载；再次启用会同时开启两者。此选择会在重新启动后保留。正在进行或已下载的更新仍可能完成。你仍可手动选择“检查更新…”。"
            : "When enabled, HappyRanch automatically checks https://app.happyranch.ai/mac/appcast.xml and may download updates automatically. The request reveals your IP address to app.happyranch.ai and sends the app name, app version and Sparkle version in User-Agent. System-profile sending is off; Sparkle does not add your macOS version or hardware details to this check request.\n\nTurning off the option below disables future automatic checks and downloads; turning it back on enables both. Your choice persists across launches. An update already in progress or downloaded may still complete. You can still choose Check for Updates… manually."
        if !configured {
            alert.informativeText += chinese
                ? "\n\n此构建未配置更新签名公钥，更新功能已停用。"
                : "\n\nUpdates are disabled in this build because its update signing public key is not configured."
        }
        alert.showsSuppressionButton = true
        let checkbox = alert.suppressionButton
        checkbox?.title = chinese ? "自动检查更新" : "Automatically check for updates"
        checkbox?.state = configured && updater.automaticallyChecksForUpdates ? .on : .off
        checkbox?.isEnabled = configured
        alert.addButton(withTitle: chinese ? "保存" : "Save")
        alert.addButton(withTitle: chinese ? "取消" : "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn, configured, let checkbox else { return }
        let enabled = checkbox.state == .on
        guard enabled != updater.automaticallyChecksForUpdates else { return }
        if enabled {
            updater.automaticallyChecksForUpdates = true
            updater.automaticallyDownloadsUpdates = true
        } else {
            // Sparkle ignores download-setting writes when automatic updates
            // are disallowed, so persist this before disabling checks.
            updater.automaticallyDownloadsUpdates = false
            updater.automaticallyChecksForUpdates = false
        }
    }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
                 untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        postponeUpdateRelaunch(installHandler)
        return true
    }

    /// Sparkle owns its post-install launch; recovery app.relaunch is deferred.
    func postponeUpdateRelaunch(_ installHandler: @escaping () -> Void) {
        guard pendingUpdateInstall == nil else { return }
        pendingUpdateInstall = installHandler; hasPendingUpdate = true
        resumePendingUpdate()
    }

    func resumePendingUpdate() {
        guard hasPendingUpdate, !updateStopping, !terminating, !appLifetime.busy else { return }
        updateTask?.cancel(); updateRequestID = UUID()
        appLifetime.request(.update)
    }

    private func checkUpdateGate(token: UUID, choice: UpdateGate.Choice?) async {
        guard !Task.isCancelled, appLifetime.token == token, hasPendingUpdate, !terminating else { return }
        let handle = currentHandle, owned = supervisor.isManagedBySelf
        let count = owned && handle?.isRunning == true
            ? await UpdateSessionProbe.count(homeDir: supervisor.homeDir ?? daemonHome()) : nil
        guard !Task.isCancelled, appLifetime.token == token, currentHandle === handle,
              owned == supervisor.isManagedBySelf, hasPendingUpdate, !terminating else {
            appLifetime.cancelUnstarted(token: token); return
        }
        applyUpdateGate(runningSessions: count, choice: choice)
    }

    /// Policy chooses a prompt or web admission, never a stop/release.
    func applyUpdateGate(runningSessions: Int?, choice: UpdateGate.Choice?) {
        guard let token = appLifetime.token, hasPendingUpdate, !updateStopping, !terminating else { return }
        switch UpdateGate.action(ownsDaemon: supervisor.isManagedBySelf,
            processRunning: currentHandle?.isRunning, runningSessions: runningSessions, choice: choice) {
        case .release, .stopDaemon: appLifetime.beginWebGate(token: token)
        case .confirmRestart: appLifetime.offerConfirmation(token: token, kind: .updateIdle)
        case .prompt(let count): appLifetime.offerConfirmation(token: token, kind: .updateBusy(count))
        case .wait:
            appLifetime.cancelUnstarted(token: token)
            scheduleUpdateRecheck(requestID: updateRequestID)
        case .later: appLifetime.cancelUnstarted(token: token)
        }
    }

    private func scheduleUpdateRecheck(requestID: UUID) {
        updateTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(5)) } catch { return }
            guard let self, requestID == updateRequestID, hasPendingUpdate, !terminating else { return }
            // New reservation/consent on every observation; no standing permission.
            resumePendingUpdate()
        }
    }

    private func releaseUpdate(token: UUID) -> Bool {
        guard appLifetime.isExecuting(token, intent: .update), !terminating, let install = pendingUpdateInstall else { return false }
        pendingUpdateInstall = nil; hasPendingUpdate = false
        updateTask?.cancel(); updateStopping = true
        admittedTermination = .update
        diagnostics.recordUpdaterEvent("Lifecycle admitted; owned exit observed; releasing install")
        install(); return true
    }

    // MARK: - App lifetime

    var lifetimeScheme: AppScheme? { mainScheme }
    var lifetimeObservation: MacLifetimeObservation {
        MacLifetimeObservation(process: currentHandle.map { ObjectIdentifier($0) },
            running: currentHandle?.isRunning, owned: supervisor.isManagedBySelf,
            connection: homeInvite.lifetime, role: connectionRole.rawValue)
    }
    var lifetimeRefusal: String? {
        if localControls.busy || updateStopping || terminating { return "native_effect_pending" }
        if homeInvite.busy || managedJoin.busy || homeInvite.bridgeHasConfirmation
            || homeInvite.lifecycleUnresolved || mainScheme?.lifecycleNonGETPending == true
            || homeInvite.cleanupOutcomes.values.contains(where: { $0 != "confirmed" })
            || mainScheme?.connectionMutationPending == true || connectionWindow?.lifecycleBusy == true {
            return "connection_outcome_pending"
        }
        if supervisor.isManagedBySelf, currentHandle == nil, supervisor.state != .stopped {
            return "owned_process_unknown"
        }
        return nil
    }

    func prepareLifecycleConfirmation(token: UUID, intent: MacLifetimeIntent) {
        if intent == .update {
            updateTask = Task { [weak self] in
                guard let self else { return }
                await checkUpdateGate(token: token, choice: waitingForUpdateIdle ? .whenIdle : nil)
            }
        } else { appLifetime.offerConfirmation(token: token, kind: .exit) }
    }

    static func lifecycleAlert(for prompt: MacAppLifetime.Prompt, zh: Bool,
                               ownedRunning: Bool, remote: Bool, accessUnsaved: Bool) -> NSAlert {
        let alert = NSAlert(); alert.alertStyle = .warning
        switch prompt.kind {
        case .exit: alert.messageText = zh ? "退出 HappyRanch？" : "Quit HappyRanch?"
        case .updateIdle:
            alert.messageText = zh ? "重新启动 HappyRanch 以安装更新？" : "Restart HappyRanch to install the update?"
        case .updateBusy(let count):
            alert.messageText = count == nil ? (zh ? "工作状态不可用" : "Work status is unavailable")
                : (zh ? "智能体工作正在运行" : "Agent work is running")
        }
        var body = ownedRunning
            ? (zh ? "此应用拥有的本机后台服务将停止，正在运行的工作将被中断。" : "The local daemon owned by this app will stop. Running work will be interrupted.")
            : (zh ? "此应用没有正在运行且由其拥有的本机后台服务可停止。" : "This app has no running local daemon of its own to stop.")
        if remote {
            body += zh ? " 远程连接将关闭；远程电脑上的服务、授权和已保存的访问权限将保留。"
                : " The remote connection will close; its Host service, grant and saved access will remain."
            if accessUnsaved {
                body += zh ? " 访问权限尚未保存；下次连接可能需要新的邀请。"
                    : " Access is not saved; connecting again may require a new invite."
            }
        }
        body += zh ? " 未完成的连接操作或保存结果不会被视为已完成。" : "Unfinished connection operations or save results are not treated as completed."
        alert.informativeText = body
        alert.addButton(withTitle: zh ? "取消" : "Cancel"); alert.buttons[0].keyEquivalent = "\r"
        switch prompt.kind {
        case .exit: alert.addButton(withTitle: zh ? "退出" : "Quit")
        case .updateIdle: alert.addButton(withTitle: zh ? "立即重新启动" : "Restart now")
        case .updateBusy(let count):
            alert.addButton(withTitle: zh ? "工作完成后更新" : "Update when they finish")
            alert.addButton(withTitle: zh ? "立即更新并停止工作" : "Update now and stop them")
            alert.buttons[2].isEnabled = count != nil
        }
        for button in alert.buttons.dropFirst() { button.keyEquivalent = "" }
        return alert
    }

    func presentLifecycleConfirmation(_ prompt: MacAppLifetime.Prompt) {
        guard lifecycleAlert == nil, let window = mainScheme?.localPromptWindow,
              window.attachedSheet == nil else {
            appLifetime.respondToLifecycleConfirmation(token: prompt.token, response: .abort); return
        }
        let alert = Self.lifecycleAlert(for: prompt, zh: AppLocalePreference.shared.nativeChinese,
            ownedRunning: supervisor.isManagedBySelf && currentHandle?.isRunning == true,
            remote: mainScheme?.remote == true, accessUnsaved: homeInvite.bridgeHasUnsavedAccess)
        lifecycleAlert = alert
        alert.beginSheetModal(for: window) { [weak self, weak alert] response in
            guard let self, self.lifecycleAlert === alert else { return }
            self.lifecycleAlert = nil; self.lifecycleAlertDeadline?.cancel()
            self.appLifetime.respondToLifecycleConfirmation(token: prompt.token, response: response)
        }
        lifecycleAlertDeadline = Task { @MainActor [weak self, weak alert] in
            try? await Task.sleep(for: .seconds(120))
            guard !Task.isCancelled, let self, let alert, self.lifecycleAlert === alert else { return }
            window.endSheet(alert.window, returnCode: .abort)
        }
    }

    func lifecycleConfirmed(token: UUID, choice: MacAppLifetime.Choice) {
        switch choice {
        case .proceed: appLifetime.beginWebGate(token: token)
        case .whenIdle:
            waitingForUpdateIdle = true
            appLifetime.cancelUnstarted(token: token)
            scheduleUpdateRecheck(requestID: updateRequestID)
        case .restart, .now:
            waitingForUpdateIdle = false
            updateTask = Task { [weak self] in
                await self?.checkUpdateGate(token: token, choice: choice == .now ? .now : .restart)
            }
        }
    }

    func completeLifecycle(_ intent: MacLifetimeIntent, token: UUID) -> Bool {
        guard appLifetime.effectsStillCurrent(token, intent: intent) else { return false }
        pendingRestart = false
        if intent == .update {
            guard stopOwnedChildForExit(), appLifetime.frontendStillCurrent(token, intent: intent),
                  hasPendingUpdate else { return false }
            return releaseUpdate(token: token)
        }
        return intent == .close ? completeMainClose(token: token) : completeOrdinaryQuit(token: token, intent: .quit)
    }
    func completeMainClose(token: UUID) -> Bool { completeOrdinaryQuit(token: token, intent: .close) }
    private func completeOrdinaryQuit(token: UUID, intent: MacLifetimeIntent) -> Bool {
        guard appLifetime.isExecuting(token, intent: intent) else { return false }
        pendingRestart = false
        guard stopOwnedChildForExit(), appLifetime.frontendStillCurrent(token, intent: intent) else { return false }
        mainWindowOwner?.closeAfterAdmission(); mainScheme?.close()
        admittedTermination = .quit
        defer { admittedTermination = nil }
        terminationDelivery.completeQuit()
        return true
    }
    func lifecycleHeld(_ reason: String) {
        if reason == "lifecycle_outcome_unknown", stateText.contains("Stop incomplete") {
            diagnostics.recordUpdaterEvent("Lifecycle held: " + reason); return
        }
        stateText = AppLocalePreference.shared.nativeChinese
            ? "退出或更新已暂停。请保留当前窗口，检查未完成的编辑、保存或连接操作，然后重试。"
            : "Quit or update held. Keep this window, check unfinished edits, saves or connection operations, then retry."
        diagnostics.recordUpdaterEvent("Lifecycle held: " + reason)
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        if admittedTermination == .update { admittedTermination = nil }
        if appLifetime.intent == .update, let token = appLifetime.token { appLifetime.cancelUnstarted(token: token) }
        updateRequestID = UUID()
        updateTask?.cancel()
        pendingUpdateInstall = nil
        hasPendingUpdate = false
        updateStopping = false
        if supervisor.state != .stopping { refreshDerivedState() }
        diagnostics.recordUpdaterEvent("Aborted: \(error.localizedDescription)")
    }

    func updater(_ updater: SPUUpdater, failedToDownloadUpdate item: SUAppcastItem, error: Error) {
        diagnostics.recordUpdaterEvent("Download failed: \(error.localizedDescription)")
    }

    private func showMoveToApplicationsNoticeIfNeeded() {
        let key = "UpdaterMoveToApplicationsNoticeShown"
        guard !UserDefaults.standard.bool(forKey: key),
              UpdateConfiguration.needsMoveNotice(bundleURL: Bundle.main.bundleURL,
                homeURL: URL(fileURLWithPath: NSHomeDirectory()),
                readOnlyVolume: (try? Bundle.main.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]))?
                    .volumeIsReadOnly == true) else { return }
        UserDefaults.standard.set(true, forKey: key)
        diagnostics.recordUpdaterEvent("Move to Applications notice shown")
        let alert = NSAlert()
        alert.messageText = (AppLocalePreference.shared.nativeChinese ? "将 HappyRanch 移到“应用程序”" : "Move HappyRanch to Applications")
        alert.informativeText = (AppLocalePreference.shared.nativeChinese ? "请将 HappyRanch 移到“应用程序”文件夹并从那里重新打开，以便安装自动更新。" : "Move HappyRanch to your Applications folder and reopen it there so automatic updates can be installed.")
        alert.addButton(withTitle: (AppLocalePreference.shared.nativeChinese ? "好" : "OK"))
        alert.runModal()
    }

    // MARK: - Daemon home

    /// UserDefaults key for an explicit daemon-home override.
    /// Set by applicationDidFinishLaunching when the app detects it is running
    /// as a .app bundle, to prevent a stale inherited HAPPYRANCH_DAEMON_HOME
    /// env var from overriding the app's intended isolated home.
    nonisolated private static let daemonHomeOverrideKey = "HappyRanchDaemonHome"

    /// Returns true when running from within a .app bundle (as opposed to
    /// `swift run`). Uses structural bundle-path detection that is independent
    /// of the PACKAGING_MODE env var.
    /// Test seam: override via _testIsRunningInAppBundle.
    nonisolated(unsafe) private static var __testIsRunningInAppBundle: Bool?
    nonisolated private static let _testIsRunningInAppBundleLock = NSLock()

    nonisolated static var _testIsRunningInAppBundle: Bool? {
        get { _testIsRunningInAppBundleLock.withLock { __testIsRunningInAppBundle } }
        set { _testIsRunningInAppBundleLock.withLock { __testIsRunningInAppBundle = newValue } }
    }

    nonisolated static func isRunningInAppBundle() -> Bool {
        if let override = _testIsRunningInAppBundle { return override }
        return Bundle.main.bundlePath.hasSuffix(".app")
    }

    /// Resolves the daemon home with the intended precedence:
    /// (0) Explicit UserDefaults override (set by app startup in bundled mode)
    /// (1) HAPPYRANCH_DAEMON_HOME env var (dev iteration)
    /// (2) packagingMode() == "bundled" (fallback)
    /// (3) ~/.happyranch (default)
    nonisolated static func resolvedDaemonHome() -> String {
        // Step 0: explicit persisted override (set by app when bundled).
        // Only consulted when running in a .app bundle; in non-bundled/dev
        // mode skip UserDefaults so an intentionally-exported
        // HAPPYRANCH_DAEMON_HOME env var is not masked by a stale
        // auto-written value from a prior bundled launch.
        if isRunningInAppBundle(),
           let persisted = UserDefaults.standard.string(forKey: daemonHomeOverrideKey),
           !persisted.isEmpty {
            return persisted
        }
        // Step 1: env var (preserved for dev iteration).
        // Reject empty (or whitespace-only) values — a present-but-empty var
        // must not yield "". Fall through to the next branch instead.
        if let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"],
           !envHome.trimmingCharacters(in: .whitespaces).isEmpty {
            return envHome
        }
        let home = NSHomeDirectory()
        // Step 2: packaging-mode detection (fallback)
        if packagingMode() == "bundled" {
            return "\(home)/Library/Application Support/HappyRanch"
        }
        // Step 3: default
        return "\(home)/.happyranch"
    }

    /// Legacy entry point — delegates to resolvedDaemonHome() so every
    /// caller inherits the same explicit-pass-through behavior.
    nonisolated static func daemonHome() -> String {
        resolvedDaemonHome()
    }

    /// Returns a single-line diagnostic log recording how daemonHome was resolved.
    /// Format: "resolved_home=<path> branch=<branch> env_present=<bool>"
    /// branch is one of: user-defaults, env, bundled-appsupport, default-dotdir
    nonisolated static func launchDiagnosticLogLine(resolvedHome: String) -> String {
        let envPresent = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"] != nil
        let branch: String
        if let persisted = UserDefaults.standard.string(forKey: daemonHomeOverrideKey),
           !persisted.isEmpty, persisted == resolvedHome {
            branch = "user-defaults"
        } else if envPresent,
                  let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"],
                  envHome == resolvedHome {
            branch = "env"
        } else if packagingMode() == "bundled" {
            branch = "bundled-appsupport"
        } else {
            branch = "default-dotdir"
        }
        return "resolved_home=\(resolvedHome) branch=\(branch) env_present=\(envPresent)"
    }

    private func daemonHome() -> String { AppDelegate.daemonHome() }

    // MARK: - External daemon discovery

    private func discoverExistingDaemon(home: String) {
        let portFile = URL(fileURLWithPath: "\(home)/daemon.port")
        let pidFile = URL(fileURLWithPath: "\(home)/daemon.pid")

        let reader = PortReader()

        // Try to read port
        guard let port = try? reader.readPort(from: portFile) else {
            return // No port file = daemon not running
        }

        // Try to read PID
        let pidString = try? String(contentsOf: pidFile, encoding: .utf8)
        let pid = pidString.flatMap { Int32($0.trimmingCharacters(in: .whitespacesAndNewlines)) }

        if let pid = pid {
            // Check if the process is actually running
            if processIsAlive(pid: pid) {
                // External daemon detected
                supervisor.onExternalDaemonDetected(pid: pid, port: port)
                refreshDerivedState()

                // Probe health before loading the URL
                Task {
                    await probeAndLoad(port: port)
                }
                return
            } else {
                // Stale PID
                supervisor.onStalePidDetected(pid: pid)
                refreshDerivedState()
                return
            }
        }
    }

    // MARK: - Daemon actions

    /// Restart the managed daemon: stop then start.
    /// Only valid for app-managed daemons; external daemons are never restarted.
    ///
    /// The restart is genuinely two-phase: the stop is issued synchronously,
    /// and a pending-restart flag instructs the termination-completion path
    /// (which settles the supervisor to .stopped ASYNCHRONOUSLY) to perform
    /// the follow-on start.  This fixes the previous bug where the synchronous
    /// canStart check after stopDaemon() was always false because the
    /// supervisor was still .stopping.
    func restartDaemon() {
        guard !localControls.busy, !appLifetime.busy else { return }
        restartOwnedDaemon()
    }

    private func restartOwnedDaemon() {
        guard !updateStopping, !terminating else { return }
        guard supervisor.isManagedBySelf, canStop else { return }
        pendingRestart = true
        stopOwnedDaemon(confirmed: true)
    }

    func startDaemon() {
        guard !localControls.busy, !appLifetime.busy else { return }
        startOwnedDaemon()
    }

    private func startOwnedDaemon() {
        guard !updateStopping, !terminating else { return }
        // Always clear the restart flag — a direct start supersedes any
        // pending restart (safety net; the termination handler clears it
        // before issuing the follow-on start, but an unrelated start while
        // waiting for a restart's termination handler shouldn't leave a
        // stale flag).
        pendingRestart = false
        guard currentHandle?.isRunning != true else {
            stateText = "Cannot start: the previous managed daemon is still running"
            return
        }
        do {
            try supervisor.start()
            refreshDerivedState()
            diagnostics.recordStartCommand(Self.startCommandForCurrentMode())

            // Launch the daemon process
            launchDaemonProcess()
        } catch {
            stateText = "Error: \(error.localizedDescription)"
            refreshDerivedState()
        }
    }

    func stopDaemon(confirmed: Bool = false) {
        guard !localControls.busy, !appLifetime.busy else { return }
        stopOwnedDaemon(confirmed: confirmed)
    }

    private func stopOwnedDaemon(confirmed: Bool = false) {
        do {
            try supervisor.stop(confirmed: confirmed)
            refreshDerivedState()

            // Only terminate the managed process; external daemons are never touched
            if supervisor.isManagedBySelf {
                terminateManagedProcess()
            }
        } catch DaemonSupervisorError.externalStopRequiresConfirmation {
            stateText = "Stop requires confirmation for external daemon"
            refreshDerivedState()
        } catch {
            stateText = "Error: \(error.localizedDescription)"
            refreshDerivedState()
        }
    }

    func stopDaemonWithConfirmation() {
        stopDaemon(confirmed: true)
    }

    // MARK: - Packaging mode

    /// Test seam: override in tests to simulate bundled mode without setting
    /// the real process environment.  Production code never sets this.
    /// Lock-protected to prevent parallel-test races (FINDING 2 fix).
    nonisolated(unsafe) private static var __testPackagingMode: String?
    nonisolated private static let _testPackagingModeLock = NSLock()

    nonisolated static var _testPackagingMode: String? {
        get { _testPackagingModeLock.withLock { __testPackagingMode } }
        set { _testPackagingModeLock.withLock { __testPackagingMode = newValue } }
    }

    /// Returns the packaging mode: "bundled" when running inside a .app bundle
    /// with PACKAGING_MODE=bundled, "dev" otherwise.
    nonisolated static func packagingMode() -> String {
        if let override = _testPackagingMode { return override }
        return ProcessInfo.processInfo.environment["PACKAGING_MODE"] ?? "dev"
    }

    /// Returns the display string for the start command in the current mode.
    nonisolated static func startCommandForCurrentMode() -> String {
        if packagingMode() == "bundled" {
            return "Contents/Resources/daemon/happyranch-daemon"
        }
        return "uv run python -m runtime.daemon"
    }

    /// Returns the filesystem path to the frozen daemon binary inside the .app bundle.
    /// Bundle.main.resourceURL is the Contents/Resources directory.
    nonisolated static func bundledDaemonPath() -> String? {
        guard let resourceURL = Bundle.main.resourceURL else { return nil }
        return resourceURL
            .appendingPathComponent("daemon")
            .appendingPathComponent("happyranch-daemon")
            .path
    }

    /// Test seam: when true, bundledDaemonPreflightError always returns nil
    /// (bypasses the filesystem check). Used by tests that exercise the bundled
    /// launch path without a real daemon binary in the test bundle.
    nonisolated(unsafe) private static var __testSkipBundledDaemonPreflight = false
    nonisolated private static let _testSkipBundledDaemonPreflightLock = NSLock()

    nonisolated static var _testSkipBundledDaemonPreflight: Bool {
        get { _testSkipBundledDaemonPreflightLock.withLock { __testSkipBundledDaemonPreflight } }
        set { _testSkipBundledDaemonPreflightLock.withLock { __testSkipBundledDaemonPreflight = newValue } }
    }

    /// Preflight check for the bundled daemon binary.
    /// Returns an actionable error message naming the checked path, filesystem
    /// details and fix if missing or not executable; nil if present and executable.
    /// This is a pure seam — testable without a real Bundle.
    nonisolated static func bundledDaemonPreflightError(path: String) -> String? {
        if _testSkipBundledDaemonPreflight { return nil }
        let files = FileManager.default
        if files.isExecutableFile(atPath: path) { return nil }

        let details: String
        do {
            // attributesOfItem does not follow a final symlink, so report the
            // actual item at the checked path even when its target is missing.
            let attributes = try files.attributesOfItem(atPath: path)
            let itemType = attributes[.type] as? FileAttributeType
            let fileType: String
            if itemType == .typeRegular {
                fileType = "regular file"
            } else if itemType == .typeDirectory {
                fileType = "directory"
            } else if itemType == .typeSymbolicLink {
                fileType = "symlink"
            } else {
                fileType = "other"
            }
            let owner = attributes[.ownerAccountName] as? String ?? "unknown"
            let uid = (attributes[.ownerAccountID] as? NSNumber)?.stringValue ?? "unknown"
            let gid = (attributes[.groupOwnerAccountID] as? NSNumber)?.stringValue ?? "unknown"
            let mode = (attributes[.posixPermissions] as? NSNumber).map {
                String(format: "%04o", $0.uint32Value)
            } ?? "unknown"
            details = "\(fileType), owner \(owner) (uid \(uid)), group id \(gid), POSIX mode \(mode); not executable by the current user"
        } catch {
            let fileError = error as NSError
            if fileError.domain == NSCocoaErrorDomain &&
                (fileError.code == NSFileNoSuchFileError || fileError.code == NSFileReadNoSuchFileError) {
                details = "path does not exist; attributes could not be read: \(error.localizedDescription)"
            } else {
                details = "attributes could not be read: \(error.localizedDescription); not executable by the current user"
            }
        }
        return "Bundled daemon binary missing or not executable at \(path): \(details) — rebuild the app with app/mac/scripts/build-app.sh; this build is incomplete."
    }

    /// Returns the filesystem path to the bundled web/dist directory inside the .app bundle.
    nonisolated static func bundledWebDistPath() -> String? {
        guard let resourceURL = Bundle.main.resourceURL else { return nil }
        return resourceURL
            .appendingPathComponent("web")
            .appendingPathComponent("dist")
            .path
    }

    // MARK: - Process management

    private func launchDaemonProcess() {
        managedExitSettled = false
        let pc = processController
        let mode = Self.packagingMode()

        if mode == "bundled" {
            launchBundledDaemon(pc: pc)
        } else {
            launchDevDaemon(pc: pc)
        }
    }

    /// Launch the frozen daemon binary from inside the .app bundle (bundled mode).
    private func launchBundledDaemon(pc: ProcessControlling) {
        guard let daemonPath = Self.bundledDaemonPath() else {
            let launchError = "App bundle Resources directory could not be resolved; no bundled daemon path was checked — rebuild the app with app/mac/scripts/build-app.sh; this build is incomplete."
            stateText = launchError
            diagnostics.recordLaunchLog(launchError)
            return
        }

        if let preflightError = Self.bundledDaemonPreflightError(path: daemonPath) {
            stateText = preflightError
            diagnostics.recordLaunchLog(preflightError)
            return
        }

        var childEnv = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: daemonHome(),
            parentEnvironment: ProcessInfo.processInfo.environment
        )
        // In bundled mode, point HAPPYRANCH_WEB_DIST at the bundled web/dist.
        if let webDistPath = Self.bundledWebDistPath() {
            childEnv["HAPPYRANCH_WEB_DIST"] = webDistPath
        }
        // Bind a random free 127.0.0.1 port so the app runs alongside a dev daemon on 8765.
        childEnv["HAPPYRANCH_DAEMON_PORT"] = "0"

        // Ensure the daemon home directory exists before launching.
        // On a first-ever/clean install, ~/Library/Application Support/HappyRanch
        // may not exist, causing Process to throw NSFileNoSuchFileError
        // ("The file 'HappyRanch' doesn't exist") when currentDirectoryURL
        // points to a nonexistent path.
        _ = try? FileManager.default.createDirectory(atPath: daemonHome(), withIntermediateDirectories: true, attributes: nil)

        do {
            let handle = try pc.launch(
                executableURL: URL(fileURLWithPath: daemonPath),
                arguments: ["--reader-guarded-start=online-team-move@3"],
                currentDirectoryURL: URL(fileURLWithPath: daemonHome()),
                environment: childEnv,
                terminationHandler: { @Sendable [weak self] exitedHandle in
                    let exitCode = exitedHandle.terminationStatus
                    let signal = exitedHandle.terminationReason == .uncaughtSignal ? exitCode : nil as Int32?
                    let stderr = exitedHandle.capturedStandardError
                    let stdout = exitedHandle.capturedStandardOutput
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        if let ch = self.currentHandle,
                           ch.processIdentifier != exitedHandle.processIdentifier {
                            return
                        }
                        if let stderr = stderr {
                            self.diagnostics.recordDaemonStderr(stderr)
                        }
                        if let stdout = stdout {
                            self.diagnostics.recordDaemonStdout(stdout)
                        }
                        self.settleManagedProcessExit(exitCode: exitCode, signal: signal)
                    }
                }
            )
            currentHandle = handle
            let pid = handle.processIdentifier

            diagnostics.recordDaemonState(pid: pid, port: 0, bindHost: "127.0.0.1", state: "starting")

            Task {
                await healthProbeLoop(pid: pid)
            }
        } catch {
            currentHandle = nil
            diagnostics.recordLaunchLog("Launch failed (bundled): \(error.localizedDescription)")
            stateText = "Launch failed: \(error.localizedDescription)"
            supervisor.onProcessExited(exitCode: -1, signal: nil)
            refreshDerivedState()
        }
    }

    /// Launch the daemon via `uv run` from the repo root (dev mode).
    private func launchDevDaemon(pc: ProcessControlling) {
        // Ensure the daemon home directory exists before launching.
        // Even though dev mode uses repoRoot() as cwd, the daemon still
        // receives HAPPYRANCH_DAEMON_HOME and needs that directory to exist
        // for its own internal initialization.
        _ = try? FileManager.default.createDirectory(atPath: daemonHome(), withIntermediateDirectories: true, attributes: nil)

        do {
            let handle = try pc.launch(
                executableURL: URL(fileURLWithPath: "/usr/bin/env"),
                arguments: ["uv", "run", "python", "-m", "runtime.daemon", "--reader-guarded-start=online-team-move@3"],
                currentDirectoryURL: URL(fileURLWithPath: repoRoot()),
                environment: EnvironmentSanitizer.buildChildEnvironment(
                    daemonHome: daemonHome(),
                    parentEnvironment: ProcessInfo.processInfo.environment
                ),
                terminationHandler: { @Sendable [weak self] exitedHandle in
                    let exitCode = exitedHandle.terminationStatus
                    let signal = exitedHandle.terminationReason == .uncaughtSignal ? exitCode : nil as Int32?
                    let stderr = exitedHandle.capturedStandardError
                    let stdout = exitedHandle.capturedStandardOutput
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        // PID guard: reject only if currentHandle is set AND
                        // doesn't match. If currentHandle is nil (immediate
                        // exit before the caller stores the returned handle),
                        // this is the current launch's callback — accept it.
                        if let ch = self.currentHandle,
                           ch.processIdentifier != exitedHandle.processIdentifier {
                            return
                        }
                        if let stderr = stderr {
                            self.diagnostics.recordDaemonStderr(stderr)
                        }
                        if let stdout = stdout {
                            self.diagnostics.recordDaemonStdout(stdout)
                        }
                        self.settleManagedProcessExit(exitCode: exitCode, signal: signal)
                    }
                }
            )
            currentHandle = handle
            let pid = handle.processIdentifier

            diagnostics.recordDaemonState(pid: pid, port: 0, bindHost: "127.0.0.1", state: "starting")

            // Start health probing loop
            Task {
                await healthProbeLoop(pid: pid)
            }
        } catch {
            currentHandle = nil
            diagnostics.recordLaunchLog("Launch failed (dev): \(error.localizedDescription)")
            stateText = "Launch failed: \(error.localizedDescription)"
            supervisor.onProcessExited(exitCode: -1, signal: nil)
            refreshDerivedState()
        }
    }

    /// Called only after the current child's exit has actually been observed.
    private func settleManagedProcessExit(exitCode: Int32, signal: Int32?) {
        guard !managedExitSettled else { return }
        managedExitSettled = true
        supervisor.onProcessExited(exitCode: exitCode, signal: signal)
        diagnostics.recordExit(exitCode: exitCode, signal: signal)
        refreshDerivedState()
        if pendingRestart {
            pendingRestart = false
            startOwnedDaemon()
        }
    }

    /// Stop only our current child; a timeout never fabricates an exit.
    func terminateManagedProcess() {
        guard supervisor.isManagedBySelf, let handle = currentHandle else { return }

        try? supervisor.stop()
        refreshDerivedState()
        let outcome = ManagedProcessStop.stop(handle, grace: managedStopGrace)
        switch outcome {
        case .exited(let status, let signal):
            diagnostics.recordLaunchLog("Managed Stop: exited, status=\(status), signal=\(signal.map(String.init) ?? "none")")
        case .stillRunning(let error):
            diagnostics.recordLaunchLog("Managed Stop: still running, SIGKILL errno=\(error.map(String.init) ?? "none")")
        }
        if case .stillRunning(let killError) = outcome,
           let current = currentHandle, current === handle {
            // Keep .stopping and Start disabled. The real exit callback alone
            // records termination status and performs any pending restart.
            refreshDerivedState()
            stateText = "Stop incomplete: daemon still running; SIGKILL errno: \(killError.map(String.init) ?? "none")"
        }
    }

    private func repoRoot() -> String {
        // The app is run from the happyranch repo root
        if let envRoot = ProcessInfo.processInfo.environment["HAPPYRANCH_REPO_ROOT"] {
            return envRoot
        }
        return FileManager.default.currentDirectoryPath
    }

    // MARK: - Health probe loop

    private func healthProbeLoop(pid: Int32) async {
        let home = daemonHome()
        let portFile = URL(fileURLWithPath: "\(home)/daemon.port")
        let reader = PortReader()

        var polling = ManagedHealthPolling()
        while !Task.isCancelled, let delay = polling.nextDelay(state: supervisor.state) {
            guard let handle = currentHandle, handle.processIdentifier == pid, handle.isRunning else { return }
            do {
                try await Task.sleep(nanoseconds: delay)
            } catch { return }
            guard currentHandle === handle, handle.isRunning,
                  [.starting, .running, .unhealthy].contains(supervisor.state) else { return }

            // Try to read the port
            if let port = try? reader.readPort(from: portFile) {
                let baseURL = localTransport.baseURL(for: port)
                let probe = HealthProbe(baseURL: baseURL)
                let (success, latencyMs, errorMessage) = await probe.check()

                await MainActor.run {
                    // A Stop or new launch may have happened during HTTP I/O.
                    guard currentHandle === handle, handle.isRunning,
                          [.starting, .running, .unhealthy].contains(supervisor.state) else { return }
                    if success {
                        let wasRecovering = (supervisor.state != .running)
                        supervisor.onHealthCheckPassed(pid: pid, port: port)
                        diagnostics.recordHealthProbe(success: true, latencyMs: latencyMs, errorMessage: nil)
                        diagnostics.recordDaemonState(pid: pid, port: port, bindHost: "127.0.0.1", state: "running")
                        refreshDerivedState()
                        if !localControls.busy, connectionRole == .home && !homeInvite.hasActiveSession,
                           mainScheme == nil || mainScheme?.hasLocalTarget == true || webViewURL == nil {
                            let transport = LocalAppAPITransport(port: port,
                                credential: LocalTokenCredentialProvider(homeDir: Self.daemonHome()))
                            if wasRecovering, mainScheme?.hasLocalTarget == true {
                                // Same selected local target, fresh admission after reconnect.
                                let revision = mainScheme?.localServiceWillChange(starting: true)
                                Task { [weak self] in
                                    guard let self else { return }
                                    let health = try? await AppAPIHealth.read(from: transport)
                                    guard currentHandle === handle, handle.isRunning, !localControls.busy else { return }
                                    mainScheme?.localServiceDidChange(transport: transport, promptURL: baseURL + "/api/v1", health: health, revision: revision)
                                }
                            } else if selectMainTransport(id: baseURL, transport: transport, remote: false, promptURL: baseURL + "/api/v1") == nil {
                                webViewURL = baseURL
                            }
                        }
                        if wasRecovering {
                            dismissDiagnosticsOnRecovery()
                        }
                    } else {
                        supervisor.onHealthCheckFailed()
                        if !localControls.busy { mainScheme?.localServiceWillChange(starting: false) }
                        diagnostics.recordHealthProbe(success: false, latencyMs: latencyMs, errorMessage: errorMessage)
                        refreshDerivedState()
                    }
                }
            } else {
                supervisor.onHealthCheckFailed()
                diagnostics.recordHealthProbe(success: false, latencyMs: 0, errorMessage: "Daemon port unavailable")
                refreshDerivedState()
            }
        }
    }

    func probeAndLoad(port: UInt16) async {
        let baseURL = localTransport.baseURL(for: port)
        let probe = HealthProbe(baseURL: baseURL)
        let (success, latencyMs, errorMessage) = await probe.check()

        await MainActor.run {
            if success {
                diagnostics.recordHealthProbe(success: true, latencyMs: latencyMs, errorMessage: nil)
                if connectionRole == .home && !homeInvite.hasActiveSession {
                    if selectMainTransport(id: baseURL, transport: LocalAppAPITransport(port: port,
                        credential: LocalTokenCredentialProvider(homeDir: Self.daemonHome())), remote: false, promptURL: baseURL + "/api/v1") == nil {
                        webViewURL = baseURL
                    }
                }
            } else {
                diagnostics.recordHealthProbe(success: false, latencyMs: latencyMs, errorMessage: errorMessage)
            }
        }
    }

    // MARK: - Installation-local controls (Main is retained throughout effects)

    var localObservation: MacLocalObservation {
        let state = supervisor.state
        let home = Self.daemonHome()
        let pidText = try? String(contentsOfFile: home + "/daemon.pid", encoding: .utf8)
        let pid = pidText.flatMap { Int32($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
        let observedOther = pid.map { $0 > 1 && $0 != currentHandle?.processIdentifier && processIsAlive(pid: $0) } ?? false
        let external = observedOther || state == .externalRunning || (state.isRunning && !supervisor.isManagedBySelf)
        if external {
            let running = state == .externalRunning ? supervisor.observedPid.map { processIsAlive(pid: $0) ? "running" : "stopped" } ?? "unknown" : "unknown"
            return MacLocalObservation(supported: false, running: running)
        }
        let running: String
        switch state {
        case .starting: running = "starting"
        case .running, .unhealthy: running = currentHandle?.isRunning == true ? "running" : "unknown"
        case .externalRunning: running = "unknown" // handled by the fresh external observation above
        case .notConfigured, .stopped, .crashed, .stalePid, .failed:
            running = currentHandle?.isRunning == true ? "unknown" : "stopped"
        case .stopping: running = "unknown"
        }
        return MacLocalObservation(supported: !external, running: running)
    }

    var localMutationAvailable: Bool {
        !terminating && !updateStopping && !appLifetime.busy && localObservation.supported
            && ![.starting, .stopping].contains(supervisor.state)
    }

    // Sparkle's pending app installation is not an installed-daemon-update hint.
    var localUpdateObservation: String { "unknown" }

    func reconcileLocalService() async -> Bool {
        let observed = localObservation
        guard observed.settled else { return false }
        let revision = mainScheme?.localServiceWillChange(starting: observed.running == "running")
        if observed.running == "stopped" {
            mainScheme?.localServiceDidChange(transport: nil, promptURL: nil, health: nil, revision: revision)
            return true
        }
        guard let handle = currentHandle, handle.isRunning, supervisor.isManagedBySelf,
              let port = supervisor.observedPort else { return false }
        let transport = LocalAppAPITransport(port: port, credential: LocalTokenCredentialProvider(homeDir: Self.daemonHome()))
        let health: AppAPIHealth?
        do { health = try await AppAPIHealth.read(from: transport) }
        catch is CancellationError { return false }
        catch let error as AppAPIError where error == .cancelled { return false }
        catch { health = nil }
        guard currentHandle === handle, handle.isRunning, localMutationAvailable else { return false }
        mainScheme?.localServiceDidChange(transport: transport,
            promptURL: localTransport.baseURL(for: port) + "/api/v1", health: health, revision: revision)
        return true
    }

    static func localRestartAlert(zh: Bool, computerName name: String) -> NSAlert {
        let alert = NSAlert()
        alert.messageText = zh ? "重新启动 \(name) 上的 HappyRanch？" : "Restart HappyRanch on \(name)?"
        alert.informativeText = zh
            ? "这会中断此电脑上的本地工作。仅重新启动守护进程，应用和当前对话框将保持打开。不会保证工作自动恢复。"
            : "This interrupts local work on this computer. Only the daemon restarts; the app and current dialog stay open. Work may not resume automatically."
        alert.addButton(withTitle: zh ? "取消" : "Cancel")
        alert.addButton(withTitle: zh ? "重新启动" : "Restart")
        alert.buttons[0].keyEquivalent = "\r"; alert.buttons[1].keyEquivalent = ""
        return alert
    }

    static func localRestartConfirmed(_ response: NSApplication.ModalResponse) -> Bool {
        response == .alertSecondButtonReturn
    }

    func confirmLocalRestart() async -> Bool {
        guard let window = mainScheme?.localPromptWindow, window.attachedSheet == nil else { return false }
        let zh = AppLocalePreference.shared.nativeChinese
        let name = Host.current().localizedName ?? (zh ? "此电脑" : "This computer")
        let alert = Self.localRestartAlert(zh: zh, computerName: name)
        let timeout = Task { @MainActor in
            do { try await Task.sleep(for: .seconds(120)) } catch { return }
            if let parent = alert.window.sheetParent { parent.endSheet(alert.window, returnCode: .alertFirstButtonReturn) }
        }
        defer { timeout.cancel() }
        return await withCheckedContinuation { continuation in
            alert.beginSheetModal(for: window) { continuation.resume(returning: Self.localRestartConfirmed($0)) }
        }
    }

    func performLocalCommand(_ command: String) async throws -> MacLocalReadmission? {
        guard localMutationAvailable else { throw AppAPIError.unavailable }
        let starting = command != "local.stop"
        let revision = mainScheme?.localServiceWillChange(starting: starting)
        if supervisor.state == .notConfigured { supervisor.configure(homeDir: Self.daemonHome()) }
        switch command {
        case "local.start":
            if currentHandle?.isRunning != true { startOwnedDaemon() }
        case "local.stop":
            // No confirmed-external takeover: only this app's current child.
            if supervisor.isManagedBySelf { stopOwnedDaemon() }
        case "local.restart":
            if currentHandle?.isRunning == true { restartOwnedDaemon() }
            else { startOwnedDaemon() }
        default: throw AppAPIError.unavailable
        }
        // The existing stop path pumps Main's run loop. Continue only after
        // its real callback/handle observation, never after a signal request.
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(90))
        while [.starting, .stopping].contains(supervisor.state), clock.now < deadline, !terminating {
            try await clock.sleep(for: .milliseconds(50))
        }
        guard !terminating else { throw AppAPIError.cancelled }
        if !starting {
            guard localObservation.running == "stopped" else { throw AppAPIError.unavailable }
            mainScheme?.localServiceDidChange(transport: nil, promptURL: nil, health: nil, revision: revision)
            return nil
        }
        guard supervisor.isManagedBySelf, let handle = currentHandle, handle.isRunning,
              [.running, .unhealthy].contains(supervisor.state), let port = supervisor.observedPort else { throw AppAPIError.unavailable }
        let transport = LocalAppAPITransport(port: port, credential: LocalTokenCredentialProvider(homeDir: Self.daemonHome()))
        let health: AppAPIHealth?
        do { health = try await AppAPIHealth.read(from: transport) }
        catch is CancellationError { throw AppAPIError.cancelled }
        catch let error as AppAPIError where error == .cancelled { throw error }
        catch { health = nil }
        guard currentHandle === handle, handle.isRunning, localMutationAvailable else { throw AppAPIError.unavailable }
        mainScheme?.localServiceDidChange(transport: transport,
            promptURL: localTransport.baseURL(for: port) + "/api/v1", health: health, revision: revision)
        return MacLocalReadmission(health: health)
    }

    // MARK: - Role preference

    /// Persist the user's role choice and reconfigure the supervisor accordingly.
    ///
    /// - HOME: configures the supervisor with the standard daemon home
    ///   and refreshes derived state (same as the normal launch path).
    /// - CLIENT: resets the supervisor to `.notConfigured` and stops any
    ///   active home connector.
    func setRolePreference(_ preference: ConnectionRolePreference) {
        guard !updateStopping, !terminating else { return }
        connectionRolePreference = preference

        switch preference {
        case .home:
            if supervisor.state == .notConfigured {
                supervisor.configure(homeDir: daemonHome())
                refreshDerivedState()
            }
        case .client:
            // Stop any active home connector first
            if let connector = homeConnector {
                connector.stop()
                homeConnector = nil
            }
            // Reset supervisor to notConfigured so the daemon-related
            // UI (controls, health probes) gracefully degrades
            supervisor.forceState(.notConfigured)
            refreshDerivedState()
        case .undetermined:
            break // Should not be persisted; treat as no-op
        }
    }

    // MARK: - Role switching

    /// The label for role-switch menu item — dynamic based on current role.
    var switchRoleMenuLabel: String {
        Self.switchRoleMenuLabel(for: connectionRole, locale: .shared)
    }

    static func switchRoleMenuLabel(for role: ConnectionRole, locale: AppLocalePreference) -> String {
        switch role {
        case .home: return locale.nativeChinese ? "连接到另一台电脑…" : "Connect to another computer…"
        case .client: return locale.nativeChinese ? "在此 Mac 上运行 HappyRanch…" : "Run HappyRanch on this Mac…"
        }
    }

    /// Whether switching roles requires teardown of an active daemon or connection.
    var roleSwitchRequiresTeardown: Bool {
        switch connectionRole {
        case .home:
            return supervisor.isManagedBySelf &&
                   (supervisor.state == .running || supervisor.state == .starting || supervisor.state == .unhealthy)
        case .client:
            return clientBridge != nil || managedJoin.bridge != nil || managedJoin.busy || homeInvite.busy || homeInvite.phase == .connected
        }
    }

    /// Switch the connection role to the given preference, handling in-flight teardown.
    ///
    /// - HOME → CLIENT: stops running daemon subprocess (via ``stopDaemon(confirmed:)``)
    ///   and home connector, then persists the CLIENT preference. Refuses the
    ///   switch while the managed child survives, including an earlier Stop.
    /// - CLIENT → HOME: disconnects client bridge (via ``disconnectRemote()``),
    ///   then persists the HOME preference which configures the supervisor.
    ///
    /// No-op when the target role equals the current effective role.
    /// This method is safe to call from tests (no UI dependency).
    func switchConnectionRole(to preference: ConnectionRolePreference) {
        guard !updateStopping, !terminating, !localControls.busy, !appLifetime.busy else { return }
        let currentRole = connectionRole
        let targetRole = ConnectionRole.detect(supervisor: supervisor, preference: preference)

        guard currentRole != targetRole else { return }

        switch currentRole {
        case .home:
            // Stop running daemon subprocess before switching (load-bearing).
            // Reuses the existing stopDaemon path — supervisor.stop + terminateManagedProcess.
            if supervisor.isManagedBySelf,
               supervisor.state == .running || supervisor.state == .starting || supervisor.state == .unhealthy {
                stopDaemon(confirmed: true)
            }
            // Stop can return with a live child, including one already .stopping.
            // Preserve its HOME role, ownership and incomplete-Stop diagnostic.
            if supervisor.isManagedBySelf, currentHandle?.isRunning == true {
                return
            }
            // Stop home connector (also done by setRolePreference, but be explicit).
            if homeConnector != nil {
                stopHomeConnector()
            }
        case .client:
            // Disconnect client bridge if active.
            if clientBridge != nil || managedJoin.bridge != nil || managedJoin.busy || homeInvite.busy || homeInvite.hasActiveSession || homeInvite.phase == .connected {
                guard disconnectRemote() == nil else { return }
                webViewURL = nil
            }
        }

        setRolePreference(preference)
    }

    // MARK: - A2 Remote connection actions

    /// Start the home connector on the tailnet interface.
    ///
    /// Reads the self 100.x address from TailscaleStatusProvider, configures
    /// the connector with the shared pairing store, and starts listening.
    func startHomeConnector() {
        guard case .home = connectionRole else { return }

        // Fetch tailnet self IP for bindHost validation
        let selfIP: String?
        if let status = try? tailscaleProvider.fetchStatus(),
           let firstIP = status.selfTailscaleIPs.first {
            selfIP = firstIP
            tailnetSelfIP = firstIP
        } else {
            selfIP = nil
            tailnetSelfIP = nil
        }

        // Require a tailnet address to start
        guard let bindHost = selfIP else {
            connectionState = .tailnetNotDetected
            return
        }

        let credentialProvider = LocalTokenCredentialProvider(homeDir: daemonHome())

        let port: UInt16
        // Read daemon port from port file
        let portFile = URL(fileURLWithPath: "\(daemonHome())/daemon.port")
        if let daemonPort = try? PortReader().readPort(from: portFile) {
            port = daemonPort
        } else {
            connectionState = .offline
            return
        }

        let connector = HomeConnector(
            bindHost: bindHost,
            bindPort: homeConnectorPort,
            daemonPort: port,
            credentialProvider: credentialProvider,
            pairedDeviceStore: pairingStore,
            tailnetSelfIP: bindHost
        )

        guard case .stopped = connector.state else {
            connectionState = .offline
            return
        }

        do {
            try connector.start()
            homeConnector = connector
            connectionStateManager.onTailscaleDetected(homeNodeOnline: true)
            connectionState = connectionStateManager.state
        } catch {
            connectionStateManager.onTailscaleDetected(homeNodeOnline: false)
            connectionState = connectionStateManager.state
        }
    }

    /// Stop the home connector.
    func stopHomeConnector() {
        homeConnector?.stop()
        homeConnector = nil
        pairingCode = nil
        connectionStateManager.onTailscaleLost()
        connectionState = connectionStateManager.state
    }

    /// Generate a one-time pairing code and capture the tailnet address.
    func generatePairingCodeAction() {
        guard homeConnector != nil else { return }
        pairingCode = pairingStore.generatePairingCode()
        // Refresh tailnet address in case it changed
        if let status = try? tailscaleProvider.fetchStatus(),
           let firstIP = status.selfTailscaleIPs.first {
            tailnetSelfIP = firstIP
        }
    }

    /// Revoke a paired device by credential.
    func revokeDeviceAction(credential: String) {
        guard let connector = homeConnector else { return }
        _ = connector.revokeDevice(credential: credential)
    }

    /// Connect to a remote home runtime (CLIENT role).
    ///
    /// Starts the client loopback bridge, redeems the pairing code with the
    /// home connector, sets deviceCredential on success, then points the
    /// WebView at the bridge's local URL.
    func connectToRemote() {
        guard case .client = connectionRole, !isConnecting, clientBridge == nil, !homeInvite.hasActiveSession else { return }
        let managed = clientLane == .managed
        if managed && !managedJoin.canPair { return }
        guard !clientHomeHost.isEmpty,
              let port = UInt16(clientHomePort),
              !clientPairingCode.isEmpty else {
            clientConnectError = "Please fill in all fields"
            return
        }

        clientConnectError = nil
        isConnecting = true
        connectionStateManager.forceState(.offline)  // pairing is not runtime health
        connectionState = connectionStateManager.state

        let bridge: ClientBridge
        if managed {
            guard let joined = managedJoin.bridge else { isConnecting = false; return }
            bridge = joined
            managedJoin.pairingStarted()
        } else {
            bridge = ClientBridge(homeConnectorHost: clientHomeHost, homeConnectorPort: port)
        }
        let pairingCode = clientPairingCode
        let homeHost = clientHomeHost
        if managed { clientPairingCode = "" }

        self.clientBridge = bridge

        // GUI traffic uses the selected native owner; no page listener starts.
        Task {
            guard self.clientBridge === bridge else { return }
            do {
                try await bridge.redeemPairing(
                    code: pairingCode,
                    homeHost: homeHost,
                    homePort: port,
                    timeout: 30
                )

                // Probe through the authenticated bridge, whose connector hop
                // carries the device credential. Pairing alone is never online.
                guard self.clientBridge === bridge else { return }
                if managed { managedJoin.paired() }
                let health = await bridge.appRuntimeHealth()
                let healthy = health == .healthy
                await MainActor.run {
                    guard self.clientBridge === bridge else { return }
                    isConnecting = false
                    if let reason = selectMainTransport(id: appTargetGeneration.uuidString, transport: bridge, remote: true) {
                        clientConnectError = reason
                        homeInvite.retireBridge(bridge); self.clientBridge = nil
                        if managed { managedJoin.disconnect() }
                        return
                    }
                    webViewURL = (!managed || healthy) ? "happyranch-app://app/" : nil
                    if managed {
                        applyManagedHealth(health)
                        monitorManagedHealth(bridge)
                    } else {
                        if healthy { connectionStateManager.onHeartbeatSucceeded() }
                        connectionState = connectionStateManager.state
                    }
                }
            } catch let error as RedeemPairingError {
                await MainActor.run {
                    guard self.clientBridge === bridge else { return }
                    isConnecting = false
                    bridge.stop()
                    self.clientBridge = nil
                    if managed {
                        managedJoin.pairingFailed(error)
                    }
                    switch error {
                    case .refused:
                        clientConnectError = "Refused — invalid or expired pairing code"
                    case .connectionFailed(let msg):
                        clientConnectError = "Connection failed: \(msg)"
                    case .invalidResponse:
                        clientConnectError = "Invalid response from home connector"
                    case .managed(let failure):
                        clientConnectError = failure.localizedDescription
                    }
                    connectionStateManager.onHeartbeatFailed()
                    connectionState = connectionStateManager.state
                }
            } catch {
                await MainActor.run {
                    guard self.clientBridge === bridge else { return }
                    isConnecting = false
                    bridge.stop()
                    self.clientBridge = nil
                    if managed { managedJoin.disconnect() }
                    clientConnectError = managed ? "Pairing did not complete. Request fresh pairing approval." : "Unexpected error: \(error.localizedDescription)"
                    connectionStateManager.onHeartbeatFailed()
                    connectionState = connectionStateManager.state
                }
            }
        }
    }

    /// Disconnect from the remote home runtime (CLIENT role).
    @discardableResult
    func disconnectRemote() -> String? {
        if let reason = disconnectSelectedTarget() { return reason }
        isConnecting = false
        clientConnectError = nil
        connectionStateManager.onTailscaleLost()
        connectionState = connectionStateManager.state
        return nil
    }

    func cancelManagedConnection() {
        if isConnecting {
            disconnectRemote()
        } else {
            managedJoin.cancel()
        }
    }

    func confirmManagedRevocation() {
        remoteHealthTask?.cancel()
        remoteHealthTask = nil
        clientBridge?.stop()
        clientBridge = nil
        webViewURL = nil
        clientPairingCode = ""
        managedJoin.confirmRevoked()
        connectionState = managedJoin.connectionState
    }

    private func applyManagedHealth(_ health: ClientBridge.RuntimeHealth) {
        switch health {
        case .healthy: managedJoin.healthResult(true)
        case .denied: managedJoin.healthResult(false, denied: true)
        case .unreachable: managedJoin.healthResult(false)
        case .managed(let failure):
            managedJoin.pairingFailed(.managed(failure))
            clientBridge = nil
        }
        connectionState = managedJoin.connectionState
        if health != .healthy { webViewURL = nil }
    }

    private func monitorManagedHealth(_ bridge: ClientBridge) {
        remoteHealthTask?.cancel()
        remoteHealthTask = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 5_000_000_000) } catch { return }
                guard let self, self.clientBridge === bridge else { return }
                let health = await bridge.appRuntimeHealth()
                guard !Task.isCancelled, self.clientBridge === bridge else { return }
                self.applyManagedHealth(health)
                if health == .healthy { self.webViewURL = "happyranch-app://app/" }
            }
        }
    }

    // MARK: - Helpers

    /// Refresh derived @Published properties from the current supervisor state.
    /// Call this after every state change so menu-command bindings stay in sync.
    /// Internal so tests can verify menu-command enable/disable derivation
    /// across all supervisor states.
    func refreshDerivedState() {
        connectionWindow?.invalidateIfRoleChanged(connectionRole)
        mainScheme?.connectionRoleDidChange()
        stateText = supervisor.state.description
        let s = supervisor.state
        canStart = (s == .stopped || s == .crashed || s == .stalePid || s == .failed)
            && currentHandle?.isRunning != true && !updateStopping && !terminating
        canStop = s.canStop
        canRestart = canStop && supervisor.isManagedBySelf && !updateStopping && !terminating
    }

    /// Dismiss the diagnostics sheet when the daemon recovers to a healthy state.
    /// Called from healthProbeLoop after a successful health check pass during
    /// recovery (non-.running → .running). Internal for test coverage.
    func dismissDiagnosticsOnRecovery() {
        if supervisor.state == .running {
            showDiagnostics = false
        }
    }

    private func processIsAlive(pid: Int32) -> Bool {
        let result = kill(pid, 0)
        return result == 0 || errno == EPERM
    }
}
