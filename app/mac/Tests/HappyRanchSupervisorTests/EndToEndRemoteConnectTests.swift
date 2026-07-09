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

// MARK: - StubDaemonServer

/// A minimal HTTP listener on 127.0.0.1 that the HomeConnector reverse-proxies to.
///
/// Supports two modes:
/// - **Normal mode**: answers `GET /tasks` with a 200 + recognizable body
///   (other paths get a 404).
/// - **Stall mode**: receives the request but keeps the connection open
///   indefinitely — used in the revoke-while-connected test so we can
///   verify the connector tears down a live connection.
private final class StubDaemonServer: @unchecked Sendable {

    private let port: UInt16
    private let queue: DispatchQueue
    private var listener: NWListener?
    private let lock = NSLock()
    private var _receivedRequests: [String] = []
    private var _receivedConnectionCount: Int = 0

    var receivedRequests: [String] { lock.withLock { _receivedRequests } }
    var receivedConnectionCount: Int { lock.withLock { _receivedConnectionCount } }

    /// When true, daemon receives requests but NEVER responds (keeps the
    /// connection open indefinitely).  Used for live-session revocation.
    var stallMode: Bool = false

    /// The body to return for a 200 response (normal mode).
    var responseBody: String = "{\"surface\":\"tasks\",\"content\":\"SPA surface loaded via remote connect\"}"

    init(port: UInt16) {
        self.port = port
        self.queue = DispatchQueue(label: "com.happyranch.e2e-stub-daemon-\(UUID().uuidString)")
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
        lock.withLock { _receivedConnectionCount += 1 }

        connection.stateUpdateHandler = { [weak self, weak connection] connState in
            guard let self, let connection else { return }
            switch connState {
            case .ready:
                connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
                    [weak self] data, _, _, error in
                    guard let self else { return }
                    if let data, let request = String(data: data, encoding: .utf8) {
                        self.lock.withLock { self._receivedRequests.append(request) }
                    }

                    if self.stallMode {
                        // Stall: keep the connection open — never respond.
                        return
                    }

                    // Normal mode: respond 200 with a recognizable body
                    let body = self.responseBody
                    let response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
                    connection.send(
                        content: response.data(using: .utf8),
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

// MARK: - TestDaemonCredentialProvider

/// Integration-test credential provider that returns a known raw daemon token.
///
/// The token is intentionally a recognizable string so the security test
/// (step 7b) can assert it NEVER appears in client-visible responses.
private final class TestCredentialProvider: DaemonCredentialProvider, @unchecked Sendable {
    let token: String

    /// Known raw daemon token — must NOT appear in any client-visible bytes.
    static let integrationToken = "hr_token_INTEGRATION_TEST_DO_NOT_LEAK_12345"

    init(token: String = integrationToken) {
        self.token = token
    }

    func credential() throws -> String {
        return token
    }
}

// MARK: - Port allocator

/// Thread-safe port allocator — avoids port conflicts between parallel tests.
/// Uses a rolling range starting at a high base that varies per compilation
/// to reduce TIME_WAIT conflicts from prior test runs.
private let portAllocator = NSLock()
nonisolated(unsafe) private var nextPort: UInt16 = UInt16((Date().timeIntervalSince1970).truncatingRemainder(dividingBy: 2000) + 52000)

/// Allocate a pair of ports (daemonPort, connectorBindPort) for one test.
private func allocatePorts() -> (daemon: UInt16, connector: UInt16) {
    portAllocator.lock()
    defer { portAllocator.unlock() }
    let daemon = nextPort
    let connector = nextPort + 1
    nextPort += 2
    if nextPort > 54900 { nextPort = 52000 }
    return (daemon, connector)
}

// MARK: - HTTP helpers

/// Send an HTTP request and return the response.  Uses a generous 30s timeout
/// for CI compatibility (MEM-118).
private func sendHTTPRequest(
    host: String,
    port: UInt16,
    path: String,
    method: String = "GET",
    headers: [String: String] = [:],
    timeout: TimeInterval = 30
) throws -> HTTPResult {
    let result = HTTPResult()
    let endpoint = NWEndpoint.hostPort(
        host: NWEndpoint.Host(host),
        port: NWEndpoint.Port(integerLiteral: port)
    )
    let connection = NWConnection(to: endpoint, using: .tcp)
    let queue = DispatchQueue(label: "com.happyranch.e2e-test-client-\(UUID().uuidString)")
    let semaphore = DispatchSemaphore(value: 0)

    connection.stateUpdateHandler = { state in
        switch state {
        case .ready:
            var request = "\(method) \(path) HTTP/1.1\r\n"
            request += "Host: \(host):\(port)\r\n"
            for (key, value) in headers {
                request += "\(key): \(value)\r\n"
            }
            request += "Connection: close\r\n\r\n"

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
        throw NSError(domain: "e2e-test", code: -1,
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
    timeout: TimeInterval = 30
) throws -> PersistentConnection {
    let queue = DispatchQueue(label: "com.happyranch.e2e-persist-\(UUID().uuidString)")
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
                    // torn down by the remote side.
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
            domain: "e2e-test", code: -1,
            userInfo: [NSLocalizedDescriptionKey: "Persistent connection timed out"]
        )
    }

    if persistent.wasFailed {
        connection.cancel()
        throw NSError(
            domain: "e2e-test", code: -1,
            userInfo: [NSLocalizedDescriptionKey: "Persistent connection failed"]
        )
    }

    return persistent
}


// MARK: - End-to-End Remote Connect Tests

/// End-to-end integration test: wires the REAL HomeConnector, RealPairingStore,
/// ClientBridge, and a stub loopback daemon together in ONE process.
///
/// This is the verification gate for THR-034: the full connector<->bridge<->pairing-store
/// path has never run together — exercise it here.
@Suite("EndToEndRemoteConnect")
struct EndToEndRemoteConnectTests {

    /// The single comprehensive end-to-end test.
    ///
    /// Steps (all in one test; assert each):
    /// 1. REAL HomeConnector bound loopback via the nil tailnetSelfIP seam;
    ///    REAL RealPairingStore; a TestCredentialProvider.
    ///    Start and poll until .running(port).
    /// 2. Stub loopback daemon listening on a known port.
    /// 3. store.generatePairingCode() -> code.
    /// 4. REAL ClientBridge; start(); redeemPairing() -> hrpair_ credential.
    /// 5. Drive a request through bridge -> connector -> stub daemon for
    ///    an ALLOWED surface (GET /tasks); assert 200 + expected body.
    /// 6. REVOKE-WHILE-CONNECTED: with a live streaming connection OPEN
    ///    through the path, call connector.revokeDevice(credential:) ->
    ///    assert the live connection is TORN DOWN; assert reconnect 403s.
    /// 7. END-TO-END SECURITY: (a) unpaired client is REFUSED (403),
    ///    (b) raw daemon token NEVER appears in any client-visible bytes.
    @Test("full pairing + SPA proxy + revoke + security invariants (THR-034 gate)")
    func fullEndToEndRemoteConnect() async throws {
        // ---- Allocate ports ----
        let ports = allocatePorts()
        let daemonPort = ports.daemon
        let connectorPort = ports.connector

        // ---- Step 2: Start stub daemon ----
        let stubDaemon = StubDaemonServer(port: daemonPort)
        try stubDaemon.start()
        defer { stubDaemon.stop() }

        // ---- Step 1: REAL HomeConnector bound loopback via nil seam ----
        let credProvider = TestCredentialProvider()
        let pairingStore = RealPairingStore()

        let connector = HomeConnector(
            bindHost: "127.0.0.1",
            bindPort: connectorPort,
            daemonPort: daemonPort,
            credentialProvider: credProvider,
            surfaceAllowList: .default,
            pairedDeviceStore: pairingStore,
            tailnetSelfIP: nil  // test seam — allows loopback bind
        )

        // Verify the connector is not in a failed state from init validation
        guard case .stopped = connector.state else {
            #expect(Bool(false), "Connector init should leave state .stopped, got \(connector.state)")
            return
        }

        try connector.start()
        defer { connector.stop() }

        // Poll until .running with port
        var waitCount = 0
        var capturedPort: UInt16 = 0
        while waitCount < 100 {
            if case .running(let port) = connector.state {
                capturedPort = port
                break
            }
            if case .failed(let msg) = connector.state {
                #expect(Bool(false), "Connector failed to start: \(msg)")
                return
            }
            try await Task.sleep(nanoseconds: 100_000_000)  // 0.1s
            waitCount += 1
        }
        guard case .running = connector.state else {
            #expect(Bool(false), "Connector never reached .running after \(waitCount * 100)ms: \(connector.state)")
            return
        }
        #expect(capturedPort > 0, "Connector .running should report a valid port")

        // ---- Step 3: Generate pairing code ----
        let pairingCode = pairingStore.generatePairingCode()
        #expect(!pairingCode.isEmpty, "Pairing code should not be empty")
        #expect(pairingCode.count == 8, "Pairing code should be 8 chars, got \(pairingCode.count)")

        // ---- Step 4: REAL ClientBridge + redeemPairing ----
        let bridge = ClientBridge(
            homeConnectorHost: "127.0.0.1",
            homeConnectorPort: connectorPort,
            bridgePort: nil  // ephemeral
        )
        #expect(bridge.deviceCredential == nil, "deviceCredential should be nil before redeem")

        try bridge.start()
        defer { bridge.stop() }

        // Poll bridge until .running
        var bridgeWaitCount = 0
        while bridgeWaitCount < 100 {
            if case .running = bridge.state { break }
            if case .failed(let msg) = bridge.state {
                #expect(Bool(false), "Bridge failed to start: \(msg)")
                return
            }
            try await Task.sleep(nanoseconds: 100_000_000)  // 0.1s
            bridgeWaitCount += 1
        }
        guard case .running = bridge.state else {
            #expect(Bool(false), "Bridge never reached .running")
            return
        }
        guard let bridgePort = bridge.bridgePort, bridgePort > 0 else {
            #expect(Bool(false), "Bridge should have an allocated port")
            return
        }

        // Redeem the pairing code through the bridge
        try await bridge.redeemPairing(
            code: pairingCode,
            homeHost: "127.0.0.1",
            homePort: connectorPort,
            timeout: 30
        )

        guard let deviceCred = bridge.deviceCredential else {
            #expect(Bool(false), "bridge.deviceCredential should be set after successful redeem")
            return
        }
        #expect(deviceCred.hasPrefix("hrpair_"), "Credential should have hrpair_ prefix, got: \(deviceCred.prefix(20))")

        // Verify the store recognizes this credential
        #expect(pairingStore.isPaired(deviceID: deviceCred), "Store should recognize the paired credential")

        // ---- Step 5: Drive a request through bridge -> connector -> daemon ----
        let response = try sendHTTPRequest(
            host: "127.0.0.1",
            port: bridgePort,
            path: "/tasks",
            timeout: 30
        )
        #expect(response.status == 200,
                "SPA surface request through bridge should return 200, got \(response.status): \(response.body)")
        #expect(response.body.contains("SPA surface loaded via remote connect"),
                "Response body should contain the stub daemon's response: \(response.body)")

        // ---- Step 7b: Security — raw daemon token NEVER leaks to client ----
        let rawToken = TestCredentialProvider.integrationToken
        #expect(!response.raw.contains(rawToken),
                "CRITICAL: daemon token LEAKED in client-visible response: \(rawToken) in \(response.raw)")
        #expect(!response.body.contains(rawToken),
                "CRITICAL: daemon token LEAKED in response body: \(response.body)")

        // ---- Step 6: REVOKE-WHILE-CONNECTED ----
        // Set daemon to stall mode so the proxied connection stays open
        stubDaemon.stallMode = true
        defer { stubDaemon.stallMode = false }

        // Open a persistent connection through the bridge
        let persistent = try sendPersistentHTTPRequest(
            host: "127.0.0.1",
            port: bridgePort,
            path: "/tasks",
            timeout: 30
        )
        defer { persistent.connection.cancel() }

        // Wait for the stub daemon to receive the request — proves the full
        // chain (client->bridge->connector->daemon) is established
        var pollCount = 0
        while stubDaemon.receivedConnectionCount < 2, pollCount < 150 {
            try await Task.sleep(nanoseconds: 100_000_000)  // 0.1s
            pollCount += 1
        }
        // Small extra sleep to ensure HomeConnector has registered the connection
        try await Task.sleep(nanoseconds: 200_000_000)  // 0.2s
        #expect(
            stubDaemon.receivedConnectionCount >= 2,
            "Stub daemon should have received the proxied request (conns: \(stubDaemon.receivedConnectionCount)) after \(pollCount * 100)ms"
        )

        // REVOKE the device — this must tear down the ACTIVE session
        let revoked = connector.revokeDevice(credential: deviceCred)
        #expect(revoked, "Revocation should succeed")

        // The persistent connection MUST have been cancelled
        let cancelled = persistent.waitForCancellation(timeout: 10)
        #expect(cancelled, "Active connection was NOT torn down by revocation — live-session invariant BROKEN")

        // RECONNECT with the revoked credential must 403
        let reconnectResponse = try sendHTTPRequest(
            host: "127.0.0.1",
            port: bridgePort,
            path: "/tasks",
            timeout: 30
        )
        #expect(reconnectResponse.status == 403,
                "Expected 403 on reconnect after revocation, got \(reconnectResponse.status): \(reconnectResponse.body)")
        #expect(
            reconnectResponse.body.contains("not paired") || reconnectResponse.body.contains("Forbidden"),
            "Reconnect response should indicate pairing revoked, got: \(reconnectResponse.body)"
        )

        // Turn off stall mode so teardown is clean
        stubDaemon.stallMode = false

        // ---- Step 7a: Security — unpaired client is REFUSED (403) ----
        // Create a second bridge WITHOUT deviceCredential set
        let unpairedBridge = ClientBridge(
            homeConnectorHost: "127.0.0.1",
            homeConnectorPort: connectorPort
        )
        #expect(unpairedBridge.deviceCredential == nil)

        try unpairedBridge.start()
        defer { unpairedBridge.stop() }

        var ubWaitCount = 0
        while ubWaitCount < 100 {
            if case .running = unpairedBridge.state { break }
            try await Task.sleep(nanoseconds: 100_000_000)  // 0.1s
            ubWaitCount += 1
        }
        guard case .running = unpairedBridge.state else {
            #expect(Bool(false), "Unpaired bridge never reached .running")
            return
        }
        guard let ubPort = unpairedBridge.bridgePort, ubPort > 0 else {
            #expect(Bool(false), "Unpaired bridge should have an allocated port")
            return
        }

        let unpairedResponse = try sendHTTPRequest(
            host: "127.0.0.1",
            port: ubPort,
            path: "/tasks",
            timeout: 30
        )
        #expect(unpairedResponse.status == 403,
                "Unpaired client should get 403, got \(unpairedResponse.status): \(unpairedResponse.body)")
        #expect(
            unpairedResponse.body.contains("not paired") || unpairedResponse.body.contains("Forbidden"),
            "Unpaired response should indicate device not paired, got: \(unpairedResponse.body)"
        )

        // ---- Step 7b (continued): Verify no token leak in any client response ----
        #expect(!unpairedResponse.raw.contains(rawToken),
                "CRITICAL: daemon token LEAKED in 403 response to unpaired client")
    }
}
