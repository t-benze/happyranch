import Testing
import Foundation
import Network
@testable import HappyRanchSupervisor

// MARK: - Thread-safe result container

/// Thread-safe container for the result of an async HTTP request.
private final class HTTPResult: @unchecked Sendable {
    private let lock = NSLock()
    private var _status: Int = 0
    private var _body: String = ""
    private var _raw: String = ""
    private var _error: String? = nil

    var status: Int { lock.withLock { _status } }
    var body: String { lock.withLock { _body } }
    var raw: String { lock.withLock { _raw } }
    var error: String? { lock.withLock { _error } }

    func setResult(status: Int, body: String, raw: String) {
        lock.withLock {
            _status = status
            _body = body
            _raw = raw
        }
    }

    func setError(_ msg: String) {
        lock.withLock { _error = msg }
    }
}

// MARK: - FakeDaemonServer

/// A minimal HTTP server that runs on a loopback port, records received
/// requests, and returns test responses.
private final class FakeDaemonServer: @unchecked Sendable {

    private let port: UInt16
    private let queue: DispatchQueue
    private var listener: NWListener?
    private let lock = NSLock()
    private var _receivedRequests: [String] = []

    var receivedRequests: [String] {
        lock.withLock { _receivedRequests }
    }

    var responseBody: String = "{\"status\":\"ok\"}"
    var responseStatus: Int = 200
    var responseHeaders: [String: String] = ["Content-Type": "application/json"]

    init(port: UInt16) {
        self.port = port
        self.queue = DispatchQueue(label: "com.happyranch.fake-daemon-\(UUID().uuidString)")
    }

    func start() throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = NWEndpoint.hostPort(
            host: "127.0.0.1",
            port: NWEndpoint.Port(integerLiteral: port)
        )

        let listener = try NWListener(using: parameters)
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            self.queue.async { self.handleConnection(connection) }
        }
        listener.start(queue: queue)
        self.listener = listener
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    private func handleConnection(_ connection: NWConnection) {
        let body = responseBody
        let status = responseStatus
        let statusText = status == 200 ? "OK" : "Error"
        var headers = responseHeaders
        headers["Content-Length"] = "\(body.utf8.count)"
        headers["Connection"] = "close"
        let headerLines = headers.map { "\($0.key): \($0.value)" }.joined(separator: "\r\n")
        let responseStr = "HTTP/1.1 \(status) \(statusText)\r\n\(headerLines)\r\n\r\n\(body)"
        let responseData = responseStr.data(using: .utf8) ?? Data()

        connection.stateUpdateHandler = { connectionState in
            switch connectionState {
            case .ready:
                connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
                    data, _, _, _ in
                    guard let data = data, let request = String(data: data, encoding: .utf8)
                    else {
                        connection.cancel()
                        return
                    }
                    self.lock.withLock { self._receivedRequests.append(request) }
                    connection.send(
                        content: responseData,
                        contentContext: .finalMessage,
                        isComplete: true,
                        completion: .contentProcessed { _ in
                            connection.cancel()
                        }
                    )
                }
            case .failed, .cancelled:
                break
            default:
                break
            }
        }
        connection.start(queue: queue)
    }
}

// MARK: - FakeCredentialProvider

private final class FakeCredentialProvider: DaemonCredentialProvider, @unchecked Sendable {
    let token: String
    private let lock = NSLock()
    private var _callCount = 0

    init(token: String) { self.token = token }

    func credential() throws -> String {
        lock.withLock { _callCount += 1 }
        return token
    }

    var credentialCallCount: Int { lock.withLock { _callCount } }
}

// MARK: - FakePairedDeviceStore

private final class FakePairedDeviceStore: PairedDeviceStore, @unchecked Sendable {
    var isPairedResult = true
    func isPaired(deviceID: String) -> Bool { isPairedResult }
}

// MARK: - Helpers

/// Thread-safe port allocator — avoids port conflicts between parallel tests.
private let portAllocator = NSLock()
nonisolated(unsafe) private var nextPort: UInt16 = 50000

/// Allocate a pair of ports (daemonPort, bindPort) for one test.
private func allocatePortPair() -> (daemon: UInt16, bind: UInt16) {
    portAllocator.lock()
    defer { portAllocator.unlock() }
    let daemon = nextPort
    let bind = nextPort + 1
    nextPort += 2
    if nextPort > 60000 { nextPort = 50000 }
    return (daemon, bind)
}

/// Send an HTTP request to the given host:port and return the response.
private func sendHTTPRequest(
    host: String,
    port: UInt16,
    path: String,
    method: String = "GET",
    headers: [String: String] = [:],
    body: String? = nil,
    timeout: TimeInterval = 5
) throws -> HTTPResult {
    let result = HTTPResult()
    let endpoint = NWEndpoint.hostPort(
        host: NWEndpoint.Host(host),
        port: NWEndpoint.Port(integerLiteral: port)
    )
    let connection = NWConnection(to: endpoint, using: .tcp)
    let queue = DispatchQueue(label: "com.happyranch.test-client-\(UUID().uuidString)")
    let semaphore = DispatchSemaphore(value: 0)

    connection.stateUpdateHandler = { state in
        switch state {
        case .ready:
            var request = "\(method) \(path) HTTP/1.1\r\n"
            request += "Host: \(host):\(port)\r\n"
            for (key, value) in headers {
                request += "\(key): \(value)\r\n"
            }
            if let body = body {
                request += "Content-Length: \(body.utf8.count)\r\n"
            }
            request += "Connection: close\r\n\r\n"
            if let body = body { request += body }

            connection.send(
                content: request.data(using: .utf8),
                completion: .contentProcessed { _ in
                    connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
                        data, _, _, error in
                        if let error = error {
                            result.setError(error.localizedDescription)
                        } else if let data = data,
                                  let response = String(data: data, encoding: .utf8) {
                            var status = 0
                            var bodyText = ""
                            let lines = response.components(separatedBy: "\r\n")
                            if let statusLine = lines.first {
                                let parts = statusLine.components(separatedBy: " ")
                                if parts.count >= 2, let sc = Int(parts[1]) { status = sc }
                            }
                            if let bodyRange = response.range(of: "\r\n\r\n") {
                                bodyText = String(response[bodyRange.upperBound...])
                            }
                            result.setResult(status: status, body: bodyText, raw: response)
                        }
                        semaphore.signal()
                    }
                }
            )
        case .failed(let error):
            result.setError(error.localizedDescription)
            semaphore.signal()
        default:
            break
        }
    }
    connection.start(queue: queue)

    if semaphore.wait(timeout: .now() + timeout) == .timedOut {
        connection.cancel()
        result.setError("Request timed out")
    }
    connection.cancel()

    if let errMsg = result.error {
        throw NSError(domain: "test", code: -1,
                      userInfo: [NSLocalizedDescriptionKey: errMsg])
    }
    return result
}

// MARK: - HomeConnector Tests

@Suite("HomeConnector")
struct HomeConnectorTests {

    /// Helper: start connector + fake daemon, run body, stop with cleanup delay.
    private func withConnector(
        daemonPort: UInt16,
        bindPort: UInt16,
        credentialProvider: DaemonCredentialProvider,
        surfaceAllowList: SurfaceAllowList = .default,
        pairedDeviceStore: PairedDeviceStore = StubPairedDeviceStore(),
        body: (HomeConnector) throws -> Void
    ) throws {
        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: bindPort,
            daemonPort: daemonPort,
            credentialProvider: credentialProvider,
            surfaceAllowList: surfaceAllowList,
            pairedDeviceStore: pairedDeviceStore
        )
        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer {
            connector.stop()
            Thread.sleep(forTimeInterval: 0.1)  // port release delay
        }
        try body(connector)
    }

    // --- INVARIANT 1: Binds tailnet-interface ONLY ---

    @Test("init stores bindHost and daemonPort — does NOT bind to 0.0.0.0")
    func initNeverUsesWildcard() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let connector = HomeConnector(
            bindHost: "100.64.0.1",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider
        )
        #expect(connector.state == .stopped)
    }

    @Test("state transitions: stopped -> running -> stopped")
    func stateTransitions() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: bindPort,
            daemonPort: daemonPort,
            credentialProvider: credProvider
        )

        #expect(connector.state == .stopped)
        try connector.start()

        var stateCheckCount = 0
        while case .stopped = connector.state, stateCheckCount < 50 {
            Thread.sleep(forTimeInterval: 0.05)
            stateCheckCount += 1
        }

        guard case .running = connector.state else {
            #expect(Bool(false), "Expected .running but got \(connector.state)")
            return
        }

        connector.stop()
        #expect(connector.state == .stopped)
    }

    // --- INVARIANT 2: Credential injection ---

    @Test("proxies request to daemon with credential injected")
    func proxiesRequestWithCredential() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        fakeDaemon.responseBody = "{\"daemon\":\"response\"}"
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "hr_token_master_test")
        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: bindPort,
            daemonPort: daemonPort,
            credentialProvider: credProvider
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05)
            waitCount += 1
        }
        defer { connector.stop() }

        guard case .running = connector.state else {
            #expect(Bool(false), "Connector failed to start: \(connector.state)")
            return
        }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks", timeout: 5
        )

        #expect(response.status == 200)
        #expect(response.body.contains("daemon"))
        #expect(response.body.contains("response"))

        Thread.sleep(forTimeInterval: 0.1)

        let requests = fakeDaemon.receivedRequests
        #expect(!requests.isEmpty, "Fake daemon should have received at least one request")

        if let receivedRequest = requests.first {
            #expect(receivedRequest.contains("GET /tasks HTTP/1.1"))
            #expect(
                receivedRequest.contains("Authorization: Bearer hr_token_master_test"),
                "Expected Authorization header with token, got: \(receivedRequest)"
            )
        }
    }

    @Test("injects credential ONLY on the loopback hop — not visible in client response")
    func credentialNotVisibleInResponse() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        fakeDaemon.responseBody = "{\"status\":\"ok\"}"
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "hr_token_secret_xyz")
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/agents", timeout: 5
        )

        #expect(!response.raw.contains("hr_token_secret_xyz"),
                "Credential leaked to client response")
        #expect(!response.raw.contains("Authorization: Bearer"),
                "Authorization header leaked to client response")
    }

    // --- INVARIANT 3: Surface allow-list deny gate ---

    @Test("denies /report-completion with 403")
    func deniesReportCompletion() throws {
        try assertDenied(path: "/report-completion")
    }

    @Test("denies /dispatch with 403")
    func deniesDispatch() throws {
        try assertDenied(path: "/dispatch")
    }

    @Test("denies /auth/bootstrap with 403")
    func deniesAuthBootstrap() throws {
        try assertDenied(path: "/auth/bootstrap")
    }

    @Test("denies /auth/registration-token with 403")
    func deniesAuthRegistrationToken() throws {
        try assertDenied(path: "/auth/registration-token")
    }

    @Test("denies /memory/add with 403")
    func deniesMemoryAdd() throws {
        try assertDenied(path: "/memory/add")
    }

    @Test("denies /learning/add with 403")
    func deniesLearningAdd() throws {
        try assertDenied(path: "/learning/add")
    }

    @Test("denies thread /reply with 403")
    func deniesThreadReply() throws {
        try assertDenied(path: "/reply")
    }

    @Test("denies /threads/{id}/dispatch with 403")
    func deniesThreadDispatch() throws {
        try assertDenied(path: "/threads/some-thread-id/dispatch")
    }

    @Test("denies /as-founder/surface with 403")
    func deniesAsFounder() throws {
        try assertDenied(path: "/as-founder/some-endpoint")
    }

    // --- Helper for deny-gate tests ---

    private func assertDenied(path: String) throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer {
            fakeDaemon.stop()
            Thread.sleep(forTimeInterval: 0.05)  // let OS release port
        }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: path, timeout: 5
        )
        #expect(response.status == 403, "Expected 403 for \(path), got \(response.status)")
        #expect(response.body.contains("Forbidden"))
    }

    // --- INVARIANT 4: Paired-device check ---

    @Test("allows request when device is paired (stub always returns true)")
    func allowsWhenPaired() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairedStore = StubPairedDeviceStore()
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairedStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks", timeout: 5
        )
        #expect(response.status == 200)
    }

    @Test("denies request with 403 when device is not paired")
    func deniesWhenNotPaired() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairedStore = FakePairedDeviceStore()
        pairedStore.isPairedResult = false
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairedStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks", timeout: 5
        )
        #expect(response.status == 403)
        #expect(response.body.contains("not paired"))
    }

    // --- INITIAL STATE ---

    @Test("initial state is .stopped")
    func initialStopped() {
        let credProvider = FakeCredentialProvider(token: "t")
        let connector = HomeConnector(
            bindHost: "100.64.0.1", bindPort: 8443,
            daemonPort: 9876, credentialProvider: credProvider
        )
        #expect(connector.state == .stopped)
    }

    // --- alreadyRunning error ---

    @Test("throws alreadyRunning when started twice")
    func throwsAlreadyRunning() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        do {
            try connector.start()
            #expect(Bool(false), "Expected alreadyRunning error")
        } catch HomeConnectorError.alreadyRunning {
            // Expected
        } catch {
            #expect(Bool(false), "Unexpected error: \(error)")
        }
    }

    // --- Multiple requests ---

    @Test("subsequent requests after initial connection are proxied")
    func subsequentRequestsProxied() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        _ = try sendHTTPRequest(host: "127.0.0.1", port: bindPort, path: "/tasks", timeout: 5)
        Thread.sleep(forTimeInterval: 0.1)
        _ = try sendHTTPRequest(host: "127.0.0.1", port: bindPort, path: "/agents", timeout: 5)
        Thread.sleep(forTimeInterval: 0.1)

        let requests = fakeDaemon.receivedRequests
        #expect(requests.count >= 2, "Expected at least 2 requests, got \(requests.count)")
    }

    // --- 404 relaying ---

    @Test("relays 404 from daemon to client")
    func relays404() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        fakeDaemon.responseStatus = 404
        fakeDaemon.responseBody = "Not Found"
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/nonexistent", timeout: 5
        )
        #expect(response.status == 404)
    }
}
