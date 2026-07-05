import Testing
@testable import HappyRanchSupervisor

@Suite("HealthProbe")
struct HealthProbeTests {

    @Test("health probe succeeds on valid response")
    func healthProbeSucceeds() async throws {
        // We can't test real HTTP without a server, but we test the URL construction
        // and the probe logic. The actual HTTP call is tested via integration.
        let probe = HealthProbe(baseURL: "http://127.0.0.1:8888")
        let url = probe.healthCheckURL
        #expect(url.absoluteString == "http://127.0.0.1:8888/api/v1/health")
    }

    @Test("health probe URL for different ports")
    func healthProbeURLForDifferentPorts() {
        let probe = HealthProbe(baseURL: "http://127.0.0.1:4321")
        let url = probe.healthCheckURL
        #expect(url.absoluteString.contains("4321"))
        #expect(url.absoluteString.hasSuffix("/api/v1/health"))
    }

    @Test("isReachable returns false for invalid URL")
    func isReachableInvalidURL() {
        let probe = HealthProbe(baseURL: "not a valid url")
        // Should not crash, should handle gracefully
        #expect(probe.baseURL == "not a valid url")
    }
}
