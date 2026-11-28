import AppKit
import Darwin
import Foundation
import Testing
import HappyRanchSupervisor
@preconcurrency import Sparkle
@testable import HappyRanchApp

@Suite("Update gate", .serialized)
@MainActor
struct UpdateGateTests {
    @Test("owned idle update observes exit before one Sparkle release after both gates")
    func idleStopsBeforeRelease() async throws {
        let (f, factory, handle) = try await ownedDaemon(); defer { f.dispose() }
        var releases = 0
        f.app.postponeUpdateRelaunch {
            #expect(!handle.isRunning); #expect(factory.terminateCallCount == 1); releases += 1
        }
        #expect(f.backend.prompts.last?.kind == .updateIdle)
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: handle.isRunning, runningSessions: 0) == .confirmRestart)
        #expect(factory.terminateCallCount == 0 && releases == 0)
        // A caller-supplied policy choice cannot substitute for native response.
        f.app.applyUpdateGate(runningSessions: 0, choice: .restart)
        #expect(factory.terminateCallCount == 0 && releases == 0)
        try f.respond()
        #expect(factory.terminateCallCount == 0 && releases == 0)
        _ = try await f.answer()
        #expect(releases == 1 && !f.app.hasPendingUpdate)
        f.app.applyUpdateGate(runningSessions: 0, choice: .restart)
        #expect(releases == 1)
    }

    @Test("aborted Sparkle release cannot admit later Quit with new protected owners", arguments: ["dirty", "pending", "unknown"])
    func abortedReleaseRequiresFreshQuitGate(held: String) async throws {
        let (f, factory, handle) = try await ownedDaemon(); defer { f.dispose() }
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }
        try f.respond()
        let updateToken = try #require(f.app.appLifetime.token)
        _ = try await f.answer()
        #expect(releases == 1 && !handle.isRunning && factory.terminateCallCount == 1)
        let controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: nil, userDriverDelegate: nil)
        f.app.updater(controller.updater, didAbortWithError: NSError(domain: "SyntheticInstallerError", code: 1))
        let blocking = held == "dirty" ? "prompt" : "hold"
        let counts = ["dirty", "failed", "pending", "unknown"].map { "\"\($0)\":\($0 == held ? 1 : 0)" }.joined(separator: ",")
        let summary = "{\"schema\":2,\"blocking\":\"\(blocking)\",\"counts\":{\(counts)}}"
        _ = try await f.call("app.ownersChanged", summary)
        #expect(f.app.applicationShouldTerminate(NSApplication.shared) == .terminateCancel)
        #expect(f.app.appLifetime.token != updateToken && f.app.appLifetime.confirmation?.intent == .quit)
        try f.respond()
        let refusal = try await f.answer(summary: summary)
        #expect(refusal.contains("\"effect\":\"none\""))
        #expect(f.termination.calls == 0 && f.backend.effects == [.update])
        #expect(f.app.lifetimeScheme === f.scheme && f.scheme.lifecycleAnchor != nil)
        #expect(releases == 1 && factory.terminateCallCount == 1 && !f.app.hasPendingUpdate)
    }

    @Test("When idle re-enters fresh consent; Later retains the download until explicit resume")
    func waitAndLater() async throws {
        let (f, factory, _) = try await ownedDaemon(); defer { f.dispose() }
        f.backend.count = 3
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }
        #expect(f.backend.prompts.last?.kind == .updateBusy(3))
        let earlier = f.app.appLifetime.token
        try f.respond() // When idle, not stop permission.
        #expect(!f.app.appLifetime.busy && releases == 0 && factory.terminateCallCount == 0)
        f.backend.count = 0
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: true, runningSessions: 0, choice: .whenIdle) == .confirmRestart)
        try await f.eventually(seconds: 8) { f.backend.prompts.last?.kind == .updateIdle }
        #expect(f.app.appLifetime.token != earlier, "Idle polling must reserve fresh response authority")
        #expect(f.backend.prompts.last?.kind == .updateIdle)
        try f.respond(.alertFirstButtonReturn) // Later/Cancel.
        #expect(f.app.hasPendingUpdate && releases == 0)
        f.app.resumePendingUpdate(); try f.respond()
        _ = try await f.answer()
        #expect(factory.terminateCallCount == 1 && releases == 1)
    }

    @Test("busy or unknown after Restart now returns to a fresh prompt without stopping")
    func busyAfterRestartConsentReturnsToPrompt() async throws {
        for count in [2, nil] as [Int?] {
            #expect(UpdateGate.action(ownsDaemon: true, processRunning: true,
                runningSessions: count, choice: .restart) == .prompt(runningSessions: count))
            let (f, factory, _) = try await ownedDaemon(); defer { f.dispose() }
            var releases = 0
            f.app.postponeUpdateRelaunch { releases += 1 }
            f.backend.count = count; try f.respond()
            #expect(f.backend.prompts.last?.kind == .updateBusy(count))
            #expect(factory.terminateCallCount == 0 && releases == 0)
            try f.respond(.alertFirstButtonReturn)
        }
    }

    @Test("Update now stops known busy work only after native response and web receipt")
    func explicitNow() async throws {
        let (f, factory, handle) = try await ownedDaemon(); defer { f.dispose() }
        f.backend.count = 4
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: true, runningSessions: 4) == .prompt(runningSessions: 4))
        var releases = 0
        f.app.postponeUpdateRelaunch { #expect(!handle.isRunning); releases += 1 }
        #expect(f.backend.prompts.last?.kind == .updateBusy(4))
        #expect(factory.terminateCallCount == 0)
        try f.respond(.alertThirdButtonReturn)
        #expect(factory.terminateCallCount == 0 && releases == 0)
        _ = try await f.answer()
        #expect(releases == 1 && factory.terminateCallCount == 1)
    }

    @Test("unknown work or missing process never authorizes stop or install")
    func uncertaintyHolds() async throws {
        for count in [nil, -1] as [Int?] {
            #expect(UpdateGate.action(ownsDaemon: true, processRunning: true, runningSessions: count) == .prompt(runningSessions: nil))
            #expect(UpdateGate.action(ownsDaemon: true, processRunning: true, runningSessions: count, choice: .now) == .prompt(runningSessions: nil))
            let (f, factory, _) = try await ownedDaemon(); defer { f.dispose() }
            f.backend.count = count
            var releases = 0
            f.app.postponeUpdateRelaunch { releases += 1 }
            #expect(f.backend.prompts.last?.kind == .updateBusy(nil))
            try f.respond(.alertThirdButtonReturn) // Disabled/invalid response refuses.
            #expect(releases == 0 && factory.terminateCallCount == 0)
            #expect(f.app.hasPendingUpdate)
        }
        #expect(UpdateGate.action(ownsDaemon: true, processRunning: nil, runningSessions: 0) == .wait)
        #expect(!UpdateGate.canReleaseAfterStop(sameProcess: false, processRunning: false))
        #expect(!UpdateGate.canReleaseAfterStop(sameProcess: true, processRunning: nil))
    }

    @Test("incomplete Stop retains lifetime custody even after a later child exit")
    func incompleteStop() async throws {
        let (f, _, _) = try await ownedDaemon(); defer { f.dispose() }
        let survivor = UpdateSurvivor(); f.app.currentHandle = survivor
        f.app.managedStopGrace = .init(term: 0, kill: 0)
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }; try f.respond()
        _ = try await f.answer()
        #expect(releases == 0 && f.app.hasPendingUpdate && f.app.appLifetime.busy)
        #expect(survivor.terms == 1 && survivor.kills == 1)
        #expect(f.app.stateText.contains("Stop incomplete"))
        #expect(!f.app.canStart)
        survivor.isRunning = false
        f.app.resumePendingUpdate()
        #expect(releases == 0 && f.app.appLifetime.busy)
    }

    @Test("replacement and nested request during Stop cannot release install or signal replacement")
    func replacementDuringStopHolds() async throws {
        let (f, _, handle) = try await ownedDaemon(); defer { f.dispose() }
        let original = try #require(handle as? FakeProcessHandle)
        let replacement = FakeProcessHandle(processIdentifier: 54321)
        let oldCallback = original.onTerminate
        original.onTerminate = {
            #expect(!f.app.appLifetime.request(.quit))
            f.app.currentHandle = replacement; oldCallback?()
        }
        defer { original.onTerminate = nil }
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }; try f.respond()
        let token = try #require(f.app.appLifetime.token?.uuidString)
        _ = try await f.answer()
        #expect(!original.isRunning && replacement.isRunning)
        #expect(releases == 0 && f.app.hasPendingUpdate && f.app.appLifetime.busy)
        let duplicate = try await f.answer(id: token)
        #expect(duplicate.contains("\"effect\":\"unknown\""))
        #expect(f.backend.effects.count == 1)
    }

    @Test("Main retirement during Stop retains consumed custody and cannot release installation")
    func retirementDuringStopHolds() async throws {
        let (f, _, handle) = try await ownedDaemon(); defer { f.dispose() }
        let original = try #require(handle as? FakeProcessHandle)
        let oldCallback = original.onTerminate
        original.onTerminate = { f.scheme.documentDidStart(); oldCallback?() }
        defer { original.onTerminate = nil }
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }; try f.respond()
        _ = try await f.answer()
        #expect(!original.isRunning && releases == 0 && f.app.appLifetime.busy)
        #expect(f.app.hasPendingUpdate)
    }

    @Test("truly external/unowned updates still require web admission and send no signals", arguments: [false, true])
    func unownedPassesThrough(client: Bool) async throws {
        let f = try LifetimeFixture(); defer { f.dispose() }
        try await f.admit()
        let saved = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer { UserDefaults.standard.set(saved, forKey: "connectionRolePreference") }
        let handle = FakeProcessHandle(); f.app.currentHandle = handle
        f.app.supervisor.onExternalDaemonDetected(pid: 12345, port: 8765)
        if client { f.app.connectionRolePreference = .client }
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }
        #expect(releases == 0 && handle.isRunning)
        #expect(f.backend.prompts.isEmpty)
        _ = try await f.answer()
        #expect(releases == 1 && handle.isRunning && !f.app.hasPendingUpdate)
    }

    @Test("install on Quit uses system admission and never invokes Sparkle relaunch callback")
    func quitStopsWithoutRelaunch() async throws {
        let (f, factory, handle) = try await ownedDaemon(); defer { f.dispose() }
        var releases = 0
        f.app.postponeUpdateRelaunch { releases += 1 }
        try f.respond(.alertFirstButtonReturn)
        #expect(f.app.applicationShouldTerminate(NSApplication.shared) == .terminateCancel)
        try f.respond(); _ = try await f.answer()
        #expect(!handle.isRunning && factory.terminateCallCount == 1)
        #expect(releases == 0 && f.termination.calls == 1)
        #expect(f.app.applicationShouldTerminate(NSApplication.shared) == .terminateCancel,
                "A returned ordinary delivery must not leave standing exit permission")
        f.app.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        #expect(!f.app.hasPendingUpdate && releases == 0)
    }

    @Test("CLIENT selection cannot erase ownership of the app's existing local child")
    func clientSelectionStillStopsOwnedChild() async throws {
        let saved = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer { UserDefaults.standard.set(saved, forKey: "connectionRolePreference") }
        let (f, factory, handle) = try await ownedDaemon(); defer { f.dispose() }
        f.app.connectionRolePreference = .client
        var releases = 0
        f.app.postponeUpdateRelaunch { #expect(!handle.isRunning); releases += 1 }
        #expect(factory.terminateCallCount == 0 && releases == 0)
        try f.respond(); _ = try await f.answer()
        #expect(!handle.isRunning && factory.terminateCallCount == 1 && releases == 1)
    }

    private func ownedDaemon() async throws -> (LifetimeFixture, FakeProcessController, any ProcessHandle) {
        let f = try LifetimeFixture(); try await f.admit()
        let factory = FakeProcessController()
        let handle = try factory.launch(executableURL: URL(fileURLWithPath: "/unused"), arguments: [],
            currentDirectoryURL: nil, environment: nil, terminationHandler: nil)
        f.app.currentHandle = handle
        f.app.supervisor.configure(homeDir: f.root.path); try f.app.supervisor.start()
        f.app.supervisor.onHealthCheckPassed(pid: handle.processIdentifier, port: 8765)
        f.app.refreshDerivedState()
        return (f, factory, handle)
    }
}

private final class UpdateSurvivor: ProcessHandle, @unchecked Sendable {
    var isRunning = true
    let processIdentifier: Int32 = 321
    var terms = 0
    var kills = 0
    var terminationStatus: Int32 { precondition(!isRunning); return 0 }
    var terminationReason: Process.TerminationReason { .exit }
    var capturedStandardError: String? { nil }
    var capturedStandardOutput: String? { nil }
    func terminate() { terms += 1 }
    func forceKill() -> Int32? { kills += 1; return EPERM }
}
