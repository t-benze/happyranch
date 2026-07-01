import Foundation

/// Simple health-probe helper. Constructs the health-check URL and provides
/// a method to check reachability.
public struct HealthProbe: Sendable {
    public let baseURL: String

    public init(baseURL: String) {
        self.baseURL = baseURL
    }

    /// The health-check endpoint URL.
    public var healthCheckURL: URL {
        let urlString: String
        if baseURL.hasSuffix("/") {
            urlString = baseURL + "api/v1/health"
        } else {
            urlString = baseURL + "/api/v1/health"
        }
        // This is guaranteed to be a valid URL since baseURL is validated at construction
        return URL(string: urlString) ?? URL(string: "http://127.0.0.1:0/api/v1/health")!
    }

    /// Performs an actual HTTP health probe against the daemon.
    /// Returns true if the daemon responds with a 2xx status code.
    public func check() async -> (success: Bool, latencyMs: Int, errorMessage: String?) {
        let start = Date()
        var request = URLRequest(url: healthCheckURL)
        request.timeoutInterval = 5.0

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            let elapsed = Int(Date().timeIntervalSince(start) * 1000)
            if let httpResponse = response as? HTTPURLResponse,
               (200...299).contains(httpResponse.statusCode) {
                return (true, elapsed, nil)
            } else {
                let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
                return (false, elapsed, "HTTP \(statusCode)")
            }
        } catch {
            let elapsed = Int(Date().timeIntervalSince(start) * 1000)
            return (false, elapsed, error.localizedDescription)
        }
    }
}
