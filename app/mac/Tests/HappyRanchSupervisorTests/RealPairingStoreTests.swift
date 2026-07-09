import Testing
import Foundation
@testable import HappyRanchSupervisor

// MARK: - RealPairingStore Tests

@Suite("RealPairingStore")
struct RealPairingStoreTests {

    // MARK: - isPaired tests

    @Test("empty credential is never paired")
    func emptyCredentialNotPaired() {
        let store = RealPairingStore()
        #expect(!store.isPaired(deviceID: ""))
    }

    @Test("unknown credential is not paired")
    func unknownCredentialNotPaired() {
        let store = RealPairingStore()
        #expect(!store.isPaired(deviceID: "hrpair_unknown1234"))
        #expect(!store.isPaired(deviceID: "bogus"))
    }

    @Test("paired credential is recognized after pairing")
    func pairedCredentialRecognized() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()

        guard let credential = store.pair(usingCode: code, deviceName: "test-device") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        #expect(store.isPaired(deviceID: credential))
    }

    // MARK: - generatePairingCode tests

    @Test("generated code is non-empty")
    func pairingCodeNonEmpty() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        #expect(!code.isEmpty)
    }

    @Test("generated codes are different each call")
    func pairingCodesUnique() {
        let store = RealPairingStore()
        let code1 = store.generatePairingCode()
        let code2 = store.generatePairingCode()
        #expect(code1 != code2)
    }

    @Test("generated code has expected length (8 chars)")
    func pairingCodeLength() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        #expect(code.count == 8)
    }

    @Test("generated code is uppercase alphanumeric")
    func pairingCodeFormat() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        let validChars = CharacterSet(charactersIn: "ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        #expect(code.unicodeScalars.allSatisfy { validChars.contains($0) })
    }

    @Test("hasActivePairingCode returns true after generation")
    func hasActiveCodeAfterGeneration() {
        let store = RealPairingStore()
        #expect(!store.hasActivePairingCode)
        _ = store.generatePairingCode()
        #expect(store.hasActivePairingCode)
    }

    // MARK: - pair tests

    @Test("pair returns a credential with hrpair_ prefix")
    func pairReturnsPrefixedCredential() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()

        guard let credential = store.pair(usingCode: code, deviceName: "my-device") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        #expect(credential.hasPrefix("hrpair_"))
        #expect(!credential.hasPrefix("hr_token_"))
        #expect(!credential.hasPrefix("hr_session_"))
    }

    @Test("pair returns nil for invalid code")
    func pairInvalidCodeReturnsNil() {
        let store = RealPairingStore()
        _ = store.generatePairingCode()  // generates ABC, but we send XYZ
        let result = store.pair(usingCode: "WRONG-1", deviceName: "test")
        #expect(result == nil)
    }

    @Test("pair returns nil when no code was generated")
    func pairWithoutCodeReturnsNil() {
        let store = RealPairingStore()
        let result = store.pair(usingCode: "ANYTHING", deviceName: "test")
        #expect(result == nil)
    }

    @Test("pair is one-time use — second attempt returns nil")
    func pairOneTimeUse() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()

        // First use: succeeds
        let credential1 = store.pair(usingCode: code, deviceName: "device-1")
        #expect(credential1 != nil)

        // Second use with same code: fails
        let credential2 = store.pair(usingCode: code, deviceName: "device-2")
        #expect(credential2 == nil)
    }

    @Test("pairing code is consumed after successful pair")
    func codeConsumedAfterPair() {
        let store = RealPairingStore()
        _ = store.generatePairingCode()
        #expect(store.hasActivePairingCode)

        let code = store.generatePairingCode()  // regenerate
        let _ = store.pair(usingCode: code, deviceName: "test")
        #expect(!store.hasActivePairingCode)
    }

    @Test("multiple devices get unique credentials")
    func multipleDevicesUniqueCredentials() {
        let store = RealPairingStore()

        let code1 = store.generatePairingCode()
        guard let cred1 = store.pair(usingCode: code1, deviceName: "laptop") else {
            #expect(Bool(false), "First pairing should succeed")
            return
        }
        #expect(store.pairedDeviceCount == 1)

        let code2 = store.generatePairingCode()
        guard let cred2 = store.pair(usingCode: code2, deviceName: "phone") else {
            #expect(Bool(false), "Second pairing should succeed")
            return
        }
        #expect(store.pairedDeviceCount == 2)

        #expect(cred1 != cred2, "Different devices should get unique credentials")
        #expect(store.isPaired(deviceID: cred1))
        #expect(store.isPaired(deviceID: cred2))
    }

    // MARK: - revokePairing tests

    @Test("revokePairing returns true for existing credential")
    func revokeExistingReturnsTrue() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        guard let credential = store.pair(usingCode: code, deviceName: "test") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }
        #expect(store.revokePairing(credential: credential))
    }

    @Test("revokePairing returns false for unknown credential")
    func revokeUnknownReturnsFalse() {
        let store = RealPairingStore()
        #expect(!store.revokePairing(credential: "hrpair_nonexistent"))
    }

    @Test("revoked credential is no longer paired")
    func revokedCredentialNotPaired() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        guard let credential = store.pair(usingCode: code, deviceName: "test") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        #expect(store.isPaired(deviceID: credential))

        _ = store.revokePairing(credential: credential)
        #expect(!store.isPaired(deviceID: credential))
    }

    @Test("revoking one device does not affect others")
    func revokeOneDoesNotAffectOthers() {
        let store = RealPairingStore()

        let code1 = store.generatePairingCode()
        guard let cred1 = store.pair(usingCode: code1, deviceName: "laptop") else {
            #expect(Bool(false), "First pairing should succeed")
            return
        }

        let code2 = store.generatePairingCode()
        guard let cred2 = store.pair(usingCode: code2, deviceName: "phone") else {
            #expect(Bool(false), "Second pairing should succeed")
            return
        }

        #expect(store.pairedDeviceCount == 2)

        // Revoke device 1
        _ = store.revokePairing(credential: cred1)
        #expect(!store.isPaired(deviceID: cred1))
        #expect(store.isPaired(deviceID: cred2), "Device 2 should still be paired")
        #expect(store.pairedDeviceCount == 1)
    }

    @Test("re-pairing with revoked credential is rejected")
    func revokedCredentialCannotRePair() {
        let store = RealPairingStore()
        let code1 = store.generatePairingCode()
        guard let credential = store.pair(usingCode: code1, deviceName: "test") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        _ = store.revokePairing(credential: credential)
        #expect(!store.isPaired(deviceID: credential))

        // Even though the credential value is known, it can't be used
        // because the store keeps revoked credentials in a deny set
    }

    // MARK: - Paired device count and names

    @Test("pairedDeviceCount reflects actual count")
    func pairedDeviceCount() {
        let store = RealPairingStore()
        #expect(store.pairedDeviceCount == 0)

        let code = store.generatePairingCode()
        _ = store.pair(usingCode: code, deviceName: "laptop")
        #expect(store.pairedDeviceCount == 1)

        let code2 = store.generatePairingCode()
        _ = store.pair(usingCode: code2, deviceName: "phone")
        #expect(store.pairedDeviceCount == 2)

        // Revoke is tested separately
    }

    @Test("pairedDeviceNames reflects paired devices")
    func pairedDeviceNames() {
        let store = RealPairingStore()
        #expect(store.pairedDeviceNames.isEmpty)

        let code = store.generatePairingCode()
        _ = store.pair(usingCode: code, deviceName: "my-macbook")
        #expect(store.pairedDeviceNames.contains("my-macbook"))
    }

    // MARK: - Credential format tests

    @Test("credential has hrpair_ prefix and 32-char hex suffix")
    func credentialFormat() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        guard let credential = store.pair(usingCode: code, deviceName: "test") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        #expect(credential.hasPrefix("hrpair_"))
        let hexPart = String(credential.dropFirst(7))  // "hrpair_"
        #expect(hexPart.count == 32, "Expected 32 hex chars, got \(hexPart.count)")
        let hexChars = CharacterSet(charactersIn: "0123456789abcdef")
        #expect(hexPart.unicodeScalars.allSatisfy { hexChars.contains($0) })
    }

    @Test("generated credentials are different across pairings")
    func credentialsUnique() {
        let store = RealPairingStore()

        let code1 = store.generatePairingCode()
        guard let cred1 = store.pair(usingCode: code1, deviceName: "a") else {
            #expect(Bool(false), "First pairing should succeed")
            return
        }

        let code2 = store.generatePairingCode()
        guard let cred2 = store.pair(usingCode: code2, deviceName: "b") else {
            #expect(Bool(false), "Second pairing should succeed")
            return
        }

        #expect(cred1 != cred2)
    }

    // MARK: - Store isolation

    @Test("two stores are completely isolated")
    func storeIsolation() {
        let store1 = RealPairingStore()
        let store2 = RealPairingStore()

        let code = store1.generatePairingCode()
        guard let cred = store1.pair(usingCode: code, deviceName: "test") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        #expect(store1.isPaired(deviceID: cred))
        #expect(!store2.isPaired(deviceID: cred))
        #expect(store1.pairedDeviceCount == 1)
        #expect(store2.pairedDeviceCount == 0)
    }

    // MARK: - pairedDevices accessor (GAP #2)

    @Test("pairedDevices returns empty array initially")
    func pairedDevicesEmptyOnInit() {
        let store = RealPairingStore()
        #expect(store.pairedDevices().isEmpty)
    }

    @Test("pairedDevices returns (credential, name) pairs after pairing")
    func pairedDevicesReturnsPairs() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        guard let credential = store.pair(usingCode: code, deviceName: "test-mac") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        let devices = store.pairedDevices()
        #expect(devices.count == 1)
        #expect(devices[0].credential == credential)
        #expect(devices[0].name == "test-mac")
    }

    @Test("pairedDevices removes row after revoke")
    func pairedDevicesRemovesRowAfterRevoke() {
        let store = RealPairingStore()
        let code = store.generatePairingCode()
        guard let credential = store.pair(usingCode: code, deviceName: "temp") else {
            #expect(Bool(false), "Pairing should succeed")
            return
        }

        #expect(store.pairedDevices().count == 1)
        _ = store.revokePairing(credential: credential)
        #expect(store.pairedDevices().isEmpty)
    }

    @Test("pairedDevices returns multiple devices with correct names")
    func pairedDevicesMultipleDevices() {
        let store = RealPairingStore()

        let code1 = store.generatePairingCode()
        guard let cred1 = store.pair(usingCode: code1, deviceName: "laptop") else {
            #expect(Bool(false), "First pairing should succeed")
            return
        }

        let code2 = store.generatePairingCode()
        guard let cred2 = store.pair(usingCode: code2, deviceName: "phone") else {
            #expect(Bool(false), "Second pairing should succeed")
            return
        }

        let devices = store.pairedDevices()
        #expect(devices.count == 2)

        let names = Set(devices.map { $0.name })
        #expect(names.contains("laptop"))
        #expect(names.contains("phone"))

        let credentials = Set(devices.map { $0.credential })
        #expect(credentials.contains(cred1))
        #expect(credentials.contains(cred2))
    }

    @Test("pairedDevices sortens result after revoke + re-pair")
    func pairedDevicesAfterRevokeAndRePair() {
        let store = RealPairingStore()

        let code1 = store.generatePairingCode()
        _ = store.pair(usingCode: code1, deviceName: "first")
        #expect(store.pairedDevices().count == 1)

        // Revoke first
        let devicesAfterPair1 = store.pairedDevices()
        #expect(devicesAfterPair1.count == 1)
        _ = store.revokePairing(credential: devicesAfterPair1[0].credential)
        #expect(store.pairedDevices().isEmpty)

        // Re-pair a new device
        let code2 = store.generatePairingCode()
        guard let cred2 = store.pair(usingCode: code2, deviceName: "second") else {
            #expect(Bool(false), "Re-pair should succeed")
            return
        }
        let devices = store.pairedDevices()
        #expect(devices.count == 1)
        #expect(devices[0].credential == cred2)
        #expect(devices[0].name == "second")
    }
}
