import XCTest
@testable import HappyRanchApp

final class AppSchemeRouteTests: XCTestCase {
    func testAdmissionDiagnosticRetainsOnlyClosedResultTypesAndPageFacts() {
        let token = String(repeating: "22", count: 16)
        let cases: [(String, Any?)] = [
            ("nil", nil), ("null", NSNull()), ("string", token),
            ("dictionary", ["context": token]), ("dictionary", NSDictionary()),
            ("bool", true), ("bool", false), ("bool", NSNumber(value: true)),
            ("number", NSNumber(value: 0)), ("number", NSNumber(value: 1)),
            ("number", 42), ("other", [token]), ("other", ())
        ]
        for (expected, raw) in cases {
            var diagnostic = AppSchemeAdmissionDiagnostic()
            diagnostic.contextResult = AppSchemeAdmissionDiagnostic.resultType(raw)
            diagnostic.bindResult = AppSchemeAdmissionDiagnostic.resultType(raw)
            diagnostic.diagnosticsResult = AppSchemeAdmissionDiagnostic.resultType(raw)
            diagnostic.recordPageFacts(raw)
            XCTAssertEqual(diagnostic.contextResult, expected)
            XCTAssertTrue(diagnostic.description.contains("nativeResults={context=\(expected), bind=\(expected), diagnostics=\(expected)}"))
            XCTAssertFalse(diagnostic.description.contains(token))
        }
        var diagnostic = AppSchemeAdmissionDiagnostic()
        diagnostic.recordPageFacts(["bootstrap": token, "readyState": "complete", "unknown": token])
        XCTAssertEqual(diagnostic.pageFacts.components(separatedBy: ", ").count, 15)
        XCTAssertTrue(diagnostic.pageFacts.contains("bootstrap=invalid"))
        XCTAssertTrue(diagnostic.pageFacts.contains("readyState=complete"))
        XCTAssertFalse(diagnostic.description.contains(token))
    }

    func testOwnerReceiptStoresProtectionAndDocumentLossRetainsUncertainty() throws {
        var owners = AppSchemeOwnerSummary()
        XCTAssertFalse(owners.allowsNavigation, "Missing receipt is not clean clearance")
        for (counts, level) in [("0,0,0,0", "none"), ("1,0,0,0", "prompt"),
                                ("0,1,0,0", "prompt"), ("0,0,1,0", "hold"), ("0,0,0,1", "hold")] {
            let values = counts.split(separator: ",")
            try owners.accept("{\"schema\":2,\"blocking\":\"\(level)\",\"counts\":{\"dirty\":\(values[0]),\"failed\":\(values[1]),\"pending\":\(values[2]),\"unknown\":\(values[3])}}")
            XCTAssertEqual(owners.blocking, level)
            XCTAssertEqual(owners.allowsNavigation, level == "none")
        }
        owners.retire(nonGETPending: false)
        let clean = #"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#
        try owners.accept(clean)
        XCTAssertFalse(owners.allowsNavigation, "New clean document cannot clear a lost unresolved save")
        var activeMutation = AppSchemeOwnerSummary()
        try activeMutation.accept(clean)
        activeMutation.retire(nonGETPending: true)
        try activeMutation.accept(clean)
        XCTAssertFalse(activeMutation.allowsNavigation, "Native sends protect even when web reports clean")
    }

    func testInvalidOwnerPushCannotRetainCleanClearance() throws {
        let clean = #"{"schema":2,"blocking":"none","counts":{"dirty":0,"failed":0,"pending":0,"unknown":0}}"#
        for invalid in [clean.replacingOccurrences(of: "\"schema\":2", with: "\"schema\":2,\"schema\":2"),
                        clean.replacingOccurrences(of: "\"pending\":0", with: "\"pending\":1"),
                        clean.replacingOccurrences(of: "\"dirty\":0", with: "\"dirty\":true"),
                        clean.replacingOccurrences(of: "\"unknown\":0", with: "\"unknown\":256"),
                        clean.replacingOccurrences(of: "\"schema\":2", with: "\"gate\":{\"requestId\":\"unsolicited\",\"decision\":\"proceed\"},\"schema\":2"),
                        String(repeating: " ", count: 1025) + clean] {
            var owners = AppSchemeOwnerSummary()
            try owners.accept(clean)
            XCTAssertTrue(owners.allowsNavigation)
            XCTAssertThrowsError(try owners.accept(invalid))
            XCTAssertFalse(owners.allowsNavigation)
        }
    }

    func testAppOriginAndDeepRoutePreserveEscapedAPITarget() throws {
        let route = try AppSchemeRoute(URL(string: "happyranch-app://app/api/v1/orgs/test/artifacts/a%20b?raw=1")!)
        XCTAssertEqual(route.path, "/api/v1/orgs/test/artifacts/a b")
        XCTAssertEqual(route.target, "/api/v1/orgs/test/artifacts/a%20b?raw=1")
        XCTAssertEqual(try AppSchemeRoute(URL(string: "happyranch-app://app/settings")!).path, "/settings")
    }
    func testForeignOriginAndEncodedTraversalRefuse() {
        for raw in ["https://app/settings", "happyranch-app://other/settings", "happyranch-app://app:99/",
                    "happyranch-app://user@app/", "happyranch-app://app/%2e%2e/private", "happyranch-app://app/%252e%252e/private",
                    "happyranch-app://app/a%5cb", "happyranch-app://app/a%00b", "happyranch-app://app/a//b"] {
            XCTAssertThrowsError(try AppSchemeRoute(URL(string: raw)!), raw)
        }
    }
}
