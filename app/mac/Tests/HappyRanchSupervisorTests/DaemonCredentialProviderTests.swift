import Testing
import Foundation
@testable import HappyRanchSupervisor

@Suite("DaemonCredentialProvider protocol conformance")
struct DaemonCredentialProviderProtocolTests {

    @Test("LocalTokenCredentialProvider conforms to DaemonCredentialProvider")
    func conformsToProtocol() {
        let provider = LocalTokenCredentialProvider(homeDir: "/tmp/test")
        // Type check — compiles only if conformance holds
        let _: any DaemonCredentialProvider = provider
        #expect(Bool(true))
    }
}

@Suite("LocalTokenCredentialProvider")
struct LocalTokenCredentialProviderTests {

    // MARK: - Token reading

    @Test("reads a valid token from daemon.token file")
    func readsValidToken() throws {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let tokenFile = tempDir.appendingPathComponent("daemon.token")
        try "hr_token_test123abc".write(to: tokenFile, atomically: true, encoding: .utf8)

        let provider = LocalTokenCredentialProvider(homeDir: tempDir.path)
        let token = try provider.credential()

        #expect(token == "hr_token_test123abc")
    }

    @Test("reads token with trailing whitespace")
    func readsTokenWithWhitespace() throws {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let tokenFile = tempDir.appendingPathComponent("daemon.token")
        try "  hr_token_whitespace  \n\n".write(to: tokenFile, atomically: true, encoding: .utf8)

        let provider = LocalTokenCredentialProvider(homeDir: tempDir.path)
        let token = try provider.credential()

        #expect(token == "hr_token_whitespace")
    }

    @Test("throws tokenFileNotFound when daemon.token does not exist")
    func throwsWhenTokenNotFound() {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hr-test-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let provider = LocalTokenCredentialProvider(homeDir: tempDir.path)

        do {
            _ = try provider.credential()
            #expect(Bool(false), "Expected error but got none")
        } catch DaemonCredentialProviderError.tokenFileNotFound(let path) {
            #expect(path.contains("daemon.token"))
        } catch {
            #expect(Bool(false), "Unexpected error: \(error)")
        }
    }

    @Test("throws tokenFileEmpty when daemon.token is empty")
    func throwsWhenTokenEmpty() throws {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let tokenFile = tempDir.appendingPathComponent("daemon.token")
        try "".write(to: tokenFile, atomically: true, encoding: .utf8)

        let provider = LocalTokenCredentialProvider(homeDir: tempDir.path)

        do {
            _ = try provider.credential()
            #expect(Bool(false), "Expected error but got none")
        } catch DaemonCredentialProviderError.tokenFileEmpty {
            // Expected
        } catch {
            #expect(Bool(false), "Unexpected error: \(error)")
        }
    }

    @Test("throws tokenFileEmpty when daemon.token contains only whitespace")
    func throwsWhenTokenOnlyWhitespace() throws {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let tokenFile = tempDir.appendingPathComponent("daemon.token")
        try "   \n  \n  ".write(to: tokenFile, atomically: true, encoding: .utf8)

        let provider = LocalTokenCredentialProvider(homeDir: tempDir.path)

        do {
            _ = try provider.credential()
            #expect(Bool(false), "Expected error but got none")
        } catch DaemonCredentialProviderError.tokenFileEmpty {
            // Expected
        } catch {
            #expect(Bool(false), "Unexpected error: \(error)")
        }
    }

    @Test("credential is read fresh on each call (no caching)")
    func credentialIsReadFresh() throws {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let tokenFile = tempDir.appendingPathComponent("daemon.token")
        try "hr_token_first".write(to: tokenFile, atomically: true, encoding: .utf8)

        let provider = LocalTokenCredentialProvider(homeDir: tempDir.path)

        let first = try provider.credential()
        #expect(first == "hr_token_first")

        // Change the token on disk (simulates daemon restart with fresh token)
        try "hr_token_second".write(to: tokenFile, atomically: true, encoding: .utf8)

        let second = try provider.credential()
        #expect(second == "hr_token_second")
        #expect(first != second)
    }
}
