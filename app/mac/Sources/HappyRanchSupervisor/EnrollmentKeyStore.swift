import Foundation
import Security
import Darwin

/// Serialized file-based login-Keychain custody. No Data Protection store,
/// custom ACL, access group, accessibility class or fallback store is used.
public actor EnrollmentKeyStore {
    public static let service = "ai.happyranch.HappyRanchApp.managed-enrollment"

    public enum Failure: Error, Equatable, Sendable, LocalizedError {
        case locked, access, cleanup, invalidKey

        public var errorDescription: String? {
            switch self {
            case .locked: return "Login Keychain unavailable. Unlock it and try again; nothing sent."
            case .access: return "Keychain access failed; nothing sent. Resolve access and try again."
            case .cleanup: return "Keychain cleanup failed; nothing sent. Resolve access and try again."
            case .invalidKey: return "Enter a nonempty enrollment key without control characters."
            }
        }
    }

    public init() {}

    private func login() throws -> SecKeychain {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Keychains/login.keychain-db").path
        var keychain: SecKeychain?
        guard SecKeychainOpen(path, &keychain) == errSecSuccess, let keychain else { throw Failure.locked }
        var status: SecKeychainStatus = 0
        guard SecKeychainGetStatus(keychain, &status) == errSecSuccess,
              status & SecKeychainStatus(kSecUnlockStateStatus) != 0 else { throw Failure.locked }
        return keychain
    }

    private func query(_ login: SecKeychain, account: String? = nil) -> [String: Any] {
        var value: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrSynchronizable as String: false,
            kSecMatchSearchList as String: [login],
        ]
        if let account { value[kSecAttrAccount as String] = account }
        return value
    }

    private func accounts(_ login: SecKeychain) throws -> [String] {
        var request = query(login)
        request[kSecReturnAttributes as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitAll
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess, let rows = result as? [[String: Any]] else { throw Failure.cleanup }
        return try rows.map {
            guard let service = $0[kSecAttrService as String] as? String, service == Self.service,
                  let account = $0[kSecAttrAccount as String] as? String else { throw Failure.cleanup }
            return account
        }
    }

    /// Metadata only: delete each exact account, then confirm the service is absent.
    /// Failure gates managed actions; never assume a broad Delete removed all items.
    public func removeStaleItems() throws {
        let keychain = try login()
        for account in try accounts(keychain) {
            let status = SecItemDelete(query(keychain, account: account) as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw Failure.cleanup }
        }
        guard try accounts(keychain).isEmpty else { throw Failure.cleanup }
    }

    /// Stages once and returns owned C bytes ONLY after successful exact deletion.
    /// The caller clears its SecureField before awaiting this operation. Cancellation
    /// does not interrupt Security calls: their result and deletion settle first.
    public func stageAndTake(_ input: Data) throws -> ManagedEnrollmentKey {
        var bytes = input
        defer { bytes.resetBytes(in: 0..<bytes.count) }
        guard !bytes.isEmpty, bytes.allSatisfy({ $0 >= 32 && $0 != 127 }) else { throw Failure.invalidKey }
        let keychain = try login()
        // Every submission settles prior custody before staging another item.
        try removeStaleItems()
        let account = UUID().uuidString
        let exact = query(keychain, account: account)
        var add: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: false,
            kSecUseKeychain as String: keychain,
            kSecValueData as String: bytes,
        ]
        let added = SecItemAdd(add as CFDictionary, nil)
        add.removeAll()
        guard added == errSecSuccess else {
            _ = SecItemDelete(exact as CFDictionary)
            throw Failure.access
        }
        // All read/allocation failures still attempt exact cleanup. A later action
        // must pass removeStaleItems again if that cleanup itself failed.
        var deletionAttempted = false
        defer { if !deletionAttempted { _ = SecItemDelete(exact as CFDictionary) } }
        var read = exact
        read[kSecReturnData as String] = true
        read[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(read as CFDictionary, &result) == errSecSuccess,
              var data = result as? Data else { throw Failure.access }
        result = nil
        defer { data.resetBytes(in: 0..<data.count) }
        guard !data.isEmpty, data.allSatisfy({ $0 >= 32 && $0 != 127 }),
              let allocation = calloc(data.count + 1, 1) else { throw Failure.access }
        data.copyBytes(to: allocation.assumingMemoryBound(to: UInt8.self), count: data.count)
        let key = ManagedEnrollmentKey(taking: allocation, byteCount: data.count + 1)
        deletionAttempted = true
        guard SecItemDelete(exact as CFDictionary) == errSecSuccess else {
            key.discard()
            throw Failure.cleanup
        }
        // Confirm absence before handing anything to configure/init/Up.
        var absent: CFTypeRef?
        guard SecItemCopyMatching(exact as CFDictionary, &absent) == errSecItemNotFound else {
            key.discard()
            throw Failure.cleanup
        }
        return key
    }
}
