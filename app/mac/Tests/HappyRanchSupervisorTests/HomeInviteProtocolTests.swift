import XCTest
import Foundation
@testable import HappyRanchSupervisor

final class HomeInviteProtocolTests: XCTestCase {
    private func response(_ status: Int, _ fields: [String: Any]) throws -> HomeHTTPResponse {
        HomeHTTPResponse(status: status, body: try JSONSerialization.data(withJSONObject: fields))
    }

    func testPendingHandleIsBoundedAndCannotExtendInviteDeadline() throws {
        let deadline = Date(timeIntervalSince1970: 1_800_000_300)
        var fields: [String: Any] = ["state": "pending-approval", "completion_handle": String(repeating: "a", count: 64),
            "comparison_phrase": "pair-1234-abcd-5678-efab", "expires_at": "2027-01-15T08:05:00+00:00"]
        let pending = try ManagedHomeInviteSession.pending(response(200, fields), deadline: deadline)
        XCTAssertEqual(pending.expiresAt, deadline)
        XCTAssertTrue(pending.requestBody() == Data(String(repeating: "a", count: 64).utf8), "Completion sends raw UTF-8 handle only")
        fields["expires_at"] = "2027-01-15T08:05:00.000000+00:00"
        XCTAssertNoThrow(try ManagedHomeInviteSession.pending(response(200, fields), deadline: deadline))
        XCTAssertThrowsError(try ManagedHomeInviteSession.pending(response(202, fields), deadline: deadline))
        fields["expires_at"] = "2027-01-15T08:05:01+00:00"
        XCTAssertThrowsError(try ManagedHomeInviteSession.pending(response(200, fields), deadline: deadline))
        for invalid in ["1800000300", "2027-01-15T08:05:00", "not-a-date"] {
            fields["expires_at"] = invalid
            XCTAssertThrowsError(try ManagedHomeInviteSession.pending(response(200, fields), deadline: deadline))
        }
        fields["expires_at"] = "2027-01-15T08:05:00+00:00"
        fields["unexpected"] = "synthetic"
        XCTAssertThrowsError(try ManagedHomeInviteSession.pending(response(200, fields), deadline: deadline))
        let rendered = String(describing: pending) + String(reflecting: pending) + String(describing: pending.customMirror.children)
        XCTAssertFalse(rendered.contains(String(repeating: "a", count: 64)), "Pending handle must stay redacted")
    }

    func testDeliveredCredentialUsesNativeNodeAndRefusesInventedServerIdentity() throws {
        let trust = try RemoteInviteTests.trust()
        var fields: [String: Any] = ["credential": "hrpin_1_SYNTHETIC-BEARER-CANARY"]
        let profile = UUID()
        let delivered = try XCTUnwrap(ManagedHomeInviteSession.delivered(response(200, fields), trust: trust,
            profileID: profile, stableNodeID: "native-stable-node"))
        XCTAssertNil(try ManagedHomeInviteSession.delivered(response(403, ["error": "pairing_denied"]), trust: trust,
            profileID: profile, stableNodeID: "native-stable-node"))
        for (status, body) in [(202, ["credential": "synthetic"]), (403, ["error": "unknown"]),
                               (403, ["error": "pairing_denied", "unexpected": "value"])] {
            XCTAssertThrowsError(try ManagedHomeInviteSession.delivered(response(status, body), trust: trust,
                profileID: profile, stableNodeID: "native-stable-node"))
        }
        XCTAssertEqual(delivered.networkProfileID, profile)
        XCTAssertEqual(delivered.binding.stableNodeID, "native-stable-node")
        XCTAssertEqual(delivered.binding.homeID, trust.homeID)
        XCTAssertEqual(delivered.binding.controlPlaneID, trust.controlPlaneID)
        for key in ["stable_node_id", "home_id", "control_plane_id", "connector_id"] {
            fields[key] = "substituted"
            XCTAssertThrowsError(try ManagedHomeInviteSession.delivered(response(200, fields), trust: trust,
                profileID: profile, stableNodeID: "native-stable-node"), key)
            fields.removeValue(forKey: key)
        }
        fields["credential"] = "synthetic\r\nHeader: injection"
        XCTAssertThrowsError(try ManagedHomeInviteSession.delivered(response(200, fields), trust: trust,
            profileID: profile, stableNodeID: "native-stable-node"))
        XCTAssertThrowsError(try ManagedHomeInviteSession.delivered(response(200, ["credential": "synthetic"]), trust: trust,
            profileID: profile, stableNodeID: ""))
        let roundTrip = try JSONDecoder().decode(SavedDeviceCredential.self, from: JSONEncoder().encode(delivered))
        XCTAssertEqual(roundTrip.networkProfileID, profile)
    }

    func testOnlyClosedServerCodesDistinguishRevocationAndDaemonOutage() throws {
        for (status, fields, expected) in [
            (403, ["error": "revocation_denied"], HomeConnectionFailure.revoked),
            (403, ["error": "revocation_stream_closed"], .revoked),
            (403, ["error": "daemon_unavailable"], .daemonUnavailable),
            (403, ["error": "unknown"], .approvalDenied),
            (503, ["error": "daemon_unavailable"], .daemonUnavailable),
            (503, ["error": "unknown"], .homeUnavailable),
            (302, ["error": "unknown"], .homeUnavailable)
        ] {
            XCTAssertThrowsError(try response(status, fields).requireAuthorized()) { error in
                XCTAssertTrue(error as? HomeConnectionFailure == expected, "Closed response category mismatch")
            }
        }
        XCTAssertNoThrow(try response(200, [:]).requireAuthorized())
        let duplicate = HomeHTTPResponse(status: 202, body: Data("{\"expires_at\":1,\"expires_at\":2}".utf8))
        XCTAssertThrowsError(try duplicate.fields())
    }

    func testCeremonyRedactionCanariesIncludeMalformedAndStructuredValues() {
        let canary = "SYNTHETIC-SECRET-CANARY"
        let inputs = ["hrinvite1:malformed " + canary, "hrinvite2:malformed " + canary, "hrpin_1_" + canary, "hrpair_" + canary,
            "{\"join_key\":\"" + canary + "\"}", "pair_secret=" + canary,
            "{\"completion_handle\":\"" + canary, "credential=" + canary]
        for input in inputs {
            XCTAssertFalse(DiagnosticsRedactor.redact(input).contains(canary), "Secret leaked from text redaction")
        }
        let fields = Dictionary(uniqueKeysWithValues: ["join_key", "pair_secret", "completion_handle", "credential", "raw_invite", "Authorization"].map { ($0, canary) })
        XCTAssertTrue(DiagnosticsRedactor.redactStrings(in: fields).values.allSatisfy { !$0.contains(canary) }, "Secret leaked from structured redaction")
        for failure in HomeConnectionFailure.allCases {
            XCTAssertFalse(failure.localizedDescription.contains(canary), "Secret leaked from closed error copy")
        }
    }
    func testAddressRecoveryBearerDiagnosticCanary() {
        let canary = "SYNTHETIC-ADDRESS-RECOVERY-CANARY"
        let text = DiagnosticsRedactor.redact("{\"recovery\":{\"bearer\":\"" + canary + "\"}}")
        XCTAssertFalse(text.contains(canary))
        let fields = DiagnosticsRedactor.redactStrings(in: ["bearer": canary])
        XCTAssertFalse(String(describing: fields).contains(canary))
    }

}
