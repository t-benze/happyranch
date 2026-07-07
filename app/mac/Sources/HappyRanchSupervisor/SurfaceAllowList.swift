import Foundation

// MARK: - SurfaceAllowList

/// Enforces the remote-surface allow-list as a **deny gate**.
///
/// Remote sessions expose the **normal Web SPA surface only**.
/// The following surfaces are **EXCLUDED** (must be UNREACHABLE remotely):
///
/// - Agent-callback endpoints: `/report-completion`, `/dispatch`,
///   `/threads/{id}/dispatch`, thread `/reply`, `/decline`, `/close-out`
/// - Management endpoints: `/manage-agent`, `/manage-repo`
/// - Memory/learning write endpoints: `/memory/add`, `/memory/update`,
///   `/memory/promote`, `/learning/add`, `/learning/update`, `/learning/promote`
/// - `--as-founder` / TTY override surfaces
/// - Auth bootstrap & registration: `/auth/bootstrap`, `/auth/registration-token`
///
/// Policy: **explicit allow-or-deny**.  Paths on the deny-list are
/// always rejected.  Everything else is allowed (default-allow for
/// the normal SPA surface, with explicit deny for the sensitive paths).
public struct SurfaceAllowList: Sendable {

    /// Paths that are **denied** remotely.
    private let deniedPaths: Set<String>

    /// Path prefixes that are **denied** remotely (prefix-match).
    private let deniedPrefixes: [String]

    // MARK: - Default policy

    /// The default v1 remote-surface allow-list as specified in the THR-034 §4c
    /// design document.
    public static let `default`: SurfaceAllowList = {
        let deniedPaths: Set<String> = [
            // Agent-callback endpoints
            "/report-completion",
            "/dispatch",
            // Thread mutating endpoints
            "/reply",
            "/decline",
            "/close-out",
            // Management endpoints
            "/manage-agent",
            "/manage-repo",
            // Auth bootstrap & registration
            "/auth/bootstrap",
            "/auth/registration-token",
        ]

        let deniedPrefixes: [String] = [
            // Thread dispatch (dynamic ID in path)
            "/threads/",
            // Memory/learning add/update/promote
            "/memory/",
            "/learning/",
            // --as-founder / TTY override surfaces
            "/as-founder",
        ]

        return SurfaceAllowList(deniedPaths: deniedPaths, deniedPrefixes: deniedPrefixes)
    }()

    // MARK: - Init

    /// Create a custom allow-list (for testing).
    ///
    /// - Parameters:
    ///   - deniedPaths: Exact paths that are denied.
    ///   - deniedPrefixes: Path prefixes that are denied (prefix-match).
    public init(deniedPaths: Set<String> = [], deniedPrefixes: [String] = []) {
        self.deniedPaths = deniedPaths
        self.deniedPrefixes = deniedPrefixes
    }

    // MARK: - Gate

    /// Check whether a request path is **allowed** for remote access.
    ///
    /// - Parameter path: The URL path of the incoming request (e.g. `/tasks`).
    /// - Returns: `true` if the path is allowed, `false` if it is denied.
    public func isAllowed(path: String) -> Bool {
        // Normalize: strip trailing slash for exact match (but keep for prefix)
        let normalizedPath = path.hasSuffix("/") && path.count > 1
            ? String(path.dropLast())
            : path

        // 1. Exact-match deny
        if deniedPaths.contains(normalizedPath) {
            return false
        }

        // 2. Prefix-match deny
        for prefix in deniedPrefixes {
            if normalizedPath.hasPrefix(prefix) {
                return false
            }
        }

        // 3. Default-allow for everything else (normal SPA surface)
        return true
    }
}
