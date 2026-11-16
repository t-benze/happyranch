import XCTest
import Foundation
@testable import HappyRanchSupervisor

final class HomeInviteProtocolTests: XCTestCase {
    private func response(_ status: Int, _ fields: [String: Any]) throws -> HomeHTTPResponse {
        HomeHTTPResponse(status: status, body: try JSONSerialization.data(withJSONObject: fields))
    }

    func testPendingHandleIsBoundedAndCannotExtendInviteDeadline() throws {
        let deadline = Date(timeIntervalSince1970: 1_800_000_300)
        var fields: [String: Any] = ["completion_handle": String(repeating: "a", count: 64),
            "comparison_phrase": "pair-1234-abcd-5678-efab", "expires_at": 1_800_000_300]
        let pending = try ManagedHomeInviteSession.pending(response(202, fields), deadline: deadline)
        XCTAssertEqual(pending.expiresAt, deadline)
        fields["expires_at"] = 1_800_000_301
        XCTAssertThrowsError(try ManagedHomeInviteSession.pending(response(202, fields), deadline: deadline))
        fields["expires_at"] = 1_800_000_300
        fields["unexpected"] = "synthetic"
        XCTAssertThrowsError(try ManagedHomeInviteSession.pending(response(202, fields), deadline: deadline))
        let rendered = String(describing: pending) + String(reflecting: pending) + String(describing: pending.customMirror.children)
        XCTAssertFalse(rendered.contains(String(repeating: "a", count: 64)), "Pending handle must stay redacted")
    }

    func testDeliveredTrustTupleCannotBeSubstituted() throws {
        let trust = try RemoteInviteTests.trust()
        var fields: [String: Any] = ["credential": "hrpin_1_SYNTHETIC-BEARER-CANARY", "stable_node_id": "stable-node",
            "home_id": trust.homeID, "control_plane_id": trust.controlPlaneID, "connector_id": trust.connectorID]
        let profile = UUID()
        let delivered = try ManagedHomeInviteSession.delivered(response(200, fields), trust: trust, profileID: profile)
        XCTAssertEqual(delivered.networkProfileID, profile)
        XCTAssertEqual(delivered.binding.stableNodeID, "stable-node")
        for key in ["home_id", "control_plane_id", "connector_id"] {
            let old = fields[key]; fields[key] = "substituted"
            XCTAssertThrowsError(try ManagedHomeInviteSession.delivered(response(200, fields), trust: trust, profileID: profile), key)
            fields[key] = old
        }
        fields["credential"] = "synthetic\r\nHeader: injection"
        XCTAssertThrowsError(try ManagedHomeInviteSession.delivered(response(200, fields), trust: trust, profileID: profile))
        let roundTrip = try JSONDecoder().decode(SavedDeviceCredential.self, from: JSONEncoder().encode(delivered))
        XCTAssertEqual(roundTrip.networkProfileID, profile)
    }

    func testOnlyClosedServerCodesDistinguishRevocationAndDaemonOutage() throws {
        for (status, fields, expected) in [
            (403, ["error": "revoked"], HomeConnectionFailure.revoked),
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
        let inputs = ["hrinvite1:malformed " + canary, "hrpin_1_" + canary, "hrpair_" + canary,
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
}
