import Foundation
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

@Suite("Packaged Mac connection contract")
struct ConnectionContractTests {
    private func request(_ command: String = "client.status", args: String = "{}", id: String = "r1") -> String {
        "{\"protocol\":\"happyranch.connection\",\"version\":1,\"requestId\":\"\(id)\",\"command\":\"\(command)\",\"args\":\(args)}"
    }

    @Test("Wire rejects duplicate/extra keys, unknown commands, bad Unicode and bounds")
    func closedWire() throws {
        #expect(try ConnectionRequest(request()).command == "client.status")
        #expect(try ConnectionRequest(request("bridge.hello", args: "{\"minVersion\":1,\"maxVersion\":1}")).id == "r1")
        let target = try ConnectionRequest(request("client.connectSaved", args: "{\"computerRef\":\"c1\"}"))
        #expect(target.args == ["computerRef": .string("c1")])
        let same = try ConnectionRequest(request("client.connectSaved", args: "{\"computerRef\":\"c1\"}"))
        let changed = try ConnectionRequest(request("client.connectSaved", args: "{\"computerRef\":\"c2\"}"))
        #expect(target.hasSameBody(as: same))
        #expect(!target.hasSameBody(as: changed))
        let malformed = [
            request().replacingOccurrences(of: "\"version\":1", with: "\"version\":1,\"version\":1"),
            request(args: "{\"x\":1,\"x\":2}"), request(args: "{\"unexpected\":true}"),
            request("arbitrary.execute"), request("client.retrySave", args: "{\"replacing\":true}"),
            request("client.cancelAttempt", args: "{\"attempt\":\"old\"}"), request("client.connectSaved", args: "{\"computerRef\":\"../secret\"}"),
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
        app.clientLane = .managed
        let model = app.homeInvite, context = app.homeInvite.lifetime
        #expect(model.beginConfirmation(for: context))
        model.webLoaded(url: URL(string: "http://127.0.0.1:49152/")!, lifetime: context)
        model.endConfirmation(for: context)
        #expect(model.phase == .idle && model.webURL == nil)
        #expect(ConnectionClientProjection.snapshot(app: app)["stage"] as? String == "idle")
    }

    private func error(_ admission: ConnectionMutationLedger.Admission) throws -> [String: ConnectionJSON]? {
        if case .replay(let outcome) = admission {
            return try ConnectionJSON.parse(outcome.reply(id: "r1"), limit: 32768).object?["error"]?.object
        }
        return nil
    }

    @Test("Mutation bodies replay the observed outcome and conflict before another effect")
    func mutationReplayAndConflict() throws {
        var ledger = ConnectionMutationLedger()
        let original = try ConnectionRequest(request("client.connectSaved", args: "{\"computerRef\":\"c1\"}"))
        guard case .start = ledger.begin(original) else { Issue.record("first mutation refused"); return }
        ledger.markDispatched(original.id)
        ledger.finish(original.id, outcome: .failure(.init(.permissionDenied, recovery: .nativePermission)))
        #expect(try error(ledger.begin(original))?["code"] == .string("permissionDenied"))
        let changed = try ConnectionRequest(request("client.connectSaved", args: "{\"computerRef\":\"c2\"}"))
        #expect(try error(ledger.begin(changed))?["code"] == .string("invalidRequest"))
        #expect(try error(ledger.begin(ConnectionRequest(request("client.disconnect"))))?["code"] == .string("invalidRequest"))
        #expect(ledger.count == 1 && ledger.mutationID == nil)
    }

    @Test("64 retained IDs never evict; attempt cancellation has its own repeat-safe record")
    func ledgerBoundsAndCancellation() throws {
        var ledger = ConnectionMutationLedger()
        for number in 0..<64 {
            let item = try ConnectionRequest(request("client.retrySave", id: "m\(number)"))
            guard case .start = ledger.begin(item) else { Issue.record("early saturation"); return }
            ledger.finish(item.id, outcome: .failure(.init(.saveFailed, effect: .unknown, recovery: .explicitRetry)))
        }
        #expect(ledger.count == 64)
        #expect(try error(ledger.begin(ConnectionRequest(request("client.retrySave", id: "overflow"))))?["code"] == .string("busy"))
        #expect(try error(ledger.begin(ConnectionRequest(request("client.retrySave", id: "m0"))))?["code"] == .string("saveFailed"))
        let cancel = try ConnectionRequest(request("client.cancelAttempt", id: "cancel"))
        let attempt = UUID()
        guard case .start = ledger.begin(cancel, cancelling: attempt) else { Issue.record("Cancel blocked by saturation"); return }
        #expect(ledger.reservePrompt(for: cancel.id))
        #expect(!ledger.reservePrompt(for: cancel.id))
        ledger.markDispatched(cancel.id)
        ledger.finish(cancel.id, outcome: .failure(.init(.outcomeUnknown, effect: .unknown, recovery: .nativeRecovery)))
        let repeated = try ConnectionRequest(request("client.cancelAttempt", id: "cancel2"))
        #expect(try error(ledger.begin(repeated, cancelling: attempt))?["effect"] == .string("unknown"))
        #expect(try error(ledger.begin(cancel, cancelling: UUID()))?["code"] == .string("outcomeUnknown"))
        #expect(try error(ledger.begin(ConnectionRequest(request("client.retrySave", id: "cancel"))))?["code"] == .string("invalidRequest"))
        #expect(ledger.count == 64)
    }

    @Test("Timeout retains the native prompt and original action; Cancel bypasses only the mutation slot")
    func ledgerBusyPromptAndTimeout() throws {
        var ledger = ConnectionMutationLedger()
        let save = try ConnectionRequest(request("client.retrySave"))
        guard case .start = ledger.begin(save) else { Issue.record("save refused"); return }
        #expect(ledger.reservePrompt(for: save.id))
        #expect(try error(ledger.begin(ConnectionRequest(request("client.retrySave", id: "r2"))))?["code"] == .string("busy"))
        ledger.markDispatched(save.id); ledger.timedOut(save.id)
        #expect(try error(ledger.begin(save))?["effect"] == .string("unknown"))
        #expect(ledger.mutationID == save.id && ledger.promptID == save.id)
        let cancel = try ConnectionRequest(request("client.cancelAttempt", id: "cancel"))
        guard case .start = ledger.begin(cancel, cancelling: UUID()) else { Issue.record("Cancel busy"); return }
        #expect(!ledger.reservePrompt(for: cancel.id))
        #expect(ledger.originatingAction == "client.retrySave")
        ledger.finish(save.id, outcome: .failure(.init(.saveFailed, effect: .unknown)))
        #expect(ledger.mutationID == nil && ledger.promptID == nil)
        #expect(ledger.originatingAction == "client.retrySave")
    }

    @Test("Released app sheet leaves Cancel reachable through storage and stopping")
    func cancellationAfterSheetRelease() throws {
        var ledger = ConnectionMutationLedger()
        let connect = try ConnectionRequest(request("client.connectSaved", args: "{\"computerRef\":\"c1\"}"))
        guard case .start = ledger.begin(connect) else { Issue.record("connect refused"); return }
        #expect(ledger.reservePrompt(for: connect.id))
        ledger.releasePrompt(connect.id)
        ledger.markDispatched(connect.id)
        #expect(ledger.mutationID == connect.id && ledger.promptID == nil)
        let cancel = try ConnectionRequest(request("client.cancelAttempt", id: "cancel"))
        let attempt = UUID()
        guard case .start = ledger.begin(cancel, cancelling: attempt) else { Issue.record("Cancel unreachable"); return }
        #expect(ledger.reservePrompt(for: cancel.id))
        let repeated = try ConnectionRequest(request("client.cancelAttempt", id: "again"))
        #expect(try error(ledger.begin(repeated, cancelling: attempt))?["code"] == .string("busy"))
        ledger.releasePrompt(cancel.id); ledger.markDispatched(cancel.id)
        // Confirmed stop has invalidated the model attempt; it is now stopping.
        #expect(try error(ledger.begin(repeated))?["effect"] == .string("unknown"))
        #expect(ledger.promptID == nil && ledger.mutationID == connect.id)
        ledger.timedOut(cancel.id)
        #expect(try error(ledger.begin(repeated))?["effect"] == .string("unknown"))
        ledger.finish(cancel.id, outcome: .failure(.init(.outcomeUnknown, effect: .unknown)))
        #expect(try error(ledger.begin(repeated, cancelling: attempt))?["effect"] == .string("unknown"))
    }

    @Test("Hello advertises all ten backed client commands and no host capability")
    @MainActor
    func backedCapabilities() throws {
        let previous = UserDefaults.standard.object(forKey: "connectionRolePreference")
        defer {
            if let previous { UserDefaults.standard.set(previous, forKey: "connectionRolePreference") }
            else { UserDefaults.standard.removeObject(forKey: "connectionRolePreference") }
        }
        let app = AppDelegate(); app.connectionRolePreference = .client; app.clientLane = .managed
        let hello = ConnectionClientProjection.hello(app: app, catalog: ConnectionComputerCatalog())
        let commands = Set((hello["capabilities"] as? [[String: String]] ?? []).compactMap { $0["command"] })
        #expect(commands == ["client.status", "client.savedComputers", "client.health", "client.retrySave",
            "client.acceptInvite", "client.connectSaved", "client.removeSaved", "client.disconnect", "client.cancelAttempt", "client.retry"])
        #expect(hello["roles"] as? [String] == ["client"])
        #expect(!commands.contains { $0.hasPrefix("host.") })
        #expect(try ConnectionJSON.parse(ConnectionRequest.reply(id: "r1")).object?["error"]?.object ==
            ["code": .string("unavailable"), "effect": .string("none"), "recovery": .string("refresh")])
        // Fresh reads and hello do not launch discovery, health or a connection.
        #expect(app.homeInvite.phase == .idle && !app.homeInvite.busy && !app.homeInvite.discoveryComplete)
    }

    @Test("Health and recovery use closed observations without declaring a loaded UI")
    @MainActor
    func healthAndRecovery() {
        let model = HomeInviteModel()
        let health = ConnectionClientProjection.health(model, now: Date())
        #expect(health["access"] as? String == "unknown")
        #expect(health["runtime"] as? String == "unknown")
        #expect(health["observedAt"] is NSNull)
        #expect(ConnectionClientProjection.saveError(.locked) == .init(.keychainLocked, recovery: .nativePermission))
        #expect(ConnectionClientProjection.saveError(.identityChanged) == .init(.identityChanged, effect: .unknown, recovery: .nativeRecovery))
        #expect(ConnectionClientProjection.saveError(.saveFailed).effect == .unknown)
        #expect(model.phase == .idle && !model.busy)
    }
}
