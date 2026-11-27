import Foundation
import Darwin
import HappyRanchSupervisor

/// Only /dev/tty receives target/phrase, never stdout/stderr.
/// Noncanonical input has independent byte and monotonic 120-second bounds.
final class ConnectionTerminal: @unchecked Sendable {
    private let descriptor: Int32
    private let readTimeout: Duration
    convenience init() throws {
        try self.init(descriptor: Darwin.open("/dev/tty", O_RDWR | O_CLOEXEC | O_NONBLOCK))
    }
    /// Takes ownership, including on validation failure. Tests supply a PTY duplicate.
    init(descriptor: Int32, readTimeout: Duration = .seconds(120)) throws {
        guard descriptor >= 0 else { throw ConnectionError(.unavailable) }
        let flags = fcntl(descriptor, F_GETFL)
        guard descriptor < Int32(FD_SETSIZE), isatty(descriptor) == 1, flags >= 0,
              fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
            Darwin.close(descriptor)
            throw ConnectionError(.unavailable)
        }
        self.descriptor = descriptor
        self.readTimeout = readTimeout
    }
    deinit { Darwin.close(descriptor) }
    func say(_ text: String) throws {
        let bytes = Array((text + "\n").utf8)
        let count = bytes.withUnsafeBytes { Darwin.write(descriptor, $0.baseAddress, $0.count) }
        guard count == bytes.count else { throw ConnectionError(.unavailable) }
    }
    static func plain(_ text: String) -> String {
        text.unicodeScalars.filter { (32...126).contains($0.value) }.prefix(256).map { String($0) }.joined()
    }
    func read(_ prompt: String, hidden: Bool = false, limit: Int = 32) async throws -> String {
        let worker = Task.detached { [self] in
            var original = termios()
            guard tcgetattr(descriptor, &original) == 0 else { throw ConnectionError(.unavailable) }
            var mode = original
            mode.c_lflag &= ~tcflag_t(ICANON)
            if hidden { mode.c_lflag &= ~tcflag_t(ECHO | ECHONL) }
            guard tcsetattr(descriptor, TCSAFLUSH, &mode) == 0 else { throw ConnectionError(.unavailable) }
            defer { _ = tcsetattr(descriptor, TCSAFLUSH, &original) }
            try say(prompt) // readiness is published only after echo is disabled
            let deadline = ContinuousClock.now.advanced(by: readTimeout)
            var bytes = [UInt8]()
            defer { _ = bytes.withUnsafeMutableBytes { $0.initializeMemory(as: UInt8.self, repeating: 0) } }
            while bytes.count <= limit {
                try Task.checkCancellation()
                guard ContinuousClock.now < deadline else { throw CancellationError() }
                // Darwin poll does not support tty devices. Swift cannot import
                // FD_SET's C macro; set the bit in Darwin's Int32 fd_set words.
                // Initialization rejects descriptors outside FD_SETSIZE.
                var input = fd_set()
                withUnsafeMutableBytes(of: &input.fds_bits) { storage in
                    let words = storage.bindMemory(to: Int32.self)
                    words[Int(descriptor) / 32] |= Int32(bitPattern: UInt32(1) << UInt32(descriptor % 32))
                }
                var timeout = timeval(tv_sec: 0, tv_usec: 100_000)
                let ready = Darwin.select(descriptor + 1, &input, nil, nil, &timeout)
                if ready < 0, errno == EINTR { continue }
                guard ready >= 0 else { throw ConnectionError(.unavailable) }
                if ready == 0 { continue }
                var byte: UInt8 = 0
                let count = Darwin.read(descriptor, &byte, 1)
                if count < 0, errno == EINTR || errno == EAGAIN { continue }
                guard count == 1, byte != 4 else { throw CancellationError() }
                if byte == 10 || byte == 13 {
                    guard let result = String(bytes: bytes, encoding: .utf8) else { throw ConnectionError(.invalidInvite) }
                    return result
                }
                if byte == 127 || byte == 8 { if !bytes.isEmpty { bytes.removeLast() } }
                else { bytes.append(byte) }
            }
            throw ConnectionError(hidden ? .invalidInvite : .unavailable)
        }
        return try await withTaskCancellationHandler(operation: { try await worker.value }, onCancel: { worker.cancel() })
    }
}

/// Foreground consumer of the SAME native services as HomeInviteModel. These
/// normal protocol dependencies keep custody and transport with their owners.
@MainActor
final class ConnectionCLI {
    static let actions = ["status", "accept-invite", "saved-computers", "connect-saved", "remove-saved",
                          "disconnect", "cancel-attempt", "retry", "retry-save", "health"]
    private let store: any SavedHomeCredentialStore
    private let makeSession: @Sendable () -> any HomeInviteSession
    private var session: (any HomeInviteSession)?
    private var terminal: ConnectionTerminal?
    private var savedAccess = "none"
    private var saveCode: String?
    private var removalDispatched = false
    private var removalConfirmed = false
    private var revoked: SavedDeviceCredential?

    init(store: any SavedHomeCredentialStore = LoginKeychainDeviceStore(),
         makeSession: @escaping @Sendable () -> any HomeInviteSession = {
             ManagedHomeInviteSession(controller: RealProcessController(), executable:
                 Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/happyranch-home-bridge"))
         }) {
        self.store = store
        self.makeSession = makeSession
    }

    static func write(_ result: [String: Any]) throws {
        // All callers supply closed fields; no native Error description, trust,
        // credential, target, health diagnostic or URL is serialized here.
        let bytes = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        try FileHandle.standardOutput.write(contentsOf: bytes + Data([10]))
    }

    func execute(_ action: String) async -> Int32 {
        var result: [String: Any]
        do { result = try await run(action) }
        catch is CancellationError {
            result = ["state": "cancelled", "effect": session == nil ? "none" : "confirmed"]
        } catch {
            result = Self.failure(error, effect: session == nil ? .none : .unknown)
        }
        // Unstructured cleanup has an independent cancellation lifetime. A
        // second signal cannot interrupt custody settlement or teardown.
        let clean = await Task { await self.cleanup() }.value
        if removalDispatched {
            result = removalConfirmed ? ["state": "removed", "effect": "confirmed"] :
                ["code": "removalIncomplete", "state": "unknown", "effect": "unknown"]
        } else if !clean {
            result = ["code": "cleanupIncomplete", "state": "unknown", "effect": "unknown"]
        }
        if session != nil { result["savedAccess"] = savedAccess }
        do { try Self.write(result) }
        catch { return 1 } // cleanup already settled; never print a raw error
        return result["code"] == nil ? 0 : 1
    }

    func run(_ action: String) async throws -> [String: Any] {
        try Task.checkCancellation()
        if action == "status" { return ["state": "notOwned", "effect": "none", "observation": "unavailable"] }
        if ["disconnect", "cancel-attempt", "retry", "retry-save", "health"].contains(action) {
            return ["code": "unavailable", "state": "notOwned", "effect": "none", "recovery": "owningProcess"]
        }
        guard Self.actions.contains(action) else { throw ConnectionError(.invalidRequest) }
        if action == "saved-computers" { return try Self.computers(await store.savedHomes()) }
        let terminal = try ConnectionTerminal()
        self.terminal = terminal // missing terminal refuses before storage/network
        let credential: SavedDeviceCredential
        if action == "accept-invite" {
            var raw = try await terminal.read("Paste invite (hidden):", hidden: true, limit: 5500)
            var invite: RemoteInvite? = try RemoteInvite.parse(raw)
            raw.removeAll()
            let trust = invite!.trust
            try show(trust)
            guard try await terminal.read("Type connect to consent (default: cancel):") == "connect" else {
                return ["state": "cancelled", "effect": "none"]
            }
            let save = try await terminal.read("Save in login Keychain? Type save (default: session-only):") == "save"
            try Task.checkCancellation()
            guard Date() < invite!.expiresAt else { throw HomeConnectionFailure.expiredInvite }
            let candidate = makeSession()
            session = candidate
            let id = UUID()
            let pairing = HomePairingInvitation(invite!)
            var key = invite!.withSecrets { _, key, _ in key }
            invite = nil
            defer { key.resetBytes(in: 0..<key.count) }
            credential = try await approve(candidate, trust: trust, id: id, key: key, pairing: pairing, terminal: terminal)
            key.resetBytes(in: 0..<key.count)
            try Task.checkCancellation()
            if save {
                savedAccess = "unknown"
                // Do not cancel a dispatched Security write/readback. The
                // foreground owner observes its actual result before teardown.
                let store = self.store
                let write = Task.detached { try await store.save(credential) }
                do { try await write.value; savedAccess = "saved" }
                catch {
                    let failure = Self.failure(error, effect: .none)
                    saveCode = failure["code"] as? String
                    savedAccess = ["keychainLocked", "keychainDenied", "identityChanged"].contains(saveCode ?? "") ? "needsPermission" : "notSaved"
                }
            }
            if savedAccess != "saved" {
                try terminal.say("Access not saved. This connection is session-only; reopening may need a new invite.")
            }
        } else {
            guard let selected = try await selectSaved(action) else { return ["state": "cancelled", "effect": "none"] }
            credential = selected
            try Task.checkCancellation()
            if action == "remove-saved" {
                removalDispatched = true
                try await remove(credential)
                removalConfirmed = true
                return ["state": "removed", "effect": "confirmed"]
            }
            savedAccess = "saved"
            let candidate = makeSession()
            session = candidate
            try await candidate.join(trust: credential.trust, profileID: credential.networkProfileID!, key: nil)
        }
        try Task.checkCancellation()
        guard let session else { throw ConnectionError(.unavailable) }
        do { try await session.checkAccess(credential) } // reachability only; no runtime listener
        catch {
            if error as? HomeConnectionFailure == .revoked { revoked = credential }
            throw error
        }
        try Task.checkCancellation()
        try terminal.say(NativeCopy.runtimeReachable.text)
        try terminal.say(NativeCopy.observingAccess.text)
        try Self.write(["state": "runtimeReachable", "effect": "confirmed", "savedAccess": savedAccess,
                    "code": saveCode as Any? ?? NSNull()])
        while true {
            try await Task.sleep(for: .seconds(5))
            do { try await session.checkAccess() }
            catch {
                if error as? HomeConnectionFailure == .revoked { revoked = credential }
                throw error
            }
        }
    }

    private func approve(_ candidate: any HomeInviteSession, trust: SavedHomeTrust, id: UUID,
                         key: Data, pairing: HomePairingInvitation, terminal: ConnectionTerminal) async throws -> SavedDeviceCredential {
        // One absolute invite deadline covers join AND pairing, including a
        // consent delay. Expiry cancels the existing native operation; it never
        // retries enrollment, replays a key or advances a late delivery.
        return try await withThrowingTaskGroup(of: SavedDeviceCredential.self) { group in
            group.addTask {
                try await candidate.join(trust: trust, profileID: id, key: key)
                try Task.checkCancellation()
                let pending = try await candidate.pair(pairing)
                guard pending.expiresAt <= pairing.expiresAt else { throw HomeConnectionFailure.authorizationIncomplete }
                try terminal.say("Compare on Host: " + ConnectionTerminal.plain(pending.comparisonPhrase))
                try terminal.say("Waiting for Host approval. Ctrl-C cancels this attempt.")
                let deadline = ContinuousClock.now.advanced(by: .seconds(max(0, pending.expiresAt.timeIntervalSinceNow)))
                while true {
                    try Task.checkCancellation()
                    guard Date() < pending.expiresAt, ContinuousClock.now < deadline else { throw HomeConnectionFailure.expiredInvite }
                    if let delivered = try await candidate.complete(pending, trust: trust, profileID: id) {
                        try Task.checkCancellation()
                        guard Date() < pending.expiresAt else { throw HomeConnectionFailure.expiredInvite }
                        return delivered
                    }
                    try await Task.sleep(for: .seconds(1))
                }
            }
            group.addTask {
                try await Task.sleep(for: .seconds(max(0, pairing.expiresAt.timeIntervalSinceNow)))
                throw HomeConnectionFailure.expiredInvite
            }
            defer { group.cancelAll() }
            guard let delivered = try await group.next() else { throw HomeConnectionFailure.authorizationIncomplete }
            return delivered
        }
    }

    private func show(_ trust: SavedHomeTrust) throws {
        for (name, value) in [("Target", trust.homeID), ("Control", trust.controlPlaneID),
                              ("Control URL", trust.controlURL), ("Dial IP", trust.dialIP), ("Host", trust.homeEndpoint)] {
            try terminal?.say(name + ": " + ConnectionTerminal.plain(value))
        }
    }

    private func selectSaved(_ action: String) async throws -> SavedDeviceCredential? {
        let discovery = try await store.savedHomes()
        let items = try Self.records(discovery)
        guard !items.isEmpty, let terminal else { throw ConnectionError(.unavailable) }
        for (index, record) in items.enumerated() {
            try terminal.say("\(index + 1): " + ConnectionTerminal.plain(record.trust.homeID))
        }
        let input = try await terminal.read("Select 1-\(items.count) (default: cancel):")
        guard input.utf8.allSatisfy({ (48...57).contains($0) }), let number = Int(input), (1...items.count).contains(number) else { return nil }
        let selected = items[number - 1]
        try show(selected.trust)
        let verb = action == "remove-saved" ? "remove" : "connect"
        guard try await terminal.read("Type \(verb) to confirm (default: cancel):") == verb else { return nil }
        try Task.checkCancellation()
        let current = try await store.load(binding: selected.binding, trust: selected.trust)
        guard current.binding == selected.binding, current.trust == selected.trust,
              current.networkProfileID == selected.networkProfileID,
              current.withBearer({ bytes in selected.withBearer { bytes == $0 } }) else { throw ConnectionError(.stateChanged) }
        return current
    }

    static func records(_ discovery: SavedHomeDiscovery) throws -> [SavedDeviceCredential] {
        let items = discovery.credentials
        guard items.count <= 32, items.allSatisfy({ $0.networkProfileID != nil }),
              Set(items.compactMap(\.networkProfileID)).count == items.count else { throw ConnectionError(.unavailable) }
        return items.sorted { $0.networkProfileID!.uuidString < $1.networkProfileID!.uuidString }
    }

    static func computers(_ discovery: SavedHomeDiscovery) throws -> [String: Any] {
        let items = try records(discovery)
        let computers = items.enumerated().map { index, _ in
            ["computerRef": "c" + UUID().uuidString.replacingOccurrences(of: "-", with: ""), "label": "Saved computer \(index + 1)"]
        }
        return ["computers": computers, "savedAccess": !items.isEmpty ? "saved" : discovery.failures.isEmpty ? "none" : "unknown",
                "observation": discovery.failures.isEmpty ? "known" : "stale"]
    }

    private func remove(_ credential: SavedDeviceCredential) async throws {
        guard let id = credential.networkProfileID else { throw ConnectionError(.removalIncomplete, effect: .unknown) }
        let store = self.store
        // Once native deletion starts, settle the complete captured-target
        // operation even after a signal. No next selection or automatic retry.
        try await Task.detached {
            try HomeNetworkState.remove(id)
            try await store.delete(binding: credential.binding)
        }.value
    }

    private func cleanup() async -> Bool {
        guard let session else { return true }
        var stopped: Bool?
        let stop = Task {
            do { try await session.stop(); stopped = true }
            catch { stopped = false }
        }
        let deadline = ContinuousClock.now.advanced(by: .seconds(8))
        while stopped == nil, ContinuousClock.now < deadline { try? await Task.sleep(for: .milliseconds(50)) }
        guard stopped == true else { stop.cancel(); return false }
        if let revoked {
            do { try await remove(revoked) }
            catch { return false }
        }
        return true
    }

    static func failure(_ error: Error, effect: ConnectionError.Effect) -> [String: Any] {
        if let closed = error as? ConnectionError { return closed.fields }
        if let storage = error as? SavedAccessFailure {
            return ConnectionClientProjection.saveError(storage).fields.merging(["effect": effect.rawValue]) { _, new in new }
        }
        let code: String
        if let invite = error as? RemoteInviteError {
            return ["code": invite == .time ? "expiredInvite" : invite == .reapprovalRequired ? "newInviteRequired" : "invalidInvite", "effect": effect.rawValue]
        }
        switch error as? HomeConnectionFailure {
        case .malformedInvite: code = "invalidInvite"
        case .expiredInvite: code = "expiredInvite"
        case .reapprovalRequired: code = "newInviteRequired"
        case .revoked: code = "revoked"
        case .daemonUnavailable: code = "daemonUnavailable"
        case .identityChanged, .identityUnavailable: code = "identityMismatch"
        case .controlUnreachable, .joinDenied: code = "joinUnavailable"
        case .homeUnavailable: code = "runtimeUnavailable"
        case .cleanupIncomplete: code = "cleanupIncomplete"
        default: code = "unavailable"
        }
        return ["code": code, "effect": effect.rawValue]
    }
}
