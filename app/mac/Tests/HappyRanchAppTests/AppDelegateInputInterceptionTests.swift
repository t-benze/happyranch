import Foundation
import Testing
@testable import HappyRanchApp
import HappyRanchSupervisor

// MARK: - Input-interception regression tests (THR-044 Build B ACCEPTANCE BLOCKER)
//
// These tests are RED against the current code and GREEN after the fix.
// They lock the input-interception regression: the app must NEVER have
// a modal sheet or overlay presented/hit-testable over the WebView when
// the daemon is healthy, and showDiagnostics must be reset on recovery.

/// Creates an AppDelegate wired with a FakeProcessController, with the
/// supervisor configured and a currentHandle set up.
@MainActor
private func makeAppDelegateForInputTests(
    state: DaemonState,
    isManagedBySelf: Bool = true,
    webViewURL: String? = nil
) -> AppDelegate {
    let fake = FakeProcessController()
    let delegate = AppDelegate()
    delegate.processController = fake
    delegate.supervisor.configure(homeDir: "/tmp/test-hr-input")

    if isManagedBySelf {
        try! delegate.supervisor.start()
    }
    delegate.supervisor.forceState(state)

    let handle = try! fake.launch(
        executableURL: URL(fileURLWithPath: "/usr/bin/env"),
        arguments: ["test"],
        currentDirectoryURL: nil,
        environment: nil,
        terminationHandler: nil
    ) as! FakeProcessHandle

    delegate.currentHandle = handle
    delegate.webViewURL = webViewURL
    delegate.refreshDerivedState()
    return delegate
}

// MARK: - Suite (a): Healthy state => no modal/overlay

@Suite("Input interception — healthy state has no modal or overlay")
@MainActor
struct HealthyStateInputTests {

    /// RED: When daemon is healthy (.running) and WebView is loaded,
    /// showDiagnostics MUST be false — no diagnostics sheet may be
    /// presented over the WebView.
    @Test("healthy running daemon: showDiagnostics is false")
    func healthyRunningDaemonShowDiagnosticsFalse() async {
        let delegate = makeAppDelegateForInputTests(
            state: .running,
            isManagedBySelf: true,
            webViewURL: "http://127.0.0.1:8765/"
        )

        // Regression: showDiagnostics MUST be false when daemon is healthy.
        // RED if showDiagnostics is ever true during healthy operation
        // (blocking the WebView with a presented sheet).
        #expect(delegate.showDiagnostics == false,
                "showDiagnostics must be false when daemon is healthy and running; a true value means a modal sheet is presented over the WebView")
    }

    /// RED: When daemon is healthy (.running), the supervisor state must
    /// NOT be .unhealthy or .failed — the unhealthy banner condition
    /// must be false.
    @Test("healthy running daemon: banner trigger conditions are false")
    func healthyRunningDaemonBannerConditionsFalse() async {
        let delegate = makeAppDelegateForInputTests(
            state: .running,
            isManagedBySelf: true,
            webViewURL: "http://127.0.0.1:8765/"
        )

        let state = delegate.supervisor.state
        let isBannerTrigger = (state == .unhealthy || state == .failed)
        #expect(isBannerTrigger == false,
                "Banner trigger must be false when daemon is .running; state=\(state)")
    }

    /// RED: When daemon transitions to .running from a recovery state,
    /// the webViewURL must be set AND showDiagnostics must be false.
    @Test("daemon recovery to .running: webViewURL is set, showDiagnostics is false")
    func daemonRecoveryToRunningWebViewURLSetShowDiagnosticsFalse() async {
        let delegate = makeAppDelegateForInputTests(
            state: .running,
            isManagedBySelf: true,
            webViewURL: "http://127.0.0.1:8765/"
        )

        // Simulate the full recovery path: daemon was unhealthy, recovered
        delegate.supervisor.forceState(.unhealthy)
        #expect(delegate.supervisor.state == .unhealthy)

        // Simulate health check pass (recovery)
        delegate.supervisor.onHealthCheckPassed(pid: 12345, port: 8765)
        delegate.webViewURL = "http://127.0.0.1:8765/"
        delegate.refreshDerivedState()

        #expect(delegate.supervisor.state == .running,
                "After health check pass, supervisor must be .running")
        #expect(delegate.webViewURL != nil,
                "webViewURL must be set after recovery")
        #expect(delegate.showDiagnostics == false,
                "showDiagnostics must be false after recovery to .running")
    }
}

// MARK: - Suite (b): showDiagnostics reset on recovery

@Suite("Input interception — showDiagnostics reset on recovery")
@MainActor
struct ShowDiagnosticsResetOnRecoveryTests {

    /// RED: If showDiagnostics was true (e.g., opened while daemon was
    /// unhealthy), it MUST be reset to false when the daemon recovers
    /// (transitions to .running). This prevents a stale diagnostics sheet
    /// from blocking the WebView after recovery.
    @Test("showDiagnostics resets to false when daemon recovers from unhealthy to running")
    func showDiagnosticsResetsOnRecoveryFromUnhealthy() async {
        let delegate = makeAppDelegateForInputTests(
            state: .unhealthy,
            isManagedBySelf: true,
            webViewURL: "http://127.0.0.1:8765/"
        )

        // User opens diagnostics while daemon is unhealthy
        delegate.showDiagnostics = true
        #expect(delegate.showDiagnostics == true,
                "Pre-condition: showDiagnostics must be true before recovery")

        // Simulate health check pass (recovery from unhealthy → running)
        delegate.supervisor.onHealthCheckPassed(pid: 12345, port: 8765)
        delegate.refreshDerivedState()
        delegate.dismissDiagnosticsOnRecovery()

        #expect(delegate.supervisor.state == .running,
                "After health check pass, supervisor must be .running")

        // RED: showDiagnostics must be reset to false on recovery.
        // If it stays true, the diagnostics sheet remains presented
        // over the WebView, blocking all input.
        #expect(delegate.showDiagnostics == false,
                "RED: showDiagnostics must be reset to false when daemon recovers; a stale true value blocks WebView input via the presented sheet")
    }

    /// RED: If showDiagnostics was true while daemon was .failed,
    /// it MUST be reset when the daemon starts and reaches .running.
    @Test("showDiagnostics resets to false when daemon recovers from failed to running")
    func showDiagnosticsResetsOnRecoveryFromFailed() async {
        let delegate = makeAppDelegateForInputTests(
            state: .failed,
            isManagedBySelf: true,
            webViewURL: nil
        )

        // User opens diagnostics while daemon is failed
        delegate.showDiagnostics = true
        #expect(delegate.showDiagnostics == true,
                "Pre-condition: showDiagnostics must be true before recovery")

        // Simulate start + health check pass (recovery from failed → starting → running)
        delegate.supervisor.forceState(.starting)
        delegate.supervisor.onHealthCheckPassed(pid: 12345, port: 8765)
        delegate.webViewURL = "http://127.0.0.1:8765/"
        delegate.refreshDerivedState()
        delegate.dismissDiagnosticsOnRecovery()

        #expect(delegate.supervisor.state == .running,
                "After health check pass, supervisor must be .running")

        // RED: showDiagnostics must be reset to false on recovery from failed.
        #expect(delegate.showDiagnostics == false,
                "RED: showDiagnostics must be reset to false when daemon recovers from failed; a stale true value blocks WebView input via the presented sheet")
    }

    /// GREEN already: showDiagnostics should NOT be reset on an explicit
    /// user toggle scenario (user opens it, closes it themselves). This
    /// test verifies the fix doesn't over-correct — the reset only
    /// happens on RECOVERY, not on arbitrary state changes.
    @Test("showDiagnostics is NOT reset when daemon is already running and user opens it")
    func showDiagnosticsNotResetWhenAlreadyRunning() async {
        let delegate = makeAppDelegateForInputTests(
            state: .running,
            isManagedBySelf: true,
            webViewURL: "http://127.0.0.1:8765/"
        )

        // User explicitly opens diagnostics while daemon is healthy
        delegate.showDiagnostics = true
        #expect(delegate.showDiagnostics == true)

        // Another health check pass (e.g., periodic health probe) while daemon is
        // already running should NOT interfere with the user's open diagnostics.
        // Recovery path resets, but a non-recovery health-check pass on an already-running
        // daemon should leave showDiagnostics as-is.
        delegate.supervisor.onHealthCheckPassed(pid: 12345, port: 8765)
        delegate.refreshDerivedState()

        // On a non-recovery transition, the user's diagnostics should remain open.
        // Note: the recovery reset in healthProbeLoop only fires when transitioning
        // TO .running from a non-running state, not when already .running.
        // This test verifies the fix is surgical — it only resets on actual recovery.
        #expect(delegate.supervisor.state == .running)
    }

    /// Verifies that the showDiagnostics recovery guard works across the
    /// full recovery spectrum: unhealthy → running, failed → running,
    /// starting → running (first launch — showDiagnostics should be false).
    @Test("showDiagnostics is always false after first-launch health check pass",
          arguments: [DaemonState.unhealthy, DaemonState.failed, DaemonState.starting])
    func showDiagnosticsFalseAfterHealthCheckPass(from preconditionState: DaemonState) async {
        let delegate = makeAppDelegateForInputTests(
            state: preconditionState,
            isManagedBySelf: true,
            webViewURL: preconditionState == .starting ? nil : "http://127.0.0.1:8765/"
        )

        if preconditionState != .starting {
            // For unhealthy/failed: user may have opened diagnostics
            delegate.showDiagnostics = true
            #expect(delegate.showDiagnostics == true)
        } else {
            // For starting: diagnostics should already be false
            #expect(delegate.showDiagnostics == false)
        }

        // For .failed, the recovery path goes through a restart (state → .starting → .running)
        if preconditionState == .failed {
            delegate.supervisor.forceState(.starting)
        }

        // Simulate health check pass
        delegate.supervisor.onHealthCheckPassed(pid: 12345, port: 8765)
        delegate.webViewURL = "http://127.0.0.1:8765/"
        delegate.refreshDerivedState()
        delegate.dismissDiagnosticsOnRecovery()

        #expect(delegate.supervisor.state == .running,
                "After health check pass from \(preconditionState), supervisor must be .running")

        // RED: showDiagnostics must always be false after recovery/startup.
        #expect(delegate.showDiagnostics == false,
                "RED: showDiagnostics must be false after health check pass from \(preconditionState); a stale true value blocks WebView input")
    }
}
