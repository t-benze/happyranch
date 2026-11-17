import XCTest
import Foundation
@testable import HappyRanchSupervisor

final class HomeBridgeProcessTests: XCTestCase {
    private func trust(_ fingerprint: String = "synthetic-pin") -> SavedHomeTrust {
        SavedHomeTrust(homeID: "home", controlPlaneID: "control", connectorID: "connector",
            controlURL: "https://control.happyranch.invalid:8443", dialIP: "192.168.1.40", homeEndpoint: "100.64.0.1:443",
            caDER: Data("synthetic-public-ca".utf8), caSHA256: fingerprint)
    }
    func testFixedEnvironmentAndPipeOnlyHandoff() throws {
        let fake = FakeProcessController()
        let helper = HomeBridgeProcess(controller: fake, executable: URL(fileURLWithPath: "/bundle/Contents/Helpers/happyranch-home-bridge"))
        defer { try? helper.stop() }
        let secret = Data("JOIN-KEY-CANARY".utf8)
        try helper.start(trust: trust(), hostname: "synthetic-mac", stateDirectory: "/synthetic/profile", joinKey: secret)
        XCTAssertTrue(fake.lastArguments == ["--home-bridge-v2"], "Only fixed helper mode may enter argv")
        XCTAssertTrue(fake.lastEnvironment == [:], "No inherited proxy/trust/debug/keylog variables may enter env")
        let input = try XCTUnwrap(fake.lastPipedInput)
        let length = try XCTUnwrap(input.read(upToCount: 4))
        let size = length.reduce(0) { ($0 << 8) | Int($1) }
        let data = try XCTUnwrap(input.read(upToCount: size))
        let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertTrue((fields["joinKey"] as? String).map { Data($0.utf8) == secret } == true, "Join key must cross private stdin")
        XCTAssertTrue(fields["stateDirectory"] as? String == "/synthetic/profile", "State path must cross private stdin")
        XCTAssertTrue(fields["caDER"] as? String == trust().caDER.base64EncodedString(), "Exact CA must cross private stdin")
        XCTAssertTrue(fields["version"] as? Int == 2)
        XCTAssertTrue(fields["dialIP"] as? String == trust().dialIP)
        XCTAssertTrue(fields["homeID"] as? String == trust().homeID)
        XCTAssertTrue(fields["controlPlaneID"] as? String == trust().controlPlaneID)
        XCTAssertEqual(Set(fields.keys), ["version", "caDER", "fingerprint", "dialIP", "homeID", "controlPlaneID", "controlURL", "hostname", "stateDirectory", "joinKey", "socketPath", "target"])
        XCTAssertEqual(helper.stableNodeID, "synthetic-stable-node")
        XCTAssertFalse(String(describing: helper).contains("JOIN-KEY-CANARY"), "No secret reflection")
    }
    func testV1TrustAndMissingNativeIdentityNeverAdmit() throws {
        let fake = FakeProcessController()
        let helper = HomeBridgeProcess(controller: fake, executable: URL(fileURLWithPath: "/bundle/helper"))
        defer { try? helper.stop() }
        let t = trust()
        let old = SavedHomeTrust(version: 1, homeID: t.homeID, controlPlaneID: t.controlPlaneID,
            connectorID: t.connectorID, controlURL: "https://192.168.1.40:8443", dialIP: t.dialIP,
            homeEndpoint: t.homeEndpoint, caDER: t.caDER, caSHA256: t.caSHA256)
        XCTAssertThrowsError(try helper.start(trust: old, hostname: "synthetic-mac", stateDirectory: "/synthetic/old", joinKey: Data())) { error in
            XCTAssertTrue(error as? ManagedNetworkError == .homeReapprovalRequired)
        }
        XCTAssertEqual(fake.launchCallCount, 0)
        fake.pipedReadyResponse = Data("0\n".utf8)
        XCTAssertThrowsError(try helper.start(trust: t, hostname: "synthetic-mac", stateDirectory: "/synthetic/no-node", joinKey: Data()))
        XCTAssertNil(helper.stableNodeID)
    }

    func testOneProcessPerProfileAndTrustChangeRestarts() throws {
        let fake = FakeProcessController()
        let helper = HomeBridgeProcess(controller: fake, executable: URL(fileURLWithPath: "/bundle/helper"))
        let other = HomeBridgeProcess(controller: FakeProcessController(), executable: URL(fileURLWithPath: "/bundle/helper"))
        defer { try? helper.stop(); try? other.stop() }
        try helper.start(trust: trust(), hostname: "synthetic-mac", stateDirectory: "/synthetic/one-profile", joinKey: Data())
        try helper.start(trust: trust(), hostname: "synthetic-mac", stateDirectory: "/synthetic/one-profile", joinKey: Data())
        XCTAssertEqual(fake.launchCallCount, 1)
        XCTAssertThrowsError(try other.start(trust: trust(), hostname: "synthetic-mac", stateDirectory: "/synthetic/one-profile", joinKey: Data()))
        try helper.start(trust: trust("changed-pin"), hostname: "synthetic-mac", stateDirectory: "/synthetic/one-profile", joinKey: Data())
        XCTAssertEqual(fake.launchCallCount, 2)
        XCTAssertEqual(fake.terminateCallCount, 1)
    }
    func testCancellationBeforeLaunchRefusesWithoutProcess() throws {
        let fake = FakeProcessController()
        let helper = HomeBridgeProcess(controller: fake, executable: URL(fileURLWithPath: "/bundle/helper"))
        helper.cancel()
        XCTAssertThrowsError(try helper.start(trust: trust(), hostname: "synthetic-mac", stateDirectory: "/synthetic/cancelled", joinKey: Data()))
        XCTAssertEqual(fake.launchCallCount, 0)
    }
    func testSessionAddressRecoveryUsesEmptyKeyAndPrivatePipe() async throws {
        let fake = FakeProcessController()
        let session = ManagedHomeInviteSession(controller: fake, executable: URL(fileURLWithPath: "/bundle/helper"))
        let original = trust()
        let target = SavedHomeTrust(homeID: original.homeID, controlPlaneID: original.controlPlaneID,
            connectorID: original.connectorID, controlURL: original.controlURL, dialIP: "192.168.1.41",
            homeEndpoint: original.homeEndpoint, caDER: original.caDER, caSHA256: original.caSHA256)
        let profile = UUID()
        let canary = "SYNTHETIC-RECOVERY-PIPE-CANARY"
        let credential = try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: original.homeID,
            controlPlaneID: original.controlPlaneID, stableNodeID: "synthetic-stable-node"), trust: original,
            deliveredBearer: Data(canary.utf8), networkProfileID: profile)
        // Fake process acknowledges launch only. Native grant/node enforcement
        // belongs to Go tests; this test owns session -> bridge -> pipe wiring.
        let updated = try await session.recoverAddress(credential, to: target)
        XCTAssertEqual(updated.trust, target)
        XCTAssertEqual(updated.binding, credential.binding)
        let input = try XCTUnwrap(fake.lastPipedInput)
        let length = try XCTUnwrap(input.read(upToCount: 4))
        let size = length.reduce(0) { ($0 << 8) | Int($1) }
        let data = try XCTUnwrap(input.read(upToCount: size))
        let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let recovery = try XCTUnwrap(fields["recovery"] as? [String: String])
        XCTAssertTrue(fields["joinKey"] as? String == "")
        XCTAssertTrue(fields["controlURL"] as? String == target.controlURL)
        XCTAssertTrue(recovery["previousDialIP"] == original.dialIP)
        XCTAssertTrue(recovery["stableNodeID"] == credential.binding.stableNodeID)
        XCTAssertTrue(recovery["bearer"] == canary, "Saved grant must cross only the private pipe")
        XCTAssertTrue(fake.lastArguments == ["--home-bridge-v2"])
        XCTAssertTrue(fake.lastEnvironment == [:])
        XCTAssertFalse(String(describing: session).contains(canary))
        try await session.stop()
    }

}
