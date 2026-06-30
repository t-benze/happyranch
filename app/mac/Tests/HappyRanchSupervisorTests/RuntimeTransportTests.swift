import Testing
@testable import HappyRanchSupervisor

@Suite("RuntimeTransport")
struct RuntimeTransportTests {

    // MARK: - LocalLoopbackTransport

    @Test("LocalLoopbackTransport yields correct loopback URL from port value")
    func localLoopbackTransportYieldsCorrectURL() {
        let transport = LocalLoopbackTransport()
        let url = transport.baseURL(for: 9876)
        #expect(url == "http://127.0.0.1:9876/")
    }

    @Test("LocalLoopbackTransport for different ports")
    func localLoopbackTransportDifferentPorts() {
        let transport = LocalLoopbackTransport()
        #expect(transport.baseURL(for: 4321) == "http://127.0.0.1:4321/")
        #expect(transport.baseURL(for: 80) == "http://127.0.0.1:80/")
    }

    // MARK: - RemoteTransport placeholder

    @Test("RemoteTransport exists as internal placeholder")
    func remoteTransportExists() {
        let transport = RemoteTransport(host: "example.com")
        let url = transport.baseURL(for: 8080)
        #expect(url == "http://example.com:8080/")
    }

    @Test("RemoteTransport is not reachable from LocalLoopbackTransport path")
    func remoteTransportNotReachableFromLocalLoopback() {
        // LocalLoopbackTransport always returns 127.0.0.1 regardless of input
        let localTransport = LocalLoopbackTransport()
        _ = RemoteTransport(host: "evil.example.com")  // Exists but not wired to UI

        // The critical invariant: LocalLoopbackTransport NEVER produces a
        // non-loopback URL. This is an existence proof — the LocalLoopbackTransport
        // type itself cannot generate a remote URL.
        let localURL = localTransport.baseURL(for: 9999)
        #expect(localURL.contains("127.0.0.1"),
                "LocalLoopbackTransport must always use 127.0.0.1")

        // RemoteTransport is a separate type — it's not reachable via
        // the LocalLoopbackTransport code path at all (compile-time safety).
    }

    @Test("protocol conformance verified")
    func protocolConformance() {
        let local: RuntimeTransport = LocalLoopbackTransport()
        let remote: RuntimeTransport = RemoteTransport(host: "example.com")

        #expect(local.baseURL(for: 8888) == "http://127.0.0.1:8888/")
        #expect(remote.baseURL(for: 8888) == "http://example.com:8888/")
    }
}
