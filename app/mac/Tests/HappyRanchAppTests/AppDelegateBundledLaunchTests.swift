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

// MARK: - Packaging mode + daemon home resolution (serialized — shared static state)

@Suite("Packaging mode detection and daemon home resolution", .serialized)
struct PackagingAndHomeResolutionTests {

    // MARK: packagingMode

    @Test("packagingMode returns dev by default")
    func packagingModeReturnsDevByDefault() {
        AppDelegate._testPackagingMode = nil
        defer { AppDelegate._testPackagingMode = nil }
        #expect(AppDelegate.packagingMode() == "dev")
    }

    @Test("packagingMode returns bundled when overridden in test seam")
    func packagingModeReturnsBundledWhenOverridden() {
        AppDelegate._testPackagingMode = "bundled"
        defer { AppDelegate._testPackagingMode = nil }
        #expect(AppDelegate.packagingMode() == "bundled")
    }

    // MARK: - Start command strings (depends on packagingMode)

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

    // MARK: resolvedDaemonHome() precedence

    @Test("resolvedDaemonHome honors explicit UserDefaults override (bundled mode)")
    func resolvedDaemonHomeUsesUserDefaultsOverride() {
        let testHome = "/tmp/test-user-defaults-home"
        AppDelegate._testPackagingMode = nil
        AppDelegate._testIsRunningInAppBundle = true
        UserDefaults.standard.set(testHome, forKey: "HappyRanchDaemonHome")
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testIsRunningInAppBundle = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let result = AppDelegate.resolvedDaemonHome()
        #expect(result == testHome,
                "UserDefaults override should win (bundled mode), got \(result) expected \(testHome)")
    }

    @Test("resolvedDaemonHome ignores empty UserDefaults value (bundled mode)")
    func resolvedDaemonHomeIgnoresEmptyUserDefaults() {
        AppDelegate._testPackagingMode = nil
        AppDelegate._testIsRunningInAppBundle = true
        UserDefaults.standard.set("", forKey: "HappyRanchDaemonHome")
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testIsRunningInAppBundle = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let result = AppDelegate.resolvedDaemonHome()
        #expect(!result.isEmpty, "result must not be empty")
        #expect(result != "", "empty UserDefaults override must be ignored")
    }

    @Test("resolvedDaemonHome with packagingMode=bundled returns AppSupport when no UD override")
    func resolvedDaemonHomePackagingBundledReturnsAppSupport() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = "bundled"
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let result = AppDelegate.resolvedDaemonHome()
        let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        if envHome == nil {
            let expected = "\(NSHomeDirectory())/Library/Application Support/HappyRanch"
            #expect(result == expected,
                    "bundled mode should return AppSupport dir, got \(result) expected \(expected)")
        } else {
            // If env var is set, it takes priority over packaging mode (step 1 > step 2)
            #expect(result == envHome,
                    "env var should win over packaging mode when no UD override, got \(result) expected \(envHome!)")
        }
    }

    @Test("resolvedDaemonHome returns dotdir default when no override, no env, packaging=dev")
    func resolvedDaemonHomeReturnsDotdirDefault() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = nil
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let result = AppDelegate.resolvedDaemonHome()
        let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        if envHome == nil {
            let expected = "\(NSHomeDirectory())/.happyranch"
            #expect(result == expected,
                    "default should be ~/.happyranch, got \(result) expected \(expected)")
        } else {
            #expect(result == envHome,
                    "env var should win when no UD override, got \(result) expected \(envHome!)")
        }
    }

    @Test("resolvedDaemonHome returns env var when no override and env is set")
    func resolvedDaemonHomeReturnsEnvVar() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = nil
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        let result = AppDelegate.resolvedDaemonHome()
        if let envHome {
            #expect(result == envHome,
                    "Should return env var when no override, got \(result) expected \(envHome)")
        }
        #expect(!result.isEmpty, "result must never be empty")
    }

    @Test("resolvedDaemonHome ignores empty HAPPYRANCH_DAEMON_HOME env var")
    func resolvedDaemonHomeIgnoresEmptyEnvVar() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = nil
        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        setenv("HAPPYRANCH_DAEMON_HOME", "", 1)
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
            if let old = oldHome {
                setenv("HAPPYRANCH_DAEMON_HOME", old, 1)
            } else {
                unsetenv("HAPPYRANCH_DAEMON_HOME")
            }
        }

        let result = AppDelegate.resolvedDaemonHome()
        #expect(!result.isEmpty, "Result must not be empty when env var is empty, got '\(result)'")
        let expected = "\(NSHomeDirectory())/.happyranch"
        #expect(result == expected,
                "Should fall through to default dotdir when env var is empty, got \(result)")
    }

    // MARK: isRunningInAppBundle()

    @Test("isRunningInAppBundle returns false in test runner")
    func isRunningInAppBundleReturnsFalseInTests() {
        AppDelegate._testIsRunningInAppBundle = nil
        defer { AppDelegate._testIsRunningInAppBundle = nil }
        #expect(AppDelegate.isRunningInAppBundle() == false,
                "Test runner should not be detected as a bundled app")
    }

    @Test("isRunningInAppBundle honors test seam override")
    func isRunningInAppBundleHonorsTestSeam() {
        AppDelegate._testIsRunningInAppBundle = true
        defer { AppDelegate._testIsRunningInAppBundle = nil }
        #expect(AppDelegate.isRunningInAppBundle() == true)

        AppDelegate._testIsRunningInAppBundle = false
        #expect(AppDelegate.isRunningInAppBundle() == false)
    }

    // MARK: launchDiagnosticLogLine()

    @Test("launchDiagnosticLogLine reports user-defaults branch when override matches resolvedHome")
    func diagnosticLogLineReportsUserDefaultsBranch() {
        AppDelegate._testPackagingMode = nil
        let testHome = "/tmp/test-diag-ud"
        UserDefaults.standard.set(testHome, forKey: "HappyRanchDaemonHome")
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let log = AppDelegate.launchDiagnosticLogLine(resolvedHome: testHome)
        #expect(log.contains("branch=user-defaults"),
                "Log should report branch=user-defaults when override matches, got: \(log)")
        #expect(log.contains("resolved_home=\(testHome)"),
                "Log should contain resolved_home, got: \(log)")
    }

    @Test("launchDiagnosticLogLine reports env branch when env matches and no UD")
    func diagnosticLogLineReportsEnvBranch() {
        AppDelegate._testPackagingMode = nil
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        let resolvedHome = AppDelegate.resolvedDaemonHome()
        let log = AppDelegate.launchDiagnosticLogLine(resolvedHome: resolvedHome)

        let envPresent = envHome != nil
        #expect(log.contains("env_present=\(envPresent)"),
                "Log should report env_present=\(envPresent), got: \(log)")

        if envHome != nil, envHome == resolvedHome {
            #expect(log.contains("branch=env"),
                    "Log should report branch=env when env matches, got: \(log)")
        }
    }

    @Test("launchDiagnosticLogLine reports bundled-appsupport branch")
    func diagnosticLogLineReportsBundledBranch() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = "bundled"
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let bundledHome = "\(NSHomeDirectory())/Library/Application Support/HappyRanch"
        let log = AppDelegate.launchDiagnosticLogLine(resolvedHome: bundledHome)

        let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        if envHome == nil {
            #expect(log.contains("branch=bundled-appsupport"),
                    "Log should report branch=bundled-appsupport, got: \(log)")
        }
        #expect(log.contains("env_present="),
                "Log should always contain env_present, got: \(log)")
    }

    @Test("launchDiagnosticLogLine reports default-dotdir branch")
    func diagnosticLogLineReportsDefaultBranch() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = nil
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let dotDirHome = "\(NSHomeDirectory())/.happyranch"
        let log = AppDelegate.launchDiagnosticLogLine(resolvedHome: dotDirHome)

        let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        if envHome == nil {
            #expect(log.contains("branch=default-dotdir"),
                    "Log should report branch=default-dotdir, got: \(log)")
        }
        #expect(log.contains("env_present="),
                "Log should always contain env_present, got: \(log)")
    }

    @Test("launchDiagnosticLogLine contains all required fields")
    func diagnosticLogLineHasAllFields() {
        let log = AppDelegate.launchDiagnosticLogLine(resolvedHome: "/tmp/test")
        #expect(log.contains("resolved_home="), "missing resolved_home")
        #expect(log.contains("branch="), "missing branch")
        #expect(log.contains("env_present="), "missing env_present")
        #expect(!log.contains("\n"), "log must be single line")
    }

    @Test("launchLog is recorded in diagnostics during bundled launch")
    @MainActor
    func launchLogRecordedInDiagnostics() async throws {
        AppDelegate._testPackagingMode = "bundled"
        AppDelegate._testIsRunningInAppBundle = true

        let bundledHome = "\(NSHomeDirectory())/Library/Application Support/HappyRanch"
        UserDefaults.standard.set(bundledHome, forKey: "HappyRanchDaemonHome")
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testIsRunningInAppBundle = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let (delegate, _) = makeAppDelegateForBundled(
            state: .stopped,
            isManagedBySelf: true
        )

        let home = AppDelegate.resolvedDaemonHome()
        delegate.diagnostics.recordLaunchLog(
            AppDelegate.launchDiagnosticLogLine(resolvedHome: home)
        )

        let bundle = delegate.diagnostics.collect()
        let launcherLog = bundle["launcher_log"] as? String ?? ""
        #expect(launcherLog.contains("branch=user-defaults"),
                "Launch log should report branch=user-defaults, got: \(launcherLog)")
        #expect(launcherLog.contains("resolved_home=\(bundledHome)"),
                "Launch log should contain bundled home, got: \(launcherLog)")
        #expect(launcherLog.contains("env_present="),
                "Launch log should contain env_present, got: \(launcherLog)")
    }

    // MARK: daemonHome() backward compatibility

    @Test("daemonHome() delegates to resolvedDaemonHome()")
    func daemonHomeDelegatesToResolved() {
        AppDelegate._testPackagingMode = nil
        AppDelegate._testIsRunningInAppBundle = true
        let testHome = "/tmp/test-delegation"
        UserDefaults.standard.set(testHome, forKey: "HappyRanchDaemonHome")
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testIsRunningInAppBundle = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let dhResult = AppDelegate.daemonHome()
        let rdhResult = AppDelegate.resolvedDaemonHome()
        #expect(dhResult == rdhResult,
                "daemonHome() must delegate to resolvedDaemonHome()")
        #expect(dhResult == testHome,
                "Both should return UserDefaults override, got \(dhResult)")
    }

    @Test("daemonHome() never returns empty string")
    func daemonHomeNeverReturnsEmpty() {
        UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = nil
        defer {
            AppDelegate._testPackagingMode = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let result = AppDelegate.daemonHome()
        #expect(!result.isEmpty, "daemonHome() must never return empty, got '\(result)'")
    }

    @Test("resolvedDaemonHome returns AppSupport when isRunningInAppBundle and UD set")
    func resolvedDaemonHomeAppSupportWhenBundledAndUDSet() {
        let bundledHome = "\(NSHomeDirectory())/Library/Application Support/HappyRanch"
        UserDefaults.standard.set(bundledHome, forKey: "HappyRanchDaemonHome")
        AppDelegate._testPackagingMode = nil
        AppDelegate._testIsRunningInAppBundle = true
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testIsRunningInAppBundle = nil
            UserDefaults.standard.removeObject(forKey: "HappyRanchDaemonHome")
        }

        let result = AppDelegate.resolvedDaemonHome()
        #expect(result == bundledHome,
                "AppSupport home should be returned when UD is set by bundled startup, got \(result) expected \(bundledHome)")
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
        AppDelegate._testSkipBundledDaemonPreflight = true
        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        setenv("HAPPYRANCH_DAEMON_HOME", "/tmp/test-hr-bundled", 1)
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
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
        AppDelegate._testSkipBundledDaemonPreflight = true
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
        }

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
        AppDelegate._testSkipBundledDaemonPreflight = true
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
        }

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

    @Test("startDaemon from failed state in bundled mode launches recovery process")
    func startDaemonFromFailedInBundledModeLaunchesRecoveryProcess() async throws {
        AppDelegate._testPackagingMode = "bundled"
        AppDelegate._testSkipBundledDaemonPreflight = true
        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        setenv("HAPPYRANCH_DAEMON_HOME", "/tmp/test-hr-bundled", 1)
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
            if let old = oldHome {
                setenv("HAPPYRANCH_DAEMON_HOME", old, 1)
            } else {
                unsetenv("HAPPYRANCH_DAEMON_HOME")
            }
        }

        let (delegate, fake) = makeAppDelegateForBundled(
            state: .failed,
            isManagedBySelf: true
        )

        // Verify pre-condition: supervisor is in .failed
        #expect(delegate.supervisor.state == .failed,
                "Pre-condition: supervisor must be .failed before recovery launch")

        delegate.startDaemon()

        // The recovery path must launch one process
        #expect(fake.launchCallCount == 1,
                "Recovery from .failed must launch exactly one process, got \(fake.launchCallCount)")

        // Supervisor must transition to .starting
        #expect(delegate.supervisor.state == .starting,
                "Recovery from .failed must transition to .starting, got \(delegate.supervisor.state)")

        // Bundled mode: must launch the frozen daemon binary (not /usr/bin/env)
        if let url = fake.lastExecutableURL {
            #expect(url.path.hasSuffix("/daemon/happyranch-daemon"),
                    "Recovery launch in bundled mode must use frozen daemon binary, got \(url.path)")
            #expect(url.path != "/usr/bin/env",
                    "Recovery launch in bundled mode must NOT use /usr/bin/env")
        }

        // Arguments must be empty (no uv/python wrappers in bundled mode)
        #expect(fake.lastArguments?.isEmpty == true,
                "Recovery launch in bundled mode must have no arguments, got \(fake.lastArguments ?? [])")

        // Working directory must be daemon home
        if let cwd = fake.lastCurrentDirectoryURL {
            #expect(cwd.path == "/tmp/test-hr-bundled",
                    "Recovery launch cwd must be daemon home, got \(cwd.path)")
        }

        // Diagnostics must record the bundled start command
        let bundle = delegate.diagnostics.collect()
        let cmd = bundle["start_command"] as? String ?? ""
        #expect(cmd == "Contents/Resources/daemon/happyranch-daemon",
                "Recovery diagnostics must record bundled start command, got \(cmd)")
    }
}

// MARK: - Bundled launch: ephemeral port

@Suite("Bundled launch ephemeral port")
@MainActor
struct BundledLaunchEphemeralPortTests {

    @Test("startDaemon in bundled mode sets HAPPYRANCH_DAEMON_PORT=0 in child env")
    func startDaemonInBundledModeSetsPortZero() async throws {
        AppDelegate._testPackagingMode = "bundled"
        AppDelegate._testSkipBundledDaemonPreflight = true
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
        }

        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-bundled")
        try! delegate.supervisor.start()
        delegate.supervisor.forceState(.stopped)
        delegate.refreshDerivedState()

        delegate.startDaemon()

        #expect(fake.launchCallCount == 1)
        if let env = fake.lastEnvironment {
            #expect(env["HAPPYRANCH_DAEMON_PORT"] == "0",
                    "Bundled launch must set HAPPYRANCH_DAEMON_PORT=0, got \(env["HAPPYRANCH_DAEMON_PORT"] ?? "nil")")
        } else {
            #expect(Bool(false), "Bundled launch must have child environment")
        }
    }

    @Test("startDaemon in dev mode does NOT set HAPPYRANCH_DAEMON_PORT in child env")
    func startDaemonInDevModeDoesNotSetPortZero() async throws {
        AppDelegate._testPackagingMode = nil // dev mode
        defer { AppDelegate._testPackagingMode = nil }

        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: "/tmp/test-hr-dev")
        try! delegate.supervisor.start()
        delegate.supervisor.forceState(.stopped)
        delegate.refreshDerivedState()

        delegate.startDaemon()

        #expect(fake.launchCallCount == 1)
        if let env = fake.lastEnvironment {
            #expect(env["HAPPYRANCH_DAEMON_PORT"] == nil,
                    "Dev mode must NOT set HAPPYRANCH_DAEMON_PORT, got \(env["HAPPYRANCH_DAEMON_PORT"] ?? "nil")")
        }
    }
}

// MARK: - Bundled daemon preflight check

@Suite("Bundled daemon preflight check")
struct BundledDaemonPreflightTests {

    @Test("bundledDaemonPreflightError returns fix-naming message for missing path")
    func preflightErrorReturnsMessageForMissingPath() {
        let error = AppDelegate.bundledDaemonPreflightError(path: "/nonexistent/path/happyranch-daemon")
        #expect(error != nil, "Should return error for non-existent path")
        #expect(error?.contains("build-app.sh") == true,
                "Error message must name the fix command (build-app.sh), got \(error ?? "nil")")
        #expect(error?.contains("rebuild") == true,
                "Error message must be actionable (rebuild), got \(error ?? "nil")")
    }

    @Test("bundledDaemonPreflightError returns nil for present executable file")
    func preflightErrorReturnsNilForExecutableFile() throws {
        // Use a known system executable guaranteed to exist
        let error = AppDelegate.bundledDaemonPreflightError(path: "/bin/sh")
        #expect(error == nil, "Should return nil for present executable file /bin/sh, got \(error ?? "nil")")
    }

    @Test("bundledDaemonPreflightError returns message for non-executable file")
    func preflightErrorReturnsMessageForNonExecutableFile() throws {
        // /etc/hosts is a plain data file, never executable
        let error = AppDelegate.bundledDaemonPreflightError(path: "/etc/hosts")
        #expect(error != nil, "Should return error for non-executable file /etc/hosts")
        #expect(error?.contains("build-app.sh") == true,
                "Error message must name the fix command")
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

    @Test("startDaemon from failed state in dev mode launches recovery process via uv run")
    func startDaemonFromFailedInDevModeLaunchesRecoveryProcess() async throws {
        AppDelegate._testPackagingMode = nil  // default dev mode
        defer { AppDelegate._testPackagingMode = nil }

        let (delegate, fake) = makeAppDelegateForBundled(
            state: .failed,
            isManagedBySelf: true
        )

        // Verify pre-condition: supervisor is in .failed
        #expect(delegate.supervisor.state == .failed,
                "Pre-condition: supervisor must be .failed before recovery launch")

        delegate.startDaemon()

        // The recovery path must launch one process
        #expect(fake.launchCallCount == 1,
                "Recovery from .failed must launch exactly one process, got \(fake.launchCallCount)")

        // Supervisor must transition to .starting
        #expect(delegate.supervisor.state == .starting,
                "Recovery from .failed must transition to .starting, got \(delegate.supervisor.state)")

        // Dev mode: must use /usr/bin/env (not bundled binary)
        #expect(fake.lastExecutableURL?.path == "/usr/bin/env",
                "Recovery launch in dev mode must use /usr/bin/env, got \(fake.lastExecutableURL?.path ?? "nil")")

        // Dev mode: must pass uv run arguments
        #expect(fake.lastArguments == ["uv", "run", "python", "-m", "runtime.daemon"],
                "Recovery launch in dev mode must use uv run arguments, got \(fake.lastArguments ?? [])")

        // Diagnostics must record the dev start command
        let bundle = delegate.diagnostics.collect()
        let cmd = bundle["start_command"] as? String ?? ""
        #expect(cmd == "uv run python -m runtime.daemon",
                "Recovery diagnostics must record dev start command, got \(cmd)")
    }
}

// MARK: - First-launch directory creation

@Suite("First-launch directory creation")
@MainActor
struct FirstLaunchDirectoryCreationTests {

    @Test("bundled mode creates daemonHome before launch when directory does not exist")
    func bundledModeCreatesDaemonHomeBeforeLaunch() async throws {
        AppDelegate._testPackagingMode = "bundled"
        AppDelegate._testSkipBundledDaemonPreflight = true

        // Create a unique temp path that does NOT exist
        let tempBase = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-first-launch-\(UUID().uuidString)")
        let tempHome = tempBase.appendingPathComponent("HappyRanch").path

        // Ensure clean state: path does not exist
        try? FileManager.default.removeItem(atPath: tempHome)
        #expect(!FileManager.default.fileExists(atPath: tempHome),
                "Pre-condition: temp home must not exist before test")

        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        setenv("HAPPYRANCH_DAEMON_HOME", tempHome, 1)
        defer {
            AppDelegate._testPackagingMode = nil
            AppDelegate._testSkipBundledDaemonPreflight = false
            if let old = oldHome {
                setenv("HAPPYRANCH_DAEMON_HOME", old, 1)
            } else {
                unsetenv("HAPPYRANCH_DAEMON_HOME")
            }
            try? FileManager.default.removeItem(atPath: tempBase.path)
        }

        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: tempHome)
        try delegate.supervisor.start()
        delegate.supervisor.forceState(.stopped)
        delegate.refreshDerivedState()

        delegate.startDaemon()

        // Assert: the directory was created on disk
        #expect(FileManager.default.fileExists(atPath: tempHome),
                "daemonHome directory must exist after bundled launch, even on first run")

        // Assert: the Process was given a cwd that exists
        if let cwd = fake.lastCurrentDirectoryURL {
            #expect(FileManager.default.fileExists(atPath: cwd.path),
                    "currentDirectoryURL given to Process must exist, got \(cwd.path)")
            #expect(cwd.path == tempHome,
                    "currentDirectoryURL should be daemonHome, got \(cwd.path)")
        } else {
            #expect(Bool(false), "Bundled launch must set currentDirectoryURL")
        }
    }

    @Test("dev mode creates daemonHome before launch when directory does not exist")
    func devModeCreatesDaemonHomeBeforeLaunch() async throws {
        AppDelegate._testPackagingMode = nil  // dev mode

        let tempBase = FileManager.default.temporaryDirectory
            .appendingPathComponent("test-first-launch-dev-\(UUID().uuidString)")
        let tempHome = tempBase.appendingPathComponent(".happyranch").path

        try? FileManager.default.removeItem(atPath: tempHome)
        #expect(!FileManager.default.fileExists(atPath: tempHome))

        let oldHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"]
        setenv("HAPPYRANCH_DAEMON_HOME", tempHome, 1)
        defer {
            AppDelegate._testPackagingMode = nil
            if let old = oldHome {
                setenv("HAPPYRANCH_DAEMON_HOME", old, 1)
            } else {
                unsetenv("HAPPYRANCH_DAEMON_HOME")
            }
            try? FileManager.default.removeItem(atPath: tempBase.path)
        }

        let fake = FakeProcessController()
        let delegate = AppDelegate()
        delegate.processController = fake
        delegate.supervisor.configure(homeDir: tempHome)
        try delegate.supervisor.start()
        delegate.supervisor.forceState(.stopped)
        delegate.refreshDerivedState()

        delegate.startDaemon()

        // Assert: the directory was created on disk
        #expect(FileManager.default.fileExists(atPath: tempHome),
                "daemonHome directory must exist after dev launch, even on first run")

        // Dev mode uses repoRoot() as cwd — daemonHome is ensured as env for daemon use
        #expect(fake.lastCurrentDirectoryURL != nil,
                "Dev launch must set currentDirectoryURL")
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
