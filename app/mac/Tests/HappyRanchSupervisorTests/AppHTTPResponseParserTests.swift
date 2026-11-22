import XCTest
@testable import HappyRanchSupervisor

final class AppHTTPResponseParserTests: XCTestCase {
    func testChunkedSSELeavesBeforeCompletionAcrossFragmentedFraming() throws {
        var parser = AppHTTPResponseParser()
        let header = Data("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n".utf8)
        var delivered = Data()
        for byte in header { _ = try parser.receive(Data([byte])) }
        for byte in Data("9\r\ndata: 1\n\n\r\n".utf8) {
            for event in try parser.receive(Data([byte])) { if case .bytes(let data) = event { delivered.append(data) } }
        }
        XCTAssertEqual(delivered, Data("data: 1\n\n".utf8))
        XCTAssertFalse(parser.complete, "SSE must leave before final chunk/EOF")
        _ = try parser.receive(Data("0\r\n\r\n".utf8))
        XCTAssertTrue(parser.complete)
    }
    func testLengthTruncationCannotBecomeSuccessfulEOF() throws {
        var parser = AppHTTPResponseParser()
        _ = try parser.receive(Data("HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nab".utf8))
        XCTAssertThrowsError(try parser.receive(Data(), eof: true))
        var exact = AppHTTPResponseParser()
        _ = try exact.receive(Data("HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nab".utf8))
        XCTAssertTrue(exact.complete)
    }
    func testRedirectAndAmbiguousFramingRefuse() {
        for response in [
            "HTTP/1.1 302 Found\r\nLocation: https://other.invalid/\r\nContent-Length: 0\r\n\r\n",
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 4\r\n\r\n",
            "HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n"
        ] {
            var parser = AppHTTPResponseParser()
            XCTAssertThrowsError(try parser.receive(Data(response.utf8)))
        }
    }
    func testHeaderBoundaryStripsIdentityAndConnectionNominations() {
        let headers = AppAPIHeaders.filtered(["Authorization": "synthetic", "Cookie": "synthetic",
            "X-HappyRanch-Device-Credential": "synthetic", "Connection": "Accept", "Accept": "text/event-stream",
            "Content-Type": "application/json", "Range": "bytes=0-9"], allowed: AppAPIHeaders.request)
        XCTAssertEqual(headers, ["content-type": "application/json", "range": "bytes=0-9"])
    }
}
