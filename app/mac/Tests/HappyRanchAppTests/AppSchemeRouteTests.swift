import XCTest
@testable import HappyRanchApp

final class AppSchemeRouteTests: XCTestCase {
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
