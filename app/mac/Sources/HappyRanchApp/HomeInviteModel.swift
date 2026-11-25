import Foundation
import Combine
import HappyRanchSupervisor

/// The only home-client UI state. No secret is published or restored.
@MainActor
final class HomeInviteModel: ObservableObject {
    enum Phase: Equatable {
        case idle, checkingSavedAccess, validating, joining, pairing, awaitingApproval, saving, opening, readyToSwitch, connected, runtimeBusy, stopping
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
            case .readyToSwitch: return Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "可以切换" : "Ready to switch"
            case .connected: return "Connected"
            case .runtimeBusy: return Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "忙碌" : "Busy"
            case .stopping: return "Closing local session; waiting for any storage write/readback to settle"
            case .failed(let failure): return failure.localizedDescription
            case .keychain(let failure): return failure.localizedDescription
            }
        }
        var busy: Bool { [.checkingSavedAccess, .validating, .joining, .pairing, .awaitingApproval, .saving, .opening, .runtimeBusy, .stopping].contains(self) }
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
    @Published private(set) var appReadiness: AppAPIHealth?
    @Published private(set) var savedHomes: [SavedHome] = []
    @Published private var candidateSaveFailure: SavedAccessFailure?
    var saveFailure: SavedAccessFailure? {
        if let active { return active.saveFailure }
        return candidateSaveFailure ?? unconfirmedSaves.first?.failure
    }
    @Published private(set) var removalFailures: [String] = []
    @Published private(set) var discoveryFailures: [SavedAccessFailure] = []
    @Published private(set) var discoveryComplete = false
    @Published private(set) var accessObservation: AccessObservation?
    @Published private(set) var candidateID: UUID?
    @Published private(set) var candidateOutcome: String?
    @Published private(set) var cleanupOutcomes: [UUID: String] = [:]
    @Published private(set) var selectedFailure: HomeConnectionFailure?
    let connectionStates = ConnectionStateManager()
    @Published var selectedHomeID: UUID?
    private(set) var attemptID = UUID()
    @Published private(set) var documentGeneration = UUID()
    var lifetime: Lifetime { Lifetime(attempt: attemptID, document: documentGeneration) }
    var appTransport: (any AppAPITransport)? { try? active?.session.appTransport() }
    var activeID: UUID? { active?.id }
    var activeHasUnsavedAccess: Bool { active?.unsaved != nil }
    // Outcome evidence survives session/credential teardown. Only a protected
    // save/readback or confirmed removal of that binding settles its warning.
    private struct UnconfirmedSave {
        let binding: SavedDeviceBinding
        let failure: SavedAccessFailure
    }
    @Published private var unconfirmedSaves: [UnconfirmedSave] = []
    var hasUnsavedAccessWarning: Bool { !unconfirmedSaves.isEmpty || activeHasUnsavedAccess }
    var hasActiveSession: Bool { active != nil }
    var bridgeAccessObservation: AccessObservation? {
        if let active { return active.observation }
        return accessObservation?.lifetime == lifetime ? accessObservation : nil
    }
    var bridgeRuntimeAvailable: Bool { active != nil && appReadiness?.state == "ready" }
    private final class ActiveSession {
        let id: UUID
        let session: any HomeInviteSession
        let credential: SavedDeviceCredential
        var unsaved: SavedDeviceCredential?
        var saveFailure: SavedAccessFailure?
        var observation: AccessObservation
        init(id: UUID, session: any HomeInviteSession, credential: SavedDeviceCredential,
             unsaved: SavedDeviceCredential?, saveFailure: SavedAccessFailure?, observation: AccessObservation) {
            self.id = id; self.session = session; self.credential = credential
            self.unsaved = unsaved; self.saveFailure = saveFailure
            self.observation = observation
        }
    }
    private var active: ActiveSession?
    // Failed/unknown cleanup keeps its original owner alive and blocks identity reuse.
    private var retiring: [UUID: ActiveSession] = [:]
    private var revokedRetiring: [UUID: ActiveSession] = [:]
    private var retirementTasks: [UUID: Task<Void, Never>] = [:]
    private var retiringBridges: [UUID: ClientBridge] = [:]
    private var candidateHealth: AppAPIHealth?
    private var mainDocumentLoaded = false
    private var confirmation: Lifetime?
    private var loadedURL: URL?
    private var saving: Lifetime?
    private var cancelledSave: Lifetime?
    private var operation: Task<Void, Never>?
    private var expiryTask: Task<Void, Never>?
    private var navigationDeadline: Task<Void, Never>?
    private var monitor: Task<Void, Never>?
    private var candidateMonitor: Task<Void, Never>?
    private var session: (any HomeInviteSession)?
    private let store: any SavedHomeCredentialStore
    private let makeSession: @Sendable () -> any HomeInviteSession
    private var records: [UUID: SavedDeviceCredential] = [:]
    private var unsaved: SavedDeviceCredential?
    private var activeCredential: SavedDeviceCredential?
    private var pendingRemoval: SavedDeviceCredential?
    private var pendingRemovalErased = false
    private var pendingRemovalNetworkErased = false
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

    var busy: Bool { (phase.busy && phase != .runtimeBusy) || operation != nil }
    // Nonsecret observations only; no read or discovery is initiated here.
    var bridgeActiveComputerID: UUID? { active?.credential.networkProfileID ?? activeCredential?.networkProfileID }
    var bridgeHasUnsavedAccess: Bool { unsaved != nil || active?.unsaved != nil }
    var bridgeHasConfirmation: Bool { confirmation != nil }
    var bridgeCanAccept: Bool { !busy && session == nil && pendingRemoval == nil }
    var bridgeCanCancel: Bool {
        pendingRemoval == nil && [.joining, .pairing, .awaitingApproval, .saving, .opening, .readyToSwitch].contains(phase)
    }
    var bridgeCandidate: [String: Any]? {
        guard let id = candidateID else { return nil }
        let stage: String
        if let candidateOutcome { stage = candidateOutcome }
        else {
            switch phase {
            case .readyToSwitch: stage = "ready_to_switch"
            case .awaitingApproval: stage = "awaiting_approval"
            case .failed, .keychain: stage = "failed"
            default: stage = "signing_in"
            }
        }
        let zh = Locale.preferredLanguages.first?.hasPrefix("zh") == true
        return ["kind": "remote", "id": id.uuidString, "name": zh ? "远程电脑" : "Remote computer",
                "phase": stage, "deadline": expiresAt.map(ConnectionClientProjection.timestamp) as Any? ?? NSNull()]
    }

    private func identityInUse(home: String, profile: UUID? = nil) -> Bool {
        ([active].compactMap { $0 } + Array(retiring.values)).contains {
            $0.credential.trust.homeID == home || (profile != nil && $0.credential.networkProfileID == profile)
        }
    }
    func bridgeCanConnect(_ home: SavedHome) -> Bool {
        bridgeCanAccept && !identityInUse(home: home.trust.homeID, profile: home.id)
    }
    func bridgeIsSelected(_ home: SavedHome) -> Bool { active?.credential.networkProfileID == home.id }
    func waitForCleanup(id: UUID) async -> Bool {
        await retirementTasks[id]?.value
        return cleanupOutcomes[id] == "confirmed"
    }

    private func beginCandidate() {
        candidateID = UUID(); candidateOutcome = nil; candidateHealth = nil
        unsaved = nil; candidateSaveFailure = nil; activeCredential = nil
        connectionStates.beginCandidate(id: candidateID!)
    }

    /// Only the Main scheme's synchronous owner-gated commit calls this.
    /// It does not await teardown or infer selection from readiness.
    @discardableResult
    func commitCandidate() throws -> AppScheme.Target {
        guard operation == nil, saving == nil, confirmation == nil, phase == .readyToSwitch,
              let id = candidateID, let session, let credential = activeCredential,
              let health = candidateHealth, health.state == "ready" else { throw AppAPIError.unavailable }
        let transport = try session.appTransport()
        let previous = active
        active = ActiveSession(id: id, session: session, credential: credential, unsaved: unsaved, saveFailure: candidateSaveFailure,
            observation: AccessObservation(access: .allowed, observedAt: Date(), lifetime: lifetime))
        self.session = nil; candidateID = nil; candidateOutcome = nil
        candidateMonitor?.cancel(); candidateMonitor = nil
        appReadiness = health; phase = .connected
        selectedFailure = nil
        connectionStates.commitCandidate(id: id)
        startActiveMonitor()
        if let previous { retire(previous) }
        return AppScheme.Target(id: id.uuidString, transport: transport, remote: true,
                                health: health, accessUnsaved: hasUnsavedAccessWarning)
    }

    /// Admission belongs to AppScheme; storage/attempt settlement must finish first.
    var canDisconnect: Bool { operation == nil && saving == nil }
    func disconnectActive() {
        guard canDisconnect else { return }
        let previous = active; active = nil
        monitor?.cancel(); monitor = nil; appReadiness = nil
        connectionStates.disconnectActive()
        if candidateID == nil { phase = .idle; activeCredential = nil; unsaved = nil; candidateSaveFailure = nil }
        else if candidateOutcome != nil { phase = .idle }
        if let previous { retire(previous) }
    }

    private func retire(_ previous: ActiveSession, revoked: Bool = false) {
        guard retirementTasks[previous.id] == nil else { return }
        retiring[previous.id] = previous; cleanupOutcomes[previous.id] = "unknown"
        if revoked { revokedRetiring[previous.id] = previous }
        retirementTasks[previous.id] = Task {
            do {
                try await previous.session.stop()
                if revoked {
                    guard let profile = previous.credential.networkProfileID else { throw HomeConnectionFailure.cleanupIncomplete }
                    try await Task.detached { try HomeNetworkState.remove(profile) }.value
                    try await store.delete(binding: previous.credential.binding)
                    unconfirmedSaves.removeAll { $0.binding == previous.credential.binding }
                    records.removeValue(forKey: profile); savedHomes.removeAll { $0.id == profile }
                    if selectedHomeID == profile { selectedHomeID = savedHomes.first?.id }
                    if candidateID == nil, target == previous.credential.trust { target = nil }
                    revokedRetiring.removeValue(forKey: previous.id)
                }
                retiring.removeValue(forKey: previous.id); cleanupOutcomes[previous.id] = "confirmed"
            } catch { cleanupOutcomes[previous.id] = "incomplete" }
            retirementTasks.removeValue(forKey: previous.id)
        }
    }
    func retireBridge(_ bridge: ClientBridge) {
        let id = UUID()
        retiringBridges[id] = bridge; cleanupOutcomes[id] = "unknown"
        retirementTasks[id] = Task {
            await bridge.stopManagedNetwork()
            if bridge.lane != .managed || bridge.managedStopConfirmed {
                cleanupOutcomes[id] = "confirmed"; retiringBridges.removeValue(forKey: id)
            } else { cleanupOutcomes[id] = "incomplete" }
            retirementTasks.removeValue(forKey: id)
        }
    }
    var bridgeRemovalTarget: SavedHome? {
        let revoked = revokedRetiring.keys.sorted { $0.uuidString < $1.uuidString }.first.flatMap { revokedRetiring[$0]?.credential }
        guard let credential = pendingRemoval ?? revoked, let id = credential.networkProfileID else { return nil }
        return SavedHome(id: id, trust: credential.trust, binding: credential.binding)
    }
    var bridgeReplacementTarget: SavedHome? {
        guard let credential = active?.unsaved ?? unsaved, let id = credential.networkProfileID else { return nil }
        return SavedHome(id: id, trust: credential.trust, binding: credential.binding)
    }
    var bridgeNeedsNewInvite: Bool { needsNewInvite }
    var bridgeCanReplace: Bool {
        !busy && (retryingActiveSave ? active?.saveFailure == .identityChanged :
                  unsaved != nil && candidateSaveFailure == .identityChanged)
    }
    private var retryingActiveSave: Bool { active?.unsaved != nil }
    var bridgeCanRetrySave: Bool {
        !busy && (retryingActiveSave ? active?.saveFailure != .identityChanged :
                  unsaved != nil && !needsReplacementConsent && candidateSaveFailure != .identityChanged)
    }

    private func loadSaved(_ credential: SavedDeviceCredential, token: Lifetime) async throws -> SavedDeviceCredential {
        // SecItem provides only a final result. No OS prompt observation exists.
        // Keep the same safe point as writes; OS Cancel is a storage outcome.
        let previous = phase
        phase = .saving; saving = token
        defer {
            saving = nil; cancelledSave = nil
            if lifetime == token { phase = previous }
        }
        let read = Task { try await store.load(binding: credential.binding, trust: credential.trust) }
        let saved = try await read.value
        try check(token)
        guard saved.binding == credential.binding, saved.networkProfileID == credential.networkProfileID,
              saved.trust == credential.trust,
              saved.withBearer({ old in credential.withBearer { old == $0 } }) else {
            throw SavedAccessFailure.identityChanged
        }
        return saved
    }

    /// Wait for the real native operation, including cancellation safe points.
    /// Opening still waits for the existing native release-UI load callback.
    func bridgeSettle() async {
        while let current = operation { await current.value }
        while phase == .opening {
            try? await Task.sleep(for: .milliseconds(20))
            if let current = operation { await current.value }
        }
    }

    /// Reuse the existing save path and await its observed write/readback. This
    /// never invokes the explicit replacement/delete path or guesses completion
    /// from Task scheduling. OS Keychain UI does not own the app prompt slot.
    func bridgeRetrySave(replacing: Bool = false) async -> SaveSettlement {
        guard replacing ? bridgeCanReplace : bridgeCanRetrySave else { return .refused }
        let selected = retryingActiveSave ? active : nil
        let token = lifetime
        retrySave(replacing: replacing)
        guard let operation else { return .refused }
        await operation.value
        guard lifetime == token else { return .changed }
        if let selected {
            guard active === selected else { return .changed }
            if let failure = selected.saveFailure { return .failed(failure) }
            return selected.unsaved == nil ? .saved : .failed(.saveFailed)
        }
        if let candidateSaveFailure { return .failed(candidateSaveFailure) }
        return unsaved == nil ? .saved : .failed(.saveFailed)
    }
    var retryGuidance: String {
        if saveFailure != nil, !bridgeHasUnsavedAccess { return "Saving was not confirmed. Retry checks saved access again; a new invite may be needed." }
        if saveFailure != nil { return "Retry saving while this session is open. If the app quits before saving, create a new invite on Linux." }
        return needsNewInvite ? "Create a new invite on Linux. Retry accepts a new invite; the spent key is never reused." :
            "Connect to your home network / Retry. Saved access needs no new invite while its grant remains valid."
    }

    func prepare(reconnectSingle: Bool = true) {
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
                if reconnectSingle, savedHomes.count == 1 { reconnect() }
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
        beginCandidate()
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
                guard !identityInUse(home: trust.homeID) else { throw HomeConnectionFailure.identityUnavailable }
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
                            let saved = try await loadSaved(previous, token: token)
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
        guard !identityInUse(home: metadata.trust.homeID, profile: id) else { return }
        beginCandidate()
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil; needsNewInvite = false
        target = metadata.trust
        phase = .checkingSavedAccess
        operation = Task {
            defer { settleOperation(token) }
            do {
                let credential: SavedDeviceCredential
                do { credential = try await loadSaved(metadata, token: token) }
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
                if previousPhase == .connected || previousPhase == .runtimeBusy {
                    active?.unsaved = unsaved; active?.saveFailure = candidateSaveFailure
                    phase = previousPhase
                } else if previousPhase == .opening || (previousPhase == .readyToSwitch && confirmation != nil) {
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
            let failure = (error as? SavedAccessFailure) ?? .saveFailed
            candidateSaveFailure = failure
            unconfirmedSaves.removeAll { $0.binding == credential.binding }
            unconfirmedSaves.append(UnconfirmedSave(binding: credential.binding, failure: failure))
        }
    }

    private func retainSaved(_ credential: SavedDeviceCredential) {
        unconfirmedSaves.removeAll { $0.binding == credential.binding }
        guard let id = credential.networkProfileID else { return }
        records[id] = credential
        savedHomes.removeAll { $0.id == id }
        savedHomes.append(SavedHome(id: id, trust: credential.trust, binding: credential.binding))
        selectedHomeID = id; candidateSaveFailure = nil; unsaved = nil
        needsReplacementConsent = false
        if candidateID == nil, active?.credential.binding == credential.binding {
            active?.unsaved = nil; active?.saveFailure = nil
        }
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
                candidateSaveFailure = (error as? SavedAccessFailure) ?? .saveFailed
            }
            throw error
        }
        if lifetime == token || cancelledSave == token { retainSaved(updated) }
        try check(token)
        return updated
    }

    func retrySave(replacing: Bool = false) {
        if retryingActiveSave, operation == nil, let selected = active, let credential = selected.unsaved {
            let token = lifetime
            saving = token
            operation = Task {
                defer { saving = nil; cancelledSave = nil; settleOperation(token) }
                // Keep the selected owner's outcome independent of candidate Cancel.
                do {
                    if replacing {
                        do { try await store.delete(binding: credential.binding) }
                        catch { selected.saveFailure = .deletionFailed; objectWillChange.send(); return }
                        guard lifetime == token else {
                            selected.saveFailure = .deletionFailed; objectWillChange.send(); return
                        }
                    }
                    try await store.save(credential)
                    guard active === selected else { return }
                    selected.unsaved = nil; selected.saveFailure = nil
                    unconfirmedSaves.removeAll { $0.binding == credential.binding }
                    if let id = credential.networkProfileID {
                        records[id] = credential
                        savedHomes.removeAll { $0.id == id }
                        savedHomes.append(SavedHome(id: id, trust: credential.trust, binding: credential.binding))
                    }
                } catch {
                    guard active === selected else { return }
                    selected.saveFailure = (error as? SavedAccessFailure) ?? .saveFailed
                }
                objectWillChange.send()
            }
            return
        }
        guard operation == nil, let delivered = unsaved else { return }
        let token = lifetime
        operation = Task {
            defer { settleOperation(token) }
            if replacing {
                // Called only after fresh approval/delivery and explicit UI consent.
                let previous = phase
                phase = .saving; saving = token
                defer {
                    saving = nil; cancelledSave = nil
                    if lifetime == token, phase == .saving { phase = previous }
                }
                do {
                    try await store.delete(binding: delivered.binding)
                    if lifetime != token, cancelledSave == token {
                        // Deletion settled, replacement did not. Retain the
                        // partial effect; Cancel cannot claim complete cleanup.
                        candidateSaveFailure = .deletionFailed
                        return
                    }
                    try check(token)
                } catch {
                    guard lifetime == token || cancelledSave == token else { return }
                    candidateSaveFailure = .deletionFailed; return
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
        let url = try await candidate.openApp(credential)
        try check(token)
        let health = try await AppAPIHealth.read(from: candidate.appTransport())
        try health.requireReady(); try check(token)
        candidateHealth = health
        startCandidateMonitor(token: token, candidate: candidate)
        accessObservation = AccessObservation(access: .allowed, observedAt: Date(), lifetime: token)
        // The first load admits the release document. Later candidates reuse it.
        webURL = url
        if mainDocumentLoaded { phase = .readyToSwitch; return }
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
    func mainDocumentAdmitted() { mainDocumentLoaded = true }

    func webLoaded(url: URL, lifetime context: Lifetime) {
        guard lifetime == context, webURL == url,
              phase == .opening || (phase == .saving && saving == context) else { return }
        navigationDeadline?.cancel(); navigationDeadline = nil
        loadedURL = url
        // Retry save does not navigate again. Retain this native observation
        // through its storage settlement, then release the same load gate.
        guard phase == .opening, confirmation == nil else { return }
        mainDocumentLoaded = true
        phase = .readyToSwitch
    }

    private func startCandidateMonitor(token: Lifetime, candidate: any HomeInviteSession) {
        candidateMonitor?.cancel()
        candidateMonitor = Task {
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(5))
                    try check(token)
                    guard candidateID != nil, session != nil else { return }
                    try await candidate.checkAccess()
                    let health = try await AppAPIHealth.read(from: candidate.appTransport())
                    try health.requireReady(); try check(token)
                    candidateHealth = health
                } catch {
                    guard lifetime == token, candidateID != nil, !Task.isCancelled else { return }
                    await fail((error as? HomeConnectionFailure) ?? .homeUnavailable, token: token)
                    return
                }
            }
        }
    }

    private func startActiveMonitor() {
        monitor?.cancel()
        guard let selected = active else { return }
        monitor = Task {
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(5))
                    guard active === selected else { return }
                    try await selected.session.checkAccess()
                    // Busy includes ordinary active work and bounded-unknown
                    // update observations. Preserve the admitted document and
                    // transfers, publish busy, then recheck without replay.
                    let health = try await AppAPIHealth.read(from: selected.session.appTransport())
                    if health.state != "busy" { try health.requireReady() }
                    guard active === selected, !Task.isCancelled else { return }
                    appReadiness = health
                    selected.observation = AccessObservation(access: .allowed, observedAt: Date(),
                        lifetime: Lifetime(attempt: selected.id, document: documentGeneration))
                    connectionStates.observeActive(id: selected.id, state: health.state == "busy" ? .offline : .online)
                    if (candidateID == nil || candidateOutcome != nil) && (phase == .connected || phase == .runtimeBusy) {
                        phase = health.state == "busy" ? .runtimeBusy : .connected
                    }
                    if candidateID == nil { accessObservation = AccessObservation(access: .allowed, observedAt: Date(), lifetime: lifetime) }
                } catch {
                    guard active === selected, !Task.isCancelled else { return }
                    let failure = (error as? HomeConnectionFailure) ?? .homeUnavailable
                    active = nil; appReadiness = nil; connectionStates.disconnectActive()
                    selectedFailure = failure
                    if failure == .revoked { needsNewInvite = true }
                    retire(selected, revoked: failure == .revoked)
                    if candidateID == nil {
                        attemptID = UUID(); webURL = nil; phase = .failed(failure)
                        activeCredential = nil; unsaved = nil
                        if failure == .revoked { candidateSaveFailure = nil }
                        // The denial belongs to A, never to a concurrent B attempt.
                        accessObservation = AccessObservation(access: failure == .revoked ? .revokedConfirmed :
                            (failure == .approvalDenied ? .accessDenied : .unknown), observedAt: Date(), lifetime: lifetime)
                    }
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
        mainDocumentLoaded = false
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
        expiresAt = nil; comparisonPhrase = nil
        webURL = nil
        candidateOutcome = "failed"; connectionStates.cancelCandidate()
        if failure == .revoked {
            pendingRemoval = activeCredential
            unsaved = nil; candidateSaveFailure = nil
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

    /// Candidate-only Cancel invalidates late attempt results. Selected-owner
    /// Disconnect must enter through Main's owner/transfer gate.
    func cancel(endingAt finalPhase: Phase = .idle) {
        let old = operation
        // Do not interrupt Keychain create/update + readback. Fencing is
        // immediate; teardown waits for its independently observed result.
        if let saving { cancelledSave = saving }
        else { old?.cancel() }
        if finalPhase == .failed(.revoked) { pendingRemoval = activeCredential }
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil
        expiryTask?.cancel(); navigationDeadline?.cancel(); candidateMonitor?.cancel(); candidateMonitor = nil
        expiresAt = nil; comparisonPhrase = nil; unsaved = nil
        webURL = nil
        if candidateID != nil { candidateOutcome = finalPhase == .idle ? "cancelled" : "failed" }
        connectionStates.cancelCandidate()
        if finalPhase == .failed(.revoked) { candidateSaveFailure = nil }
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
                phase = pendingRemoval == nil ? (finalPhase == .idle && active != nil ?
                    (appReadiness?.state == "busy" ? .runtimeBusy : .connected) : finalPhase) : .failed(.cleanupIncomplete)
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

    /// Retained native controls use Main's gate before releasing selected custody.
    func disconnectSelected(using scheme: AppScheme?) async -> String? {
        guard let scheme else { return "owner_save_unknown" }
        let selected = activeID
        if let reason = scheme.disconnect() { return reason }
        if let selected, !(await waitForCleanup(id: selected)) { return "removalIncomplete" }
        return nil
    }

    func removeSavedSelection(using scheme: AppScheme?) async -> String? {
        guard !busy, let target = bridgeRemovalTarget ?? savedHomes.first(where: { $0.id == selectedHomeID }) else { return "stateChanged" }
        let context = lifetime
        if bridgeIsSelected(target) {
            if let reason = await disconnectSelected(using: scheme) { return reason }
            guard lifetime == context, self.selectedHomeID == target.id else { return "stateChanged" }
        }
        removeSavedComputer(expected: target)
        await bridgeSettle()
        guard bridgeRemovalTarget == nil, !savedHomes.contains(target), removalFailures.isEmpty else { return "removalIncomplete" }
        return nil
    }

    /// A cancellation result belongs to the captured candidate, not the phase
    /// of a different selected owner that remains connected.
    func cancelAttempt() async -> Bool {
        guard let candidate = candidateID, bridgeCanCancel else { return false }
        cancel()
        let stopped = lifetime
        await bridgeSettle()
        return lifetime == stopped && candidateID == candidate && session == nil &&
            candidateOutcome == "cancelled" && phase != .failed(.cleanupIncomplete) &&
            removalFailures.isEmpty && candidateSaveFailure != .deletionFailed
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

    func removeSavedComputer(afterRevocation: Bool = false, expected: SavedHome? = nil) {
        guard operation == nil else { return }
        if let expected, let previous = revokedRetiring.values.first(where: {
            $0.credential.networkProfileID == expected.id && $0.credential.binding == expected.binding && $0.credential.trust == expected.trust
        }) {
            retire(previous, revoked: true)
            let token = lifetime
            operation = Task {
                await retirementTasks[previous.id]?.value
                guard lifetime == token else { return }
                if candidateID == nil { phase = cleanupOutcomes[previous.id] == "confirmed" ? .failed(.revoked) : .failed(.cleanupIncomplete) }
                operation = nil
            }
            return
        }
        let selected = selectedHomeID.flatMap { records[$0] }
        guard let credential = pendingRemoval ?? (afterRevocation ? activeCredential : selected),
              let id = credential.networkProfileID else { return }
        // The native entry must owner-gate disconnect and confirm cleanup before
        // deleting a selected/retiring node's saved identity.
        guard !identityInUse(home: credential.trust.homeID, profile: id) else { return }
        if let expected {
            guard expected.id == id, expected.binding == credential.binding, expected.trust == credential.trust else { return }
        }
        if pendingRemoval == nil { pendingRemovalNetworkErased = false }
        pendingRemoval = credential
        pendingRemovalErased = false
        attemptID = UUID(); let token = lifetime
        confirmation = nil; loadedURL = nil
        candidateMonitor?.cancel(); navigationDeadline?.cancel(); expiryTask?.cancel()
        webURL = nil
        unsaved = nil; comparisonPhrase = nil; expiresAt = nil
        phase = .stopping; removalFailures = []
        let candidate = session
        operation = Task {
            defer { if lifetime == token { operation = nil } }
            // Local teardown does not depend on Keychain read permission.
            // Keep the captured removal target for retry if custody is unreadable.
            do {
                try await candidate?.stop()
                try check(token)
                session = nil; activeCredential = nil
            } catch {
                guard lifetime == token else { return }
                removalFailures = ["Local session could not be confirmed stopped"]; phase = .failed(.cleanupIncomplete); return
            }
            // The web sheet captured this exact native record. Reread its
            // binding/trust/profile/bearer before the first destructive effect.
            if expected != nil {
                do { _ = try await loadSaved(credential, token: token) }
                catch {
                    guard lifetime == token else { return }
                    // A retry after confirmed native-state erasure may find
                    // Keychain already absent. Delete still verifies absence.
                    if !pendingRemovalNetworkErased || (error as? SavedAccessFailure) != .notFound {
                        removalFailures = ["Saved access changed or could not be read; removal was not confirmed"]
                        phase = .keychain((error as? SavedAccessFailure) ?? .identityChanged)
                        return
                    }
                }
            }
            do {
                try await Task.detached { try HomeNetworkState.remove(id) }.value
                if pendingRemoval?.binding == credential.binding,
                   pendingRemoval?.networkProfileID == id { pendingRemovalNetworkErased = true }
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
        unconfirmedSaves.removeAll { $0.binding == credential.binding }
        if selectedHomeID == id { selectedHomeID = savedHomes.first?.id }
        if activeCredential?.binding == credential.binding { activeCredential = nil }
        pendingRemoval = nil; pendingRemovalErased = false; pendingRemovalNetworkErased = false
        removalFailures = []; target = nil; candidateSaveFailure = nil
    }
}
