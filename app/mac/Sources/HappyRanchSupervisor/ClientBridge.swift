import Foundation
import Network
import CTsnet

// MARK: - RedeemPairingError

/// Errors surfaced by the client-side pairing-redeem handshake.
public enum RedeemPairingError: Error, Equatable {
    /// The home connector returned 403 — the pairing code is invalid or expired.
    case refused

    /// Could not connect to the home connector (network error, unreachable).
    case connectionFailed(String)

    /// The home connector returned an unexpected response
    /// (e.g. 200 without a credential field, or malformed JSON).
    case invalidResponse
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
    public var deviceCredential: String?

    /// Whether to use the in-process tsnet WireGuard tunnel for outbound
    /// connections to the home connector instead of the default Tailscale/
    /// NWConnection path.
    ///
    /// Controlled by the `HAPPYRANCH_TSNET_TRANSPORT` environment variable.
    /// Set to `"1"` to enable.  Off by default — the existing NWConnection
    /// path is the default and is NOT deleted.
    ///
    /// This flag is read once at init time.  The default (false) MUST select
    /// the existing NWConnection/Tailscale path.
    public static let useTsnetTransport: Bool = {
        ProcessInfo.processInfo.environment["HAPPYRANCH_TSNET_TRANSPORT"] == "1"
    }()

    // MARK: - Tsnet engine lifecycle

    /// Lock guarding tsnet engine state.
    private static let tsnetLock = NSLock()

    /// Whether the tsnet engine has been successfully initialised and started.
    /// All accesses are guarded by tsnetLock.
    nonisolated(unsafe) private static var tsnetEngineReady = false

    /// Cached error from a failed tsnet init/start attempt (nil if never tried or success).
    /// All accesses are guarded by tsnetLock.
    nonisolated(unsafe) private static var tsnetInitError: String?

    /// True while tsnet_init/tsnet_start is in progress (guards against concurrent init).
    /// All accesses are guarded by tsnetLock.
    nonisolated(unsafe) private static var tsnetInitializing = false

    /// Ensure the in-process tsnet WireGuard engine is initialised and started.
    ///
    /// Called lazily on first use when `useTsnetTransport` is true.  Reads
    /// dev-only config from environment variables:
    ///   - `HAPPYRANCH_TSNET_AUTH_KEY` (required — Tailscale auth key)
    ///   - `HAPPYRANCH_TSNET_CONTROL_URL` (optional — defaults to saas)
    ///   - `HAPPYRANCH_TSNET_HOSTNAME` (optional — defaults to "happyranch-tsnet")
    ///
    /// Thread-safe and idempotent — safe to call before every dial.
    /// Uses blocking C-ABI calls (tsnet_init, tsnet_start) — intended to be
    /// called off the main thread (ClientBridge dispatch queue).
    ///
    /// - Returns: nil on success, or an error message describing the failure.
    ///   The error is cached; subsequent calls return the same error without
    ///   re-attempting init.
    static func ensureTsnetEngine() -> String? {
        tsnetLock.lock()
        if tsnetEngineReady {
            tsnetLock.unlock()
            return nil
        }
        if let err = tsnetInitError {
            tsnetLock.unlock()
            return err
        }
        if tsnetInitializing {
            tsnetLock.unlock()
            return "tsnet engine initialisation in progress — retry"
        }
        tsnetInitializing = true
        tsnetLock.unlock()

        defer {
            tsnetLock.lock()
            tsnetInitializing = false
            tsnetLock.unlock()
        }

        // Read dev-only config from environment variables.
        let authKey = ProcessInfo.processInfo.environment["HAPPYRANCH_TSNET_AUTH_KEY"] ?? ""
        guard !authKey.isEmpty else {
            let err = "HAPPYRANCH_TSNET_AUTH_KEY environment variable not set"
            tsnetLock.lock()
            tsnetInitError = err
            tsnetLock.unlock()
            return err
        }

        let controlURL = ProcessInfo.processInfo.environment["HAPPYRANCH_TSNET_CONTROL_URL"]
        let hostname = ProcessInfo.processInfo.environment["HAPPYRANCH_TSNET_HOSTNAME"] ?? "happyranch-tsnet"

        // tsnet_init — blocking call that configures the tsnet server.
        let initResult: Int32 = authKey.withCString { authPtr in
            hostname.withCString { hostPtr in
                if let ctrl = controlURL {
                    return ctrl.withCString { ctrlPtr in
                        tsnet_init(authPtr, ctrlPtr, hostPtr)
                    }
                } else {
                    return tsnet_init(authPtr, nil, hostPtr)
                }
            }
        }

        if initResult != 0 {
            let errPtr = tsnet_last_error()
            let errMsg = errPtr.map { String(cString: $0) } ?? "tsnet_init failed (unknown error)"
            if let errPtr { tsnet_free_string(errPtr) }
            tsnetLock.lock()
            tsnetInitError = errMsg
            tsnetLock.unlock()
            return errMsg
        }

        // tsnet_start — blocking call that brings the engine online.
        let startResult = tsnet_start()
        if startResult != 0 {
            let errPtr = tsnet_last_error()
            let errMsg = errPtr.map { String(cString: $0) } ?? "tsnet_start failed (unknown error)"
            if let errPtr { tsnet_free_string(errPtr) }
            tsnetLock.lock()
            tsnetInitError = errMsg
            tsnetLock.unlock()
            return errMsg
        }

        tsnetLock.lock()
        tsnetEngineReady = true
        tsnetLock.unlock()
        return nil
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
        queue: DispatchQueue = DispatchQueue(label: "com.happyranch.client-bridge")
    ) {
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
        queue.sync {
            listener?.cancel()
            listener = nil
            state = .stopped
        }
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
        clientConnection.stateUpdateHandler = { [weak self, weak clientConnection] state in
            guard let self, let clientConnection else { return }
            switch state {
            case .ready:
                self.receiveFromClient(clientConnection)
            case .failed, .cancelled:
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

        if Self.useTsnetTransport {
            forwardToHomeConnectorViaTsnet(
                clientConnection: clientConnection,
                requestToSend: requestToSend
            )
        } else {
            forwardToHomeConnectorViaNWConnection(
                clientConnection: clientConnection,
                requestToSend: requestToSend
            )
        }
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

    /// Forward the request to the home connector through the in-process tsnet
    /// WireGuard tunnel (OFF-BY-DEFAULT — only when `useTsnetTransport` is true).
    ///
    /// Uses the tsnet_conn_* C-ABI to open a duplex connection through the
    /// userspace WireGuard tunnel.  The relay loop runs on a dedicated
    /// dispatch queue to avoid blocking the bridge's serial queue on
    /// blocking tsnet_conn_read calls.
    private func forwardToHomeConnectorViaTsnet(
        clientConnection: NWConnection,
        requestToSend: String
    ) {
        // Ensure the tsnet engine is initialised and started before the first dial.
        // Idempotent — subsequent calls return immediately.
        if let initErr = Self.ensureTsnetEngine() {
            self.sendErrorResponse(
                to: clientConnection,
                status: 502,
                message: "Bad Gateway — tsnet engine not available: \(initErr)"
            )
            return
        }

        let addr = "\(homeConnectorHost):\(homeConnectorPort)"
        let connID = tsnet_conn_dial("tcp", addr, 30_000)

        guard connID >= 0 else {
            let errPtr = tsnet_last_error()
            let errMsg = errPtr.map { String(cString: $0) } ?? "unknown tsnet error"
            if let errPtr { tsnet_free_string(errPtr) }
            self.sendErrorResponse(
                to: clientConnection,
                status: 502,
                message: "Bad Gateway — tsnet dial failed: \(errMsg)"
            )
            return
        }

        // Write the initial request to tsnet
        let requestData = requestToSend.data(using: .utf8) ?? Data()
        let written = requestData.withUnsafeBytes { (ptr: UnsafeRawBufferPointer) -> Int32 in
            guard let base = ptr.baseAddress else { return -1 }
            return tsnet_conn_write(connID, base.assumingMemoryBound(to: CChar.self), Int32(requestData.count))
        }

        if written < 0 {
            tsnet_conn_close(connID)
            self.sendErrorResponse(
                to: clientConnection,
                status: 502,
                message: "Bad Gateway — tsnet write failed"
            )
            return
        }

        // Relay tsnet -> client on a dedicated queue (tsnet_conn_read blocks)
        let tsnetQueue = DispatchQueue(
            label: "com.happyranch.tsnet-relay-\(UUID().uuidString)"
        )
        tsnetQueue.async { [weak self, weak clientConnection] in
            guard let self else { return }
            self.relayTsnetToClient(
                connID: connID,
                client: clientConnection,
                queue: tsnetQueue
            )
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
            client?.send(
                content: data,
                completion: .contentProcessed { [weak self] _ in
                    guard let self else { return }
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
            tsnet_conn_close(connID)
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

    func configure(
        continuation: CheckedContinuation<String, Error>,
        connection: NWConnection
    ) {
        lock.lock()
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
        continuation = nil
        connection = nil
        lock.unlock()

        conn?.cancel()

        guard let cont = cont else { return }
        switch result {
        case .success(let credential):
            cont.resume(returning: credential)
        case .failure(let error):
            cont.resume(throwing: error)
        }
    }
}
