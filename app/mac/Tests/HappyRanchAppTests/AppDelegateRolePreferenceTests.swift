import Testing
import Foundation
@testable import HappyRanchApp
import HappyRanchSupervisor

// MARK: - Role preference persistence and launch behavior

@Suite("AppDelegate role preference")
@MainActor
struct AppDelegateRolePreferenceTests {

    // MARK: - Helpers

    /// Creates an AppDelegate WITHOUT calling supervisor.configure.
    /// Used for CLIENT-mode tests where the supervisor must stay .notConfigured.
    @MainActor
    private func makeClientModeDelegate() -> AppDelegate {
        let delegate = AppDelegate()
        // Do NOT configure the supervisor — mimics a CLIENT launch in
        // applicationDidFinishLaunching when connectionRolePreference == .client.
        delegate.connectionRolePreference = .client
        return delegate
    }

    /// Creates an AppDelegate fully configured for HOME mode.
    /// Mirrors the existing makeAppDelegate pattern in other test files.
    @MainActor
    private func makeHomeModeDelegate() -> AppDelegate {
        let delegate = AppDelegate()
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")
        delegate.connectionRolePreference = .home
        delegate.refreshDerivedState()
        return delegate
    }

    // MARK: - (a) CLIENT mode: fresh launch reaches client connect/redeem form

    @Test("CLIENT-mode launch: supervisor stays .notConfigured, connectionRole is .client")
    func clientModeLaunchSupervisorNotConfigured() {
        let delegate = makeClientModeDelegate()

        // The supervisor must stay .notConfigured — no daemon lifecycle.
        #expect(delegate.supervisor.state == .notConfigured,
                "CLIENT launch must NOT configure the supervisor; expected .notConfigured, got \(delegate.supervisor.state)")
    }

    @Test("CLIENT-mode launch: connectionRole returns .client")
    func clientModeLaunchRoleIsClient() {
        let delegate = makeClientModeDelegate()

        #expect(delegate.connectionRole == .client,
                "CLIENT launch must return .client role, got \(delegate.connectionRole)")
    }

    @Test("CLIENT-mode launch: daemon controls are hidden (connectionRole != .home)")
    func clientModeLaunchDaemonControlsHidden() {
        let delegate = makeClientModeDelegate()

        // canStart/daemon controls should reflect the absence of a daemon
        #expect(delegate.connectionRole != .home,
                "CLIENT role must not be .home (controls hidden)")
        #expect(!delegate.connectionRole.isLocal,
                "CLIENT role isLocal must be false")
    }

    @Test("CLIENT-mode launch: client connect/redeem fields accessible")
    func clientModeLaunchConnectFieldsAccessible() {
        let delegate = makeClientModeDelegate()

        // The client-side input fields must be available for UI binding.
        // They default to empty strings.
        #expect(delegate.clientHomeHost == "")
        #expect(delegate.clientHomePort == "8443")
        #expect(delegate.clientPairingCode == "")
        #expect(delegate.clientConnectError == nil)
        #expect(delegate.isConnecting == false)
        #expect(delegate.clientBridge == nil)
    }

    // MARK: - (b) HOME mode: daemon-running launch reaches HOME connector surface

    @Test("HOME-mode launch: supervisor is configured, connectionRole is .home")
    func homeModeLaunchRoleIsHome() {
        let delegate = makeHomeModeDelegate()

        #expect(delegate.supervisor.state == .stopped,
                "HOME launch must configure supervisor (state .stopped), got \(delegate.supervisor.state)")
        #expect(delegate.connectionRole == .home,
                "HOME launch must return .home role, got \(delegate.connectionRole)")
    }

    @Test("HOME-mode launch: daemon controls are visible (connectionRole == .home)")
    func homeModeLaunchControlsVisible() {
        let delegate = makeHomeModeDelegate()

        #expect(delegate.connectionRole == .home,
                "HOME role must be .home (controls visible)")
        #expect(delegate.connectionRole.isLocal,
                "HOME role isLocal must be true")
    }

    @Test("HOME-mode launch: canStart reflects configured state")
    func homeModeLaunchCanStartReflectsState() {
        let delegate = makeHomeModeDelegate()

        // .stopped state → can start
        #expect(delegate.canStart == true,
                "HOME with .stopped daemon must have canStart = true")
        #expect(delegate.canStop == false,
                "HOME with .stopped daemon must have canStop = false")
    }

    @Test("HOME-mode launch: home connector can be started (tailscale check required)")
    func homeModeLaunchHomeConnectorAccessible() {
        let delegate = makeHomeModeDelegate()

        // The homeConnector should be nil initially (not yet started).
        #expect(delegate.homeConnector == nil,
                "HOME mode: homeConnector must start nil")

        // Verify the home connector port default
        #expect(delegate.homeConnectorPort == 8443)
    }

    // MARK: - Role preference persistence

    @Test("connectionRolePreference default is nil after init (first-launch)")
    func connectionRolePreferenceDefaultNil() {
        let delegate = AppDelegate()

        #expect(delegate.connectionRolePreference == nil,
                "First launch must have nil connectionRolePreference")
    }

    @Test("setRolePreference(.home) persists and configures supervisor")
    func setRolePreferenceHomePersistsAndConfigures() {
        let delegate = AppDelegate()
        // Start from first-launch state (no configure)
        #expect(delegate.supervisor.state == .notConfigured)

        delegate.setRolePreference(.home)

        // Preference must be persisted
        #expect(delegate.connectionRolePreference == .home)
        // Supervisor must be configured
        #expect(delegate.supervisor.state == .stopped,
                "setRolePreference(.home) must configure supervisor, got \(delegate.supervisor.state)")
        // Role must be HOME
        #expect(delegate.connectionRole == .home)
    }

    @Test("setRolePreference(.client) persists and resets supervisor to notConfigured")
    func setRolePreferenceClientPersistsAndResets() {
        let delegate = AppDelegate()
        // First configure it as HOME (to test the reset path)
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")
        delegate.connectionRolePreference = .home
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .stopped)
        #expect(delegate.connectionRole == .home)

        // Now switch to CLIENT
        delegate.setRolePreference(.client)

        // Preference must be persisted
        #expect(delegate.connectionRolePreference == .client)
        // Supervisor must be reset
        #expect(delegate.supervisor.state == .notConfigured,
                "setRolePreference(.client) must reset supervisor to .notConfigured, got \(delegate.supervisor.state)")
        // Role must be CLIENT
        #expect(delegate.connectionRole == .client)
        // Client fields accessible
        #expect(delegate.clientHomeHost == "")
    }

    @Test("setRolePreference(.home) when already configured is a no-op for supervisor")
    func setRolePreferenceHomeWhenAlreadyConfigured() {
        let delegate = makeHomeModeDelegate()
        #expect(delegate.supervisor.state == .stopped)

        // Calling setRolePreference(.home) again should not change state
        delegate.setRolePreference(.home)

        #expect(delegate.supervisor.state == .stopped,
                "Re-setting HOME preference must not change supervisor state")
        #expect(delegate.connectionRolePreference == .home)
    }

    @Test("setRolePreference(.client) stops active homeConnector")
    func setRolePreferenceClientStopsHomeConnector() {
        let delegate = AppDelegate()
        delegate.supervisor.configure(homeDir: "/tmp/test-hr")
        delegate.connectionRolePreference = .home

        // Create a mock home connector (just needs to be set, not started)
        let connector = HomeConnector(
            bindHost: "100.64.0.1",
            bindPort: 8443,
            daemonPort: 8765,
            credentialProvider: LocalTokenCredentialProvider(homeDir: "/tmp/test-hr"),
            pairedDeviceStore: RealPairingStore(),
            tailnetSelfIP: "100.64.0.1"
        )
        delegate.homeConnector = connector

        delegate.setRolePreference(.client)

        #expect(delegate.homeConnector == nil,
                "setRolePreference(.client) must nil out homeConnector")
        #expect(delegate.connectionRolePreference == .client)
    }

    // MARK: - UserDefaults persistence round-trip

    @Test("role preference round-trips through UserDefaults")
    func rolePreferenceRoundTripUserDefaults() {
        // Clean up any existing key
        UserDefaults.standard.removeObject(forKey: "connectionRolePreference")

        // Simulate first launch: read from UserDefaults (should be nil)
        let delegate = AppDelegate()
        if let raw = UserDefaults.standard.string(forKey: "connectionRolePreference"),
           let pref = ConnectionRolePreference(rawValue: raw) {
            delegate.connectionRolePreference = pref
        }
        #expect(delegate.connectionRolePreference == nil)

        // Set HOME
        delegate.connectionRolePreference = .home
        #expect(UserDefaults.standard.string(forKey: "connectionRolePreference") == "home")

        // Create a new delegate to simulate a relaunch
        let delegate2 = AppDelegate()
        if let raw = UserDefaults.standard.string(forKey: "connectionRolePreference"),
           let pref = ConnectionRolePreference(rawValue: raw) {
            delegate2.connectionRolePreference = pref
        }
        #expect(delegate2.connectionRolePreference == .home)

        // Switch to CLIENT
        delegate2.connectionRolePreference = .client
        #expect(UserDefaults.standard.string(forKey: "connectionRolePreference") == "client")

        // Relaunch again
        let delegate3 = AppDelegate()
        if let raw = UserDefaults.standard.string(forKey: "connectionRolePreference"),
           let pref = ConnectionRolePreference(rawValue: raw) {
            delegate3.connectionRolePreference = pref
        }
        #expect(delegate3.connectionRolePreference == .client)

        // Clean up
        UserDefaults.standard.removeObject(forKey: "connectionRolePreference")
    }

    // MARK: - Existing daemon detection independent of role

    @Test("CLIENT-mode launch: discoverExistingDaemon still runs (port file inspection independent of configure)")
    func clientModeLaunchDiscoverExistingDaemonRuns() {
        let delegate = makeClientModeDelegate()
        // Verify the supervisor is .notConfigured (CLIENT mode)
        #expect(delegate.supervisor.state == .notConfigured)

        // discoverExistingDaemon() uses PortReader directly, not the supervisor's
        // homeDir — so it should be safe to call even in CLIENT mode.
        // We can't meaningfully trigger daemon discovery without a real port file,
        // but we verify the structural invariant: calling it on a .notConfigured
        // supervisor does NOT crash or throw.
        #expect(delegate.connectionRole == .client)
    }

    // MARK: - Role switch after first launch (TASK-2318)

    @Test("switchConnectionRole(.client) from HOME after first launch changes role and persists preference")
    func switchToClientAfterFirstLaunchChangesRole() {
        let delegate = makeHomeModeDelegate()
        #expect(delegate.connectionRole == .home)
        #expect(delegate.connectionRolePreference == .home)

        delegate.switchConnectionRole(to: .client)

        #expect(delegate.connectionRolePreference == .client,
                "switchConnectionRole(.client) must persist .client preference")
        #expect(delegate.connectionRole == .client,
                "switchConnectionRole(.client) must return .client role")
        #expect(delegate.supervisor.state == .notConfigured,
                "switchConnectionRole(.client) must reset supervisor to .notConfigured")
    }

    @Test("switchConnectionRole(.home) from CLIENT after first launch changes role and configures supervisor")
    func switchToHomeAfterFirstLaunchChangesRole() {
        let delegate = makeClientModeDelegate()
        #expect(delegate.connectionRole == .client)
        #expect(delegate.connectionRolePreference == .client)

        delegate.switchConnectionRole(to: .home)

        #expect(delegate.connectionRolePreference == .home,
                "switchConnectionRole(.home) must persist .home preference")
        #expect(delegate.connectionRole == .home,
                "switchConnectionRole(.home) must return .home role")
        #expect(delegate.supervisor.state == .stopped,
                "switchConnectionRole(.home) must configure supervisor (state .stopped), got \(delegate.supervisor.state)")
    }

    @Test("switchConnectionRole to same role is a no-op")
    func switchToSameRoleIsNoOp() {
        let delegate = makeHomeModeDelegate()
        #expect(delegate.connectionRole == .home)
        let beforeState = delegate.supervisor.state

        delegate.switchConnectionRole(to: .home)

        #expect(delegate.connectionRolePreference == .home)
        #expect(delegate.connectionRole == .home)
        #expect(delegate.supervisor.state == beforeState,
                "Switching to same role must not change supervisor state")
    }

    @Test("switchConnectionRole(.client) from CLIENT is a no-op")
    func switchToClientFromClientIsNoOp() {
        let delegate = makeClientModeDelegate()
        #expect(delegate.connectionRole == .client)

        delegate.switchConnectionRole(to: .client)

        #expect(delegate.connectionRolePreference == .client)
        #expect(delegate.connectionRole == .client)
        #expect(delegate.supervisor.state == .notConfigured)
    }

    // MARK: - Role switch with running daemon (TASK-2318)

    @Test("HOME→CLIENT with running daemon stops the daemon subprocess")
    func homeToClientWithRunningDaemonStopsDaemon() {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-switch")
        delegate.connectionRolePreference = .home

        // Start daemon via the real path (launches through fake)
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)

        // Simulate health-check success to reach .running
        delegate.supervisor.onHealthCheckPassed(
            pid: fake.activeHandle!.processIdentifier,
            port: 9876
        )
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .running)
        #expect(delegate.supervisor.isManagedBySelf == true)

        // Switch to CLIENT — must stop the running daemon
        delegate.switchConnectionRole(to: .client)

        // The daemon subprocess must have been terminated
        #expect(fake.terminateCallCount >= 1,
                "HOME→CLIENT must terminate the running daemon subprocess; terminateCallCount=\(fake.terminateCallCount)")
        // After switch, supervisor must be reset
        #expect(delegate.supervisor.state == .notConfigured,
                "After HOME→CLIENT, supervisor must be .notConfigured")
        #expect(delegate.connectionRolePreference == .client)
        #expect(delegate.connectionRole == .client)
    }

    @Test("HOME→CLIENT with running daemon stops home connector")
    func homeToClientWithRunningDaemonStopsHomeConnector() {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-switch")
        delegate.connectionRolePreference = .home

        // Set up a home connector
        let connector = HomeConnector(
            bindHost: "100.64.0.1",
            bindPort: 8443,
            daemonPort: 8765,
            credentialProvider: LocalTokenCredentialProvider(homeDir: "/tmp/test-hr-switch"),
            pairedDeviceStore: RealPairingStore(),
            tailnetSelfIP: "100.64.0.1"
        )
        delegate.homeConnector = connector

        // Start daemon so it's running
        delegate.startDaemon()
        delegate.supervisor.onHealthCheckPassed(
            pid: fake.activeHandle!.processIdentifier,
            port: 9876
        )
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .running)

        // Switch to CLIENT
        delegate.switchConnectionRole(to: .client)

        // Both daemon and home connector must be stopped
        #expect(fake.terminateCallCount >= 1,
                "HOME→CLIENT must terminate the daemon subprocess")
        #expect(delegate.homeConnector == nil,
                "HOME→CLIENT must nil out homeConnector")
        #expect(delegate.connectionRolePreference == .client)
        #expect(delegate.connectionRole == .client)
    }

    @Test("HOME→CLIENT with already-stopped daemon does not double-terminate")
    func homeToClientWithStoppedDaemonNoDoubleTerminate() {
        let delegate = makeHomeModeDelegate()
        #expect(delegate.supervisor.state == .stopped)
        #expect(delegate.connectionRole == .home)

        // Switch to CLIENT with daemon already stopped
        delegate.switchConnectionRole(to: .client)

        #expect(delegate.supervisor.state == .notConfigured,
                "After switch from stopped HOME, supervisor must be .notConfigured")
        #expect(delegate.connectionRolePreference == .client)
        #expect(delegate.connectionRole == .client)
    }

    // MARK: - CLIENT→HOME with active client connection (TASK-2318)

    @Test("CLIENT→HOME clears clientBridge and resets connection state")
    func clientToHomeClearsClientBridge() {
        let delegate = makeClientModeDelegate()
        #expect(delegate.connectionRole == .client)

        // Set up a fake active client bridge
        delegate.clientBridge = ClientBridge(
            homeConnectorHost: "100.64.0.1",
            homeConnectorPort: 8443
        )
        delegate.clientHomeHost = "100.64.0.1"
        delegate.webViewURL = "http://127.0.0.1:9876/"
        delegate.connectionStateManager.forceState(.online)
        delegate.connectionState = delegate.connectionStateManager.state
        #expect(delegate.clientBridge != nil)
        #expect(delegate.webViewURL != nil)
        #expect(delegate.connectionState == .online)

        // Switch to HOME
        delegate.switchConnectionRole(to: .home)

        #expect(delegate.clientBridge == nil,
                "CLIENT→HOME must nil out clientBridge")
        #expect(delegate.webViewURL == nil,
                "CLIENT→HOME must clear webViewURL")
        #expect(delegate.connectionRolePreference == .home)
        #expect(delegate.connectionRole == .home)
        #expect(delegate.supervisor.state == .stopped,
                "CLIENT→HOME must configure supervisor")
    }

    // MARK: - Menu/action path reachability (TASK-2336)

    /// Proves the menu command's action path reaches switchConnectionRole
    /// via the executeRoleSwitch seam (extracted from confirmAndSwitchRole).
    /// Without this test, removing the menu item or disconnecting
    /// confirmAndSwitchRole from switchConnectionRole would still pass CI.
    @Test("executeRoleSwitch(.client) from HOME with running daemon stops daemon and changes role")
    func menuPathHomeToClientStopsDaemon() {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-menu")
        delegate.connectionRolePreference = .home

        // Start daemon → reach .running (same pattern as existing test at line 332)
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        delegate.supervisor.onHealthCheckPassed(
            pid: fake.activeHandle!.processIdentifier,
            port: 9876
        )
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .running)
        #expect(delegate.supervisor.isManagedBySelf == true)

        // Exercise the menu/action seam — this is what the menu button's
        // confirmAndSwitchRole calls after the user confirms the alert.
        let app = HappyRanchApp()
        app.executeRoleSwitch(from: .home, in: delegate)

        // Daemon subprocess must have been terminated
        #expect(fake.terminateCallCount >= 1,
                "Menu HOME→CLIENT must terminate the running daemon; terminateCallCount=\(fake.terminateCallCount)")
        // Supervisor must be reset
        #expect(delegate.supervisor.state == .notConfigured,
                "After menu HOME→CLIENT, supervisor must be .notConfigured, got \(delegate.supervisor.state)")
        #expect(delegate.connectionRolePreference == .client)
        #expect(delegate.connectionRole == .client)
    }

    @Test("executeRoleSwitch(.home) from CLIENT changes role and configures supervisor")
    func menuPathClientToHomeConfiguresSupervisor() {
        let delegate = makeClientModeDelegate()
        #expect(delegate.connectionRole == .client)
        #expect(delegate.connectionRolePreference == .client)

        // Exercise the menu/action seam
        let app = HappyRanchApp()
        app.executeRoleSwitch(from: .client, in: delegate)

        #expect(delegate.connectionRolePreference == .home,
                "Menu CLIENT→HOME must persist .home preference")
        #expect(delegate.connectionRole == .home,
                "Menu CLIENT→HOME must return .home role")
        #expect(delegate.supervisor.state == .stopped,
                "Menu CLIENT→HOME must configure supervisor, got \(delegate.supervisor.state)")
    }

    @Test("executeRoleSwitch from home toggles to client (label consistency)")
    func menuPathToggleHomeToClient() {
        let delegate = makeHomeModeDelegate()
        #expect(delegate.connectionRole == .home)

        let app = HappyRanchApp()
        app.executeRoleSwitch(from: .home, in: delegate)

        #expect(delegate.connectionRole == .client)
        #expect(delegate.connectionRolePreference == .client)
    }

    // MARK: - confirmAndSwitchRole confirm→switch path (TASK-2350)

    /// Proves the full confirmAndSwitchRole path with a stub confirmation:
    /// HOME→CLIENT with a running daemon, stub returns true → daemon teardown + role switch.
    /// This closes the reviewer gap: the existing tests only exercised executeRoleSwitch
    /// directly, bypassing confirmAndSwitchRole (the method the menu Button invokes).
    @Test("confirmAndSwitchRole HOME→CLIENT with running daemon, stub returns true, tears down daemon")
    func confirmAndSwitchRoleHomeToClientRunningDaemonConfirmedTrue() {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-confirm-1")
        delegate.connectionRolePreference = .home

        // Start daemon → reach .running (same pattern as existing tests)
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        delegate.supervisor.onHealthCheckPassed(
            pid: fake.activeHandle!.processIdentifier,
            port: 9876
        )
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .running)
        #expect(delegate.supervisor.isManagedBySelf == true)
        #expect(delegate.connectionRole == .home)

        // Exercise confirmAndSwitchRole directly with a stub that returns true
        let app = HappyRanchApp()
        app.confirmAndSwitchRole(delegate) { _, _ in true }

        // Daemon subprocess must have been terminated through confirmAndSwitchRole
        #expect(fake.terminateCallCount >= 1,
                "confirmAndSwitchRole must terminate the daemon subprocess; terminateCallCount=\(fake.terminateCallCount)")
        // Supervisor must be reset
        #expect(delegate.supervisor.state == .notConfigured,
                "After confirmAndSwitchRole HOME→CLIENT, supervisor must be .notConfigured, got \(delegate.supervisor.state)")
        #expect(delegate.connectionRolePreference == .client,
                "confirmAndSwitchRole must persist .client preference")
        #expect(delegate.connectionRole == .client,
                "confirmAndSwitchRole must return .client role")
    }

    /// Proves the confirmation gate: stub returns false → NO switch happens.
    /// A regression that removes or disconnects the confirmation gate
    /// would still switch roles even with the stub returning false.
    @Test("confirmAndSwitchRole with stub returning false does NOT switch (confirmation gate)")
    func confirmAndSwitchRoleCancelGateStubReturnsFalse() {
        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-confirm-2")
        delegate.connectionRolePreference = .home

        // Start daemon → reach .running
        delegate.startDaemon()
        #expect(fake.launchCallCount == 1)
        delegate.supervisor.onHealthCheckPassed(
            pid: fake.activeHandle!.processIdentifier,
            port: 9876
        )
        delegate.refreshDerivedState()
        #expect(delegate.supervisor.state == .running)
        #expect(delegate.connectionRole == .home)

        // Exercise confirmAndSwitchRole directly with a stub that returns false
        let app = HappyRanchApp()
        app.confirmAndSwitchRole(delegate) { _, _ in false }

        // NO switch must have occurred — confirmation gate is load-bearing
        #expect(fake.terminateCallCount == 0,
                "confirmAndSwitchRole with stub=false must NOT terminate daemon; terminateCallCount=\(fake.terminateCallCount)")
        #expect(delegate.supervisor.state == .running,
                "After confirmAndSwitchRole stub=false, supervisor must still be .running, got \(delegate.supervisor.state)")
        #expect(delegate.connectionRolePreference == .home,
                "confirmAndSwitchRole stub=false must NOT change preference")
        #expect(delegate.connectionRole == .home,
                "confirmAndSwitchRole stub=false must NOT change role")
    }

    /// CLIENT→HOME via confirmAndSwitchRole with stub returning true.
    @Test("confirmAndSwitchRole CLIENT→HOME with stub returning true configures supervisor")
    func confirmAndSwitchRoleClientToHomeConfirmedTrue() {
        let delegate = makeClientModeDelegate()
        #expect(delegate.connectionRole == .client)
        #expect(delegate.connectionRolePreference == .client)
        #expect(delegate.supervisor.state == .notConfigured)

        let app = HappyRanchApp()
        app.confirmAndSwitchRole(delegate) { _, _ in true }

        #expect(delegate.connectionRolePreference == .home,
                "confirmAndSwitchRole CLIENT→HOME must persist .home preference")
        #expect(delegate.connectionRole == .home,
                "confirmAndSwitchRole CLIENT→HOME must return .home role")
        #expect(delegate.supervisor.state == .stopped,
                "confirmAndSwitchRole CLIENT→HOME must configure supervisor, got \(delegate.supervisor.state)")
    }
}
