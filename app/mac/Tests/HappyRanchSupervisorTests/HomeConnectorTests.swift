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
    private var _receivedConnectionCount: Int = 0

    var receivedRequests: [String] {
        lock.withLock { _receivedRequests }
    }

    /// Number of connections accepted (not just requests).
    var receivedConnectionCount: Int {
        lock.withLock { _receivedConnectionCount }
    }

    var responseBody: String = "{\"status\":\"ok\"}"
    var responseStatus: Int = 200
    var responseHeaders: [String: String] = ["Content-Type": "application/json"]

    /// When true, the daemon receives requests but NEVER responds (keeps
    /// the connection open indefinitely).  Used to test live-session
    /// revocation — the connector must tear down a stalled connection
    /// when the device is revoked.
    var stallMode: Bool = false

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
        self.lock.withLock { self._receivedConnectionCount += 1 }

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

                    // Stall mode: record the request but keep the connection
                    // open indefinitely — used to test live-session revocation.
                    if self.stallMode {
                        return
                    }

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
    func generatePairingCode() -> String { return "FAKE-CODE" }
    func pair(usingCode: String, deviceName: String) -> String? { return "fake-credential" }
    func revokePairing(credential: String) -> Bool { return true }
}
/// Permissive stub for existing tests that don't test pairing behavior.
private func makePermissiveStub() -> StubPairedDeviceStore {
    let stub = StubPairedDeviceStore()
    stub.setAllowAll(true)
    return stub
}


// MARK: - Helpers

/// Thread-safe port allocator — avoids port conflicts between parallel tests.
/// Start above common service ports and the xray proxy range (50000+).
private let portAllocator = NSLock()
nonisolated(unsafe) private var nextPort: UInt16 = 55000

/// Allocate a pair of ports (daemonPort, bindPort) for one test.
private func allocatePortPair() -> (daemon: UInt16, bind: UInt16) {
    portAllocator.lock()
    defer { portAllocator.unlock() }
    let daemon = nextPort
    let bind = nextPort + 1
    nextPort += 2
    if nextPort > 60000 { nextPort = 55000 }
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

// MARK: - Persistent connection helper (live-session revocation)

/// A connection that stays alive across the test so we can observe
/// whether it was torn down by revocation.
private final class PersistentConnection: @unchecked Sendable {
    let connection: NWConnection
    let queue: DispatchQueue
    private let lock = NSLock()
    private var _wasCancelled = false
    private var _wasFailed = false
    private var _cancelledSemaphore = DispatchSemaphore(value: 0)

    var wasCancelled: Bool { lock.withLock { _wasCancelled } }
    var wasFailed: Bool { lock.withLock { _wasFailed } }

    func markCancelled() {
        lock.withLock { _wasCancelled = true }
        _cancelledSemaphore.signal()
    }
    func markFailed() { lock.withLock { _wasFailed = true } }

    /// Wait up to `timeout` seconds for the connection to be cancelled.
    func waitForCancellation(timeout: TimeInterval) -> Bool {
        return _cancelledSemaphore.wait(timeout: .now() + timeout) != .timedOut
    }

    init(connection: NWConnection, queue: DispatchQueue) {
        self.connection = connection
        self.queue = queue
    }
}

/// Open a persistent connection to the given host:port, send an HTTP request,
/// and return the connection kept alive.  The caller is responsible for
/// observing the connection state and eventually cancelling it.
private func sendPersistentHTTPRequest(
    host: String,
    port: UInt16,
    path: String,
    method: String = "GET",
    headers: [String: String] = [:],
    timeout: TimeInterval = 5
) throws -> PersistentConnection {
    let queue = DispatchQueue(label: "com.happyranch.test-persist-\(UUID().uuidString)")
    let endpoint = NWEndpoint.hostPort(
        host: NWEndpoint.Host(host),
        port: NWEndpoint.Port(integerLiteral: port)
    )
    let connection = NWConnection(to: endpoint, using: .tcp)
    let persistent = PersistentConnection(connection: connection, queue: queue)
    let readySemaphore = DispatchSemaphore(value: 0)

    connection.stateUpdateHandler = { state in
        switch state {
        case .ready:
            var request = "\(method) \(path) HTTP/1.1\r\n"
            request += "Host: \(host):\(port)\r\n"
            for (key, value) in headers {
                request += "\(key): \(value)\r\n"
            }
            request += "Connection: keep-alive\r\n\r\n"
            connection.send(
                content: request.data(using: .utf8),
                completion: .contentProcessed { _ in
                    // Start a receive to detect when the connection is
                    // torn down by the remote side.  The callback fires
                    // with an error or isComplete when the connection closes.
                    connection.receive(
                        minimumIncompleteLength: 1,
                        maximumLength: 65536
                    ) { _, _, isComplete, error in
                        if error != nil || isComplete {
                            persistent.markCancelled()
                        }
                    }
                    readySemaphore.signal()
                }
            )
        case .failed:
            persistent.markFailed()
            readySemaphore.signal()
        case .cancelled:
            persistent.markCancelled()
        default:
            break
        }
    }
    connection.start(queue: queue)

    if readySemaphore.wait(timeout: .now() + timeout) == .timedOut {
        connection.cancel()
        throw NSError(
            domain: "test", code: -1,
            userInfo: [NSLocalizedDescriptionKey: "Persistent connection timed out"]
        )
    }

    if persistent.wasFailed {
        connection.cancel()
        throw NSError(
            domain: "test", code: -1,
            userInfo: [NSLocalizedDescriptionKey: "Persistent connection failed"]
        )
    }

    return persistent
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
        pairedDeviceStore: PairedDeviceStore? = nil,
        body: (HomeConnector) throws -> Void
    ) throws {
        let store = pairedDeviceStore ?? {
            let stub = StubPairedDeviceStore()
            stub.setAllowAll(true)
            return stub
        }()
        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: bindPort,
            daemonPort: daemonPort,
            credentialProvider: credentialProvider,
            surfaceAllowList: surfaceAllowList,
            pairedDeviceStore: store
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
            credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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

    @Test("allows request when device is paired (stub explicitly configured to allow)")
    func allowsWhenPaired() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairedStore = makePermissiveStub()
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
            daemonPort: 9876, credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: makePermissiveStub()
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

    // MARK: - A2.3 Pairing credential tests

    // --- INVARIANT 3 (HARD): Unpaired peer is REJECTED ---

    @Test("rejects request WITHOUT device credential header (unpaired peer)")
    func rejectsUnpairedPeerNoCredential() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()

        // Do NOT pair any device — the store is empty.
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Send request WITHOUT X-HappyRanch-Device-Credential header.
        // The connector MUST reject: tailnet presence is necessary but
        // NOT sufficient — a random tailnet peer gets NOTHING.
        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks", timeout: 5
        )
        #expect(response.status == 403,
                "Expected 403 Forbidden for unpaired peer, got \(response.status)")
        #expect(response.body.contains("not paired"))
    }

    @Test("rejects request with INVALID device credential")
    func rejectsInvalidCredential() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Send with a bogus credential
        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": "hrpair_fakeinvalid12345678"],
            timeout: 5
        )
        #expect(response.status == 403,
                "Expected 403 Forbidden for invalid credential, got \(response.status)")
        #expect(response.body.contains("not paired"))
    }

    @Test("allows request with VALID device credential after pairing")
    func allowsPairedPeerWithValidCredential() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        fakeDaemon.responseBody = "{\"daemon\":\"response\"}"
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()

        // Generate a pairing code and pair a device
        let code = pairingStore.generatePairingCode()
        guard let credential = pairingStore.pair(usingCode: code, deviceName: "test-client") else {
            #expect(Bool(false), "Pairing should succeed with valid code")
            return
        }

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Send with the valid credential
        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": credential],
            timeout: 5
        )
        #expect(response.status == 200,
                "Expected 200 for paired peer, got \(response.status)")
        #expect(response.body.contains("daemon"))
    }

    // --- Pairing endpoint tests ---

    @Test("POST /pair with valid code returns a credential")
    func pairingEndpointReturnsCredential() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()
        let code = pairingStore.generatePairingCode()

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Send POST /pair with the code as body
        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort,
            path: "/pair", method: "POST",
            headers: ["Content-Type": "text/plain"],
            body: code,
            timeout: 5
        )
        #expect(response.status == 200,
                "Expected 200 for valid pairing code, got \(response.status)")
        #expect(response.body.contains("\"credential\""))
        #expect(response.body.contains("hrpair_"))
        #expect(!response.body.contains("hr_token_"))
        #expect(!response.body.contains("hr_session_"))
    }

    @Test("POST /pair with invalid code returns 403")
    func pairingEndpointRejectsInvalidCode() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()
        _ = pairingStore.generatePairingCode()  // generates a code, but we send a different one

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort,
            path: "/pair", method: "POST",
            body: "WRONG-CODE",
            timeout: 5
        )
        #expect(response.status == 403,
                "Expected 403 for invalid pairing code, got \(response.status)")
        #expect(response.body.contains("invalid"))
    }

    @Test("POST /pair is one-time-use — second attempt fails")
    func pairingCodeIsOneTimeUse() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()
        let code = pairingStore.generatePairingCode()

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // First attempt: should succeed
        let response1 = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort,
            path: "/pair", method: "POST",
            body: code, timeout: 5
        )
        #expect(response1.status == 200)

        // Second attempt with same code: should fail
        let response2 = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort,
            path: "/pair", method: "POST",
            body: code, timeout: 5
        )
        #expect(response2.status == 403,
                "Expected 403 for reused pairing code, got \(response2.status)")
    }

    // --- Revocation tests (A2.4 pre-groundwork) ---

    @Test("rejects request with revoked credential")
    func rejectsRevokedCredential() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()

        let code = pairingStore.generatePairingCode()
        guard let credential = pairingStore.pair(usingCode: code, deviceName: "test-client") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Before revocation: request succeeds
        let beforeResponse = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": credential],
            timeout: 5
        )
        #expect(beforeResponse.status == 200, "Expected 200 before revocation")

        // Revoke
        let revoked = pairingStore.revokePairing(credential: credential)
        #expect(revoked, "Revocation should succeed")

        // After revocation: request is rejected
        let afterResponse = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": credential],
            timeout: 5
        )
        #expect(afterResponse.status == 403,
                "Expected 403 after revocation, got \(afterResponse.status)")
        #expect(afterResponse.body.contains("not paired"))
    }

    // MARK: - A2.4 LIVE-SESSION REVOCATION

    /// INVARIANT: Revocation tears down ACTIVE proxy connections, not just
    /// future ones.  This test opens a live session, revokes the device,
    /// and asserts the connection is IMMEDIATELY dropped AND reconnect is
    /// refused — satisfying both halves of the A2.4 invariant.
    @Test("revocation tears down active proxy connection — live session dropped")
    func revocationTearsDownActiveSession() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        // Use a stalling fake daemon so the proxied connection stays open
        // indefinitely — the revocation should tear it down.
        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        fakeDaemon.stallMode = true
        try fakeDaemon.start()
        defer {
            fakeDaemon.stallMode = false
            fakeDaemon.stop()
        }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let pairingStore = RealPairingStore()

        // Pair a device
        let code = pairingStore.generatePairingCode()
        guard let credential = pairingStore.pair(usingCode: code, deviceName: "test-client") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: pairingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Open a persistent connection with the paired credential.
        // This connection stays open (daemon is stalling).
        let persistent = try sendPersistentHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": credential],
            timeout: 5
        )
        defer { persistent.connection.cancel() }

        // Wait for the daemon to receive the proxied request so we know
        // the session was established.
        var pollCount = 0
        while fakeDaemon.receivedRequests.isEmpty, pollCount < 100 {
            Thread.sleep(forTimeInterval: 0.1)
            pollCount += 1
        }
        #expect(
            !fakeDaemon.receivedRequests.isEmpty || fakeDaemon.receivedConnectionCount > 0,
            "Fake daemon should have received the proxied request (conns: \(fakeDaemon.receivedConnectionCount), reqs: \(fakeDaemon.receivedRequests.count)) after \(pollCount * 100)ms"
        )

        // REVOKE: this must tear down the ACTIVE session.
        let revoked = connector.revokeDevice(credential: credential)
        #expect(revoked, "Revocation should succeed")

        // The persistent connection MUST have been cancelled.
        let cancelled = persistent.waitForCancellation(timeout: 3)
        #expect(cancelled, "Active connection was NOT torn down by revocation")

        // RECONNECT is refused — future requests with the revoked
        // credential get 403.
        let reconnectResponse = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": credential],
            timeout: 5
        )
        #expect(reconnectResponse.status == 403,
                "Expected 403 on reconnect after revocation, got \(reconnectResponse.status)")
        #expect(reconnectResponse.body.contains("not paired"))
    }

    // MARK: - FINDING 3 [CRITICAL] — permissive paired-device default

    @Test("REJECTS request WITHOUT device credential header (deny-by-default store)")
    func rejectsMissingCredentialWithDenyingStore() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        // Use a DENYING stub — the default behavior after FINDING 3 fix
        let denyingStore = StubPairedDeviceStore()  // denies all by default
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: denyingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Send request WITHOUT any device credential header.
        // The denying store MUST reject — a random tailnet peer gets NOTHING.
        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks", timeout: 5
        )
        #expect(response.status == 403,
                "Expected 403 for missing credential with deny-by-default store, got \(response.status)")
        #expect(response.body.contains("not paired"))
    }

    @Test("REJECTS request with INVALID device credential (deny-by-default store)")
    func rejectsInvalidCredentialWithDenyingStore() throws {
        let ports = allocatePortPair(); let daemonPort = ports.daemon
        let bindPort = ports.bind

        let fakeDaemon = FakeDaemonServer(port: daemonPort)
        try fakeDaemon.start()
        defer { fakeDaemon.stop() }

        let credProvider = FakeCredentialProvider(token: "test-token")
        let denyingStore = StubPairedDeviceStore()  // denies all by default
        let connector = HomeConnector(
            bindHost: "127.0.0.1", bindPort: bindPort,
            daemonPort: daemonPort, credentialProvider: credProvider,
            pairedDeviceStore: denyingStore
        )

        try connector.start()
        var waitCount = 0
        while case .stopped = connector.state, waitCount < 50 {
            Thread.sleep(forTimeInterval: 0.05); waitCount += 1
        }
        defer { connector.stop() }

        // Send with a bogus credential
        let response = try sendHTTPRequest(
            host: "127.0.0.1", port: bindPort, path: "/tasks",
            headers: ["X-HappyRanch-Device-Credential": "hrpair_fakeinvalid12345678"],
            timeout: 5
        )
        #expect(response.status == 403,
                "Expected 403 for invalid credential with deny-by-default store, got \(response.status)")
        #expect(response.body.contains("not paired"))
    }

    // MARK: - FINDING 4 [HIGH] — unvalidated bindHost

    @Test("bindHost validation: wildcard 0.0.0.0 sets state to .failed")
    func bindHostRejectsWildcard() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let store = makePermissiveStub()

        let connector = HomeConnector(
            bindHost: "0.0.0.0",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider,
            pairedDeviceStore: store
        )

        // Must be in .failed state due to wildcard rejection
        guard case .failed(let msg) = connector.state else {
            #expect(Bool(false), "Expected .failed state for wildcard bind, got \(connector.state)")
            return
        }
        #expect(msg.contains("wildcard") || msg.contains("0.0.0.0"),
                "Error message should mention wildcard rejection, got: \(msg)")
    }

    @Test("bindHost validation: empty host sets state to .failed")
    func bindHostRejectsEmpty() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let store = makePermissiveStub()

        let connector = HomeConnector(
            bindHost: "",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider,
            pairedDeviceStore: store
        )

        guard case .failed(let msg) = connector.state else {
            #expect(Bool(false), "Expected .failed state for empty bind, got \(connector.state)")
            return
        }
        #expect(msg.contains("empty") || msg.contains("must not"),
                "Error message should mention empty rejection, got: \(msg)")
    }

    @Test("bindHost validation: public IP sets state to .failed with tailnetSelfIP")
    func bindHostRejectsPublicIP() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let store = makePermissiveStub()

        let connector = HomeConnector(
            bindHost: "8.8.8.8",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider,
            pairedDeviceStore: store,
            tailnetSelfIP: "100.64.0.1"
        )

        guard case .failed(let msg) = connector.state else {
            #expect(Bool(false), "Expected .failed state for public IP, got \(connector.state)")
            return
        }
        #expect(msg.contains("not a tailnet") || msg.contains("8.8.8.8"),
                "Error message should mention non-tailnet rejection, got: \(msg)")
    }

    @Test("bindHost validation: tailnet 100.x address is ACCEPTED")
    func bindHostAcceptsTailnetAddress() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let store = makePermissiveStub()

        let connector = HomeConnector(
            bindHost: "100.64.0.1",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider,
            pairedDeviceStore: store,
            tailnetSelfIP: "100.64.0.1"
        )
        #expect(connector.state == .stopped,
                "Tailnet 100.x address should be accepted, got: \(connector.state)")
    }

    @Test("bindHost validation: loopback is ACCEPTED when tailnetSelfIP is nil (test seam)")
    func bindHostAcceptsLoopbackWithoutTailnetIP() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let store = makePermissiveStub()

        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider,
            pairedDeviceStore: store
        )
        #expect(connector.state == .stopped,
                "Loopback should be accepted when tailnetSelfIP is nil, got: \(connector.state)")
    }

    @Test("bindHost validation: loopback sets state to .failed with tailnetSelfIP (production)")
    func bindHostRejectsLoopbackWithTailnetIP() {
        let credProvider = FakeCredentialProvider(token: "test-token")
        let store = makePermissiveStub()

        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: 8443,
            daemonPort: 9876,
            credentialProvider: credProvider,
            pairedDeviceStore: store,
            tailnetSelfIP: "100.64.0.1"
        )

        guard case .failed(let msg) = connector.state else {
            #expect(Bool(false), "Expected .failed state for loopback in production, got \(connector.state)")
            return
        }
        #expect(msg.contains("loopback") || msg.contains("production"),
                "Error message should mention loopback rejection, got: \(msg)")
    }
}
