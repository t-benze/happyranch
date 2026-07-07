import Foundation

// MARK: - DaemonPathNormalizer

/// Canonical daemon-path normalizer used by BOTH ClientBridge and HomeConnector.
///
/// Recognizes the ``/api/v1`` prefix (and any versioned prefix like ``/api/v2``)
/// so all path matching is prefix-agnostic.
///
/// ## Motivation (reviewer FINDING 1 — CRITICAL)
///
/// The SPA bootstraps via `GET /api/v1/auth/bootstrap` (web/src/lib/auth.ts:10),
/// but ClientBridge.swift:251 only intercepts the UNPREFIXED `/auth/bootstrap`.
/// Without prefix-agnostic matching, the prefixed request is forwarded to the
/// home connector, which injects the real daemon bearer and relays the raw-token
/// bootstrap response back over the tailnet — a CRITICAL token leak.
///
/// ## Usage
///
/// Call ``stripApiPrefix(_:)`` before any path comparison.  The returned
/// ``StrippedPath`` carries both the unprefixed path and the matched version,
/// so callers can also reconstruct prefixed variants if needed.
public enum DaemonPathNormalizer {

    /// Regex matching `/api/v<number>` prefixes.
    /// Example matches: `/api/v1`, `/api/v2`, `/api/v42`
    private static let apiPrefixPattern = try! NSRegularExpression(
        pattern: "^/api/v\\d+",
        options: []
    )

    /// Regex matching `/orgs/<slug>` segment that follows the API prefix.
    /// Example: `/orgs/happyranch`, `/orgs/my-org`
    private static let orgsPrefixPattern = try! NSRegularExpression(
        pattern: "^/orgs/[^/]+",
        options: []
    )

    // MARK: - Strip prefix

    /// Strip the `/api/vN` prefix AND `/orgs/{slug}` segment from a path if present.
    ///
    /// - Parameter path: A URL path, e.g. `/api/v1/orgs/happyranch/tasks` or `/api/v1/auth/bootstrap`.
    /// - Returns: The daemon-internal path without versioned API or org slug prefixes.
    ///
    /// ```swift
    /// DaemonPathNormalizer.stripApiPrefix("/api/v1/orgs/happyranch/tasks")
    /// // → "/tasks"
    /// DaemonPathNormalizer.stripApiPrefix("/api/v1/auth/bootstrap")
    /// // → "/auth/bootstrap"
    /// DaemonPathNormalizer.stripApiPrefix("/api/v2/orgs/my-org/tasks/123")
    /// // → "/tasks/123"
    /// ```
    public static func stripApiPrefix(_ path: String) -> String {
        var result = path
        let nsPath = result as NSString

        // Strip /api/vN
        let range = NSRange(location: 0, length: nsPath.length)
        if let match = apiPrefixPattern.firstMatch(in: result, options: [.anchored], range: range) {
            result = nsPath.substring(from: match.range.length)
        }

        // Strip /orgs/{slug} if it follows immediately
        let nsResult = result as NSString
        let orgsRange = NSRange(location: 0, length: nsResult.length)
        if let match = orgsPrefixPattern.firstMatch(in: result, options: [.anchored], range: orgsRange) {
            result = nsResult.substring(from: match.range.length)
        }

        return result
    }

    // MARK: - Both forms

    /// Return both the prefixed and unprefixed forms of a path for deny-gate matching.
    ///
    /// - Parameter path: A known unprefixed daemon path, e.g. `/auth/bootstrap`.
    /// - Returns: An array containing the unprefixed form and the `/api/v1`-prefixed form.
    ///
    /// Example:
    /// ```swift
    /// DaemonPathNormalizer.bothForms("/auth/bootstrap")
    /// // → ["/auth/bootstrap", "/api/v1/auth/bootstrap"]
    /// ```
    public static func bothForms(_ unprefixedPath: String) -> [String] {
        [unprefixedPath, "/api/v1\(unprefixedPath)"]
    }
}
