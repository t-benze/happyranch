import XCTest
import Foundation
@testable import HappyRanchSupervisor

/// Hold the real helper launch boundary on the existing injected controller.
/// No native export or production-only test mode is needed.
private final class HeldHelperController: ProcessControlling, @unchecked Sendable {
    let base = FakeProcessController()
    private let lock = NSLock()
    private let released = DispatchSemaphore(value: 0)
    private var entered = false
    var isWaiting: Bool { lock.withLock { entered } }
    func release() { released.signal() }
    func launchPiped(executableURL: URL, arguments: [String], environment: [String: String],
                     input: FileHandle, output: FileHandle) throws -> any ProcessHandle {
        lock.withLock { entered = true }
        guard released.wait(timeout: .now() + 5) == .success else { throw ManagedNetworkError.deadline }
        return try base.launchPiped(executableURL: executableURL, arguments: arguments, environment: environment, input: input, output: output)
    }
    func launch(executableURL: URL, arguments: [String], currentDirectoryURL: URL?, environment: [String: String]?,
                terminationHandler: (@Sendable (any ProcessHandle) -> Void)?) throws -> any ProcessHandle {
        try base.launch(executableURL: executableURL, arguments: arguments, currentDirectoryURL: currentDirectoryURL,
                        environment: environment, terminationHandler: terminationHandler)
    }
}

private actor LegacyJoinResults {
    private(set) var values: [ManagedNetworkError] = []
    func append(_ error: ManagedNetworkError) { values.append(error) }
}

final class ManagedOwnerFenceTests: XCTestCase {
    private func trust(_ home: String) -> SavedHomeTrust {
        SavedHomeTrust(homeID: home, controlPlaneID: "control", connectorID: "connector",
            controlURL: "https://control.happyranch.invalid:28443", dialIP: "192.168.1.40", homeEndpoint: "100.64.0.1:443",
            caDER: Data("synthetic-public-ca".utf8), caSHA256: "synthetic-pin")
    }
    private func bridge() -> ClientBridge {
        ClientBridge(homeConnectorHost: "100.64.0.1", homeConnectorPort: 443, lane: .managed)
    }

    func testDifferentHelperProfilesCoexistAndSameProfileOrHelperRefuses() async throws {
        let a = bridge(), b = bridge(), duplicate = bridge()
        let fakeA = FakeProcessController(), fakeB = FakeProcessController(), fakeDuplicate = FakeProcessController()
        let executable = URL(fileURLWithPath: "/synthetic/helper")
        let helperA = HomeBridgeProcess(controller: fakeA, executable: executable)
        let helperB = HomeBridgeProcess(controller: fakeB, executable: executable)
        let otherHelper = HomeBridgeProcess(controller: fakeDuplicate, executable: executable)
        let profileA = "/synthetic/" + UUID().uuidString, profileB = "/synthetic/" + UUID().uuidString
        try await a.resumeManagedNetwork(controlURL: trust("A").controlURL, hostname: "synthetic-A", stateDirectory: profileA,
                                         homeTrust: trust("A"), helper: helperA)
        do {
            try await b.resumeManagedNetwork(controlURL: trust("B").controlURL, hostname: "synthetic-B", stateDirectory: profileB,
                                             homeTrust: trust("B"), helper: helperB)
            XCTAssertEqual(fakeA.launchCallCount, 1); XCTAssertEqual(fakeB.launchCallCount, 1)
            for (profile, helper) in [(profileA, otherHelper), ("/synthetic/" + UUID().uuidString, helperA)] {
                do {
                    try await duplicate.resumeManagedNetwork(controlURL: trust("A").controlURL, hostname: "synthetic-A", stateDirectory: profile,
                                                             homeTrust: trust("A"), helper: helper)
                    XCTFail("Duplicate node/profile or helper was admitted")
                } catch { XCTAssertEqual(error as? ManagedNetworkError, .busy) }
            }
            XCTAssertEqual(fakeDuplicate.launchCallCount, 0)
            await a.stopManagedNetwork()
            XCTAssertTrue(a.managedStopConfirmed, "B must not keep A's confirmed teardown reserved")
            XCTAssertFalse(b.managedStopConfirmed)
            XCTAssertEqual(fakeB.terminateCallCount, 0)
            await b.stopManagedNetwork()
            XCTAssertTrue(b.managedStopConfirmed)
        } catch {
            await a.stopManagedNetwork(); await b.stopManagedNetwork(); throw error
        }
    }

    func testLegacySingletonStillRefusesSecondOwnerWhileHelperIsIsolated() async throws {
        let controller = HeldHelperController(), helperBridge = bridge()
        let helper = HomeBridgeProcess(controller: controller, executable: URL(fileURLWithPath: "/synthetic/helper"))
        let t = trust("held"), profile = "/synthetic/" + UUID().uuidString
        let helperJoin = Task {
            try await helperBridge.resumeManagedNetwork(controlURL: t.controlURL, hostname: "held", stateDirectory: profile,
                                                        homeTrust: t, helper: helper)
        }
        defer { controller.release() }
        let deadline = Date().addingTimeInterval(3)
        while !controller.isWaiting, Date() < deadline { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(controller.isWaiting)
        let results = LegacyJoinResults()
        // Both legacy inputs are invalid before native init: this cannot dial
        // or start a real engine. One reserves the singleton while queued behind
        // the held helper; the other must refuse before reaching that queue.
        let legacyA = bridge(), legacyB = bridge()
        let joins = [legacyA, legacyB].map { legacy in
            Task {
                do { try await legacy.resumeManagedNetwork(controlURL: "http://invalid.example", hostname: "synthetic", stateDirectory: "/invalid") }
                catch { await results.append((error as? ManagedNetworkError) ?? .unavailable) }
            }
        }
        while await results.values.isEmpty, Date() < deadline { try await Task.sleep(for: .milliseconds(10)) }
        let heldResults = await results.values
        XCTAssertEqual(heldResults, [.busy], "Exactly one legacy attempt must be refused while the first is still reserved")
        controller.release()
        do { try await helperJoin.value } catch {
            for join in joins { await join.value }
            await helperBridge.stopManagedNetwork(); throw error
        }
        for join in joins { await join.value }
        let final = await results.values
        XCTAssertEqual(final.count, 2)
        XCTAssertTrue(final.contains(.busy)); XCTAssertTrue(final.contains(.invalidConfiguration))
        XCTAssertEqual(controller.base.launchCallCount, 1)
        await helperBridge.stopManagedNetwork()
        XCTAssertTrue(helperBridge.managedStopConfirmed)
        XCTAssertTrue(legacyA.managedStopConfirmed && legacyB.managedStopConfirmed)
    }
}
