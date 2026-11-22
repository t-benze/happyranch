import Foundation
import HappyRanchSupervisor
import CryptoKit
import Darwin

enum ConnectionRefusal: Error { case invalid }

/// Parse before Foundation can discard duplicate keys. The wire accepts only
/// integer numbers, valid Unicode strings, and at most sixteen nested levels.
indirect enum ConnectionJSON: Equatable {
    case object([String: ConnectionJSON]), array([ConnectionJSON]), string(String)
    case integer(Int64), bool(Bool), null

    var object: [String: ConnectionJSON]? { if case .object(let v) = self { return v }; return nil }
    var string: String? { if case .string(let v) = self { return v }; return nil }
    var integer: Int64? { if case .integer(let v) = self { return v }; return nil }
    var array: [ConnectionJSON]? { if case .array(let v) = self { return v }; return nil }

    static func parse(_ raw: String, limit: Int = 4096) throws -> ConnectionJSON {
        guard raw.utf8.count <= limit else { throw ConnectionRefusal.invalid }
        var parser = Parser(bytes: Array(raw.utf8))
        let value = try parser.value(depth: 0)
        parser.space()
        guard parser.index == parser.bytes.count else { throw ConnectionRefusal.invalid }
        return value
    }

    private struct Parser {
        let bytes: [UInt8]
        var index = 0
        mutating func space() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
        mutating func take(_ byte: UInt8) -> Bool {
            space()
            guard index < bytes.count, bytes[index] == byte else { return false }
            index += 1; return true
        }
        mutating func text() throws -> String {
            space()
            let start = index
            guard take(34) else { throw ConnectionRefusal.invalid }
            while index < bytes.count {
                let byte = bytes[index]; index += 1
                if byte == 34 {
                    return try JSONDecoder().decode(String.self, from: Data(bytes[start..<index]))
                }
                if byte == 92 { index += 1 }
            }
            throw ConnectionRefusal.invalid
        }
        mutating func value(depth: Int) throws -> ConnectionJSON {
            guard depth <= 16 else { throw ConnectionRefusal.invalid }
            space()
            guard index < bytes.count else { throw ConnectionRefusal.invalid }
            if bytes[index] == 34 { return .string(try text()) }
            if take(123) {
                var result: [String: ConnectionJSON] = [:]
                if take(125) { return .object(result) }
                repeat {
                    let key = try text()
                    guard result[key] == nil, take(58) else { throw ConnectionRefusal.invalid }
                    result[key] = try value(depth: depth + 1)
                    if take(125) { return .object(result) }
                } while take(44)
                throw ConnectionRefusal.invalid
            }
            if take(91) {
                var result: [ConnectionJSON] = []
                if take(93) { return .array(result) }
                repeat {
                    result.append(try value(depth: depth + 1))
                    if take(93) { return .array(result) }
                } while take(44)
                throw ConnectionRefusal.invalid
            }
            for (literal, result) in [("true", ConnectionJSON.bool(true)), ("false", .bool(false)), ("null", .null)] {
                let token = Array(literal.utf8)
                if bytes[index...].starts(with: token) { index += token.count; return result }
            }
            let start = index
            if bytes[index] == 45 { index += 1 }
            guard index < bytes.count, (48...57).contains(bytes[index]) else { throw ConnectionRefusal.invalid }
            if bytes[index] == 48 { index += 1 }
            else { while index < bytes.count && (48...57).contains(bytes[index]) { index += 1 } }
            guard let number = Int64(String(decoding: bytes[start..<index], as: UTF8.self)) else { throw ConnectionRefusal.invalid }
            return .integer(number)
        }
    }
}

struct ConnectionRequest {
    static let commands: Set<String> = [
        "bridge.hello", "host.status", "host.enable", "host.turnOff", "host.createInvite",
        "host.approveDevice", "host.rejectDevice", "host.nextDevice", "host.revokeDevice",
        "client.status", "client.acceptInvite", "client.savedComputers", "client.connectSaved",
        "client.removeSaved", "client.disconnect", "client.cancelAttempt", "client.retry",
        "client.retrySave", "client.health"
    ]
    let id: String
    let command: String
    let args: [String: ConnectionJSON]
    var isMutation: Bool {
        command != "bridge.hello" && !["client.status", "client.savedComputers", "client.health", "host.status"].contains(command)
    }
    func hasSameBody(as other: Self) -> Bool { command == other.command && args == other.args }

    init(_ raw: String) throws {
        guard let value = try ConnectionJSON.parse(raw).object,
              Set(value.keys) == ["protocol", "version", "requestId", "command", "args"],
              value["protocol"] == .string("happyranch.connection"), value["version"] == .integer(1),
              let id = value["requestId"]?.string, Self.identifier(id),
              let command = value["command"]?.string, Self.commands.contains(command),
              let args = value["args"]?.object else { throw ConnectionRefusal.invalid }
        switch command {
        case "bridge.hello":
            guard args == ["minVersion": .integer(1), "maxVersion": .integer(1)] else { throw ConnectionRefusal.invalid }
        case "client.connectSaved", "client.removeSaved":
            guard Set(args.keys) == ["computerRef"], let ref = args["computerRef"]?.string,
                  Self.identifier(ref) else { throw ConnectionRefusal.invalid }
        default:
            guard args.isEmpty else { throw ConnectionRefusal.invalid }
        }
        self.id = id; self.command = command; self.args = args
    }

    static func identifier(_ value: String) -> Bool {
        (1...64).contains(value.utf8.count) && value.utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 95 || $0 == 45
        }
    }

    static func reply(id: String, result: [String: Any]? = nil, error: ConnectionError = .init(.unavailable)) -> String {
        var value: [String: Any] = ["protocol": "happyranch.connection", "version": 1, "requestId": id, "ok": result != nil]
        if let result { value["result"] = result }
        else { value["error"] = error.fields }
        guard let bytes = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              bytes.count <= 32768 else {
            return reply(id: id, error: .init(.invalidRequest))
        }
        return String(decoding: bytes, as: UTF8.self)
    }
}

/// Only the closed wire vocabulary can cross this boundary. Native errors and
/// originating actions remain native; health reads never replace that action.
struct ConnectionError: Error, Equatable {
    enum Code: String {
        case invalidRequest, unsupportedVersion, unsupportedCommand, notLocalShell, documentChanged, busy
        case permissionDenied, stateChanged, expired, unavailable, outcomeUnknown, invalidInvite, newInviteRequired
        case networkUnavailable, trustMismatch, identityChanged, runtimeUnavailable, accessDenied, revokedConfirmed
        case keychainLocked, keychainDenied, saveFailed, removalIncomplete
    }
    enum Effect: String { case none, confirmed, unknown }
    enum Recovery: String { case refresh, explicitRetry, newInviteOnHost, nativePermission, nativeRecovery, operatorRecovery, none }
    let code: Code
    let effect: Effect
    let recovery: Recovery
    init(_ code: Code, effect: Effect = .none, recovery: Recovery = .refresh) {
        self.code = code; self.effect = effect; self.recovery = recovery
    }
    var fields: [String: String] { ["code": code.rawValue, "effect": effect.rawValue, "recovery": recovery.rawValue] }
}

enum ConnectionOutcome {
    case result([String: Any]), failure(ConnectionError)
    func reply(id: String) -> String {
        switch self {
        case .result(let value): return ConnectionRequest.reply(id: id, result: value)
        case .failure(let error): return ConnectionRequest.reply(id: id, error: error)
        }
    }
}

/// Per-document, non-evicting mutation journal. The transport owns its lifetime;
/// sheet code can reserve one prompt, revalidate, then mark actual dispatch.
struct ConnectionMutationLedger {
    enum Admission { case start, replay(ConnectionOutcome) }
    private struct Entry {
        let request: ConnectionRequest
        var dispatched = false
        var settled = false
        var outcome: ConnectionOutcome?
        var current: ConnectionOutcome {
            outcome ?? .failure(.init(dispatched ? .outcomeUnknown : .busy,
                                     effect: dispatched ? .unknown : .none))
        }
    }
    private var entries: [String: Entry] = [:]
    private var cancellationReplays: [String: Entry] = [:]
    private var cancellation: (attempt: UUID, entry: Entry)?
    private(set) var mutationID: String?
    private(set) var promptID: String?
    private(set) var originatingAction: String?
    var count: Int { entries.count + cancellationReplays.count }

    func recorded(_ request: ConnectionRequest) -> ConnectionOutcome? {
        if let previous = entries[request.id] ?? cancellationReplays[request.id]
            ?? (cancellation?.entry.request.id == request.id ? cancellation?.entry : nil) {
            return previous.request.hasSameBody(as: request) ? previous.current : .failure(.init(.invalidRequest))
        }
        return nil
    }
    mutating func begin(_ request: ConnectionRequest, cancelling attempt: UUID? = nil) -> Admission {
        if let previous = recorded(request) { return .replay(previous) }
        if request.command == "client.cancelAttempt" {
            if let cancellation, !cancellation.entry.settled {
                return .replay(attempt == nil || cancellation.attempt == attempt
                    ? cancellation.entry.current : .failure(.init(.stateChanged)))
            }
            guard let attempt else { return .replay(.failure(.init(.stateChanged))) }
            if let cancellation, cancellation.attempt == attempt {
                // Keep (including sheet expiry) settles this intent, not the
                // attempt. A later explicit Cancel needs its own confirmation.
                guard case .result(let result) = cancellation.entry.current,
                      result["effect"] as? String == "none" else { return .replay(cancellation.entry.current) }
            }
            if let cancellation {
                guard count < 64 else { return .replay(.failure(.init(.busy))) }
                // Archived replies share the non-evicting journal bound.
                cancellationReplays[cancellation.entry.request.id] = cancellation.entry
            }
            cancellation = (attempt, Entry(request: request))
            return .start // current cancellation has its own slot, even at journal capacity
        }
        guard request.isMutation else { return .replay(.failure(.init(.invalidRequest))) }
        guard mutationID == nil, promptID == nil, count < 64 else { return .replay(.failure(.init(.busy))) }
        entries[request.id] = Entry(request: request)
        mutationID = request.id; originatingAction = request.command
        return .start
    }

    func pendingCancellation() -> ConnectionOutcome? {
        guard let cancellation, !cancellation.entry.settled else { return nil }
        return cancellation.entry.current
    }
    mutating func reservePrompt(for id: String) -> Bool {
        guard promptID == nil, entries[id] != nil || cancellation?.entry.request.id == id else { return false }
        promptID = id; return true
    }
    mutating func releasePrompt(_ id: String) {
        if promptID == id { promptID = nil }
    }
    mutating func markDispatched(_ id: String) {
        if cancellation?.entry.request.id == id { cancellation?.entry.dispatched = true }
        else { entries[id]?.dispatched = true }
    }
    mutating func timedOut(_ id: String) {
        let outcome = ConnectionOutcome.failure(.init(.outcomeUnknown, effect: .unknown, recovery: .explicitRetry))
        if cancellation?.entry.request.id == id { cancellation?.entry.outcome = outcome }
        else { entries[id]?.outcome = outcome }
        // Keep mutation/prompt ownership until native settlement, even if the
        // transport deadline expires while a Keychain operation is still open.
    }
    mutating func finish(_ id: String, outcome: ConnectionOutcome) {
        if cancellation?.entry.request.id == id {
            cancellation?.entry.outcome = outcome; cancellation?.entry.settled = true
        } else {
            entries[id]?.outcome = outcome; entries[id]?.settled = true
        }
        if mutationID == id { mutationID = nil }
        if promptID == id { promptID = nil }
    }
}

/// A literal bundle inventory, re-read using descriptor-relative no-follow walks
/// and verified again at delivery. Neither caller URLs nor preferences pick root.
struct ConnectionAssets {
    static let origin = "happyranch-shell://connection"
    static let entry = origin + "/index.html"
    static let csp = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    let root: URL
    let resources: [String: Record]
    struct Record { let bytes: Int; let digest: String }

    init(root: URL) throws {
        self.root = root
        let raw = try Self.read(root: root, path: "resource-manifest.json", limit: 1024 * 1024)
        guard let text = String(data: raw, encoding: .utf8),
              let manifest = try ConnectionJSON.parse(text, limit: 1024 * 1024).object,
              Set(manifest.keys) == ["version", "entry", "resources"],
              manifest["version"] == .integer(1), manifest["entry"] == .string("index.html"),
              let rows = manifest["resources"]?.array, (1...256).contains(rows.count) else { throw ConnectionRefusal.invalid }
        var records: [String: Record] = [:]
        for row in rows {
            guard let item = row.object, Set(item.keys) == ["path", "bytes", "sha256"],
                  let path = item["path"]?.string, Self.literal(path), path != "resource-manifest.json",
                  records[path] == nil, let size = item["bytes"]?.integer, (0...16777216).contains(size),
                  let digest = item["sha256"]?.string, digest.utf8.count == 64,
                  digest.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { throw ConnectionRefusal.invalid }
            records[path] = Record(bytes: Int(size), digest: digest)
        }
        guard records["index.html"] != nil, records.values.reduce(0, { $0 + $1.bytes }) <= 33554432 else { throw ConnectionRefusal.invalid }
        self.resources = records
        var actual: Set<String> = []
        var enumerationFailed = false
        guard let iterator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey],
            options: [], errorHandler: { _, _ in enumerationFailed = true; return false }) else { throw ConnectionRefusal.invalid }
        var allowedDirectories: Set<String> = []
        for path in records.keys {
            var parts = path.split(separator: "/").map(String.init)
            parts.removeLast()
            while !parts.isEmpty { allowedDirectories.insert(parts.joined(separator: "/")); parts.removeLast() }
        }
        for case let url as URL in iterator {
            let flags = try url.resourceValues(forKeys: [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey])
            let relative = url.pathComponents.dropFirst(root.pathComponents.count).joined(separator: "/")
            guard flags.isSymbolicLink == false else { throw ConnectionRefusal.invalid }
            if flags.isDirectory == true {
                guard allowedDirectories.contains(relative) else { throw ConnectionRefusal.invalid }
            } else {
                guard flags.isRegularFile == true,
                      relative == "resource-manifest.json" || records[relative] != nil else { throw ConnectionRefusal.invalid }
                actual.insert(relative)
            }
        }
        guard !enumerationFailed, actual == Set(records.keys).union(["resource-manifest.json"]) else { throw ConnectionRefusal.invalid }
        for path in records.keys { _ = try asset(path) }
    }

    static func literal(_ path: String) -> Bool {
        !path.isEmpty && path.utf8.count <= 256 && path.utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || [95, 46, 47, 45].contains($0)
        } && path.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }

    func resolve(_ url: URL) throws -> (Data, String) {
        let raw = url.absoluteString
        guard raw.hasPrefix(Self.origin + "/") else { throw ConnectionRefusal.invalid }
        let path = String(raw.dropFirst(Self.origin.count + 1))
        guard Self.literal(path), raw == Self.origin + "/" + path else { throw ConnectionRefusal.invalid }
        let mime: String
        switch (path as NSString).pathExtension {
        case "html": mime = "text/html; charset=utf-8"
        case "js": mime = "text/javascript; charset=utf-8"
        case "css": mime = "text/css; charset=utf-8"
        case "woff2": mime = "font/woff2"
        case "woff": mime = "font/woff"
        case "ttf": mime = "font/ttf"
        case "txt": mime = "text/plain; charset=utf-8"
        default: mime = "application/octet-stream"
        }
        return (try asset(path), mime)
    }

    func asset(_ path: String) throws -> Data {
        guard let record = resources[path] else { throw ConnectionRefusal.invalid }
        let bytes = try Self.read(root: root, path: path, limit: record.bytes)
        guard bytes.count == record.bytes,
              SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == record.digest else { throw ConnectionRefusal.invalid }
        return bytes
    }

    private static func read(root: URL, path: String, limit: Int) throws -> Data {
        guard root.isFileURL, root.path.hasPrefix("/"), literal(path) else { throw ConnectionRefusal.invalid }
        let parts = root.path.split(separator: "/").map(String.init) + path.split(separator: "/").map(String.init)
        var descriptor = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw ConnectionRefusal.invalid }
        defer { close(descriptor) }
        for (index, part) in parts.enumerated() {
            let flags = O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | (index == parts.count - 1 ? 0 : O_DIRECTORY)
            let next = openat(descriptor, part, flags)
            guard next >= 0 else { throw ConnectionRefusal.invalid }
            close(descriptor); descriptor = next
        }
        var before = stat(), after = stat()
        guard fstat(descriptor, &before) == 0, (before.st_mode & S_IFMT) == S_IFREG,
              before.st_size >= 0, before.st_size <= limit else { throw ConnectionRefusal.invalid }
        var data = Data(), buffer = [UInt8](repeating: 0, count: 16384)
        while data.count <= limit {
            let count = Darwin.read(descriptor, &buffer, min(buffer.count, limit + 1 - data.count))
            guard count >= 0 else { throw ConnectionRefusal.invalid }
            if count == 0 { break }
            data.append(contentsOf: buffer.prefix(count))
        }
        guard fstat(descriptor, &after) == 0, data.count == before.st_size, data.count <= limit,
              before.st_ino == after.st_ino, before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec, before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
              before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec, before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec else { throw ConnectionRefusal.invalid }
        return data
    }
}

/// Opaque references live only in this native view. Labels contain no endpoint,
/// home ID, binding, fingerprint or credential. Ordering uses native identity.
@MainActor
final class ConnectionComputerCatalog {
    private struct Record { let home: HomeInviteModel.SavedHome; let ref: String }
    private var records: [UUID: Record] = [:]
    private var activeRefs: [UUID: String] = [:]
    func update(_ homes: [HomeInviteModel.SavedHome]) {
        let ids = Set(homes.map(\.id))
        records = records.filter { ids.contains($0.key) }
        for home in homes {
            if records[home.id]?.home != home {
                records[home.id] = Record(home: home, ref: "c" + UUID().uuidString.replacingOccurrences(of: "-", with: ""))
            }
        }
    }
    func ref(for id: UUID) -> String {
        if let record = records[id] { return record.ref }
        if let ref = activeRefs[id] { return ref }
        let ref = "c" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
        // One active identity, not an unbounded history of unsaved attempts.
        activeRefs = [id: ref]
        return ref
    }
    func resolve(_ ref: String, in model: HomeInviteModel) -> UUID? {
        guard let record = records.values.first(where: { $0.ref == ref }),
              model.savedHomes.contains(record.home) else { return nil }
        return record.home.id
    }
    func computers(known: Bool) -> Any {
        guard known, records.count <= 32 else { return NSNull() }
        return records.keys.sorted { $0.uuidString < $1.uuidString }.enumerated().map { index, id in
            ["computerRef": records[id]!.ref, "label": Locale.preferredLanguages.first?.hasPrefix("zh") == true
                ? "已保存的电脑 \(index + 1)" : "Saved computer \(index + 1)"]
        }
    }
}

@MainActor
enum ConnectionClientProjection {
    static let backedCommands = ["client.status", "client.savedComputers", "client.health", "client.retrySave",
        "client.acceptInvite", "client.connectSaved", "client.removeSaved", "client.disconnect", "client.cancelAttempt", "client.retry"]
    nonisolated static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
    static func health(_ model: HomeInviteModel, now: Date) -> [String: Any] {
        guard let sample = model.accessObservation, sample.lifetime == model.lifetime else {
            return ["access": "unknown", "runtime": "unknown", "route": "unknown", "observedAt": NSNull(), "observation": "unavailable"]
        }
        let fresh = (0...10).contains(now.timeIntervalSince(sample.observedAt))
        return ["access": fresh ? sample.access.rawValue : "unknown",
                "runtime": fresh && sample.access == .allowed && model.phase == .connected ? "available" : "unknown",
                "route": "unknown", "observedAt": timestamp(sample.observedAt),
                "observation": fresh ? (sample.access == .unknown ? "unavailable" : "known") : "stale"]
    }
    static func savedAccess(_ model: HomeInviteModel) -> String {
        if !model.removalFailures.isEmpty { return "removalIncomplete" }
        if case .keychain(let failure) = model.phase {
            if [.locked, .denied, .cancelled, .identityChanged].contains(failure) { return "needsPermission" }
            return "unknown"
        }
        if let failure = model.saveFailure {
            if [.locked, .denied, .cancelled, .identityChanged].contains(failure) { return "needsPermission" }
            if failure == .unavailable || failure == .deletionFailed { return "unknown" }
            return "notSaved"
        }
        if model.bridgeHasUnsavedAccess { return "notSaved" }
        if !model.savedHomes.isEmpty { return "saved" }
        return model.discoveryComplete ? "none" : "unknown"
    }
    static func savedComputers(_ model: HomeInviteModel, catalog: ConnectionComputerCatalog) -> [String: Any] {
        catalog.update(model.savedHomes)
        return ["computers": catalog.computers(known: model.discoveryComplete), "savedAccess": savedAccess(model),
                "observation": model.discoveryComplete ? (model.discoveryFailures.isEmpty ? "known" : "stale") : "unavailable"]
    }
    static func snapshot(model: HomeInviteModel, catalog: ConnectionComputerCatalog) -> [String: Any] {
        let saved = savedComputers(model, catalog: catalog)
        let stage: String
        switch model.phase {
        case .idle, .checkingSavedAccess: stage = "idle"
        case .validating: stage = "validating"
        case .joining: stage = "joining"
        case .pairing: stage = "pairing"
        case .awaitingApproval: stage = "waitingApproval"
        case .saving: stage = "saving"
        case .opening: stage = "opening"
        case .connected, .runtimeBusy: stage = "connected"
        case .stopping: stage = "stopping"
        case .failed, .keychain: stage = "failed"
        }
        let health = health(model, now: Date())
        return ["stage": stage, "runtime": health["runtime"]!, "savedAccess": saved["savedAccess"]!, "route": "unknown",
                "observedAt": health["observedAt"]!, "observation": saved["observation"]!, "computers": saved["computers"]!,
                "activeComputerRef": model.bridgeActiveComputerID.map { catalog.ref(for: $0) } as Any? ?? NSNull(),
                "expiresAt": model.expiresAt.map(timestamp) as Any? ?? NSNull(),
                "comparisonPhrase": model.comparisonPhrase.map(displayText) as Any? ?? NSNull()]
    }
    nonisolated static func displayText(_ value: String) -> String {
        let scalars = value.unicodeScalars.filter {
            !CharacterSet.controlCharacters.contains($0) && !(0x7f...0x9f).contains($0.value)
                && !(0x202a...0x202e).contains($0.value) && !(0x2066...0x2069).contains($0.value)
        }
        var result = ""
        for scalar in scalars {
            let next = String(scalar)
            if result.utf8.count + next.utf8.count > 253 { return result + "…" }
            result += next
        }
        return result
    }
    static func snapshot(app: AppDelegate, catalog: ConnectionComputerCatalog? = nil) -> [String: Any] {
        if app.connectionRole == .client && app.clientLane == .managed {
            var value = snapshot(model: app.homeInvite, catalog: catalog ?? ConnectionComputerCatalog())
            if app.homeInvite.phase == .idle {
                switch app.managedJoin.phase {
                case .joining: value["stage"] = "joining"
                case .joined, .pairing: value["stage"] = "pairing"
                case .paired, .online, .reconnecting, .unreachable: value["stage"] = "opening"
                case .canceling: value["stage"] = "stopping"
                case .interrupted, .accessDenied, .revoked, .error: value["stage"] = "failed"
                default: break
                }
            }
            return value
        }
        var stage = "idle"
        if app.connectionRole == .client {
            if app.isConnecting { stage = "pairing" }
            else if app.connectionState == .online || app.connectionState == .reconnecting { stage = "opening" }
            else if app.connectionState == .pairedUnreachable { stage = "failed" }
        }
        return ["stage": stage, "runtime": "unknown", "savedAccess": "unknown", "route": "unknown",
                "observedAt": NSNull(), "observation": "unavailable", "computers": NSNull(),
                "activeComputerRef": NSNull(), "expiresAt": NSNull(), "comparisonPhrase": NSNull()]
    }
    static func hello(app: AppDelegate, catalog: ConnectionComputerCatalog) -> [String: Any] {
        let snapshot = snapshot(app: app, catalog: catalog)
        let commands = app.connectionRole == .client && app.clientLane == .managed ? backedCommands : ["client.status"]
        return ["version": 1, "platform": "mac", "roles": ["client"],
                "capabilities": commands.map { ["command": $0, "availability": "available"] },
                "observationLimits": ["host": [], "client": observationLimits(snapshot)]]
    }
    static func observationLimits(_ snapshot: [String: Any]) -> [String] {
        var limits = ["route"]
        for field in ["observedAt", "computers", "activeComputerRef", "expiresAt", "comparisonPhrase"] {
            if snapshot[field] is NSNull { limits.append(field) }
        }
        if snapshot["savedAccess"] as? String == "unknown" { limits.append("savedAccess") }
        return limits
    }
    static func saveError(_ failure: SavedAccessFailure) -> ConnectionError {
        switch failure {
        case .locked: return .init(.keychainLocked, recovery: .nativePermission)
        case .denied, .cancelled: return .init(.keychainDenied, recovery: .nativePermission)
        case .identityChanged: return .init(.identityChanged, effect: .unknown, recovery: .nativeRecovery)
        case .deletionFailed: return .init(.removalIncomplete, effect: .unknown, recovery: .nativeRecovery)
        case .reapprovalRequired: return .init(.newInviteRequired, recovery: .newInviteOnHost)
        default: return .init(.saveFailed, effect: .unknown, recovery: .explicitRetry)
        }
    }
    static func savedAction(_ model: HomeInviteModel, catalog: ConnectionComputerCatalog,
                            effect: String = "confirmed", localCleanup: String = "notApplicable") -> [String: Any] {
        ["effect": effect, "hostDenial": "notApplicable", "hostCleanup": "notApplicable",
         "localCleanup": localCleanup, "networkCleanup": "notApplicable", "inviteState": "unknown",
         "expiresAt": NSNull(), "host": NSNull(), "client": snapshot(model: model, catalog: catalog)]
    }
}
