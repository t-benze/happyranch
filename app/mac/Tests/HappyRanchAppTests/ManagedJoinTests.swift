import Testing
import Foundation
import HappyRanchSupervisor
@testable import HappyRanchApp

@Suite("Managed join UI state")
@MainActor
struct ManagedJoinTests {
    @Test("Draft and late health results cannot publish online or enable pairing")
    func draftAndLateHealthAreGated() {
        let model = ManagedJoinModel()
        #expect(model.phase == .draft)
        #expect(model.connectionState == .tailnetNotDetected)
        #expect(!model.canPair)
        model.paired()
        model.healthResult(true)
        model.pairingStarted()
        #expect(model.phase == .draft)
        #expect(!model.custodyReady)
        model.join(key: Data("synthetic".utf8), homeHost: "100.64.0.1", port: "8443")
        #expect(model.bridge == nil)
        #expect(model.phase == .draft)
    }

    @Test("Only authenticated paired health becomes online; loss and denial stay distinct")
    func pairedHealthTransitions() {
        typealias Phase = ManagedJoinModel.Phase
        #expect(Phase.draft.connectionState == .tailnetNotDetected)
        #expect(Phase.joined.connectionState == .offline)
        #expect(Phase.pairing.connectionState == .offline)
        #expect(Phase.paired.connectionState == .offline)
        #expect(Phase.online.connectionState == .online)
        #expect(Phase.reconnecting.connectionState == .reconnecting)
        #expect(Phase.unreachable.connectionState == .pairedUnreachable)
        #expect(Phase.joined.afterHealth(true) == .joined)
        #expect(Phase.pairing.afterHealth(true) == .pairing)
        #expect(Phase.paired.afterHealth(true) == .online)
        #expect(Phase.online.afterHealth(false) == .reconnecting)
        #expect(Phase.reconnecting.afterHealth(false) == .unreachable)
        #expect(Phase.unreachable.afterHealth(true) == .online)
        #expect(Phase.online.afterHealth(false, denied: true) == .accessDenied)
        #expect(Phase.revoked.afterHealth(true) == .revoked)
        #expect(Phase.canceling.afterHealth(true) == .canceling)
        #expect(Phase.interrupted.afterHealth(true) == .interrupted)
    }

    @Test("Every outstanding custody/join/pair/cancel phase freezes actions")
    func duplicateActionPhases() {
        let busy: [ManagedJoinModel.Phase] = [.checkingKeychain, .waitingForKeychain, .joining, .pairing, .paired, .canceling]
        for phase in busy { #expect(phase.busy) }
        let settled: [ManagedJoinModel.Phase] = [.draft, .joined, .online, .reconnecting, .interrupted, .accessDenied, .unreachable, .revoked, .error("fixed")]
        for phase in settled { #expect(!phase.busy) }
    }

    @Test("HTTPS control and home inputs refuse unsafe metadata and zero ports")
    func inputValidation() throws {
        #expect(try ManagedJoinModel.validatedControlURL("https://control.example:8443") == "https://control.example:8443")
        for input in ["http://control.example", "https://user:secret@control.example", "https://control.example?x=y", "https://control.example#fragment", "https://control.example:0", "https://control.example/path", "https://control.example\n"] {
            #expect(throws: ManagedNetworkError.invalidConfiguration) { try ManagedJoinModel.validatedControlURL(input) }
        }
        #expect(ManagedJoinModel.validTarget("100.64.0.1", port: "8443") == 8443)
        #expect(ManagedJoinModel.validTarget("home.tailnet.example", port: "65535") == 65535)
        #expect(ManagedJoinModel.validTarget("fd7a:115c:a1e0::1", port: "8443") == 8443)
        for host in ["", "home/path", "home?token=x", "user@host", "host\n", "host name"] {
            #expect(ManagedJoinModel.validTarget(host, port: "8443") == nil)
        }
        #expect(ManagedJoinModel.validTarget("100.64.0.1", port: "0") == nil)
        #expect(ManagedJoinModel.validTarget("100.64.0.1", port: "65536") == nil)
    }

    @Test("Fixed recovery copy never treats denial, timeout or used key as confirmed revocation")
    func truthfulFailureCopy() {
        #expect(ManagedJoinModel.Phase.accessDenied.message.contains("does not confirm"))
        #expect(ManagedJoinModel.Phase.unreachable.message.contains("does not confirm"))
        #expect(ManagedJoinModel.Phase.interrupted.message.contains("may be consumed"))
        #expect(ManagedJoinModel.Phase.revoked.message.contains("fresh enrollment key and fresh pairing"))
        #expect(ManagedJoinModel.safeMessage(EnrollmentKeyStore.Failure.locked).contains("Unlock"))
        #expect(ManagedJoinModel.safeMessage(EnrollmentKeyStore.Failure.access).contains("nothing sent"))
        #expect(ManagedJoinModel.safeMessage(ManagedNetworkError.backend).contains("Request a new enrollment key"))
        #expect(ManagedJoinModel.safeMessage(ManagedNetworkError.targetNotPeer).contains("current tailnet peer"))
        #expect(ManagedJoinModel.safeMessage(ManagedNetworkError.peerChanged).contains("pair again"))
        #expect(!ManagedJoinModel.safeMessage(NSError(domain: "synthetic-private-value", code: 1)).contains("synthetic-private-value"))
    }

    @Test("Fresh recovery allocates a new profile and carries no enrollment or pairing secret")
    func freshProfileIdentity() throws {
        let first = ManagedJoinProfile.fresh(controlURL: "https://control.example")
        let second = ManagedJoinProfile.fresh(controlURL: first.controlURL)
        #expect(first.uuid != second.uuid)
        #expect(UUID(uuidString: first.uuid) != nil)
        #expect(first.directory.hasSuffix("/ManagedTailnet/" + first.uuid))
        let object = try #require(JSONSerialization.jsonObject(with: JSONEncoder().encode(first)) as? [String: String])
        #expect(Set(object.keys) == ["controlURL", "hostname", "uuid", "phase"])
        #expect(object["phase"] == "draft")
    }

    @Test("DIY remains the default lane with the original blank connect form")
    func diyDefaultKeeper() {
        let delegate = AppDelegate()
        #expect(delegate.clientLane == .diy)
        #expect(delegate.clientHomeHost.isEmpty)
        #expect(delegate.clientHomePort == "8443")
        #expect(delegate.clientPairingCode.isEmpty)
        #expect(!delegate.isConnecting)
        #expect(delegate.clientBridge == nil)
        #expect(delegate.managedJoin.phase == .draft)
    }
}
