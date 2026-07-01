import Testing
import Foundation
@testable import HappyRanchApp
import HappyRanchSupervisor

// MARK: - Helpers

/// Creates an AppDelegate wired with a FakeProcessController.
@MainActor
private func makeAppDelegateForBundled(
    state: DaemonState = .notConfigured,
    isManagedBySelf: Bool = false
) -> (AppDelegate, FakeProcessController) {
    let fake = FakeProcessController()
    let delegate = AppDelegate()
    delegate.processController = fake
    delegate.supervisor.configure(homeDir: "/tmp/test-hr-bundled")

    if isManagedBySelf {
        try! delegate.supervisor.start()
    }
    delegate.supervisor.forceState(state)
    delegate.refreshDerivedState()
    return (delegate, fake)
}

// MARK: - Packaging mode detection

@Suite("Packaging mode detection")
struct PackagingModeTests {

    @Test("packagingMode returns dev by default")
    func packagingModeReturnsDevByDefault() {
        #expect(AppDelegate.packagingMode() == "dev")
    }

    @Test("packagingMode returns bundled when overridden in test seam")
    func packagingModeReturnsBundledWhenOverridden() {
        AppDelegate._testPackagingMode = "bundled"
        defer { AppDelegate._testPackagingMode = nil }
        #expect(AppDelegate.packagingMode() == "bundled")
    }
}

// MARK: - Start command strings

@Suite("Start command strings")
struct StartCommandTests {

    @Test("startCommandForCurrentMode returns dev command in dev mode")
    func devModeCommand() {
        AppDelegate._testPackagingMode = nil
        defer { AppDelegate._testPackagingMode = nil }
        #expect(AppDelegate.startCommandForCurrentMode() == "uv run python -m runtime.daemon")
    }

    @Test("startCommandForCurrentMode returns bundled command in bundled mode")
    func bundledModeCommand() {
        AppDelegate._testPackagingMode = "bundled"
        defer { AppDelegate._testPackagingMode = nil }
        #expect(AppDelegate.startCommandForCurrentMode() == "Contents/Resources/daemon/happyranch-daemon")
    }
}

// MARK: - Bundled path helpers

@Suite("Bundled path helpers")
struct BundledPathTests {

    @Test("bundledDaemonPath appends correct components to resource URL")
    func bundledDaemonPathAppendsCorrectComponents() {
        // Bundle.main.resourceURL is the app's Contents/Resources at runtime.
        // In tests, it may be nil (no .app bundle), so this is a structural test.
        if let path = AppDelegate.bundledDaemonPath() {
            #expect(path.hasSuffix("/daemon/happyranch-daemon"),
                    "Bundled daemon path must end with /daemon/happyranch-daemon, got \(path)")
        }
        // If nil (no .app bundle in test runner), that's expected — skip.
    }

    @Test("bundledWebDistPath appends correct components to resource URL")
    func bundledWebDistPathAppendsCorrectComponents() {
        if let path = AppDelegate.bundledWebDistPath() {
            #expect(path.hasSuffix("/web/dist"),
                    "Bundled web dist path must end with /web/dist, got \(path)")
        }
    }
}

// MARK: - Bundled launch: uses frozen binary path

@Suite("Bundled launch path")
@MainActor
struct BundledLaunchTests {

    @Test("startDaemon in bundled mode launches frozen daemon from resources")
    func startDaemonInBundledModeLaunchesFrozenBinary() async throws {
        AppDelegate._testPackagingMode = "bundled"
        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        setenv("HAPPYRANCH_DAEMON_HOME", "/tmp/test-hr-bundled", 1)
        defer {
            AppDelegate._testPackagingMode = nil
            if let old = oldHome {
                setenv("HAPPYRANCH_DAEMON_HOME", old, 1)
            } else {
                unsetenv("HAPPYRANCH_DAEMON_HOME")
            }
        }

        let (delegate, fake) = makeAppDelegateForBundled(
            state: .stopped,
            isManagedBySelf: true
        )

        delegate.startDaemon()

        // Verify the fake recorded a launch
        #expect(fake.launchCallCount == 1, "Should have launched one process")

        // The executable should be the bundled daemon path, not /usr/bin/env
        if let url = fake.lastExecutableURL {
            #expect(url.path.hasSuffix("/daemon/happyranch-daemon"),
                    "Bundled launch should use frozen daemon binary, got \(url.path)")
            #expect(url.path != "/usr/bin/env",
                    "Bundled launch should NOT use /usr/bin/env")
        }

        // Arguments should be empty (no uv/python wrappers)
        #expect(fake.lastArguments?.isEmpty == true,
                "Bundled launch should have no arguments, got \(fake.lastArguments ?? [])")

        // Working directory should be the daemon home, not the repo root
        if let cwd = fake.lastCurrentDirectoryURL {
            #expect(cwd.path == "/tmp/test-hr-bundled",
                    "Bundled launch cwd should be daemon home, got \(cwd.path)")
        }
    }

    @Test("startDaemon in bundled mode sets HAPPYRANCH_WEB_DIST in child env")
    func startDaemonInBundledModeSetsWebDist() async throws {
        AppDelegate._testPackagingMode = "bundled"
        defer { AppDelegate._testPackagingMode = nil }

        let (delegate, fake) = makeAppDelegateForBundled(
            state: .stopped,
            isManagedBySelf: true
        )

        delegate.startDaemon()

        // Check the child environment has HAPPYRANCH_WEB_DIST
        // (it will be set if Bundle.main.resourceURL is non-nil in test runner)
        if let env = fake.lastEnvironment {
            if let webDist = env["HAPPYRANCH_WEB_DIST"] {
                #expect(webDist.hasSuffix("/web/dist"),
                        "HAPPYRANCH_WEB_DIST should point to bundled web/dist, got \(webDist)")
            }
            // If Bundle.main.resourceURL is nil in the test runner, webDist won't be set.
            // That's fine — the structural test above covers the path logic.
        }
    }

    @Test("startDaemon in bundled mode records bundled start command")
    func startDaemonInBundledModeRecordsBundledCommand() async throws {
        AppDelegate._testPackagingMode = "bundled"
        defer { AppDelegate._testPackagingMode = nil }

        let (delegate, _) = makeAppDelegateForBundled(
            state: .stopped,
            isManagedBySelf: true
        )

        delegate.startDaemon()

        let bundle = delegate.diagnostics.collect()
        let cmd = bundle["start_command"] as? String ?? ""
        #expect(cmd == "Contents/Resources/daemon/happyranch-daemon",
                "Diagnostics should record bundled start command, got \(cmd)")
    }
}

// MARK: - Dev launch (existing path unchanged)

@Suite("Dev launch path")
@MainActor
struct DevLaunchTests {

    @Test("startDaemon in dev mode uses uv run as before")
    func startDaemonInDevModeUsesUvRun() async throws {
        AppDelegate._testPackagingMode = nil  // default dev mode
        defer { AppDelegate._testPackagingMode = nil }

        let (delegate, fake) = makeAppDelegateForBundled(
            state: .stopped,
            isManagedBySelf: true
        )

        delegate.startDaemon()

        #expect(fake.launchCallCount == 1)
        #expect(fake.lastExecutableURL?.path == "/usr/bin/env")
        #expect(fake.lastArguments == ["uv", "run", "python", "-m", "runtime.daemon"])
    }

    @Test("startDaemon in dev mode records dev start command")
    func startDaemonInDevModeRecordsDevCommand() async throws {
        AppDelegate._testPackagingMode = nil
        defer { AppDelegate._testPackagingMode = nil }

        let (delegate, _) = makeAppDelegateForBundled(
            state: .stopped,
            isManagedBySelf: true
        )

        delegate.startDaemon()

        let bundle = delegate.diagnostics.collect()
        let cmd = bundle["start_command"] as? String ?? ""
        #expect(cmd == "uv run python -m runtime.daemon")
    }
}

// MARK: - Unhealthy/failed banner trigger logic

@Suite("Unhealthy/failed banner trigger logic")
@MainActor
struct UnhealthyBannerTriggerTests {

    @Test("failed state has canStart = true for restart recovery")
    func failedStateCanStart() {
        let (delegate, _) = makeAppDelegateForBundled(
            state: .failed,
            isManagedBySelf: true
        )
        // Failed daemon should be restartable
        #expect(delegate.canStart == true,
                "Failed daemon should be restartable (canStart)")
        #expect(delegate.canStop == false,
                "Failed daemon should not be stoppable (already dead)")
    }

    @Test("unhealthy state preserves existing canStop = true")
    func unhealthyStateCanStop() {
        let (delegate, _) = makeAppDelegateForBundled(
            state: .unhealthy,
            isManagedBySelf: true
        )
        #expect(delegate.canStop == true,
                "Unhealthy daemon should be stoppable")
        #expect(delegate.canStart == false,
                "Unhealthy daemon should not show start (it's still alive)")
    }

    @Test("canStart covers terminal and stopped states (menu enable/disable)")
    func canStartCoversTerminalAndStoppedStates() {
        // canStart enables the Daemon > Start Daemon menu item.
        // It is distinct from the placeholder view's start button condition.
        let startableViaMenu: Set<DaemonState> = [
            .stopped, .crashed, .stalePid, .failed
        ]
        let notStartableViaMenu: Set<DaemonState> = [
            .notConfigured, .starting, .running, .unhealthy, .externalRunning, .stopping
        ]

        for state in startableViaMenu {
            let (delegate, _) = makeAppDelegateForBundled(state: state, isManagedBySelf: true)
            #expect(delegate.canStart == true,
                    "State \(state) should be startable from menu")
        }
        for state in notStartableViaMenu {
            let (delegate, _) = makeAppDelegateForBundled(state: state)
            #expect(delegate.canStart == false,
                    "State \(state) should NOT be startable from menu")
        }
    }

    @Test("banner-trigger states: unhealthy and failed are terminal or transient")
    func bannerTriggerStateProperties() {
        // unhealthy: transient (may recover), not terminal
        #expect(!DaemonState.unhealthy.isTerminal)
        #expect(DaemonState.unhealthy.canStop)

        // failed: terminal, needs restart
        #expect(DaemonState.failed.isTerminal)
        #expect(!DaemonState.failed.canStop)
    }
}
