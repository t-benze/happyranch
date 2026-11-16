import Foundation
import CryptoKit

public struct SavedDeviceBinding: Codable, Equatable, Sendable {
    public let homeID: String
    public let controlPlaneID: String
    public let stableNodeID: String

    public init(homeID: String, controlPlaneID: String, stableNodeID: String) throws {
        guard [homeID, controlPlaneID, stableNodeID].allSatisfy({ value in
            !value.isEmpty && value.utf8.count <= 256 && value.utf8.allSatisfy { $0 > 32 && $0 < 127 }
        }) else { throw SavedAccessFailure.invalidRecord }
        self.homeID = homeID
        self.controlPlaneID = controlPlaneID
        self.stableNodeID = stableNodeID
    }

    /// Length-delimited encoding prevents tuple aliasing. No display label participates.
    var account: String {
        let bytes = [homeID, controlPlaneID, stableNodeID].map { "\($0.utf8.count):\($0)" }.joined()
        return SHA256.hash(data: Data(bytes.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Encoded only inside the login Keychain. No invite, join key or pending handle.
public struct SavedDeviceCredential: Codable, Sendable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let binding: SavedDeviceBinding
    public let trust: SavedHomeTrust
    /// Nonsecret native profile identity, kept with the protected trust/grant.
    /// Nil denotes a pre-integration record; it cannot silently select a node.
    public let networkProfileID: UUID?
    private let bearer: Data
    public var description: String { "SavedDeviceCredential(<redacted>)" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["contents": "<redacted>"]) }

    public init(binding: SavedDeviceBinding, trust: SavedHomeTrust, deliveredBearer: Data,
                networkProfileID: UUID? = nil) throws {
        guard binding.homeID == trust.homeID, binding.controlPlaneID == trust.controlPlaneID,
              !deliveredBearer.isEmpty, deliveredBearer.count <= 4096,
              deliveredBearer.allSatisfy({ $0 >= 33 && $0 <= 126 }) else { throw SavedAccessFailure.invalidRecord }
        self.binding = binding
        self.trust = trust
        self.bearer = deliveredBearer
        self.networkProfileID = networkProfileID
    }

    public func withBearer<T>(_ body: (Data) throws -> T) rethrows -> T { try body(bearer) }

    /// Only an explicitly accepted fresh invite may select a new LAN URL.
    /// No tailnet endpoint, home, connector, CA or node repin is permitted.
    public func updatingAddress(to target: SavedHomeTrust) throws -> Self {
        guard trust.homeID == target.homeID, trust.controlPlaneID == target.controlPlaneID,
              trust.connectorID == target.connectorID, trust.homeEndpoint == target.homeEndpoint,
              trust.caDER == target.caDER, trust.caSHA256 == target.caSHA256,
              networkProfileID != nil else { throw HomeConnectionFailure.identityChanged }
        _ = try RemoteInvite.homeControlHost(target.controlURL)
        return try Self(binding: binding, trust: target, deliveredBearer: bearer, networkProfileID: networkProfileID)
    }

    func validated(binding: SavedDeviceBinding, trust: SavedHomeTrust) throws -> Self {
        guard self.binding == binding, self.trust == trust else { throw SavedAccessFailure.identityChanged }
        return try Self(binding: binding, trust: trust, deliveredBearer: bearer, networkProfileID: networkProfileID)
    }
}

public enum SavedAccessFailure: String, Error, CaseIterable, Sendable, LocalizedError {
    case notFound, cancelled, denied, locked, identityChanged, invalidRecord, saveFailed, deletionFailed, unavailable

    public var errorDescription: String? {
        switch self {
        case .cancelled: return "Saved access needs permission — Retry"
        case .locked: return "Unlock login Keychain and retry"
        case .identityChanged: return "Saved access needs Keychain permission after this update — Unlock/Allow and retry"
        case .saveFailed: return "Access was not saved — Retry"
        case .deletionFailed: return "Removal incomplete — Retry removal"
        case .notFound, .denied, .invalidRecord, .unavailable:
            return "Saved access unavailable — create a new invite on Linux"
        }
    }
}

/// Normal model dependency injection; the Security implementation owns all OS calls.
public protocol SavedDeviceCredentialStore: Sendable {
    func save(_ credential: SavedDeviceCredential) async throws
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential
    func delete(binding: SavedDeviceBinding) async throws
}

/// Discovery reads the same protected records; there is no plaintext index.
public protocol SavedHomeCredentialStore: SavedDeviceCredentialStore {
    func savedHomes() async throws -> [SavedDeviceCredential]
    /// Updates data on the exact existing item, preserving its current ACL.
    func refreshAddress(from credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential
}

/// Custody state only. A loaded/saved credential never means Connected.
public actor SavedAccessModel {
    public enum State: Equatable, Sendable { case empty, available, saved, removed, failed(SavedAccessFailure) }
    public private(set) var state: State = .empty
    private var credential: SavedDeviceCredential?
    private let store: any SavedDeviceCredentialStore

    public init(store: any SavedDeviceCredentialStore) { self.store = store }

    /// Caller must have completed durable Linux approval and credential delivery.
    public func saveAfterDelivery(_ delivered: SavedDeviceCredential) async {
        credential = delivered
        await retrySave()
    }

    public func retrySave() async {
        guard let credential else { state = .failed(.notFound); return }
        do { try await store.save(credential); state = .saved }
        catch { state = .failed(.saveFailed) }
        // Save failure intentionally retains the in-memory delivered grant for Retry.
    }

    public func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async {
        credential = nil
        do {
            credential = try await store.load(binding: binding, trust: trust).validated(binding: binding, trust: trust)
            state = .available
        } catch { state = .failed((error as? SavedAccessFailure) ?? .unavailable) }
    }

    public func remove(binding: SavedDeviceBinding) async {
        credential = nil
        do { try await store.delete(binding: binding); state = .removed }
        catch { state = .failed(.deletionFailed) }
    }

    public func currentCredential() -> SavedDeviceCredential? { credential }
}
