import Foundation

// MARK: - SurfaceAllowList

/// Enforces the remote-surface deny gate — **method-aware and route-aware**.
///
/// Remote sessions expose the **normal Web SPA surface only**.
/// All agent-callback, management, memory-write, thread-mutating, and
/// auth-bootstrap/registration-token routes are DENIED on BOTH prefixed
/// (`/api/v1/...`) and unprefixed forms.
///
/// The AUTHORITATIVE exclusion list is the ``EXCLUDED_PATHS`` map in
/// ``web/src/test/openapi-coverage.test.ts`` (mirrored exactly below).
///
/// Policy: **deny-by-default for sensitive routes, allow for normal SPA routes.**
/// The deny gate is method-aware — a route may be denied for POST but allowed
/// for GET.
public struct SurfaceAllowList: Sendable {

    /// Method-aware deny entries.
    /// Key format: `"<METHOD> <path>"`.
    /// The path is the UNPREFIXED, CONCRETE daemon route path with `*`
    /// wildcards replacing template segments (`{task_id}`, `{agent_name}`, etc.).
    ///
    /// Example: `"POST /tasks/*/completion"` matches `POST /tasks/TASK-123/completion`.
    private let deniedMethods: Set<String>

    /// Path prefixes that are **denied** remotely (all methods, prefix-match).
    private let deniedPrefixes: [String]

    /// Denied path patterns for routes with template placeholders.
    /// Each tuple is `(method, segment, suffix)` meaning:
    /// "deny if path contains `segment` AND contains `suffix`".
    /// This handles routes like `/agents/{agent_name}/memory` where the
    /// method matters but the agent name varies.
    ///
    /// For the memory-write routes where `contains` would over-match
    /// the browser-facing `POST .../memory/entries/search`, use
    /// ``deniedSegmentEndsWithSuffixes`` instead.
    private let deniedSegmentPatterns: [(method: String, segment: String, suffix: String)]

    /// Denied path patterns where the suffix must appear at the **end** of
    /// the path (``hasSuffix``), not just anywhere in it.
    ///
    /// Used for memory-write patterns like `POST /agents/*/memory` which
    /// would otherwise over-match the browser-facing
    /// `POST /agents/*/memory/entries/search` via plain `contains`.
    private let deniedSegmentEndsWithSuffixes: [(method: String, segment: String, suffix: String)]

    // MARK: - Default policy

    /// The authoritative v2 remote-surface deny gate.
    ///
    /// All EXCLUDED_PATHS from openapi-coverage.test.ts are denied on
    /// BOTH prefixed and unprefixed forms, plus auth bootstrap,
    /// registration-token, and --as-founder surfaces.
    public static let `default`: SurfaceAllowList = {
        var denied: [String] = []
        var segmentPatterns: [(String, String, String)] = []

        // ── Auth bootstrap & registration-token (FINDING 1+2) ──────────────
        // Both methods, both prefixed and unprefixed forms.
        for p in DaemonPathNormalizer.bothForms("/auth/bootstrap") {
            denied.append("GET \(p)")
            denied.append("POST \(p)")
        }
        for p in DaemonPathNormalizer.bothForms("/auth/registration-token") {
            denied.append("GET \(p)")
            denied.append("POST \(p)")
        }

        // ── Exact-match routes (no template placeholders) ──────────────────
        // These are denied for specific methods only.
        let exactDenies: [(String, String)] = [
            // Agent self-service
            ("POST", "/agents/manage"),
            // Thread agent callback
            ("POST", "/threads/compose-as-agent"),
            // Jobs agent callback
            ("POST", "/jobs/submit"),
            // Dreams agent callback
            ("POST", "/dreams/{dream_id}/complete"),
            // Work-hours wake spawn
            ("POST", "/work-hours/{work_hour_id}/spawn"),
            // Executor conformance + registration
            ("POST", "/executors/conformance-checkin"),
            ("POST", "/executors/register"),
            // Founder set-executor (CLI-only)
            ("PUT", "/agents/{agent_name}/executor"),
            // Artifacts — agent-facing v1 (upload, list)
            ("POST", "/artifacts"),
            ("GET", "/artifacts"),
            // Metrics — agent/CLI facing (operational metrics)
            ("GET", "/metrics"),
            ("GET", "/metrics/history"),
        ]
        for (method, path) in exactDenies {
            denied.append("\(method) \(path)")
        }

        // ── Segment-pattern routes (template placeholders in middle of path) ──
        // Format: (method, segment-to-contain, suffix-to-end-with)
        let segmentDenies: [(String, String, String)] = [
            // Task agent callbacks
            ("POST", "/tasks/", "/completion"),
            ("POST", "/tasks/", "/progress"),
            // Agent self-service & management
            ("POST", "/agents/", "/repos"),
            ("PUT", "/agents/", "/executor"),
            // Memory writes — non-POST methods are safe for `contains`
            // because the browser-facing search route is POST-only.
            ("PUT", "/agents/", "/memory"),
            ("PATCH", "/agents/", "/memory"),
            // Thread agent callbacks (reply, decline, dispatch,
            // post-as-agent, attachments)
            ("POST", "/threads/", "/reply"),
            ("POST", "/threads/", "/decline"),
            ("POST", "/threads/", "/dispatch"),
            ("POST", "/threads/", "/post-as-agent"),
            // Attachments: POST uses contains (no single-attachment upload route),
            // GET uses ends-with to avoid over-matching /attachments/{id} download.
            ("POST", "/threads/", "/attachments"),
            // Dreams agent callback
            ("POST", "/dreams/", "/complete"),
            // Work-hours wake spawn
            ("POST", "/work-hours/", "/spawn"),
            // Artifacts — agent-facing download (GET /artifacts/{name})
            ("GET", "/artifacts/", ""),
        ]
        let memoryEndsWithDenies: [(String, String, String)] = [
            // Memory writes where suffix must be path-end to avoid
            // over-matching the browser-facing POST .../memory/entries/search.
            ("POST", "/agents/", "/memory"),
            ("POST", "/agents/", "/memory/entries/"),
            ("POST", "/agents/", "/promote"),
            ("POST", "/agents/", "/reindex"),
            ("POST", "/agents/", "/compact"),
            ("PATCH", "/agents/", "/lifecycle"),
            // GET /threads/{id}/attachments (list) uses ends-with to avoid
            // over-matching the browser-facing GET .../attachments/{id} (download).
            ("GET", "/threads/", "/attachments"),
        ]
        segmentPatterns = segmentDenies

        // ── Legacy unprefixed deny list (defense-in-depth) ──────────────────
        // Agent-callback endpoints
        for p in ["/report-completion", "/dispatch"] {
            denied.append("GET \(p)")
            denied.append("POST \(p)")
        }
        // Thread mutating endpoints
        for p in ["/reply", "/decline", "/close-out"] {
            denied.append("GET \(p)")
            denied.append("POST \(p)")
        }
        // Management endpoints
        for p in ["/manage-agent", "/manage-repo"] {
            denied.append("GET \(p)")
            denied.append("POST \(p)")
        }

        let deniedPrefixes: [String] = [
            "/as-founder",
            "/api/v1/as-founder",
            // Defense-in-depth prefix denies (no "/threads/" — browser-facing
            // thread routes must stay ALLOWED; per-method+path denies below).
            "/memory/",
            "/learning/",
        ]

        return SurfaceAllowList(
            deniedMethods: Set(denied),
            deniedPrefixes: deniedPrefixes,
            deniedSegmentPatterns: segmentPatterns,
            deniedSegmentEndsWithSuffixes: memoryEndsWithDenies
        )
    }()

    // MARK: - Init

    public init(
        deniedMethods: Set<String> = [],
        deniedPrefixes: [String] = [],
        deniedSegmentPatterns: [(method: String, segment: String, suffix: String)] = [],
        deniedSegmentEndsWithSuffixes: [(method: String, segment: String, suffix: String)] = []
    ) {
        self.deniedMethods = deniedMethods
        self.deniedPrefixes = deniedPrefixes
        self.deniedSegmentPatterns = deniedSegmentPatterns
        self.deniedSegmentEndsWithSuffixes = deniedSegmentEndsWithSuffixes
    }

    // MARK: - Gate

    /// Check whether a request (method, path) is **allowed** for remote access.
    ///
    /// - Parameters:
    ///   - method: The HTTP method (e.g. "GET", "POST").
    ///   - path: The normalized (unprefixed) path without api/orgs prefixes.
    ///   - rawPath: The original raw path from the request.
    /// - Returns: `true` if the request is allowed, `false` if it is denied.
    public func isAllowed(method: String, path: String, rawPath: String) -> Bool {
        // Build the set of paths to check: the normalized path plus its
        // /api/v1-prefixed form (for defense-in-depth with unprefixed client requests).
        let pathsToCheck = [path, "/api/v1\(path)"]

        for checkPath in pathsToCheck {
            // 1. Method+path exact deny
            let key = "\(method) \(checkPath)"
            if deniedMethods.contains(key) {
                return false
            }

            // 2. Segment-pattern deny (for template routes like /agents/*/memory)
            for pattern in deniedSegmentPatterns {
                guard method == pattern.method else { continue }
                let segmentMatch = checkPath.contains(pattern.segment)
                let suffixMatch = pattern.suffix.isEmpty || checkPath.contains(pattern.suffix)
                if segmentMatch && suffixMatch {
                    return false
                }
            }

            // 2b. Segment-pattern deny where suffix must end the path
            // (hasSuffix, not contains — prevents over-matching sub-paths).
            for pattern in deniedSegmentEndsWithSuffixes {
                guard method == pattern.method else { continue }
                let segmentMatch = checkPath.contains(pattern.segment)
                let endsWithSuffix = pattern.suffix.isEmpty || checkPath.hasSuffix(pattern.suffix)
                if segmentMatch && endsWithSuffix {
                    return false
                }
            }

            // 3. Prefix-match deny (all methods)
            for prefix in deniedPrefixes {
                if checkPath.hasPrefix(prefix) {
                    return false
                }
            }
        }

        // Also check the raw path against prefixes
        for prefix in deniedPrefixes {
            if rawPath.hasPrefix(prefix) {
                return false
            }
        }

        return true
    }

    /// Legacy compatibility signature.
    @available(*, deprecated, message: "Use isAllowed(method:path:rawPath:) instead")
    public func isAllowed(path: String) -> Bool {
        let normalizedPath = path.hasSuffix("/") && path.count > 1
            ? String(path.dropLast())
            : path
        if !isAllowed(method: "GET", path: normalizedPath, rawPath: normalizedPath) { return false }
        if !isAllowed(method: "POST", path: normalizedPath, rawPath: normalizedPath) { return false }
        return true
    }
}
