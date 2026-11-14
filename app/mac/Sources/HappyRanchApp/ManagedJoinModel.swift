import Foundation
import Combine
import Darwin
import HappyRanchSupervisor

/// Nonsecret UI state. Enrollment text lives only in the view's private draft.
@MainActor
final class ManagedJoinModel: ObservableObject {
    enum Phase: Equatable {
        case draft, checkingKeychain, waitingForKeychain, joining, joined, pairing, paired, online
        case canceling, interrupted, accessDenied, reconnecting, unreachable, revoked, error(String)

        var message: String {
            switch self {
            case .draft: return "Network not joined"
            case .checkingKeychain: return "Checking login Keychain; nothing sent"
            case .waitingForKeychain: return "Waiting for Keychain permission; nothing sent"
            case .joining: return "Joining managed network…"
            case .joined: return "Network joined. Pair with your home runtime."
            case .pairing: return "Pairing with home runtime…"
            case .paired: return "Paired. Checking authenticated runtime health…"
            case .online: return "Paired and online"
            case .canceling: return "Canceling; waiting for custody and network operations to settle…"
            case .interrupted: return "Enrollment may have completed; the key may be consumed. Check saved membership explicitly, or request a fresh key and pair again."
            case .accessDenied: return "Access denied or expired. This does not confirm device revocation. Request fresh pairing approval."
            case .reconnecting: return "Connection lost. Checking the paired runtime again…"
            case .unreachable: return "Unreachable. This does not confirm device revocation. Check the home runtime and network."
            case .revoked: return "Device marked revoked by you. Rejoin with a fresh enrollment key and fresh pairing approval."
            case .error(let message): return message
            }
        }

        func afterHealth(_ healthy: Bool, denied: Bool = false) -> Self {
            guard [.paired, .online, .reconnecting, .unreachable, .accessDenied].contains(self) else { return self }
            if denied { return .accessDenied }
            if healthy { return .online }
            return self == .online ? .reconnecting : .unreachable
        }

        var connectionState: ConnectionState {
            switch self {
            case .online: return .online
            case .joined, .pairing, .paired, .accessDenied: return .offline
            case .reconnecting: return .reconnecting
            case .unreachable: return .pairedUnreachable
            default: return .tailnetNotDetected
            }
        }

        var busy: Bool {
            switch self {
            case .checkingKeychain, .waitingForKeychain, .joining, .pairing, .paired, .canceling: return true
            default: return false
            }
        }
    }

    @Published private(set) var phase: Phase = .draft
    @Published private(set) var custodyReady = false
    @Published private(set) var profiles: [ManagedJoinProfile] = []
    @Published var selectedProfileID = ""
    @Published var controlURL = ""
    @Published private(set) var confirmedControlHost: String?
    private let keyStore = EnrollmentKeyStore()
    private var operation: Task<Void, Never>?
    private var generation = UUID()
    private(set) var bridge: ClientBridge?
    private var profile: ManagedJoinProfile?
    private var prepared = false
    private var networkSubmitted = false
    private var blockedResumeIDs: Set<String> = []

    var busy: Bool { phase.busy }
    var canConfirmRevoked: Bool { !busy && (profile != nil || profiles.contains(where: { $0.uuid == selectedProfileID })) }
    var canPair: Bool { bridge != nil && [.joined, .accessDenied, .unreachable].contains(phase) }
    var connectionState: ConnectionState { phase.connectionState }

    func prepare() {
        guard !prepared, !busy else { return }
        prepared = true
        refreshCustody()
    }

    func refreshCustody() {
        guard !busy, bridge == nil else { return }
        let token = UUID()
        generation = token
        phase = .checkingKeychain
        custodyReady = false
        operation = Task {
            defer { if generation == token { operation = nil } }
            do {
                try await keyStore.removeStaleItems()
                try Task.checkCancellation()
                guard generation == token else { return }
                custodyReady = true
                do {
                    profiles = try ManagedJoinProfile.saved()
                    phase = profiles.isEmpty ? .draft : .interrupted
                } catch {
                    // Historical incomplete state refuses resume, but must not
                    // prevent fresh-key recovery into a different UUID profile.
                    profiles = []
                    phase = .error(Self.safeMessage(error))
                }
            } catch {
                guard generation == token else { return }
                phase = .error(Self.safeMessage(error))
            }
        }
    }

    static func validatedControlURL(_ input: String) throws -> String {
        guard !input.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              var parts = URLComponents(string: input), parts.scheme?.lowercased() == "https",
              let host = parts.host, !host.isEmpty,
              !host.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) || CharacterSet.whitespaces.contains($0) }),
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.port == nil || (1...65535).contains(parts.port!),
              parts.url != nil else { throw ManagedNetworkError.invalidConfiguration }
        parts.scheme = "https"
        parts.host = host.lowercased()
        parts.path = ""
        return parts.string!
    }

    static func validTarget(_ host: String, port: String) -> UInt16? {
        guard !host.isEmpty, !host.contains(where: { $0.isWhitespace }),
              !host.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              !host.contains(where: { "/?#@%".contains($0) }),
              let number = UInt16(port), number > 0 else { return nil }
        return number
    }

    /// The SecureField caller must clear its binding synchronously on submission.
    func join(key: Data, homeHost: String, port: String) {
        guard !busy, custodyReady, bridge == nil else { return }
        let control: String
        let homePort: UInt16
        do {
            control = try Self.validatedControlURL(controlURL)
            guard let value = Self.validTarget(homeHost, port: port), !key.isEmpty else {
                throw ManagedNetworkError.invalidConfiguration
            }
            homePort = value
        } catch { phase = .error("Enter a trusted HTTPS control URL, home tailnet address, nonzero port and enrollment key."); return }
        let fresh = ManagedJoinProfile.fresh(controlURL: control)
        begin(profile: fresh, homeHost: homeHost, homePort: homePort, key: key)
    }

    func resume(homeHost: String, port: String) {
        guard !busy, custodyReady, bridge == nil,
              let saved = profiles.first(where: { $0.uuid == selectedProfileID }) else { return }
        guard let homePort = Self.validTarget(homeHost, port: port) else {
            phase = .error("Enter a home tailnet address and a port from 1 to 65535.")
            return
        }
        do {
            guard !blockedResumeIDs.contains(saved.uuid), try !saved.isRevoked() else { phase = .revoked; return }
            _ = try Self.validatedControlURL(saved.controlURL)
        } catch { phase = .error(Self.safeMessage(error)); return }
        begin(profile: saved, homeHost: homeHost, homePort: homePort, key: nil)
    }

    private func begin(profile: ManagedJoinProfile, homeHost: String, homePort: UInt16, key: Data?) {
        let token = UUID()
        generation = token
        self.profile = profile
        confirmedControlHost = URL(string: profile.controlURL)?.host
        networkSubmitted = false
        let candidate = ClientBridge(homeConnectorHost: homeHost, homeConnectorPort: homePort, lane: .managed)
        bridge = candidate
        phase = .waitingForKeychain
        var draft = key
        operation = Task {
            defer {
                if let count = draft?.count { draft?.resetBytes(in: 0..<count) }
                if generation == token { operation = nil }
            }
            do {
                // Explicit saved checks also settle stale custody before any network.
                if draft != nil {
                    let owned = try await keyStore.stageAndTake(draft!)
                    if let count = draft?.count { draft?.resetBytes(in: 0..<count) }
                    draft = nil
                    try Task.checkCancellation()
                    guard generation == token else { throw CancellationError() }
                    networkSubmitted = true
                    phase = .joining
                    try await candidate.joinManagedNetwork(key: owned, controlURL: profile.controlURL,
                        hostname: profile.hostname, stateDirectory: profile.directory)
                } else {
                    try await keyStore.removeStaleItems()
                    try Task.checkCancellation()
                    guard generation == token, try !profile.isRevoked() else { throw CancellationError() }
                    networkSubmitted = true
                    phase = .joining
                    try await candidate.resumeManagedNetwork(controlURL: profile.controlURL,
                        hostname: profile.hostname, stateDirectory: profile.directory)
                }
                try Task.checkCancellation()
                guard generation == token else { throw CancellationError() }
                phase = .joined
            } catch {
                await candidate.stopManagedNetwork()
                guard generation == token else { return }
                bridge = nil
                phase = error is CancellationError ? .interrupted : .error(Self.safeMessage(error))
            }
        }
    }

    func pairingStarted() { guard canPair else { return }; phase = .pairing }
    func paired() { guard phase == .pairing else { return }; phase = .paired }
    func healthResult(_ healthy: Bool, denied: Bool = false) {
        phase = phase.afterHealth(healthy, denied: denied)
    }
    func pairingFailed(_ error: RedeemPairingError) {
        let result: Phase
        switch error {
        case .refused: result = .error("Invalid or expired pairing code. Request a fresh pairing code.")
        case .managed(let failure): result = .error(failure.localizedDescription)
        case .connectionFailed: result = .unreachable
        case .invalidResponse: result = .error("Invalid response from home connector")
        }
        disconnect(endingAt: result)
    }

    func cancel() {
        guard busy, phase != .checkingKeychain, phase != .canceling else { return }
        disconnect()
    }

    func disconnect(endingAt finalPhase: Phase = .interrupted) {
        let pending = operation
        let candidate = bridge
        let settledPhase: Phase = finalPhase == .interrupted && !networkSubmitted
            ? .error("Join canceled; nothing sent.") : finalPhase
        let token = UUID()
        generation = token
        phase = .canceling
        pending?.cancel()
        candidate?.stop()
        operation = Task {
            await pending?.value
            await candidate?.stopManagedNetwork()
            guard generation == token else { return }
            bridge = nil
            do { profiles = try ManagedJoinProfile.saved(); phase = settledPhase }
            catch { profiles = []; phase = .error(Self.safeMessage(error)) }
            operation = nil
        }
    }

    func confirmRevoked() {
        guard !busy, let profile = profile ?? profiles.first(where: { $0.uuid == selectedProfileID }) else { return }
        // Keep uncertainty sticky across explicit refreshes in this process.
        blockedResumeIDs.insert(profile.uuid)
        // Stop traffic immediately, even if persisting the resume block fails.
        bridge?.stop()
        do { try profile.markRevoked(); disconnect(endingAt: .revoked) }
        catch {
            custodyReady = false
            disconnect(endingAt: .error("Revocation could not be saved safely. Saved membership is blocked until resolved."))
        }
    }

    static func safeMessage(_ error: Error) -> String {
        if let failure = error as? EnrollmentKeyStore.Failure { return failure.localizedDescription }
        if let failure = error as? ManagedNetworkError { return failure.localizedDescription }
        return "Saved membership could not be safely opened or saved. Resolve it before resuming, or join a fresh profile with a fresh key and pairing approval."
    }
}

/// Nonsecret profile discovery and an operator-confirmed resume block. Native
/// init remains the authority for profile/state/consumed-marker validation.
struct ManagedJoinProfile: Codable, Equatable, Identifiable, Sendable {
    let controlURL: String
    let hostname: String
    let uuid: String
    let phase: String
    var id: String { uuid }
    static var root: String {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
            "Library/Application Support/ai.happyranch.HappyRanchApp/ManagedTailnet").path
    }
    var directory: String { Self.root + "/" + uuid }
    static func fresh(controlURL: String) -> Self {
        let id = UUID().uuidString
        return Self(controlURL: controlURL, hostname: "happyranch-mac-" + id.lowercased(), uuid: id, phase: "draft")
    }

    private static func openDirectory(_ path: String) throws -> Int32 {
        var fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard fd >= 0 else { throw ManagedNetworkError.stateFailure }
        for part in path.split(separator: "/") {
            let next = openat(fd, String(part), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            close(fd)
            guard next >= 0 else { throw ManagedNetworkError.stateFailure }
            fd = next
        }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_uid == geteuid(), info.st_mode & 0o7777 == 0o700 else {
            close(fd); throw ManagedNetworkError.stateFailure
        }
        return fd
    }

    private static func readFile(_ name: String, directory: Int32) throws -> Data? {
        let fd = openat(directory, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        if fd < 0 && errno == ENOENT { return nil }
        guard fd >= 0 else { throw ManagedNetworkError.stateFailure }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == geteuid(), info.st_mode & 0o7777 == 0o600, info.st_nlink == 1,
              info.st_size >= 0, info.st_size <= 65536 else { throw ManagedNetworkError.stateFailure }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
        guard let bytes = try handle.read(upToCount: 65537), bytes.count <= 65536 else { throw ManagedNetworkError.stateFailure }
        return bytes
    }

    static func saved() throws -> [Self] {
        // No missing-root creation here; only native fresh init creates profile state.
        var rootInfo = stat()
        if lstat(root, &rootInfo) != 0, errno == ENOENT { return [] }
        let fd = try openDirectory(root)
        defer { close(fd) }
        let names = try FileManager.default.contentsOfDirectory(atPath: root).sorted()
        var profiles: [Self] = []
        for name in names {
            guard UUID(uuidString: name) != nil else { throw ManagedNetworkError.stateFailure }
            let child = try openDirectory(root + "/" + name)
            defer { close(child) }
            guard let data = try readFile("profile.json", directory: child) else { throw ManagedNetworkError.stateFailure }
            let profile = try JSONDecoder().decode(Self.self, from: data)
            guard profile.uuid == name, ["draft", "submitting", "joined"].contains(profile.phase) else { throw ManagedNetworkError.stateFailure }
            profiles.append(profile)
        }
        return profiles
    }

    func isRevoked() throws -> Bool {
        let fd = try Self.openDirectory(directory)
        defer { close(fd) }
        // Presence, even interrupted/invalid contents, blocks resume.
        return try Self.readFile("revoked", directory: fd) != nil
    }

    func markRevoked() throws {
        let directoryFD = try Self.openDirectory(directory)
        defer { close(directoryFD) }
        let fd = openat(directoryFD, "revoked", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        if fd < 0 && errno == EEXIST { guard try isRevoked() else { throw ManagedNetworkError.stateFailure }; return }
        guard fd >= 0 else { throw ManagedNetworkError.stateFailure }
        let bytes = Array("operator-confirmed\n".utf8)
        let written = bytes.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
        let synced = fsync(fd)
        let closed = close(fd)
        guard written == bytes.count, synced == 0, closed == 0, fsync(directoryFD) == 0 else { throw ManagedNetworkError.stateFailure }
    }
}
