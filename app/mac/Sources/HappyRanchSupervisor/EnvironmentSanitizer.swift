import Foundation

/// Builds a sanitized environment for the daemon child process.
///
/// The daemon child Process must NOT inherit the full parent environment,
/// which may contain non-operational HAPPYRANCH_* overrides
/// (HAPPYRANCH_DAEMON_BIND_HOST, CORS/auth/debug vars, secrets).
///
/// This function is extracted as a PURE, testable function independent of
/// the GUI so it can be unit-tested without AppDelegate.
public struct EnvironmentSanitizer: Sendable {

    /// Build a sanitized environment dictionary for a daemon child Process.
    ///
    /// - Parameters:
    ///   - daemonHome: Value for HAPPYRANCH_DAEMON_HOME (operational).
    ///   - parentEnvironment: The ProcessInfo.processInfo.environment dict.
    ///
    /// - Returns: A dictionary suitable for Process.environment containing
    ///   only the minimum OS/shell vars plus operational HAPPYRANCH vars.
    public static func buildChildEnvironment(
        daemonHome: String,
        parentEnvironment: [String: String]
    ) -> [String: String] {
        var env: [String: String] = [:]

        // Keep minimum OS/shell vars needed to run `uv run`
        // PATH is essential for finding `uv` and `python`
        if let path = parentEnvironment["PATH"] {
            env["PATH"] = path
        }
        if let home = parentEnvironment["HOME"] {
            env["HOME"] = home
        }

        // Operational HAPPYRANCH vars (explicitly set, not inherited)
        env["HAPPYRANCH_DAEMON_HOME"] = daemonHome

        // HAPPYRANCH_WEB_DIST: pass through ONLY if already present in
        // the parent env (build-time override). Never add it otherwise.
        if let webDist = parentEnvironment["HAPPYRANCH_WEB_DIST"] {
            env["HAPPYRANCH_WEB_DIST"] = webDist
        }

        // Explicitly do NOT pass through any other HAPPYRANCH_* variable.
        // This includes (but is not limited to):
        //   HAPPYRANCH_DAEMON_BIND_HOST
        //   HAPPYRANCH_CORS_ORIGINS
        //   HAPPYRANCH_AUTH_*
        //   HAPPYRANCH_DEBUG
        //   HAPPYRANCH_SECRETS_*
        // These are filtered implicitly by only whitelisting known keys above.

        return env
    }
}
