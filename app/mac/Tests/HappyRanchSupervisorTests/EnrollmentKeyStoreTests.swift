import Testing
import Foundation
import Security
@testable import HappyRanchSupervisor

/// Run in a disposable macOS login session with an unlocked login Keychain,
/// no app running, and no existing managed-enrollment items. Never lock or
/// change the ACL/search list of a developer's real Keychain in an automated test.
@Suite("Login Keychain enrollment custody", .serialized)
struct EnrollmentKeyStoreTests {
    private func login() throws -> SecKeychain {
        var keychain: SecKeychain?
        let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Keychains/login.keychain-db").path
        #expect(SecKeychainOpen(path, &keychain) == errSecSuccess)
        return try #require(keychain)
    }
    private func query(_ login: SecKeychain, account: String? = nil) -> [String: Any] {
        var value: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: EnrollmentKeyStore.service,
            kSecAttrSynchronizable as String: false, kSecMatchSearchList as String: [login]]
        if let account { value[kSecAttrAccount as String] = account }
        return value
    }
    private func requireEmpty(_ login: SecKeychain) throws {
        var result: CFTypeRef?
        try #require(SecItemCopyMatching(query(login) as CFDictionary, &result) == errSecItemNotFound,
                     "Disposable login session must have no pre-existing app enrollment items")
    }

    @Test("Returned owned key is usable only after the real login item is absent, then single-consume")
    func deletionBeforeHandoff() async throws {
        let login = try login()
        try requireEmpty(login)
        let store = EnrollmentKeyStore()
        let key = try await store.stageAndTake(Data("synthetic-disposable-enrollment".utf8))
        try requireEmpty(login)
        let value = try key.consume { String(cString: $0) }
        #expect(value == "synthetic-disposable-enrollment")
        #expect(throws: ManagedNetworkError.invalidConfiguration) { try key.consume { _ in } }
    }

    @Test("Stale cleanup deletes every exact synthetic account without reading secrets")
    func staleMultipleAccounts() async throws {
        let login = try login()
        try requireEmpty(login)
        let accounts = [UUID().uuidString, UUID().uuidString]
        defer { for account in accounts { _ = SecItemDelete(query(login, account: account) as CFDictionary) } }
        for account in accounts {
            let add: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: EnrollmentKeyStore.service, kSecAttrAccount as String: account,
                kSecAttrSynchronizable as String: false, kSecUseKeychain as String: login,
                kSecValueData as String: Data("synthetic-stale".utf8)]
            try #require(SecItemAdd(add as CFDictionary, nil) == errSecSuccess)
        }
        try await EnrollmentKeyStore().removeStaleItems()
        try requireEmpty(login)
    }

    @Test("Cancellation still settles Security custody and leaves no staged item")
    func canceledCustodySettles() async throws {
        let login = try login()
        try requireEmpty(login)
        let store = EnrollmentKeyStore()
        let task = Task { try await store.stageAndTake(Data("synthetic-cancel".utf8)) }
        task.cancel()
        let key = try await task.value
        key.discard()
        try requireEmpty(login)
        #expect(throws: ManagedNetworkError.invalidConfiguration) { try key.consume { _ in } }
    }

    @Test("Invalid enrollment bytes refuse before Keychain access")
    func invalidKeyBytes() async {
        let store = EnrollmentKeyStore()
        for input in [Data(), Data([0]), Data("synthetic\n".utf8)] {
            do { _ = try await store.stageAndTake(input); Issue.record("Invalid key accepted") }
            catch let error as EnrollmentKeyStore.Failure { #expect(error == .invalidKey) }
            catch { Issue.record("Wrong fixed failure category") }
        }
    }
}
