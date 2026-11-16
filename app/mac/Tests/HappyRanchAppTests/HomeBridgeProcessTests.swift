import XCTest
import Foundation
@testable import HappyRanchSupervisor

final class HomeBridgeProcessTests: XCTestCase {
    private func trust(_ fingerprint: String = "synthetic-pin") -> SavedHomeTrust {
        SavedHomeTrust(homeID: "home", controlPlaneID: "control", connectorID: "connector",
            controlURL: "https://192.168.1.40:8443", homeEndpoint: "100.64.0.1:443",
            caDER: Data("synthetic-public-ca".utf8), caSHA256: fingerprint)
    }
    func testFixedEnvironmentAndPipeOnlyHandoff() throws {
        let fake = FakeProcessController()
        let helper = HomeBridgeProcess(controller: fake, executable: URL(fileURLWithPath: "/bundle/Contents/Helpers/happyranch-home-bridge"))
        defer { try? helper.stop() }
        let secret = Data("JOIN-KEY-CANARY".utf8)
        try helper.start(trust: trust(), hostname: "synthetic-mac", stateDirectory: "/synthetic/profile", joinKey: secret)
        XCTAssertTrue(fake.lastArguments == ["--home-bridge-v1"], "Only fixed helper mode may enter argv")
        XCTAssertTrue(fake.lastEnvironment == ["GODEBUG": "x509usefallbackroots=1"], "Only fixed Go root mode may enter env")
        let input = try XCTUnwrap(fake.lastPipedInput)
        let length = try XCTUnwrap(input.read(upToCount: 4))
        let size = length.reduce(0) { ($0 << 8) | Int($1) }
        let data = try XCTUnwrap(input.read(upToCount: size))
        let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: String])
        XCTAssertTrue(fields["joinKey"].map { Data($0.utf8) == secret } == true, "Join key must cross private stdin")
        XCTAssertTrue(fields["stateDirectory"] == "/synthetic/profile", "State path must cross private stdin")
        XCTAssertTrue(fields["ca"] == trust().caDER.base64EncodedString(), "Exact CA must cross private stdin")
        XCTAssertFalse(String(describing: helper).contains("JOIN-KEY-CANARY"), "No secret reflection")
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
}
