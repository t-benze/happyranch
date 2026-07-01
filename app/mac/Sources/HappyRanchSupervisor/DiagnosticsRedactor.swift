import Foundation

/// Redacts sensitive information from diagnostic output.
/// Covers: bearer tokens, daemon.token file contents, allow-rules secrets.
public struct DiagnosticsRedactor: Sendable {

    /// Redaction token placed where sensitive data was removed.
    public static let redactedMarker = "[REDACTED]"

    /// Patterns to match for redaction. Each element is a tuple of
    /// (regex pattern, description of what is matched).
    private static let patterns: [(pattern: String, description: String)] = [
        // daemon.token file content (hr_token_ prefix)
        ("hr_token_[A-Za-z0-9_\\-\\.]+", "daemon token"),
        // Bearer tokens: "Bearer <value>" or "bearer <value>" or "Bearer token: <value>"
        ("[Bb]earer\\s+(token:\\s*)?[A-Za-z0-9_\\-\\.]+", "bearer token"),
        // Token refresh/set patterns: "Token refresh: <value>"
        ("[Tt]oken\\s+\\w+:\\s*[A-Za-z0-9_\\-\\.]+", "token refresh"),
        // Generic token=value or token:value assignments
        ("token[=:]\\s*[A-Za-z0-9_\\-\\.]+", "token assignment"),
        // API keys / secret patterns including key-xxx forms
        ("secret[-_]?(api[-_]?)?key[=\\-: ]+[A-Za-z0-9_\\-\\.]+", "API key"),
        // allow-rules patterns with secrets
        ("pattern:\\s*\"[A-Za-z0-9_\\-\\.]+\"", "allow-rules secret"),
    ]

    /// Redact all sensitive patterns from the given string.
    /// Returns the redacted string. Non-sensitive content is preserved.
    public static func redact(_ input: String) -> String {
        var result = input
        for (pattern, _) in patterns {
            if let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive) {
                result = regex.stringByReplacingMatches(
                    in: result,
                    range: NSRange(result.startIndex..., in: result),
                    withTemplate: redactedMarker
                )
            }
        }
        return result
    }

    /// Redacts all values in a dictionary of strings recursively.
    public static func redactStrings(in dict: [String: String]) -> [String: String] {
        dict.mapValues { redact($0) }
    }
}
