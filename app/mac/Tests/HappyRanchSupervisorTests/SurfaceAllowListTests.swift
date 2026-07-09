import Testing
import Foundation
@testable import HappyRanchSupervisor

@Suite("SurfaceAllowList deny gate")
struct SurfaceAllowListTests {

    // MARK: - Default policy exists

    @Test("default policy is non-empty")
    func defaultPolicyIsNonEmpty() {
        let policy = SurfaceAllowList.default
        // Test at least one known denied path
        #expect(!policy.isAllowed(path: "/report-completion"))
    }

    // MARK: - Agent-callback endpoints (must be DENIED)

    @Test("denies /report-completion")
    func deniesReportCompletion() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/report-completion"))
    }

    @Test("denies /dispatch")
    func deniesDispatch() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/dispatch"))
    }

    @Test("denies /threads/{id}/dispatch via prefix match")
    func deniesThreadDispatch() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/threads/some-id/dispatch"))
        #expect(!policy.isAllowed(path: "/threads/abc123/dispatch"))
    }

    // MARK: - Thread mutating endpoints (must be DENIED)

    @Test("denies thread /reply")
    func deniesThreadReply() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/reply"))
    }

    @Test("denies thread /decline")
    func deniesThreadDecline() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/decline"))
    }

    @Test("denies thread /close-out")
    func deniesThreadCloseOut() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/close-out"))
    }

    // MARK: - Management endpoints (must be DENIED)

    @Test("denies /manage-agent")
    func deniesManageAgent() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/manage-agent"))
    }

    @Test("denies /manage-repo")
    func deniesManageRepo() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/manage-repo"))
    }

    // MARK: - Auth bootstrap & registration (must be DENIED)

    @Test("denies /auth/bootstrap")
    func deniesAuthBootstrap() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/auth/bootstrap"))
    }

    @Test("denies /auth/registration-token")
    func deniesAuthRegistrationToken() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/auth/registration-token"))
    }

    // MARK: - Memory/learning write endpoints (must be DENIED via prefix)

    @Test("denies /memory/add via prefix")
    func deniesMemoryAdd() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/memory/add"))
    }

    @Test("denies /memory/update via prefix")
    func deniesMemoryUpdate() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/memory/update"))
    }

    @Test("denies /memory/promote via prefix")
    func deniesMemoryPromote() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/memory/promote"))
    }

    @Test("denies /learning/add via prefix")
    func deniesLearningAdd() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/learning/add"))
    }

    @Test("denies /learning/update via prefix")
    func deniesLearningUpdate() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/learning/update"))
    }

    @Test("denies /learning/promote via prefix")
    func deniesLearningPromote() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/learning/promote"))
    }

    // MARK: - --as-founder override surfaces (must be DENIED)

    @Test("denies /as-founder prefix")
    func deniesAsFounder() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/as-founder"))
        #expect(!policy.isAllowed(path: "/as-founder/tasks"))
    }

    // MARK: - Normal SPA surface (must be ALLOWED)

    @Test("allows /tasks — normal SPA surface")
    func allowsTasks() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/tasks"))
    }

    @Test("allows / — root SPA surface")
    func allowsRoot() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/"))
    }

    @Test("allows /agents — normal SPA surface")
    func allowsAgents() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/agents"))
    }

    @Test("allows /settings — normal SPA surface")
    func allowsSettings() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/settings"))
    }

    @Test("allows /tokens — normal SPA surface")
    func allowsTokens() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/tokens"))
    }

    @Test("allows /threads (list view) — SPA read surface")
    func allowsThreadsList() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/threads"))
    }

    @Test("allows /dashboard — normal SPA surface")
    func allowsDashboard() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/dashboard"))
    }

    @Test("allows /kb — normal SPA surface")
    func allowsKB() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/kb"))
    }

    @Test("denies /artifacts — excluded from remote surface (agent-facing v1)")
    func deniesArtifacts() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/artifacts"))
    }

    @Test("allows /jobs — normal SPA surface")
    func allowsJobs() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/jobs"))
    }

    // MARK: - Trailing slash normalization

    @Test("denies /report-completion/ (trailing slash)")
    func deniesReportCompletionWithTrailingSlash() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/report-completion/"))
    }

    @Test("denies /auth/bootstrap/ (trailing slash)")
    func deniesAuthBootstrapWithTrailingSlash() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/auth/bootstrap/"))
    }

    // MARK: - Known SPA sub-paths

    @Test("allows /tasks/123 — SPA detail view")
    func allowsTaskDetail() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/tasks/123"))
    }

    @Test("allows /threads/{id} (detail) — browser-facing thread detail view")
    func allowsThreadDetail() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/threads/some-thread"))
    }

    // MARK: - FIX A: browser-facing thread routes ALLOWED

    @Test("allows GET /threads/{id}/messages — browser-facing")
    func allowsThreadMessages() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "GET", path: "/threads/thr-abc/messages", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/messages"))
        #expect(policy.isAllowed(method: "GET", path: "/threads/thr-abc/messages", rawPath: "/threads/thr-abc/messages"))
    }

    @Test("allows GET /threads/{id}/tail — browser-facing")
    func allowsThreadTail() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "GET", path: "/threads/thr-abc/tail", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/tail"))
        #expect(policy.isAllowed(method: "GET", path: "/threads/thr-abc/tail", rawPath: "/threads/thr-abc/tail"))
    }

    @Test("allows GET /threads/{id}/tasks — browser-facing")
    func allowsThreadTasks() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "GET", path: "/threads/thr-abc/tasks", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/tasks"))
        #expect(policy.isAllowed(method: "GET", path: "/threads/thr-abc/tasks", rawPath: "/threads/thr-abc/tasks"))
    }

    @Test("allows POST /threads/{id}/send — browser-facing")
    func allowsThreadSend() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/send", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/send"))
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/send", rawPath: "/threads/thr-abc/send"))
    }

    @Test("allows POST /threads/{id}/invite — browser-facing")
    func allowsThreadInvite() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/invite", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/invite"))
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/invite", rawPath: "/threads/thr-abc/invite"))
    }

    @Test("allows POST /threads/{id}/extend — browser-facing")
    func allowsThreadExtend() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/extend", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/extend"))
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/extend", rawPath: "/threads/thr-abc/extend"))
    }

    @Test("allows POST /threads/{id}/archive — browser-facing")
    func allowsThreadArchive() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/archive", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/archive"))
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/archive", rawPath: "/threads/thr-abc/archive"))
    }

    @Test("allows POST /threads/{id}/abort-replies — browser-facing")
    func allowsThreadAbortReplies() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/abort-replies", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/abort-replies"))
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/abort-replies", rawPath: "/threads/thr-abc/abort-replies"))
    }

    @Test("allows POST /threads/{id}/resume — browser-facing")
    func allowsThreadResume() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/resume", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/resume"))
        #expect(policy.isAllowed(method: "POST", path: "/threads/thr-abc/resume", rawPath: "/threads/thr-abc/resume"))
    }

    // MARK: - FIX A: agent-callback thread routes DENIED

    @Test("denies POST /threads/{id}/reply — agent callback (prefixed + unprefixed)")
    func deniesThreadReplyAgentCallback() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/reply", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/reply"))
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/reply", rawPath: "/threads/thr-abc/reply"))
    }

    @Test("denies POST /threads/{id}/decline — agent callback")
    func deniesThreadDeclineAgentCallback() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/decline", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/decline"))
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/decline", rawPath: "/threads/thr-abc/decline"))
    }

    @Test("denies POST /threads/{id}/dispatch — agent callback")
    func deniesThreadDispatchAgentCallback() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/dispatch", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/dispatch"))
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/dispatch", rawPath: "/threads/thr-abc/dispatch"))
    }

    @Test("denies POST /threads/compose-as-agent — agent callback")
    func deniesThreadComposeAsAgent() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/threads/compose-as-agent", rawPath: "/api/v1/orgs/happyranch/threads/compose-as-agent"))
        #expect(!policy.isAllowed(method: "POST", path: "/threads/compose-as-agent", rawPath: "/threads/compose-as-agent"))
    }

    @Test("denies POST /threads/{id}/post-as-agent — agent callback")
    func deniesThreadPostAsAgent() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/post-as-agent", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/post-as-agent"))
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/post-as-agent", rawPath: "/threads/thr-abc/post-as-agent"))
    }

    @Test("denies GET /threads/{id}/attachments — agent callback")
    func deniesThreadAttachmentsGet() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/threads/thr-abc/attachments", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/attachments"))
        #expect(!policy.isAllowed(method: "GET", path: "/threads/thr-abc/attachments", rawPath: "/threads/thr-abc/attachments"))
    }

    @Test("denies POST /threads/{id}/attachments — agent callback")
    func deniesThreadAttachmentsPost() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/attachments", rawPath: "/api/v1/orgs/happyranch/threads/thr-abc/attachments"))
        #expect(!policy.isAllowed(method: "POST", path: "/threads/thr-abc/attachments", rawPath: "/threads/thr-abc/attachments"))
    }

    // MARK: - Custom policy

    @Test("custom policy denies only specified paths")
    func customPolicy() {
        let policy = SurfaceAllowList(
            deniedMethods: ["GET /secret", "POST /secret"],
            deniedPrefixes: ["/admin/"]
        )
        #expect(!policy.isAllowed(path: "/secret"))
        #expect(!policy.isAllowed(path: "/admin/users"))
        #expect(policy.isAllowed(path: "/tasks"))
        #expect(policy.isAllowed(path: "/"))
    }

    @Test("empty custom policy allows everything")
    func emptyPolicyAllowsAll() {
        let policy = SurfaceAllowList()
        #expect(policy.isAllowed(path: "/report-completion"))
        #expect(policy.isAllowed(path: "/anything"))
        #expect(policy.isAllowed(path: "/"))
    }

    // MARK: - FINDING 2 [CRITICAL] — method-aware deny gate

    /// The authoritative set of excluded routes from web/src/test/openapi-coverage.test.ts
    /// EXCLUDED_PATHS.  Tested with concrete values for template placeholders.
    private static let excludedRoutes: [(method: String, path: String)] = [
        // Task agent callbacks
        ("POST", "/tasks/TASK-123/completion"),
        ("POST", "/tasks/TASK-123/progress"),
        // Agent self-service
        ("POST", "/agents/manage"),
        ("POST", "/agents/dev_agent/repos"),
        // Founder set-executor
        ("PUT", "/agents/dev_agent/executor"),
        // Memory writes
        ("POST", "/agents/dev_agent/memory"),
        ("POST", "/agents/dev_agent/memory/entries/"),
        ("PUT", "/agents/dev_agent/memory/entries/entry-1"),
        ("POST", "/agents/dev_agent/memory/entries/entry-1/promote"),
        ("POST", "/agents/dev_agent/memory/entries/reindex"),
        ("PATCH", "/agents/dev_agent/memory/entries/entry-1/lifecycle"),
        ("POST", "/agents/dev_agent/memory/entries/compact"),
        // Thread agent callbacks
        ("POST", "/threads/thr-abc/reply"),
        ("POST", "/threads/thr-abc/decline"),
        ("POST", "/threads/thr-abc/dispatch"),
        ("POST", "/threads/compose-as-agent"),
        ("POST", "/threads/thr-abc/post-as-agent"),
        // Thread-scoped attachments
        ("GET", "/threads/thr-abc/attachments"),
        ("POST", "/threads/thr-abc/attachments"),
        // Jobs agent callback
        ("POST", "/jobs/submit"),
        // Dreams agent callback
        ("POST", "/dreams/dream-1/complete"),
        // Work-hours wake spawn
        ("POST", "/work-hours/wh-1/spawn"),
        // Registration-token mint
        ("POST", "/auth/registration-token"),
        // Executor conformance + registration
        ("POST", "/executors/conformance-checkin"),
        ("POST", "/executors/register"),
        // Artifacts — agent-facing v1 (upload, list, download)
        ("POST", "/artifacts"),
        ("GET", "/artifacts"),
        ("GET", "/artifacts/report.pdf"),
        // Metrics — agent/CLI facing (operational metrics)
        ("GET", "/metrics"),
        ("GET", "/metrics/history"),
    ]

    @Test("each EXCLUDED_PATHS route is denied (prefixed /api/v1 form, concrete paths)")
    func eachExcludedRouteDeniedPrefixed() {
        let policy = SurfaceAllowList.default
        for (method, path) in Self.excludedRoutes {
            let prefixedPath = "/api/v1/orgs/happyranch\(path)"
            // Normalize: HomeConnector strips /api/v1 and /orgs/{slug} before checking
            let normalizedPath = DaemonPathNormalizer.stripApiPrefix(prefixedPath)
            let allowed = policy.isAllowed(method: method, path: normalizedPath, rawPath: prefixedPath)
            #expect(!allowed, "\(method) \(prefixedPath) should be DENIED but was ALLOWED (normalized: \(normalizedPath))")
        }
    }

    @Test("each EXCLUDED_PATHS route is denied (unprefixed form, concrete paths)")
    func eachExcludedRouteDeniedUnprefixed() {
        let policy = SurfaceAllowList.default
        for (method, path) in Self.excludedRoutes {
            let allowed = policy.isAllowed(method: method, path: path, rawPath: path)
            #expect(!allowed, "\(method) \(path) should be DENIED but was ALLOWED")
        }
    }

    @Test("normal browser-facing route is ALLOWED (GET /api/v1/orgs/{slug}/tasks)")
    func normalBrowserRouteAllowed() {
        let policy = SurfaceAllowList.default
        // A normal SPA route should be allowed
        #expect(policy.isAllowed(method: "GET", path: "/tasks", rawPath: "/api/v1/orgs/happyranch/tasks"))
        #expect(policy.isAllowed(method: "GET", path: "/agents", rawPath: "/api/v1/orgs/happyranch/agents"))
        #expect(policy.isAllowed(method: "GET", path: "/dashboard/summary", rawPath: "/api/v1/orgs/happyranch/dashboard/summary"))
    }

    @Test("auth bootstrap denied via method-aware API (both prefixed and unprefixed)")
    func authBootstrapDeniedMethodAware() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/auth/bootstrap", rawPath: "/auth/bootstrap"))
        #expect(!policy.isAllowed(method: "GET", path: "/auth/bootstrap", rawPath: "/api/v1/auth/bootstrap"))
        #expect(!policy.isAllowed(method: "POST", path: "/auth/bootstrap", rawPath: "/auth/bootstrap"))
    }

    @Test("auth registration-token denied via method-aware API (both forms)")
    func authRegistrationTokenDeniedMethodAware() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/auth/registration-token", rawPath: "/auth/registration-token"))
        #expect(!policy.isAllowed(method: "POST", path: "/auth/registration-token", rawPath: "/api/v1/auth/registration-token"))
        #expect(!policy.isAllowed(method: "GET", path: "/auth/registration-token", rawPath: "/api/v1/auth/registration-token"))
    }

    // MARK: - FINDING A: artifacts + metrics deny, prefixed & unprefixed regression

    @Test("denies GET /artifacts (unprefixed)")
    func deniesArtifactsGetUnprefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/artifacts", rawPath: "/artifacts"))
    }

    @Test("denies GET /artifacts (prefixed /api/v1/orgs/{slug} form)")
    func deniesArtifactsGetPrefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/artifacts", rawPath: "/api/v1/orgs/happyranch/artifacts"))
    }

    @Test("denies POST /artifacts (unprefixed)")
    func deniesArtifactsPostUnprefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/artifacts", rawPath: "/artifacts"))
    }

    @Test("denies POST /artifacts (prefixed /api/v1/orgs/{slug} form)")
    func deniesArtifactsPostPrefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/artifacts", rawPath: "/api/v1/orgs/happyranch/artifacts"))
    }

    @Test("denies GET /artifacts/{name} (unprefixed concrete path)")
    func deniesArtifactsGetNameUnprefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/artifacts/report.pdf", rawPath: "/artifacts/report.pdf"))
    }

    @Test("denies GET /artifacts/{name} (prefixed /api/v1/orgs/{slug} form)")
    func deniesArtifactsGetNamePrefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/artifacts/report.pdf", rawPath: "/api/v1/orgs/happyranch/artifacts/report.pdf"))
    }

    @Test("denies GET /metrics (unprefixed)")
    func deniesMetricsGetUnprefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/metrics", rawPath: "/metrics"))
    }

    @Test("denies GET /metrics (prefixed /api/v1 form — no orgs slug)")
    func deniesMetricsGetPrefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/metrics", rawPath: "/api/v1/metrics"))
    }

    @Test("denies GET /metrics/history (unprefixed)")
    func deniesMetricsHistoryGetUnprefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/metrics/history", rawPath: "/metrics/history"))
    }

    @Test("denies GET /metrics/history (prefixed /api/v1 form)")
    func deniesMetricsHistoryGetPrefixed() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "GET", path: "/metrics/history", rawPath: "/api/v1/metrics/history"))
    }

    @Test("allows DELETE /artifacts/{name} — browser-facing delete route")
    func allowsArtifactsDelete() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "DELETE", path: "/artifacts/report.pdf", rawPath: "/api/v1/orgs/happyranch/artifacts/report.pdf"))
    }

    // MARK: - FIX B: memory search ALLOWED, memory writes DENIED

    @Test("allows POST /agents/{agent_name}/memory/entries/search — browser-facing")
    func allowsMemorySearch() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/search", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/search"))
        #expect(policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/search", rawPath: "/agents/dev_agent/memory/entries/search"))
    }

    @Test("denies POST /agents/{agent_name}/memory — legacy memory append")
    func deniesMemoryLegacyAppend() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory"))
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory", rawPath: "/agents/dev_agent/memory"))
    }

    @Test("denies POST /agents/{agent_name}/memory/entries/ — create entry")
    func deniesMemoryCreateEntry() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/"))
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/", rawPath: "/agents/dev_agent/memory/entries/"))
    }

    @Test("denies PUT /agents/{agent_name}/memory/entries/{id} — update entry")
    func deniesMemoryUpdateEntry() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "PUT", path: "/agents/dev_agent/memory/entries/entry-1", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/entry-1"))
        #expect(!policy.isAllowed(method: "PUT", path: "/agents/dev_agent/memory/entries/entry-1", rawPath: "/agents/dev_agent/memory/entries/entry-1"))
    }

    @Test("denies PATCH /agents/{agent_name}/memory/entries/{id}/lifecycle — lifecycle")
    func deniesMemoryLifecycle() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "PATCH", path: "/agents/dev_agent/memory/entries/entry-1/lifecycle", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/entry-1/lifecycle"))
        #expect(!policy.isAllowed(method: "PATCH", path: "/agents/dev_agent/memory/entries/entry-1/lifecycle", rawPath: "/agents/dev_agent/memory/entries/entry-1/lifecycle"))
    }

    @Test("denies POST /agents/{agent_name}/memory/entries/{id}/promote — promote")
    func deniesMemoryPromoteEntry() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/entry-1/promote", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/entry-1/promote"))
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/entry-1/promote", rawPath: "/agents/dev_agent/memory/entries/entry-1/promote"))
    }

    @Test("denies POST /agents/{agent_name}/memory/entries/reindex — reindex")
    func deniesMemoryReindex() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/reindex", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/reindex"))
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/reindex", rawPath: "/agents/dev_agent/memory/entries/reindex"))
    }

    @Test("denies POST /agents/{agent_name}/memory/entries/compact — compact")
    func deniesMemoryCompact() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/compact", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/compact"))
        #expect(!policy.isAllowed(method: "POST", path: "/agents/dev_agent/memory/entries/compact", rawPath: "/agents/dev_agent/memory/entries/compact"))
    }

    @Test("allows GET /agents/{agent_name}/memory/entries/ — browser-facing read")
    func allowsMemoryGetEntries() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "GET", path: "/agents/dev_agent/memory/entries/", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/"))
        #expect(policy.isAllowed(method: "GET", path: "/agents/dev_agent/memory/entries/", rawPath: "/agents/dev_agent/memory/entries/"))
    }

    @Test("allows GET /agents/{agent_name}/memory/entries/{id_or_slug} — browser-facing read")
    func allowsMemoryGetEntryById() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(method: "GET", path: "/agents/dev_agent/memory/entries/my-entry", rawPath: "/api/v1/orgs/happyranch/agents/dev_agent/memory/entries/my-entry"))
        #expect(policy.isAllowed(method: "GET", path: "/agents/dev_agent/memory/entries/my-entry", rawPath: "/agents/dev_agent/memory/entries/my-entry"))
    }
}
