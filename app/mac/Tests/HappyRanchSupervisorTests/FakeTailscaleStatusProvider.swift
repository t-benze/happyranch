import Foundation
import HappyRanchSupervisor

/// Test double for ``TailscaleStatusProviding``.
///
/// Allows tests to simulate tailscale presence/absence, home-node
/// online/offline, and status parsing without a real tailnet.
///
/// Mirrors the ``FakeProcessController`` pattern: a configurable
/// `@unchecked Sendable` class that records calls and lets tests
/// control the returned status, running flag, and home-node address.
public final class FakeTailscaleStatusProvider: TailscaleStatusProviding, @unchecked Sendable {

    // MARK: - Configuration

    /// The status to return from `fetchStatus()`.
    public var stubStatus: TailscaleStatus = TailscaleStatus(
        isRunning: true,
        backendState: "Running",
        selfNodeName: "test-mac.test.ts.net",
        selfTailscaleIPs: ["100.64.0.1"],
        peers: [],
        version: "1.80.0"
    )

    /// The running flag for `isTailscaleRunning()`.
    public var stubIsRunning: Bool = true

    /// The home-node address for `resolveHomeNode(fallbackAddress:)`.
    /// When `nil`, the method falls back to the fallback address (if any).
    public var stubHomeNodeAddress: String? = "100.100.100.100"

    /// When set, `fetchStatus()` throws this error instead of returning
    /// the stub status.
    public var stubFetchError: Error?

    /// When set, the first `fetchStatus()` call returns this status
    /// (one-shot override); subsequent calls use `stubStatus`.
    /// Useful for testing state transitions.
    public var nextFetchStatusOverride: TailscaleStatus?

    // MARK: - Call tracking

    public var fetchStatusCallCount = 0
    public var isTailscaleRunningCallCount = 0
    public var resolveHomeNodeCallCount = 0
    public private(set) var lastFallbackAddress: String?

    // MARK: - Init

    public init() {}

    // MARK: - TailscaleStatusProviding

    public func fetchStatus() throws -> TailscaleStatus {
        fetchStatusCallCount += 1

        if let error = stubFetchError {
            throw error
        }

        if let override = nextFetchStatusOverride {
            nextFetchStatusOverride = nil
            return override
        }

        return stubStatus
    }

    public func isTailscaleRunning() -> Bool {
        isTailscaleRunningCallCount += 1
        return stubIsRunning
    }

    public func resolveHomeNode(fallbackAddress: String?) -> String? {
        resolveHomeNodeCallCount += 1
        lastFallbackAddress = fallbackAddress

        if let addr = stubHomeNodeAddress {
            return addr
        }

        return fallbackAddress
    }
}
