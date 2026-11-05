import Foundation

/// Recovery is independent of process ownership and HTTP liveness.
public struct StartupObservation: Sendable, Equatable {
    public enum Recovery: String, Sendable { case unknown, recovering, ready, recovery_failed, stopping }
    public let serving: Bool?
    public let recovery: Recovery
    public static let unknown = StartupObservation(serving: nil, recovery: .unknown)
    public var ready: Bool { serving == true && recovery == .ready }
    public var label: String {
        switch recovery {
        case .ready: return "recovery ready (observed)"
        case .recovering: return "recovering; new work held"
        case .recovery_failed: return "recovery failed; new work held"
        case .stopping: return "stopping; new work held"
        case .unknown: return "recovery unknown; new work held"
        }
    }

    private struct Envelope: Decodable {
        let status: String
        let startup: Startup
        struct Startup: Decodable {
            let state: String
            let serving: Bool
            let recovery_ready: Bool
            let stopping: Bool
        }
    }

    static func decode(_ data: Data) -> StartupObservation {
        guard let body = try? JSONDecoder().decode(Envelope.self, from: data), body.status == "ok" else { return .unknown }
        let value = body.startup
        guard ["ready", "recovering", "recovery_failed", "stopping", "stopped", "unknown"].contains(value.state) else { return .unknown }
        if value.stopping || value.state == "stopped" || value.state == "stopping" {
            return StartupObservation(serving: value.serving, recovery: .stopping)
        }
        if value.state == "ready" {
            return StartupObservation(serving: value.serving, recovery: value.serving && value.recovery_ready ? .ready : .unknown)
        }
        guard !value.recovery_ready, let recovery = Recovery(rawValue: value.state) else { return .unknown }
        return StartupObservation(serving: value.serving, recovery: recovery)
    }
}

public struct HealthObservation: Sendable {
    /// HTTP liveness only; never a recovery-ready assertion.
    public let success: Bool
    public let latencyMs: Int
    public let errorMessage: String?
    public let startup: StartupObservation
}

/// One bounded request/body observation, with no retry or process effects.
public struct HealthProbe: Sendable {
    public let baseURL: String
    public init(baseURL: String) { self.baseURL = baseURL }

    public var healthCheckURL: URL {
        let prefix = baseURL.hasSuffix("/") ? baseURL : baseURL + "/"
        return URL(string: prefix + "api/v1/health") ?? URL(string: "http://127.0.0.1:0/api/v1/health")!
    }

    public func observe() async -> HealthObservation {
        let clock = ContinuousClock()
        let start = clock.now
        var request = URLRequest(url: healthCheckURL)
        request.timeoutInterval = 5.0
        func result(_ success: Bool, _ error: String?, _ startup: StartupObservation = .unknown) -> HealthObservation {
            let elapsed = start.duration(to: clock.now).components
            let ms = Int(elapsed.seconds * 1000 + elapsed.attoseconds / 1_000_000_000_000_000)
            return HealthObservation(success: success, latencyMs: ms, errorMessage: error, startup: startup)
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5.0
        configuration.timeoutIntervalForResource = 5.0
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                return result(false, "health_http_unavailable")
            }
            var data = Data()
            for try await byte in bytes {
                try Task.checkCancellation()
                guard clock.now < start.advanced(by: .seconds(5)), data.count < 16384 else {
                    return result(true, "startup_body_unavailable")
                }
                data.append(byte)
            }
            try Task.checkCancellation()
            guard clock.now < start.advanced(by: .seconds(5)) else { return result(true, "startup_body_unavailable") }
            return result(true, nil, StartupObservation.decode(data))
        } catch {
            return result(false, "health_transport_unavailable")
        }
    }

    /// Compatibility liveness API. Call observe() when consuming recovery state.
    public func check() async -> (success: Bool, latencyMs: Int, errorMessage: String?) {
        let value = await observe()
        return (value.success, value.latencyMs, value.errorMessage)
    }
}
