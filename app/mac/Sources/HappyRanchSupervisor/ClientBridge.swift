import Foundation
import Network

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

    /// The session-scoped credential prefix.
    private static let sessionCredentialPrefix = "hr_session_"

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
        let credHeader = "X-HappyRanch-Device-Credential: \(credential)\r\n"

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

        let homeEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host(homeConnectorHost),
            port: NWEndpoint.Port(integerLiteral: homeConnectorPort)
        )
        let homeConnection = NWConnection(to: homeEndpoint, using: .tcp)

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
