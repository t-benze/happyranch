import Foundation
import Testing
@testable import HappyRanchSupervisor

@Suite("EnvironmentSanitizer")
struct EnvironmentSanitizerTests {

    // MARK: - Sanitization correctness

    @Test("HAPPYRANCH_DAEMON_BIND_HOST dropped from child environment")
    func dropsDaemonBindHost() {
        let parent: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "HAPPYRANCH_DAEMON_BIND_HOST": "0.0.0.0",
            "HAPPYRANCH_DAEMON_HOME": "/tmp/hr",
        ]
        let child = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/hr",
            parentEnvironment: parent
        )
        #expect(child["HAPPYRANCH_DAEMON_BIND_HOST"] == nil,
                "HAPPYRANCH_DAEMON_BIND_HOST must NOT be passed to daemon child")
    }

    @Test("arbitrary HAPPYRANCH_FOO is dropped")
    func dropsArbitraryHappyRanchVar() {
        let parent: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "HAPPYRANCH_FOO": "bar-value",
            "HAPPYRANCH_SECRET_TOKEN": "abc123",
        ]
        let child = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/hr",
            parentEnvironment: parent
        )
        #expect(child["HAPPYRANCH_FOO"] == nil,
                "Arbitrary HAPPYRANCH_FOO must NOT be passed to daemon child")
        #expect(child["HAPPYRANCH_SECRET_TOKEN"] == nil,
                "HAPPYRANCH_SECRET_TOKEN must NOT be passed to daemon child")
    }

    @Test("HAPPYRANCH_WEB_DIST passes through only when present in parent")
    func webDistConditionalPassThrough() {
        // Case 1: present in parent → passes through
        let parentWith: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "HAPPYRANCH_WEB_DIST": "/path/to/web/dist",
        ]
        let childWith = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/hr",
            parentEnvironment: parentWith
        )
        #expect(childWith["HAPPYRANCH_WEB_DIST"] == "/path/to/web/dist",
                "HAPPYRANCH_WEB_DIST should pass through when present")

        // Case 2: absent from parent → absent from child
        let parentWithout: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
        ]
        let childWithout = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/hr",
            parentEnvironment: parentWithout
        )
        #expect(childWithout["HAPPYRANCH_WEB_DIST"] == nil,
                "HAPPYRANCH_WEB_DIST must NOT be added when absent in parent")
    }

    @Test("HAPPYRANCH_DAEMON_HOME is always set")
    func daemonHomeIsSet() {
        let parent: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "HAPPYRANCH_DAEMON_HOME": "/some/other/path", // Should be overridden
        ]
        let child = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/my-hr-home",
            parentEnvironment: parent
        )
        #expect(child["HAPPYRANCH_DAEMON_HOME"] == "/tmp/my-hr-home",
                "HAPPYRANCH_DAEMON_HOME must be set to the explicit daemon home")
    }

    @Test("PATH and HOME survive")
    func pathAndHomeSurvive() {
        let parent: [String: String] = [
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/Users/testuser",
        ]
        let child = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/hr",
            parentEnvironment: parent
        )
        #expect(child["PATH"] == "/usr/local/bin:/usr/bin:/bin",
                "PATH must survive sanitization")
        #expect(child["HOME"] == "/Users/testuser",
                "HOME must survive sanitization")
    }

    @Test("no extra variables leak through")
    func noExtraVariablesLeak() {
        let parent: [String: String] = [
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "SECRET_API_KEY": "should-not-leak",
            "AWS_ACCESS_KEY_ID": "should-not-leak",
            "HAPPYRANCH_DAEMON_BIND_HOST": "0.0.0.0",
            "HAPPYRANCH_AUTH_SECRET": "topsecret",
            "HAPPYRANCH_DEBUG": "1",
            "HAPPYRANCH_CORS_ORIGINS": "*",
            "HAPPYRANCH_WEB_DIST": "/tmp/web",
        ]
        let child = EnvironmentSanitizer.buildChildEnvironment(
            daemonHome: "/tmp/hr",
            parentEnvironment: parent
        )

        // Only these should survive
        let allowedKeys: Set<String> = ["PATH", "HOME", "HAPPYRANCH_DAEMON_HOME", "HAPPYRANCH_WEB_DIST"]
        for key in child.keys {
            #expect(allowedKeys.contains(key),
                    "Unexpected key '\(key)' leaked into child environment")
        }

        // Verify count is at most 4
        #expect(child.count <= 4, "Child environment should have at most 4 variables")
    }
}
