import Foundation

// MARK: - RuntimeTransport protocol

/// Internal protocol for constructing the daemon URL used by the WebView.
///
/// Only `LocalLoopbackTransport` is wired to the UI.
/// `RemoteTransport` is an internal placeholder — never exposed to the founder.
public protocol RuntimeTransport: Sendable {
    /// Build the base URL string for the daemon (e.g. "http://127.0.0.1:9876/").
    func baseURL(for port: UInt16) -> String
}

// MARK: - LocalLoopbackTransport (the only wired implementation)

/// Constructs the 127.0.0.1:<port> URL from the port value.
public struct LocalLoopbackTransport: RuntimeTransport {
    public init() {}

    public func baseURL(for port: UInt16) -> String {
        "http://127.0.0.1:\(port)/"
    }
}

// MARK: - RemoteTransport (internal placeholder — not exposed to UI)

/// Internal placeholder for future remote daemon connectivity.
/// This is NOT wired to any UI surface and MUST NOT have founder-facing controls.
public struct RemoteTransport: RuntimeTransport {

    /// Remote daemon host (never set from UI).
    let host: String

    public init(host: String) {
        self.host = host
    }

    public func baseURL(for port: UInt16) -> String {
        "http://\(host):\(port)/"
    }
}
