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

    @Test("allows /artifacts — normal SPA surface")
    func allowsArtifacts() {
        let policy = SurfaceAllowList.default
        #expect(policy.isAllowed(path: "/artifacts"))
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

    @Test("denies /threads/{id} (detail) — thread detail contains reply/decline/close-out")
    func deniesThreadDetail() {
        let policy = SurfaceAllowList.default
        #expect(!policy.isAllowed(path: "/threads/some-thread"))
    }

    // MARK: - Custom policy

    @Test("custom policy denies only specified paths")
    func customPolicy() {
        let policy = SurfaceAllowList(
            deniedPaths: ["/secret"],
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
}
