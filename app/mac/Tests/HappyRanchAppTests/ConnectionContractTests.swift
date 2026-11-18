import Foundation
import Testing
@testable import HappyRanchApp

@Suite("Packaged Mac connection contract")
struct ConnectionContractTests {
    private func request(_ command: String = "client.status", args: String = "{}") -> String {
        "{\"protocol\":\"happyranch.connection\",\"version\":1,\"requestId\":\"r1\",\"command\":\"\(command)\",\"args\":\(args)}"
    }

    @Test("Wire rejects duplicate/extra keys, unknown commands, bad Unicode and bounds")
    func closedWire() throws {
        #expect(try ConnectionRequest(request()).command == "client.status")
        #expect(try ConnectionRequest(request("bridge.hello", args: "{\"minVersion\":1,\"maxVersion\":1}")).id == "r1")
        let malformed = [
            request().replacingOccurrences(of: "\"version\":1", with: "\"version\":1,\"version\":1"),
            request(args: "{\"x\":1,\"x\":2}"), request(args: "{\"unexpected\":true}"),
            request("arbitrary.execute"), request("client.connectSaved", args: "{\"computerRef\":\"../secret\"}"),
            request().replacingOccurrences(of: "\"version\":1", with: "\"version\":true"),
            request().replacingOccurrences(of: "\"version\":1", with: "\"version\":1.0"),
            request().replacingOccurrences(of: "\"r1\"", with: "\"\\ud800\""),
            request() + String(repeating: " ", count: 4096)
        ]
        for raw in malformed { #expect(throws: (any Error).self) { try ConnectionRequest(raw) } }
        #expect(throws: (any Error).self) {
            try ConnectionJSON.parse(String(repeating: "[", count: 17) + "0" + String(repeating: "]", count: 17))
        }
        #expect(try ConnectionJSON.parse("{\"a\":1,\"b\":2}").object?.count == 2)
        #expect(throws: (any Error).self) { try ConnectionJSON.parse("{\"a\":1,\"\\u0061\":2}") }
    }

    private func fixture() throws -> URL {
        let root = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        try Data("hello".utf8).write(to: root.appendingPathComponent("index.html"))
        let manifest = #"{"version":1,"entry":"index.html","resources":[{"path":"index.html","bytes":5,"sha256":"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}]}"#
        try Data(manifest.utf8).write(to: root.appendingPathComponent("resource-manifest.json"))
        return root
    }

    @Test("Bundle loader serves only literal manifest assets and rechecks content")
    func manifestDelivery() throws {
        let root = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let assets = try ConnectionAssets(root: root)
        let (bytes, mime) = try assets.resolve(URL(string: ConnectionAssets.entry)!)
        #expect(bytes == Data("hello".utf8)); #expect(mime == "text/html; charset=utf-8")
        for raw in ["happyranch-shell://connection/other", "happyranch-shell://connection/resource-manifest.json",
                    "happyranch-shell://connection/%2e%2e/index.html", "happyranch-shell://connection/index.html?q=1",
                    "happyranch-shell://connection:1/index.html", "https://connection/index.html"] {
            #expect(throws: (any Error).self) { try assets.resolve(URL(string: raw)!) }
        }
        try Data("jello".utf8).write(to: root.appendingPathComponent("index.html"))
        #expect(throws: (any Error).self) { try assets.resolve(URL(string: ConnectionAssets.entry)!) }
        try Data("hello!".utf8).write(to: root.appendingPathComponent("index.html"))
        #expect(throws: (any Error).self) { try ConnectionAssets(root: root) }
    }

    @Test("Bundle admission refuses stray files and symlinked manifest/assets/root")
    func manifestInventory() throws {
        let root = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let stray = root.appendingPathComponent("stray")
        try Data().write(to: stray)
        #expect(throws: (any Error).self) { try ConnectionAssets(root: root) }
        try FileManager.default.removeItem(at: stray)
        for name in ["index.html", "resource-manifest.json"] {
            let file = root.appendingPathComponent(name)
            let saved = try Data(contentsOf: file)
            try FileManager.default.removeItem(at: file)
            try FileManager.default.createSymbolicLink(at: file, withDestinationURL: root.appendingPathComponent("missing"))
            #expect(throws: (any Error).self) { try ConnectionAssets(root: root) }
            try FileManager.default.removeItem(at: file)
            try saved.write(to: file)
        }
        let alias = root.appendingPathComponent("alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: root)
        #expect(throws: (any Error).self) { try ConnectionAssets(root: alias) }
        try FileManager.default.removeItem(at: alias)
        #expect(try ConnectionAssets(root: root).resources.count == 1)
    }

    @Test("Client status is a nonsecret read-only projection; legacy health is not UI-loaded")
    @MainActor
    func clientProjection() throws {
        let previous = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer {
            if let previous { UserDefaults.standard.set(previous, forKey: "connectionRolePreference") }
            else { UserDefaults.standard.removeObject(forKey: "connectionRolePreference") }
        }
        let app = AppDelegate()
        app.connectionRolePreference = .client
        app.clientLane = .diy
        app.clientPairingCode = "synthetic-pair-secret"
        app.clientHomeHost = "synthetic-private-endpoint.invalid"
        app.connectionState = .online
        let snapshot = ConnectionClientProjection.snapshot(app: app)
        #expect(snapshot["stage"] as? String == "opening")
        #expect(snapshot["runtime"] as? String == "unknown")
        #expect(Set(snapshot.keys) == ["stage", "runtime", "savedAccess", "route", "observedAt", "observation",
                                       "computers", "activeComputerRef", "expiresAt", "comparisonPhrase"])
        for name in ["computers", "activeComputerRef", "expiresAt", "comparisonPhrase", "observedAt"] {
            #expect(snapshot[name] is NSNull)
        }
        let wire = ConnectionRequest.reply(id: "r1", result: snapshot)
        #expect(!wire.contains("synthetic-"))
        #expect(app.clientPairingCode == "synthetic-pair-secret")
        #expect(app.homeInvite.phase == .idle)
        #expect(app.managedJoin.phase == .draft)
    }
}
