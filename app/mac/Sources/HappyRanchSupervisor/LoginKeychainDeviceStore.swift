import Foundation
import Security

/// Thin file-based login-Keychain adapter. Never updates/recreates an existing
/// item, changes an ACL, adds a CA, or uses a data-protection/shared store.
public actor LoginKeychainDeviceStore: SavedHomeCredentialStore {
    public static let service = "ai.happyranch.HappyRanchApp.saved-home.v1"
    public init() {}

    private func login() throws -> SecKeychain {
        let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Keychains/login.keychain-db").path
        var keychain: SecKeychain?
        let status = SecKeychainOpen(path, &keychain)
        guard status == errSecSuccess, let keychain else { throw failure(status) }
        return keychain
    }

    private func query(_ login: SecKeychain, _ binding: SavedDeviceBinding) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: Self.service, kSecAttrAccount as String: binding.account,
         kSecAttrSynchronizable as String: false, kSecMatchSearchList as String: [login]]
    }

    private func failure(_ status: OSStatus) -> SavedAccessFailure {
        switch status {
        case errSecItemNotFound: return .notFound
        case errSecUserCanceled: return .cancelled
        case errSecAuthFailed: return .denied
        case errSecInteractionNotAllowed, errSecNotAvailable: return .locked
        case errSecDuplicateItem: return .identityChanged
        default: return .unavailable
        }
    }

    private func executable() throws -> String {
        guard let path = Bundle.main.executableURL?.resolvingSymlinksInPath().path else { throw SavedAccessFailure.identityChanged }
        return path
    }

    private func trustedApplication() throws -> SecTrustedApplication {
        var app: SecTrustedApplication?
        let status = try executable().withCString { SecTrustedApplicationCreateFromPath($0, &app) }
        guard status == errSecSuccess, let app else { throw SavedAccessFailure.identityChanged }
        return app
    }

    private func access() throws -> SecAccess {
        let app = try trustedApplication()
        var access: SecAccess?
        guard SecAccessCreate("HappyRanch saved home access" as CFString, [app] as CFArray, &access) == errSecSuccess,
              let access else { throw SavedAccessFailure.saveFailed }
        return access
    }

    /// Inspect the decrypt ACL without modifying it. Empty/nil lists, multiple
    /// trusted apps, foreign paths and changed executable identities all refuse.
    private func validateACL(_ item: SecKeychainItem) throws {
        var access: SecAccess?
        guard SecKeychainItemCopyAccess(item, &access) == errSecSuccess, let access,
              let acls = SecAccessCopyMatchingACLList(access, kSecACLAuthorizationDecrypt) as? [SecACL],
              !acls.isEmpty else { throw SavedAccessFailure.identityChanged }
        // Compare the public opaque application data to a freshly constructed
        // current-executable reference; do not depend on its undocumented encoding.
        // Keychain itself verifies the code identity during the consent-enabled read.
        var currentData: CFData?
        guard SecTrustedApplicationCopyData(try trustedApplication(), &currentData) == errSecSuccess,
              let currentData else { throw SavedAccessFailure.identityChanged }
        for acl in acls {
            var applications: CFArray?
            var label: CFString?
            var prompt = SecKeychainPromptSelector()
            guard SecACLCopyContents(acl, &applications, &label, &prompt) == errSecSuccess,
                  let apps = applications as? [SecTrustedApplication], apps.count == 1 else { throw SavedAccessFailure.identityChanged }
            var appData: CFData?
            guard SecTrustedApplicationCopyData(apps[0], &appData) == errSecSuccess, let appData else { throw SavedAccessFailure.identityChanged }
            guard appData as Data == currentData as Data else {
                throw SavedAccessFailure.identityChanged
            }
        }
    }

    private func item(_ login: SecKeychain, _ binding: SavedDeviceBinding) throws -> SecKeychainItem {
        var request = query(login, binding)
        request[kSecReturnRef as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        guard status == errSecSuccess, let result else { throw failure(status) }
        guard CFGetTypeID(result) == SecKeychainItemGetTypeID() else { throw SavedAccessFailure.invalidRecord }
        return unsafeBitCast(result, to: SecKeychainItem.self)
    }

    public func save(_ credential: SavedDeviceCredential) async throws {
        let keychain = try login()
        // Create only. Duplicate item returns to explicit replacement consent in
        // the UI; never delete/update the old item as an implicit save retry.
        var bytes = try JSONEncoder().encode(credential)
        defer { bytes.resetBytes(in: 0..<bytes.count) }
        let add: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service, kSecAttrAccount as String: credential.binding.account,
            kSecAttrSynchronizable as String: false, kSecUseKeychain as String: keychain,
            kSecAttrAccess as String: try access(), kSecValueData as String: bytes,
        ]
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess || status == errSecDuplicateItem else { throw failure(status) }
        // Read back the real item and enforce its actual binding and ACL.
        let saved = try await load(binding: credential.binding, trust: credential.trust)
        let sameBearer = saved.withBearer { old in credential.withBearer { old == $0 } }
        guard sameBearer, saved.networkProfileID == credential.networkProfileID else { throw SavedAccessFailure.identityChanged }
    }

    public func load(binding: SavedDeviceBinding, trust: SavedHomeTrust) async throws -> SavedDeviceCredential {
        let keychain = try login()
        let ref = try item(keychain, binding)
        return try read(ref).validated(binding: binding, trust: trust)
    }

    public func savedHomes() async throws -> [SavedDeviceCredential] {
        let keychain = try login()
        let request: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service, kSecAttrSynchronizable as String: false,
            kSecMatchSearchList as String: [keychain], kSecMatchLimit as String: kSecMatchLimitAll,
            kSecReturnRef as String: true]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess, let refs = result as? [SecKeychainItem], refs.count <= 32 else {
            throw failure(status)
        }
        // No partial discovery: a denied/changed item must remain visibly recoverable.
        return try refs.map { ref in
            let credential = try read(ref)
            return try credential.validated(binding: credential.binding, trust: credential.trust)
        }
    }

    private func read(_ ref: SecKeychainItem) throws -> SavedDeviceCredential {
        var request: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                     kSecMatchItemList as String: [ref], kSecReturnData as String: true,
                                     kSecMatchLimit as String: kSecMatchLimitOne]
        // No interaction-fail/skip flag: normal consent/unlock UI remains enabled.
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        request.removeAll()
        guard status == errSecSuccess else { throw failure(status) }
        guard var bytes = result as? Data, bytes.count <= 16384 else { throw SavedAccessFailure.invalidRecord }
        result = nil
        defer { bytes.resetBytes(in: 0..<bytes.count) }
        // Read first to allow the OS's ordinary consent flow after an update;
        // never return material until the resulting exact ACL is validated.
        try validateACL(ref)
        do {
            return try JSONDecoder().decode(SavedDeviceCredential.self, from: bytes)
        } catch let error as SavedAccessFailure { throw error }
        catch { throw SavedAccessFailure.invalidRecord }
    }

    public func delete(binding: SavedDeviceBinding) async throws {
        let keychain = try login()
        let ref: SecKeychainItem
        do { ref = try item(keychain, binding) }
        catch SavedAccessFailure.notFound { return }
        let status = SecItemDelete([kSecClass as String: kSecClassGenericPassword,
                                    kSecMatchItemList as String: [ref]] as CFDictionary)
        guard status == errSecSuccess else { throw SavedAccessFailure.deletionFailed }
        do { _ = try item(keychain, binding); throw SavedAccessFailure.deletionFailed }
        catch SavedAccessFailure.notFound { return }
        catch { throw SavedAccessFailure.deletionFailed }
    }
}
