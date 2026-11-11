/// Poll startup at 0.5s for 30 attempts, then monitor a healthy daemon every 5s.
/// One failed HTTP check uses the supervisor's existing unhealthy transition.
public struct ManagedHealthPolling {
    private var startupAttempts = 0
    private var reachedRunning = false

    public init() {}

    /// Nil ends monitoring only for a terminal/stopping state or startup expiry.
    public mutating func nextDelay(state: DaemonState) -> UInt64? {
        switch state {
        case .running:
            reachedRunning = true
            return 5_000_000_000
        case .starting, .unhealthy:
            if reachedRunning { return 5_000_000_000 }
            guard startupAttempts < 30 else { return nil }
            startupAttempts += 1
            return 500_000_000
        default:
            return nil
        }
    }
}
