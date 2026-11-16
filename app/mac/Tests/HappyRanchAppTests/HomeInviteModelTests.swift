import Foundation
import Combine
import CryptoKit
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

private actor HomeStoreFixture: SavedHomeCredentialStore {
    var credentials: [SavedDeviceCredential] = []
    var readFailure: SavedAccessFailure?
    var saveFails = false
    var deleteFails = false
    private(set) var saves = 0
    func configure(read: SavedAccessFailure? = nil, saveFails: Bool = false, deleteFails: Bool = false) {
        readFailure = read; self.saveFails = saveFails; self.deleteFails = deleteFails
    }
    func savedHomes() async throws -> [SavedDeviceCredential] {
        if let readFailure { throw readFailure }; return credentials
    }
    func save(_ credential: SavedDeviceCredential) async throws {
        saves += 1
        if saveFails { throw SavedAccessFailure.saveFailed }
        credentials.removeAll { $0.binding == credential.binding }; credentials.append(credential)
    }
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        if let readFailure { throw readFailure }
        guard let credential = credentials.first(where: { $0.binding == binding && $0.trust == trust }) else { throw SavedAccessFailure.notFound }
        return credential
    }
    func delete(binding: SavedDeviceBinding) async throws {
        if deleteFails { throw SavedAccessFailure.deletionFailed }
        credentials.removeAll { $0.binding == binding }
    }
}

private actor HomeSessionFixture: HomeInviteSession {
    var joinFailure: HomeConnectionFailure?
    var accessFailure: HomeConnectionFailure?
    private(set) var joins = 0
    private(set) var emptyKeys = 0
    private(set) var completions = 0
    private var approved = false
    func configure(join: HomeConnectionFailure? = nil, access: HomeConnectionFailure? = nil) {
        joinFailure = join; accessFailure = access
    }
    func approve() { approved = true }
    func join(trust: SavedHomeTrust, profileID: UUID, key: Data?) async throws {
        joins += 1
        if key == nil { emptyKeys += 1 }
        if let joinFailure { throw joinFailure }
    }
    func pair(_ invite: HomePairingInvitation) async throws -> PendingHomeApproval {
        try PendingHomeApproval(comparisonPhrase: "pair-1234-abcd-5678-efab", expiresAt: invite.expiresAt,
            completionHandle: String(repeating: "a", count: 64))
    }
    func complete(_ pending: PendingHomeApproval, trust: SavedHomeTrust, profileID: UUID) async throws -> SavedDeviceCredential? {
        completions += 1
        guard approved else { return nil }
        return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
            controlPlaneID: trust.controlPlaneID, stableNodeID: "synthetic-stable-node"), trust: trust,
            deliveredBearer: Data("hrpin_1_SYNTHETIC-BEARER-CANARY".utf8), networkProfileID: profileID)
    }
    func open(_ credential: SavedDeviceCredential) async throws -> URL {
        try await checkAccess()
        return URL(string: "http://127.0.0.1:49152/")!
    }
    func checkAccess() async throws { if let accessFailure { throw accessFailure } }
    func stop() async throws {}
}

@Suite("Single home invite UI contract")
@MainActor
struct HomeInviteModelTests {
    // Public synthetic CA; no private key is present in this fixture.
    private let ca = "MIIBcDCCARegAwIBAgIBAzAKBggqhkjOPQQDAjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwHhcNMjMxMTE0MjIxMzIwWhcNMzMwNTE4MDMzMzIwWjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQn+y+9zcuDcMqqUHzKu60sYl6vJtfgX8DR1VfNh6XtkNUwTmInfcpYhOhYzq94Nc4xe87GBk1JAN43UK9cLaDAo0IwQDAOBgNVHQ8BAf8EBAMCAgQwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUyJSU09xAckvJECBK6NS3R58e9wswCgYIKoZIzj0EAwIDRwAwRAIgSGTa05eLWaPEQa9fYBwp979EMWXfu6F1aLr+IlLxlqMCIAv6++jkLKpTs+XT4vP9ETW/CdjHrUHv1hqkirv2ErYF"

    private func invite(expired: Bool = false, ttl: Int = 299, control: String = "https://192.168.1.40:8443") throws -> String {
        func wire(_ data: Data) -> String { data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
        let now = Int(Date().timeIntervalSince1970)
        let fields: [String: Any] = ["v": 1, "mode": "home", "control_url": control,
            "control_plane_id": "control", "home_id": "home", "connector_id": "connector", "home_endpoint": "100.64.0.1:443",
            "control_tls": ["kind": "local_ca", "ca_der": wire(Data(base64Encoded: ca)!), "sha256": certificateDigest(Data(base64Encoded: ca)!)],
            "invite_id": wire(Data(repeating: 1, count: 16)), "join_key": String(repeating: "ab", count: 24),
            "pair_secret": wire(Data(repeating: 2, count: 32)), "issued_at": now - (expired ? 301 : 1), "expires_at": now + (expired ? -1 : ttl)]
        return "hrinvite1:" + wire(try JSONSerialization.data(withJSONObject: fields))
    }

    private func eventually(_ predicate: @MainActor () -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(5)
        while !predicate(), Date() < deadline { try? await Task.sleep(for: .milliseconds(10)) }
        return predicate()
    }

    @Test("Join and approval never publish Connected before the expected web load")
    func approvalAndWebLoadGate() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        let model = HomeInviteModel(store: store, makeSession: { session })
        var phases: [HomeInviteModel.Phase] = []
        let observation = model.$phase.sink { phases.append($0) }
        defer { observation.cancel(); model.cancel() }
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
        model.webLoaded(url: url, attempt: UUID())
        #expect(model.phase == .opening)
        model.webLoaded(url: URL(string: "http://127.0.0.1:49153/")!, attempt: model.attemptID)
        #expect(model.phase == .opening)
        model.webLoaded(url: url, attempt: model.attemptID)
        #expect(model.phase == .connected)
        #expect(model.expiresAt == nil && model.comparisonPhrase == nil)
        #expect(phases.filter { [.validating, .joining, .pairing, .awaitingApproval, .opening, .connected].contains($0) } ==
            [.validating, .joining, .pairing, .awaitingApproval, .opening, .connected])
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
        let old = model.attemptID
        model.cancel()
        #expect(await eventually { model.phase == .idle && !model.busy })
        await session.approve()
        model.webLoaded(url: URL(string: "http://127.0.0.1:49152/")!, attempt: old)
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

    @Test("Save retry retains delivery, reopen uses no key, and Keychain failure stays visible")
    func savedAccessRecovery() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await store.configure(saveFails: true); await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.phase == .opening && model.saveFailure == .saveFailed })
        model.webLoaded(url: try #require(model.webURL), attempt: model.attemptID)
        await store.configure()
        model.retrySave()
        #expect(await eventually { model.saveFailure == nil && !model.savedHomes.isEmpty && !model.busy })
        model.cancel()
        #expect(await eventually { !model.busy })
        let reopen = HomeInviteModel(store: store, makeSession: { session })
        reopen.prepare()
        #expect(await eventually { reopen.phase == .opening && reopen.webURL != nil })
        reopen.webLoaded(url: try #require(reopen.webURL), attempt: reopen.attemptID)
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

    @Test("Local deletion failure reports what remains and Retry removal erases the record")
    func removalFailureAndRetry() async throws {
        let store = HomeStoreFixture(), session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: store, makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        model.cancel()
        #expect(await eventually { !model.busy })
        await store.configure(deleteFails: true)
        model.removeSavedComputer()
        #expect(await eventually { model.phase == .keychain(.deletionFailed) && !model.busy })
        #expect(!model.removalFailures.isEmpty && !model.savedHomes.isEmpty)
        await store.configure()
        model.removeSavedComputer()
        #expect(await eventually { model.phase == .idle && !model.busy })
        #expect(model.savedHomes.isEmpty && model.removalFailures.isEmpty)
        #expect(try await store.savedHomes().isEmpty)
    }

    @Test("Same CA with changed URL visibly refuses before touching saved membership")
    func changedAddressRemainsClosed() async throws {
        let session = HomeSessionFixture()
        await session.approve()
        let model = HomeInviteModel(store: HomeStoreFixture(), makeSession: { session })
        model.accept(try invite())
        #expect(await eventually { model.webURL != nil })
        model.cancel()
        #expect(await eventually { !model.busy })
        model.accept(try invite(control: "https://192.168.1.41:8443"))
        #expect(await eventually { model.phase == .failed(.addressChanged) && !model.busy })
        #expect(await session.joins == 1)
        #expect(model.webURL == nil && !model.savedHomes.isEmpty)
    }
}

private func certificateDigest(_ ca: Data) -> String {
    SHA256.hash(data: ca).map { String(format: "%02x", $0) }.joined()
}
