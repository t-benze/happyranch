import Foundation
import Darwin

public enum HomeConnectionFailure: String, Error, Equatable, Sendable, CaseIterable, LocalizedError {
    case malformedInvite, expiredInvite, controlUnreachable, joinDenied, homeUnavailable
    case identityUnavailable, approvalDenied, authorizationIncomplete, revoked, daemonUnavailable
    case identityChanged, addressChanged, cleanupIncomplete, reapprovalRequired

    public var errorDescription: String? {
        switch self {
        case .reapprovalRequired: return "New invite and approval required"
        case .malformedInvite: return "Invite could not be validated — Create new invite on Linux"
        case .expiredInvite: return "Invite expired — Create new invite on Linux"
        case .controlUnreachable, .homeUnavailable: return "Not on your home network, or home computer unavailable"
        case .joinDenied: return "Network join did not finish — Create new invite on Linux"
        case .identityUnavailable: return "Device identity unavailable — Create new invite on Linux"
        case .approvalDenied: return "Linux approval denied or expired — Create new invite on Linux"
        case .authorizationIncomplete: return "Authorization did not finish — Create a new invite on Linux"
        case .revoked: return "Access revoked — Create new invite on Linux"
        case .daemonUnavailable: return "Home HappyRanch is offline/restarting — Retry"
        case .identityChanged: return "Home identity changed — approve again"
        case .addressChanged: return "Home address changed — Refresh home address on Linux and accept a fresh invite. If device identity cannot be retained, approve again."
        case .cleanupIncomplete: return "Local cleanup incomplete — Retry removal"
        }
    }

    public static func joinFailure(_ error: Error) -> Self {
        switch error as? ManagedNetworkError {
        case .homeReapprovalRequired: return .reapprovalRequired
        case .homeTrust: return .controlUnreachable
        case .deadline, .backend: return .joinDenied
        case .targetNotPeer, .peerChanged, .stateFailure: return .identityUnavailable
        default: return .controlUnreachable
        }
    }
}

enum HomeRequestPath: String, Sendable { case invite = "/pair/invite", complete = "/pair/complete", health = "/api/v1/health", runtime = "/" }

/// This response stays private to the transport and has no printable body.
struct HomeHTTPResponse: Sendable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    let status: Int
    let body: Data
    var description: String { "HomeHTTPResponse(<redacted>)" }
    var debugDescription: String { description }
    var customMirror: Mirror { Mirror(self, children: ["contents": "<redacted>"]) }

    static func parse(_ response: String) throws -> Self {
        guard let boundary = response.range(of: "\r\n\r\n"),
              let line = response.components(separatedBy: "\r\n").first else { throw HomeConnectionFailure.authorizationIncomplete }
        let parts = line.split(separator: " ")
        guard parts.count >= 2, ["HTTP/1.1", "HTTP/1.0"].contains(String(parts[0])),
              let status = Int(parts[1]), (100...599).contains(status) else { throw HomeConnectionFailure.authorizationIncomplete }
        return Self(status: status, body: Data(response[boundary.upperBound...].utf8))
    }

    func fields() throws -> [String: InviteValue] {
        guard body.count <= 8192 else { throw HomeConnectionFailure.authorizationIncomplete }
        var reader = InviteJSON(bytes: Array(body))
        do { return try reader.document() }
        catch { throw HomeConnectionFailure.authorizationIncomplete }
    }

    func requireAuthorized() throws {
        if (200...299).contains(status) { return }
        // Only a closed connector code establishes revocation or daemon outage.
        let fields = try? fields()
        let code = try? fields?.text("error")
        if status == 403, (code == "revocation_denied" || code == "revocation_stream_closed") { throw HomeConnectionFailure.revoked }
        if status == 403 || status == 503, code == "daemon_unavailable" { throw HomeConnectionFailure.daemonUnavailable }
        if status == 401 || status == 403 { throw HomeConnectionFailure.approvalDenied }
        throw HomeConnectionFailure.homeUnavailable
    }
}

/// Memory-only handle. No synthesized display or mirror may expose it.
public struct PendingHomeApproval: Sendable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let comparisonPhrase: String
    public let expiresAt: Date
    private let handle: String
    public var description: String { "PendingHomeApproval(<redacted>)" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["contents": "<redacted>"]) }

    public init(comparisonPhrase: String, expiresAt: Date, completionHandle: String) throws {
        guard completionHandle.utf8.count == 64, completionHandle.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              comparisonPhrase.range(of: "^pair-[0-9a-f]{4}(-[0-9a-f]{4}){3}$", options: .regularExpression) != nil else {
            throw HomeConnectionFailure.authorizationIncomplete
        }
        self.comparisonPhrase = comparisonPhrase; self.expiresAt = expiresAt; self.handle = completionHandle
    }

    func requestBody() -> Data { Data(handle.utf8) }
}

/// After join-key handoff only these pairing bytes survive until redemption.
public struct HomePairingInvitation: Sendable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let expiresAt: Date
    private let secret: Data
    public init(_ invite: RemoteInvite) {
        expiresAt = invite.expiresAt
        secret = invite.withSecrets { _, _, pair in pair }
    }
    public var description: String { "HomePairingInvitation(<redacted>)" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["contents": "<redacted>"]) }
    func requestBody() -> Data { Data(Base64URL.encode(secret).utf8) }
}

/// Normal dependency boundary for the UI; the real adapter owns network work.
public protocol HomeInviteSession: Sendable {
    func join(trust: SavedHomeTrust, profileID: UUID, key: Data?) async throws
    func recoverAddress(_ credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential
    func pair(_ invite: HomePairingInvitation) async throws -> PendingHomeApproval
    func complete(_ pending: PendingHomeApproval, trust: SavedHomeTrust, profileID: UUID) async throws -> SavedDeviceCredential?
    func open(_ credential: SavedDeviceCredential) async throws -> URL
    func openApp(_ credential: SavedDeviceCredential) async throws -> URL
    func appTransport() throws -> any AppAPITransport
    func checkAccess(_ credential: SavedDeviceCredential) async throws
    func checkAccess() async throws
    func stop() async throws
}

extension HomeInviteSession {
    public func openApp(_ credential: SavedDeviceCredential) async throws -> URL { try await open(credential) }
    public func appTransport() throws -> any AppAPITransport { throw AppAPIError.unavailable }
}

/// All remote bytes go through the per-home helper's managed, pinned peer dial.
/// No legacy pairing, public network, arbitrary URL or hostname fallback exists.
public final class ManagedHomeInviteSession: HomeInviteSession, @unchecked Sendable {
    private let controller: any ProcessControlling
    private let executable: URL
    private let lock = NSLock()
    private var bridge: ClientBridge?

    public init(controller: any ProcessControlling, executable: URL) {
        self.controller = controller; self.executable = executable
    }

    public func join(trust: SavedHomeTrust, profileID: UUID, key: Data?) async throws {
        try await join(trust: trust, profileID: profileID, key: key, recovery: nil)
    }

    public func recoverAddress(_ credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        let updated = try credential.updatingAddress(to: trust)
        guard let id = credential.networkProfileID else { throw HomeConnectionFailure.identityUnavailable }
        try await join(trust: trust, profileID: id, key: nil, recovery: credential)
        return updated // Helper already checked live StableID and current connector admission.
    }

    private func join(trust: SavedHomeTrust, profileID: UUID, key: Data?, recovery: SavedDeviceCredential?) async throws {
        guard let target = URLComponents(string: "https://" + trust.homeEndpoint),
              let host = target.host, target.port == 443 else { throw HomeConnectionFailure.malformedInvite }
        let candidate = ClientBridge(homeConnectorHost: host, homeConnectorPort: 443, lane: .managed)
        let helper = HomeBridgeProcess(controller: controller, executable: executable)
        try lock.withLock {
            guard bridge == nil else { throw ManagedNetworkError.busy }
            bridge = candidate
        }
        let directory = HomeNetworkState.directory(profileID)
        let hostname = "happyranch-mac-" + profileID.uuidString.lowercased()
        do {
            if let key {
                guard let bytes = calloc(key.count + 1, 1) else { throw ManagedNetworkError.unavailable }
                key.copyBytes(to: bytes.assumingMemoryBound(to: UInt8.self), count: key.count)
                try await candidate.joinManagedNetwork(key: ManagedEnrollmentKey(taking: bytes, byteCount: key.count + 1),
                    controlURL: trust.controlURL, hostname: hostname, stateDirectory: directory, homeTrust: trust, helper: helper)
            } else {
                try await candidate.resumeManagedNetwork(controlURL: trust.controlURL, hostname: hostname,
                    stateDirectory: directory, homeTrust: trust, helper: helper, addressRecovery: recovery)
            }
            try Task.checkCancellation()
        } catch {
            try? await stop()
            if error is CancellationError { throw CancellationError() }
            throw HomeConnectionFailure.joinFailure(error)
        }
    }

    private func active() throws -> ClientBridge {
        try lock.withLock { guard let bridge else { throw HomeConnectionFailure.identityUnavailable }; return bridge }
    }

    public func pair(_ invite: HomePairingInvitation) async throws -> PendingHomeApproval {
        guard Date() < invite.expiresAt else { throw HomeConnectionFailure.expiredInvite }
        var body = invite.requestBody()
        defer { body.resetBytes(in: 0..<body.count) }
        return try Self.pending(try await active().homeRequest(.invite, body: body), deadline: invite.expiresAt)
    }

    static func pending(_ response: HomeHTTPResponse, deadline: Date) throws -> PendingHomeApproval {
        if response.status == 503 { try response.requireAuthorized() }
        guard response.status == 200 else { throw HomeConnectionFailure.approvalDenied }
        let fields = try response.fields()
        guard Set(fields.keys) == ["state", "completion_handle", "comparison_phrase", "expires_at"],
              try fields.text("state") == "pending-approval" else { throw HomeConnectionFailure.authorizationIncomplete }
        let stamp = try fields.text("expires_at")
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var expiry = formatter.date(from: stamp)
        if expiry == nil {
            formatter.formatOptions = [.withInternetDateTime]
            expiry = formatter.date(from: stamp)
        }
        guard let expiry, expiry <= deadline else { throw HomeConnectionFailure.authorizationIncomplete }
        return try PendingHomeApproval(comparisonPhrase: fields.text("comparison_phrase"),
            expiresAt: expiry, completionHandle: fields.text("completion_handle"))
    }

    public func complete(_ pending: PendingHomeApproval, trust: SavedHomeTrust, profileID: UUID) async throws -> SavedDeviceCredential? {
        guard Date() < pending.expiresAt else { throw HomeConnectionFailure.expiredInvite }
        var body = pending.requestBody()
        defer { body.resetBytes(in: 0..<body.count) }
        let response = try await active().homeRequest(.complete, body: body)
        guard let node = try active().homeStableNodeID else { throw HomeConnectionFailure.identityUnavailable }
        return try Self.delivered(response, trust: trust, profileID: profileID, stableNodeID: node)
    }

    static func delivered(_ response: HomeHTTPResponse, trust: SavedHomeTrust, profileID: UUID, stableNodeID: String) throws -> SavedDeviceCredential? {
        // Linux intentionally conflates pending/rejected/spent handles. Poll only
        // this exact denial with the same handle until the original deadline.
        if response.status == 403 {
            let fields = try response.fields()
            if Set(fields.keys) == ["error"], try fields.text("error") == "pairing_denied" { return nil }
        }
        if response.status == 503 { try response.requireAuthorized() }
        guard response.status == 200 else { throw HomeConnectionFailure.approvalDenied }
        let fields = try response.fields()
        guard Set(fields.keys) == ["credential"] else { throw HomeConnectionFailure.authorizationIncomplete }
        return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
            controlPlaneID: trust.controlPlaneID, stableNodeID: stableNodeID), trust: trust,
            deliveredBearer: Data(fields.text("credential").utf8), networkProfileID: profileID)
    }

    public func open(_ credential: SavedDeviceCredential) async throws -> URL {
        let candidate = try active()
        try candidate.useHomeCredential(credential)
        try await checkAccess()
        try await Task.detached { try candidate.start() }.value
        let deadline = Date().addingTimeInterval(10)
        while candidate.bridgePort == nil {
            try Task.checkCancellation()
            guard Date() < deadline else { throw HomeConnectionFailure.homeUnavailable }
            if case .failed = candidate.state { throw HomeConnectionFailure.homeUnavailable }
            try await Task.sleep(for: .milliseconds(25))
        }
        guard let port = candidate.bridgePort, let url = URL(string: "http://127.0.0.1:\(port)/") else {
            throw HomeConnectionFailure.homeUnavailable
        }
        return url
    }

    /// GUI path: authorize the existing selected owner, with no loopback listener.
    public func openApp(_ credential: SavedDeviceCredential) async throws -> URL {
        try await checkAccess(credential)
        return URL(string: "happyranch-app://app/")!
    }

    public func appTransport() throws -> any AppAPITransport { try active() }

    /// Shared GUI/CLI admission: bind native authority and observe health only.
    public func checkAccess(_ credential: SavedDeviceCredential) async throws {
        try active().useHomeCredential(credential)
        try await checkAccess()
    }

    public func checkAccess() async throws { try await active().homeRequest(.health).requireAuthorized() }

    public func stop() async throws {
        guard let candidate = lock.withLock({ bridge }) else { return }
        await candidate.stopManagedNetwork()
        guard candidate.managedStopConfirmed else { throw HomeConnectionFailure.cleanupIncomplete }
        lock.withLock { bridge = nil }
    }
}

/// Deletion is limited to the exact private profile, under the native flock.
/// Unknown/nested/unsafe entries refuse with a visible incomplete result.
public enum HomeNetworkState {
    public static func directory(_ id: UUID) -> String {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
            "Library/Application Support/ai.happyranch.HappyRanchApp/ManagedTailnet/" + id.uuidString).path
    }

    public static func remove(_ id: UUID) throws {
        let path = directory(id)
        var fd = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard fd >= 0 else { throw HomeConnectionFailure.cleanupIncomplete }
        defer { close(fd) }
        for component in path.split(separator: "/").dropLast() {
            let next = openat(fd, String(component), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            if next < 0, errno == ENOENT { return }
            guard next >= 0 else { throw HomeConnectionFailure.cleanupIncomplete }
            close(fd); fd = next
        }
        var parentInfo = stat()
        guard fstat(fd, &parentInfo) == 0, parentInfo.st_uid == geteuid(), parentInfo.st_mode & 0o7777 == 0o700 else {
            throw HomeConnectionFailure.cleanupIncomplete
        }
        let name = id.uuidString
        let profile = openat(fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        if profile < 0, errno == ENOENT { return }
        guard profile >= 0 else { throw HomeConnectionFailure.cleanupIncomplete }
        defer { close(profile) }
        var info = stat()
        guard fstat(profile, &info) == 0, info.st_uid == geteuid(), info.st_mode & 0o7777 == 0o700 else {
            throw HomeConnectionFailure.cleanupIncomplete
        }
        let lockFD = openat(profile, ".lock", O_RDWR | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        if lockFD < 0, errno == ENOENT,
           try FileManager.default.contentsOfDirectory(atPath: path).isEmpty {
            // Retry after the final lock unlink succeeded but rmdir failed.
            var named = stat()
            guard fstatat(fd, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
                  named.st_dev == info.st_dev, named.st_ino == info.st_ino,
                  unlinkat(fd, name, AT_REMOVEDIR) == 0, fsync(fd) == 0 else { throw HomeConnectionFailure.cleanupIncomplete }
            return
        }
        guard lockFD >= 0 else { throw HomeConnectionFailure.cleanupIncomplete }
        defer { close(lockFD) }
        var lockInfo = stat()
        guard fstat(lockFD, &lockInfo) == 0, lockInfo.st_uid == geteuid(), lockInfo.st_mode & S_IFMT == S_IFREG,
              lockInfo.st_mode & 0o7777 == 0o600, lockInfo.st_nlink == 1,
              flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { throw HomeConnectionFailure.cleanupIncomplete }
        // tsnet's current private profile uses flat files. Do not recursively
        // delete an unknown future layout or follow a symlink to another home.
        let names = try FileManager.default.contentsOfDirectory(atPath: path)
        guard names.count <= 64 else { throw HomeConnectionFailure.cleanupIncomplete }
        for item in names {
            var entry = stat()
            guard fstatat(profile, item, &entry, AT_SYMLINK_NOFOLLOW) == 0,
                  entry.st_uid == geteuid(), entry.st_mode & S_IFMT == S_IFREG,
                  entry.st_mode & 0o7777 == 0o600, entry.st_nlink == 1 else { throw HomeConnectionFailure.cleanupIncomplete }
        }
        for item in names where item != ".lock" {
            guard unlinkat(profile, item, 0) == 0 else { throw HomeConnectionFailure.cleanupIncomplete }
        }
        var named = stat()
        guard fstatat(fd, name, &named, AT_SYMLINK_NOFOLLOW) == 0,
              named.st_dev == info.st_dev, named.st_ino == info.st_ino,
              unlinkat(profile, ".lock", 0) == 0, fsync(profile) == 0,
              unlinkat(fd, name, AT_REMOVEDIR) == 0, fsync(fd) == 0 else { throw HomeConnectionFailure.cleanupIncomplete }
    }
}
