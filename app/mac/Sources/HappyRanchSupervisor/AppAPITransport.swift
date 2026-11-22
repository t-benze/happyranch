import Foundation

/// Native-only selected-daemon transport. Neither request credentials nor URLs
/// are returned to JavaScript. Response events preserve upstream byte order.
public enum AppAPIEvent: Sendable {
    case response(Int, [String: String])
    case bytes(Data)
}

public enum AppAPIError: Error, Sendable { case unavailable, invalidResponse, cancelled, slowReader }

public protocol AppAPITransport: Sendable {
    func appRequest(method: String, target: String, headers: [String: String], body: Data) -> AppAPITransfer
}

public final class AppAPITransfer: @unchecked Sendable {
    public let events: AsyncThrowingStream<AppAPIEvent, Error>
    private let continuation: AsyncThrowingStream<AppAPIEvent, Error>.Continuation
    private let lock = NSLock()
    private var ended = false
    private var cancellation: (@Sendable () -> Void)?

    public init() {
        let pair = AsyncThrowingStream<AppAPIEvent, Error>.makeStream(bufferingPolicy: .bufferingOldest(32))
        events = pair.stream; continuation = pair.continuation
        continuation.onTermination = { [weak self] _ in self?.cancel() }
    }
    public func onCancel(_ action: @escaping @Sendable () -> Void) {
        let run = lock.withLock { if ended { return true }; cancellation = action; return false }
        if run { action() }
    }
    public var isCancelled: Bool { lock.withLock { ended } }
    public func emit(_ event: AppAPIEvent) {
        if case .dropped = continuation.yield(event) { finish(AppAPIError.slowReader) }
    }
    public func finish(_ error: Error? = nil) {
        let action: (@Sendable () -> Void)? = lock.withLock {
            guard !ended else { return nil }; ended = true
            defer { cancellation = nil }; return cancellation
        }
        if let error { continuation.finish(throwing: error) } else { continuation.finish() }
        action?()
    }
    public func cancel() { finish(AppAPIError.cancelled) }
}

/// Browser identity headers are never forwarded. Framing is generated natively.
public enum AppAPIHeaders {
    public static let request: Set<String> = ["accept", "accept-language", "content-type", "if-match",
        "if-none-match", "if-modified-since", "range", "last-event-id"]
    public static let response: Set<String> = ["content-type", "content-disposition", "content-range",
        "accept-ranges", "etag", "last-modified", "retry-after"]
    public static func filtered(_ headers: [String: String], allowed: Set<String>) -> [String: String] {
        let connection = Set((headers.first { $0.key.lowercased() == "connection" }?.value ?? "")
            .lowercased().split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) })
        return headers.reduce(into: [:]) { result, pair in
            let key = pair.key.lowercased()
            if allowed.contains(key), !connection.contains(key), !pair.value.contains("\r"), !pair.value.contains("\n") {
                result[key] = pair.value
            }
        }
    }
}

/// An already verified local daemon port; never follows redirects or proxies.
public final class LocalAppAPITransport: AppAPITransport, @unchecked Sendable {
    private let port: UInt16
    private let credential: any DaemonCredentialProvider
    public init(port: UInt16, credential: any DaemonCredentialProvider) {
        self.port = port; self.credential = credential
    }
    public func appRequest(method: String, target: String, headers: [String: String], body: Data) -> AppAPITransfer {
        let transfer = AppAPITransfer()
        guard target.hasPrefix("/api/v1/"), !target.contains("\r"), !target.contains("\n"),
              let url = URL(string: "http://127.0.0.1:\(port)" + target),
              url.host == "127.0.0.1", url.port == Int(port), body.count <= 67108864 else {
            transfer.finish(AppAPIError.unavailable); return transfer
        }
        do {
            var request = URLRequest(url: url)
            request.httpMethod = method; request.httpBody = body.isEmpty ? nil : body
            request.allHTTPHeaderFields = AppAPIHeaders.filtered(headers, allowed: AppAPIHeaders.request)
            let token = try credential.credential()
            guard !token.isEmpty, token.utf8.count <= 4096,
                  token.utf8.allSatisfy({ (33...126).contains($0) }) else { throw AppAPIError.unavailable }
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
            let configuration = URLSessionConfiguration.ephemeral
            configuration.connectionProxyDictionary = [:]; configuration.httpCookieStorage = nil
            configuration.urlCredentialStorage = nil; configuration.urlCache = nil
            configuration.timeoutIntervalForRequest = 3600; configuration.timeoutIntervalForResource = 3600
            let delegate = AppAPIDelegate(transfer)
            let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
            let task = session.dataTask(with: request)
            transfer.onCancel { task.cancel(); session.invalidateAndCancel() }
            task.resume()
        } catch { transfer.finish(AppAPIError.unavailable) }
        return transfer
    }
}

private final class AppAPIDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let transfer: AppAPITransfer
    init(_ transfer: AppAPITransfer) { self.transfer = transfer }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil); transfer.finish(AppAPIError.invalidResponse)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse else { completionHandler(.cancel); return }
        let fields = http.allHeaderFields.reduce(into: [String: String]()) { out, pair in
            if let key = pair.key as? String, let value = pair.value as? String { out[key] = value }
        }
        transfer.emit(.response(http.statusCode, AppAPIHeaders.filtered(fields, allowed: AppAPIHeaders.response)))
        completionHandler(.allow)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        transfer.emit(.bytes(data))
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        transfer.finish(error == nil ? nil : AppAPIError.unavailable); session.finishTasksAndInvalidate()
    }
}

/// Incremental HTTP/1 response framing for the existing pinned native remote
/// dial. SSE bytes leave this parser immediately, including partial frames.
public struct AppHTTPResponseParser {
    private var pending = Data()
    private var headersRead = false
    private var chunked = false
    private var remaining: Int?
    private var chunkRemaining: Int?
    private var chunkCRLF = false
    private var trailers = false
    public private(set) var complete = false
    private let head: Bool
    public init(head: Bool = false) { self.head = head }

    public mutating func receive(_ data: Data, eof: Bool = false) throws -> [AppAPIEvent] {
        guard !complete else { if data.isEmpty { return [] }; throw AppAPIError.invalidResponse }
        pending.append(data)
        var events: [AppAPIEvent] = []
        if !headersRead {
            guard let end = pending.range(of: Data("\r\n\r\n".utf8)) else {
                guard pending.count <= 65536, !eof else { throw AppAPIError.invalidResponse }; return []
            }
            guard end.lowerBound <= 65536, let raw = String(data: pending[..<end.lowerBound], encoding: .utf8) else {
                throw AppAPIError.invalidResponse
            }
            let lines = raw.components(separatedBy: "\r\n"), statusLine = lines[0].split(separator: " ")
            guard statusLine.count >= 2, ["HTTP/1.1", "HTTP/1.0"].contains(String(statusLine[0])),
                  let status = Int(statusLine[1]), (200...599).contains(status),
                  !(300..<400).contains(status) || status == 304 else { throw AppAPIError.invalidResponse }
            var fields: [String: String] = [:]
            for line in lines.dropFirst() {
                guard let colon = line.firstIndex(of: ":"), colon != line.startIndex else { throw AppAPIError.invalidResponse }
                let name = line[..<colon].lowercased()
                let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
                if let previous = fields[name] {
                    guard !["content-length", "transfer-encoding", "content-type"].contains(name) else { throw AppAPIError.invalidResponse }
                    fields[name] = previous + ", " + value
                } else { fields[name] = value }
            }
            if let encoding = fields["transfer-encoding"] {
                guard encoding.lowercased() == "chunked", fields["content-length"] == nil else { throw AppAPIError.invalidResponse }
                chunked = true
            }
            if let length = fields["content-length"] {
                guard !length.isEmpty, length.utf8.allSatisfy({ (48...57).contains($0) }),
                      let count = Int(length) else { throw AppAPIError.invalidResponse }
                remaining = count
            }
            pending.removeSubrange(..<end.upperBound); headersRead = true
            events.append(.response(status, AppAPIHeaders.filtered(fields, allowed: AppAPIHeaders.response)))
            if head || status == 204 || status == 304 { complete = true; pending.removeAll(); return events }
        }
        while !pending.isEmpty && !complete {
            if !chunked {
                let count = min(remaining ?? pending.count, pending.count)
                if count > 0 { events.append(.bytes(Data(pending.prefix(count)))); pending.removeFirst(count) }
                if let left = remaining { remaining = left - count; if remaining == 0 { complete = true } }
                break
            }
            if trailers {
                if pending.starts(with: Data("\r\n".utf8)) { pending.removeFirst(2); complete = true }
                else if let end = pending.range(of: Data("\r\n\r\n".utf8)) { pending.removeSubrange(..<end.upperBound); complete = true }
                else if pending.count > 65536 { throw AppAPIError.invalidResponse }
                break
            }
            if chunkCRLF {
                guard pending.count >= 2 else { break }
                guard pending.starts(with: Data("\r\n".utf8)) else { throw AppAPIError.invalidResponse }
                pending.removeFirst(2); chunkCRLF = false
            }
            if chunkRemaining == nil {
                guard let end = pending.range(of: Data("\r\n".utf8)) else {
                    guard pending.count <= 128 else { throw AppAPIError.invalidResponse }; break
                }
                guard end.lowerBound - pending.startIndex <= 128,
                      let line = String(data: pending[..<end.lowerBound], encoding: .ascii),
                      let number = line.split(separator: ";", maxSplits: 1).first,
                      !number.isEmpty, number.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }),
                      let size = Int(number, radix: 16) else { throw AppAPIError.invalidResponse }
                pending.removeSubrange(..<end.upperBound)
                if size == 0 { trailers = true; continue }; chunkRemaining = size
            }
            if let left = chunkRemaining {
                let count = min(left, pending.count)
                if count > 0 { events.append(.bytes(Data(pending.prefix(count)))); pending.removeFirst(count) }
                chunkRemaining = left - count
                if chunkRemaining == 0 { chunkRemaining = nil; chunkCRLF = true }
            }
        }
        if !chunked && remaining == 0 { complete = true }
        if eof {
            guard headersRead, chunked ? complete : (remaining == nil || remaining == 0) else { throw AppAPIError.invalidResponse }
            complete = true
        }
        guard !complete || pending.isEmpty else { throw AppAPIError.invalidResponse }
        return events
    }
}
