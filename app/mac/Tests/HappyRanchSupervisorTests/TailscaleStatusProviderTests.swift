import Testing
import Foundation
@testable import HappyRanchSupervisor

@Suite("TailscaleStatus model")
struct TailscaleStatusModelTests {

    @Test("isRunning is true when backendState is Running")
    func isRunningTrue() {
        let status = TailscaleStatus(isRunning: true, backendState: "Running")
        #expect(status.isRunning)
    }

    @Test("isRunning is false when backendState is not Running")
    func isRunningFalse() {
        let status = TailscaleStatus(isRunning: false, backendState: "Stopped")
        #expect(!status.isRunning)
    }

    @Test("peers list is empty by default")
    func peersEmptyByDefault() {
        let status = TailscaleStatus(isRunning: true)
        #expect(status.peers.isEmpty)
    }

    @Test("peers can be populated")
    func peersPopulated() {
        let peer = TailscalePeer(online: true, tailscaleIPs: ["100.64.0.2"])
        let status = TailscaleStatus(isRunning: true, peers: [peer])
        #expect(status.peers.count == 1)
        #expect(status.peers[0].online == true)
        #expect(status.peers[0].tailscaleIPs == ["100.64.0.2"])
    }

    @Test("selfTailscaleIPs is empty by default")
    func selfIPsEmptyByDefault() {
        let status = TailscaleStatus(isRunning: true)
        #expect(status.selfTailscaleIPs.isEmpty)
    }
}

@Suite("TailscalePeer model")
struct TailscalePeerModelTests {

    @Test("online peer has online = true")
    func onlinePeer() {
        let peer = TailscalePeer(online: true)
        #expect(peer.online)
    }

    @Test("offline peer has online = false")
    func offlinePeer() {
        let peer = TailscalePeer(online: false)
        #expect(!peer.online)
    }

    @Test("peer with full metadata")
    func fullPeerMetadata() {
        let peer = TailscalePeer(
            nodeID: "n12345ABCDE",
            hostName: "home-mac",
            dnsName: "home-mac.tailnet.ts.net",
            online: true,
            tailscaleIPs: ["100.64.0.10"],
            lastSeen: "2026-07-07T08:00:00Z",
            os: "macOS"
        )
        #expect(peer.nodeID == "n12345ABCDE")
        #expect(peer.hostName == "home-mac")
        #expect(peer.dnsName == "home-mac.tailnet.ts.net")
        #expect(peer.online == true)
        #expect(peer.tailscaleIPs == ["100.64.0.10"])
        #expect(peer.lastSeen == "2026-07-07T08:00:00Z")
        #expect(peer.os == "macOS")
    }
}

@Suite("FakeTailscaleStatusProvider")
struct FakeTailscaleStatusProviderTests {

    @Test("default stub returns running tailscale with placeholder data")
    func defaultStub() throws {
        let fake = FakeTailscaleStatusProvider()
        let status = try fake.fetchStatus()

        #expect(status.isRunning)
        #expect(status.backendState == "Running")
        #expect(status.selfNodeName == "test-mac.test.ts.net")
        #expect(fake.fetchStatusCallCount == 1)
    }

    @Test("isTailscaleRunning returns stub value")
    func isRunningStub() {
        let fake = FakeTailscaleStatusProvider()
        #expect(fake.isTailscaleRunning())

        fake.stubIsRunning = false
        #expect(!fake.isTailscaleRunning())

        #expect(fake.isTailscaleRunningCallCount == 2)
    }

    @Test("resolveHomeNode returns stub address")
    func resolveHomeNodeStub() {
        let fake = FakeTailscaleStatusProvider()
        let address = fake.resolveHomeNode(fallbackAddress: nil)
        #expect(address == "100.100.100.100")
        #expect(fake.resolveHomeNodeCallCount == 1)
    }

    @Test("resolveHomeNode falls back to manual address when stub is nil")
    func resolveHomeNodeFallback() {
        let fake = FakeTailscaleStatusProvider()
        fake.stubHomeNodeAddress = nil

        let result = fake.resolveHomeNode(fallbackAddress: "100.200.200.200")
        #expect(result == "100.200.200.200")
        #expect(fake.lastFallbackAddress == "100.200.200.200")
    }

    @Test("resolveHomeNode returns nil when stub is nil and no fallback")
    func resolveHomeNodeNil() {
        let fake = FakeTailscaleStatusProvider()
        fake.stubHomeNodeAddress = nil

        let result = fake.resolveHomeNode(fallbackAddress: nil)
        #expect(result == nil)
    }

    @Test("fetchStatus with error stub throws")
    func fetchStatusThrows() {
        let fake = FakeTailscaleStatusProvider()
        fake.stubFetchError = TailscaleStatusProviderError.commandFailed(exitCode: 1)

        do {
            _ = try fake.fetchStatus()
            Issue.record("Expected fetchStatus to throw")
        } catch let error as TailscaleStatusProviderError {
            #expect(error == .commandFailed(exitCode: 1))
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    @Test("nextFetchStatusOverride is consumed on first fetchStatus call")
    func nextFetchStatusOverrideConsumed() throws {
        let fake = FakeTailscaleStatusProvider()
        let override = TailscaleStatus(isRunning: false, backendState: "Stopped")
        fake.nextFetchStatusOverride = override

        // First call returns override
        let first = try fake.fetchStatus()
        #expect(!first.isRunning)

        // Second call returns default stub
        let second = try fake.fetchStatus()
        #expect(second.isRunning)
    }

    @Test("call tracking increments correctly")
    func callTracking() throws {
        let fake = FakeTailscaleStatusProvider()
        _ = try fake.fetchStatus()
        _ = fake.isTailscaleRunning()
        _ = fake.resolveHomeNode(fallbackAddress: "100.1.1.1")

        #expect(fake.fetchStatusCallCount == 1)
        #expect(fake.isTailscaleRunningCallCount == 1)
        #expect(fake.resolveHomeNodeCallCount == 1)
        #expect(fake.lastFallbackAddress == "100.1.1.1")
    }
}

@Suite("JSON status parsing")
struct TailscaleStatusParsingTests {

    /// Build a minimal valid tailscale status JSON.
    func makeStatusJSON(
        backendState: String = "Running",
        selfNodeName: String? = "my-mac.tailnet.ts.net",
        selfIPs: [String] = ["100.64.0.1"],
        version: String = "1.80.0",
        peers: [[String: Any]] = []
    ) -> String {
        var dict: [String: Any] = [
            "Version": version,
            "TUN": true,
            "BackendState": backendState,
            "Self": [
                "ID": "nodeid:abc123",
                "PublicKey": "nodekey:abc123",
                "HostName": "my-mac",
                "DNSName": selfNodeName as Any,
                "OS": "macOS",
                "TailscaleIPs": selfIPs
            ]
        ]

        if !peers.isEmpty {
            var peerDict: [String: [String: Any]] = [:]
            for (index, peer) in peers.enumerated() {
                peerDict["nodeid:peer\(index)"] = peer
            }
            dict["Peer"] = peerDict
        }

        return String(data: try! JSONSerialization.data(withJSONObject: dict), encoding: .utf8)!
    }

    @Test("parses running status correctly")
    func parseRunningStatus() throws {
        let json = makeStatusJSON(backendState: "Running")
        let data = json.data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        let status = try provider.parseStatusForTesting(data)
        #expect(status.isRunning)
        #expect(status.backendState == "Running")
        #expect(status.selfNodeName == "my-mac.tailnet.ts.net")
        #expect(status.selfTailscaleIPs == ["100.64.0.1"])
        #expect(status.version == "1.80.0")
    }

    @Test("parses stopped status correctly")
    func parseStoppedStatus() throws {
        let json = makeStatusJSON(backendState: "Stopped")
        let data = json.data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        let status = try provider.parseStatusForTesting(data)
        #expect(!status.isRunning)
        #expect(status.backendState == "Stopped")
    }

    @Test("parses peer list correctly")
    func parsePeerList() throws {
        let peerJSON: [String: Any] = [
            "ID": "nodeid:peer0",
            "HostName": "home-mac",
            "DNSName": "home-mac.tailnet.ts.net",
            "Online": true,
            "TailscaleIPs": ["100.64.0.10"],
            "LastSeen": "2026-07-07T08:00:00Z",
            "OS": "macOS"
        ]
        let json = makeStatusJSON(peers: [peerJSON])
        let data = json.data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        let status = try provider.parseStatusForTesting(data)
        #expect(status.peers.count == 1)
        #expect(status.peers[0].hostName == "home-mac")
        #expect(status.peers[0].dnsName == "home-mac.tailnet.ts.net")
        #expect(status.peers[0].online == true)
        #expect(status.peers[0].tailscaleIPs == ["100.64.0.10"])
        #expect(status.peers[0].lastSeen == "2026-07-07T08:00:00Z")
        #expect(status.peers[0].os == "macOS")
    }

    @Test("parses offline peer correctly")
    func parseOfflinePeer() throws {
        let peerJSON: [String: Any] = [
            "HostName": "offline-mac",
            "Online": false,
            "TailscaleIPs": ["100.64.0.99"]
        ]
        let json = makeStatusJSON(peers: [peerJSON])
        let data = json.data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        let status = try provider.parseStatusForTesting(data)
        #expect(status.peers.count == 1)
        #expect(!status.peers[0].online)
        #expect(status.peers[0].hostName == "offline-mac")
    }

    @Test("parses multiple peers correctly")
    func parseMultiplePeers() throws {
        let peer1: [String: Any] = [
            "HostName": "mac-1",
            "Online": true,
            "TailscaleIPs": ["100.64.0.2"]
        ]
        let peer2: [String: Any] = [
            "HostName": "mac-2",
            "Online": false,
            "TailscaleIPs": ["100.64.0.3"]
        ]
        let json = makeStatusJSON(peers: [peer1, peer2])
        let data = json.data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        let status = try provider.parseStatusForTesting(data)
        #expect(status.peers.count == 2)
        // Dictionary iteration order is non-deterministic — sort by hostName
        let sorted = status.peers.sorted { ($0.hostName ?? "") < ($1.hostName ?? "") }
        #expect(sorted[0].hostName == "mac-1")
        #expect(sorted[1].hostName == "mac-2")
    }

    @Test("parses empty peer dict gracefully")
    func parseEmptyPeers() throws {
        let json = makeStatusJSON(peers: [])
        let data = json.data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        let status = try provider.parseStatusForTesting(data)
        #expect(status.peers.isEmpty)
    }

    @Test("throws invalidJSON for malformed input")
    func invalidJSONThrows() {
        let data = "not json".data(using: .utf8)!
        let provider = TailscaleStatusProvider()

        do {
            _ = try provider.parseStatusForTesting(data)
            Issue.record("Expected parseStatus to throw")
        } catch let error as TailscaleStatusProviderError {
            #expect(error == .invalidJSON)
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }
}

// MARK: - Testing-only exposure

extension TailscaleStatusProvider {
    /// Expose the private `parseStatusJSON(_:)` for testing.
    /// The real method reads from `tailscale status --json`; this
    /// lets tests drive the parser with synthetic JSON payloads.
    func parseStatusForTesting(_ data: Data) throws -> TailscaleStatus {
        try parseStatusJSON(data)
    }
}
