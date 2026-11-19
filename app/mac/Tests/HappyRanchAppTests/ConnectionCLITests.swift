import Foundation
import Testing
import HappyRanchSupervisor
@testable import HappyRanchApp

private actor CLIStore: SavedHomeCredentialStore {
    let discovery: SavedHomeDiscovery
    private(set) var reads = 0
    init(_ discovery: SavedHomeDiscovery = SavedHomeDiscovery(credentials: [])) { self.discovery = discovery }
    func savedHomes() async throws -> SavedHomeDiscovery { reads += 1; return discovery }
    func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        reads += 1; throw SavedAccessFailure.unavailable
    }
    func save(_ credential: SavedDeviceCredential) async throws { reads += 1; throw SavedAccessFailure.unavailable }
    func delete(binding: SavedDeviceBinding) async throws { reads += 1; throw SavedAccessFailure.unavailable }
    func refreshAddress(from credential: SavedDeviceCredential, to trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        reads += 1; throw SavedAccessFailure.unavailable
    }
}

@Suite("Mac foreground connection CLI")
@MainActor
struct ConnectionCLITests {
    @Test("Separate invocations own no session and do not touch custody or transport")
    func foreignActions() async throws {
        let store = CLIStore()
        let cli = ConnectionCLI(store: store, makeSession: { fatalError("foreign invocation started transport") })
        let status = try await cli.run("status")
        #expect(status as? [String: String] == ["state": "notOwned", "effect": "none", "observation": "unavailable"])
        for action in ["disconnect", "cancel-attempt", "retry", "retry-save", "health"] {
            let result = try await cli.run(action)
            #expect(result as? [String: String] == ["code": "unavailable", "state": "notOwned", "effect": "none", "recovery": "owningProcess"])
        }
        #expect(await store.reads == 0)
    }

    private func credential(id: UUID? = UUID()) throws -> SavedDeviceCredential {
        // Synthetic protected DTO; parser/CA verification is owned by invite tests.
        let trust = SavedHomeTrust(homeID: "HOME-CANARY", controlPlaneID: "CONTROL-CANARY", connectorID: "CONNECTOR-CANARY",
            controlURL: "https://control.happyranch.invalid:8443", dialIP: "192.168.1.40", homeEndpoint: "100.64.0.10:443",
            caDER: Data("CA-CANARY".utf8), caSHA256: "FINGERPRINT-CANARY")
        return try SavedDeviceCredential(binding: SavedDeviceBinding(homeID: trust.homeID,
            controlPlaneID: trust.controlPlaneID, stableNodeID: "NODE-CANARY"), trust: trust,
            deliveredBearer: Data("BEARER-CANARY".utf8), networkProfileID: id)
    }

    @Test("Saved listing uses generic labels, opaque refs and explicit partial discovery")
    func savedProjection() async throws {
        let record = try credential()
        let store = CLIStore(SavedHomeDiscovery(credentials: [record], failures: [.denied]))
        let cli = ConnectionCLI(store: store, makeSession: { fatalError("list started transport") })
        let result = try await cli.run("saved-computers")
        #expect(result["savedAccess"] as? String == "saved")
        #expect(result["observation"] as? String == "stale")
        let rows = try #require(result["computers"] as? [[String: String]])
        #expect(rows.count == 1)
        #expect(Set(rows[0].keys) == ["computerRef", "label"])
        #expect(rows[0]["label"] == "Saved computer 1")
        let reference = try #require(rows[0]["computerRef"])
        #expect(ConnectionRequest.identifier(reference))
        let serialized = String(decoding: try JSONSerialization.data(withJSONObject: result), as: UTF8.self)
        for canary in ["HOME-CANARY", "CONTROL-CANARY", "CONNECTOR-CANARY", "NODE-CANARY", "CA-CANARY",
                       "FINGERPRINT-CANARY", "BEARER-CANARY", "192.168.1.40", "100.64.0.10", record.networkProfileID!.uuidString] {
            #expect(!serialized.contains(canary))
        }
        #expect(await store.reads == 1)
        let unavailableCLI = ConnectionCLI(store: CLIStore(SavedHomeDiscovery(credentials: [], failures: [.locked])))
        let unavailable = try await unavailableCLI.run("saved-computers")
        #expect(unavailable["savedAccess"] as? String == "unknown")
        #expect(unavailable["observation"] as? String == "stale")
    }

    @Test("Oversized, missing-profile and duplicate-profile discovery cannot select a target")
    func savedBounds() async throws {
        let records = try (0..<32).map { _ in try credential() }
        let bounded = ConnectionCLI(store: CLIStore(SavedHomeDiscovery(credentials: records)))
        let result = try await bounded.run("saved-computers")
        #expect((result["computers"] as? [[String: String]])?.count == 32)
        for invalid in [records + [try credential()], [try credential(id: nil)], [records[0], records[0]]] {
            let cli = ConnectionCLI(store: CLIStore(SavedHomeDiscovery(credentials: invalid)))
            do { _ = try await cli.run("saved-computers"); Issue.record("Unsafe discovery was projected") }
            catch { #expect(error as? ConnectionError == ConnectionError(.unavailable)) }
        }
    }

    @Test("Cancellation precedes invite intake and cannot start custody or transport")
    func cancellationBeforeIntake() async throws {
        let store = CLIStore()
        let cli = ConnectionCLI(store: store, makeSession: { fatalError("cancelled invocation started transport") })
        let operation = Task { _ = try await cli.run("accept-invite") }
        operation.cancel()
        do { _ = try await operation.value; Issue.record("Cancellation was ignored") }
        catch { #expect(error is CancellationError) }
        #expect(await store.reads == 0)
    }
}
