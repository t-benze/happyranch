import Testing
@testable import HappyRanchSupervisor

@Suite("DiagnosticsRedactor")
struct DiagnosticsRedactorTests {

    @Test("redacts bearer token from string")
    func redactsBearerToken() {
        let input = "Authorization: Bearer abc123def456ghi789"
        let redacted = DiagnosticsRedactor.redact(input)
        #expect(!redacted.contains("abc123def456ghi789"))
        #expect(redacted.contains("[REDACTED]"))
    }

    @Test("redacts daemon.token file content")
    func redactsTokenFileContent() {
        let input = "token: hr_token_verysecretvalue12345"
        let redacted = DiagnosticsRedactor.redact(input)
        #expect(!redacted.contains("verysecretvalue12345"))
        #expect(redacted.contains("[REDACTED]"))
    }

    @Test("redacts allow-rules patterns")
    func redactsAllowRules() {
        let input = """
        allowed_tools: ["bash", "write"]
        allow_rules:
          - pattern: "secret-api-key-abcdef"
        """
        let redacted = DiagnosticsRedactor.redact(input)
        #expect(!redacted.contains("secret-api-key-abcdef"))
    }

    @Test("preserves non-sensitive information")
    func preservesNonSensitive() {
        let input = """
        Daemon PID: 12345
        Port: 9876
        Bind host: 127.0.0.1
        Version: 1.2.3
        """
        let redacted = DiagnosticsRedactor.redact(input)
        #expect(redacted.contains("PID: 12345"))
        #expect(redacted.contains("Port: 9876"))
        #expect(redacted.contains("127.0.0.1"))
        #expect(redacted.contains("Version: 1.2.3"))
    }

    @Test("redacts from multiline log content")
    func redactsMultilineLog() {
        let input = """
        2026-01-01 INFO Starting daemon on port 8888
        2026-01-01 DEBUG Bearer token: xyz-secret-abc
        2026-01-01 INFO Health check passed
        """
        let redacted = DiagnosticsRedactor.redact(input)
        #expect(!redacted.contains("xyz-secret-abc"))
        #expect(redacted.contains("[REDACTED]"))
        #expect(redacted.contains("Health check passed"))
    }

    @Test("redacts token from multiple occurrences")
    func redactsMultipleOccurrences() {
        let input = "token=secret1 and also token=secret1 again"
        let redacted = DiagnosticsRedactor.redact(input)
        let redactedCount = redacted.components(separatedBy: "[REDACTED]").count - 1
        #expect(redactedCount == 2)
    }

    @Test("no-op on clean input")
    func noOpOnCleanInput() {
        let input = "System information: all clear"
        let redacted = DiagnosticsRedactor.redact(input)
        #expect(redacted == input)
    }
}
