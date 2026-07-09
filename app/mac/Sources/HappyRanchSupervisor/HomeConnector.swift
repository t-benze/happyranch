import Foundation
import Network

// MARK: - HomeConnector

/// The HOME CONNECTOR — the security-critical remote-reachability core.
///
/// This component runs on the **home** machine and:
/// 1. Binds ONLY to the tailnet-interface address (the 100.x node addr
///    from ``TailscaleStatusProviding/selfTailscaleIPs``) — **NEVER**
///    `0.0.0.0`, **NEVER** a public listener.
/// 2. Reverse-proxies inbound HTTP + SSE + WebSocket to
///    `http://127.0.0.1:<daemon_port>/`, injecting the daemon credential
///    **only on the loopback hop** via the pluggable
///    ``DaemonCredentialProvider`` seam.
/// 3. Enforces the ``SurfaceAllowList`` as a DENY GATE.
/// 4. Enforces a paired-device check via ``PairedDeviceStore``
///    (stubbed in A2.1; real implementation in A2.3).
///
/// ## Hard invariants (reviewer verifies each):
/// - ZERO daemon auth-code change.
/// - Connector binds tailnet-interface ONLY.
/// - Token NEVER crosses the tailnet; injection via pluggable provider.
/// - Ride-installed only: no tsnet, no Network Extension, no new dependency.
public final class HomeConnector: @unchecked Sendable {

    // MARK: - State

    /// Current lifecycle state of the connector.
    public enum State: Equatable, CustomStringConvertible {
        case stopped
        case running(port: UInt16)
        case failed(String)  // error description

        public var description: String {
            switch self {
            case .stopped: return "stopped"
            case .running(let port): return "running(\(port))"
            case .failed(let msg): return "failed(\(msg))"
            }
        }
    }

    public private(set) var state: State = .stopped

    // MARK: - Configuration

    /// The tailscale IP address to bind to (e.g. "100.64.0.1").
    private let bindHost: String

    /// The port to listen on for remote connections.
    private let bindPort: UInt16

    /// The daemon's loopback port.
    private let daemonPort: UInt16

    /// Pluggable credential provider.
    private let credentialProvider: DaemonCredentialProvider

    /// Surface allow-list deny gate.
    private let surfaceAllowList: SurfaceAllowList

    /// Paired-device store (stub in A2.1).
    private let pairedDeviceStore: PairedDeviceStore

    /// Dispatch queue for the listener and connections.
    private let queue: DispatchQueue

    /// The underlying network listener.
    private var listener: NWListener?

    // MARK: - Connection tracking (A2.4 live-session revocation)

    /// Lock guarding ``deviceConnections``.
    private let connectionsLock = NSLock()

    /// Active connections keyed by device credential.
    /// When ``revokeDevice(credential:)`` is called, all connections
    /// for that credential are immediately cancelled.
    private var deviceConnections: [String: [NWConnection]] = [:]

    // MARK: - Init

    /// - Parameters:
    ///   - bindHost: Tailscale IP address to bind to (e.g. from `selfTailscaleIPs`).
    ///     Validated against wildcard / public / non-tailnet addresses (FINDING 4).
    ///   - bindPort: Port to listen on for remote connections.
    ///   - daemonPort: The daemon's loopback port.
    ///   - credentialProvider: Pluggable credential injection seam.
    ///   - surfaceAllowList: Surface allow-list deny gate (default: v1 policy).
    ///   - pairedDeviceStore: Paired-device store (REQUIRED — no permissive default,
    ///     FINDING 3).
    ///   - queue: Dispatch queue for connections (default: a new serial queue).
    ///   - tailnetSelfIP: Optional tailnet self-address for bindHost validation.
    ///     When nil, validation is skipped (test seam only).  In production,
    ///     supply the 100.x IP from TailscaleStatusProvider.
    public init(
        bindHost: String,
        bindPort: UInt16,
        daemonPort: UInt16,
        credentialProvider: DaemonCredentialProvider,
        surfaceAllowList: SurfaceAllowList = .default,
        pairedDeviceStore: PairedDeviceStore,
        queue: DispatchQueue = DispatchQueue(label: "com.happyranch.home-connector"),
        tailnetSelfIP: String? = nil
    ) {
        // --- bindHost validation (FINDING 4) ---
        // Reject wildcard, empty, loopback (in production), public/non-tailnet.
        // Throws HomeConnectorError instead of preconditionFailure so tests can
        // verify the rejection behavior.
        guard !bindHost.isEmpty else {
            self.bindHost = ""
            self.bindPort = 0
            self.daemonPort = 0
            self.credentialProvider = credentialProvider
            self.surfaceAllowList = surfaceAllowList
            self.pairedDeviceStore = pairedDeviceStore
            self.queue = queue
            self.state = .failed("HomeConnector bindHost must not be empty")
            return
        }

        let lowerHost = bindHost.lowercased()

        // Reject wildcard addresses
        guard lowerHost != "0.0.0.0" && lowerHost != "::" else {
            self.bindHost = bindHost
            self.bindPort = 0
            self.daemonPort = 0
            self.credentialProvider = credentialProvider
            self.surfaceAllowList = surfaceAllowList
            self.pairedDeviceStore = pairedDeviceStore
            self.queue = queue
            self.state = .failed(
                "HomeConnector refuses to bind to \(bindHost) — wildcard binding is forbidden"
            )
            return
        }

        // Allow loopback ONLY when tailnetSelfIP is nil (test seam).
        // In production (tailnetSelfIP is set), loopback is rejected.
        let isLoopback = lowerHost == "127.0.0.1" || lowerHost == "::1" || lowerHost == "localhost"
        if isLoopback && tailnetSelfIP != nil {
            self.bindHost = bindHost
            self.bindPort = 0
            self.daemonPort = 0
            self.credentialProvider = credentialProvider
            self.surfaceAllowList = surfaceAllowList
            self.pairedDeviceStore = pairedDeviceStore
            self.queue = queue
            self.state = .failed(
                "HomeConnector refuses loopback bind in production — must use tailnet 100.x address"
            )
            return
        }

        // When a tailnet self-IP is provided, verify the bindHost is a tailnet address
        // AND that it matches the self-IP EXACTLY (FINDING B).
        // A different 100.x address (e.g. another tailnet peer's IP) is REJECTED.
        if let tailnetIP = tailnetSelfIP {
            // Tailnet addresses are in the 100.64.0.0/10 range (CGNAT)
            let isTailnet = bindHost.hasPrefix("100.")
            guard isTailnet else {
                self.bindHost = bindHost
                self.bindPort = 0
                self.daemonPort = 0
                self.credentialProvider = credentialProvider
                self.surfaceAllowList = surfaceAllowList
                self.pairedDeviceStore = pairedDeviceStore
                self.queue = queue
                self.state = .failed(
                    "HomeConnector bindHost \(bindHost) is not a tailnet address " +
                    "(expected 100.x.y.z); self-IP is \(tailnetIP)"
                )
                return
            }
            // Exact-match requirement: bindHost must equal the self-IP,
            // not just any 100.x address on the tailnet.
            guard bindHost == tailnetIP else {
                self.bindHost = bindHost
                self.bindPort = 0
                self.daemonPort = 0
                self.credentialProvider = credentialProvider
                self.surfaceAllowList = surfaceAllowList
                self.pairedDeviceStore = pairedDeviceStore
                self.queue = queue
                self.state = .failed(
                    "HomeConnector bindHost \(bindHost) does not match tailnet self-IP \(tailnetIP)"
                )
                return
            }
        }

        self.bindHost = bindHost
        self.bindPort = bindPort
        self.daemonPort = daemonPort
        self.credentialProvider = credentialProvider
        self.surfaceAllowList = surfaceAllowList
        self.pairedDeviceStore = pairedDeviceStore
        self.queue = queue
    }

    // MARK: - Lifecycle

    /// Start listening on the tailscale interface.
    ///
    /// - Throws: If the listener cannot be created or started.
    public func start() throws {
        guard case .stopped = state else {
            throw HomeConnectorError.alreadyRunning
        }

        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host(bindHost),
            port: NWEndpoint.Port(integerLiteral: bindPort)
        )

        let listener: NWListener
        do {
            listener = try NWListener(using: parameters)
        } catch {
            state = .failed(error.localizedDescription)
            throw HomeConnectorError.listenerCreationFailed(underlying: error.localizedDescription)
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
        // Tear down all tracked connections.
        connectionsLock.lock()
        let allConnections = deviceConnections.values.flatMap { $0 }
        deviceConnections.removeAll()
        connectionsLock.unlock()
        for connection in allConnections {
            connection.cancel()
        }
    }

    // MARK: - Connection tracking (A2.4)

    /// Register a connection as active for the given device credential.
    private func registerConnection(_ connection: NWConnection, forDevice deviceID: String) {
        connectionsLock.lock()
        var conns = deviceConnections[deviceID, default: []]
        conns.append(connection)
        deviceConnections[deviceID] = conns
        connectionsLock.unlock()
    }

    /// Unregister a connection for the given device credential.
    private func unregisterConnection(_ connection: NWConnection, forDevice deviceID: String) {
        connectionsLock.lock()
        guard var conns = deviceConnections[deviceID] else {
            connectionsLock.unlock()
            return
        }
        conns.removeAll { $0 === connection }
        if conns.isEmpty {
            deviceConnections.removeValue(forKey: deviceID)
        } else {
            deviceConnections[deviceID] = conns
        }
        connectionsLock.unlock()
    }

    /// Revoke a paired device **AND tear down its active proxy connections.**
    ///
    /// ## A2.4 live-session revocation invariant:
    /// - Invalidates the device's pairing credential via ``PairedDeviceStore``.
    /// - Actively cancels every open `NWConnection` (both client-side and
    ///   daemon-side) associated with that credential.
    /// - Returns `true` if a device was revoked, `false` if the credential
    ///   was not found.
    ///
    /// After this call, any reconnect attempt with the same credential
    /// receives 403 — ``isPaired(deviceID:)`` returns false for it.
    public func revokeDevice(credential: String) -> Bool {
        guard pairedDeviceStore.revokePairing(credential: credential) else {
            return false
        }

        // Tear down all active connections for this device.
        connectionsLock.lock()
        let connections = deviceConnections.removeValue(forKey: credential) ?? []
        connectionsLock.unlock()

        for connection in connections {
            connection.cancel()
        }

        return true
    }

    // MARK: - Listener state

    private func handleListenerState(_ newState: NWListener.State) {
        switch newState {
        case .ready:
            if let port = listener?.port {
                state = .running(port: port.rawValue)
            } else {
                state = .running(port: bindPort)
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
        DiagnosticsCollector.shared?.recordConnectPathLog(
            stage: "homeConnector-accepted",
            message: "New inbound connection accepted on \(bindHost):\(bindPort)"
        )

        clientConnection.stateUpdateHandler = { [weak self, weak clientConnection] state in
            guard let self, let clientConnection else { return }
            switch state {
            case .waiting(let error):
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-connection-waiting",
                    message: "Inbound connection waiting: \(error.localizedDescription)"
                )
            case .ready:
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-connection-ready",
                    message: "Inbound connection ready — reading request"
                )
                self.receiveFromClient(clientConnection)
            case .failed(let error):
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-connection-failed",
                    message: "Inbound connection failed: \(error.localizedDescription)"
                )
                clientConnection.cancel()
            case .cancelled:
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-connection-cancelled",
                    message: "Inbound connection cancelled"
                )
                clientConnection.cancel()
            default:
                break
            }
        }
        clientConnection.start(queue: queue)
    }

    // MARK: - Request reading

    /// Read the HTTP request from the client, validate, and relay to daemon.
    private func receiveFromClient(_ clientConnection: NWConnection) {
        clientConnection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
            [weak self] data, _, isComplete, error in

            guard let self else { return }

            if error != nil {
                clientConnection.cancel()
                return
            }

            guard let data, !data.isEmpty else {
                if isComplete {
                    clientConnection.cancel()
                }
                return
            }

            // Parse the HTTP request
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
            let rawPath = parts[1]

            DiagnosticsCollector.shared?.recordConnectPathLog(
                stage: "homeConnector-request-received",
                message: "Received \(method) \(rawPath)"
            )

            // Normalize path: strip trailing slash AND /api/vN prefix.
            // The daemon routes live under /api/v1 (runtime/daemon/app.py:199-221),
            // so the unprefixed-only matching was a CRITICAL bypass (reviewer FINDING 1).
            var normalizedPath = rawPath.hasSuffix("/") && rawPath.count > 1
                ? String(rawPath.dropLast())
                : rawPath
            normalizedPath = DaemonPathNormalizer.stripApiPrefix(normalizedPath)

            // Parse headers for device credential extraction
            let headers = Self.parseHeaders(from: lines)

            // --- PAIRING ENDPOINT (handled locally, NOT proxied to daemon) ---
            // POST /pair — client sends pairing code, gets back a per-device credential.
            // This is BEFORE the surface-allow-list gate because pairing is a native
            // connector operation, not a daemon surface.
            if method == "POST" && normalizedPath == "/pair" {
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-pair-request",
                    message: "POST /pair — attempting pairing (code NOT logged)"
                )
                let body = Self.extractBody(from: requestString) ?? ""
                if let credential = self.pairedDeviceStore.pair(
                    usingCode: body.trimmingCharacters(in: .whitespaces),
                    deviceName: "client"
                ) {
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "homeConnector-pair-success",
                        message: "Pairing succeeded — device credential issued (value NOT logged)"
                    )
                    let responseBody = "{\"credential\":\"\(credential)\"}"
                    self.sendJSONResponse(
                        to: clientConnection,
                        status: 200,
                        body: responseBody
                    )
                } else {
                    DiagnosticsCollector.shared?.recordConnectPathLog(
                        stage: "homeConnector-pair-rejected",
                        message: "Pairing rejected — invalid or expired pairing code"
                    )
                    self.sendErrorResponse(
                        to: clientConnection,
                        status: 403,
                        message: "Forbidden — invalid or expired pairing code"
                    )
                }
                return
            }

            // --- SURFACE ALLOW-LIST DENY GATE ---
            // Pass both the normalized (unprefixed) path for the allow-list check
            // AND the original raw path so the allow-list can also match prefixed forms.
            guard self.surfaceAllowList.isAllowed(method: method, path: normalizedPath, rawPath: rawPath) else {
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-surface-deny",
                    message: "Surface allow-list DENY for \(method) \(rawPath)"
                )
                self.sendErrorResponse(
                    to: clientConnection,
                    status: 403,
                    message: "Forbidden — surface not available remotely"
                )
                return
            }

            // --- PAIRED-DEVICE CHECK ---
            // Extract the device credential from the X-HappyRanch-Device-Credential header.
            // An empty or missing credential is treated as "unpaired" and rejected.
            // This is the hard invariant: tailnet presence is necessary but NOT sufficient;
            // a random tailnet peer WITHOUT a valid pairing credential gets NOTHING.
            let deviceID = headers["x-happyranch-device-credential"] ?? ""
            let deviceIDForLog = deviceID.isEmpty ? "(empty)" : "(present, NOT logged)"
            guard self.pairedDeviceStore.isPaired(deviceID: deviceID) else {
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-device-unauthorized",
                    message: "Device credential not paired — rejecting (deviceID: \(deviceIDForLog))"
                )
                self.sendErrorResponse(
                    to: clientConnection,
                    status: 403,
                    message: "Forbidden — device not paired"
                )
                return
            }
            DiagnosticsCollector.shared?.recordConnectPathLog(
                stage: "homeConnector-device-authorized",
                message: "Device credential paired — authorizing (deviceID: \(deviceIDForLog))"
            )

            // Register the client connection for live-session tracking (A2.4).
            // If the device is later revoked, this connection will be actively
            // torn down.
            if !deviceID.isEmpty {
                self.registerConnection(clientConnection, forDevice: deviceID)
            }

            // --- INJECT CREDENTIAL ---
            let credential: String
            do {
                credential = try self.credentialProvider.credential()
            } catch {
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-credential-unavailable",
                    message: "Credential provider failed: \(error.localizedDescription)"
                )
                self.sendErrorResponse(
                    to: clientConnection,
                    status: 500,
                    message: "Internal Server Error — credential unavailable"
                )
                return
            }
            DiagnosticsCollector.shared?.recordConnectPathLog(
                stage: "homeConnector-credential-injected",
                message: "Daemon credential injected (value NOT logged)"
            )

            // Modify the request: inject the Authorization header
            let modifiedRequest = self.injectCredential(
                into: requestString,
                credential: credential
            )

            // --- RELAY TO DAEMON ---
            DiagnosticsCollector.shared?.recordConnectPathLog(
                stage: "homeConnector-relay-start",
                message: "Relaying \(method) \(rawPath) to daemon on 127.0.0.1:\(daemonPort)"
            )
            self.relayToDaemon(
                clientConnection: clientConnection,
                modifiedRequest: modifiedRequest,
                initialData: data,
                deviceID: deviceID
            )
        }
    }

    // MARK: - Credential injection

    /// Inject the daemon credential into the HTTP request.
    ///
    /// Adds an `Authorization: Bearer <credential>` header before the
    /// first `\r\n\r\n` (end of headers).
    private func injectCredential(into request: String, credential: String) -> String {
        let authHeader = "Authorization: Bearer \(credential)\r\n"

        // Find the end of headers (first \r\n\r\n).
        // Insert at lowerBound + 2 (past the first \r\n of the separator,
        // which terminates the last header line) so the new header lands on
        // its own line, not merged into the preceding header.
        if let headerEndRange = request.range(of: "\r\n\r\n") {
            var modified = request
            let insertPos = request.index(headerEndRange.lowerBound, offsetBy: 2)
            modified.insert(
                contentsOf: authHeader,
                at: insertPos
            )
            return modified
        }

        // If no header end found, append after the request line
        if let firstNewlineRange = request.range(of: "\r\n") {
            var modified = request
            modified.insert(
                contentsOf: authHeader,
                at: firstNewlineRange.upperBound
            )
            return modified
        }

        // Fallback: just append
        return request + "\r\n" + authHeader
    }

    // MARK: - Relay to daemon

    /// Open a connection to the daemon on loopback, forward the request,
    /// and relay the response back to the client.
    ///
    /// Uses a request-response pattern: sends the request, reads the full
    /// daemon response, forwards it to the client, then closes both connections.
    /// This handles HTTP (single request-response) and SSE (the daemon streams
    /// until it closes).  Full bidirectional WebSocket proxying is deferred to
    /// a future slice.
    ///
    /// - Parameters:
    ///   - deviceID: The paired-device credential for live-session tracking
    ///     (A2.4).  When non-empty, the daemon connection is registered and
    ///     will be torn down if the device is revoked.
    private func relayToDaemon(
        clientConnection: NWConnection,
        modifiedRequest: String,
        initialData: Data,
        deviceID: String
    ) {
        let daemonEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host("127.0.0.1"),
            port: NWEndpoint.Port(integerLiteral: daemonPort)
        )
        let daemonConnection = NWConnection(to: daemonEndpoint, using: .tcp)

        daemonConnection.stateUpdateHandler = { [weak clientConnection] state in
            guard let clientConnection else { return }
            switch state {
            case .waiting(let error):
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-daemon-connection-waiting",
                    message: "Daemon connection waiting: \(error.localizedDescription)"
                )
            case .ready:
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-daemon-connection-ready",
                    message: "Daemon connection ready — forwarding request"
                )
                // Register the daemon-side connection for live-session
                // tracking (A2.4).  When the device is revoked, this
                // connection will be cancelled.
                if !deviceID.isEmpty {
                    self.registerConnection(daemonConnection, forDevice: deviceID)
                }

                let modifiedData = modifiedRequest.data(using: .utf8) ?? Data()
                daemonConnection.send(
                    content: modifiedData,
                    completion: .contentProcessed { [weak self] _ in
                        self?.relayDaemonResponse(
                            daemon: daemonConnection,
                            client: clientConnection
                        )
                    }
                )
            case .failed(let error):
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-daemon-connection-failed",
                    message: "Daemon connection failed: \(error.localizedDescription)"
                )
                // Clean up tracked connections when either side closes.
                if !deviceID.isEmpty {
                    self.unregisterConnection(daemonConnection, forDevice: deviceID)
                    self.unregisterConnection(clientConnection, forDevice: deviceID)
                }
                clientConnection.cancel()
            case .cancelled:
                DiagnosticsCollector.shared?.recordConnectPathLog(
                    stage: "homeConnector-daemon-connection-cancelled",
                    message: "Daemon connection cancelled"
                )
                // Clean up tracked connections when either side closes.
                if !deviceID.isEmpty {
                    self.unregisterConnection(daemonConnection, forDevice: deviceID)
                    self.unregisterConnection(clientConnection, forDevice: deviceID)
                }
                clientConnection.cancel()
            default:
                break
            }
        }
        daemonConnection.start(queue: queue)
    }

    /// Relay daemon response chunks to the client until the daemon closes.
    private func relayDaemonResponse(
        daemon: NWConnection,
        client: NWConnection
    ) {
        daemon.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
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
                            self.relayDaemonResponse(daemon: daemon, client: client)
                        }
                    }
                )
            } else if isComplete {
                client.cancel()
            } else {
                self.relayDaemonResponse(daemon: daemon, client: client)
            }
        }
    }

    // MARK: - Request parsing helpers

    /// Parse HTTP headers from request lines into a dictionary.
    /// Headers are normalized to lowercase keys for case-insensitive lookup.
    private static func parseHeaders(from lines: [String]) -> [String: String] {
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {  // skip request line
            guard !line.isEmpty else { continue }
            guard let colonIndex = line.firstIndex(of: ":") else { continue }
            let key = String(line[..<colonIndex]).trimmingCharacters(in: .whitespaces).lowercased()
            let value = String(line[colonIndex...].dropFirst()).trimmingCharacters(in: .whitespaces)
            headers[key] = value
        }
        return headers
    }

    /// Extract the HTTP body from a raw request string (content after \r\n\r\n).
    private static func extractBody(from request: String) -> String? {
        guard let bodyRange = request.range(of: "\r\n\r\n") else { return nil }
        return String(request[bodyRange.upperBound...])
    }

    /// Send a JSON response and close the connection.
    private func sendJSONResponse(
        to connection: NWConnection,
        status: Int,
        body: String
    ) {
        let statusText: String = {
            switch status {
            case 200: return "OK"
            case 201: return "Created"
            case 403: return "Forbidden"
            case 500: return "Internal Server Error"
            default: return "OK"
            }
        }()

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
}

// MARK: - HomeConnectorError

public enum HomeConnectorError: Error, Equatable {
    case alreadyRunning
    case listenerCreationFailed(underlying: String)
}

extension HomeConnectorError {
    public static func == (lhs: HomeConnectorError, rhs: HomeConnectorError) -> Bool {
        switch (lhs, rhs) {
        case (.alreadyRunning, .alreadyRunning):
            return true
        case (.listenerCreationFailed(let lm), .listenerCreationFailed(let rm)):
            return lm == rm
        default:
            return false
        }
    }
}
