import AppKit
import HappyRanchSupervisor

/// Installation-local observations, independent of the selected Main target.
struct MacLocalObservation {
    let supported: Bool
    let running: String

    func json(busy: Bool) -> [String: Any] {
        ["supported": supported, "mode": "appProcess", "running": running,
         "keepRunning": "unavailable", "busy": busy]
    }
    var settled: Bool { running == "running" || running == "stopped" }
}

struct MacLocalReadmission {
    let health: AppAPIHealth?
    var json: [String: Any] {
        let state = health?.state ?? "unreachable"
        return ["state": ["ready", "unreachable", "incompatible"].contains(state) ? state : "unknown",
                "incompatible": state == "incompatible" ? [
                    "side": health!.incompatibleSide, "host_contract": health!.contract as Any? ?? NSNull(),
                    "client_contract": 1, "min_daemon": 1] as [String: Any] : NSNull()]
    }
}

/// The app's existing child lifecycle supplies effects; no service/credential owner here.
@MainActor
protocol MacLocalControlBackend: AnyObject {
    var localObservation: MacLocalObservation { get }
    var localMutationAvailable: Bool { get }
    var localUpdateObservation: String { get }
    func reconcileLocalService() async -> Bool
    func confirmLocalRestart() async -> Bool
    func performLocalCommand(_ command: String) async throws -> MacLocalReadmission?
}

/// App-lifetime custody, deliberately separate from Connection's request ledger.
/// A lost document cannot cancel an effect or release an unresolved owner.
@MainActor
final class MacLocalControls {
    static let commands: Set<String> = ["local.status", "local.update.status", "local.start", "local.stop",
        "local.restart", "local.keepRunning.set", "local.repair", "local.turnOff"]
    private weak var backend: (any MacLocalControlBackend)?
    private var owner: UUID?
    private var executing = false
    private var reconciliation: Task<Bool, Never>?
    private var readers = 0
    var busy: Bool { owner != nil }

    init(backend: any MacLocalControlBackend) { self.backend = backend }

    static func error(_ code: String, effect: String = "none", recovery: String = "nativeRecovery") -> String {
        encode(["code": code, "effect": effect, "recovery": recovery])
    }
    private static func encode(_ object: [String: Any]) -> String {
        String(decoding: (try? JSONSerialization.data(withJSONObject: object, options: .sortedKeys)) ?? Data(), as: UTF8.self)
    }
    private static func valid(_ command: String, _ raw: String) -> Bool {
        guard commands.contains(command), let object = try? ConnectionJSON.parse(raw, limit: 4096).object else { return false }
        if command == "local.keepRunning.set" {
            return Set(object.keys) == ["schema", "enabled"] && object["schema"] == .integer(2)
                && (object["enabled"] == .bool(true) || object["enabled"] == .bool(false))
        }
        return object.isEmpty
    }

    /// Called synchronously from the admitted Main dispatcher: reserve BEFORE
    /// scheduling work or opening native consent, including nested-run-loop calls.
    func call(command: String, data: String, current: @escaping @MainActor () -> Bool,
              gate: @escaping @MainActor () -> String?,
              reply: @escaping @MainActor @Sendable (Any?, String?) -> Void) {
        guard Self.valid(command, data) else {
            reply(nil, Self.error("invalidRequest", recovery: "none")); return
        }
        guard current(), let backend else { reply(nil, Self.error("unavailable")); return }
        if command == "local.status" {
            let observed = backend.localObservation
            // Only an explicit fresh status after settlement reconciles a failed
            // or lost outcome. Reads during execution never release the slot.
            if let token = owner, !executing {
                guard readers < 8 else { reply(nil, Self.error("busy", recovery: "refresh")); return }
                readers += 1
                if reconciliation == nil { reconciliation = Task { @MainActor in await backend.reconcileLocalService() } }
                let observation = reconciliation!
                Task { @MainActor in
                    defer { readers -= 1 }
                    let reconciled = await observation.value
                    if owner == token { reconciliation = nil }
                    if reconciled, !executing, owner == token { owner = nil }
                    guard current() else { reply(nil, Self.error("documentChanged", recovery: "refresh")); return }
                    reply(Self.encode(["schema": 2, "service": backend.localObservation.json(busy: busy)]), nil)
                }
                return
            }
            reply(Self.encode(["schema": 2, "service": observed.json(busy: busy)]), nil); return
        }
        if command == "local.update.status" {
            reply(Self.encode(["schema": 2, "update": backend.localUpdateObservation]), nil); return
        }
        guard owner == nil else { reply(nil, Self.error("busy", recovery: "refresh")); return }
        guard ["local.start", "local.stop", "local.restart"].contains(command),
              backend.localObservation.supported, backend.localMutationAvailable else {
            reply(nil, Self.error("unavailable")); return
        }
        if let reason = gate() { reply(nil, Self.error(reason, recovery: "refresh")); return }
        let token = UUID()
        owner = token; executing = true
        Task { @MainActor in
            var began = false
            defer { executing = false }
            do {
                if command == "local.restart", !(await backend.confirmLocalRestart()) {
                    owner = nil
                    reply(nil, Self.error("permissionDenied", recovery: "nativePermission")); return
                }
                guard current() else {
                    owner = nil; reply(nil, Self.error("documentChanged", recovery: "refresh")); return
                }
                guard backend.localObservation.supported, backend.localMutationAvailable else {
                    owner = nil; reply(nil, Self.error("unavailable")); return
                }
                if let reason = gate() {
                    owner = nil; reply(nil, Self.error(reason, recovery: "refresh")); return
                }
                began = true
                let readmission = try await backend.performLocalCommand(command)
                let observed = backend.localObservation
                guard current(), owner == token, observed.supported, observed.settled,
                      (command == "local.stop" || readmission != nil) else { throw AppAPIError.unavailable }
                owner = nil
                var value: [String: Any] = ["schema": 2, "command": command, "service": observed.json(busy: false)]
                if let readmission { value["readmission"] = readmission.json }
                reply(Self.encode(value), nil)
            } catch {
                // Retain owner through failures, cancellation and lost replies.
                if !began { owner = nil }
                reply(nil, Self.error(began ? "outcomeUnknown" : "unavailable",
                                      effect: began ? "unknown" : "none"))
            }
        }
    }
}
