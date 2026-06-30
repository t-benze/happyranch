import AppKit
import HappyRanchSupervisor

/// Application delegate — owns the daemon supervisor and app-level state.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, ObservableObject {

    let supervisor = DaemonSupervisor()
    let diagnostics = DiagnosticsCollector(homeDir: daemonHome())

    /// Transport for constructing daemon URLs. Only LocalLoopbackTransport is wired.
    let localTransport: RuntimeTransport = LocalLoopbackTransport()

    /// Handle to the app-managed daemon Process. Retained so we can send
    /// SIGTERM on stop/quit. nil when no managed daemon is running.
    private var managedProcess: Process?

    @Published var webViewURL: String?
    @Published var stateText: String = DaemonState.notConfigured.description
    @Published var showDiagnostics = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let home = daemonHome()
        supervisor.configure(homeDir: home)
        stateText = supervisor.state.description

        // Check for existing daemon
        discoverExistingDaemon()
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Terminate the managed daemon if it's still alive.
        // External daemons are NEVER terminated on quit.
        if supervisor.isManagedBySelf,
           supervisor.state == .running || supervisor.state == .starting || supervisor.state == .unhealthy {
            terminateManagedProcess()
        }
    }

    // MARK: - Daemon home

    static func daemonHome() -> String {
        if let envHome = ProcessInfo.processInfo.environment["HAPPYRANCH_DAEMON_HOME"] {
            return envHome
        }
        let home = NSHomeDirectory()
        return "\(home)/.happyranch"
    }

    private func daemonHome() -> String { AppDelegate.daemonHome() }

    // MARK: - External daemon discovery

    private func discoverExistingDaemon() {
        let home = daemonHome()
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
                stateText = supervisor.state.description

                // Probe health before loading the URL
                Task {
                    await probeAndLoad(port: port)
                }
                return
            } else {
                // Stale PID
                supervisor.onStalePidDetected(pid: pid)
                stateText = supervisor.state.description
                return
            }
        }
    }

    // MARK: - Daemon actions

    func startDaemon() {
        do {
            try supervisor.start()
            stateText = supervisor.state.description
            diagnostics.recordStartCommand("uv run python -m runtime.daemon")

            // Launch the daemon process
            launchDaemonProcess()
        } catch {
            stateText = "Error: \(error.localizedDescription)"
        }
    }

    func stopDaemon(confirmed: Bool = false) {
        do {
            try supervisor.stop(confirmed: confirmed)
            stateText = supervisor.state.description

            // Only terminate the managed process; external daemons are never touched
            if supervisor.isManagedBySelf {
                terminateManagedProcess()
            }
        } catch DaemonSupervisorError.externalStopRequiresConfirmation {
            stateText = "Stop requires confirmation for external daemon"
        } catch {
            stateText = "Error: \(error.localizedDescription)"
        }
    }

    func stopDaemonWithConfirmation() {
        stopDaemon(confirmed: true)
    }

    // MARK: - Process management

    private func launchDaemonProcess() {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = ["uv", "run", "python", "-m", "runtime.daemon"]
        task.currentDirectoryURL = URL(fileURLWithPath: repoRoot())

        // Build a SANITIZED environment — do NOT inherit full parent env.
        // Only PATH, HOME, and operational HAPPYRANCH vars survive.
        task.environment = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: daemonHome(),
            parentEnvironment: ProcessInfo.processInfo.environment
        )

        task.terminationHandler = { @Sendable [weak self] process in
            let exitCode = process.terminationStatus
            let signal = process.terminationReason == .uncaughtSignal ? 1 : nil as Int32?
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.managedProcess = nil
                self.supervisor.onProcessExited(exitCode: exitCode, signal: signal)
                self.diagnostics.recordExit(exitCode: exitCode, signal: signal)
                self.stateText = self.supervisor.state.description
            }
        }

        do {
            try task.run()
            managedProcess = task
            let pid = Int32(task.processIdentifier)
            diagnostics.recordDaemonState(pid: pid, port: 0, bindHost: "127.0.0.1", state: "starting")

            // Start health probing loop
            Task {
                await healthProbeLoop(pid: pid)
            }
        } catch {
            stateText = "Launch failed: \(error.localizedDescription)"
            supervisor.onProcessExited(exitCode: -1, signal: nil)
        }
    }

    /// Send SIGTERM to the managed daemon and wait for exit.
    /// Escalates (forces state to crashed) if the process doesn't exit within a bounded wait.
    private func terminateManagedProcess() {
        guard let process = managedProcess, process.isRunning else {
            managedProcess = nil
            return
        }

        try? supervisor.stop()
        stateText = supervisor.state.description

        process.terminate() // SIGTERM

        // Bounded wait for the process to exit
        let deadline = Date().addingTimeInterval(5.0)
        while process.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
        }

        if process.isRunning {
            // Escalate: process didn't respond to SIGTERM
            diagnostics.recordExit(exitCode: -1, signal: 9)
            supervisor.forceState(.crashed)
            stateText = supervisor.state.description
            managedProcess = nil
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

        // Poll for port file and health check
        for _ in 0..<30 { // 30 attempts, ~15 seconds total
            guard supervisor.state == .starting || supervisor.state == .unhealthy else {
                break
            }

            // Try to read the port
            if let port = try? reader.readPort(from: portFile) {
                let baseURL = localTransport.baseURL(for: port)
                let probe = HealthProbe(baseURL: baseURL)
                let (success, latencyMs, errorMessage) = await probe.check()

                await MainActor.run {
                    if success {
                        supervisor.onHealthCheckPassed(pid: pid, port: port)
                        diagnostics.recordHealthProbe(success: true, latencyMs: latencyMs, errorMessage: nil)
                        diagnostics.recordDaemonState(pid: pid, port: port, bindHost: "127.0.0.1", state: "running")
                        stateText = supervisor.state.description
                        webViewURL = baseURL
                    } else {
                        supervisor.onHealthCheckFailed()
                        diagnostics.recordHealthProbe(success: false, latencyMs: latencyMs, errorMessage: errorMessage)
                        stateText = supervisor.state.description
                    }
                }
            }

            // Only continue if still in a state that warrants probing
            guard supervisor.state == .starting || supervisor.state == .unhealthy else {
                break
            }

            try? await Task.sleep(nanoseconds: 500_000_000) // 0.5s
        }
    }

    func probeAndLoad(port: UInt16) async {
        let baseURL = localTransport.baseURL(for: port)
        let probe = HealthProbe(baseURL: baseURL)
        let (success, latencyMs, errorMessage) = await probe.check()

        await MainActor.run {
            if success {
                diagnostics.recordHealthProbe(success: true, latencyMs: latencyMs, errorMessage: nil)
                webViewURL = baseURL
            } else {
                diagnostics.recordHealthProbe(success: false, latencyMs: latencyMs, errorMessage: errorMessage)
            }
        }
    }

    // MARK: - Helpers

    private func processIsAlive(pid: Int32) -> Bool {
        let result = kill(pid, 0)
        return result == 0 || errno == EPERM
    }
}
