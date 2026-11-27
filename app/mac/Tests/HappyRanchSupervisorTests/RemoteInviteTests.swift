import XCTest
import Foundation
import CryptoKit
@testable import HappyRanchSupervisor

final class RemoteInviteTests: XCTestCase {
    // Public synthetic EC CA with CA:TRUE and IP SAN 192.168.1.40. Generated
    // solely for fixtures; its private key was discarded and is not a resource.
    private static let certificate = "MIIBtTCCAVqgAwIBAgIUJFYL6Zgls9xJBeFOg2sV9o2332EwCgYIKoZIzj0EAwIwJzElMCMGA1UEAwwcSGFwcHlSYW5jaC1TeW50aGV0aWMtVGVzdC1DQTAeFw0yNjExMTYxNDQxMDdaFw0zNjExMTMxNDQxMDdaMCcxJTAjBgNVBAMMHEhhcHB5UmFuY2gtU3ludGhldGljLVRlc3QtQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQ2NwX3SKY44WS3nPHycbi2Zrl8wernrIOA0phhfdgW4s7lwEx/Spkox8B0KgSG+PXv0EnabvCsy9tw76VaKmiRo2QwYjAdBgNVHQ4EFgQUnfsAuLVx5ONiVlxf+ouILt9tLzcwHwYDVR0jBBgwFoAUnfsAuLVx5ONiVlxf+ouILt9tLzcwDwYDVR0TAQH/BAUwAwEB/zAPBgNVHREECDAGhwTAqAEoMAoGCCqGSM49BAMCA0kAMEYCIQDpwzqLwmBOUP/7Q92jFQy2U2qJPLVZi4/KD0TwlrQFAgIhAPqwm3bQLNxd4VVa1Ok/45qyDj+E4DkSMcyledYZWnmI"
    static let now = Date(timeIntervalSince1970: 1_800_000_010)
    private let joinCanary = String(repeating: "cafe12", count: 8)

    private func wire(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    private func tls(_ ca: Data) -> [String: Any] {
        ["kind": "local_ca", "dial_ip": "192.168.1.40", "ca_der": wire(ca), "sha256": SHA256.hash(data: ca).map { String(format: "%02x", $0) }.joined()]
    }
    private func fixture() -> [String: Any] {
        ["v": 2, "mode": "home", "control_url": "https://control.happyranch.invalid:28443/",
         "control_plane_id": "test-control", "control_tls": tls(Data(base64Encoded: Self.certificate)!),
         "home_endpoint": "100.64.0.10:443", "home_id": "test-home", "connector_id": "test-connector",
         "invite_id": wire(Data("INVITE-CANARY-12".utf8)), "join_key": joinCanary,
         "pair_secret": wire(Data(String(repeating: "PAIR-CANARY-1234", count: 2).utf8)),
         "issued_at": 1_800_000_000, "expires_at": 1_800_000_300]
    }
    private func encode(_ fields: [String: Any]) throws -> String {
        "hrinvite2:" + wire(try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys, .withoutEscapingSlashes]))
    }
    private func refusal(_ input: String, _ expected: RemoteInviteError, name: String) {
        do { _ = try RemoteInvite.parse(input, now: Self.now); XCTFail("Accepted refusal case: \(name)") }
        catch let error as RemoteInviteError { XCTAssertEqual(error, expected, name) }
        catch { XCTFail("Non-closed failure: \(name)") }
    }

    func testSANLessCAAndCAValidity() throws {
        // Public synthetic CA only; generated private key was never written.
        let ca = Data(base64Encoded: "MIIBcDCCARegAwIBAgIBAzAKBggqhkjOPQQDAjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwHhcNMjMxMTE0MjIxMzIwWhcNMzMwNTE4MDMzMzIwWjAgMR4wHAYDVQQDExVTeW50aGV0aWMgU0FOLWxlc3MgQ0EwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQn+y+9zcuDcMqqUHzKu60sYl6vJtfgX8DR1VfNh6XtkNUwTmInfcpYhOhYzq94Nc4xe87GBk1JAN43UK9cLaDAo0IwQDAOBgNVHQ8BAf8EBAMCAgQwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUyJSU09xAckvJECBK6NS3R58e9wswCgYIKoZIzj0EAwIDRwAwRAIgSGTa05eLWaPEQa9fYBwp979EMWXfu6F1aLr+IlLxlqMCIAv6++jkLKpTs+XT4vP9ETW/CdjHrUHv1hqkirv2ErYF")!
        var fields = fixture(); fields["control_tls"] = tls(ca)
        XCTAssertNoThrow(try RemoteInvite.parse(encode(fields), now: Self.now))
        // Change the typed CA boolean while retaining parseable DER. Signature
        // verification is live-chain responsibility; offline CA:TRUE is required.
        var nonCA = ca
        let marker = Data([0x30, 0x03, 0x01, 0x01, 0xff])
        let range = try XCTUnwrap(nonCA.range(of: marker))
        nonCA[range.upperBound - 1] = 0
        fields["control_tls"] = tls(nonCA)
        refusal(try encode(fields), .certificate, name: "CA must have CA:TRUE")
        // A time within the invite but beyond the CA validity must refuse.
        fields = fixture(); fields["control_tls"] = tls(ca)
        fields["issued_at"] = 2_000_000_000; fields["expires_at"] = 2_000_000_300
        do {
            _ = try RemoteInvite.parse(encode(fields), now: Date(timeIntervalSince1970: 2_000_000_010))
            XCTFail("Expired CA accepted")
        } catch { XCTAssertTrue(error as? RemoteInviteError == .certificate) }
    }

    func testValidFixtureRoundTrip() throws {
        let encoded = try encode(fixture())
        let invite = try RemoteInvite.parse(encoded, now: Self.now)
        XCTAssertEqual(invite.trust.homeID, "test-home")
        XCTAssertEqual(invite.trust.version, 2)
        XCTAssertEqual(invite.trust.controlURL, "https://control.happyranch.invalid:28443")
        XCTAssertEqual(invite.trust.dialIP, "192.168.1.40")
        XCTAssertEqual(invite.trust.homeEndpoint, "100.64.0.10:443")
        XCTAssertEqual(invite.expiresAt.timeIntervalSince(invite.issuedAt), 300)
        let correctSecrets = invite.withSecrets { id, key, pair in
            id == Data("INVITE-CANARY-12".utf8) && key == Data(joinCanary.utf8) &&
                pair == Data(String(repeating: "PAIR-CANARY-1234", count: 2).utf8)
        }
        XCTAssertTrue(HomePairingInvitation(invite).requestBody() == Data("UEFJUi1DQU5BUlktMTIzNFBBSVItQ0FOQVJZLTEyMzQ".utf8), "Pair request is only the exact base64url secret text")
        XCTAssertTrue(correctSecrets, "Fixture transport bytes must survive validation")
        let decoded = try JSONSerialization.jsonObject(with: JSONSerialization.data(withJSONObject: fixture())) as! [String: Any]
        XCTAssertEqual(try RemoteInvite.parse(encode(decoded), now: Self.now).trust, invite.trust)
    }

    func testEnvelopeAndExactSchemaRefusals() throws {
        let valid = try encode(fixture())
        refusal(String(repeating: "a", count: 5501), .oversized, name: "encoded limit")
        refusal("hrinvite2:" + wire(Data(repeating: 32, count: 4097)), .oversized, name: "decoded limit")
        refusal("wrong:" + valid, .prefix, name: "prefix")
        refusal(valid + "=", .encoding, name: "padded envelope")
        for bad in ["", "a", "AA=", "AA+_", "AA/_", "AA\n", "AB"] {
            refusal("hrinvite2:" + bad, .encoding, name: "base64url")
        }
        var extra = fixture(); extra["unexpected"] = "value"
        refusal(try encode(extra), .schema, name: "unknown field")
        for key in fixture().keys {
            var missing = fixture(); missing.removeValue(forKey: key)
            refusal(try encode(missing), .schema, name: "missing \(key)")
        }
        var fields = fixture(); fields["v"] = 3
        refusal(try encode(fields), .unsupported, name: "version")
        for mode in ["advanced", "own-server", "future"] {
            fields = fixture(); fields["mode"] = mode
            refusal(try encode(fields), .unsupported, name: "mode")
        }
        for value in [true, "1", NSNull(), [1], 1.5] as [Any] {
            fields = fixture(); fields["v"] = value
            refusal(try encode(fields), .schema, name: "version type")
        }
        let json = String(data: try JSONSerialization.data(withJSONObject: fixture(), options: .sortedKeys), encoding: .utf8)!
        for suffix in [",\"v\":1}", ",\"\\u0076\":1}"] {
            refusal("hrinvite2:" + wire(Data((String(json.dropLast()) + suffix).utf8)), .schema, name: "duplicate key")
        }
        let duplicateTLS = json.replacingOccurrences(of: "\"kind\":\"local_ca\"", with: "\"kind\":\"local_ca\",\"kind\":\"local_ca\"")
        refusal("hrinvite2:" + wire(Data(duplicateTLS.utf8)), .schema, name: "duplicate nested key")
        refusal("hrinvite2:" + wire(Data((json + "{}").utf8)), .schema, name: "trailing JSON")
        refusal("hrinvite2:" + wire(Data([0xff, 0xfe])), .schema, name: "invalid UTF8")
    }

    func testURLAndTrustRefusals() throws {
        for url in ["https://control.happyranch.invalid:8443", "http://control.happyranch.invalid:28443", "https://user@control.happyranch.invalid:28443",
            "https://user:pass@control.happyranch.invalid:28443", "https://control.happyranch.invalid:28443?query",
            "https://control.happyranch.invalid:28443#fragment", "https://control.happyranch.invalid:28443/path",
            "https://control.happyranch.invalid:443", "https://127.0.0.1:28443", "https://example.test:28443",
            "https://192.168.1.40:28443", "https://192.168.001.40:28443", "https://control.happyranch.invalid:28443/%2f",
            "https://CONTROL.happyranch.invalid:28443", "https://control.happyranch.invalid:28443//"] {
            var fields = fixture(); fields["control_url"] = url
            refusal(try encode(fields), .url, name: "control URL")
        }
        for endpoint in [":443", "100.64.0.10:80", "100.64.0.10:443/path", "user@100.64.0.10:443", "100.64.0.10:443?q", "100.64.0.10:443#f"] {
            var fields = fixture(); fields["home_endpoint"] = endpoint
            refusal(try encode(fields), .url, name: "runtime endpoint")
        }
        var fields = fixture(); var changedTLS = tls(Data(base64Encoded: Self.certificate)!)
        changedTLS["dial_ip"] = "192.168.1.41"; fields["control_tls"] = changedTLS
        XCTAssertNoThrow(try RemoteInvite.parse(encode(fields), now: Self.now)) // SAN belongs to live leaf
        var trust = fixture()["control_tls"] as! [String: Any]
        trust["sha256"] = String(repeating: "0", count: 64); fields = fixture(); fields["control_tls"] = trust
        refusal(try encode(fields), .fingerprint, name: "fingerprint mismatch")
        trust = fixture()["control_tls"] as! [String: Any]
        trust["sha256"] = (trust["sha256"] as! String).uppercased()
        fields = fixture(); fields["control_tls"] = trust
        refusal(try encode(fields), .fingerprint, name: "uppercase fingerprint")
        for malformed in [Data([0x30, 0]), Data(base64Encoded: Self.certificate)! + Data([0])] {
            fields = fixture(); fields["control_tls"] = tls(malformed)
            refusal(try encode(fields), .certificate, name: "malformed or trailing DER")
        }
        trust = fixture()["control_tls"] as! [String: Any]; trust["extra"] = "value"
        fields = fixture(); fields["control_tls"] = trust
        refusal(try encode(fields), .schema, name: "unknown trust field")
        for kind in ["system", "future"] {
            fields = fixture(); fields["control_tls"] = ["kind": kind]
            refusal(try encode(fields), .unsupported, name: "unsupported trust")
        }
        trust = fixture()["control_tls"] as! [String: Any]; trust["kind"] = "LOCAL_CA"
        fields = fixture(); fields["control_tls"] = trust
        refusal(try encode(fields), .unsupported, name: "wrong union discriminator with valid members")
        for key in ["kind", "ca_der", "sha256", "dial_ip"] {
            trust = fixture()["control_tls"] as! [String: Any]; trust.removeValue(forKey: key)
            fields = fixture(); fields["control_tls"] = trust
            refusal(try encode(fields), .schema, name: "missing trust field")
        }
        trust = fixture()["control_tls"] as! [String: Any]; trust["ca_der"] = "not+base64url"
        fields = fixture(); fields["control_tls"] = trust
        refusal(try encode(fields), .certificate, name: "CA encoding")
    }

    func testTimeAndEntropyRefusals() throws {
        for (issued, expires) in [(1_800_000_000, 1_800_000_010), (1_800_000_011, 1_800_000_300),
            (1_800_000_000, 1_800_000_301), (1_800_000_000, 1_800_000_000), (-1, 100)] {
            var fields = fixture(); fields["issued_at"] = issued; fields["expires_at"] = expires
            // Negative JSON tokens are outside the pinned nonnegative Int64 grammar.
            refusal(try encode(fields), issued < 0 ? .schema : .time, name: "time window")
        }
        for key in ["issued_at", "expires_at"] {
            var fields = fixture(); fields[key] = key == "issued_at" ? "1800000000" : "1800000300"
            refusal(try encode(fields), .time, name: "string \(key)")
            fields = fixture(); fields[key] = key == "issued_at" ? 1_800_000_000.5 : 1_800_000_299.5
            refusal(try encode(fields), .schema, name: "float \(key)")
        }
        for (key, count) in [("invite_id", 15), ("invite_id", 17), ("pair_secret", 31), ("pair_secret", 33)] {
            var fields = fixture(); fields[key] = wire(Data(repeating: 97, count: count))
            refusal(try encode(fields), .entropy, name: "entropy byte length")
        }
        var fields = fixture(); fields["join_key"] = "SYNTHETIC-SECRET-CANARY"
        refusal(try encode(fields), .entropy, name: "join key format")
    }

    func testSecretRepresentationsAreRedacted() throws {
        let invite = try RemoteInvite.parse(encode(fixture()), now: Self.now)
        let rendered = String(describing: invite) + String(reflecting: invite) + String(describing: invite.customMirror.children)
        for secret in [joinCanary, "INVITE-CANARY-12", "PAIR-CANARY-1234", try encode(fixture())] {
            XCTAssertFalse(rendered.contains(secret), "Invite display/debug/mirror must be redacted")
            for error in [RemoteInviteError.entropy, .certificate, .schema, .time] {
                XCTAssertFalse((String(describing: error) + error.localizedDescription).contains(secret), "Failures must remain closed")
            }
        }
    }

    func testV1AndModeTrustMismatchRequireClosedRefusal() throws {
        let valid = try encode(fixture())
        refusal("hrinvite1:" + String(valid.dropFirst(10)), .reapprovalRequired, name: "old prefix")
        var old = fixture(); old["v"] = 1
        refusal(try encode(old), .unsupported, name: "old payload in new envelope")
        var mismatch = fixture(); mismatch["mode"] = "own-server"
        refusal(try encode(mismatch), .unsupported, name: "local CA in own-server mode")
        mismatch = fixture(); mismatch["control_tls"] = ["kind": "system"]
        refusal(try encode(mismatch), .unsupported, name: "system trust in home mode")
        mismatch["mode"] = "own-server"
        refusal(try encode(mismatch), .unsupported, name: "R3 disabled")
        mismatch["control_tls"] = ["kind": "system", "dial_ip": "192.168.1.40"]
        refusal(try encode(mismatch), .schema, name: "mixed union")
    }

    func testDialIPIsCanonicalRFC1918AndCannotSelectControlURL() throws {
        for ip in ["127.0.0.1", "100.64.0.1", "169.254.1.1", "172.15.0.1", "172.32.0.1", "8.8.8.8",
                   "::1", "::ffff:192.168.1.40", "192.168.001.40", "192.168.1.40.", "192.168.1.40..", "192.168.1.40:28443", "192.168.1.40\n"] {
            var fields = fixture(); var trust = tls(Data(base64Encoded: Self.certificate)!)
            trust["dial_ip"] = ip; fields["control_tls"] = trust
            refusal(try encode(fields), .url, name: "non-RFC1918/canonical dial IP")
        }
        for ip in ["10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.41"] {
            var fields = fixture(); var trust = tls(Data(base64Encoded: Self.certificate)!)
            trust["dial_ip"] = ip; fields["control_tls"] = trust
            let invite = try RemoteInvite.parse(encode(fields), now: Self.now)
            XCTAssertEqual(invite.trust.dialIP, ip)
            XCTAssertEqual(invite.trust.controlURL, "https://control.happyranch.invalid:28443")
            XCTAssertEqual(invite.trust.homeEndpoint, "100.64.0.10:443")
        }
    }

    static func trust() throws -> SavedHomeTrust {
        let ca = Data(base64Encoded: certificate)!
        return SavedHomeTrust(homeID: "test-home", controlPlaneID: "test-control", connectorID: "test-connector",
            controlURL: "https://control.happyranch.invalid:28443/", dialIP: "192.168.1.40", homeEndpoint: "100.64.0.10:443", caDER: ca,
            caSHA256: SHA256.hash(data: ca).map { String(format: "%02x", $0) }.joined())
    }
}
