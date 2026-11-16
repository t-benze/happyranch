import Foundation
import Combine
import HappyRanchSupervisor

/// The only home-client UI state. No secret is published or restored.
@MainActor
final class HomeInviteModel: ObservableObject {
    enum Phase: Equatable {
        case idle, checkingSavedAccess, validating, joining, pairing, awaitingApproval, opening, connected, stopping
        case failed(HomeConnectionFailure), keychain(SavedAccessFailure)

        var message: String {
            switch self {
            case .idle: return "Idle"
            case .checkingSavedAccess: return "Checking saved access in login Keychain"
            case .validating: return "Validating invite"
            case .joining: return "Joining network"
            case .pairing: return "Pairing"
            case .awaitingApproval: return "Awaiting Linux approval"
            case .opening: return "Opening verified session"
            case .connected: return "Connected"
            case .stopping: return "Closing local session"
            case .failed(let failure): return failure.localizedDescription
            case .keychain(let failure): return failure.localizedDescription
            }
        }
        var busy: Bool { [.checkingSavedAccess, .validating, .joining, .pairing, .awaitingApproval, .opening, .stopping].contains(self) }
    }

    struct SavedHome: Identifiable {
        let id: UUID
        let trust: SavedHomeTrust
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var target: SavedHomeTrust?
    @Published private(set) var expiresAt: Date?
    @Published private(set) var comparisonPhrase: String?
    @Published private(set) var webURL: URL?
    @Published private(set) var savedHomes: [SavedHome] = []
    @Published private(set) var saveFailure: SavedAccessFailure?
    @Published private(set) var removalFailures: [String] = []
    @Published var selectedHomeID: UUID?
    private(set) var attemptID = UUID()
    private var operation: Task<Void, Never>?
    private var expiryTask: Task<Void, Never>?
    private var navigationDeadline: Task<Void, Never>?
    private var monitor: Task<Void, Never>?
    private var session: (any HomeInviteSession)?
    private let store: any SavedHomeCredentialStore
    private let makeSession: @Sendable () -> any HomeInviteSession
    private var records: [UUID: SavedDeviceCredential] = [:]
    private var unsaved: SavedDeviceCredential?
    private var prepared = false
    private var needsNewInvite = false
    private var rePairHomeIDs: Set<UUID> = []
    private var unfinishedMembership: (trust: SavedHomeTrust, id: UUID)?
    private(set) var needsReplacementConsent = false

    init(store: any SavedHomeCredentialStore = LoginKeychainDeviceStore(),
         makeSession: @escaping @Sendable () -> any HomeInviteSession = {
             ManagedHomeInviteSession(controller: RealProcessController(), executable:
                 Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/happyranch-home-bridge"))
         }) {
        self.store = store; self.makeSession = makeSession
    }

    var busy: Bool { phase.busy || operation != nil }
    var retryGuidance: String {
        if saveFailure != nil { return "Retry saving while this session is open. If the app quits before saving, create a new invite on Linux." }
        return needsNewInvite ? "Create a new invite on Linux. Retry accepts a new invite; the spent key is never reused." :
            "Connect to your home network / Retry. Saved access needs no new invite while its grant remains valid."
    }

    func prepare() {
        guard !prepared, !busy else { return }
        prepared = true
        phase = .checkingSavedAccess
        operation = Task {
            do {
                let credentials = try await store.savedHomes()
                try Task.checkCancellation()
                var discovered: [UUID: SavedDeviceCredential] = [:]
                for credential in credentials {
                    guard let id = credential.networkProfileID else { throw SavedAccessFailure.invalidRecord }
                    guard discovered[id] == nil else { throw SavedAccessFailure.invalidRecord }
                    discovered[id] = credential
                }
                records = discovered
                savedHomes = records.map { SavedHome(id: $0.key, trust: $0.value.trust) }.sorted { $0.id.uuidString < $1.id.uuidString }
                selectedHomeID = savedHomes.first?.id
                phase = .idle
                operation = nil
                // A single saved computer is the ordinary quit/reopen path.
                if savedHomes.count == 1 { reconnect() }
            } catch {
                phase = .keychain((error as? SavedAccessFailure) ?? .unavailable)
                needsReplacementConsent = true
                operation = nil
            }
        }
    }

    /// SecureField caller clears its binding synchronously before calling.
    func accept(_ raw: String, replacementConsent: Bool = false) {
        guard !busy, session == nil else { return }
        guard !needsReplacementConsent || replacementConsent else { return }
        let token = UUID(); attemptID = token
        phase = .validating; needsNewInvite = true; removalFailures = []
        var draft = raw
        operation = Task {
            defer { settleOperation(token); draft.removeAll() }
            do {
                var invite: RemoteInvite? = try await Task.detached { [input = draft] in try RemoteInvite.parse(input) }.value
                draft.removeAll()
                try Task.checkCancellation()
                guard attemptID == token, invite != nil else { return }
                let trust = invite!.trust
                let deadline = invite!.expiresAt
                target = trust; expiresAt = deadline
                // No automatic CA repin or blind protected marker rewrite.
                if let previous = records.values.first(where: { $0.trust.homeID == trust.homeID }) {
                    guard previous.trust.caSHA256 == trust.caSHA256, previous.trust.caDER == trust.caDER,
                          previous.trust.controlPlaneID == trust.controlPlaneID,
                          previous.trust.connectorID == trust.connectorID else { throw HomeConnectionFailure.identityChanged }
                    guard previous.trust == trust else { throw HomeConnectionFailure.addressChanged }
                    if let id = previous.networkProfileID, !rePairHomeIDs.contains(id) {
                        invite = nil
                        expiresAt = nil
                        selectedHomeID = id
                        operation = nil
                        phase = .idle
                        reconnect()
                        return // valid grant path never consumes this invite/key
                    }
                }
                armExpiry(deadline, token: token)
                let candidate = makeSession(); session = candidate
                let retained = unfinishedMembership.flatMap { $0.trust == trust ? $0.id : nil }
                let id = retained ?? UUID()
                unfinishedMembership = (trust, id)
                phase = .joining
                var pairing: HomePairingInvitation? = HomePairingInvitation(invite!)
                var key = retained == nil ? invite!.withSecrets { _, key, _ in key } : Data()
                invite = nil
                do { try await candidate.join(trust: trust, profileID: id, key: retained == nil ? key : nil) }
                catch { key.resetBytes(in: 0..<key.count); throw error }
                key.resetBytes(in: 0..<key.count)
                try check(token)
                phase = .pairing
                let pending = try await candidate.pair(pairing!)
                pairing = nil
                try check(token)
                guard pending.expiresAt <= deadline, Date() < pending.expiresAt else { throw HomeConnectionFailure.expiredInvite }
                expiresAt = pending.expiresAt
                armExpiry(pending.expiresAt, token: token)
                comparisonPhrase = pending.comparisonPhrase
                phase = .awaitingApproval
                while true {
                    try check(token)
                    guard Date() < pending.expiresAt else { throw HomeConnectionFailure.expiredInvite }
                    if let delivered = try await candidate.complete(pending, trust: trust, profileID: id) {
                        try check(token)
                        guard Date() < pending.expiresAt else { throw HomeConnectionFailure.expiredInvite }
                        expiryTask?.cancel(); expiryTask = nil
                        expiresAt = nil; comparisonPhrase = nil
                        needsNewInvite = false
                        unfinishedMembership = nil
                        unsaved = delivered
                        await saveDelivered(token: token)
                        try check(token)
                        try await open(candidate, credential: delivered, token: token)
                        return
                    }
                    try await Task.sleep(for: .seconds(1))
                }
            } catch {
                let failure: HomeConnectionFailure
                if let error = error as? RemoteInviteError { failure = error == .time ? .expiredInvite : .malformedInvite }
                else { failure = (error as? HomeConnectionFailure) ?? .authorizationIncomplete }
                await fail(failure, token: token)
            }
        }
    }

    func reconnect() {
        guard !busy, session == nil, let id = selectedHomeID, let metadata = records[id] else { return }
        let token = UUID(); attemptID = token; needsNewInvite = false
        phase = .checkingSavedAccess
        operation = Task {
            defer { settleOperation(token) }
            do {
                let credential: SavedDeviceCredential
                do { credential = try await store.load(binding: metadata.binding, trust: metadata.trust) }
                catch {
                    guard attemptID == token else { return }
                    needsReplacementConsent = true
                    rePairHomeIDs.insert(id)
                    phase = .keychain((error as? SavedAccessFailure) ?? .unavailable); return
                }
                try check(token)
                guard credential.networkProfileID == id, credential.binding == metadata.binding,
                      credential.trust == metadata.trust else { throw HomeConnectionFailure.identityUnavailable }
                rePairHomeIDs.remove(id)
                needsReplacementConsent = false
                let candidate = makeSession(); session = candidate; target = credential.trust
                phase = .joining
                try await candidate.join(trust: credential.trust, profileID: id, key: nil)
                try check(token)
                try await open(candidate, credential: credential, token: token)
            } catch { await fail((error as? HomeConnectionFailure) ?? .homeUnavailable, token: token) }
        }
    }

    private func saveDelivered(token: UUID) async {
        guard let credential = unsaved, let id = credential.networkProfileID else { return }
        do {
            try await store.save(credential)
            guard attemptID == token else { return }
            records[id] = credential
            savedHomes.removeAll { $0.id == id }
            savedHomes.append(SavedHome(id: id, trust: credential.trust))
            selectedHomeID = id; saveFailure = nil; unsaved = nil
            needsReplacementConsent = false
        } catch {
            guard attemptID == token else { return }
            saveFailure = (error as? SavedAccessFailure) == .identityChanged ? .identityChanged : .saveFailed
        }
    }

    func retrySave(replacing: Bool = false) {
        guard operation == nil, let delivered = unsaved else { return }
        let token = attemptID
        operation = Task {
            defer { if attemptID == token { operation = nil } }
            if replacing {
                // Called only after fresh approval/delivery and explicit UI consent.
                do { try await store.delete(binding: delivered.binding) }
                catch { saveFailure = .deletionFailed; return }
            }
            await saveDelivered(token: token)
        }
    }

    private func open(_ candidate: any HomeInviteSession, credential: SavedDeviceCredential, token: UUID) async throws {
        phase = .opening
        let url = try await candidate.open(credential)
        try check(token)
        webURL = url
        navigationDeadline?.cancel()
        navigationDeadline = Task {
            do { try await Task.sleep(for: .seconds(10)) } catch { return }
            guard attemptID == token, phase == .opening else { return }
            cancel(endingAt: .failed(.homeUnavailable))
        }
    }

    /// Called only for the expected main-frame 2xx navigation, after connector
    /// authorization and release-verifier admission. Health alone cannot call it.
    func webLoaded(url: URL, attempt: UUID) {
        guard attemptID == attempt, phase == .opening, webURL == url else { return }
        navigationDeadline?.cancel(); navigationDeadline = nil
        phase = .connected
        let token = attemptID
        monitor = Task {
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(5))
                    guard attemptID == token, let session else { return }
                    try await session.checkAccess()
                } catch {
                    guard attemptID == token, !Task.isCancelled else { return }
                    let failure = (error as? HomeConnectionFailure) ?? .homeUnavailable
                    cancel(endingAt: .failed(failure))
                    return
                }
            }
        }
    }

    func webFailed(url: URL, attempt: UUID) {
        guard attemptID == attempt, webURL == url else { return }
        cancel(endingAt: .failed(.homeUnavailable))
    }

    private func check(_ token: UUID) throws {
        try Task.checkCancellation()
        guard attemptID == token else { throw CancellationError() }
    }

    private func armExpiry(_ expiry: Date, token: UUID) {
        expiryTask?.cancel()
        expiryTask = Task {
            do { try await Task.sleep(for: .seconds(max(0, expiry.timeIntervalSinceNow))) } catch { return }
            guard attemptID == token else { return }
            cancel(endingAt: .failed(.expiredInvite))
        }
    }

    private func fail(_ failure: HomeConnectionFailure, token: UUID) async {
        // Cancellation owner changes attemptID and performs its own settlement.
        guard attemptID == token else { return }
        expiryTask?.cancel(); expiryTask = nil
        expiresAt = nil; comparisonPhrase = nil; webURL = nil
        do { try await session?.stop(); session = nil; phase = .failed(failure) }
        catch { phase = .failed(.cleanupIncomplete) }
        if failure == .approvalDenied || failure == .revoked || failure == .identityUnavailable { needsNewInvite = true }
        if failure == .approvalDenied || failure == .identityUnavailable, let id = selectedHomeID { rePairHomeIDs.insert(id) }
    }

    /// Disconnect retains the Keychain record and native membership. Cancel
    /// invalidates every late result and forgets pending secrets/handles.
    func cancel(endingAt finalPhase: Phase = .idle) {
        let old = operation; old?.cancel()
        attemptID = UUID(); let token = attemptID
        expiryTask?.cancel(); monitor?.cancel(); navigationDeadline?.cancel()
        expiresAt = nil; comparisonPhrase = nil; webURL = nil; unsaved = nil; saveFailure = nil
        phase = .stopping
        operation = Task {
            await old?.value
            guard attemptID == token else { return }
            do { try await session?.stop(); session = nil; phase = finalPhase }
            catch { phase = .failed(.cleanupIncomplete) }
            operation = nil
            if finalPhase == .failed(.revoked) { removeSavedComputer(afterRevocation: true) }
        }
    }

    func retry() {
        guard !busy else { return }
        if unsaved != nil { retrySave(); return }
        if !prepared || savedHomes.isEmpty {
            prepared = false; prepare()
        } else if !needsNewInvite { reconnect() }
        else { phase = .idle }
    }

    private func settleOperation(_ token: UUID) {
        guard attemptID == token else { return }
        operation = nil
        if phase == .failed(.revoked) { removeSavedComputer(afterRevocation: true) }
    }

    func removeSavedComputer(afterRevocation: Bool = false) {
        guard operation == nil, let id = selectedHomeID, let credential = records[id] else { return }
        let token = UUID(); attemptID = token
        monitor?.cancel(); navigationDeadline?.cancel(); expiryTask?.cancel()
        webURL = nil; unsaved = nil; comparisonPhrase = nil; expiresAt = nil
        phase = .stopping; removalFailures = []
        operation = Task {
            defer { if attemptID == token { operation = nil } }
            do { try await session?.stop(); session = nil }
            catch { removalFailures = ["Local session could not be confirmed stopped"]; phase = .failed(.cleanupIncomplete); return }
            do { try await Task.detached { try HomeNetworkState.remove(id) }.value }
            catch {
                removalFailures = ["Saved network state could not be erased", "Login Keychain access retained for Retry removal"]
                phase = .failed(.cleanupIncomplete); return
            }
            do { try await store.delete(binding: credential.binding) }
            catch { removalFailures = ["Login Keychain bearer and trust could not be erased"]; phase = .keychain(.deletionFailed); return }
            records.removeValue(forKey: id); savedHomes.removeAll { $0.id == id }
            selectedHomeID = savedHomes.first?.id; target = nil; saveFailure = nil
            phase = afterRevocation ? .failed(.revoked) : .idle
            if afterRevocation { needsNewInvite = true }
        }
    }
}
