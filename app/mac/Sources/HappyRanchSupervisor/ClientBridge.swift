import Foundation
import Network
import CTsnet
import Darwin

// MARK: - RedeemPairingError

/// Errors surfaced by the client-side pairing-redeem handshake.
public enum RedeemPairingError: Error, Equatable, LocalizedError {
    /// The home connector returned 403 — the pairing code is invalid or expired.
    case refused

    /// Could not connect to the home connector (network error, unreachable).
    case connectionFailed(String)

    /// The home connector returned an unexpected response
    /// (e.g. 200 without a credential field, or malformed JSON).
    case invalidResponse

    /// Fixed native categories, distinct from pairing-code refusal.
    case managed(ManagedNetworkError)

    static func managedDialFailure(_ result: Int32) -> Self {
        switch result {
        case -13: return .connectionFailed("Request timed out")
        case -14: return .connectionFailed("Unreachable")
        default: return .managed(.result(result))
        }
    }

    public var errorDescription: String? {
        switch self {
        case .refused: return "Invalid or expired pairing code"
        case .connectionFailed(let message): return message
        case .invalidResponse: return "Invalid response from home connector"
        case .managed(let error): return error.localizedDescription
        }
    }
}

// MARK: - ClientBridge

/// The CLIENT LOOPBACK BRIDGE — runs on the **client** (away) machine.
///
/// This component:
/// 1. Stands up an HTTP listener on `http://127.0.0.1:<ephemeral>/` so the
///    unmodified SPA (WKWebView) connects via the existing
///    ``LocalLoopbackTransport`` seam — **the SPA is UNMODIFIED**.
/// 2. Answers `GET /auth/bootstrap` with a **session-scoped credential**
///    (`hr_session_<random>`) — **NEVER** the raw daemon token (which is
///    home-only and NEVER leaves the home machine).
/// 3. Forwards all other HTTP requests (framed HTTP, SSE, WebSocket upgrade)
///    to the home connector over the tailnet, **stripping** the SPA's
///    Authorization header so the session credential never crosses the tailnet.
///
/// The home connector (A2.1) injects the real daemon token on its loopback
/// hop to the daemon.  The raw daemon token NEVER leaves the home machine.
///
/// ## Hard invariants (reviewer BLOCKs on breach):
/// - Client bridge answers bootstrap with session-scoped credential,
///   **never the raw daemon token**.
/// - The SPA is **unmodified** — reuses the ``RuntimeTransport`` loopback
///   seam; zero changes to web code or `RuntimeTransport.swift`.
/// - Ride-installed only: No new dependency, no new entitlement.
/// - Zero `runtime/daemon/` change.
public final class ClientBridge: @unchecked Sendable {

    // MARK: - State

    /// Current lifecycle state of the bridge.
    public enum State: Equatable, CustomStringConvertible {
        case stopped
        case running(port: UInt16)
        case failed(String)

        public var description: String {
            switch self {
            case .stopped: return "stopped"
            case .running(let port): return "running(\(port))"
            case .failed(let msg): return "failed(\(msg))"
            }
        }
    }

    public private(set) var state: State = .stopped

    /// The port the bridge is listening on (nil until started).
    public var bridgePort: UInt16? {
        if case .running(let port) = state { return port }
        return nil
    }

    // MARK: - Configuration

    /// The home connector's tailnet address (e.g. "100.64.0.1").
    private let homeConnectorHost: String

    /// The home connector's listening port.
    private let homeConnectorPort: UInt16

    /// Optional pre-specified bridge port.  When nil, the bridge allocates
    /// an ephemeral port (port 0).
    private let requestedBridgePort: UInt16?

    /// Dispatch queue for the listener and connections.
    private let queue: DispatchQueue

    /// The underlying network listener.
    private var listener: NWListener?

    /// The per-device pairing credential to inject on forwarded requests.
    ///
    /// Set after a successful pairing handshake (A2.3).  When non-nil,
    /// the bridge adds `X-HappyRanch-Device-Credential: <credential>` to
    /// every forwarded request so the home connector can authorize the device.
    /// When nil (default), the home connector will reject all non-pairing requests.
    ///
    /// Thread-safe: this is set from outside the bridge, read on the bridge's
    /// internal dispatch queue.
    private let credentialLock = NSLock()
    private var storedDeviceCredential: String?
    public var deviceCredential: String? {
        get { credentialLock.withLock { storedDeviceCredential } }
        set { credentialLock.withLock { storedDeviceCredential = newValue } }
    }

    /// Explicit selection; DIY preserves the existing system-Tailscale path.
    public enum Lane: Sendable, Equatable { case diy, managed }
    public let lane: Lane

    // One native engine per process. The queue settles Up/Close before reuse;
    // the lock fences cancellation, owner admission and late credential results.
    private static let tsnetLock = NSLock()
    private static let engineQueue = DispatchQueue(label: "com.happyranch.managed-engine")
    nonisolated(unsafe) private static var managedOwner: UUID?
    private var managedAttempt: UUID?
    private var nativeConfigured = false
    private var managedReady = false
    private var managedPaired = false
    private let managedDials = DispatchGroup()
    private var managedPairing: RedeemCompleter?
    private var managedHandles: Set<Int32> = []
    private var clients: [ObjectIdentifier: NWConnection] = [:] // listener queue only

    /// Takes custody immediately, even on validation/busy/cancellation failure.
    /// PR2 must delete the Keychain item BEFORE invoking this API. No String key.
    public func joinManagedNetwork(
        key: ManagedEnrollmentKey, controlURL: String, hostname: String, stateDirectory: String
    ) async throws {
        defer { key.discard() }
        guard lane == .managed else { throw ManagedNetworkError.invalidConfiguration }
        let attempt = UUID()
        try Self.tsnetLock.withLock {
            guard Self.managedOwner == nil else { throw ManagedNetworkError.busy }
            Self.managedOwner = attempt
            managedAttempt = attempt
            nativeConfigured = false
            managedReady = false
            managedPaired = false
            deviceCredential = nil
        }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                Self.engineQueue.async {
                    var result: Int32 = -12
                    do {
                        // Init configures only. Holding this lock closes cancel-before-init.
                        result = try Self.tsnetLock.withLock {
                            guard self.managedAttempt == attempt else { return -12 }
                            guard Self.validManagedConfiguration(controlURL, hostname, stateDirectory) else { return -10 }
                            let configured = try key.consume { auth in
                                controlURL.withCString { control in
                                    hostname.withCString { name in
                                        stateDirectory.withCString { directory in
                                            tsnet_init_managed(auth, control, name, directory)
                                        }
                                    }
                                }
                            }
                            self.nativeConfigured = configured == 0
                            return configured
                        }
                    } catch { result = -10 }
                    // Wipe also when validation failed before consume, before any Up.
                    key.discard()
                    if result == 0 { result = tsnet_up_managed(120_000) }
                    let accepted = Self.tsnetLock.withLock {
                        if result == 0 && self.managedAttempt == attempt {
                            self.managedReady = true
                            return true
                        }
                        return false
                    }
                    if accepted {
                        continuation.resume()
                    } else {
                        if Self.tsnetLock.withLock({ self.nativeConfigured }) { tsnet_close() }
                        Self.tsnetLock.withLock {
                            self.nativeConfigured = false
                            if Self.managedOwner == attempt { Self.managedOwner = nil }
                            if self.managedAttempt == attempt { self.managedAttempt = nil }
                        }
                        continuation.resume(throwing: result == 0 ? ManagedNetworkError.canceled : ManagedNetworkError.result(result))
                    }
                }
            }
        } onCancel: {
            self.cancelManagedNetwork()
        }
    }

    /// Explicit saved-membership check. Never supplies/replays an enrollment key.
    public func resumeManagedNetwork(controlURL: String, hostname: String, stateDirectory: String) async throws {
        guard let allocation = calloc(1, 1) else { throw ManagedNetworkError.unavailable }
        let empty = ManagedEnrollmentKey(taking: allocation, byteCount: 1)
        try await joinManagedNetwork(key: empty, controlURL: controlURL, hostname: hostname, stateDirectory: stateDirectory)
    }

    /// Closes traffic/pin and discards pairing authority, retaining saved membership.
    /// A new target uses a new bridge, empty-key reopen, and explicit fresh pairing.
    public func cancelManagedNetwork() {
        guard lane == .managed else { return }
        let previous: UUID? = Self.tsnetLock.withLock {
            let previous = managedAttempt
            managedAttempt = nil
            managedReady = false
            managedPaired = false
            deviceCredential = nil
            managedHandles.removeAll()
            // Owner stays reserved until the serial worker has settled Close.
            if previous != nil && Self.managedOwner == previous && nativeConfigured { tsnet_cancel() }
            return previous
        }
        queue.async {
            self.clients.values.forEach { $0.cancel() }
            self.clients.removeAll()
            self.listener?.cancel()
            self.listener = nil
            self.state = .stopped
        }
        let pairing = Self.tsnetLock.withLock { managedPairing }
        pairing?.finish(with: .failure(RedeemPairingError.managed(.canceled)))
        guard let previous else { return }
        Self.engineQueue.async {
            guard Self.tsnetLock.withLock({ Self.managedOwner == previous }) else { return }
            if Self.tsnetLock.withLock({ self.nativeConfigured }) { tsnet_close() }
            self.managedDials.wait()
            Self.tsnetLock.withLock {
                self.nativeConfigured = false
                Self.managedOwner = nil
            }
        }
    }

    private static func validManagedConfiguration(_ control: String, _ hostname: String, _ directory: String) -> Bool {
        guard [control, hostname, directory].allSatisfy({ !$0.isEmpty && !$0.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) }),
              let url = URLComponents(string: control), url.scheme == "https", url.host?.isEmpty == false,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.port == nil || (1...65535).contains(url.port!), directory.hasPrefix("/") else { return false }
        return true
    }

    private func managedDial(timeoutMs: Int32, expectedAttempt: UUID) -> Int32 {
        let attempt: UUID? = Self.tsnetLock.withLock {
            guard managedReady, let attempt = managedAttempt, attempt == expectedAttempt, Self.managedOwner == attempt else { return nil }
            managedDials.enter()
            return attempt
        }
        guard let attempt else { return -15 }
        defer { managedDials.leave() }
        let handle = homeConnectorHost.withCString { tsnet_conn_dial_managed($0, Int32(homeConnectorPort), timeoutMs) }
        if handle >= 0 {
            let accepted = Self.tsnetLock.withLock {
                guard managedReady && managedAttempt == attempt && Self.managedOwner == attempt else { return false }
                managedHandles.insert(handle)
                return true
            }
            if !accepted { tsnet_conn_close(handle); return -12 }
        }
        return handle
    }

    private func closeManagedHandle(_ handle: Int32) {
        Self.tsnetLock.withLock { _ = managedHandles.remove(handle) }
        tsnet_conn_close(handle)
    }

    private static func writeAll(_ data: Data, handle: Int32) -> Int32 {
        data.withUnsafeBytes { bytes in
            guard let base = bytes.baseAddress else { return 0 }
            var offset = 0
            while offset < bytes.count {
                let n = tsnet_conn_write(handle, base.advanced(by: offset).assumingMemoryBound(to: CChar.self), Int32(min(bytes.count - offset, 65536)))
                guard n > 0 else { return n == 0 ? -14 : n }
                offset += Int(n)
            }
            return 0
        }
    }

    /// The session-scoped credential prefix.
    private static let sessionCredentialPrefix = "hr_session_"

    /// TCP parameters that bypass the system proxy.
    ///
    /// NWConnection honours system-level SOCKS/HTTP proxies by default.
    /// The home connector lives on the tailnet (100.x.x.x) and must be
    /// reached directly — routing through a local proxy causes hangs or
    /// timeouts when the proxy doesn't relay the response correctly.
    private static var directTCP: NWParameters {
        let tcp = NWProtocolTCP.Options()
        let params = NWParameters(tls: nil, tcp: tcp)
        params.preferNoProxies = true
        return params
    }

    // MARK: - Init

    /// - Parameters:
    ///   - homeConnectorHost: Home connector tailnet IP (e.g. from TailscaleStatusProvider).
    ///   - homeConnectorPort: Home connector listening port.
    ///   - bridgePort: Local loopback port for the bridge listener.  If nil,
    ///     the bridge allocates an ephemeral port (pass 0 to NWListener).
    ///   - queue: Dispatch queue (default: new serial queue).
    public init(
        homeConnectorHost: String,
        homeConnectorPort: UInt16,
        bridgePort: UInt16? = nil,
        lane: Lane = .diy,
        queue: DispatchQueue = DispatchQueue(label: "com.happyranch.client-bridge")
    ) {
        self.lane = lane
        self.homeConnectorHost = homeConnectorHost
        self.homeConnectorPort = homeConnectorPort
        self.requestedBridgePort = bridgePort
        self.queue = queue
        self.deviceCredential = nil
    }

    // MARK: - Lifecycle

    /// Start the bridge listener on `127.0.0.1:<bridgePort>`.
    ///
    /// - Throws: ``ClientBridgeError`` if the listener cannot be created
    ///   or if the bridge is already running.
    public func start() throws {
        guard case .stopped = state else {
            throw ClientBridgeError.alreadyRunning
        }

        let actualPort = requestedBridgePort ?? 0

        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host("127.0.0.1"),
            port: NWEndpoint.Port(integerLiteral: actualPort)
        )

        let listener: NWListener
        do {
            listener = try NWListener(using: parameters)
        } catch {
            state = .failed(error.localizedDescription)
            throw ClientBridgeError.listenerCreationFailed(underlying: error.localizedDescription)
        }

        listener.stateUpdateHandler = { [weak self] newState in
            guard let self else { return }
            self.queue.async {
                self.handleListenerState(newState)
            }
        }

        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { return }
            self.queue.async {
                self.handleNewConnection(connection)
            }
        }

        listener.start(queue: queue)
        self.listener = listener
    }

    /// Stop listening and drop all active connections.
    public func stop() {
        cancelManagedNetwork()
        queue.sync {
            clients.values.forEach { $0.cancel() }
            clients.removeAll()
            listener?.cancel()
            listener = nil
            state = .stopped
        }
    }

    /// Authenticated runtime health via this bridge's local loopback listener.
    /// No redirects/proxies: a remote Location must never escape the selected lane.
    public func checkRuntimeHealth() async -> Bool {
        guard deviceCredential?.isEmpty == false, let port = bridgePort,
              let url = URL(string: "http://127.0.0.1:\(port)/api/v1/health") else { return false }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.connectionProxyDictionary = [:]
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 5
        let session = URLSession(configuration: configuration, delegate: BridgeHealthDelegate(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
            let (_, response) = try await session.bytes(from: url)
            guard let response = response as? HTTPURLResponse else { return false }
            return (200...299).contains(response.statusCode)
        } catch { return false }
    }

    // MARK: - Pairing redeem (GAP #1)

    /// Redeem a one-time pairing code with the home connector.
    ///
    /// Opens a direct TCP connection to the home connector on the tailnet,
    /// sends `POST /pair` with the one-time code as the body, and parses
    /// the response.
    ///
    /// On a 200 response containing `{"credential":"hrpair_..."}`,
    /// sets ``deviceCredential`` and returns normally.  The caller should
    /// then set `webViewURL` to `http://127.0.0.1:<bridgePort>/` so the
    /// unmodified SPA loads through the bridge loopback.
    ///
    /// - Parameters:
    ///   - code: The one-time pairing code from the home-side UI.
    ///   - homeHost: The home connector's tailnet address (e.g. "100.64.0.1").
    ///   - homePort: The home connector's listening port.
    ///   - timeout: Maximum time to wait for the response (default: 30s).
    /// - Throws: ``RedeemPairingError/refused`` if the home connector returns
    ///   403 (invalid/expired code), ``RedeemPairingError/connectionFailed``
    ///   if the home connector is unreachable, or
    ///   ``RedeemPairingError/invalidResponse`` if the response is unparseable.
    public func redeemPairing(
        code: String,
        homeHost: String,
        homePort: UInt16,
        timeout: TimeInterval = 30
    ) async throws {
        if lane == .managed {
            try await redeemPairingManaged(code: code, homeHost: homeHost, homePort: homePort, timeout: timeout)
            return
        }
        DiagnosticsCollector.shared?.recordConnectPathLog(
            stage: "redeemPairing-start",
            message: "Attempting to redeem pairing with \(homeHost):\(homePort), timeout=\(timeout)s (code length=\(code.count))"
        )

        let completer = RedeemCompleter()
        let credential: String = try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<String, Error>) in
            let redeemQueue = DispatchQueue(label: "com.happyranch.redeem-\(UUID().uuidString)")

            let endpoint = NWEndpoint.hostPort(
                host: NWEndpoint.Host(homeHost),
                port: NWEndpoint.Port(integerLiteral: homePort)
            )
            let connection = NWConnection(to: endpoint, using: Self.directTCP)

            DiagnosticsCollector.shared?.recordConnectPathLog(
                stage: "redeemPairing-connection-created",
                message: "NWConnection created to \(homeHost):\(homePort)"
            )

            completer.configure(continuation: continuation, connection: connection)

            // Timeout guard (30s to avoid CI flakiness — MEM-118)
            redeemQueue.asyncAfter(deadline: .now() + timeout) {
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "redeemPairing-timeout-fired",
                    message: "Timeout boundary reached after \(timeout)s — no response received from home connector"
                )
                completer.finish(with: .failure(
                    RedeemPairingError.connectionFailed("Request timed out")
                ))
            }

            connection.stateUpdateHandler = { [completer] state in
                switch state {
                case .waiting(let error):
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "redeemPairing-connection-waiting",
                        message: "NWConnection waiting: \(error.localizedDescription)"
                    )
                case .preparing:
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "redeemPairing-connection-preparing",
                        message: "NWConnection preparing"
                    )
                case .ready:
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "redeemPairing-connection-ready",
                        message: "NWConnection ready — sending POST /pair (code NOT logged)"
                    )
                    let requestBody = code
                    let request = """
                        POST /pair HTTP/1.1\r
                        Host: \(homeHost):\(homePort)\r
                        Content-Type: text/plain\r
                        Content-Length: \(requestBody.utf8.count)\r
                        Connection: close\r
                        \r
                        \(requestBody)
                        """
                    connection.send(
                        content: request.data(using: .utf8),
                        completion: .contentProcessed { _ in
                            DiagnosticsCollector.shared?.recordConnectPathLog(
                                stage: "redeemPairing-request-sent",
                                message: "POST /pair request sent, waiting for response"
                            )
                            connection.receive(
                                minimumIncompleteLength: 1,
                                maximumLength: 65536
                            ) { data, _, _, error in
                                if let error = error {
                                    DiagnosticsCollector.shared?.recordConnectPathLog(
                                        stage: "redeemPairing-receive-error",
                                        message: "Receive error: \(error.localizedDescription)"
                                    )
                                    completer.finish(with: .failure(
                                        RedeemPairingError.connectionFailed(error.localizedDescription)
                                    ))
                                    return
                                }
                                guard let data = data,
                                      let response = String(data: data, encoding: .utf8) else {
                                    DiagnosticsCollector.shared?.recordConnectPathLog(
                                        stage: "redeemPairing-response-empty",
                                        message: "Response data is nil or not UTF-8 decodable"
                                    )
                                    completer.finish(with: .failure(RedeemPairingError.invalidResponse))
                                    return
                                }

                                // Log status line from response (NO body — avoid credential leak)
                                let statusLine = response.components(separatedBy: "\r\n").first ?? "(unknown)"
                                DiagnosticsCollector.shared?.recordConnectPathLog(
                                    stage: "redeemPairing-response-received",
                                    message: "Response received: \(statusLine) (body NOT logged — may contain credential)"
                                )

                                Self.parseRedeemResponse(response, completer: completer)
                            }
                        }
                    )
                case .failed(let error):
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "redeemPairing-connection-failed",
                        message: "NWConnection failed: \(error.localizedDescription)"
                    )
                    completer.finish(with: .failure(
                        RedeemPairingError.connectionFailed(error.localizedDescription)
                    ))
                case .cancelled:
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "redeemPairing-connection-cancelled",
                        message: "NWConnection cancelled"
                    )
                    completer.finish(with: .failure(
                        RedeemPairingError.connectionFailed("Connection cancelled")
                    ))
                default:
                    break
                }
            }
            connection.start(queue: redeemQueue)
            DiagnosticsCollector.shared?.recordConnectPathLog(
                stage: "redeemPairing-connection-started",
                message: "NWConnection.start() called on redeem queue"
            )
        }

        DiagnosticsCollector.shared?.recordConnectPathLog(
            stage: "redeemPairing-success",
            message: "Pairing redeemed successfully (credential NOT logged)"
        )
        self.deviceCredential = credential
    }

    private func redeemPairingManaged(code: String, homeHost: String, homePort: UInt16, timeout: TimeInterval) async throws {
        // Configuration is immutable. A changed audience invalidates all authority.
        guard homeHost == homeConnectorHost && homePort == homeConnectorPort else {
            cancelManagedNetwork()
            throw RedeemPairingError.managed(.peerChanged)
        }
        let completer = RedeemCompleter()
        let attempt: UUID = try Self.tsnetLock.withLock {
            guard managedReady, let attempt = managedAttempt else { throw RedeemPairingError.managed(.unavailable) }
            guard managedPairing == nil, deviceCredential == nil else { throw RedeemPairingError.managed(.busy) }
            managedPairing = completer
            return attempt
        }
        let seconds = timeout.isFinite && timeout > 0 ? min(timeout, 30) : 30
        let deadline = DispatchTime.now() + seconds
        let credential: String = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String, Error>) in
                completer.configure(continuation: continuation)
                DispatchQueue.global().asyncAfter(deadline: deadline) {
                    completer.finish(with: .failure(RedeemPairingError.connectionFailed("Request timed out")))
                }
                DispatchQueue.global().async {
                    defer { Self.tsnetLock.withLock { self.managedPairing = nil } }
                    guard !completer.isCompleted else { return }
                    let remaining = max(1, Int32(min(30_000, Double(deadline.uptimeNanoseconds - min(deadline.uptimeNanoseconds, DispatchTime.now().uptimeNanoseconds)) / 1_000_000)))
                    let handle = self.managedDial(timeoutMs: remaining, expectedAttempt: attempt)
                    guard handle >= 0 else {
                        let error = ManagedNetworkError.result(handle)
                        completer.finish(with: .failure(RedeemPairingError.managedDialFailure(handle)))
                        if error == .peerChanged { self.cancelManagedNetwork() }
                        return
                    }
                    defer { self.closeManagedHandle(handle) }
                    guard completer.installManagedHandle(handle) else { return }
                    let request = "POST /pair HTTP/1.1\r\nHost: \(homeHost):\(homePort)\r\nContent-Type: text/plain\r\nContent-Length: \(code.utf8.count)\r\nConnection: close\r\n\r\n\(code)"
                    let written = Self.writeAll(Data(request.utf8), handle: handle)
                    guard written == 0 else {
                        completer.finish(with: .failure(RedeemPairingError.managedDialFailure(written)))
                        if written == -18 { self.cancelManagedNetwork() }
                        return
                    }
                    var response = Data()
                    var buffer = [CChar](repeating: 0, count: 8192)
                    while !completer.isCompleted {
                        let n = tsnet_conn_read(handle, &buffer, 8192)
                        guard n >= 0 else {
                            completer.finish(with: .failure(RedeemPairingError.connectionFailed("Unreachable")))
                            return
                        }
                        if n > 0 { response.append(buffer.withUnsafeBytes { Data($0.prefix(Int(n))) }) }
                        guard response.count <= 65536 else {
                            completer.finish(with: .failure(RedeemPairingError.invalidResponse)); return
                        }
                        do {
                            if let text = try Self.completePairingResponse(response, eof: n == 0) {
                                Self.parseRedeemResponse(text, completer: completer)
                                return
                            }
                        } catch {
                            completer.finish(with: .failure(error)); return
                        }
                    }
                }
            }
        } onCancel: {
            completer.finish(with: .failure(RedeemPairingError.managed(.canceled)))
        }
        try Self.tsnetLock.withLock {
            guard managedReady && managedAttempt == attempt else { throw RedeemPairingError.managed(.canceled) }
            deviceCredential = credential
            managedPaired = true
        }
    }

    /// Bounded fragmented HTTP response framing; absent length requires EOF.
    private static func completePairingResponse(_ data: Data, eof: Bool) throws -> String? {
        guard let boundary = data.range(of: Data("\r\n\r\n".utf8)) else {
            if eof { throw RedeemPairingError.invalidResponse }
            return nil
        }
        guard let header = String(data: data[..<boundary.lowerBound], encoding: .utf8) else { throw RedeemPairingError.invalidResponse }
        let fields = header.components(separatedBy: "\r\n").dropFirst()
        guard !fields.contains(where: { $0.lowercased().hasPrefix("transfer-encoding:") }) else { throw RedeemPairingError.invalidResponse }
        let lengths = fields.filter { $0.lowercased().hasPrefix("content-length:") }
        if !lengths.isEmpty {
            guard lengths.count == 1, let length = Int(lengths[0].dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)),
                  length >= 0, length <= 65536 - boundary.upperBound else { throw RedeemPairingError.invalidResponse }
            if data.count < boundary.upperBound + length {
                if eof { throw RedeemPairingError.invalidResponse }
                return nil
            }
            guard let text = String(data: data.prefix(boundary.upperBound + length), encoding: .utf8) else { throw RedeemPairingError.invalidResponse }
            return text
        }
        if !eof { return nil }
        guard let text = String(data: data, encoding: .utf8) else { throw RedeemPairingError.invalidResponse }
        return text
    }

    /// Parse the POST /pair response and resolve the completer.
    private static func parseRedeemResponse(
        _ response: String,
        completer: RedeemCompleter
    ) {
        let lines = response.components(separatedBy: "\r\n")
        guard let statusLine = lines.first else {
            completer.finish(with: .failure(RedeemPairingError.invalidResponse))
            return
        }
        let statusParts = statusLine.components(separatedBy: " ")
        guard statusParts.count >= 2,
              let statusCode = Int(statusParts[1]) else {
            completer.finish(with: .failure(RedeemPairingError.invalidResponse))
            return
        }

        if statusCode == 403 {
            completer.finish(with: .failure(RedeemPairingError.refused))
            return
        }

        guard statusCode == 200 else {
            completer.finish(with: .failure(
                RedeemPairingError.connectionFailed("Unexpected status \(statusCode)")
            ))
            return
        }

        // Parse body for credential
        guard let bodyRange = response.range(of: "\r\n\r\n") else {
            completer.finish(with: .failure(RedeemPairingError.invalidResponse))
            return
        }
        let bodyText = String(response[bodyRange.upperBound...])
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard let bodyData = bodyText.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: bodyData) as? [String: Any],
              let credential = json["credential"] as? String,
              !credential.isEmpty else {
            completer.finish(with: .failure(RedeemPairingError.invalidResponse))
            return
        }

        // Success — pass the credential back
        DiagnosticsCollector.shared?.recordConnectPathLog(
            stage: "redeemPairing-credential-extracted",
            message: "Parsed 200 response — credential extracted (value NOT logged)"
        )
        completer.finish(with: .success(credential))
    }

    // MARK: - Listener state

    private func handleListenerState(_ newState: NWListener.State) {
        switch newState {
        case .ready:
            if let port = listener?.port {
                state = .running(port: port.rawValue)
            } else if let port = requestedBridgePort, port != 0 {
                state = .running(port: port)
            } else {
                state = .failed("Listener ready but port unknown")
            }
        case .failed(let error):
            state = .failed(error.localizedDescription)
        case .cancelled:
            if case .running = state {
                state = .stopped
            }
        default:
            break
        }
    }

    // MARK: - Connection handling

    private func handleNewConnection(_ clientConnection: NWConnection) {
        clients[ObjectIdentifier(clientConnection)] = clientConnection
        clientConnection.stateUpdateHandler = { [weak self, weak clientConnection] state in
            guard let self, let clientConnection else { return }
            switch state {
            case .ready:
                self.receiveFromClient(clientConnection)
            case .failed, .cancelled:
                self.clients.removeValue(forKey: ObjectIdentifier(clientConnection))
                clientConnection.cancel()
            default:
                break
            }
        }
        clientConnection.start(queue: queue)
    }

    // MARK: - Request reading and routing

    /// Read the HTTP request from the SPA, route to bootstrap handler or forward.
    private func receiveFromClient(_ clientConnection: NWConnection) {
        clientConnection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [weak self] data, _, isComplete, error in

            guard let self else { return }

            if error != nil {
                clientConnection.cancel()
                return
            }

            guard let data, !data.isEmpty else {
                if isComplete { clientConnection.cancel() }
                return
            }

            guard let requestString = String(data: data, encoding: .utf8) else {
                self.sendErrorResponse(to: clientConnection, status: 400, message: "Bad Request")
                return
            }

            let lines = requestString.components(separatedBy: "\r\n")
            guard let requestLine = lines.first else {
                self.sendErrorResponse(to: clientConnection, status: 400, message: "Bad Request")
                return
            }

            let parts = requestLine.components(separatedBy: " ")
            guard parts.count >= 2 else {
                self.sendErrorResponse(to: clientConnection, status: 400, message: "Bad Request")
                return
            }

            let method = parts[0]
            let path = parts[1]

            // Normalize path: strip trailing slash AND /api/vN prefix.
            // The SPA bootstraps via /api/v1/auth/bootstrap (web/src/lib/auth.ts:10),
            // so the unprefixed-only check was a CRITICAL bypass (reviewer FINDING 1).
            var normalizedPath = path.hasSuffix("/") && path.count > 1
                ? String(path.dropLast())
                : path
            // Strip any /api/vN prefix so all path matching is prefix-agnostic.
            normalizedPath = DaemonPathNormalizer.stripApiPrefix(normalizedPath)

            // --- BOOTSTRAP INTERCEPTION ---
            // The bridge answers /auth/bootstrap (AND /api/v1/auth/bootstrap)
            // locally with a session-scoped credential — NEVER forwarding it
            // to the home connector.
            if normalizedPath == "/auth/bootstrap" && method == "GET" {
                self.handleAuthBootstrap(clientConnection: clientConnection)
                return
            }

            // --- FORWARD TO HOME CONNECTOR ---
            // Strip the SPA's Authorization header before forwarding
            let strippedRequest = self.stripAuthorizationHeader(from: requestString)
            self.forwardToHomeConnector(
                clientConnection: clientConnection,
                forwardedRequest: strippedRequest
            )
        }
    }

    // MARK: - /auth/bootstrap handler

    /// Answer `GET /auth/bootstrap` (and `GET /api/v1/auth/bootstrap`) with
    /// a session-scoped credential.
    ///
    /// The credential is a fresh random token with `hr_session_` prefix.
    /// It is NOT the raw daemon token — that token is home-only and NEVER
    /// leaves the home machine.  The SPA uses this session credential for
    /// subsequent requests, but the bridge STRIPS it before forwarding.
    ///
    /// Both prefixed and unprefixed forms are handled here; the path
    /// normalizer (DaemonPathNormalizer) strips the /api/v1 prefix before
    /// the comparison in receiveFromClient.
    private func handleAuthBootstrap(clientConnection: NWConnection) {
        let sessionToken = ClientBridge.generateSessionCredential()
        let body = """
            {"token":"\(sessionToken)","user":"founder","role":"admin"}
            """
        let response = """
            HTTP/1.1 200 OK\r
            Content-Type: application/json\r
            Content-Length: \(body.utf8.count)\r
            Connection: close\r
            \r
            \(body)
            """

        clientConnection.send(
            content: response.data(using: .utf8),
            contentContext: .finalMessage,
            isComplete: true,
            completion: .idempotent
        )
    }

    // MARK: - Header injection helpers

    /// Inject the `X-HappyRanch-Device-Credential` header into the request.
    ///
    /// Adds `X-HappyRanch-Device-Credential: <credential>\r\n` before the
    /// first `\r\n\r\n` (end of headers).
    private static func injectDeviceCredential(
        _ credential: String,
        into request: String
    ) -> String {
        let credHeader = "\r\nX-HappyRanch-Device-Credential: \(credential)"

        // Insert the credential header before the \r\n\r\n header/body
        // separator.  We insert at lowerBound (before the separator).
        // Swift treats \r\n as a single Character, so the old offsetBy:2
        // actually landed past the entire \r\n\r\n separator, placing the
        // header in the body.
        if let headerEndRange = request.range(of: "\r\n\r\n") {
            var modified = request
            modified.insert(contentsOf: credHeader, at: headerEndRange.lowerBound)
            return modified
        }

        // Fallback: append at end
        return request + "\r\n" + credHeader
    }

    // MARK: - Authorization header stripping

    /// Strip any `Authorization:` header from the request.
    ///
    /// The SPA sends its session-scoped credential via `Authorization: Bearer ...`.
    /// This credential must NEVER cross the tailnet — the home connector injects
    /// the real daemon token on its loopback hop.  Removing the header here
    /// ensures the SPA's credential never reaches the home machine.
    private func stripAuthorizationHeader(from request: String) -> String {
        let lines = request.components(separatedBy: "\r\n")
        let filteredLines = lines.filter { line in
            !line.lowercased().hasPrefix("authorization:")
        }
        // If nothing was filtered, return the original request unchanged.
        // The split/join cycle adds extra \r\n (from trailing empty strings
        // created by components(separatedBy:)), which corrupts the header/body
        // boundary and causes subsequent header injection (e.g.
        // injectDeviceCredential) to merge into the preceding header line.
        if filteredLines.count == lines.count {
            return request
        }
        return filteredLines.joined(separator: "\r\n")
    }

    // MARK: - Forward to home connector

    /// Open a connection to the home connector, forward the (stripped) request,
    /// and relay the response back to the client.
    private func forwardToHomeConnector(
        clientConnection: NWConnection,
        forwardedRequest: String
    ) {
        if lane == .managed {
            forwardToHomeConnectorViaTsnet(clientConnection: clientConnection, forwardedRequest: forwardedRequest)
            return
        }
        // Inject the device credential header if we have one
        let requestToSend: String
        if let credential = deviceCredential, !credential.isEmpty {
            requestToSend = Self.injectDeviceCredential(
                credential,
                into: forwardedRequest
            )
        } else {
            requestToSend = forwardedRequest
        }

        forwardToHomeConnectorViaNWConnection(
            clientConnection: clientConnection,
            requestToSend: requestToSend
        )
    }

    /// Forward the request to the home connector via the existing Tailscale/
    /// NWConnection path (DEFAULT).
    private func forwardToHomeConnectorViaNWConnection(
        clientConnection: NWConnection,
        requestToSend: String
    ) {
        let homeEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host(homeConnectorHost),
            port: NWEndpoint.Port(integerLiteral: homeConnectorPort)
        )
        let homeConnection = NWConnection(to: homeEndpoint, using: Self.directTCP)

        homeConnection.stateUpdateHandler = { [weak self, weak clientConnection] state in
            guard let self, let clientConnection else { return }
            switch state {
            case .ready:
                let requestData = requestToSend.data(using: .utf8) ?? Data()
                homeConnection.send(
                    content: requestData,
                    completion: .contentProcessed { [weak self] _ in
                        self?.relayHomeResponse(
                            home: homeConnection,
                            client: clientConnection
                        )
                    }
                )
            case .failed:
                // Home connector unreachable — send 502 to SPA
                self.sendErrorResponse(
                    to: clientConnection,
                    status: 502,
                    message: "Bad Gateway — home connector unreachable"
                )
                homeConnection.cancel()
            case .cancelled:
                clientConnection.cancel()
            default:
                break
            }
        }
        homeConnection.start(queue: queue)
    }

    /// Every managed HTTP/SSE/upgrade/probe uses the peer-only native boundary.
    private func forwardToHomeConnectorViaTsnet(clientConnection: NWConnection, forwardedRequest: String) {
        let authority: (UUID, String)? = Self.tsnetLock.withLock {
            guard managedReady, managedPaired, let attempt = managedAttempt, let credential = deviceCredential else { return nil }
            return (attempt, credential)
        }
        guard let (attempt, credential) = authority else {
            sendErrorResponse(to: clientConnection, status: 502, message: "Pair with the managed home runtime first")
            return
        }
        let requestToSend = Self.injectDeviceCredential(credential, into: forwardedRequest)
        DispatchQueue.global().async {
            let handle = self.managedDial(timeoutMs: 30_000, expectedAttempt: attempt)
            guard handle >= 0 else {
                let error = ManagedNetworkError.result(handle)
                if error == .peerChanged { self.cancelManagedNetwork() }
                self.sendErrorResponse(to: clientConnection, status: 502, message: RedeemPairingError.managedDialFailure(handle).localizedDescription)
                return
            }
            let written = Self.writeAll(Data(requestToSend.utf8), handle: handle)
            guard written == 0 else {
                self.closeManagedHandle(handle)
                if written == -18 { self.cancelManagedNetwork() }
                self.sendErrorResponse(to: clientConnection, status: 502, message: RedeemPairingError.managedDialFailure(written).localizedDescription)
                return
            }
            let relayQueue = DispatchQueue(label: "com.happyranch.managed-relay")
            // Duplex continuation is needed for upgrades and streaming request bodies.
            self.relayClientToTsnet(client: clientConnection, handle: handle)
            self.relayTsnetToClient(connID: handle, client: clientConnection, queue: relayQueue)
        }
    }

    private func relayClientToTsnet(client: NWConnection, handle: Int32) {
        client.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [self] data, _, complete, error in
            DispatchQueue.global().async {
                guard error == nil else { self.closeManagedHandle(handle); return }
                if let data, !data.isEmpty {
                    let result = Self.writeAll(data, handle: handle)
                    guard result == 0 else {
                        self.closeManagedHandle(handle)
                        if result == -18 { self.cancelManagedNetwork() }
                        client.cancel()
                        return
                    }
                }
                if !complete { self.relayClientToTsnet(client: client, handle: handle) }
            }
        }
    }

    /// Read from tsnet connection and write to the client NWConnection.
    /// Runs on a dedicated queue because tsnet_conn_read is a blocking call.
    private func relayTsnetToClient(
        connID: Int32,
        client: NWConnection?,
        queue: DispatchQueue
    ) {
        let bufSize = 65536
        let buf = UnsafeMutablePointer<CChar>.allocate(capacity: bufSize)
        defer { buf.deallocate() }

        let n = tsnet_conn_read(connID, buf, Int32(bufSize))

        if n > 0 {
            let data = Data(bytes: buf, count: Int(n))
            guard let client else { closeManagedHandle(connID); return }
            client.send(
                content: data,
                completion: .contentProcessed { [self] error in
                    guard error == nil else { closeManagedHandle(connID); client.cancel(); return }
                    queue.async {
                        self.relayTsnetToClient(
                            connID: connID,
                            client: client,
                            queue: queue
                        )
                    }
                }
            )
        } else {
            // n == 0: clean close by peer; n < 0: error
            client?.cancel()
            closeManagedHandle(connID)
        }
    }

    /// Relay home connector response chunks to the SPA until the home
    /// connector closes the connection.
    private func relayHomeResponse(
        home: NWConnection,
        client: NWConnection
    ) {
        home.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [weak self] data, _, isComplete, error in

            guard let self else { return }

            if error != nil {
                client.cancel()
                return
            }

            if let data, !data.isEmpty {
                client.send(
                    content: data,
                    completion: .contentProcessed { _ in
                        if isComplete {
                            client.cancel()
                        } else {
                            self.relayHomeResponse(home: home, client: client)
                        }
                    }
                )
            } else if isComplete {
                client.cancel()
            } else {
                self.relayHomeResponse(home: home, client: client)
            }
        }
    }

    // MARK: - Error responses

    /// Send an HTTP error response and close the connection.
    private func sendErrorResponse(
        to connection: NWConnection,
        status: Int,
        message: String
    ) {
        let statusText: String = {
            switch status {
            case 400: return "Bad Request"
            case 403: return "Forbidden"
            case 500: return "Internal Server Error"
            case 502: return "Bad Gateway"
            default: return "Error"
            }
        }()

        let body = "{\"error\":\"\(message)\"}"
        let response = """
            HTTP/1.1 \(status) \(statusText)\r
            Content-Type: application/json\r
            Content-Length: \(body.utf8.count)\r
            Connection: close\r
            \r
            \(body)
            """

        connection.send(
            content: response.data(using: .utf8),
            contentContext: .finalMessage,
            isComplete: true,
            completion: .idempotent
        )
    }

    // MARK: - Session credential generation

    /// Generate a fresh session-scoped credential.
    ///
    /// Uses `hr_session_` prefix so it's visually and programmatically
    /// distinguishable from `hr_token_` (the real daemon token) and
    /// `hrreg_` (scoped registration tokens in the daemon).
    ///
    /// Each call produces a new random token — no caching, no reuse.
    static func generateSessionCredential() -> String {
        let randomBytes = (0..<16).map { _ in UInt8.random(in: 0...255) }
        let hex = randomBytes.map { String(format: "%02x", $0) }.joined()
        return "\(sessionCredentialPrefix)\(hex)"
    }
}

// MARK: - ClientBridgeError

public enum ClientBridgeError: Error, Equatable {
    case alreadyRunning
    case listenerCreationFailed(underlying: String)
}

// MARK: - RedeemPairingCompleter (GAP #1)

/// Thread-safe, single-fire completion wrapper for the redeem-POST-handshake.
///
/// The redeem flow opens ONE NWConnection, sends POST /pair, reads ONE
/// response, and resolves exactly once.  This class prevents double-resume
/// races (timeout vs response vs .cancelled) without mutable captures in
/// Swift 6 Sendable closures.
private final class RedeemCompleter: @unchecked Sendable {

    private let lock = NSLock()
    private var continuation: CheckedContinuation<String, Error>?
    private var connection: NWConnection?
    private var completed = false
    private var managedHandle: Int32?

    var isCompleted: Bool { lock.withLock { completed } }

    func installManagedHandle(_ handle: Int32) -> Bool {
        lock.withLock {
            guard !completed else { tsnet_conn_close(handle); return false }
            managedHandle = handle
            return true
        }
    }

    func configure(
        continuation: CheckedContinuation<String, Error>,
        connection: NWConnection? = nil
    ) {
        lock.lock()
        guard !completed else {
            lock.unlock()
            connection?.cancel()
            continuation.resume(throwing: RedeemPairingError.managed(.canceled))
            return
        }
        self.continuation = continuation
        self.connection = connection
        lock.unlock()
    }

    func finish(with result: Result<String, Error>) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        completed = true
        let cont = continuation
        let conn = connection
        let handle = managedHandle
        managedHandle = nil
        continuation = nil
        connection = nil
        lock.unlock()

        conn?.cancel()
        if let handle { tsnet_conn_close(handle) }

        guard let cont = cont else { return }
        switch result {
        case .success(let credential):
            cont.resume(returning: credential)
        case .failure(let error):
            cont.resume(throwing: error)
        }
    }
}


/// Health stays on the selected bridge even when a peer responds with Location.
private final class BridgeHealthDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
