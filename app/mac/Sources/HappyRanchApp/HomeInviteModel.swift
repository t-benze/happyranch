import Foundation
import Combine
import HappyRanchSupervisor

/// The only home-client UI state. No secret is published or restored.
@MainActor
final class HomeInviteModel: ObservableObject {
    enum Phase: Equatable {
        case idle, checkingSavedAccess, validating, joining, pairing, awaitingApproval, saving, opening, connected, stopping
        case failed(HomeConnectionFailure), keychain(SavedAccessFailure)

        var message: String {
            switch self {
            case .idle: return "Idle"
            case .checkingSavedAccess: return "Checking saved access in login Keychain"
            case .validating: return "Validating invite"
            case .joining: return "Joining network"
            case .pairing: return "Pairing"
            case .awaitingApproval: return "Awaiting Linux approval"
            case .saving: return "Saving access in login Keychain"
            case .opening: return "Opening runtime"
            case .connected: return "Connected"
            case .stopping: return "Closing local session; waiting for any storage write/readback to settle"
            case .failed(let failure): return failure.localizedDescription
            case .keychain(let failure): return failure.localizedDescription
            }
        }
        var busy: Bool { [.checkingSavedAccess, .validating, .joining, .pairing, .awaitingApproval, .saving, .opening, .stopping].contains(self) }
    }

    /// Captured by operations and by the native release-UI callbacks. A URL
    /// alone cannot identify a document: loopback addresses can be reused.
    struct Lifetime: Equatable, Hashable {
        let attempt: UUID
        let document: UUID
    }

    struct SavedHome: Identifiable, Equatable {
        let id: UUID
        let trust: SavedHomeTrust
        let binding: SavedDeviceBinding
    }

    struct AccessObservation {
        enum Access: String { case allowed, accessDenied, revokedConfirmed, unknown }
        let access: Access
        let observedAt: Date
        let lifetime: Lifetime
    }
    enum SaveSettlement: Equatable { case saved, refused, changed, failed(SavedAccessFailure) }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var target: SavedHomeTrust?
    @Published private(set) var expiresAt: Date?
    @Published private(set) var comparisonPhrase: String?
    @Published private(set) var webURL: URL?
    @Published private(set) var savedHomes: [SavedHome] = []
    @Published private(set) var saveFailure: SavedAccessFailure?
    @Published private(set) var removalFailures: [String] = []
    @Published private(set) var discoveryFailures: [SavedAccessFailure] = []
    @Published private(set) var discoveryComplete = false
    @Published private(set) var accessObservation: AccessObservation?
    @Published var selectedHomeID: UUID?
    private(set) var attemptID = UUID()
    @Published private(set) var documentGeneration = UUID()
    var lifetime: Lifetime { Lifetime(attempt: attemptID, document: documentGeneration) }
    private var confirmation: Lifetime?
    private var loadedURL: URL?
    private var saving: Lifetime?
    private var cancelledSave: Lifetime?
    private var operation: Task<Void, Never>?
    private var expiryTask: Task<Void, Never>?
    private var navigationDeadline: Task<Void, Never>?
    private var monitor: Task<Void, Never>?
    private var session: (any HomeInviteSession)?
    private let store: any SavedHomeCredentialStore
    private let makeSession: @Sendable () -> any HomeInviteSession
    private var records: [UUID: SavedDeviceCredential] = [:]
    private var unsaved: SavedDeviceCredential?
    private var activeCredential: SavedDeviceCredential?
    private var pendingRemoval: SavedDeviceCredential?
    private var pendingRemovalErased = false
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
    // Nonsecret observations only; no read or discovery is initiated here.
    var bridgeActiveComputerID: UUID? { activeCredential?.networkProfileID }
    var bridgeHasUnsavedAccess: Bool { unsaved != nil }
    var bridgeHasConfirmation: Bool { confirmation != nil }
    var bridgeCanRetrySave: Bool { !busy && unsaved != nil && !needsReplacementConsent && saveFailure != .identityChanged }

    /// Reuse the existing save path and await its observed write/readback. This
    /// never invokes the explicit replacement/delete path or guesses completion
    /// from Task scheduling. Caller owns the one-prompt slot through settlement.
    func bridgeRetrySave() async -> SaveSettlement {
        guard bridgeCanRetrySave else { return .refused }
        let token = lifetime
        retrySave()
        guard let operation else { return .refused }
        await operation.value
        guard lifetime == token else { return .changed }
        if let saveFailure { return .failed(saveFailure) }
        return unsaved == nil ? .saved : .failed(.saveFailed)
    }
    var retryGuidance: String {
        if saveFailure != nil, unsaved == nil { return "Saving was not confirmed. Retry checks saved access again; a new invite may be needed." }
        if saveFailure != nil { return "Retry saving while this session is open. If the app quits before saving, create a new invite on Linux." }
        return needsNewInvite ? "Create a new invite on Linux. Retry accepts a new invite; the spent key is never reused." :
            "Connect to your home network / Retry. Saved access needs no new invite while its grant remains valid."
    }

    func prepare() {
        guard !prepared, !busy else { return }
        prepared = true
        let token = lifetime
        phase = .checkingSavedAccess
        operation = Task {
            defer { settleOperation(token) }
            do {
                let discovery = try await store.savedHomes()
                try check(token)
                let credentials = discovery.credentials
                discoveryFailures = discovery.failures
                needsReplacementConsent = !discovery.failures.isEmpty
                try Task.checkCancellation()
                var discovered: [UUID: SavedDeviceCredential] = [:]
                for credential in credentials {
                    guard let id = credential.networkProfileID else { throw SavedAccessFailure.invalidRecord }
                    guard discovered[id] == nil else { throw SavedAccessFailure.invalidRecord }
                    discovered[id] = credential
                }
                records = discovered
                discoveryComplete = true
                savedHomes = records.map { SavedHome(id: $0.key, trust: $0.value.trust, binding: $0.value.binding) }.sorted { $0.id.uuidString < $1.id.uuidString }
                selectedHomeID = savedHomes.first?.id
                phase = .idle
                operation = nil
                // A single saved computer is the ordinary quit/reopen path.
                if savedHomes.count == 1 { reconnect() }
            } catch {
                guard lifetime == token else { return }
                phase = .keychain((error as? SavedAccessFailure) ?? .unavailable)
                needsReplacementConsent = true
                operation = nil
            }
        }
    }

    /// SecureField caller clears its binding synchronously before calling.
    func accept(_ raw: String, replacementConsent: Bool = false) {
        guard !busy, session == nil, pendingRemoval == nil else { return }
        guard !needsReplacementConsent || replacementConsent else { return }
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil
        phase = .validating; needsNewInvite = true; removalFailures = []
        activeCredential = nil
        var draft = raw
        operation = Task {
            defer { settleOperation(token); draft.removeAll() }
            do {
                var invite: RemoteInvite? = try await Task.detached { [input = draft] in try RemoteInvite.parse(input) }.value
                draft.removeAll()
                try Task.checkCancellation()
                guard lifetime == token, invite != nil else { return }
                let trust = invite!.trust
                let deadline = invite!.expiresAt
                target = trust; expiresAt = deadline
                // No automatic CA repin or blind protected marker rewrite.
                if let previous = records.values.first(where: { $0.trust.homeID == trust.homeID }) {
                    guard previous.trust.caSHA256 == trust.caSHA256, previous.trust.caDER == trust.caDER,
                          previous.trust.controlPlaneID == trust.controlPlaneID,
                          previous.trust.connectorID == trust.connectorID else { throw HomeConnectionFailure.identityChanged }
                    if previous.trust != trust {
                        _ = try previous.updatingAddress(to: trust)
                        guard records.values.filter({ $0.trust.homeID == trust.homeID }).count == 1 else {
                            throw HomeConnectionFailure.addressChanged
                        }
                        if previous.trust.controlURL == trust.controlURL, previous.trust.dialIP == trust.dialIP,
                           let id = previous.networkProfileID, !rePairHomeIDs.contains(id) {
                            invite = nil; expiresAt = nil; target = previous.trust
                            selectedHomeID = id; operation = nil; phase = .idle
                            reconnect()
                            return
                        }
                        if let id = previous.networkProfileID, !rePairHomeIDs.contains(id) {
                            invite = nil // New join key/pair secret are never submitted in metadata recovery.
                            armExpiry(deadline, token: token)
                            selectedHomeID = id
                            let saved = try await store.load(binding: previous.binding, trust: previous.trust)
                            try check(token)
                            guard saved.binding == previous.binding, saved.networkProfileID == id,
                                  saved.trust == previous.trust else { throw HomeConnectionFailure.identityUnavailable }
                            let candidate = makeSession(); session = candidate
                            activeCredential = saved
                            phase = .joining
                            let recovered = try await candidate.recoverAddress(saved, to: trust)
                            try check(token)
                            guard Date() < deadline, recovered.binding == saved.binding,
                                  recovered.networkProfileID == id, recovered.trust == trust,
                                  recovered.withBearer({ old in saved.withBearer { old == $0 } }) else {
                                throw HomeConnectionFailure.identityUnavailable
                            }
                            let updated = try await refreshAddress(saved, to: trust, token: token)
                            try check(token)
                            expiryTask?.cancel(); expiryTask = nil; expiresAt = nil
                            needsNewInvite = false
                            try await open(candidate, credential: updated, token: token)
                            return
                        }
                        // A previous recovery proved the old grant/node unusable.
                        // Only explicit consent plus a fresh invite can start a
                        // new profile and ordinary Linux approval; never rewrite
                        // the old profile to repair an unknown identity.
                        guard replacementConsent else { throw HomeConnectionFailure.addressChanged }
                    }
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
                var candidate = makeSession(); session = candidate
                let retained = unfinishedMembership.flatMap { $0.trust == trust ? $0.id : nil }
                var id = retained ?? UUID()
                phase = .joining
                var pairing: HomePairingInvitation? = HomePairingInvitation(invite!)
                var key = invite!.withSecrets { _, key, _ in key }
                invite = nil
                defer { key.resetBytes(in: 0..<key.count) }
                var reopened = false
                if retained != nil {
                    // An interrupted join may or may not have enrolled. Only an
                    // explicit empty-key check can establish reusable membership.
                    do {
                        try await candidate.join(trust: trust, profileID: id, key: nil)
                        try check(token)
                        reopened = true
                    } catch {
                        try check(token)
                        try await candidate.stop()
                        try check(token)
                        candidate = makeSession(); session = candidate
                        id = UUID() // Never repair or reuse the uncertain profile/key.
                    }
                }
                unfinishedMembership = (trust, id)
                if !reopened { try await candidate.join(trust: trust, profileID: id, key: key) }
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
                    let delivery = try await candidate.complete(pending, trust: trust, profileID: id)
                    try check(token)
                    if let delivered = delivery {
                        guard Date() < pending.expiresAt else { throw HomeConnectionFailure.expiredInvite }
                        expiryTask?.cancel(); expiryTask = nil
                        expiresAt = nil; comparisonPhrase = nil
                        needsNewInvite = false
                        unfinishedMembership = nil
                        unsaved = delivered
                        activeCredential = delivered
                        await saveDelivered(token: token)
                        try check(token)
                        try await open(candidate, credential: delivered, token: token)
                        return
                    }
                    try await Task.sleep(for: .seconds(1))
                }
            } catch {
                let failure: HomeConnectionFailure
                if let error = error as? RemoteInviteError { failure = error == .reapprovalRequired ? .reapprovalRequired : (error == .time ? .expiredInvite : .malformedInvite) }
                else { failure = (error as? HomeConnectionFailure) ?? .authorizationIncomplete }
                await fail(failure, token: token)
                if let custody = error as? SavedAccessFailure, lifetime == token, session == nil {
                    phase = .keychain(custody)
                    if custody != .saveFailed {
                        needsReplacementConsent = true
                        if let id = selectedHomeID { rePairHomeIDs.insert(id) }
                    }
                }
            }
        }
    }

    func reconnect() {
        guard !busy, session == nil, pendingRemoval == nil, let id = selectedHomeID, let metadata = records[id] else { return }
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil; needsNewInvite = false
        phase = .checkingSavedAccess
        operation = Task {
            defer { settleOperation(token) }
            do {
                let credential: SavedDeviceCredential
                do { credential = try await store.load(binding: metadata.binding, trust: metadata.trust) }
                catch {
                    guard lifetime == token else { return }
                    needsReplacementConsent = true
                    rePairHomeIDs.insert(id)
                    phase = .keychain((error as? SavedAccessFailure) ?? .unavailable); return
                }
                try check(token)
                guard credential.networkProfileID == id, credential.binding == metadata.binding,
                      credential.trust == metadata.trust else { throw HomeConnectionFailure.identityUnavailable }
                rePairHomeIDs.remove(id)
                needsReplacementConsent = !discoveryFailures.isEmpty
                let candidate = makeSession(); session = candidate; target = credential.trust
                activeCredential = credential
                phase = .joining
                try await candidate.join(trust: credential.trust, profileID: id, key: nil)
                try check(token)
                try await open(candidate, credential: credential, token: token)
            } catch { await fail((error as? HomeConnectionFailure) ?? .homeUnavailable, token: token) }
        }
    }

    /// A storage write/readback is one uninterruptible settlement. Cancel may
    /// invalidate its lifetime, but owns the observed outcome until this returns.
    private func saveDelivered(token: Lifetime) async {
        guard lifetime == token, let credential = unsaved else { return }
        let previousPhase = phase
        phase = .saving; saving = token
        defer {
            saving = nil; cancelledSave = nil
            if lifetime == token {
                if previousPhase == .connected || previousPhase == .opening {
                    phase = .opening
                    if let loadedURL { webLoaded(url: loadedURL, lifetime: token) }
                } else if previousPhase != .awaitingApproval { phase = previousPhase }
            }
        }
        let write = Task { try await store.save(credential) }
        do {
            try await write.value
            guard lifetime == token || cancelledSave == token else { return }
            retainSaved(credential)
        } catch {
            guard lifetime == token || cancelledSave == token else { return }
            saveFailure = (error as? SavedAccessFailure) == .identityChanged ? .identityChanged : .saveFailed
        }
    }

    private func retainSaved(_ credential: SavedDeviceCredential) {
        guard let id = credential.networkProfileID else { return }
        records[id] = credential
        savedHomes.removeAll { $0.id == id }
        savedHomes.append(SavedHome(id: id, trust: credential.trust, binding: credential.binding))
        selectedHomeID = id; saveFailure = nil; unsaved = nil
        needsReplacementConsent = false
    }

    private func refreshAddress(_ saved: SavedDeviceCredential, to trust: SavedHomeTrust,
                                token: Lifetime) async throws -> SavedDeviceCredential {
        try check(token)
        phase = .saving; saving = token
        defer { saving = nil; cancelledSave = nil }
        let write = Task { try await store.refreshAddress(from: saved, to: trust) }
        let updated: SavedDeviceCredential
        do { updated = try await write.value }
        catch {
            if lifetime == token || cancelledSave == token {
                saveFailure = (error as? SavedAccessFailure) == .identityChanged ? .identityChanged : .saveFailed
            }
            throw error
        }
        if lifetime == token || cancelledSave == token { retainSaved(updated) }
        try check(token)
        return updated
    }

    func retrySave(replacing: Bool = false) {
        guard operation == nil, let delivered = unsaved else { return }
        let token = lifetime
        operation = Task {
            defer { settleOperation(token) }
            if replacing {
                // Called only after fresh approval/delivery and explicit UI consent.
                do {
                    try await store.delete(binding: delivered.binding)
                    try check(token)
                } catch {
                    guard lifetime == token else { return }
                    saveFailure = .deletionFailed; return
                }
            }
            await saveDelivered(token: token)
        }
    }

    private func open(_ candidate: any HomeInviteSession, credential: SavedDeviceCredential, token: Lifetime) async throws {
        try check(token)
        activeCredential = credential
        loadedURL = nil
        phase = .opening
        let url = try await candidate.open(credential)
        try check(token)
        accessObservation = AccessObservation(access: .allowed, observedAt: Date(), lifetime: token)
        webURL = url
        navigationDeadline?.cancel()
        navigationDeadline = Task {
            do { try await Task.sleep(for: .seconds(10)) } catch { return }
            guard lifetime == token, loadedURL == nil,
                  phase == .opening || saving == token else { return }
            cancel(endingAt: .failed(.homeUnavailable))
        }
    }

    /// Called only for the expected main-frame 2xx navigation, after connector
    /// authorization and release-verifier admission. Health alone cannot call it.
    func webLoaded(url: URL, lifetime context: Lifetime) {
        guard lifetime == context, webURL == url,
              phase == .opening || (phase == .saving && saving == context) else { return }
        navigationDeadline?.cancel(); navigationDeadline = nil
        loadedURL = url
        // Retry save does not navigate again. Retain this native observation
        // through its storage settlement, then release the same load gate.
        guard phase == .opening, confirmation == nil else { return }
        phase = .connected
        monitor?.cancel()
        let token = context
        monitor = Task {
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(5))
                    try check(token)
                    guard let session else { return }
                    try await session.checkAccess()
                    try check(token)
                    accessObservation = AccessObservation(access: .allowed, observedAt: Date(), lifetime: token)
                } catch {
                    guard lifetime == token, !Task.isCancelled else { return }
                    let failure = (error as? HomeConnectionFailure) ?? .homeUnavailable
                    cancel(endingAt: .failed(failure))
                    // Bind the authoritative denial to the settled local owner.
                    accessObservation = AccessObservation(access: failure == .revoked ? .revokedConfirmed :
                        (failure == .approvalDenied ? .accessDenied : .unknown), observedAt: Date(), lifetime: lifetime)
                    return
                }
            }
        }
    }

    func webFailed(url: URL, lifetime context: Lifetime) {
        guard lifetime == context, webURL == url else { return }
        cancel(endingAt: .failed(.homeUnavailable))
    }

    /// One native confirmation at a time. Dismissal can release only an already
    /// observed native load, never infer Connected from join or health success.
    func beginConfirmation(for context: Lifetime) -> Bool {
        guard lifetime == context, confirmation == nil else { return false }
        confirmation = context
        return true
    }

    func endConfirmation(for context: Lifetime) {
        guard lifetime == context, confirmation == context else { return }
        confirmation = nil
        if let loadedURL { webLoaded(url: loadedURL, lifetime: context) }
    }

    /// The document owner calls this when replacing/losing its admitted document.
    /// 3b-2 will bind this seam to the static connection document's generation.
    func documentDidChange() {
        documentGeneration = UUID()
        cancel()
    }

    private func check(_ token: Lifetime) throws {
        try Task.checkCancellation()
        guard lifetime == token else { throw CancellationError() }
    }

    private func armExpiry(_ expiry: Date, token: Lifetime) {
        expiryTask?.cancel()
        expiryTask = Task {
            do { try await Task.sleep(for: .seconds(max(0, expiry.timeIntervalSinceNow))) } catch { return }
            guard lifetime == token else { return }
            cancel(endingAt: .failed(.expiredInvite))
        }
    }

    private func fail(_ failure: HomeConnectionFailure, token: Lifetime) async {
        // Cancellation owner changes attemptID and performs its own settlement.
        guard lifetime == token else { return }
        expiryTask?.cancel(); expiryTask = nil
        expiresAt = nil; comparisonPhrase = nil; webURL = nil
        if failure == .revoked {
            pendingRemoval = activeCredential
            unsaved = nil; saveFailure = nil
        }
        let candidate = session
        do {
            try await candidate?.stop()
            guard lifetime == token else { return }
            session = nil; phase = .failed(failure)
        } catch {
            guard lifetime == token else { return }
            phase = .failed(.cleanupIncomplete)
        }
        if failure == .approvalDenied || failure == .revoked || failure == .identityUnavailable { needsNewInvite = true }
        if failure == .approvalDenied || failure == .identityUnavailable, let id = activeCredential?.networkProfileID {
            rePairHomeIDs.insert(id); needsReplacementConsent = true
        }
    }

    /// Disconnect retains the Keychain record and native membership. Cancel
    /// invalidates every late result and forgets pending secrets/handles.
    func cancel(endingAt finalPhase: Phase = .idle) {
        let old = operation
        // Do not interrupt Keychain create/update + readback. Fencing is
        // immediate; teardown waits for its independently observed result.
        if let saving { cancelledSave = saving }
        else { old?.cancel() }
        if finalPhase == .failed(.revoked) { pendingRemoval = activeCredential }
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil
        expiryTask?.cancel(); monitor?.cancel(); navigationDeadline?.cancel()
        expiresAt = nil; comparisonPhrase = nil; webURL = nil; unsaved = nil
        if finalPhase == .failed(.revoked) { saveFailure = nil }
        if let pendingRemoval {
            target = pendingRemoval.trust
            removalFailures = ["Local removal was interrupted; Retry removal for this computer"]
        }
        phase = .stopping
        operation = Task {
            await old?.value
            guard lifetime == token else { return }
            do {
                let candidate = session
                try await candidate?.stop()
                guard lifetime == token else { return }
                session = nil
                if finalPhase != .failed(.revoked) { activeCredential = nil }
                if pendingRemovalErased, let pendingRemoval {
                    finishRemoval(pendingRemoval)
                }
                phase = pendingRemoval == nil ? finalPhase : .failed(.cleanupIncomplete)
            }
            catch {
                guard lifetime == token else { return }
                phase = .failed(.cleanupIncomplete)
            }
            guard lifetime == token else { return }
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

    private func settleOperation(_ token: Lifetime) {
        guard lifetime == token else { return }
        operation = nil
        if phase == .failed(.revoked) { removeSavedComputer(afterRevocation: true) }
    }

    func removeSavedComputer(afterRevocation: Bool = false) {
        guard operation == nil else { return }
        let selected = selectedHomeID.flatMap { records[$0] }
        guard let credential = pendingRemoval ?? (afterRevocation ? activeCredential : selected),
              let id = credential.networkProfileID else { return }
        pendingRemoval = credential
        pendingRemovalErased = false
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil
        monitor?.cancel(); navigationDeadline?.cancel(); expiryTask?.cancel()
        webURL = nil; unsaved = nil; comparisonPhrase = nil; expiresAt = nil
        phase = .stopping; removalFailures = []
        operation = Task {
            defer { if lifetime == token { operation = nil } }
            do {
                let candidate = session
                try await candidate?.stop()
                try check(token)
                session = nil
            } catch {
                guard lifetime == token else { return }
                removalFailures = ["Local session could not be confirmed stopped"]; phase = .failed(.cleanupIncomplete); return
            }
            do {
                try await Task.detached { try HomeNetworkState.remove(id) }.value
                try check(token)
            } catch {
                guard lifetime == token else { return }
                removalFailures = ["Saved network state could not be erased", "Login Keychain access retained for Retry removal"]
                phase = .failed(.cleanupIncomplete); return
            }
            do {
                try await store.delete(binding: credential.binding)
                // Retain only an observed result for this captured target.
                // A newer cancellation owner reconciles it after awaiting us;
                // this stale operation still cannot publish state or reconnect.
                if pendingRemoval?.binding == credential.binding,
                   pendingRemoval?.networkProfileID == id { pendingRemovalErased = true }
                try check(token)
            } catch {
                guard lifetime == token else { return }
                removalFailures = ["Login Keychain bearer and trust could not be erased"]; phase = .keychain(.deletionFailed); return
            }
            finishRemoval(credential)
            phase = afterRevocation ? .failed(.revoked) : .idle
            if afterRevocation { needsNewInvite = true }
        }
    }

    private func finishRemoval(_ credential: SavedDeviceCredential) {
        guard let id = credential.networkProfileID,
              pendingRemoval?.binding == credential.binding,
              pendingRemoval?.networkProfileID == id else { return }
        records.removeValue(forKey: id); savedHomes.removeAll { $0.id == id }
        if selectedHomeID == id { selectedHomeID = savedHomes.first?.id }
        if activeCredential?.binding == credential.binding { activeCredential = nil }
        pendingRemoval = nil; pendingRemovalErased = false
        removalFailures = []; target = nil; saveFailure = nil
    }
}
