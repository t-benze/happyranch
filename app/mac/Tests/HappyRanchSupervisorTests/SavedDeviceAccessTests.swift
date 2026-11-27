import XCTest
import Foundation
@testable import HappyRanchSupervisor

private actor FakeSavedDeviceStore: SavedDeviceCredentialStore {
    var failure: SavedAccessFailure?
    var record: SavedDeviceCredential?
    private(set) var saves = 0
    func configure(_ failure: SavedAccessFailure?, record: SavedDeviceCredential? = nil) {
        self.failure = failure; self.record = record
    }
    func save(_ credential: SavedDeviceCredential) async throws {
        saves += 1
        if let failure { throw failure }
        record = credential
    }
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        if let failure { throw failure }
        guard let record else { throw SavedAccessFailure.notFound }
        return record
    }
    func delete(binding: SavedDeviceBinding) async throws {
        if let failure { throw failure }
        record = nil
    }
}

final class SavedDeviceAccessTests: XCTestCase, @unchecked Sendable {
    private func credential(node: String = "stable-node") throws -> SavedDeviceCredential {
        let trust = try RemoteInviteTests.trust()
        return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
            controlPlaneID: trust.controlPlaneID, stableNodeID: node), trust: trust,
            deliveredBearer: Data("SYNTHETIC-BEARER-CANARY".utf8))
    }

    func testLoadOutcomesRemainDistinct() async throws {
        let credential = try credential()
        let store = FakeSavedDeviceStore()
        let model = SavedAccessModel(store: store)
        for failure in SavedAccessFailure.allCases {
            await store.configure(failure)
            await model.load(binding: credential.binding, trust: credential.trust)
            let state = await model.state
            let current = await model.currentCredential()
            XCTAssertEqual(state, .failed(failure))
            XCTAssertNil(current)
        }
        await store.configure(nil, record: credential)
        await model.load(binding: credential.binding, trust: credential.trust)
        let state = await model.state
        XCTAssertEqual(state, .available, "Custody availability is not Connected")
    }

    func testSaveFailureKeepsDeliveryForRetry() async throws {
        let store = FakeSavedDeviceStore()
        let model = SavedAccessModel(store: store)
        await store.configure(.locked)
        await model.saveAfterDelivery(try credential())
        let failed = await model.state
        let retained = await model.currentCredential()
        XCTAssertEqual(failed, .failed(.saveFailed))
        XCTAssertNotNil(retained, "Failed save must preserve the delivered grant in memory")
        await store.configure(nil)
        await model.retrySave()
        let saved = await model.state
        let saves = await store.saves
        XCTAssertEqual(saved, .saved)
        XCTAssertEqual(saves, 2)
    }

    func testRemovalFailureIsVisibleAndRetryable() async throws {
        let credential = try credential()
        let store = FakeSavedDeviceStore()
        let model = SavedAccessModel(store: store)
        await store.configure(nil, record: credential)
        await model.load(binding: credential.binding, trust: credential.trust)
        await store.configure(.denied, record: credential)
        await model.remove(binding: credential.binding)
        let failed = await model.state
        let memory = await model.currentCredential()
        XCTAssertEqual(failed, .failed(.deletionFailed))
        XCTAssertNil(memory)
        await store.configure(nil, record: credential)
        await model.remove(binding: credential.binding)
        let removed = await model.state
        XCTAssertEqual(removed, .removed)
    }

    func testBindingAndTrustCannotBeSubstituted() async throws {
        let credential = try credential()
        let otherNode = try self.credential(node: "another-stable-node")
        XCTAssertNotEqual(credential.binding.account, otherNode.binding.account)
        let store = FakeSavedDeviceStore()
        let model = SavedAccessModel(store: store)
        await store.configure(nil, record: otherNode)
        await model.load(binding: credential.binding, trust: credential.trust)
        let wrongNode = await model.state
        XCTAssertEqual(wrongNode, .failed(.identityChanged))
        let trust = credential.trust
        let changedCA = SavedHomeTrust(homeID: trust.homeID, controlPlaneID: trust.controlPlaneID,
            connectorID: trust.connectorID, controlURL: trust.controlURL, dialIP: trust.dialIP, homeEndpoint: trust.homeEndpoint,
            caDER: trust.caDER, caSHA256: String(repeating: "0", count: 64))
        await store.configure(nil, record: credential)
        await model.load(binding: credential.binding, trust: changedCA)
        let wrongCA = await model.state
        XCTAssertEqual(wrongCA, .failed(.identityChanged), "Different CA must never become TOFU")
    }

    func testAddressUpdatePreservesGrantAndRefusesTrustChanges() throws {
        let original = try credential()
        let t = original.trust
        let saved = try SavedDeviceCredential(binding: original.binding, trust: t,
            deliveredBearer: original.withBearer { $0 }, networkProfileID: UUID())
        func changed(_ part: String) -> SavedHomeTrust {
            SavedHomeTrust(homeID: part == "home" ? "other" : t.homeID,
                controlPlaneID: part == "control" ? "other" : t.controlPlaneID,
                connectorID: part == "connector" ? "other" : t.connectorID,
                controlURL: part == "http" ? "http://control.happyranch.invalid:28443" : t.controlURL,
                dialIP: "192.168.1.41",
                homeEndpoint: part == "endpoint" ? "100.64.0.2:443" : t.homeEndpoint,
                caDER: part == "ca" ? Data("different-synthetic-ca".utf8) : t.caDER,
                caSHA256: part == "fingerprint" ? String(repeating: "0", count: 64) : t.caSHA256)
        }
        let updated = try saved.updatingAddress(to: changed("url"))
        XCTAssertEqual(updated.trust.version, 2)
        XCTAssertEqual(updated.trust.dialIP, "192.168.1.41")
        XCTAssertEqual(updated.trust.controlURL, saved.trust.controlURL)
        XCTAssertEqual(updated.binding, saved.binding)
        XCTAssertEqual(updated.networkProfileID, saved.networkProfileID)
        XCTAssertTrue(updated.withBearer { new in saved.withBearer { new == $0 } })
        for part in ["home", "control", "connector", "endpoint", "ca", "fingerprint", "http"] {
            XCTAssertThrowsError(try saved.updatingAddress(to: changed(part)), part)
        }
        XCTAssertThrowsError(try original.updatingAddress(to: changed("url")), "Missing protected profile cannot recover a node")
        let display = String(describing: updated) + String(reflecting: updated)
        XCTAssertFalse(display.contains("SYNTHETIC-BEARER-CANARY"))
    }

    func testOldHomePortSavedProfileRefuses() throws {
        let record = try credential()
        let bytes = try JSONEncoder().encode(record)
        var fields = try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        var trust = try XCTUnwrap(fields["trust"] as? [String: Any])
        trust["controlURL"] = "https://control.happyranch.invalid:8443"
        fields["trust"] = trust
        XCTAssertThrowsError(try JSONDecoder().decode(SavedDeviceCredential.self,
            from: JSONSerialization.data(withJSONObject: fields))) { error in
                XCTAssertEqual(error as? RemoteInviteError, .url)
        }
    }

    func testV1SavedProfilesRefuseWithoutReinterpretingBindings() throws {
        let record = try credential()
        let bytes = try JSONEncoder().encode(record)
        let original = try XCTUnwrap(JSONSerialization.jsonObject(with: bytes) as? [String: Any])
        for version in [nil, 1] as [Int?] {
            var fields = original
            var trust = try XCTUnwrap(fields["trust"] as? [String: Any])
            if let version { trust["version"] = version } else { trust.removeValue(forKey: "version") }
            trust.removeValue(forKey: "dialIP")
            trust["controlURL"] = "https://192.168.1.40:8443"
            fields["trust"] = trust
            XCTAssertThrowsError(try JSONDecoder().decode(SavedDeviceCredential.self,
                from: JSONSerialization.data(withJSONObject: fields))) { error in
                XCTAssertTrue(error as? SavedAccessFailure == .reapprovalRequired)
            }
        }
        XCTAssertEqual(ManagedNetworkError(rawValue: -21), .homeReapprovalRequired)
        XCTAssertEqual(HomeConnectionFailure.joinFailure(ManagedNetworkError.homeReapprovalRequired), .reapprovalRequired)
    }

    func testSavedCredentialAndFailureMessagesDoNotLeak() throws {
        let credential = try credential()
        let rendered = String(describing: credential) + String(reflecting: credential) + String(describing: credential.customMirror.children)
        XCTAssertFalse(rendered.contains("SYNTHETIC-BEARER-CANARY"))
        for failure in SavedAccessFailure.allCases {
            XCTAssertFalse((String(describing: failure) + failure.localizedDescription).contains("SYNTHETIC-BEARER-CANARY"))
        }
        XCTAssertTrue(SavedAccessFailure.identityChanged.localizedDescription.contains("after this update"))
        XCTAssertTrue(SavedAccessFailure.denied.localizedDescription.contains("new invite on Linux"))
        XCTAssertTrue(SavedAccessFailure.saveFailed.localizedDescription.contains("Access was not saved"))
        XCTAssertTrue(SavedAccessFailure.deletionFailed.localizedDescription.contains("Removal incomplete"))
    }
}
