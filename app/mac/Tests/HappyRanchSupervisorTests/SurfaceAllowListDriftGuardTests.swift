import Foundation
import Testing
@testable import HappyRanchSupervisor

/// Drift-guard test: asserts that SurfaceAllowList.default matches the
/// authoritative route classification in tests/contract/route-classification.json.
///
/// This is the SINGLE SOURCE OF TRUTH for route surface classification.
/// When a daemon route is added or reclassified, update the JSON fixture —
/// this test will then catch any Swift deny-gate drift.
///
/// Reads BOTH:
///   - tests/contract/route-classification.json (included vs excluded split)
///   - tests/contract/openapi.json (the route universe)
///
/// via repo-relative paths from the package directory (app/mac/).
/// Uses Foundation-only (JSONSerialization + FileManager); no Package.swift
/// dependency changes required.
@Suite("SurfaceAllowList drift-guard against route-classification.json")
struct SurfaceAllowListDriftGuardTests {

    // MARK: - Path helpers

    /// The package directory (app/mac/) relative to the repo root is two levels deep.
    private static let repoRoot = "../../"

    private static let classificationPath = "\(repoRoot)tests/contract/route-classification.json"
    private static let openapiPath = "\(repoRoot)tests/contract/openapi.json"

    // MARK: - Replacement map for template placeholders -> concrete values

    private static let placeholderReplacements: [(String, String)] = [
        ("{slug}", "happyranch"),
        ("{task_id}", "TASK-123"),
        ("{agent_name}", "dev_agent"),
        ("{thread_id}", "thr-abc"),
        ("{dream_id}", "dream-1"),
        ("{work_hour_id}", "wh-1"),
        ("{job_id}", "job-1"),
        ("{id}", "entry-1"),
        ("{id_or_slug}", "my-entry"),
        ("{name}", "report.pdf"),
        ("{entry_slug}", "my-kb-entry"),
        ("{conv_id}", "conv-1"),
        ("{candidate_id}", "cand-1"),
        ("{attachment_id}", "att-1"),
    ]

    /// Replace template placeholders in a path with concrete values.
    private static func concretize(_ templatePath: String) -> String {
        var result = templatePath
        for (placeholder, replacement) in placeholderReplacements {
            result = result.replacingOccurrences(of: placeholder, with: replacement)
        }
        return result
    }

    // MARK: - JSON loading

    private static func loadJSON(path: String) throws -> Any {
        let url = URL(fileURLWithPath: path)
        let data = try Data(contentsOf: url)
        return try JSONSerialization.jsonObject(with: data)
    }

    /// Load and parse the route classification JSON.
    /// Returns (included: Set<String>, excluded: Set<String>).
    private static func loadClassification() throws -> (included: Set<String>, excluded: Set<String>) {
        let json = try loadJSON(path: classificationPath) as! [String: Any]
        let included = Set((json["included"] as! [String]).map { $0.trimmingCharacters(in: .whitespaces) })
        let excluded = Set((json["excluded"] as! [String: String]).keys.map { $0.trimmingCharacters(in: .whitespaces) })
        return (included, excluded)
    }

    /// Load and parse the OpenAPI snapshot to get all daemon routes.
    /// Returns a Set of "METHOD /path" strings.
    private static func loadOpenAPIRoutes() throws -> Set<String> {
        let json = try loadJSON(path: openapiPath) as! [String: Any]
        let paths = json["paths"] as! [String: [String: Any]]
        var routes = Set<String>()
        for (path, methods) in paths {
            for method in methods.keys {
                routes.insert("\(method.uppercased()) \(path)")
            }
        }
        return routes
    }

    // MARK: - Locally-intercepted routes
    /// Routes that are browser-facing (INCLUDED) but MUST be denied at the
    /// connector level because ClientBridge intercepts them locally (auth
    /// bootstrap is a token-bearing endpoint that must never be proxied).
    private static let locallyIntercepted: Set<String> = [
        "GET /api/v1/auth/bootstrap",
    ]

    // MARK: - Tests

    @Test("auth bootstrap is locally intercepted (included but denied)")
    func authBootstrapIsLocallyIntercepted() throws {
        let policy = SurfaceAllowList.default
        // The browser-facing auth bootstrap must be denied at the connector
        // because ClientBridge intercepts it locally.
        #expect(!policy.isAllowed(method: "GET", path: "/auth/bootstrap", rawPath: "/api/v1/auth/bootstrap"))
    }
    func classificationJSONIsLoadable() throws {
        let (included, excluded) = try Self.loadClassification()
        #expect(!included.isEmpty, "included set should not be empty")
        #expect(!excluded.isEmpty, "excluded set should not be empty")
    }

    @Test("openapi.json is loadable and has routes")
    func openAPIJSONIsLoadable() throws {
        let routes = try Self.loadOpenAPIRoutes()
        #expect(!routes.isEmpty, "openapi routes should not be empty")
    }

    @Test("all EXCLUDED routes are DENIED by SurfaceAllowList.default (concrete paths, prefixed)")
    func allExcludedRoutesDenied() throws {
        let (_, excluded) = try Self.loadClassification()
        let policy = SurfaceAllowList.default

        for route in excluded.sorted() {
            let parts = route.split(separator: " ", maxSplits: 1)
            guard parts.count == 2 else {
                Issue.record("Invalid route format: \(route)")
                continue
            }
            let method = String(parts[0])
            let templatePath = String(parts[1])
            let concretePath = Self.concretize(templatePath)
            let normalizedPath = DaemonPathNormalizer.stripApiPrefix(concretePath)

            let allowed = policy.isAllowed(method: method, path: normalizedPath, rawPath: concretePath)
            #expect(!allowed, "\(route) (concrete: \(concretePath)) should be DENIED but was ALLOWED")
        }
    }

    @Test("all INCLUDED routes are ALLOWED by SurfaceAllowList.default (concrete paths, prefixed)")
    func allIncludedRoutesAllowed() throws {
        let (included, _) = try Self.loadClassification()
        let policy = SurfaceAllowList.default

        for route in included.sorted() {
            // Skip locally-intercepted routes (e.g. auth/bootstrap is
            // browser-facing but intercepted by ClientBridge, not proxied).
            if Self.locallyIntercepted.contains(route) { continue }

            let parts = route.split(separator: " ", maxSplits: 1)
            guard parts.count == 2 else {
                Issue.record("Invalid route format: \(route)")
                continue
            }
            let method = String(parts[0])
            let templatePath = String(parts[1])
            let concretePath = Self.concretize(templatePath)
            let normalizedPath = DaemonPathNormalizer.stripApiPrefix(concretePath)

            let allowed = policy.isAllowed(method: method, path: normalizedPath, rawPath: concretePath)
            #expect(allowed, "\(route) (concrete: \(concretePath), normalized: \(normalizedPath)) should be ALLOWED but was DENIED")
        }
    }

    @Test("every OpenAPI route is classified (no unclassified routes)")
    func noUnclassifiedRoutes() throws {
        let (included, excluded) = try Self.loadClassification()
        let allRoutes = try Self.loadOpenAPIRoutes()

        let classified = included.union(excluded)
        let unclassified = allRoutes.subtracting(classified)

        if !unclassified.isEmpty {
            let sorted = unclassified.sorted().joined(separator: "\n  ")
            Issue.record("Unclassified OpenAPI routes (add to route-classification.json):\n  \(sorted)")
        }
        #expect(unclassified.isEmpty, "All OpenAPI routes must be classified in route-classification.json")
    }

    @Test("no stale classification entries (no route in JSON that doesn't exist in OpenAPI)")
    func noStaleClassificationEntries() throws {
        let (included, excluded) = try Self.loadClassification()
        let allRoutes = try Self.loadOpenAPIRoutes()
        let classified = included.union(excluded)

        let stale = classified.subtracting(allRoutes)
        if !stale.isEmpty {
            let sorted = stale.sorted().joined(separator: "\n  ")
            Issue.record("Stale classification entries (route not in openapi.json):\n  \(sorted)")
        }
        #expect(stale.isEmpty, "Classification entries must match existing OpenAPI routes")
    }
}
