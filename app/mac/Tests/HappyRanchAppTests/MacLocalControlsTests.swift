import AppKit
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

@MainActor
private final class LocalBackend: MacLocalControlBackend {
    var localObservation = MacLocalObservation(supported: true, running: "running")
    var localMutationAvailable = true
    var localUpdateObservation = "unknown"
    var consent: CheckedContinuation<Bool, Never>?
    var effect: CheckedContinuation<Void, Error>?
    var holdEffect = false
    var failEffect = false
    var reconciliation = true
    var effects: [String] = []
    var reconciliations = 0
    var health = try! AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":1,"min_client":1}}"#.utf8))

    func confirmLocalRestart() async -> Bool {
        await withCheckedContinuation { consent = $0 }
    }
    func performLocalCommand(_ command: String) async throws -> MacLocalReadmission? {
        effects.append(command)
        if holdEffect { try await withCheckedThrowingContinuation { effect = $0 } }
        if failEffect { throw AppAPIError.unavailable }
        localObservation = .init(supported: true, running: command == "local.stop" ? "stopped" : "running")
        return command == "local.stop" ? nil : MacLocalReadmission(health: health)
    }
    func reconcileLocalService() async -> Bool {
        reconciliations += 1
        return reconciliation && localObservation.settled
    }
}

@MainActor
private final class LocalCaller {
    var current = true
    var refusal: String?
    var value: String?
    var error: String?
    var replies = 0
    func call(_ controls: MacLocalControls, _ command: String, data: String = "{}") {
        controls.call(command: command, data: data, current: { self.current }, gate: { self.refusal }) { value, error in
            self.value = value as? String; self.error = error; self.replies += 1
        }
    }
    var result: ConnectionJSON? { value.flatMap { try? ConnectionJSON.parse($0) } }
    var failure: ConnectionJSON? { error.flatMap { try? ConnectionJSON.parse($0) } }
}

@Suite("Mac local controls", .serialized, .timeLimit(.minutes(1)))
@MainActor
struct MacLocalControlsTests {
    private func until(_ condition: () -> Bool) async {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(2))
        while !condition(), clock.now < deadline { try? await clock.sleep(for: .milliseconds(5)) }
        #expect(condition())
    }

    @Test("Closed arguments reject before consent or effects", arguments: [
        "", "null", "[]", "{\"target\":\"remote\"}", "{\"schema\":2}", "{\"schema\":2,\"schema\":2}", String(repeating: " ", count: 4097)
    ])
    func invalidArguments(raw: String) {
        let backend = LocalBackend(), caller = LocalCaller()
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, "local.restart", data: raw)
        #expect(caller.failure?.object?["code"] == .string("invalidRequest"))
        #expect(caller.failure?.object?["recovery"] == .string("none"))
        #expect(!controls.busy && backend.effects.isEmpty && backend.consent == nil)
    }

    @Test("Unsupported Mac mutations refuse without effects", arguments: ["local.keepRunning.set", "local.repair", "local.turnOff"])
    func unsupported(command: String) {
        let backend = LocalBackend(), caller = LocalCaller()
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, command, data: command == "local.keepRunning.set" ? #"{"schema":2,"enabled":true}"# : "{}")
        #expect(caller.failure?.object?["code"] == .string("unavailable"))
        #expect(caller.failure?.object?["effect"] == .string("none"))
        #expect(!controls.busy && backend.effects.isEmpty)
    }

    @Test("Keep-running arguments validate before unsupported refusal", arguments: [
        "{}", #"{"schema":2,"enabled":1}"#, #"{"schema":true,"enabled":false}"#,
        #"{"schema":2,"enabled":false,"extra":true}"#, #"{"schema":2,"enabled":true,"enabled":false}"#
    ])
    func invalidKeepRunningArguments(raw: String) {
        let backend = LocalBackend(), caller = LocalCaller()
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, "local.keepRunning.set", data: raw)
        #expect(caller.failure?.object?["code"] == .string("invalidRequest"))
        #expect(!controls.busy && backend.effects.isEmpty)
    }

    @Test("Reservation precedes consent; busy refuses and reads remain available")
    func reservationAndConsent() async {
        let backend = LocalBackend(), first = LocalCaller(), other = LocalCaller(), read = LocalCaller()
        let controls = MacLocalControls(backend: backend)
        first.call(controls, "local.restart")
        #expect(controls.busy)
        await until { backend.consent != nil }
        #expect(backend.effects.isEmpty)
        other.call(controls, "local.stop")
        #expect(other.failure?.object?["code"] == .string("busy"))
        read.call(controls, "local.status")
        #expect(read.result?.object?["service"]?.object?["busy"] == .bool(true))
        #expect(read.result?.object?["service"]?.object?["keepRunning"] == .string("unavailable"))
        backend.consent?.resume(returning: false); backend.consent = nil
        await until { first.replies == 1 }
        #expect(first.failure?.object?["code"] == .string("permissionDenied"))
        #expect(!controls.busy && backend.effects.isEmpty)
    }

    @Test("External daemon observations never authorize mutations", arguments: ["local.start", "local.stop", "local.restart"])
    func externalDaemon(command: String) {
        let backend = LocalBackend(), caller = LocalCaller(), read = LocalCaller()
        backend.localObservation = .init(supported: false, running: "running")
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, command)
        #expect(caller.failure?.object?["code"] == .string("unavailable"))
        read.call(controls, "local.status")
        #expect(read.result?.object?["service"]?.object?["running"] == .string("running"))
        #expect(read.result?.object?["service"]?.object?["supported"] == .bool(false))
        #expect(backend.effects.isEmpty && backend.consent == nil)
    }

    @Test("Document and owner gates revalidate after consent", arguments: ["document", "owner", "external"])
    func postConsentGate(change: String) async {
        let backend = LocalBackend(), caller = LocalCaller()
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, "local.restart")
        await until { backend.consent != nil }
        if change == "document" { caller.current = false }
        if change == "owner" { caller.refusal = "owner_save_pending" }
        if change == "external" { backend.localObservation = .init(supported: false, running: "running") }
        backend.consent?.resume(returning: true); backend.consent = nil
        await until { caller.replies == 1 }
        #expect(caller.failure?.object?["effect"] == .string("none"))
        #expect(backend.effects.isEmpty && !controls.busy)
    }

    @Test("Lost document and failed effects retain custody; reads never replay", arguments: [false, true])
    func retainedOutcome(failed: Bool) async {
        let backend = LocalBackend(), caller = LocalCaller(), next = LocalCaller(), read = LocalCaller()
        backend.holdEffect = true; backend.failEffect = failed
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, "local.start")
        await until { backend.effect != nil }
        read.call(controls, "local.status")
        #expect(read.result?.object?["service"]?.object?["busy"] == .bool(true))
        if !failed { caller.current = false }
        backend.effect?.resume(); backend.effect = nil
        await until { caller.replies == 1 }
        #expect(caller.failure?.object?["code"] == .string("outcomeUnknown"))
        #expect(controls.busy && backend.effects == ["local.start"])
        next.call(controls, "local.start")
        #expect(next.failure?.object?["code"] == .string("busy"))
        read.call(controls, "local.update.status")
        #expect(controls.busy && backend.reconciliations == 0)
        backend.reconciliation = false
        let replies = read.replies
        read.call(controls, "local.status")
        await until { read.replies == replies + 1 }
        #expect(backend.reconciliations == 1 && controls.busy)
        backend.reconciliation = true
        read.call(controls, "local.status")
        await until { !controls.busy }
        #expect(backend.effects == ["local.start"])
    }

    @Test("Start and Restart carry closed compatibility; Stop omits it", arguments: ["local.start", "local.restart", "local.stop"])
    func readmission(command: String) async throws {
        let backend = LocalBackend(), caller = LocalCaller()
        backend.health = try AppAPIHealth(status: 200, data: Data(#"{"api_contract":{"current":2,"min_client":2}}"#.utf8))
        let controls = MacLocalControls(backend: backend)
        caller.call(controls, command)
        if command == "local.restart" {
            await until { backend.consent != nil }
            backend.consent?.resume(returning: true); backend.consent = nil
        }
        await until { caller.replies == 1 }
        #expect(caller.result?.object?["command"] == .string(command))
        if command == "local.stop" { #expect(caller.result?.object?["readmission"] == nil) }
        else {
            let readmission = caller.result?.object?["readmission"]?.object
            #expect(readmission?["state"] == .string("incompatible"))
            #expect(readmission?["incompatible"]?.object?["side"] == .string("app"))
        }
        #expect(!controls.busy && backend.effects == [command])
    }

    @Test("Readmission names ready, unavailable, invalid metadata and update-busy", arguments: ["ready", "unreachable", "incompatible", "unknown"])
    func admissionProjection(state: String) throws {
        let raw: String?
        switch state {
        case "ready": raw = #"{"api_contract":{"current":1,"min_client":1}}"#
        case "incompatible": raw = "{}"
        case "unknown": raw = #"{"api_contract":{"current":1,"min_client":1},"update_busy":{"busy":true}}"#
        default: raw = nil
        }
        let health = try raw.map { try AppAPIHealth(status: 200, data: Data($0.utf8)) }
        let value = MacLocalReadmission(health: health).json
        #expect(Set(value.keys) == ["state", "incompatible"])
        #expect(value["state"] as? String == state)
        if state == "incompatible" {
            #expect((value["incompatible"] as? [String: Any])?["side"] as? String == "host")
        } else { #expect(value["incompatible"] is NSNull) }
    }

}
