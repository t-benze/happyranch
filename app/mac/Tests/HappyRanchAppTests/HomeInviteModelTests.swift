import Foundation
import AppKit
import Combine
import CryptoKit
import Testing
import XCTest
import WebKit
@testable import HappyRanchSupervisor
@testable import HappyRanchApp

/// Deliberately ignores Task cancellation: real dispatched work can return late.
private actor HomeAwaitGate {
    private var point: String?
    private var reached: String?
    private var continuation: CheckedContinuation<Void, Never>?
    func arm(_ point: String) { self.point = point; reached = nil }
    func pause(_ point: String) async {
        guard self.point == point else { return }
        self.point = nil; reached = point
        await withCheckedContinuation { continuation = $0 }
    }
    func waitFor(_ point: String) async -> Bool {
        let deadline = Date().addingTimeInterval(5)
        while reached != point, Date() < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        return reached == point
    }
    func release() { continuation?.resume(); continuation = nil }
}

private actor HomeStoreFixture: SavedHomeCredentialStore {
    let gate: HomeAwaitGate
    init(gate: HomeAwaitGate = HomeAwaitGate()) { self.gate = gate }
    var credentials: [SavedDeviceCredential] = []
    var readFailure: SavedAccessFailure?
    var discoveryFailures: [SavedAccessFailure] = []
    func setDiscoveryFailures(_ failures: [SavedAccessFailure]) { discoveryFailures = failures }
    var saveFailure: SavedAccessFailure?
    func setSaveFailure(_ failure: SavedAccessFailure) { saveFailure = failure }
    var saveFails = false
    var deleteFails = false
    private(set) var refreshes = 0
    private(set) var saves = 0
    private(set) var deletedBindings: [SavedDeviceBinding] = []
    func configure(read: SavedAccessFailure? = nil, saveFails: Bool = false, deleteFails: Bool = false) {
        readFailure = read; self.saveFails = saveFails; self.deleteFails = deleteFails
    }
    func savedHomes() async throws -> SavedHomeDiscovery {
        await gate.pause("discovery")
        if let readFailure { throw readFailure }
        return SavedHomeDiscovery(credentials: credentials, failures: discoveryFailures)
    }
    func save(_ credential: SavedDeviceCredential) async throws {
        saves += 1
        await gate.pause("save")
        if let saveFailure { throw saveFailure }
        if saveFails { throw SavedAccessFailure.saveFailed }
        credentials.removeAll { $0.binding == credential.binding }; credentials.append(credential)
    }
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        await gate.pause("load")
        if let readFailure { throw readFailure }
        guard let credential = credentials.first(where: { $0.binding == binding && $0.trust == trust }) else { throw SavedAccessFailure.notFound }
        return credential
    }
    func refreshAddress(from credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        refreshes += 1
        await gate.pause("refresh")
        if saveFails { throw SavedAccessFailure.saveFailed }
        let updated = try credential.updatingAddress(to: trust)
        credentials.removeAll { $0.binding == credential.binding }; credentials.append(updated)
        return updated
    }
    func delete(binding: SavedDeviceBinding) async throws {
        await gate.pause("delete")
        if deleteFails { throw SavedAccessFailure.deletionFailed }
        deletedBindings.append(binding)
        credentials.removeAll { $0.binding == binding }
    }
}

private final class HomeReadinessFixture: AppAPITransport, @unchecked Sendable {
    private let lock = NSLock()
    private var busy = false
    func setBusy(_ value: Bool) { lock.withLock { busy = value } }
    func appRequest(method: String, target: String, headers: [String: String], body: Data) -> AppAPITransfer {
        let transfer = AppAPITransfer()
        let busy = lock.withLock { self.busy }
        transfer.emit(.response(200, ["content-type": "application/json"]))
        // The bounded-unknown update observation is still busy, not access loss.
        transfer.emit(.bytes(Data("{\"api_contract\":{\"current\":1,\"min_client\":1},\"update_busy\":{\"busy\":\(busy),\"unknown\":\(busy)}}".utf8)))
        transfer.finish(); return transfer
    }
}

/// Uses the suite's existing disposable login Keychain. Only the initial
/// synthetic refusal is injected; successful writes/readbacks use production.
private actor HomeProtectedStoreFixture: SavedHomeCredentialStore {
    let protected = LoginKeychainDeviceStore()
    let gate = HomeAwaitGate()
    private var refuseSave = true
    func allowSave() { refuseSave = false }
    func savedHomes() async throws -> SavedHomeDiscovery { try await protected.savedHomes() }
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        try await protected.load(binding: binding, trust: trust)
    }
    func save(_ credential: SavedDeviceCredential) async throws {
        if refuseSave { throw SavedAccessFailure.saveFailed }
        try await protected.save(credential) // Includes real protected readback.
        await gate.pause("save-return")
    }
    func refreshAddress(from credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        try await protected.refreshAddress(from: credential, to: trust)
    }
    func delete(binding: SavedDeviceBinding) async throws { try await protected.delete(binding: binding) }
}

private actor HomeSessionFixture: HomeInviteSession {
    nonisolated let readiness = HomeReadinessFixture()
    nonisolated func appTransport() throws -> any AppAPITransport { readiness }
    let gate: HomeAwaitGate
    init(gate: HomeAwaitGate = HomeAwaitGate()) { self.gate = gate }
    private(set) var stops = 0
    private var stopFailure: HomeConnectionFailure?
    func failStop() { stopFailure = .cleanupIncomplete }
    private(set) var opens = 0
    var joinFailure: HomeConnectionFailure?
    var accessFailure: HomeConnectionFailure?
    private(set) var recoveries = 0
    private(set) var pairs = 0
    var returnedNode: String?
    func changeReturnedNode(_ node: String) { returnedNode = node }
    private(set) var joins = 0
    private(set) var keyProfiles: [UUID] = []
    private(set) var emptyProfiles: [UUID] = []
    private var keyedMaterials: [Data] = []
    func freshKeysDiffer() -> Bool { keyedMaterials.count == 2 && keyedMaterials[0] != keyedMaterials[1] }
    var rejectEmpty = false
    var suspendJoin = false
    func configureMembership(rejectEmpty: Bool = false, suspendJoin: Bool = false) {
        self.rejectEmpty = rejectEmpty; self.suspendJoin = suspendJoin
    }
    private(set) var emptyKeys = 0
    private(set) var completions = 0
    private var approved = false
    func configure(join: HomeConnectionFailure? = nil, access: HomeConnectionFailure? = nil) {
        joinFailure = join; accessFailure = access
    }
    func approve() { approved = true }
    func resetApproval() { approved = false }
    func join(trust: SavedHomeTrust, profileID: UUID, key: Data?) async throws {
        joins += 1
        await gate.pause("join")
        if key == nil { emptyKeys += 1; emptyProfiles.append(profileID) }
        else { keyProfiles.append(profileID); keyedMaterials.append(key!) }
        if suspendJoin { try await Task.sleep(for: .seconds(30)) }
        if key == nil && rejectEmpty { throw HomeConnectionFailure.identityUnavailable }
        if let joinFailure { throw joinFailure }
    }
    func recoverAddress(_ credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        recoveries += 1
        await gate.pause("recover")
        if let accessFailure { throw accessFailure }
        let updated = try credential.updatingAddress(to: trust)
        if let returnedNode {
            return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
                controlPlaneID: trust.controlPlaneID, stableNodeID: returnedNode), trust: trust,
                deliveredBearer: credential.withBearer { $0 }, networkProfileID: credential.networkProfileID)
        }
        return updated
    }
    func pair(_ invite: HomePairingInvitation) async throws -> PendingHomeApproval {
        pairs += 1
        await gate.pause("pair")
        return try PendingHomeApproval(comparisonPhrase: "pair-1234-abcd-5678-efab", expiresAt: invite.expiresAt,
            completionHandle: String(repeating: "a", count: 64))
    }
    func complete(_ pending: PendingHomeApproval, trust: SavedHomeTrust, profileID: UUID) async throws -> SavedDeviceCredential? {
        completions += 1
        await gate.pause("complete")
        guard approved else { return nil }
        return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
            controlPlaneID: trust.controlPlaneID, stableNodeID: "synthetic-stable-node"), trust: trust,
            deliveredBearer: Data("hrpin_1_SYNTHETIC-BEARER-CANARY".utf8), networkProfileID: profileID)
    }
    func open(_ credential: SavedDeviceCredential) async throws -> URL {
        opens += 1
        await gate.pause("open")
        try await checkAccess()
        return URL(string: "http://127.0.0.1:49152/")!
    }
    func checkAccess() async throws { if let accessFailure { throw accessFailure } }
    func stop() async throws {
        stops += 1; await gate.pause("stop")
        if let stopFailure { throw stopFailure }
    }
}

@Suite("Single home invite UI contract")
@MainActor
struct HomeInviteModelTests {
    // Public synthetic CA; no private key is present in this fixture.
    private let ca = "MIIBcDCCARegAwIBAgIBAzAKBggqhkjOPQQDAjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwHhcNMjMxMTE0MjIxMzIwWhcNMzMwNTE4MDMzMzIwWjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQn+y+9zcuDcMqqUHzKu60sYl6vJtfgX8DR1VfNh6XtkNUwTmInfcpYhOhYzq94Nc4xe87GBk1JAN43UK9cLaDAo0IwQDAOBgNVHQ8BAf8EBAMCAgQwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUyJSU09xAckvJECBK6NS3R58e9wswCgYIKoZIzj0EAwIDRwAwRAIgSGTa05eLWaPEQa9fYBwp979EMWXfu6F1aLr+IlLxlqMCIAv6++jkLKpTs+XT4vP9ETW/CdjHrUHv1hqkirv2ErYF"

    fileprivate func invite(expired: Bool = false, ttl: Int = 299, control: String = "https://control.happyranch.invalid:8443", dialIP: String = "192.168.1.40", home: String = "home", joinKey: String = String(repeating: "ab", count: 24)) throws -> String {
        func wire(_ data: Data) -> String { data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
        let now = Int(Date().timeIntervalSince1970)
        let fields: [String: Any] = ["v": 2, "mode": "home", "control_url": control,
            "control_plane_id": "control", "home_id": home, "connector_id": "connector", "home_endpoint": "100.64.0.1:443",
            "control_tls": ["kind": "local_ca", "dial_ip": dialIP, "ca_der": wire(Data(base64Encoded: ca)!), "sha256": certificateDigest(Data(base64Encoded: ca)!)],
            "invite_id": wire(Data(repeating: 1, count: 16)), "join_key": joinKey,
            "pair_secret": wire(Data(repeating: 2, count: 32)), "issued_at": now - (expired ? 301 : 1), "expires_at": now + (expired ? -1 : ttl)]
        return "hrinvite2:" + wire(try JSONSerialization.data(withJSONObject: fields))
    }

    fileprivate func eventually(timeout: TimeInterval = 5, _ predicate: @MainActor () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !predicate(), Date() < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        return predicate()
    }

    @Test("Admitted runtime survives connected → busy/unknown → ready without replacement or replay")
    func admittedBusyReadinessRetainsDocument() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        try #require(await eventually { model.webURL != nil })
        let url = try #require(model.webURL), context = model.lifetime
        model.webLoaded(url: url, lifetime: context)
        await model.bridgeSettle()
        try model.commitCandidate()
        #expect(model.phase == .connected)
        var published: [URL?] = []
        let observation = model.$webURL.sink { published.append($0) }
        defer { observation.cancel(); model.disconnectActive() }
        session.readiness.setBusy(true)
        try #require(await eventually(timeout: 7) { model.phase == .runtimeBusy })
        #expect(model.phase != .connected && model.appReadiness?.state == "busy")
        #expect(model.lifetime == context && model.webURL == url)
        #expect(ConnectionClientProjection.health(model, now: Date())["runtime"] as? String == "unknown")
        #expect(await session.stops == 0)
        #expect(await session.opens == 1)
        session.readiness.setBusy(false)
        try #require(await eventually(timeout: 7) { model.phase == .connected })
        #expect(model.lifetime == context && model.webURL == url)
        #expect(published.allSatisfy { $0 == url }, "No nil publication can dismantle Main")
        #expect(await session.stops == 0)
        #expect(await session.opens == 1)
        #expect(await session.pairs == 1)
        #expect(ConnectionClientProjection.health(model, now: Date())["runtime"] as? String == "available")
        // Genuine access loss still takes the existing teardown path.
        await session.configure(access: .revoked)
        try #require(await eventually(timeout: 7) { model.phase == .failed(.revoked) })
        #expect(model.webURL == nil && model.lifetime != context)
        #expect(model.accessObservation?.access == .revokedConfirmed)
    }

    @Test("A v1 invite requires a fresh invite and approval before any join")
    func v1InviteRequiresReapproval() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        let model = HomeInviteModel(store: store, makeSession: { session })
        let v2 = try invite()
        model.accept("hrinvite1:" + String(v2.dropFirst(10)))
        #expect(await eventually { model.phase == .failed(.reapprovalRequired) })
        #expect(await session.joins == 0)
        #expect(await session.pairs == 0)
        model.cancel()
    }

    @Test("Join and approval never publish Connected before the expected web load")
    func approvalAndWebLoadGate() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        let model = HomeInviteModel(store: store, makeSession: { session })
        var phases: [HomeInviteModel.Phase] = []
        let observation = model.$phase.sink { phases.append($0) }
        defer { observation.cancel(); model.cancel(); model.disconnectActive() }
        model.accept(try invite())
        #expect(await eventually { model.phase == .awaitingApproval })
        #expect(model.webURL == nil)
        #expect(model.expiresAt != nil)
        #expect(model.comparisonPhrase != nil)
        #expect(await store.saves == 0)
        await session.approve()
        #expect(await eventually { model.phase == .opening && model.webURL != nil })
        #expect(model.phase != .connected)
        let url = try #require(model.webURL)
        model.webLoaded(url: url, lifetime: HomeInviteModel.Lifetime(attempt: UUID(), document: model.documentGeneration))
        #expect(model.phase == .opening)
        model.webLoaded(url: URL(string: "http://127.0.0.1:49153/")!, lifetime: model.lifetime)
        #expect(model.phase == .opening)
        model.webLoaded(url: url, lifetime: model.lifetime)
        #expect(model.phase == .readyToSwitch)
        #expect(!model.hasActiveSession)
        await model.bridgeSettle()
        try model.commitCandidate()
        #expect(model.phase == .connected)
        #expect(model.expiresAt == nil && model.comparisonPhrase == nil)
        #expect(phases.filter { [.validating, .joining, .pairing, .awaitingApproval, .saving, .opening, .readyToSwitch, .connected].contains($0) } ==
            [.validating, .joining, .pairing, .awaitingApproval, .saving, .opening, .readyToSwitch, .connected])
    }

    @Test("1563: failed home control join leaves a sanitized failure and Retry")
    func failedJoinIsTerminalAndRetryable() async throws {
        let session = HomeSessionFixture()
        await session.configure(join: .controlUnreachable)
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.phase == .failed(.controlUnreachable) && !model.busy })
        #expect(model.webURL == nil)
        #expect(model.retryGuidance.contains("new invite"))
        model.retry()
        #expect(await eventually { !model.busy })
        #expect(await session.joins == 1, "Retry must not replay enrollment")
    }

    @Test("Expiry refuses before networking; cancellation forgets pending authority and ignores late completion")
    func expiryAndCancel() async throws {
        let session = HomeSessionFixture()
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
        model.accept(try invite(expired: true))
        #expect(await eventually { model.phase == .failed(.expiredInvite) && !model.busy })
        #expect(await session.joins == 0)
        model.accept(try invite())
        #expect(await eventually { model.phase == .awaitingApproval })
        let old = model.lifetime
        model.cancel()
        #expect(await eventually { model.phase == .idle && !model.busy })
        await session.approve()
        model.webLoaded(url: URL(string: "http://127.0.0.1:49152/")!, lifetime: old)
        #expect(model.phase == .idle)
        #expect(model.expiresAt == nil && model.comparisonPhrase == nil && model.webURL == nil)
    }

    @Test("Waiting for Linux approval stops at the actual invite expiry")
    func pendingExpiry() async throws {
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { HomeSessionFixture() })
        model.accept(try invite(ttl: 3))
        #expect(await eventually { model.phase == .awaitingApproval })
        #expect(await eventually { model.phase == .failed(.expiredInvite) && !model.busy })
        #expect(model.expiresAt == nil && model.comparisonPhrase == nil && model.webURL == nil)
    }

    @Test("Known daemon outage and unknown home reachability stay distinct")
    func offlineCategories() async throws {
        for failure in [HomeConnectionFailure.daemonUnavailable, .homeUnavailable] {
            let session = HomeSessionFixture()
            await session.approve(); await session.configure(access: failure)
            let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
            model.accept(try invite())
            #expect(await eventually { model.phase == .failed(failure) && !model.busy })
            #expect(model.webURL == nil)
        }
        #expect(HomeConnectionFailure.daemonUnavailable.localizedDescription == "Home HappyRanch is offline/restarting — Retry")
        #expect(HomeConnectionFailure.homeUnavailable.localizedDescription == "Not on your home network, or home computer unavailable")
    }

    @Test("Save retry retains native loads before/during saving, reopen uses no key, and Keychain failure stays visible",
          arguments: [false, true])
    func savedAccessRecovery(loadDuringSave: Bool) async throws {
        let gate = HomeAwaitGate()
        let store = HomeStoreFixture(gate: gate), session = HomeSessionFixture()
        await store.configure(saveFails: true); await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        #expect(model.phase == .opening && model.saveFailure == .saveFailed)
        let url = try #require(model.webURL)
        if !loadDuringSave { model.webLoaded(url: url, lifetime: model.lifetime) }
        await store.configure()
        let savingContext = model.lifetime
        #expect(model.beginConfirmation(for: savingContext))
        await gate.arm("save")
        model.retrySave()
        try #require(await gate.waitFor("save"))
        if loadDuringSave { model.webLoaded(url: url, lifetime: savingContext) }
        #expect(model.phase == .saving)
        await gate.release()
        #expect(await eventually { model.saveFailure == nil && !model.savedHomes.isEmpty && model.phase == .opening })
        #expect(model.phase == .opening, "Save retry cannot bypass a native confirmation hold")
        model.endConfirmation(for: savingContext)
        #expect(model.phase == .readyToSwitch && !model.busy)
        model.cancel()
        #expect(await eventually { !model.busy })
        let reopen = HomeInviteModel(store: store, makeSession: { session })
        reopen.prepare()
        #expect(await eventually { reopen.phase == .opening && reopen.webURL != nil })
        reopen.webLoaded(url: try #require(reopen.webURL), lifetime: reopen.lifetime)
        #expect(await session.emptyKeys == 1)
        reopen.cancel()
        #expect(await eventually { !reopen.busy })
        for failure in [SavedAccessFailure.locked, .cancelled, .identityChanged, .denied] {
            await store.configure(read: failure)
            let denied = HomeInviteModel(store: store, makeSession: { session })
            denied.prepare()
            #expect(await eventually { denied.phase == .keychain(failure) && !denied.busy })
            #expect(denied.webURL == nil)
            denied.accept(try invite())
            #expect(denied.phase == .keychain(failure), "Replacement needs explicit consent")
        }
    }

    @Test("The native navigation deadline remains effective during Retry save")
    func retrySaveNavigationDeadline() async throws {
        let gate = HomeAwaitGate()
        let store = HomeStoreFixture(gate: gate), session = HomeSessionFixture()
        await store.configure(saveFails: true); await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        try #require(await eventually { model.webURL != nil })
        let url = try #require(model.webURL), context = model.lifetime
        await store.configure()
        await gate.arm("save")
        model.retrySave()
        try #require(await gate.waitFor("save"))
        // Exercise the existing production timer; no test clock or new hook.
        try await Task.sleep(for: .seconds(11))
        #expect(model.phase == .stopping && model.webURL == nil)
        #expect(await session.stops == 0, "Timeout waits for storage readback")
        await gate.release()
        #expect(await eventually { !model.busy })
        #expect(model.phase == .failed(.homeUnavailable) && model.savedHomes.count == 1)
        model.webLoaded(url: url, lifetime: context)
        #expect(model.phase == .failed(.homeUnavailable))
    }

    @Test("Local removal stops once despite custody failure and retains exact-target Retry",
          arguments: [nil, .cancelled, .denied, .locked, .identityChanged, .notFound] as [SavedAccessFailure?])
    func removalFailureAndRetry(readFailure: SavedAccessFailure?) async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        try #require(await eventually { model.webURL != nil })
        let url = try #require(model.webURL), old = model.lifetime
        model.webLoaded(url: url, lifetime: old)
        try #require(await eventually { model.phase == .readyToSwitch && !model.busy })
        let target = try #require(model.savedHomes.first)
        let credential = try #require(try await store.savedHomes().credentials.first)
        await store.configure(read: readFailure, deleteFails: readFailure == nil)
        model.removeSavedComputer(expected: target)
        #expect(await eventually { model.phase == .keychain(readFailure ?? .deletionFailed) && !model.busy })
        #expect(await session.stops == 1)
        #expect(model.webURL == nil && model.bridgeActiveComputerID == nil)
        #expect(model.bridgeRemovalTarget == target)
        #expect(await store.deletedBindings.isEmpty)
        #expect(!model.removalFailures.isEmpty && !model.savedHomes.isEmpty)
        model.webLoaded(url: url, lifetime: old)
        #expect(model.phase == .keychain(readFailure ?? .deletionFailed), "Late load cannot reconnect")
        let joins = await session.joins
        model.reconnect()
        await Task.yield()
        #expect(await session.joins == joins)
        model.selectedHomeID = UUID() // Retry must retain the captured target.
        if let readFailure {
            model.removeSavedComputer(expected: target)
            #expect(await eventually { model.phase == .keychain(readFailure) && !model.busy })
            #expect(await session.stops == 1)
            #expect(await store.deletedBindings.isEmpty)
        }
        await store.configure()
        model.removeSavedComputer(expected: target)
        #expect(await eventually { model.phase == .idle && !model.busy })
        #expect(await session.stops == 1)
        #expect(await store.deletedBindings == [credential.binding])
        #expect(model.bridgeRemovalTarget == nil && model.bridgeCanAccept)
        #expect(model.savedHomes.isEmpty && model.removalFailures.isEmpty)
        #expect(try await store.savedHomes().credentials.isEmpty)
    }

    @Test("Explicit same-CA fresh invite updates saved address without redeeming its new key or secret")
    func changedAddressUsesSavedGrant() async throws {
        let session = HomeSessionFixture()
        await session.approve()
        let store = HomeStoreFixture()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        model.cancel()
        #expect(await eventually { !model.busy })
        model.accept(try invite(dialIP: "192.168.1.41"))
        #expect(await eventually { model.phase == .opening && model.webURL != nil })
        #expect(await session.joins == 1)
        #expect(await session.pairs == 1)
        #expect(await session.recoveries == 1)
        #expect(await store.refreshes == 1)
        #expect(await store.saves == 1)
        #expect(model.savedHomes.first?.trust.dialIP == "192.168.1.41")
        model.webLoaded(url: try #require(model.webURL), lifetime: model.lifetime)
        #expect(model.phase == .readyToSwitch)
        model.cancel()
    }

    @Test("An optional root slash alone reopens saved membership without address migration")
    func rootSlashUsesSavedAccess() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite(control: "https://control.happyranch.invalid:8443/"))
        #expect(await eventually { model.webURL != nil })
        model.cancel()
        #expect(await eventually { !model.busy })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        #expect(await session.recoveries == 0)
        #expect(await session.emptyKeys == 1)
        #expect(await store.refreshes == 0)
        #expect(await session.pairs == 1)
        model.cancel()
    }

    @Test("Address recovery refuses missing saved grant and changed node without replacing custody")
    func addressRecoveryRefusals() async throws {
        for scenario in ["missing-grant", "different-node", "native-marker-refusal", "save-failure"] {
            let store = HomeStoreFixture(), session = HomeSessionFixture()
            await session.approve()
            let model = HomeInviteModel(store: store, makeSession: { session })
            model.accept(try invite())
            #expect(await eventually { model.webURL != nil })
            model.cancel()
            #expect(await eventually { !model.busy })
            switch scenario {
            case "missing-grant": await store.configure(read: .notFound)
            case "different-node": await session.changeReturnedNode("other-node")
            case "native-marker-refusal": await session.configure(access: .identityUnavailable)
            default: await store.configure(saveFails: true)
            }
            model.accept(try invite(dialIP: "192.168.1.41"))
            #expect(await eventually { !model.busy })
            #expect(model.webURL == nil && model.savedHomes.first?.trust.dialIP == "192.168.1.40")
            #expect(await session.pairs == 1)
            #expect(await session.joins == 1)
            if scenario == "missing-grant" { #expect(model.phase == .keychain(.notFound)); #expect(await session.recoveries == 0) }
            else if scenario == "save-failure" { #expect(model.phase == .keychain(.saveFailed)) }
            else { #expect(model.phase == .failed(.identityUnavailable)); #expect(await store.refreshes == 0) }
            if scenario != "save-failure" {
                let refusal = model.phase
                await store.configure(); await session.configure(); await session.resetApproval()
                model.accept(try invite(dialIP: "192.168.1.41"))
                #expect(model.phase == refusal, "New enrollment requires explicit consent")
                model.accept(try invite(dialIP: "192.168.1.41"), replacementConsent: true)
                #expect(await eventually { model.phase == .awaitingApproval })
                #expect(await session.joins == 2)
                #expect(await session.pairs == 2)
                #expect(await store.refreshes == 0)
                model.cancel()
                #expect(await eventually { !model.busy })
            }
        }
    }

    @Test("Fresh invites recover failed or cancelled first enrollment without replaying its key")
    func firstEnrollmentRecovery() async throws {
        for cancelled in [false, true] {
            let session = HomeSessionFixture()
            await session.configureMembership(rejectEmpty: true, suspendJoin: cancelled)
            if !cancelled { await session.configure(join: .controlUnreachable) }
            let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
            model.accept(try invite())
            if cancelled {
                #expect(await eventually { model.phase == .joining })
                // Ensure the first key actually reached the session before cancellation.
                let joinDeadline = Date().addingTimeInterval(5)
                while await session.joins == 0, Date() < joinDeadline { await Task.yield() }
                #expect(await session.joins == 1)
                model.cancel()
            }
            #expect(await eventually { !model.busy })
            await session.configure(); await session.configureMembership(rejectEmpty: true)
            model.accept(try invite(joinKey: String(repeating: "cd", count: 24)))
            #expect(await eventually { model.phase == .awaitingApproval })
            #expect(await session.freshKeysDiffer())
            let keyed = await session.keyProfiles
            let empty = await session.emptyProfiles
            #expect(keyed.count == 2 && keyed[0] != keyed[1])
            #expect(empty == [keyed[0]], "Uncertain membership is checked without the spent key")
            model.cancel()
            #expect(await eventually { !model.busy })
        }
    }

    @Test("Joined membership survives lost delivery and uses an empty key for the next invite")
    func joinedMembershipRecovery() async throws {
        let session = HomeSessionFixture()
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.phase == .awaitingApproval })
        model.cancel()
        #expect(await eventually { !model.busy })
        model.accept(try invite())
        #expect(await eventually { model.phase == .awaitingApproval })
        #expect(await session.keyProfiles.count == 1)
        let empty = await session.emptyProfiles
        let keyed = await session.keyProfiles
        #expect(empty == keyed)
        model.cancel()
    }

    @Test("Mixed discovery failures and more than 32 approved records preserve usable saved access")
    func partialDiscoverySurvivesRestart() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        let approved = HomeInviteModel(store: store, makeSession: { session })
        await store.setDiscoveryFailures([.denied, .identityChanged])
        approved.prepare()
        #expect(await eventually { !approved.busy })
        approved.accept(try invite(), replacementConsent: true)
        await session.approve()
        #expect(await eventually { approved.webURL != nil })
        approved.cancel()
        #expect(await eventually { !approved.busy })
        let restarted = HomeInviteModel(store: store, makeSession: { session })
        restarted.prepare()
        #expect(await eventually { restarted.webURL != nil })
        #expect(restarted.discoveryFailures == [.denied, .identityChanged])
        restarted.cancel()
        #expect(await eventually { !restarted.busy })
        let trust = try RemoteInvite.parse(invite()).trust
        for number in 0..<32 {
            try await store.save(SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
                controlPlaneID: trust.controlPlaneID, stableNodeID: "synthetic-node-\(number)"), trust: trust,
                deliveredBearer: Data("SYNTHETIC-DISCOVERY-CANARY".utf8), networkProfileID: UUID()))
        }
        await store.setDiscoveryFailures([.denied, .identityChanged])
        for _ in 0..<2 {
            let model = HomeInviteModel(store: store, makeSession: { session })
            model.prepare()
            #expect(await eventually { !model.busy })
            #expect(model.savedHomes.count == 33)
            #expect(model.discoveryFailures == [.denied, .identityChanged])
            #expect(model.needsReplacementConsent)
            model.reconnect()
            #expect(await eventually { model.webURL != nil })
            model.cancel()
            #expect(await eventually { !model.busy })
        }
    }

    @Test("Bridge projection returns every valid computer at 32 and null at 33 without capping custody", arguments: [32, 33])
    func bridgeProjectionBound(_ count: Int) async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        let trust = try RemoteInvite.parse(invite()).trust
        let ids = (0..<count).map { _ in UUID() }.sorted { $0.uuidString < $1.uuidString }
        for (number, id) in ids.enumerated().reversed() {
            try await store.save(SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
                controlPlaneID: trust.controlPlaneID, stableNodeID: "synthetic-node-\(number)"), trust: trust,
                deliveredBearer: Data("SYNTHETIC-BRIDGE-CANARY".utf8), networkProfileID: id))
        }
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.prepare()
        #expect(await eventually { !model.busy })
        #expect(model.savedHomes.count == count)
        let catalog = ConnectionComputerCatalog()
        let saved = ConnectionClientProjection.savedComputers(model, catalog: catalog)
        let first = ConnectionClientProjection.snapshot(model: model, catalog: catalog)
        if count == 32 {
            let rows = try #require(saved["computers"] as? [[String: String]])
            #expect(rows.count == 32)
            #expect(first["computers"] as? [[String: String]] == rows)
            #expect(rows.compactMap { catalog.resolve($0["computerRef"]!, in: model) } == ids)
            #expect(!ConnectionClientProjection.observationLimits(first).contains("computers"))
            let second = ConnectionClientProjection.snapshot(model: model, catalog: catalog)
            #expect(first["computers"] as? [[String: String]] == second["computers"] as? [[String: String]])
        } else {
            #expect(saved["computers"] is NSNull && first["computers"] is NSNull)
            #expect(ConnectionClientProjection.observationLimits(first).contains("computers"))
        }
        model.selectedHomeID = ids[0]; model.reconnect()
        #expect(await eventually { model.webURL != nil })
        let snapshot = ConnectionClientProjection.snapshot(model: model, catalog: catalog)
        let active = try #require(snapshot["activeComputerRef"] as? String)
        #expect(catalog.resolve(active, in: model) == ids[0])
        let health = ConnectionClientProjection.health(model, now: Date())
        #expect(health["access"] as? String == "allowed")
        #expect(health["runtime"] as? String == "unknown") // open is not a loaded RELEASE UI
        let later = ConnectionClientProjection.health(model, now: Date().addingTimeInterval(11))
        #expect(later["access"] as? String == "unknown" && later["observation"] as? String == "stale")
        let wire = ConnectionRequest.reply(id: "r1", result: snapshot)
        #expect(!wire.contains("SYNTHETIC-") && !wire.contains(trust.homeEndpoint) && !wire.contains(trust.controlURL))
        #expect(catalog.resolve("unknown", in: model) == nil)
        model.cancel()
        #expect(await eventually { !model.busy })
        model.selectedHomeID = ids[0]; model.removeSavedComputer()
        #expect(await eventually { !model.busy })
        #expect(catalog.resolve(active, in: model) == nil) // stale native target, even before catalog refresh
        #expect(model.savedHomes.count == count - 1)
    }

    @Test("Bridge Retry save awaits custody settlement and respects Cancel's safe point", arguments: [false, true])
    func bridgeSaveSettlement(_ cancel: Bool) async throws {
        let gate = HomeAwaitGate(), store = HomeStoreFixture(gate: gate), session = HomeSessionFixture()
        await store.configure(saveFails: true)
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite()); await session.approve()
        #expect(await eventually { model.webURL != nil })
        let url = try #require(model.webURL)
        model.webLoaded(url: url, lifetime: model.lifetime)
        #expect(await eventually { model.bridgeCanRetrySave })
        await store.configure(saveFails: false); await gate.arm("save")
        let retry = Task { await model.bridgeRetrySave() }
        #expect(await gate.waitFor("save"))
        #expect(model.phase == .saving && model.saveFailure != nil)
        if cancel { model.cancel(); #expect(model.phase == .stopping) }
        await gate.release()
        let result = await retry.value
        #expect(result == (cancel ? .changed : .saved))
        #expect(await eventually { !model.busy })
        #expect(model.savedHomes.count == 1 && model.saveFailure == nil)
        #expect(model.phase == (cancel ? .idle : .readyToSwitch))
        #expect(await store.saves == 2)
        #expect(await model.bridgeRetrySave() == .refused)
        if !cancel { model.cancel(); #expect(await eventually { !model.busy }) }
    }

    @Test("OS Keychain cancellation/denial is a save outcome, never attempt cancellation",
          arguments: [SavedAccessFailure.cancelled, .denied, .locked, .unavailable])
    func keychainResultDoesNotCancelAttempt(_ failure: SavedAccessFailure) async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await store.setSaveFailure(failure); await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        let context = model.lifetime
        #expect(model.phase == .opening && model.saveFailure == failure)
        #expect(await session.stops == 0)
        model.webLoaded(url: try #require(model.webURL), lifetime: context)
        #expect(model.phase == .readyToSwitch && model.lifetime == context)
        #expect(ConnectionClientProjection.savedAccess(model) == (failure == .unavailable ? "unknown" : "needsPermission"))
        model.cancel(); #expect(await eventually { !model.busy })
        #expect(model.saveFailure == failure)
    }

    @Test("Saved reconnect rereads the exact bearer before joining")
    func changedSavedBearerRefusesReconnect() async throws {
        let trust = try RemoteInvite.parse(invite()).trust
        let binding = try SavedDeviceBinding(homeID: trust.homeID, controlPlaneID: trust.controlPlaneID,
                                             stableNodeID: "synthetic-stable-node")
        let id = UUID(), store = HomeStoreFixture(), session = HomeSessionFixture()
        try await store.save(SavedDeviceCredential(binding: binding, trust: trust,
            deliveredBearer: Data("SYNTHETIC-ORIGINAL".utf8), networkProfileID: id))
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.prepare(reconnectSingle: false)
        #expect(await eventually { model.discoveryComplete && !model.busy })
        #expect(await session.joins == 0)
        try await store.save(SavedDeviceCredential(binding: binding, trust: trust,
            deliveredBearer: Data("SYNTHETIC-CHANGED".utf8), networkProfileID: id))
        model.reconnect()
        #expect(await eventually { !model.busy })
        #expect(model.phase == .keychain(.identityChanged))
        #expect(await session.joins == 0)
        #expect(model.webURL == nil)
    }

    @Test("Replacement cancellation waits for deletion and retains an incomplete outcome")
    func replacementCancellationRetainsPartialEffect() async throws {
        let gate = HomeAwaitGate(), store = HomeStoreFixture(gate: gate), session = HomeSessionFixture()
        await store.setSaveFailure(.identityChanged); await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        let url = try #require(model.webURL)
        model.webLoaded(url: url, lifetime: model.lifetime)
        #expect(await eventually { !model.busy })
        #expect(model.bridgeCanReplace && !model.bridgeCanRetrySave)
        await gate.arm("delete")
        let replacement = Task { await model.bridgeRetrySave(replacing: true) }
        try #require(await gate.waitFor("delete"))
        model.cancel()
        #expect(model.phase == .stopping)
        #expect(await session.stops == 0)
        await gate.release()
        #expect(await replacement.value == .changed)
        #expect(await eventually { !model.busy })
        #expect(model.phase == .idle && model.saveFailure == .deletionFailed)
        #expect(await store.saves == 1)
        #expect(model.webURL == nil)
        #expect(ConnectionClientProjection.savedAccess(model) == "unknown")
    }

    @Test("Revoked unsaved B cannot erase the selected saved computer A")
    func revocationTargetsActiveCredential() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite(home: "A"))
        #expect(await eventually { model.webURL != nil })
        let savedA = try #require(try await store.savedHomes().credentials.first)
        model.cancel()
        #expect(await eventually { !model.busy })
        await store.configure(saveFails: true); await session.configure(access: .revoked)
        model.accept(try invite(home: "B"))
        #expect(await eventually { model.phase == .failed(.revoked) && !model.busy })
        let remaining = try await store.savedHomes().credentials
        #expect(remaining.count == 1)
        let actual = try #require(remaining.first)
        #expect(actual.binding == savedA.binding && actual.networkProfileID == savedA.networkProfileID && actual.trust == savedA.trust)
        #expect(actual.withBearer { old in savedA.withBearer { old == $0 } })
        #expect(model.savedHomes.count == 1 && model.selectedHomeID == savedA.networkProfileID)
        let deleted = await store.deletedBindings
        #expect(deleted.count == 1 && deleted.first?.homeID == "B")
        #expect(model.saveFailure == nil && model.webURL == nil)
        let saves = await store.saves
        model.retrySave()
        await Task.yield()
        #expect(await store.saves == saves, "Revoked in-memory authority was forgotten")
    }

    @Test("A different saved CA requires reapproval, never address recovery")
    func addressRecoveryRejectsDifferentCA() async throws {
        let parsed = try RemoteInvite.parse(invite())
        let t = parsed.trust
        let foreign = SavedHomeTrust(homeID: t.homeID, controlPlaneID: t.controlPlaneID,
            connectorID: t.connectorID, controlURL: t.controlURL, dialIP: t.dialIP, homeEndpoint: t.homeEndpoint,
            caDER: Data("synthetic-other-ca".utf8), caSHA256: String(repeating: "0", count: 64))
        let credential = try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: t.homeID,
            controlPlaneID: t.controlPlaneID, stableNodeID: "synthetic-stable-node"), trust: foreign,
            deliveredBearer: Data("SYNTHETIC-CA-REFUSAL-CANARY".utf8), networkProfileID: UUID())
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        try await store.save(credential)
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.prepare()
        #expect(await eventually { model.webURL != nil })
        model.cancel()
        #expect(await eventually { !model.busy })
        model.accept(try invite(dialIP: "192.168.1.41"))
        #expect(await eventually { model.phase == .failed(.identityChanged) && !model.busy })
        #expect(await session.recoveries == 0)
        #expect(await store.refreshes == 0)
        #expect(model.webURL == nil)
    }

    @Test("Cancel and document replacement discard every late discovery/join/pair/load result",
          arguments: ["discovery", "load", "join", "pair", "complete", "open", "recover", "refresh"], [false, true])
    func cancelledAwaitCompletion(point: String, replaceDocument: Bool) async throws {
        let gate = HomeAwaitGate()
        let store = HomeStoreFixture(gate: gate), session = HomeSessionFixture(gate: gate)
        let model = HomeInviteModel(store: store, makeSession: { session })
        await session.approve()
        if ["discovery", "load", "recover", "refresh"].contains(point) {
            let trust = try RemoteInvite.parse(invite()).trust
            try await store.save(SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
                controlPlaneID: trust.controlPlaneID, stableNodeID: "synthetic-stable-node"), trust: trust,
                deliveredBearer: Data("SYNTHETIC-LIFETIME-CANARY".utf8), networkProfileID: UUID()))
            if point == "discovery" { await store.setDiscoveryFailures([.denied]) }
            if point == "recover" || point == "refresh" {
                model.prepare()
                #expect(await eventually { model.webURL != nil })
                model.cancel()
                #expect(await eventually { !model.busy })
            }
        }
        await gate.arm(point)
        if point == "discovery" || point == "load" { model.prepare() }
        else { model.accept(try invite(dialIP: ["recover", "refresh"].contains(point) ? "192.168.1.41" : "192.168.1.40")) }
        try #require(await gate.waitFor(point))
        let old = model.lifetime
        var afterCancel: [HomeInviteModel.Phase] = []
        if replaceDocument { model.documentDidChange() } else { model.cancel() }
        let observation = model.$phase.sink { afterCancel.append($0) }
        defer { observation.cancel() }
        #expect(model.lifetime != old && model.phase == .stopping)
        await gate.release()
        #expect(await eventually { !model.busy })
        #expect(model.phase == .idle && model.webURL == nil)
        #expect(!afterCancel.contains(.opening) && !afterCancel.contains(.connected))
        model.webLoaded(url: URL(string: "http://127.0.0.1:49152/")!, lifetime: old)
        #expect(model.phase == .idle)
        if point == "discovery" { #expect(model.savedHomes.isEmpty && model.discoveryFailures.isEmpty) }
        if point == "complete" { #expect(await store.saves == 0) }
        if point == "recover" { #expect(await store.refreshes == 0) }
        if point == "refresh" { #expect(model.savedHomes.first?.trust.dialIP == "192.168.1.41") }
    }

    @Test("Cancel waits for initial/retry save readback and retains success or failure without opening",
          arguments: ["initial-success", "initial-failure", "retry-success", "retry-failure"], [false, true])
    func cancelledSaveOutcome(scenario: String, replaceDocument: Bool) async throws {
        let gate = HomeAwaitGate()
        let store = HomeStoreFixture(gate: gate), session = HomeSessionFixture(gate: gate)
        let model = HomeInviteModel(store: store, makeSession: { session })
        await session.approve()
        let retry = scenario.hasPrefix("retry"), fails = scenario.hasSuffix("failure")
        if retry {
            await store.configure(saveFails: true)
            model.accept(try invite())
            try #require(await eventually { model.webURL != nil })
            #expect(model.saveFailure == .saveFailed)
        }
        await store.configure(saveFails: fails)
        await gate.arm("save")
        if retry { model.retrySave() } else { model.accept(try invite()) }
        try #require(await gate.waitFor("save"))
        #expect(model.phase == .saving && model.busy)
        let old = model.lifetime
        if retry {
            #expect(model.beginConfirmation(for: old))
            model.webLoaded(url: try #require(model.webURL), lifetime: old)
            #expect(model.phase == .saving, "A native load cannot bypass storage settlement")
        }
        if replaceDocument { model.documentDidChange() } else { model.cancel() }
        model.endConfirmation(for: old)
        #expect(model.phase == .stopping && model.webURL == nil)
        #expect(await session.stops == 0, "Teardown must wait for Keychain readback")
        await gate.release()
        #expect(await eventually { !model.busy })
        #expect(model.phase == .idle && model.webURL == nil)
        #expect(model.savedHomes.count == (fails ? 0 : 1))
        #expect(model.saveFailure == (fails ? .saveFailed : nil))
        #expect(try await store.savedHomes().credentials.count == (fails ? 0 : 1))
        #expect(await session.opens == (retry ? 1 : 0))
        model.webLoaded(url: URL(string: "http://127.0.0.1:49152/")!, lifetime: old)
        #expect(model.phase == .idle)
        let saves = await store.saves
        model.retrySave()
        await Task.yield()
        #expect(await store.saves == saves, "Cancel forgets in-memory retry authority")
    }

    @Test("A failure awaiting stop cannot overwrite Cancel or document replacement", arguments: [false, true])
    func cancelledFailureStop(replaceDocument: Bool) async throws {
        let gate = HomeAwaitGate(), store = HomeStoreFixture()
        let session = HomeSessionFixture(gate: gate)
        await session.configure(join: .controlUnreachable)
        await gate.arm("stop")
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        try #require(await gate.waitFor("stop"))
        if replaceDocument { model.documentDidChange() } else { model.cancel() }
        var phases: [HomeInviteModel.Phase] = []
        let observation = model.$phase.sink { phases.append($0) }
        defer { observation.cancel() }
        await gate.release()
        #expect(await eventually { !model.busy })
        #expect(model.phase == .idle && !phases.contains(.failed(.controlUnreachable)))
    }

    @Test("A superseded cancellation teardown cannot publish its old terminal state")
    func supersededTeardown() async throws {
        let gate = HomeAwaitGate()
        let gatedSession = HomeSessionFixture(gate: gate)
        await gatedSession.approve()
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { gatedSession })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        await gate.arm("stop")
        model.cancel(endingAt: .failed(.homeUnavailable))
        try #require(await gate.waitFor("stop"))
        model.documentDidChange()
        var phases: [HomeInviteModel.Phase] = []
        let observation = model.$phase.sink { phases.append($0) }
        defer { observation.cancel() }
        await gate.release()
        #expect(await eventually { !model.busy })
        #expect(model.phase == .idle && !phases.contains(.failed(.homeUnavailable)))
    }

    @Test("Interrupted removal exposes exact-target recovery and reconciles observed deletion",
          arguments: ["stop", "delete-success", "delete-failure"], [false, true])
    func cancelledRemoval(scenario: String, replaceDocument: Bool) async throws {
        let gate = HomeAwaitGate()
        let store = HomeStoreFixture(gate: gate), session = HomeSessionFixture(gate: gate)
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        try #require(await eventually { model.webURL != nil })
        let credential = try #require(try await store.savedHomes().credentials.first)
        let target = try #require(model.savedHomes.first)
        let point = scenario == "stop" ? "stop" : "delete"
        await store.configure(deleteFails: scenario == "delete-failure")
        await gate.arm(point)
        model.removeSavedComputer(expected: target)
        try #require(await gate.waitFor(point))
        let old = model.lifetime
        if replaceDocument { model.documentDidChange() } else { model.cancel() }
        await gate.release()
        #expect(await eventually { !model.busy })
        #expect(model.webURL == nil)
        model.webLoaded(url: URL(string: "http://127.0.0.1:49152/")!, lifetime: old)
        if scenario == "delete-success" {
            #expect(model.phase == .idle && model.removalFailures.isEmpty)
            #expect(model.savedHomes.isEmpty, "Current cancellation owner reconciles confirmed erasure")
            #expect(await store.deletedBindings == [credential.binding])
        } else {
            #expect(model.phase == .failed(.cleanupIncomplete) && !model.removalFailures.isEmpty)
            #expect(model.savedHomes.count == 1 && model.target == credential.trust)
            #expect(await store.deletedBindings.isEmpty)
            let joins = await session.joins
            model.reconnect()
            await Task.yield()
            #expect(await session.joins == joins, "Incomplete removal retains its captured target")
            // Retry owns the retained target even if picker selection changes.
            model.selectedHomeID = UUID()
            await store.configure()
            model.removeSavedComputer(expected: target)
            #expect(await eventually { !model.busy })
            #expect(model.phase == .idle && model.removalFailures.isEmpty && model.savedHomes.isEmpty)
            #expect(await store.deletedBindings == [credential.binding])
        }
        #expect(try await store.savedHomes().credentials.isEmpty)
        model.accept(try invite(home: "after-removal"))
        #expect(await eventually { model.webURL != nil })
        model.webLoaded(url: try #require(model.webURL), lifetime: model.lifetime)
        #expect(model.phase == .readyToSwitch, "Settled cleanup releases the new-connection guard")
        model.cancel()
        #expect(await eventually { !model.busy })
    }

    @Test("First Main load is fenced; later candidates reuse that admitted document")
    func confirmationLoadGate() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        let first = model.lifetime, url = try #require(model.webURL)
        let wrongDocument = HomeInviteModel.Lifetime(attempt: first.attempt, document: UUID())
        #expect(!model.beginConfirmation(for: wrongDocument))
        #expect(model.beginConfirmation(for: first))
        #expect(!model.beginConfirmation(for: first), "No stacked confirmations")
        model.webLoaded(url: url, lifetime: wrongDocument)
        model.webFailed(url: url, lifetime: wrongDocument)
        model.endConfirmation(for: wrongDocument)
        #expect(model.phase == .opening)
        model.webLoaded(url: url, lifetime: first)
        #expect(model.phase == .opening)
        model.endConfirmation(for: first)
        #expect(model.phase == .readyToSwitch && !model.hasActiveSession)
        model.cancel()
        #expect(await eventually { !model.busy })
        model.reconnect()
        #expect(await eventually { model.phase == .readyToSwitch && !model.busy })
        let second = model.lifetime
        #expect(first.attempt != second.attempt && first.document == second.document)
        model.webLoaded(url: url, lifetime: first)
        model.webFailed(url: url, lifetime: first)
        #expect(model.phase == .readyToSwitch && !model.hasActiveSession)
        model.cancel()
        #expect(await eventually { !model.busy })
    }

}

private func certificateDigest(_ ca: Data) -> String {
    SHA256.hash(data: ca).map { String(format: "%02x", $0) }.joined()
}

private final class HomeSessionFactory: @unchecked Sendable {
    private let lock = NSLock()
    private var sessions: [HomeSessionFixture]
    private(set) var count = 0
    init(_ sessions: [HomeSessionFixture]) { self.sessions = sessions }
    func next() -> any HomeInviteSession {
        lock.withLock { count += 1; return sessions.isEmpty ? HomeSessionFixture() : sessions.removeFirst() }
    }
}

final class HomeCandidateOwnershipTests: XCTestCase {
    @MainActor
    private func firstSelection(_ model: HomeInviteModel, home: String = "A") async throws {
        let cases = HomeInviteModelTests()
        model.accept(try cases.invite(home: home))
        let opened = await cases.eventually { model.webURL != nil }
        XCTAssertTrue(opened)
        model.webLoaded(url: try XCTUnwrap(model.webURL), lifetime: model.lifetime)
        await model.bridgeSettle()
        XCTAssertEqual(model.phase, .readyToSwitch)
        XCTAssertNil(model.appTransport, "Readiness alone does not select a transport")
        try model.commitCandidate()
    }

    @MainActor
    func testUnsavedSessionDisconnectAndNonSaveTransitionsRetainWarning() async throws {
        let a = HomeSessionFixture(), b = HomeSessionFixture(), store = HomeStoreFixture()
        await a.approve(); await b.approve(); await store.configure(saveFails: true)
        let factory = HomeSessionFactory([a, b])
        let model = HomeInviteModel(store: store, makeSession: { factory.next() })
        try await firstSelection(model)
        let selected = try XCTUnwrap(model.activeID)
        let (root, scheme, _, transport) = try AppSchemeSwitchTests().fixture()
        defer { scheme.close(); try? FileManager.default.removeItem(at: root); try? FileManager.default.removeItem(at: root.appendingPathExtension("json")) }
        func warningPresent() throws -> Bool {
            let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
            return try XCTUnwrap(status["access_unsaved"] as? Bool)
        }
        scheme.disconnectSelected = { model.disconnectActive(); return true }
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        XCTAssertNil(scheme.selectTransport(id: selected.uuidString, transport: try XCTUnwrap(model.appTransport), remote: true))
        scheme.updateCandidate(nil, accessUnsaved: model.hasUnsavedAccessWarning)
        XCTAssertTrue(try warningPresent()); XCTAssertEqual(model.saveFailure, .saveFailed)
        let disconnected = await model.disconnectSelected(using: scheme)
        XCTAssertNil(disconnected)
        XCTAssertTrue(model.cleanupOutcomes[selected] == "confirmed")
        XCTAssertFalse(model.hasActiveSession); XCTAssertNil(model.appTransport)
        XCTAssertFalse(model.bridgeHasUnsavedAccess, "Disconnect releases credential retry custody")
        XCTAssertEqual(model.saveFailure, .saveFailed, "No active target is not a save confirmation")
        XCTAssertTrue(try warningPresent(), "Disconnect's immediate status must retain the warning")
        scheme.updateCandidate(nil, accessUnsaved: model.hasUnsavedAccessWarning)
        XCTAssertTrue(try warningPresent(), "SwiftUI's subsequent projection must also retain it")
        let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
        XCTAssertEqual(status["state"] as? String, "no_target")
        XCTAssertEqual(status["access_unsaved"] as? Bool, true)
        XCTAssertTrue(model.retryGuidance.contains("new invite"))
        // Direct transport selection and document loss cannot confirm a save.
        XCTAssertNil(scheme.selectTransport(id: "other", transport: transport, remote: true))
        XCTAssertTrue(try warningPresent())
        scheme.documentDidStart(); XCTAssertTrue(try warningPresent())
        await store.configure()
        model.accept(try HomeInviteModelTests().invite(home: "B"))
        let ready = await HomeInviteModelTests().eventually { model.phase == .readyToSwitch && !model.busy }
        XCTAssertTrue(ready); XCTAssertFalse(model.savedHomes.isEmpty)
        XCTAssertTrue(model.hasUnsavedAccessWarning, "Saving B does not confirm A's failed save")
        model.cancel(); await model.bridgeSettle()
        scheme.updateCandidate(model.bridgeCandidate, accessUnsaved: model.hasUnsavedAccessWarning)
        XCTAssertTrue(try warningPresent(), "Cancel and an unrelated save retain the original failure")
    }

    @MainActor
    func testConfirmedProtectedWriteReadbackClearsMatchingDisconnectedWarning() async throws {
        let a = HomeSessionFixture(), b = HomeSessionFixture(), store = HomeProtectedStoreFixture()
        await a.approve(); await b.approve()
        let factory = HomeSessionFactory([a, b])
        let model = HomeInviteModel(store: store, makeSession: { factory.next() })
        let home = "save-warning-" + UUID().uuidString
        try await firstSelection(model, home: home)
        let target = try XCTUnwrap(model.bridgeReplacementTarget)
        let selected = try XCTUnwrap(model.activeID)
        model.disconnectActive()
        let stopped = await model.waitForCleanup(id: selected)
        XCTAssertTrue(stopped); XCTAssertTrue(model.hasUnsavedAccessWarning)
        let (root, scheme, _, _) = try AppSchemeSwitchTests().fixture()
        defer { scheme.close(); try? FileManager.default.removeItem(at: root); try? FileManager.default.removeItem(at: root.appendingPathExtension("json")) }
        scheme.updateCandidate(nil, accessUnsaved: model.hasUnsavedAccessWarning)
        func warningPresent() throws -> Bool {
            let status = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
            return try XCTUnwrap(status["access_unsaved"] as? Bool)
        }
        XCTAssertTrue(try warningPresent())
        await store.allowSave(); await store.gate.arm("save-return")
        do {
            model.accept(try HomeInviteModelTests().invite(home: home))
            let written = await store.gate.waitFor("save-return")
            XCTAssertTrue(written, "Reach the production Keychain write/readback before checking its outcome")
            let readback = try await store.load(binding: target.binding, trust: target.trust)
            XCTAssertEqual(readback.binding, target.binding)
            XCTAssertNotNil(readback.networkProfileID)
            XCTAssertTrue(model.hasUnsavedAccessWarning, "Model has not received the protected result yet")
            scheme.updateCandidate(model.bridgeCandidate, accessUnsaved: model.hasUnsavedAccessWarning)
            XCTAssertTrue(try warningPresent())
            await store.gate.release(); await model.bridgeSettle()
            XCTAssertFalse(model.hasUnsavedAccessWarning)
            XCTAssertNil(model.saveFailure)
            scheme.updateCandidate(model.bridgeCandidate, accessUnsaved: model.hasUnsavedAccessWarning)
            XCTAssertFalse(try warningPresent(), "Matching confirmed write/readback clears the Main status warning")
        } catch {
            await store.gate.release(); model.cancel(); await model.bridgeSettle()
            try await store.delete(binding: target.binding)
            throw error
        }
        model.cancel(); await model.bridgeSettle()
        try await store.delete(binding: target.binding)
    }

    @MainActor
    func testCandidateCancelPreservesActiveTransportHealthAndSaveResult() async throws {
        for candidateSaveFails in [false, true] {
            let a = HomeSessionFixture(), b = HomeSessionFixture(), store = HomeStoreFixture()
            await a.approve(); await b.approve(); await store.configure(saveFails: true)
            let factory = HomeSessionFactory([a, b])
            let model = HomeInviteModel(store: store, makeSession: { factory.next() })
            try await firstSelection(model)
            let activeID = try XCTUnwrap(model.activeID), document = model.documentGeneration
            await store.configure(saveFails: candidateSaveFails)
            model.accept(try HomeInviteModelTests().invite(home: "B"))
            let ready = await HomeInviteModelTests().eventually { model.phase == .readyToSwitch && !model.busy }
            XCTAssertTrue(ready)
            XCTAssertEqual(model.activeID, activeID)
            XCTAssertTrue((try XCTUnwrap(model.appTransport) as AnyObject) === a.readiness)
            XCTAssertEqual(model.connectionStates.state, .online)
            XCTAssertEqual(model.documentGeneration, document)
            XCTAssertEqual(model.bridgeCandidate?["phase"] as? String, "ready_to_switch")
            XCTAssertEqual(model.saveFailure, .saveFailed, "The native warning still projects selected A's failure while B is ready")
            XCTAssertEqual(ConnectionClientProjection.savedAccess(model), "notSaved")
            XCTAssertTrue(model.bridgeCanRetrySave, "The warning's Retry action still targets unsaved A")
            await store.configure()
            model.cancel(); await model.bridgeSettle()
            XCTAssertEqual(model.activeID, activeID)
            XCTAssertTrue((try XCTUnwrap(model.appTransport) as AnyObject) === a.readiness)
            XCTAssertEqual(model.bridgeCandidate?["phase"] as? String, "cancelled")
            XCTAssertEqual(model.saveFailure, .saveFailed)
            XCTAssertEqual(ConnectionClientProjection.savedAccess(model), "notSaved")
            XCTAssertTrue(model.bridgeCanRetrySave)
            XCTAssertTrue(model.activeHasUnsavedAccess)
            let aStops = await a.stops, bStops = await b.stops
            XCTAssertEqual(aStops, 0); XCTAssertEqual(bStops, 1)
            let saved = await model.bridgeRetrySave()
            XCTAssertEqual(saved, .saved)
            XCTAssertFalse(model.activeHasUnsavedAccess)
            XCTAssertNil(model.saveFailure)
            XCTAssertEqual(ConnectionClientProjection.savedAccess(model), "saved")
            model.disconnectActive()
            let stopped = await model.waitForCleanup(id: activeID)
            XCTAssertTrue(stopped)
        }
    }

    @MainActor
    func testRetainedNativeDisconnectAndRemoveUseMainGatesAndConfirmSelectedCleanup() async throws {
        for removing in [false, true] {
            let session = HomeSessionFixture(), store = HomeStoreFixture()
            await session.approve()
            let model = HomeInviteModel(store: store, makeSession: { session })
            try await firstSelection(model)
            let active = try XCTUnwrap(model.activeID)
            let saved = try XCTUnwrap(model.savedHomes.first)
            let (root, scheme, view, transport) = try AppSchemeSwitchTests().fixture()
            defer { scheme.close(); try? FileManager.default.removeItem(at: root); try? FileManager.default.removeItem(at: root.appendingPathExtension("json")) }
            scheme.disconnectSelected = { model.disconnectActive(); return true }
            @MainActor func act() async -> String? {
                if removing { return await model.removeSavedSelection(using: scheme) }
                return await model.disconnectSelected(using: scheme)
            }
            let missingOwner = await act()
            XCTAssertEqual(missingOwner, "owner_save_unknown")
            XCTAssertEqual(model.activeID, active)
            try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"hold","counts":{"dirty":0,"failed":0,"pending":1,"unknown":0}}"#)
            let pendingOwner = await act()
            XCTAssertEqual(pendingOwner, "owner_save_pending")
            try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
            let mutation = SwitchSchemeTask(method: "POST", path: "/api/v1/pending", generation: scheme.generation)
            scheme.webView(view, start: mutation)
            let sent = await HomeInviteModelTests().eventually { transport.count == 1 }
            XCTAssertTrue(sent)
            let pendingTransfer = await act()
            XCTAssertEqual(pendingTransfer, "non_get_pending")
            XCTAssertEqual(model.activeID, active)
            let stopsBefore = await session.stops, deletesBefore = await store.deletedBindings
            XCTAssertEqual(stopsBefore, 0); XCTAssertTrue(deletesBefore.isEmpty)
            scheme.webView(view, stop: mutation)
            let completed = await act()
            XCTAssertNil(completed)
            XCTAssertNil(model.activeID); XCTAssertNil(model.appTransport)
            XCTAssertNil(model.appReadiness)
            XCTAssertEqual(model.connectionStates.state, .offline)
            XCTAssertEqual(model.cleanupOutcomes[active], "confirmed")
            let stops = await session.stops, deleted = await store.deletedBindings
            XCTAssertEqual(stops, 1)
            XCTAssertEqual(deleted, removing ? [saved.binding] : [])
            XCTAssertEqual(model.savedHomes.contains(saved), !removing)
            let state = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(scheme.statusJSON().utf8)) as? [String: Any])
            XCTAssertEqual(state["state"] as? String, "no_target")
            XCTAssertTrue(state["target"] is NSNull)
        }
    }

    @MainActor
    func testRetainedNativeRemoveKeepsSavedIdentityWhenSelectedStopFails() async throws {
        let session = HomeSessionFixture(), store = HomeStoreFixture()
        await session.approve(); await session.failStop()
        let model = HomeInviteModel(store: store, makeSession: { session })
        try await firstSelection(model)
        let selected = try XCTUnwrap(model.activeID), saved = try XCTUnwrap(model.savedHomes.first)
        let (root, scheme, view, _) = try AppSchemeSwitchTests().fixture()
        defer { _ = view; scheme.close(); try? FileManager.default.removeItem(at: root); try? FileManager.default.removeItem(at: root.appendingPathExtension("json")) }
        scheme.disconnectSelected = { model.disconnectActive(); return true }
        try scheme.receiveOwnerSummary(#"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#)
        let reason = await model.removeSavedSelection(using: scheme)
        XCTAssertEqual(reason, "removalIncomplete")
        XCTAssertEqual(model.cleanupOutcomes[selected], "incomplete")
        XCTAssertTrue(model.savedHomes.contains(saved))
        let deleted = await store.deletedBindings
        XCTAssertTrue(deleted.isEmpty)
        XCTAssertNil(model.appTransport, "Failed teardown is retained separately and cannot serve Main")
    }

    @MainActor
    func testCancelCommandReportsCandidateCleanupWithSelectedOwnerRetained() async throws {
        for stopFails in [false, true] {
            let a = HomeSessionFixture(), b = HomeSessionFixture()
            await a.approve(); await b.approve()
            if stopFails { await b.failStop() }
            let factory = HomeSessionFactory([a, b])
            let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { factory.next() })
            try await firstSelection(model)
            let active = try XCTUnwrap(model.activeID)
            model.accept(try HomeInviteModelTests().invite(home: "B"))
            let ready = await HomeInviteModelTests().eventually { model.phase == .readyToSwitch && !model.busy }
            XCTAssertTrue(ready)
            let result = await ConnectionWindow.cancelAttempt(model: model, catalog: ConnectionComputerCatalog())
            let reply = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(result.reply(id: "r1").utf8)) as? [String: Any])
            if stopFails {
                let error = try XCTUnwrap(reply["error"] as? [String: Any])
                XCTAssertEqual(error["code"] as? String, "outcomeUnknown")
                XCTAssertEqual(error["effect"] as? String, "unknown")
            } else {
                let action = try XCTUnwrap(reply["result"] as? [String: Any])
                XCTAssertEqual(action["localCleanup"] as? String, "confirmed")
                XCTAssertEqual(action["effect"] as? String, "confirmed")
                XCTAssertEqual(model.phase, .connected)
            }
            XCTAssertEqual(model.activeID, active)
            XCTAssertTrue((try XCTUnwrap(model.appTransport) as AnyObject) === a.readiness)
            let aStops = await a.stops, bStops = await b.stops
            XCTAssertEqual(aStops, 0); XCTAssertEqual(bStops, 1)
            model.disconnectActive()
            let cleaned = await model.waitForCleanup(id: active)
            XCTAssertTrue(cleaned)
        }
    }

    @MainActor
    func testFailureAndSameHomeRefusalPreserveActiveWithoutSecondIdentity() async throws {
        let a = HomeSessionFixture(), b = HomeSessionFixture(), store = HomeStoreFixture()
        await a.approve(); await b.configure(join: .controlUnreachable)
        let factory = HomeSessionFactory([a, b])
        let model = HomeInviteModel(store: store, makeSession: { factory.next() })
        try await firstSelection(model)
        let activeID = try XCTUnwrap(model.activeID)
        model.accept(try HomeInviteModelTests().invite(home: "B"))
        let failed = await HomeInviteModelTests().eventually { model.phase == .failed(.controlUnreachable) && !model.busy }
        XCTAssertTrue(failed)
        XCTAssertEqual(model.activeID, activeID)
        XCTAssertEqual(model.connectionStates.state, .online)
        XCTAssertEqual(model.bridgeCandidate?["phase"] as? String, "failed")
        model.accept(try HomeInviteModelTests().invite(dialIP: "192.168.1.41", home: "A"))
        let refused = await HomeInviteModelTests().eventually { model.phase == .failed(.identityUnavailable) && !model.busy }
        XCTAssertTrue(refused)
        model.selectedHomeID = model.savedHomes.first?.id
        model.reconnect()
        XCTAssertEqual(factory.count, 2, "Neither changed-address invite nor saved reconnect may duplicate A")
        XCTAssertTrue((try XCTUnwrap(model.appTransport) as AnyObject) === a.readiness)
        let stops = await a.stops
        XCTAssertEqual(stops, 0)
        model.disconnectActive()
        let stopped = await model.waitForCleanup(id: activeID)
        XCTAssertTrue(stopped)
    }

    @MainActor
    func testAdmittedSwitchRetainsIncompleteOldCleanupWithoutRollback() async throws {
        let a = HomeSessionFixture(), b = HomeSessionFixture()
        await a.approve(); await b.approve(); await a.failStop()
        let factory = HomeSessionFactory([a, b])
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { factory.next() })
        try await firstSelection(model)
        let previous = try XCTUnwrap(model.activeID), document = model.documentGeneration
        model.accept(try HomeInviteModelTests().invite(home: "B"))
        let ready = await HomeInviteModelTests().eventually { model.phase == .readyToSwitch && !model.busy }
        XCTAssertTrue(ready)
        try model.commitCandidate()
        XCTAssertNotEqual(model.activeID, previous)
        XCTAssertEqual(model.documentGeneration, document)
        XCTAssertTrue((try XCTUnwrap(model.appTransport) as AnyObject) === b.readiness)
        XCTAssertEqual(model.cleanupOutcomes[previous], "unknown")
        let cleaned = await model.waitForCleanup(id: previous)
        XCTAssertFalse(cleaned)
        XCTAssertEqual(model.cleanupOutcomes[previous], "incomplete")
        XCTAssertEqual(model.phase, .connected)
        XCTAssertNil(model.bridgeCandidate)
        model.accept(try HomeInviteModelTests().invite(home: "A"))
        let refused = await HomeInviteModelTests().eventually { model.phase == .failed(.identityUnavailable) && !model.busy }
        XCTAssertTrue(refused)
        XCTAssertEqual(factory.count, 2, "Unknown old cleanup cannot authorize identity reuse")
        let current = try XCTUnwrap(model.activeID)
        model.disconnectActive()
        let stopped = await model.waitForCleanup(id: current)
        XCTAssertTrue(stopped)
    }

    @MainActor
    func testExistingLocalMainLetsCandidateBecomeReadyWithoutReplacingDocument() async throws {
        let session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
        let document = model.documentGeneration
        model.mainDocumentAdmitted()
        model.accept(try HomeInviteModelTests().invite())
        let ready = await HomeInviteModelTests().eventually { model.phase == .readyToSwitch && !model.busy }
        XCTAssertTrue(ready)
        XCTAssertFalse(model.hasActiveSession)
        XCTAssertEqual(model.documentGeneration, document)
        try model.commitCandidate()
        let id = try XCTUnwrap(model.activeID)
        model.disconnectActive()
        let stopped = await model.waitForCleanup(id: id)
        XCTAssertTrue(stopped)
    }

    @MainActor
    func testActiveRevocationRemovesOnlyItsSavedIdentityWhileCandidateSurvives() async throws {
        let a = HomeSessionFixture(), b = HomeSessionFixture(), store = HomeStoreFixture()
        await a.approve(); await b.approve()
        let factory = HomeSessionFactory([a, b])
        let model = HomeInviteModel(store: store, makeSession: { factory.next() })
        try await firstSelection(model)
        let previous = try XCTUnwrap(model.activeID)
        let initial = try await store.savedHomes().credentials
        let aBinding = try XCTUnwrap(initial.first?.binding)
        model.accept(try HomeInviteModelTests().invite(home: "B"))
        let ready = await HomeInviteModelTests().eventually { model.phase == .readyToSwitch && !model.busy }
        XCTAssertTrue(ready)
        let candidate = model.candidateID
        await a.configure(access: .revoked)
        let revoked = await HomeInviteModelTests().eventually(timeout: 7) { model.selectedFailure == .revoked }
        XCTAssertTrue(revoked)
        let removed = await model.waitForCleanup(id: previous)
        XCTAssertTrue(removed)
        XCTAssertEqual(model.candidateID, candidate)
        XCTAssertEqual(model.phase, .readyToSwitch)
        let remaining = try await store.savedHomes().credentials
        let deleted = await store.deletedBindings
        XCTAssertEqual(remaining.map { $0.trust.homeID }, ["B"])
        XCTAssertEqual(deleted, [aBinding])
        let bStops = await b.stops
        XCTAssertEqual(bStops, 0)
        try model.commitCandidate()
        let id = try XCTUnwrap(model.activeID)
        model.disconnectActive()
        let stopped = await model.waitForCleanup(id: id)
        XCTAssertTrue(stopped)
    }
}
